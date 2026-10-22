"""Local-file readers. No URLs, permissive decoding or script execution."""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .serialization import MAX_BYTES, MAX_CELL, MAX_FIELDS, MAX_ROWS, SAMPLE_ROWS, loads

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
NUMERIC = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,3})?\Z")
INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")


def read_text(path: str | Path) -> tuple[str, str, int]:
    path = Path(path)
    if path.is_symlink():
        raise ValueError("symbolic link inputs are unsupported")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("input must be a regular local file")
    with path.open("rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("input must be a regular local file")
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("input exceeds 4 MiB byte limit")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("input must be strict UTF-8") from None
    if text.startswith("\ufeff"):
        raise ValueError("UTF-8 BOM is unsupported; save UTF-8 without BOM")
    if "\0" in text:
        raise ValueError("NUL characters are unsupported")
    return text, "sha256:" + hashlib.sha256(data).hexdigest(), len(data)


def load_document(path: str | Path) -> dict[str, Any]:
    if Path(path).suffix.lower() != ".json":
        raise ValueError("contracts and artifacts must be strict .json files")
    value = loads(read_text(path)[0])
    if not isinstance(value, dict):
        raise TypeError("document must be a JSON object")
    return value


def check_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or not record or len(record) > MAX_FIELDS:
        raise ValueError("record must be a nonempty flat object with at most 128 fields")
    for name, value in record.items():
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("field names must be ASCII identifiers, at most 128 characters")
        if isinstance(value, (dict, list)) or not isinstance(value, (str, int, Decimal, bool, type(None))):
            raise TypeError("records support flat primitives; use Decimal instead of Python float")
        if isinstance(value, str) and len(value.encode("utf-8")) > MAX_CELL:
            raise ValueError("field exceeds 64 KiB byte limit")
        if isinstance(value, int) and value.bit_length() > 850:
            raise ValueError("integer exceeds 256 decimal digit limit")
        if isinstance(value, Decimal) and (
            not value.is_finite() or len(value.as_tuple().digits) > 256
            or abs(value.as_tuple().exponent) > 1000
        ):
            raise ValueError("decimal exceeds supported bounds")
    return record


def _object_end(text: str, start: int) -> int:
    """Preflight a flat object and field count before allocating its dictionary."""
    if start >= len(text) or text[start] != "{":
        raise ValueError("record must be a nonempty flat object")
    quoted = escaped = False
    fields = 0
    for index in range(start + 1, len(text)):
        char = text[index]
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
            raise ValueError("records support flat primitives only")
        elif char == ":":
            fields += 1
            if fields > MAX_FIELDS:
                raise ValueError("record exceeds 128 fields")
        elif char == "}":
            return index + 1
    raise ValueError("malformed JSON object")


def _flat_object(text: str) -> dict[str, Any]:
    text = text.strip()
    end = _object_end(text, 0)
    if text[end:].strip():
        raise ValueError("malformed JSON: trailing content")
    return check_record(loads(text))


def _json_records(text: str) -> Iterator[dict[str, Any]]:
    """Decode one bounded record at a time, including top-level JSON arrays."""
    text = text.strip()
    if not text.startswith("["):
        yield _flat_object(text)
        return
    index, count = 1, 0
    if text[index:].strip() == "]":
        return
    while True:
        while index < len(text) and text[index] in " \r\n\t":
            index += 1
        count += 1
        if count > MAX_ROWS:
            raise ValueError("input exceeds 50000 records")
        end = _object_end(text, index)
        yield check_record(loads(text[index:end]))
        index = end
        while index < len(text) and text[index] in " \r\n\t":
            index += 1
        if index < len(text) and text[index] == "]":
            if text[index + 1:].strip():
                raise ValueError("malformed JSON: trailing content")
            return
        if index >= len(text) or text[index] != ",":
            raise ValueError("malformed JSON array")
        index += 1


@dataclass
class Dataset:
    text: str
    format: str
    content_fingerprint: str
    byte_count: int

    def records(self) -> Iterator[dict[str, Any]]:
        if self.format == "csv":
            state = "start"
            columns = 1
            for char in self.text:
                if state != "quoted":
                    if char == ",":
                        columns += 1
                        if columns > MAX_FIELDS:
                            raise ValueError("CSV row exceeds 128 columns")
                    elif char in "\r\n":
                        columns = 1
                if state == "quoted":
                    if char == '"':
                        state = "end"
                elif state == "end":
                    if char == '"':
                        state = "quoted"
                    elif char in ",\r\n":
                        state = "start"
                    else:
                        raise ValueError("malformed CSV: characters after closing quote")
                elif char == '"':
                    if state != "start":
                        raise ValueError("malformed CSV: quote inside unquoted field")
                    state = "quoted"
                elif char in ",\r\n":
                    state = "start"
                else:
                    state = "plain"
            reader = csv.reader(io.StringIO(self.text, newline=""), strict=True)
            try:
                header = next(reader, None)
                if not header or len(header) > MAX_FIELDS or len(set(header)) != len(header):
                    raise ValueError("CSV requires unique nonempty headers, at most 128")
                check_record(dict.fromkeys(header))
                for index, row in enumerate(reader, 1):
                    if index > MAX_ROWS:
                        raise ValueError("input exceeds 50000 records")
                    if len(row) != len(header):
                        raise ValueError(f"CSV row {index}: column count differs from header")
                    yield check_record(dict(zip(header, row, strict=True)))
            except csv.Error:
                raise ValueError("malformed CSV") from None
            return
        if self.format == "json":
            values = _json_records(self.text)
        else:
            # Generator prevents allocating all parsed JSONL rows at once.
            def lines():
                for index, line in enumerate(io.StringIO(self.text), 1):
                    if index > MAX_ROWS:
                        raise ValueError("input exceeds 50000 records")
                    if not line.strip():
                        raise ValueError(f"JSONL line {index}: blank lines are unsupported")
                    try:
                        yield _flat_object(line)
                    except ValueError as exc:
                        raise ValueError(f"JSONL line {index}: {exc}") from None
            values = lines()
        for index, record in enumerate(values, 1):
            if index > MAX_ROWS:
                raise ValueError("input exceeds 50000 records")
            yield check_record(record)


def dataset(path: str | Path) -> Dataset:
    suffix = Path(path).suffix.lower()
    formats = {".csv": "csv", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl"}
    if suffix not in formats:
        raise ValueError("supported source formats: .csv, .json, .jsonl, .ndjson")
    text, digest, size = read_text(path)
    return Dataset(text, formats[suffix], digest, size)


def primitive(value: Any, *, csv_input: bool = False) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, Decimal):
        return "integer" if value == value.to_integral_value() else "number"
    if csv_input and isinstance(value, str):
        if INTEGER.fullmatch(value) and len(value) <= 256:
            return "integer"
        if NUMERIC.fullmatch(value):
            number = Decimal(value)
            if len(number.as_tuple().digits) <= 256 and abs(number.as_tuple().exponent) <= 1000:
                return "number"
    return "string"


def infer_dataset(data: Dataset) -> dict[str, Any]:
    observed: dict[str, dict[str, Any]] = {}
    total = 0
    for total, record in enumerate(data.records(), 1):
        if total > SAMPLE_ROWS:
            continue  # Still parse and structurally validate every remaining row.
        for name, value in record.items():
            if name not in observed and len(observed) >= MAX_FIELDS:
                raise ValueError("source schema exceeds 128 fields")
            item = observed.setdefault(name, {"types": set(), "present": 0, "null": 0, "empty": 0})
            item["types"].add(primitive(value, csv_input=data.format == "csv"))
            item["present"] += 1
            item["null"] += value is None
            item["empty"] += value == ""
    if not total:
        raise ValueError("input has no records")
    if len(observed) > MAX_FIELDS:
        raise ValueError("source schema exceeds 128 fields")
    sampled = min(total, SAMPLE_ROWS)
    properties, required = {}, []
    for name, item in sorted(observed.items()):
        types = item["types"] - {"null"}
        if types <= {"integer", "number"} and types:
            kind = "number" if "number" in types else "integer"
        elif not types:
            kind = "null"
        elif len(types) == 1:
            kind = next(iter(types))
        elif data.format == "csv":
            kind = "string"
        else:
            raise ValueError("heterogeneous primitive types are unsupported")
        properties[name] = {"type": [kind, "null"] if item["null"] and kind != "null" else kind}
        if item["present"] == sampled:
            required.append(name)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "properties": properties, "required": required, "additionalProperties": False,
        "x-source-fingerprint": data.content_fingerprint,
        "x-inference": {
            "version": "1", "format": data.format, "locale": "invariant",
            "bytes": data.byte_count, "sampled_records": sampled, "total_records": total,
            "sample_complete": sampled == total, "all_records_parsed": True,
            "fields": {name: {key: item[key] for key in ("present", "null", "empty")}
                       for name, item in sorted(observed.items())},
        },
    }
