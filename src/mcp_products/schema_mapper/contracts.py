"""An explicit subset, not a general JSON Schema implementation."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .parsing import NAME
from .serialization import MAX_FIELDS, dumps, fingerprint

DIALECT = "https://json-schema.org/draft/2020-12/schema"
TYPES = {"string", "integer", "number", "boolean", "null"}


def kinds(spec: dict[str, Any]) -> set[str]:
    value = spec.get("type")
    if isinstance(value, str) and value in TYPES:
        return {value}
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, str) for v in value):
        result = set(value)
        if "null" in result and len(result) == 2 and result <= TYPES:
            return result
    raise ValueError("unsupported type; use a primitive or primitive/null union")


def base_kind(spec: dict[str, Any]) -> str:
    return next(iter(kinds(spec) - {"null"}), "null")


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, Decimal)) and not isinstance(value, bool)


def fits(value: Any, spec: dict[str, Any]) -> bool:
    allowed = kinds(spec)
    if value is None:
        return "null" in allowed
    if isinstance(value, bool):
        return "boolean" in allowed
    if isinstance(value, str):
        return "string" in allowed
    if _numeric(value):
        return "number" in allowed or "integer" in allowed and value == int(value)
    return False


def check_value(value: Any, spec: dict[str, Any]) -> None:
    if not fits(value, spec):
        raise ValueError("output type mismatch")
    if "enum" in spec and not any(value == other and (
        isinstance(value, bool) == isinstance(other, bool)
    ) for other in spec["enum"]):
        raise ValueError("output enum mismatch")
    if value is not None and _numeric(value) and (
        "minimum" in spec and value < spec["minimum"]
        or "maximum" in spec and value > spec["maximum"]
    ):
        raise ValueError("output numeric range mismatch")


def contract(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise TypeError("schema must be an object")
    allowed = {"$schema", "type", "properties", "required", "additionalProperties", "title",
               "description", "x-source-fingerprint", "x-inference"}
    if set(schema) - allowed:
        raise ValueError("unsupported root schema keyword")
    if schema.get("$schema", DIALECT) != DIALECT or schema.get("type", "object") != "object":
        raise ValueError("only the documented flat 2020-12 object subset is supported")
    props = schema.get("properties")
    if not isinstance(props, dict) or not 0 < len(props) <= MAX_FIELDS:
        raise ValueError("schema needs 1..128 properties")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(n, str) for n in required):
        raise ValueError("required must list field names")
    if len(set(required)) != len(required) or set(required) - set(props):
        raise ValueError("duplicate or unknown required field")
    additional = schema.get("additionalProperties", True)
    if not isinstance(additional, bool):
        raise TypeError("additionalProperties must be boolean")
    for name, spec in props.items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(spec, dict):
            raise ValueError("invalid flat field or property specification")
        if set(spec) - {"type", "enum", "minimum", "maximum", "title", "description"}:
            raise ValueError("unsupported target type or constraint")
        kinds(spec)
        for label in ("title", "description"):
            if label in spec and not isinstance(spec[label], str):
                raise ValueError("schema annotations must be strings")
        if "enum" in spec:
            values = spec["enum"]
            if not isinstance(values, list) or not 0 < len(values) <= 128:
                raise ValueError("enum must have 1..128 primitive values")
            if any(not fits(value, spec) for value in values):
                raise ValueError("enum value is incompatible with field type")
            if len({dumps(v) for v in values}) != len(values):
                raise ValueError("duplicate enum value")
        for bound in ("minimum", "maximum"):
            if bound in spec and (base_kind(spec) not in {"integer", "number"} or not _numeric(spec[bound])):
                raise ValueError("numeric bounds require exact numeric field and limit")
        if "minimum" in spec and "maximum" in spec and spec["minimum"] > spec["maximum"]:
            raise ValueError("minimum exceeds maximum")
    result = {"$schema": DIALECT, "type": "object",
              "properties": {name: props[name] for name in sorted(props)},
              "required": sorted(required), "additionalProperties": additional}
    for label in ("title", "description"):
        if label in schema:
            if not isinstance(schema[label], str):
                raise ValueError("schema annotations must be strings")
            result[label] = schema[label]
    dumps(result)
    return result


def schema_fingerprint(schema: dict[str, Any]) -> str:
    return fingerprint(contract(schema))


def validate_record(record: dict[str, Any], schema: dict[str, Any]) -> None:
    schema = contract(schema)
    if set(schema["required"]) - set(record):
        raise ValueError("required output field missing")
    if not schema["additionalProperties"] and set(record) - set(schema["properties"]):
        raise ValueError("unknown output field")
    for name, spec in schema["properties"].items():
        if name in record:
            try:
                check_value(record[name], spec)
            except ValueError as exc:
                raise ValueError(f"{name}: {exc}") from None
