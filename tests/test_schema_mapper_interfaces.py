from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

from mcp_products.schema_mapper.cli import main
from mcp_products.schema_mapper.parsing import load_document
from mcp_products.schema_mapper.serialization import dumps, loads

ROOT = Path(__file__).resolve().parents[1]


def test_cli_exact_review_and_all_or_nothing_output(tmp_path, capsys):
    source = tmp_path / "input.csv"
    source.write_text("id\n1\n")
    target = tmp_path / "contract.json"
    target.write_text('{"properties":{"id":{"type":"integer","maximum":1}},"required":["id"]}')
    proposal, accepted, output = (tmp_path / n for n in ("proposal.json", "approved.json", "output.jsonl"))
    assert main(["map", str(source), "--target", str(target), "--out", str(proposal)]) == 0
    assert main(["review", str(proposal)]) == 0
    reviewed = loads(capsys.readouterr().out)
    scope = ["--user", "fixture", "--vendor", "fixture", "--domain", "contacts"]
    args = ["approve", str(proposal), "--reviewer", "fixture", *scope, "--out", str(accepted), "--expect"]
    assert main([*args, "sha256:wrong"]) == 2
    assert not accepted.exists()
    assert main([*args, reviewed["mapping_fingerprint"]]) == 0
    assert main(["transform", str(accepted), str(source), *scope, "--out", str(output)]) == 0
    assert loads(output.read_text()) == {"id": 1}
    assert main(["transform", str(accepted), str(source), *scope, "--out", str(source)]) == 2
    assert source.read_text() == "id\n1\n"
    assert main(["transform", str(accepted), str(source), *scope, "--out", str(output),
                 "--receipt", str(output)]) == 2
    source.write_text("id\n1\n2\n")
    assert main(["transform", str(accepted), str(source), *scope, "--out", str(output)]) == 2
    assert loads(output.read_text()) == {"id": 1}
    assert main(["validate", str(accepted), str(target), "--records", str(output)]) == 0
    output.write_text('{"id":false}\n')
    assert main(["validate", str(accepted), str(target), "--records", str(output)]) == 2
    assert main(["map", str(source), "--target", str(target), "--python", str(tmp_path / "old.py")]) == 2
    assert not (tmp_path / "old.py").exists()
    assert load_document(accepted)["approval"]["reviewer"] == "fixture"


def test_official_mcp_client_lifecycle():
    spec = importlib.util.spec_from_file_location("schema_mapper_client", ROOT / "integrations/schema-mapper/reference_client.py")
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    evidence = asyncio.run(client.workflow(ROOT))
    assert evidence["calls"] == 13
    assert evidence["unresolved_approval"] == "refused"
    assert evidence["precision"] == "exact"


def test_schema_only_cli_preserves_exact_contract(tmp_path):
    source, target, mapping = (tmp_path / n for n in ("source.json", "target.json", "map.json"))
    source.write_text('{"properties":{"amount":{"type":"number"}}}')
    target.write_text('{"properties":{"amount":{"type":"number","minimum":0.1234567890123456789012345}}}')
    assert main(["propose", str(source), "--target", str(target), "--out", str(mapping)]) == 0
    assert dumps(load_document(mapping)["target"]["properties"]["amount"]["minimum"]) == "1234567890123456789012345e-25"
