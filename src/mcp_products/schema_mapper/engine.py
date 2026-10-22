"""Deterministic, review-bound schema correspondence and primitive transforms."""
from __future__ import annotations

import copy
import keyword
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .contracts import base_kind, check_value, contract, kinds, schema_fingerprint, validate_record
from .parsing import NUMERIC, check_record, dataset, infer_dataset, primitive
from .serialization import MAX_BYTES, dumps, fingerprint

ENGINE_VERSION = "schema-mapper/2.0.0"
ARTIFACT_VERSION = "2.0"


def infer_schema(path: str | Path) -> dict[str, Any]:
    return infer_dataset(dataset(path))


def _tokens(name: str) -> set[str]:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return set(re.findall(r"[a-z]+|[0-9]+", name.lower()))


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if left.lower().replace("_", "") == right.lower().replace("_", ""):
        return 1.0
    return len(a & b) / len(a | b) if a and b else 0.0


def _payload(mapping: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items()
            if key not in {"approval", "status", "mapping_fingerprint"}}


def _seal(mapping: dict[str, Any]) -> dict[str, Any]:
    mapping["mapping_fingerprint"] = fingerprint(_payload(mapping))
    return mapping


def _ir(mapping: dict[str, Any]) -> dict[str, Any]:
    return {"version": "1", "operations": [
        {"op": "cast", "source": field["decision"]["sources"][0],
         "target": field["target_path"],
         "to": base_kind(mapping["target"]["properties"][field["target_path"][2:]])}
        for field in mapping["fields"] if field["decision"]["kind"] == "direct"
    ]}


def map_schema(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    source_contract = contract(source)
    try:
        target_contract = contract(target)
    except (ValueError, TypeError) as exc:
        # Legacy callers use proposal generation as an informational preview.
        # Preserve that interface without claiming unsupported targets validate
        # or permitting them to receive approval or executable output.
        blocked = {
            "schema_version": ARTIFACT_VERSION, "engine_version": ENGINE_VERSION,
            "source_fingerprint": source.get("x-source-fingerprint"),
            "source_schema_fingerprint": schema_fingerprint(source),
            "target_fingerprint": fingerprint(target), "source_schema": copy.deepcopy(source),
            "target": copy.deepcopy(target), "fields": [], "scope": None, "revision": 1,
            "parent_fingerprint": None, "status": "unsupported", "approval": None,
            "score_meaning": "uncalibrated lexical/type ranking score, not probability",
            "unsupported_contracts": [str(exc)], "transformation_ir": {"version": "1", "operations": []},
        }
        return _seal(blocked)
    source_props, target_props = source_contract["properties"], target_contract["properties"]
    fields = []
    for target_name, target_spec in sorted(target_props.items()):
        candidates = []
        for source_name, source_spec in sorted(source_props.items()):
            lexical = _similarity(source_name, target_name)
            compatible = base_kind(source_spec) == base_kind(target_spec) or (
                base_kind(source_spec) == "integer" and base_kind(target_spec) == "number")
            score = lexical + (0.25 if compatible else 0)
            candidates.append({
                "source_path": f"$.{source_name}", "score": round(score, 4),
                "lexical": round(lexical, 4), "type_compatible": compatible,
            })
        candidates.sort(key=lambda c: (-c["score"], c["source_path"]))
        best = candidates[0] if candidates else None
        runner = candidates[1]["score"] if len(candidates) > 1 else 0
        direct = bool(best and best["type_compatible"] and best["lexical"] > 0
                      and best["score"] >= 0.5 and best["score"] - runner >= 0.15)
        fields.append({
            "target_path": f"$.{target_name}",
            "decision": {"kind": "direct" if direct else "ambiguous",
                         "sources": [best["source_path"]] if direct else []},
            "match_score": best["score"] if best else 0,
            "confidence_band": "review_required",
            "evidence": {"lexical": best["lexical"] if best else 0,
                         "type_compatible": best["type_compatible"] if best else False,
                         "runner_up_score": runner,
                         "reason": "unique lexical/type candidate" if direct else "insufficient or tied evidence"},
            "alternatives": candidates[:5], "transform": None,
        })
    # Simultaneous conflict abstention prevents target-order advantages.
    claims: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        for path in field["decision"]["sources"]:
            claims.setdefault(path, []).append(field)
    for claimants in claims.values():
        if len(claimants) > 1:
            for field in claimants:
                field["decision"] = {"kind": "ambiguous", "sources": []}
                field["evidence"]["reason"] = "one-to-one source collision; explicit review required"
    mapping = {
        "schema_version": ARTIFACT_VERSION, "engine_version": ENGINE_VERSION,
        "source_fingerprint": source.get("x-source-fingerprint"),
        "source_schema_fingerprint": schema_fingerprint(source),
        "target_fingerprint": schema_fingerprint(target),
        "source_schema": copy.deepcopy(source), "target": target_contract,
        "scope": None, "revision": 1, "parent_fingerprint": None,
        "fields": fields, "status": "review_required" if any(
            f["decision"]["kind"] == "ambiguous" for f in fields) else "proposed",
        "score_meaning": "uncalibrated lexical/type ranking score, not probability",
        "approval": None,
    }
    mapping["transformation_ir"] = _ir(mapping)
    return _seal(mapping)


def _scope(scope: Any) -> dict[str, str]:
    if not isinstance(scope, dict) or set(scope) != {"user", "vendor", "domain"}:
        raise ValueError("scope requires user, vendor and domain")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 128 for v in scope.values()):
        raise ValueError("scope values must be nonempty strings up to 128 characters")
    return scope


