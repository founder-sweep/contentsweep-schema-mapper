"""Bounded product service, with no supplier/quality/account imports."""
from __future__ import annotations

import hashlib
import io
import time
import zipfile
from pathlib import Path

from .engine import approve_mapping, infer_schema, map_schema, resolve_mapping, transform_file
from .review import review_mapping
from .serialization import dumps, loads
from .store import Store

HOSTED_MAX_BYTES = 1024 * 1024


class Service:
    def __init__(self, root: Path, *, mode="test"):
        if mode not in {"test", "live"}:
            raise ValueError("explicit payment mode required")
        self.store = Store(root)
        from .marketing import Marketing
        self.marketing = Marketing(self.store)
        self.mode = mode

    def propose(self, owner, *, source, format, target_json, vendor, domain):
        if format not in {"csv", "json", "jsonl", "ndjson"}:
            raise ValueError("supported source formats: csv, json, jsonl, ndjson")
        if not all(isinstance(v, str) and 0 < len(v) <= 128 for v in (vendor, domain)):
            raise ValueError("vendor and domain labels need 1..128 characters")
        if not isinstance(source, str) or len(source.encode("utf-8")) > HOSTED_MAX_BYTES:
            raise ValueError("hosted source limit is 1 MiB")
        if not isinstance(target_json, str) or len(target_json.encode("utf-8")) > 131072:
            raise ValueError("hosted target contract limit is 128 KiB")
        target = loads(target_json)
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(dir=self.store.root) as temporary:
            path = Path(temporary) / ("source." + format)
            path.write_text(source, encoding="utf-8", newline="")
            inferred = infer_schema(path)
        mapping = map_schema(inferred, target)
        if mapping["status"] == "unsupported":
            raise ValueError("unsupported contract: " + dumps(mapping["unsupported_contracts"]))
        identifier = self.store.save_preview(owner, mapping, format, source.encode("utf-8"))
        review = review_mapping(mapping)
        for field in review["fields"]:
            field["target_contract_json"] = dumps(field["target_contract"])
        return {"id": identifier, "review": review, "vendor": vendor, "domain": domain,
                "source_fields": sorted(inferred["properties"]), "expires_in": 3600}

    def approve(self, owner, identifier, *, expected_fingerprint, decisions, reviewer, vendor, domain):
        preview = self.store.preview(owner, identifier)
        mapping = preview["mapping"]
        if expected_fingerprint != mapping["mapping_fingerprint"]:
            raise ValueError("proposal changed since review")
        resolved = resolve_mapping(mapping, decisions)
        scope = {"user": owner, "vendor": vendor, "domain": domain}
        approved = approve_mapping(resolved, reviewer=reviewer, scope=scope)
        # Every actual row must produce valid output before purchase is possible.
        result = transform_file(self.store.files / preview["source_path"], approved, scope=scope)
        receipt = result["receipt"]
        from .engine import generate_python
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("mapping.approved.json", dumps(approved) + "\n")
            archive.writestr("transform.py", generate_python(approved))
            archive.writestr("validation-receipt.json", dumps(receipt) + "\n")
            archive.writestr("README.txt", (
                "Schema Mapper reviewed artifact\n\n"
                "Install the free contentsweep-schema-mapper 2.0.0b2 runtime wheel (Python 3.12+).\n"
                "The generated Python imports the matching runtime; it is not self-contained.\n"
                "Keep mapping.approved.json. Reuse for this user/vendor/domain is free.\n"
                "Structural/format changes require review; matching names/types cannot establish business meaning.\n"
                "This bundle contains schema/decisions and validation counts, not raw source/output rows.\n"
                f"Engine: {receipt['engine_version']}\n"
            ))
        data = bundle.getvalue()
        if len(data) > 2 * HOSTED_MAX_BYTES:
            raise ValueError("artifact exceeds hosted capacity")
        order = self.store.save_order(owner, approved, data, self.mode)
        return self.summary(order, receipt=receipt)

    def summary(self, order, *, receipt=None):
        return {"id": order["id"], "status": order["status"], "amount_cents": order["amount"],
                "currency": order["currency"], "mode": order["mode"],
                "mapping_fingerprint": order["fingerprint"], "artifact_sha256": order["artifact_hash"],
                "receipt": receipt, "download_available": order["status"] == "paid" and not order["refunded"]
                and order["expires"] > time.time(), "download_retention_days": 7}

    def artifact_available(self, order):
        path = self.store.files / order["artifact"]
        if order["expires"] <= time.time() or not path.is_file():
            raise ValueError("artifact expired; do not start a new payment")
        if hashlib.sha256(path.read_bytes()).hexdigest() != order["artifact_hash"]:
            raise ValueError("artifact integrity check failed")
