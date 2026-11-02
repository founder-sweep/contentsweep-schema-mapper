# Schema Mapper

<!-- agent-current-status:start -->
## Current status — November 1, 2026

Current free runtime download: 2.0.0b2, Python 3.12+. Local CLI/SDK/stdio MCP need no hosted key. Optional hosted preview and validation are free; one approved artifact download is $9 once. Generated wrappers require the matching runtime. No subscription, PyPI or MCP Registry listing is claimed.

[Agent setup](https://schema.contentsweep.ai/integrate) · [Product skill](https://schema.contentsweep.ai/SKILL.md) · [Task-to-tool reference](https://schema.contentsweep.ai/agent-assets/TOOL_REFERENCE.md). Skills support Codex and Claude Code and grant no upload, business-meaning approval, spending or destination-write permission. Historical test notes below retain their original scope; synthetic/internal qualification is not paid adoption.
<!-- agent-current-status:end -->

Turn a vendor CSV into a reviewed mapping and reusable Python transform.

**[Try the free hosted example](https://schema.contentsweep.ai/?campaign=github-launch)** · **[See the sample artifact](https://schema.contentsweep.ai/sample)** · **[Working guides](https://schema.contentsweep.ai/guides)**

The Apache-2.0 local CLI, Python SDK and stdio MCP server are free. The optional hosted workflow offers free preview and full-record validation, then a **one-time $9 approved artifact download**. Saved-artifact reuse is free. The hosted source limit is 1 MiB. No subscription and no model calls.

## What it does

Infer a bounded flat CSV/JSON/JSONL schema, propose correspondences to an explicit target contract, show evidence and ambiguity, require a named review, validate all actual records, and generate a versioned mapping plus Python runtime wrapper. Leading-zero identifiers remain text. Decimal values remain exact. Structural or format changes require review.

Names and types do not establish business meaning. Review every correspondence and cast with the vendor. Scores rank candidates; they are not probabilities.

## Install

Python **3.12+** is required. From this source checkout:

```bash
python -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m mcp_products.schema_mapper --help
```

Alternatively download the matching wheel from https://schema.contentsweep.ai/runtime/download and install the saved file with `python -m pip install PATH_TO_WHEEL`. No PyPI publication is claimed.

## Review and transform a synthetic example

```bash
schema-map map examples/schema-mapper/vendor.csv --target examples/schema-mapper/customer.schema.json --out proposal.json
schema-map review proposal.json
schema-map resolve proposal.json --decisions examples/schema-mapper/decisions.json --out resolved.json
schema-map review resolved.json
```

For this synthetic example, the reviewed choices are account_code → customer_id, givenName → first_name, enabled → active. Copy mapping_fingerprint from the resolved review and substitute it below:

```bash
schema-map approve resolved.json --expect REVIEWED_FINGERPRINT --reviewer "Your reviewer name" --user local-user --vendor demo-vendor --domain customer-imports --out approved.json
schema-map transform approved.json examples/schema-mapper/vendor.csv --user local-user --vendor demo-vendor --domain customer-imports --out output.jsonl --receipt receipt.json
schema-map generate approved.json --out transform.py
```

The generated Python imports the matching free runtime; it is not self-contained. The hosted ZIP contains mapping.approved.json, transform.py, validation-receipt.json and README.txt. It does not contain transformed source records. The free sample's README contains a complete reproducible source recipe.

## Local MCP quickstart

```bash
python -m pip install '.[mcp]'
python -m mcp_products.schema_mapper.mcp
```

Configure a trusted local MCP client with your absolute Python path and arguments `-m mcp_products.schema_mapper.mcp`. The process uses its launching user's local filesystem permissions. Its nine tools cover inference, proposals, review, resolution, approval, drift, validation and transformation. It is a stdio server; the hosted purchase site is an ordinary web application.

## Supported bounds and tests

See SUPPORT.md. Core files are at most 4 MiB, with 50,000 rows, 128 primitive fields, strict UTF-8 and a documented flat JSON Schema subset. Hosted sources have a smaller 1 MiB cap. Nested values, locale/date conversions and arbitrary scripts are unsupported.

```bash
python -m pip install '.[mcp,service]' pytest
python -m pytest -q tests
```

The examples are synthetic. Test coverage is not customer validation, measured time savings, a security certification or a claim of commercial superiority. The hosted workflow processes uploaded sources on its server; read its [privacy and retention statement](https://schema.contentsweep.ai/privacy).

Content Sweep LLC · https://schema.contentsweep.ai · founder@contentsweep.ai
