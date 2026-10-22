from __future__ import annotations

from mcp_products.schema_mapper.engine import (
    approve_mapping,
    generate_python,
    infer_schema,
    map_schema,
    validate_mapping,
)

SCOPE = {"user": "fixture", "vendor": "fixture", "domain": "customers"}


def test_schema_mapper_maps_matching_fields(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("customer_id,first_name\n1,Ada\n", encoding="utf-8")
    target = {"type": "object", "properties": {"customer_id": {"type": "integer"}, "first_name": {"type": "string"}}}
    mapping = map_schema(infer_schema(source), target)
    assert mapping["status"] == "proposed"
    assert validate_mapping(mapping, target)["valid"]
    assert not validate_mapping(mapping, target)["accepted"]
    mapping = approve_mapping(mapping, reviewer="fixture reviewer", scope=SCOPE)
    assert "def transform_record" in generate_python(mapping)


def test_generated_transform_casts_csv_and_refuses_ambiguity(tmp_path):
    import pytest

    from mcp_products.schema_mapper.engine import transform_record
    source = tmp_path / "data.csv"
    source.write_text("id\n1\n")
    target = {"properties": {"id": {"type": "integer"}}, "required": ["id"]}
    mapping = map_schema(infer_schema(source), target)
    mapping = approve_mapping(mapping, reviewer="fixture reviewer", scope=SCOPE)
    namespace = {}
    exec(generate_python(mapping), namespace)  # noqa: S102 - own generated fixture
    assert namespace["transform_record"]({"id": "1"}) == transform_record({"id": "1"}, mapping) == {"id": 1}
    with pytest.raises(ValueError):
        transform_record({"id": "1.5"}, mapping)
    unresolved = map_schema({"properties": {"id": {"type": "string"}}}, target)
    assert unresolved["status"] == "review_required"
    assert not validate_mapping(unresolved, target)["valid"]
    with pytest.raises(ValueError):
        generate_python(unresolved)

    import json
    source_json = tmp_path / "data.json"
    source_json.write_text(json.dumps([{"amount": 1.5}]))
    assert infer_schema(source_json)["properties"]["amount"]["type"] == "number"
    constrained = {"properties": {"id": {"type": "integer", "multipleOf": 2}}}
    unsupported = map_schema(infer_schema(source), constrained)
    assert unsupported["status"] == "unsupported"
    assert not validate_mapping(unsupported, constrained)["valid"]
    with pytest.raises(ValueError):
        generate_python(unsupported)
