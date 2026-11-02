---
name: contentsweep-schema-mapper
description: Review flat vendor CSV/JSON/JSONL mappings against an explicit target schema, validate complete records and reuse deterministic transforms. Optional hosted $9 artifact purchase; not nested mapping, dates or unattended semantic approval.
---

# Schema Mapper: reviewed vendor import artifact

Use for an explicit flat target contract, candidate correspondence review, primitive casts and deterministic reuse. The local Apache-2.0 engine and saved-artifact reuse are free. The optional hosted service packages the user's reviewed decisions for $9 once per approved artifact; no subscription or prepaid wallet. It is not a human mapping service.

## Choose the applicable path

For local transformation, use Python 3.12+ and the free runtime from https://schema.contentsweep.ai/runtime/download. Install into a dedicated environment. No PyPI listing is claimed. Local CLI limits are 4 MiB/50,000 records/128 primitive fields; hosted source limit is 1 MiB. Nested values, locale/date conversions and arbitrary scripts are unsupported.

From the public repository:

```bash
python3 -m venv .mapper-venv
.mapper-venv/bin/python -m pip install .
.mapper-venv/bin/schema-map map examples/schema-mapper/vendor.csv --target examples/schema-mapper/customer.schema.json --out proposal.json
.mapper-venv/bin/schema-map review proposal.json
```

Inspect each target, alternatives, casts, nullability and unsupported constraints. Resolve choices using schema-map resolve, review again, and obtain authority for the exact field meanings. Then approve the displayed fingerprint with reviewer and user/vendor/domain context. An inference or successful check cannot grant semantic approval. Transform uses the same context; structure changes require new review. Generated Python imports the matching runtime and needs its approved JSON. Local MCP is stdio: python -m mcp_products.schema_mapper.mcp with the MCP extra installed. There is no paid hosted MCP endpoint.

## Hosted artifact purchase

Open https://schema.contentsweep.ai/?campaign=agent-skill in the operator's browser. Try the free synthetic example or upload an authorized flat file and explicit target JSON Schema. Review each correspondence and validate every actual record. Failed validation cannot enter Checkout. Show the operator the approved artifact and $9 price before purchasing.

The operator completes the existing Checkout. Maintain the same private browser session on return; its cookie and CSRF protect order ownership. The API is documented at https://schema.contentsweep.ai/openapi.json but is not an API-key service. Do not bypass session/Origin/CSRF protections, open the order in an unrelated session, or buy automatically.

After confirmed settlement, download the ZIP immediately in the owning session. It contains mapping.approved.json, transform.py, validation-receipt.json and README.txt, not transformed source records. Downloads last up to seven days; a refund revokes future downloads. Saved artifacts and runtime reuse are free. Verify receipt fingerprints against the approved mapping and validate local transformed records; do not execute untrusted user-supplied Python as validation.

A second vendor or changed structural mapping requires a new review. A new hosted artifact has its own $9 purchase; never describe this as funding a reusable wallet. On a lost session, use the product support flow; do not attempt another identity to recover private artifacts.

Guides: https://schema.contentsweep.ai/guides. Free sample: https://schema.contentsweep.ai/sample. Privacy: https://schema.contentsweep.ai/privacy.