def validate_mapping(mapping: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    if not isinstance(mapping, dict):
        return {"valid": False, "accepted": False, "errors": [{"error": "mapping must be an object"}], "checked_fields": 0}

    def error(message: str) -> None:
        errors.append({"error": message})

    try:
        target = contract(target)
        source = contract(mapping["source_schema"])
        if mapping.get("schema_version") != ARTIFACT_VERSION or mapping.get("engine_version") != ENGINE_VERSION:
            error("unsupported artifact or engine version; re-propose and review")
        if schema_fingerprint(target) != mapping.get("target_fingerprint") or contract(mapping["target"]) != target:
            error("target fingerprint mismatch")
        if schema_fingerprint(source) != mapping.get("source_schema_fingerprint"):
            error("source schema fingerprint mismatch")
        if fingerprint(_payload(mapping)) != mapping.get("mapping_fingerprint"):
            error("mapping fingerprint mismatch; use resolve to revise")
        fields = mapping["fields"]
        if not isinstance(fields, list) or len(fields) > 128:
            raise ValueError("fields must be a bounded list")
        seen_targets, seen_sources = set(), set()
        for field in fields:
            path, decision = field["target_path"], field["decision"]
            if path not in {f"$.{n}" for n in target["properties"]} or path in seen_targets:
                error("unknown or duplicate target path")
            seen_targets.add(path)
            kind, paths = decision["kind"], decision["sources"]
            if field.get("transform") is not None:
                error("unsupported transform; only the declared cast IR is accepted")
            if kind == "skip":
                if paths or path[2:] in target["required"]:
                    error("only optional targets may be explicitly skipped")
            elif kind != "direct" or not isinstance(paths, list) or len(paths) != 1:
                error("unresolved mapping requires review")
            else:
                source_path = paths[0]
                if source_path not in {f"$.{n}" for n in source["properties"]} or source_path in seen_sources:
                    error("unknown or duplicate source path")
                seen_sources.add(source_path)
        if seen_targets != {f"$.{n}" for n in target["properties"]}:
            error("mapping must decide every target field, including explicit optional skips")
        if mapping.get("transformation_ir") != _ir(mapping):
            error("transformation IR differs from field decisions")
        approval = mapping.get("approval")
        if approval is not None:
            _scope(mapping["scope"])
            if not isinstance(approval, dict) or approval.get("mapping_fingerprint") != mapping.get("mapping_fingerprint"):
                error("approval does not bind this exact mapping")
            elif not isinstance(approval.get("reviewer"), str) or not approval["reviewer"].strip():
                error("approval needs a named reviewer")
            elif mapping.get("status") != "approved":
                error("approval/status mismatch")
        elif mapping.get("status") not in {"proposed", "review_required"}:
            error("unapproved artifact cannot claim accepted status")
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        error(f"invalid mapping: {exc}")
    return {"valid": not errors, "accepted": not errors and bool(mapping.get("approval")),
            "errors": errors, "checked_fields": len(mapping.get("fields", [])) if isinstance(mapping.get("fields"), list) else 0}


def resolve_mapping(mapping: dict[str, Any], decisions: dict[str, str | None]) -> dict[str, Any]:
    if mapping.get("mapping_fingerprint") != fingerprint(_payload(mapping)):
        raise ValueError("mapping fingerprint mismatch")
    result = copy.deepcopy(mapping)
    known = {f["target_path"][2:] for f in result["fields"]}
    if not isinstance(decisions, dict) or set(decisions) - known:
        raise ValueError("resolution contains an unknown target")
    for field in result["fields"]:
        name = field["target_path"][2:]
        if name in decisions:
            choice = decisions[name]
            if choice is not None and (not isinstance(choice, str) or choice not in result["source_schema"]["properties"]):
                raise ValueError("resolution contains an unknown source")
            field["decision"] = {"kind": "skip" if choice is None else "direct",
                                 "sources": [] if choice is None else [f"$.{choice}"]}
            field["evidence"]["review_resolution"] = "explicit skip" if choice is None else "explicit source choice"
    result.update(approval=None, status="proposed", parent_fingerprint=mapping["mapping_fingerprint"],
                  revision=mapping["revision"] + 1)
    result["transformation_ir"] = _ir(result)
    _seal(result)
    report = validate_mapping(result, result["target"])
    if not report["valid"]:
        raise ValueError(dumps(report))
    return result


def approve_mapping(mapping: dict[str, Any], *, reviewer: str, scope: dict[str, str]) -> dict[str, Any]:
    report = validate_mapping(mapping, mapping["target"])
    if not report["valid"]:
        raise ValueError("resolve invalid or ambiguous decisions before approval: " + dumps(report))
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 128:
        raise ValueError("reviewer must be a named string up to 128 characters")
    result = copy.deepcopy(mapping)
    if result.get("scope") != _scope(scope):
        result.update(scope=copy.deepcopy(scope), revision=result["revision"] + 1,
                      parent_fingerprint=mapping["mapping_fingerprint"])
    _seal(result)
    result.update(status="approved", approval={
        "reviewer": reviewer, "at": datetime.now(UTC).isoformat(),
        "mapping_fingerprint": result["mapping_fingerprint"],
    })
    return result


def _require_approval(mapping: dict[str, Any]) -> None:
    report = validate_mapping(mapping, mapping["target"])
    if not report["accepted"]:
        raise ValueError("exact mapping approval required: " + dumps(report))


def cast(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, (int, Decimal, bool)):
            return dumps(value)
    elif kind == "boolean":
        if isinstance(value, bool):
            return value
        if value in ("true", "false"):
            return value == "true"
    elif kind in {"integer", "number"} and not isinstance(value, bool):
        if isinstance(value, str) and not NUMERIC.fullmatch(value):
            raise ValueError("numeric cast requires invariant notation; leading zeros and locale separators are refused")
        if isinstance(value, (str, int, Decimal)):
            number = Decimal(value)
            if not number.is_finite() or len(number.as_tuple().digits) > 256 or abs(number.as_tuple().exponent) > 1000:
                raise ValueError("numeric cast exceeds supported bounds")
            if kind == "integer":
                if number != number.to_integral_value():
                    raise ValueError("fractional value cannot be cast to integer")
                if int(number).bit_length() > 850:
                    raise ValueError("integer cast exceeds 256 digit limit")
                return int(number)
            return number
    raise ValueError(f"unsupported or invalid {kind} cast")


def _transform(record: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    check_record(record)
    source = contract(mapping["source_schema"])
    if set(record) - set(source["properties"]):
        raise ValueError("source drift: unknown field")
    if set(source["required"]) - set(record):
        raise ValueError("source drift: required field missing")
    csv_input = mapping["source_schema"].get("x-inference", {}).get("format") == "csv"
    for name, value in record.items():
        observed = primitive(value, csv_input=csv_input)
        allowed = kinds(source["properties"][name])
        if csv_input and "string" in allowed and isinstance(value, str):
            observed = "string"
        if observed not in allowed and not (observed == "integer" and "number" in allowed):
            raise ValueError(f"source drift: type changed for {name}")
        source_value = cast(value, base_kind(source["properties"][name])) if csv_input else value
        try:
            check_value(source_value, source["properties"][name])
        except ValueError as exc:
            raise ValueError(f"source contract {name}: {exc}") from None
    output = {}
    for op in mapping["transformation_ir"]["operations"]:
        source_name, target_name = op["source"][2:], op["target"][2:]
        if source_name not in record:
            if target_name in mapping["target"]["required"]:
                raise ValueError(f"missing mapped value: {target_name}")
            continue
        try:
            output[target_name] = cast(record[source_name], op["to"])
        except ValueError as exc:
            raise ValueError(f"{target_name}: {exc}") from None
    validate_record(output, mapping["target"])
    return output


def transform_record(record: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    _require_approval(mapping)
    return _transform(record, mapping)


def check_drift(mapping: dict[str, Any], source: dict[str, Any], *, scope: dict[str, str]) -> dict[str, Any]:
    _require_approval(mapping)
    if _scope(scope) != mapping["scope"]:
        raise ValueError("scope differs from approved user/vendor/domain")
    before, after = contract(mapping["source_schema"]), contract(source)
    a, b = before["properties"], after["properties"]
    changes = {
        "added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
        "changed": sorted(name for name in set(a) & set(b) if a[name] != b[name]),
        "required_changed": before["required"] != after["required"],
        "format_changed": mapping["source_schema"].get("x-inference", {}).get("format")
        != source.get("x-inference", {}).get("format"),
    }
    compatible = schema_fingerprint(source) == mapping["source_schema_fingerprint"] and not changes["format_changed"]
    return {"compatible": compatible, "status": "compatible" if compatible else "review_required",
            "changes": changes, "source_schema_fingerprint": schema_fingerprint(source),
            "coverage": source.get("x-inference"), "content_changed":
            source.get("x-source-fingerprint") != mapping.get("source_fingerprint")}


def transform_file(path: str | Path, mapping: dict[str, Any], *, scope: dict[str, str]) -> dict[str, Any]:
    data = dataset(path)
    drift = check_drift(mapping, infer_dataset(data), scope=scope)
    if not drift["compatible"]:
        raise ValueError("source schema drift requires new proposal and approval: " + dumps(drift))
    _require_approval(mapping)
    records = []
    output_bytes = 0
    for index, record in enumerate(data.records(), 1):
        try:
            output = _transform(record, mapping)
            output_bytes += len(dumps(output).encode("utf-8")) + 1
            if output_bytes > MAX_BYTES:
                raise ValueError("produced output exceeds 4 MiB limit")
            records.append(output)
        except ValueError as exc:
            raise ValueError(f"record {index}: {exc}") from None
    return {"records": records, "receipt": {
        "engine_version": ENGINE_VERSION, "mapping_fingerprint": mapping["mapping_fingerprint"],
        "source_fingerprint": data.content_fingerprint, "target_fingerprint": mapping["target_fingerprint"],
        "output_fingerprint": fingerprint(records), "records": len(records), "drift": drift,
    }}


def generate_python(mapping: dict[str, Any], function_name: str = "transform_record") -> str:
    _require_approval(mapping)
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise ValueError("invalid function name")
    return (
        "# Schema Mapper runtime-dependent artifact; requires matching installed engine.\n"
        "from mcp_products.schema_mapper.engine import transform_record as _transform\n"
        "from mcp_products.schema_mapper.serialization import loads as _loads\n\n"
        f"_MAPPING = _loads({dumps(mapping)!r})\n\n"
        f"def {function_name}(record):\n"
        "    return _transform(record, _MAPPING)\n"
    )
