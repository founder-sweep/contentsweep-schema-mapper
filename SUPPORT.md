# Supported beta contract: artifact 2.0, engine schema-mapper/2.0.0

## Files and parsing

| Area | Supported behavior |
|---|---|
| Sources | Local regular .csv, .json, .jsonl and .ndjson files; UTF-8 without BOM; no URL fetching |
| CSV | Comma delimiter, double-quote escaping, LF/CRLF, quoted multiline cells; unique ASCII identifier headers; exact row width; empty cell is empty string |
| JSON | One nonempty flat object or an array of nonempty flat objects; strict JSON; duplicate keys, non-finite numbers and nested field values fail |
| JSONL | Exactly one flat object per nonblank line; blank lines and malformed later lines fail |
| Byte cap | Read at most 4 MiB plus one sentinel byte before decoding/parsing; refuse larger input; JSON arrays decode one flat object at a time, with record/field count preflight |
| Other bounds | 50,000 records, 128 fields/schema or record, 128 ASCII characters/name, 64 KiB UTF-8 per string cell, JSON parser nesting at most 24 |
| Numbers | At most 256 significand digits and absolute decimal exponent at most 1000; numeric tokens at most 1500 characters; integer record/cast limit uses a conservative 850-bit bound (some 256-digit integers are refused) |
| Outputs | Transformed JSONL and owned saved documents at most 4 MiB; batch output is collected only within this bound |
| Sampling | Type/presence inference from first 10,000 records; all records still parsed and structurally checked; total/sample counts and completeness included |
| Paths | Flat $.identifier paths only; dots, spaces, Unicode names and nested paths are unsupported |
| Unsupported | TSV, YAML, XML, Parquet, compressed archives, nested arrays/objects, scripts and remote sources fail explicitly |

Whole-file bytes are bounded before JSON allocation; CSV/JSON/JSONL records are yielded rather than building a full input record list. This bounds input, not a constant process RSS guarantee. JSON source objects reject nesting and more than 128 fields before allocating dictionaries; saved artifact JSON uses a separate depth-limited parser. No raw values are saved in inferred schemas; metadata contains names, types, counts and fingerprints. Field names/annotations themselves can be sensitive. Local paths, approved artifacts and results persist until the operator deletes them. The local engine uploads nothing. The optional hosted adapter uploads the selected source for processing, applies a smaller 1 MiB source / 128 KiB target limit and keeps a preview for one hour. Its scheduled cleanup runs every minute; paid artifact copies last up to seven days, subject to browser-session ownership. See https://schema.contentsweep.ai/privacy.

## Inference and primitive transforms

Required means observed present in every sampled record, including null or empty string. Nullable means a JSON null was observed; missing, null and empty string remain different states. JSON null-only columns infer type null. Missing optional sources omit the target; explicit null remains null and must be allowed by the target.

CSV remains text at ingestion. Canonical invariant numeric text may infer integer/number; leading zeros such as 000401 remain strings. Mixed CSV numeric/text columns remain strings. JSON numbers parse as int/Decimal, never float. Heterogeneous nonnumeric JSON primitive types fail instead of guessing. Integer/number mixtures widen to number.

Allowlisted casts are identity/rename, string to exact integer/number, integer/number to integer/number with exact integrality checks, primitive to string using canonical JSON numeric/boolean notation, and string true/false to boolean. Boolean is never numeric. Null propagates only if the target permits it. Source string/type changes require explicit correspondence resolution before approval; automatic proposals prefer equal types or integer-to-number widening.

Numeric text must use a decimal point, optional minus sign and optional bounded exponent. No whitespace trimming, leading plus, grouped digits, decimal comma, currency, implicit date, unit conversion or leading-zero numeric coercion occurs. Numeric string identifiers with leading zeros cannot be cast to numbers by this beta. Invariant locale is the only supported locale. JSON numeric lexemes are not preserved byte-for-byte, but their exact values are preserved. Python callers must supply Decimal instead of float for record numbers and exact constraints. JSON integer notation such as 1.0 may infer integer when mathematically integral.

## Target contract subset

The schema must describe a flat object. Omitted root type means object for compatibility. The only supported dialect identifier is https://json-schema.org/draft/2020-12/schema; it may be omitted. This is a documented subset, not full JSON Schema compliance.

Root keywords: $schema, type, properties, required, additionalProperties (boolean), title, description. Source-only metadata x-source-fingerprint and x-inference is accepted. Every property needs type: string, integer, number, boolean or null, or a two-member primitive/null union. Property keywords: type, enum (1..128 compatible unique primitive values), minimum, maximum, title and description. Bounds apply only to numeric types, are inclusive, and must be exact numbers. Missing additionalProperties uses JSON Schema's true default. The transform emits only explicitly mapped target properties even when additional properties are allowed.

No refs, defaults, pattern/format, regex, length limits, multipleOf, exclusive bounds, nested properties, combinators, nullable extension or general unions. Unknown keywords fail closed, including those that appear to be annotations beyond this allowlist. Boolean is distinct from integer/number, including in enums. Every produced record is checked for types, required fields, enums, inclusive numeric bounds and additional-property policy.

For compatibility, the Python map_schema API returns a visibly unsupported, empty, non-executable proposal when a target contract is unsupported. Its unsupported_contracts explains why, and validation/approval/generation refuse it. The product CLI/MCP instead return an explicit error. Existing informational preview consumers can display the refusal without changing their own service behavior.

## Mapping and acceptance

Names use camel-case tokenization, normalization and token overlap plus a type feature. Scores are uncalibrated ranking scores. Every target gets evidence and up to five alternatives. A weak/tied/type-changing proposal abstains. Multiple confident targets claiming one source all abstain simultaneously, independent of input ordering. This is conservative collision resolution, not a global optimal assignment algorithm.

Every target must have one explicit source or an explicit skip for an optional target. Duplicate target/source paths, unknown paths, empty direct sources, unresolved decisions and unsupported transforms fail. One source may not be reused for multiple targets. The durable cast IR must agree with all field decisions.

The artifact stores separate artifact/engine/IR versions, exact source-content identity, source/target contract fingerprints, complete evidence, scoped review and a mapping fingerprint. The fingerprint binds source metadata, target, decisions, IR, scope and revision. Approval must bind that exact fingerprint and a named reviewer. The CLI/MCP additionally require the fingerprint displayed in review. Saving an approval is an explicit caller action, never inferred from a score.

Source and target contract fingerprints are their content-addressed version identities. The artifact version identifies the mapping envelope; it is separate from engine and IR versions.

Changing evidence, source metadata, contracts, mapping decisions or engine version invalidates the artifact/approval. Status cannot substitute for an approval. Approved artifacts are locally editable files, with integrity validation rather than tamper-proof storage. Trust and authentication belong to the embedding application. Engine upgrades and artifact 1.0 migration require a fresh proposal/review; there is no automatic old-approval adoption.

## Drift and compatibility

File execution compares inferred source structure and input format against the approved source, checks explicit scope, then validates every actual source/produced record. All structural changes require review, including additive fields and observed required/nullability changes. Content-only changes can reuse a mapping. Inference sampling is never evidence that unobserved rows conform; row checks enforce that boundary. Semantic changes with identical field names/types remain a human/vendor responsibility and cannot be detected by this deterministic schema check.

The independent module keeps infer/map/validate arguments and exported function names. Unapproved map --python is intentionally refused: use review, approve, generate. Legacy artifact 1.0 is not accepted for execution. The shared root CLI/MCP registrations remain unedited. Its old six-product smoke needs an owned review/approval step before generating Schema Mapper Python; see README.md.
