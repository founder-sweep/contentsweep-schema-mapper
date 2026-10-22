#!/usr/bin/env python3
"""Real local MCP client; example reviewer decisions are fixture-only."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_products.schema_mapper.serialization import dumps, loads


async def workflow(root: Path) -> dict:
    fixture = root / "examples/schema-mapper"
    params = StdioServerParameters(command=sys.executable,
                                  args=["-m", "mcp_products.schema_mapper.mcp"], cwd=str(root))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        calls = 0

        async def call(name, arguments, *, failure=False):
            nonlocal calls
            result = await session.call_tool(name, arguments)
            calls += 1
            assert bool(result.is_error) == failure, (name, result)
            if failure:
                return None
            return loads(next(block.text for block in result.content if block.type == "text"))

        source = await call("infer_schema", {"source": str(fixture / "vendor.csv")})
        target_json = (fixture / "customer.schema.json").read_text()
        proposal = await call("map_to_schema", {
            "source_schema_json": dumps(source), "target_schema_json": target_json})
        reviewed = await call("review_mapping", {"mapping_json": dumps(proposal)})
        assert reviewed["status"] == "review_required"
        scope = {"user": "fixture-reviewer", "vendor": "synthetic-vendor", "domain": "customer-imports"}
        await call("approve_mapping", {
            "mapping_json": dumps(proposal), "expected_fingerprint": proposal["mapping_fingerprint"],
            "reviewer": "synthetic fixture reviewer", **scope}, failure=True)
        resolved = await call("resolve_mapping", {
            "mapping_json": dumps(proposal), "decisions_json": (fixture / "decisions.json").read_text()})
        await call("review_mapping", {"mapping_json": dumps(resolved)})
        accepted = await call("approve_mapping", {
            "mapping_json": dumps(resolved), "expected_fingerprint": resolved["mapping_fingerprint"],
            "reviewer": "synthetic fixture reviewer", **scope})
        validation = await call("validate_schema_mapping", {
            "mapping_json": dumps(accepted), "target_schema_json": target_json})
        assert validation["accepted"]
        output = await call("transform_file", {
            "mapping_json": dumps(accepted), "source": str(fixture / "vendor.csv"), **scope})
        expected = [loads(line) for line in (fixture / "expected.jsonl").read_text().splitlines()]
        assert output["records"] == expected
        await call("transform_file", {
            "mapping_json": dumps(accepted), "source": str(fixture / "vendor.csv"),
            **scope, "user": "wrong-scope"}, failure=True)
        ambiguous_source = await call("infer_schema", {"source": str(fixture / "ambiguous.csv")})
        ambiguous = await call("map_to_schema", {
            "source_schema_json": dumps(ambiguous_source),
            "target_schema_json": (fixture / "ambiguous.schema.json").read_text()})
        assert ambiguous["fields"][0]["decision"]["kind"] == "ambiguous"
        await call("map_to_schema", {
            "source_schema_json": dumps(ambiguous_source),
            "target_schema_json": (fixture / "unsupported.schema.json").read_text()}, failure=True)
        await session.send_ping()
        return {"tools_listed": len(listed.tools), "calls": calls, "records": len(expected),
                "unresolved_approval": "refused", "wrong_scope": "refused",
                "ambiguous": "abstained", "unsupported": "refused", "precision": "exact"}


if __name__ == "__main__":
    print(dumps(asyncio.run(workflow(Path(__file__).resolve().parents[2]))))
