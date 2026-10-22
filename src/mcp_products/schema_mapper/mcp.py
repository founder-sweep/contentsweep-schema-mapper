"""Product-owned local stdio MCP; no hosted paths or shared service dependencies."""
from __future__ import annotations

from functools import wraps

from .engine import (
    approve_mapping,
    check_drift,
    generate_python,
    infer_schema,
    map_schema,
    resolve_mapping,
    transform_file,
    validate_mapping,
)
from .review import review_mapping
from .serialization import dumps, loads


def create_server():
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer("schema-mapper-local")

    def tool(name):
        def register(function):
            @wraps(function)
            def checked(*args, **kwargs):
                try:
                    return function(*args, **kwargs)
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    raise ToolError(str(exc)) from None
            return server.tool(name=name)(checked)
        return register

    @tool("infer_schema")
    def infer(source: str) -> str:
        """Infer flat schema from a bounded local CSV/JSON/JSONL file."""
        return dumps(infer_schema(source))

    @tool("map_to_schema")
    def propose(source_schema_json: str, target_schema_json: str) -> str:
        """Propose correspondence; strict JSON strings preserve decimal precision."""
        result = map_schema(loads(source_schema_json), loads(target_schema_json))
        if result["status"] == "unsupported":
            raise ValueError("unsupported contract: " + dumps(result["unsupported_contracts"]))
        return dumps(result)

    @tool("review_mapping")
    def review(mapping_json: str) -> str:
        """Inspect exact proposal, evidence, alternatives and approval state."""
        return dumps(review_mapping(loads(mapping_json)))

    @tool("resolve_mapping")
    def resolve(mapping_json: str, decisions_json: str) -> str:
        """Explicit target:source decisions; null skips an optional target."""
        return dumps(resolve_mapping(loads(mapping_json), loads(decisions_json)))

    @tool("approve_mapping")
    def approve(mapping_json: str, expected_fingerprint: str, reviewer: str, user: str, vendor: str, domain: str) -> str:
        """Record user-authorized review of an exact mapping for a named scope."""
        mapping = loads(mapping_json)
        if mapping["mapping_fingerprint"] != expected_fingerprint:
            raise ValueError("reviewed fingerprint differs from mapping")
        return dumps(approve_mapping(mapping, reviewer=reviewer, scope={
            "user": user, "vendor": vendor, "domain": domain}))

    @tool("validate_schema_mapping")
    def validate(mapping_json: str, target_schema_json: str) -> str:
        """Check schema, completeness, exact fingerprints, decisions and approval."""
        return dumps(validate_mapping(loads(mapping_json), loads(target_schema_json)))

    @tool("check_schema_drift")
    def drift(mapping_json: str, source: str, user: str, vendor: str, domain: str) -> str:
        """Compare a local input schema against approved scope and schema version."""
        return dumps(check_drift(loads(mapping_json), infer_schema(source), scope={
            "user": user, "vendor": vendor, "domain": domain}))

    @tool("transform_file")
    def transform(mapping_json: str, source: str, user: str, vendor: str, domain: str) -> str:
        """Transform an approved local snapshot; return exact JSON and receipt."""
        return dumps(transform_file(source, loads(mapping_json), scope={
            "user": user, "vendor": vendor, "domain": domain}))

    @tool("generate_transformation")
    def generate(mapping_json: str) -> str:
        """Emit approved runtime-dependent Python; never executes supplied code."""
        return generate_python(loads(mapping_json))

    return server


def main():
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
