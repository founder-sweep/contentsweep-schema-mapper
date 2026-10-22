"""A compact review surface, deliberately containing metadata rather than rows."""
from __future__ import annotations

from typing import Any

from .engine import validate_mapping


def review_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    used = {p[2:] for field in mapping["fields"] for p in field["decision"]["sources"]}
    return {
        "mapping_fingerprint": mapping["mapping_fingerprint"],
        "engine_version": mapping["engine_version"], "revision": mapping["revision"],
        "source_schema_fingerprint": mapping["source_schema_fingerprint"],
        "source_fingerprint": mapping["source_fingerprint"],
        "target_fingerprint": mapping["target_fingerprint"], "scope": mapping["scope"],
        "status": mapping["status"], "coverage": mapping["source_schema"].get("x-inference"),
        "fields": [{
            "target": f["target_path"], "required": f["target_path"][2:] in mapping["target"]["required"],
            "decision": f["decision"], "evidence": f["evidence"],
            "match_score": f["match_score"], "alternatives": f["alternatives"],
            "target_contract": mapping["target"]["properties"][f["target_path"][2:]],
        } for f in mapping["fields"]],
        "unused_source_fields": sorted(set(mapping["source_schema"]["properties"]) - used),
        "score_meaning": mapping["score_meaning"], "validation": validate_mapping(mapping, mapping["target"]),
        "approval": mapping["approval"],
        "review_instruction": "Verify business meaning, casts, nullability and constraints. Resolve all ambiguity and optional skips. Approval applies only to this exact version and explicit scope.",
    }
