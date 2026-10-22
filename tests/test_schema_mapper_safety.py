from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from mcp_products.schema_mapper.contracts import contract, validate_record
from mcp_products.schema_mapper.engine import (
    approve_mapping,
    check_drift,
    generate_python,
    infer_schema,
    map_schema,
    resolve_mapping,
    transform_file,
    transform_record,
    validate_mapping,
)
from mcp_products.schema_mapper.parsing import dataset
from mcp_products.schema_mapper.serialization import MAX_BYTES, dumps, loads

SCOPE = {"user": "customer-a", "vendor": "vendor-a", "domain": "contacts"}


def schema(**types):
    return {"properties": {name: {"type": kind} for name, kind in types.items()}}


def approved(source, target):
    return approve_mapping(map_schema(source, target), reviewer="fixture reviewer", scope=SCOPE)


@pytest.mark.parametrize(("suffix", "content", "error"), [
    (".csv", b"id,id\n1,2\n", "headers"),
    (".csv", b"id,name\n1\n", "column"),
    (".csv", b'id\n"unterminated\n', "malformed CSV"),
    (".csv", b'id\nba"d\n', "malformed CSV"),
    (".csv", b"id\n\xff\n", "UTF-8"),
    (".jsonl", b'{"id":1,"id":2}\n', "duplicate"),
    (".json", b'[{"id":1,"id":2}]', "duplicate"),
    (".json", b'{"id":NaN}', "non-finite"),
    (".json", b'{"id":1e99999}', "bounds"),
    (".json", b'{"id":1e999999999999999999999999999999999}', "bounds"),
    (".json", b'{"id":{"nested":1}}', "flat"),
    (".json", b'[1,2]', "flat"),
    (".json", b'{"bad.path":1}', "ASCII"),
    (".jsonl", b'{"id":1}\n\n', "blank"),
    (".jsonl", b'{"id":1}\n{broken}\n', "line 2"),
    (".txt", b"id\n1", "formats"),
    (".json", b"\xef\xbb\xbf{}", "BOM"),
    (".json", b'{"id":1,"name":2} trailing', "malformed"),
    (".json", b'[{"id":1},]', "flat"),
    (".json", b'[{"id":1}] trailing', "trailing"),
])
def test_refuses_malformed_or_unsupported_input(tmp_path, suffix, content, error):
    path = tmp_path / ("input" + suffix)
    path.write_bytes(content)
    with pytest.raises((ValueError, TypeError), match=error):
        infer_schema(path)


