"""Bounded strict JSON and lossless numeric serialization for owned artifacts."""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_BYTES = 4 * 1024 * 1024
MAX_FIELDS = 128
MAX_ROWS = 50_000
MAX_CELL = 65_536
SAMPLE_ROWS = 10_000


def _number(text: str) -> Decimal:
    if len(text) > 1500:
        raise ValueError("number token exceeds supported bounds")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError("number exceeds supported precision/exponent bounds") from None
    if len(value.as_tuple().digits) > 256 or abs(value.as_tuple().exponent) > 1000:
        raise ValueError("number exceeds supported precision/exponent bounds")
    return value


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


def loads(text: str) -> Any:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("JSON exceeds 4 MiB byte limit")
    # Bound parser recursion before json allocates containers. Artifacts have
    # shallow nested metadata; raw records are checked separately for flatness.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 24:
                raise ValueError("JSON nesting exceeds 24 levels")
        elif char in "]}":
            depth -= 1
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_float=_number,
                          parse_int=lambda s: int(_number(s)), parse_constant=_constant)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON at line {exc.lineno}, column {exc.colno}") from None


def dumps(value: Any) -> str:
    """Canonical compact JSON; Decimal tokens stay JSON numbers, never floats."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if value.bit_length() > 3400:
            raise ValueError("integer exceeds supported bounds")
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite decimal")
        _number(str(value))
        # Equivalent JSON numeric tokens have the same fingerprint.
        sign, digits, exponent = value.as_tuple()
        digits = list(digits)
        while len(digits) > 1 and digits[-1] == 0 and exponent < 1000:
            digits.pop()
            exponent += 1
        if not any(digits):
            return "0"
        token = "".join(map(str, digits))
        if exponent >= 0 and len(token) + exponent <= 256:
            return ("-" if sign else "") + token + "0" * exponent
        return ("-" if sign else "") + token + (f"e{exponent}" if exponent else "")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float")
        return dumps(_number(repr(value)))
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, list):
        return "[" + ",".join(dumps(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return "{" + ",".join(dumps(key) + ":" + dumps(value[key]) for key in sorted(value)) + "}"
    raise ValueError("unsupported JSON value")


def fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()
