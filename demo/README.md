# Review a vendor mapping without losing identifiers or decimals

Stop unresolved decisions, approve the supplied synthetic correspondence, transform and reuse it, then stop on structural drift.

Owned synthetic example. Recorded results are clearly separated from live execution. Installing a skill does not install its runtime or authorize spending or uploads.

## Inspect the evidence

Read `manifest.json`, `expected.json`, and the fixtures before running. The walkthrough covers unresolved, reviewed, reuse, drift. Results come from actual product MCP calls, not simulated responses. The free public page and downloads require no sign-in.

## Reproduce with your agent

Read the existing `review-schema-mapping` skill and its companion setup/tool references. Then follow `AGENT_PROMPT.md`. Install the matching checksum-verified Schema Mapper 2.0.0b2 runtime and mcp>=2.3,<3 into the same dedicated Python 3.12+ environment used for this runner. Use its schema-map-mcp executable absolute path. The runner uses the product’s exact-decimal serialization helpers.

These commands are qualified on macOS and Linux. Follow [client setup](https://github.com/founder-sweep/contentsweep-schema-mapper/blob/main/CLIENTS.md) for runtime installation. Use that environment’s Python to run the demo.

For deterministic verification from this repository root:

```sh
python demo/run.py --mode local --command /ABSOLUTE/TO/ENV/bin/schema-map-mcp --output demo-results
```

This local demo requires no hosted evaluation allowance or API key. Review the supplied field decisions before approving them. It grants no permission for customer files, purchases, destination writes, source edits or cloud actions. Hosted keys are your own; local analysis needs no hosted key.

Reuse the same output directory and allowance after an uncertain response. `resume.private.json` preserves quote/key/job identities before dispatch. Keep it private. Never start a new run merely because polling timed out. Changed customer data needs its own review and authorization. A completed rejection, block or warning can be billable as documented for this product.

## Interpret the result

Inspect `result.json` for assertions, complete reports and actual tool discovery. Expected stop cases are part of a passing demo. A deterministic runner is not a native agent completion. Machine-generated IDs, timestamps and local paths may differ; business values, coverage and accounting must agree.

Flat schemas and documented primitive casts, explicit reviewed business meanings and fingerprint-bound approval. Python 3.12+ and matching 2.0.0b2 runtime; hosted source limit 1 MiB.

## Price and next step

Local CLI/SDK/MCP and hosted preview/validation are free. Optional hosted approved artifact download is $9 once; no subscription.

[Read the full walkthrough and setup](https://schema.contentsweep.ai/demos/vendor-mapping) · [Product and current terms](https://schema.contentsweep.ai/)

Keep local/free options separate from optional hosted purchases. This example performs no purchase. Reports are bounded evidence, not runtime certification or proof of customer outcomes.

<!-- demo-specific-walkthrough -->
## Reuse the reviewed transform

Inspect the actual [generated transform](https://schema.contentsweep.ai/demos/vendor-mapping/transform.py) and [transformed records](https://schema.contentsweep.ai/demos/vendor-mapping/transformed.json). The transform imports the matching Schema Mapper 2.0.0b2 runtime, including its exact-decimal serialization and approval enforcement. It is not standalone Python and does not bypass scope, approval or drift checks. Keep customer_id `000401` as a string and credit_limit `123456789012.123456789` exact.
