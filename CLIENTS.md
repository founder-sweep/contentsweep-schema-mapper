# Schema Mapper: coding-agent setup

Review flat vendor mappings, validate all records and reuse an approved Python transform.

## Runtime and credentials

Follow the existing runtime setup in the README. Use a dedicated Python 3.12+ environment. Set `CONTENTSWEEP_SCHEMA_MAPPER_MCP_COMMAND` to the absolute path of its installed `schema-map-mcp` executable. Launch from a trusted workspace; the working directory is not a filesystem sandbox. Do not install or execute dependencies from the project being assessed.

## Gemini CLI

Install the extension from this repository:

```sh
gemini extensions install https://github.com/founder-sweep/contentsweep-schema-mapper
```

Provide the requested setting in the native installation prompt. API keys are sensitive settings. The extension supplies the existing skill and default MCP connection; it does not approve tool calls or spending. For a project-managed connection, merge the Gemini example from `client-configs/gemini.json` into the intended profile.

## Kilo

Use the published marketplace entry when available, or merge `client-configs/kilo.json` into the intended project configuration and install the existing skill. The catalog entry includes a companion skill; companion installation requires a client version supporting that feature. Review prerequisites before installation.

## Cline

Ask Cline to follow this README and `llms-install.md`. Merge the entry in `client-configs/cline.json` into its MCP settings and provide the named setting privately. The hosted example selects `streamableHttp` explicitly. Keep tool approvals enabled.

## Cursor

Install the Cursor plugin from the marketplace or load this repository as a local plugin for development. Configure its declared variable in the plugin settings. The root `mcp.json` is the native plugin connection definition; the copy in `client-configs/cursor.json` is for a manually managed connection. For the manual copy, use Cursor's documented environment interpolation in place of the plugin variable. Preserve unrelated entries.

## Skill and first task

Read [review-schema-mapping](skills/contentsweep-schema-mapper/SKILL.md) and its relative references. Use the repository-owned synthetic example first and inspect the substantive result, not just execution success. Fixture approval never authorizes customer uploads, business meanings, purchases or downstream writes.

## Price and supported scope

Local CLI/SDK/MCP and hosted preview/validation are free. Optional hosted approved artifact download is $9 once; no subscription.

Flat schemas and documented primitive casts, explicit reviewed business meanings and fingerprint-bound approval. Python 3.12+ and matching 2.0.0b2 runtime; hosted source limit 1 MiB.

## Recovery and removal

Keep private keys out of source control and command history. Remove only this product's plugin/server/owned companion skill. Keep unrelated tools and settings. Client installation, marketplace acceptance, recommendations and paid adoption are separate outcomes.
