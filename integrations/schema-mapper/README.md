# Embedded import onboarding

Use the Python API directly for in-process integrations, the product module CLI for local subprocesses, or the product-owned stdio MCP for agent clients. All use the same engine and artifact versions.

The reference_client.py uses the installed official Python MCP 2.3.0 client. Run .venv/bin/python integrations/schema-mapper/reference_client.py from the canonical WSL repository root. Its reviewer choices apply only to repository-owned synthetic fixtures. Real integrations must present review evidence and obtain an authorized user's semantic decisions; calling the approval tool is not a substitute for that authorization.

Configure MCP with your local Python executable, arguments -m mcp_products.schema_mapper.mcp and a deliberate working directory. This is a trusted local process, with no HTTP listener. Keep source files and approvals in your own user/vendor/domain directory.

For the SDK, import from mcp_products.schema_mapper. Decimal fields should be parsed with the package's strict serialization.loads. Save artifacts with serialization.dumps; ordinary JSON encoders do not support Decimal, and converting to float loses precision. transform_file returns records and a receipt only after complete successful evaluation. Use the CLI for bounded atomic output saving, or implement your application's own transactional persistence.

The generated transform_record wrapper validates one approved record against the carried source/target contract. Whole-file drift, scope selection, retention, UI identity and filesystem permissions remain the embedding application's responsibility when bypassing transform_file.