def test_resource_bounds_and_preparser_depth(tmp_path):
    path = tmp_path / "big.csv"
    path.write_bytes(b"id\n" + b"x" * MAX_BYTES)
    with pytest.raises(ValueError, match="4 MiB"):
        infer_schema(path)
    path.write_text("id\n" + "x" * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        infer_schema(path)
    with pytest.raises(ValueError, match="nesting"):
        loads("[" * 25 + "0" + "]" * 25)
    path.write_text("id\n" + "1\n" * 50001)
    with pytest.raises(ValueError, match="50000"):
        infer_schema(path)
    path.write_text(",".join(f"f{i}" for i in range(129)) + "\n")
    with pytest.raises(ValueError, match="128"):
        infer_schema(path)


def test_full_validation_beyond_inference_sample(tmp_path):
    path = tmp_path / "sample.jsonl"
    path.write_text('{"id":1}\n' * 10001)
    inferred = infer_schema(path)
    assert inferred["x-inference"]["sampled_records"] == 10000
    assert inferred["x-inference"]["total_records"] == 10001
    assert not inferred["x-inference"]["sample_complete"]
    path.write_text(path.read_text() + "{bad}\n")
    with pytest.raises(ValueError, match="line 10002"):
        infer_schema(path)


def test_null_missing_empty_and_identifiers_are_distinct(tmp_path):
    path = tmp_path / "source.jsonl"
    path.write_text('{"id":"001","note":null,"empty":""}\n{"id":"002","empty":""}\n')
    inferred = infer_schema(path)
    assert inferred["properties"]["id"]["type"] == "string"
    assert inferred["properties"]["note"]["type"] == "null"
    assert "note" not in inferred["required"]
    target = schema(id="string", note=["string", "null"], empty="string")
    target["required"] = ["id", "empty"]
    proposal = map_schema(inferred, target)
    proposal = resolve_mapping(proposal, {"note": "note"})
    mapping = approve_mapping(proposal, reviewer="fixture", scope=SCOPE)
    assert transform_file(path, mapping, scope=SCOPE)["records"] == [
        {"id": "001", "note": None, "empty": ""}, {"id": "002", "empty": ""}]
    target["properties"]["note"] = {"type": "string"}
    bad = approve_mapping(resolve_mapping(map_schema(inferred, target), {"note": "note"}),
                          reviewer="fixture", scope=SCOPE)
    with pytest.raises(ValueError, match="type mismatch"):
        transform_file(path, bad, scope=SCOPE)


def test_csv_leading_zero_and_mixed_string_preservation(tmp_path):
    path = tmp_path / "ids.csv"
    path.write_text("id,note\n001,123\n002,abc\n")
    inferred = infer_schema(path)
    assert inferred["properties"] == {"id": {"type": "string"}, "note": {"type": "string"}}
    mapping = approved(inferred, schema(id="string", note="string"))
    assert transform_file(path, mapping, scope=SCOPE)["records"][0] == {"id": "001", "note": "123"}
    wrong = resolve_mapping(map_schema(inferred, schema(id="integer")), {"id": "id"})
    wrong = approve_mapping(wrong, reviewer="fixture", scope=SCOPE)
    with pytest.raises(ValueError, match="leading zeros"):
        transform_file(path, wrong, scope=SCOPE)


def test_exact_decimals_large_integers_and_generated_backend(tmp_path):
    path = tmp_path / "money.json"
    path.write_text('[{"amount":12345678901234567890.1234567890123456789,"id":9007199254740993}]')
    target = schema(amount="number", id="integer")
    target["properties"]["amount"]["minimum"] = Decimal("12345678901234567890.1234567890123456788")
    mapping = approved(infer_schema(path), target)
    mapping = loads(dumps(mapping))
    expected = {"amount": Decimal("12345678901234567890.1234567890123456789"), "id": 9007199254740993}
    assert transform_file(path, mapping, scope=SCOPE)["records"] == [expected]
    namespace = {}
    exec(generate_python(mapping), namespace)  # noqa: S102 - own compiler output only
    assert namespace["transform_record"](expected) == transform_record(expected, mapping) == expected
    assert loads(dumps(expected)) == expected
    with pytest.raises((ValueError, TypeError), match="float"):
        transform_record({"amount": 1.1, "id": 1}, mapping)
    with pytest.raises(ValueError, match="range"):
        namespace["transform_record"]({"amount": Decimal("1.1"), "id": 1})
    with pytest.raises(ValueError, match="function"):
        generate_python(mapping, function_name="class")


def test_approval_tampering_scope_and_drift(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("id\n1\n")
    target = schema(id="integer")
    proposal = map_schema(infer_schema(path), target)
    for operation in (lambda: generate_python(proposal), lambda: transform_record({"id": "1"}, proposal)):
        with pytest.raises(ValueError, match="approval"):
            operation()
    mapping = approve_mapping(proposal, reviewer="fixture", scope=SCOPE)
    modified = copy.deepcopy(mapping)
    modified["fields"][0]["evidence"]["lexical"] = 0
    assert not validate_mapping(modified, target)["accepted"]
    with pytest.raises(ValueError, match="approval"):
        transform_record({"id": "1"}, modified)
    with pytest.raises(ValueError, match="scope"):
        transform_file(path, mapping, scope={**SCOPE, "user": "customer-b"})
    path.write_text("id\n2\n")
    assert check_drift(mapping, infer_schema(path), scope=SCOPE)["content_changed"]
    assert transform_file(path, mapping, scope=SCOPE)["records"] == [{"id": 2}]
    path.write_text("id,new\n2,x\n")
    assert check_drift(mapping, infer_schema(path), scope=SCOPE)["changes"]["added"] == ["new"]
    with pytest.raises(ValueError, match="drift"):
        transform_file(path, mapping, scope=SCOPE)
    with pytest.raises(ValueError, match="drift"):
        transform_record({"id": "1", "new": "x"}, mapping)
    with pytest.raises(ValueError, match="reviewer"):
        approve_mapping(proposal, reviewer="", scope=SCOPE)


def test_order_independent_collisions_ties_and_explicit_resolution():
    source = schema(name="string", other="string")
    target = schema(name="string", full_name="string")
    one = map_schema(source, target)
    two = map_schema(source, {"properties": dict(reversed(list(target["properties"].items())))})
    assert one["mapping_fingerprint"] == two["mapping_fingerprint"]
    assert all(f["decision"]["kind"] == "ambiguous" for f in one["fields"])
    assert not validate_mapping(one, target)["valid"]
    fixed = resolve_mapping(one, {"name": "name", "full_name": "other"})
    mapping = approve_mapping(fixed, reviewer="fixture", scope=SCOPE)
    assert transform_record({"name": "Ada", "other": "Ada Lovelace"}, mapping)["full_name"] == "Ada Lovelace"
    with pytest.raises(ValueError, match="duplicate"):
        resolve_mapping(one, {"name": "name", "full_name": "name"})
    tie = map_schema(schema(billing_email="string", shipping_email="string"), schema(email="string"))
    assert tie["fields"][0]["decision"]["kind"] == "ambiguous"


def test_complete_mapping_required_fields_optional_skips_and_paths():
    target = schema(id="integer", comment="string")
    target["required"] = ["id"]
    proposal = map_schema(schema(id="integer"), target)
    mapping = approve_mapping(resolve_mapping(proposal, {"comment": None}), reviewer="fixture", scope=SCOPE)
    assert transform_record({"id": 1}, mapping) == {"id": 1}
    with pytest.raises(ValueError, match="optional"):
        resolve_mapping(proposal, {"id": None, "comment": None})
    for mutation in (
        lambda m: m["fields"].pop(),
        lambda m: m["fields"].append(copy.deepcopy(m["fields"][0])),
        lambda m: m["fields"][0]["decision"].update(sources=["$.unknown"]),
        lambda m: m["transformation_ir"].update(operations=[]),
    ):
        bad = copy.deepcopy(mapping)
        mutation(bad)
        assert not validate_mapping(bad, target)["valid"]
    assert not validate_mapping([], target)["valid"]
    assert not validate_mapping(mapping, schema(id="string"))["valid"]


@pytest.mark.parametrize("extra", [
    {"pattern": ".*"}, {"format": "email"}, {"multipleOf": 2}, {"default": 1},
    {"minLength": 1}, {"type": "array"}, {"type": ["integer", "string"]},
    {"minimum": "1"}, {"minimum": 3, "maximum": 1}, {"enum": [True]},
])
def test_unsupported_or_invalid_constraints_fail_closed(extra):
    target = schema(id="integer")
    target["properties"]["id"].update(extra)
    with pytest.raises((ValueError, TypeError)):
        contract(target)


def test_output_enum_range_and_type_validation():
    target = schema(id="integer", active="boolean")
    target["required"] = ["id"]
    target["additionalProperties"] = False
    target["properties"]["id"].update(enum=[1, 2], minimum=1, maximum=2)
    validate_record({"id": 1, "active": True}, target)
    for row in ({"id": True}, {"id": 3}, {"id": 1, "active": "true"}, {"active": True}, {"id": 1, "extra": 1}):
        with pytest.raises(ValueError):
            validate_record(row, target)
    with pytest.raises(ValueError, match="enum"):
        contract({"properties": {"id": {"type": "integer", "enum": [1, 1]}}})


def test_reject_fifo_and_symlink_without_blocking(tmp_path):
    import os
    fifo = tmp_path / "input.csv"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular"):
        dataset(fifo)
    link = tmp_path / "link.csv"
    link.symlink_to(fifo)
    with pytest.raises(ValueError, match="symbolic"):
        dataset(link)


def test_format_and_late_sample_drift_fail(tmp_path):
    source = tmp_path / "a.csv"
    source.write_text("id\n1\n")
    mapping = approved(infer_schema(source), schema(id="integer"))
    json_source = tmp_path / "a.json"
    json_source.write_text('{"id":1}')
    assert check_drift(mapping, infer_schema(json_source), scope=SCOPE)["changes"]["format_changed"]
    with pytest.raises(ValueError, match="drift"):
        transform_file(json_source, mapping, scope=SCOPE)
    source.write_text("id\n" + "1\n" * 10000 + "x\n")
    with pytest.raises(ValueError, match="record 10001"):
        transform_file(source, mapping, scope=SCOPE)


def test_generated_and_interpreter_refuse_same_invalid_records(tmp_path):
    source = tmp_path / "a.csv"
    source.write_text("id\n1\n")
    mapping = approved(infer_schema(source), schema(id="integer"))
    namespace = {}
    exec(generate_python(mapping), namespace)  # noqa: S102 - own compiler output
    for record in ({}, {"id": "1.1"}, {"id": "001"}, {"id": True}, {"id": None}):
        messages = []
        for transform in (lambda row: transform_record(row, mapping), namespace["transform_record"]):
            with pytest.raises(ValueError) as caught:
                transform(record)
            messages.append(str(caught.value))
        assert messages[0] == messages[1]


def test_extreme_supported_decimal_serialization_roundtrips():
    for token in ("1e1000", "1e-1000", "1.2300", "100e1000", "10", "-0.00"):
        value = loads(token)
        assert loads(dumps(value)) == value


def test_json_array_parser_checks_row_and_field_limits_before_container_load(tmp_path, monkeypatch):
    from mcp_products.schema_mapper import parsing
    actual_loads = parsing.loads
    decoded = []

    def measured(text):
        decoded.append(len(text))
        return actual_loads(text)

    monkeypatch.setattr(parsing, "loads", measured)
    path = tmp_path / "input.json"
    path.write_text('[{"id":1},{"id":2}]')
    assert infer_schema(path)["x-inference"]["total_records"] == 2
    assert len(decoded) == 2 and max(decoded) < len(path.read_text())
    decoded.clear()
    path.write_text("{" + ",".join(f'"f{i}":1' for i in range(129)) + "}")
    with pytest.raises(ValueError, match="128"):
        infer_schema(path)
    assert decoded == []
