"""Public, reproducible guidance for the bounded hosted and local workflows."""
from __future__ import annotations

from html import escape

GUIDES = {
    "vendor-csv-to-app-contract": ("Map a vendor CSV to your application's contract", "Start with the field meanings, then validate every record before buying an artifact.", """
<h2>Give the import an explicit destination</h2><p>A vendor's <code>account_code</code> may correspond to your <code>customer_id</code>. The names suggest a candidate; a reviewer must establish whether they mean the same thing.</p>
<p>Use this repository-owned synthetic source. The identifier is text, even though it contains only digits.</p><pre>account_code,givenName,enabled
000401,Ada,true
000402,Grace,false</pre>
<p>The target is a supported, flat JSON Schema subset:</p><pre>{"type":"object","additionalProperties":false,"properties":{"customer_id":{"type":"string"},"first_name":{"type":"string"},"active":{"type":"boolean"}},"required":["customer_id","first_name","active"]}</pre>
<h2>Review, then validate</h2><ol><li>Upload a UTF-8 CSV, JSON or JSONL file and paste your target contract.</li><li>Review every correspondence and cast. For this synthetic fixture choose account_code → customer_id, givenName → first_name, enabled → active.</li><li>Approve those choices for the named vendor and application domain.</li><li>Validate all actual records. A failed record prevents checkout.</li></ol>
<p>Preview and validation are free. A $9 purchase downloads your approved mapping, a Python runtime wrapper and a full-record validation receipt. The ZIP contains schemas and decisions, not transformed source records. Python 3.12+ and the free matching runtime are required to run the wrapper.</p>
<h2>Run the same workflow locally</h2><p>The free Apache-2.0 CLI exposes infer, map, review, resolve, approve, transform, validate, generate and drift. Begin with <code>python -m mcp_products.schema_mapper --help</code>. Approval must bind the exact reviewed fingerprint and the intended user/vendor/domain scope.</p>
<p>The hosted source limit is 1 MiB. Nested values, date/locale conversions and unsupported JSON Schema keywords are refused. This example is synthetic; it is not customer validation or a claim of accuracy.</p>"""),
    "preserve-identifiers-and-decimals": ("Preserve leading-zero IDs and exact decimals", "Keep identifiers as text and avoid binary floating-point conversions in vendor imports.", """
<h2>An identifier is not an amount</h2><pre>customer_id,credit_limit
000401,123456789012.123456789
000402,2500.05</pre><p>The reviewed target keeps customer_id as a string and credit_limit as a number. <code>000401</code> stays a string; this beta refuses leading-zero identifier-to-number casts.</p>
<h2>Use exact values throughout the workflow</h2><p>JSON numbers are parsed as integers or Decimal values. Target constraints and mapping arguments travel as strict JSON strings so they do not pass through JavaScript floating-point values. A supported decimal-text-to-number cast preserves the exact mathematical value.</p><pre>{"customer_id":"000401","credit_limit":123456789012.123456789}</pre>
<p>For the Python SDK, parse exact numeric data with <code>mcp_products.schema_mapper.serialization.loads</code>, and serialize with its <code>dumps</code>. Converting a Decimal into a float elsewhere in your application can still lose precision.</p>
<h2>Check the actual contract</h2><p>Missing, null and empty string are distinct. Numeric text uses invariant decimal notation; currency symbols, grouped digits and decimal commas are unsupported. Every produced record is checked against the required fields, nullability, enums and inclusive numeric bounds you supplied.</p>
<p>The working example on the homepage uses these exact values. Preview and validation are free; the approved artifact costs $9 once. These properties demonstrate supported behavior, not universal safety for every financial workflow.</p>"""),
    "reuse-approved-mappings-and-drift": ("Reuse an approved mapping and detect structural drift", "Reuse the saved artifact for compatible files; require fresh review when the observed structure changes.", """
<h2>Save the approved artifact</h2><p>Keep mapping.approved.json, transform.py and validation-receipt.json together. The Python wrapper imports the free matching engine. Saved-artifact reuse is free and does not require another hosted purchase.</p>
<h2>Keep the original scope</h2><p>Approval is for the named user, vendor and application domain. Execute the local transform with that same scope. A content hash identifies the processed file; the source and target contract fingerprints identify the approved structure.</p>
<h2>What requires review?</h2><ul><li>An added or removed field, even an additive one.</li><li>A changed observed type, nullability, presence requirement or input format.</li><li>A revised target contract, mapping decision or engine version.</li></ul>
<p>Use the CLI's <code>drift</code> command for a report. The whole-file transform also checks structure and every actual record. On failure, the CLI leaves an existing output file untouched.</p>
<h2>What cannot be detected?</h2><p>A field can keep the same name and type while changing business meaning. A deterministic structural check cannot establish semantics. Confirm meaning with the vendor before reuse.</p>
<p>Hosted uploads are scheduled for deletion after one hour. Unpaid artifacts expire after 24 hours; purchased copies remain downloadable for up to seven days, subject to browser-session ownership. Save the ZIP immediately. Local copies persist under your control.</p>"""),
}


def page(title, description, path, content, origin):
    canonical = escape(origin.rstrip("/") + path, quote=True)
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + escape(title) + ' · Schema Mapper</title><meta name="description" content="' + escape(description, quote=True) + '">'
            '<link rel="canonical" href="' + canonical + '"><meta property="og:type" content="article"><meta property="og:title" content="' + escape(title, quote=True) + '">'
            '<meta property="og:description" content="' + escape(description, quote=True) + '"><meta property="og:url" content="' + canonical + '">'
            '<meta property="og:image" content="' + escape(origin, quote=True) + '/assets/schema-mapper-social.png"><meta name="twitter:card" content="summary_large_image">'
            '<link rel="stylesheet" href="/style.css"></head><body><header><a class="brand" href="/">Content Sweep <span>/ Schema Mapper</span></a><a href="/guides">Guides</a></header>'
            '<main class="article"><p class="eyebrow">REVIEWABLE VENDOR IMPORTS</p><h1>' + escape(title) + '</h1><p class="lede">' + escape(description) + '</p>' + content +
            '<p class="guide-cta"><a class="button" href="/?campaign=guide-' + escape(path.rsplit('/', 1)[-1])[:48] + '#demo">Try the free example →</a></p>'
            '<footer><a href="/">Hosted workflow · $9 per approved artifact</a><a href="/runtime/download">Free runtime</a><a href="/privacy">Privacy</a><a href="mailto:founder@contentsweep.ai">Merchant contact</a></footer></main></body></html>')


def guide_page(slug, origin):
    title, description, content = GUIDES[slug]
    return page(title, description, "/guides/" + slug, content, origin)


def guides_index(origin):
    content = "<div class='guide-grid'>" + "".join('<a class="guide-card" href="/guides/' + slug + '"><h2>' + escape(data[0]) + '</h2><p>' + escape(data[1]) + '</p></a>' for slug, data in GUIDES.items()) + "</div>"
    return page("Guides for reviewed vendor imports", "Working examples for flat CSV, JSON and JSONL import contracts.", "/guides", content, origin)


def sample_page(origin):
    return page("See the artifact before you buy", "A free synthetic example of the same four-file package delivered after a $9 purchase.", "/sample", """
<p><a class="button" href="/assets/schema-mapper-sample.zip">Download free synthetic sample ZIP ↓</a></p>
<div class="artifact-list"><span>mapping.approved.json</span><span>transform.py</span><span>validation-receipt.json</span><span>README.txt</span></div>
<h2>What each file does</h2><p>The mapping binds the reviewed decisions, source/target contracts, engine and user/vendor/domain scope. The Python file is a wrapper that imports the matching free runtime. The receipt records full-record validation and input/output hashes. The README explains installation and reuse.</p>
<p>The sample uses only repository-owned synthetic data. It is not a paid customer order, does not include anyone's source records, and does not unlock an unpaid customer artifact.</p>
<h2>Run it locally</h2><p>Install Python 3.12+ and the <a href="/runtime/download">free runtime wheel</a>. The sample README contains exact CLI commands and the synthetic source recipe. The core engine is Apache-2.0 and has no model or network dependency.</p>
<p>Your own approved artifact costs $9 once after free preview and validation. Reuse of a saved mapping is free. The hosted service processes your uploaded source; it does not run only in your browser.</p>""", origin)


def privacy_page(origin):
    return page("Privacy and first-party measurement", "What the optional hosted workflow retains and how campaign results are measured.", "/privacy", """
<p>The free local CLI uploads nothing. The optional hosted workflow uploads your selected source for server processing. No data is sent to an AI model and no cross-customer training occurs.</p>
<p>Raw uploads and preview copies expire after one hour; cleanup runs every minute while the service runs and on startup. Unpaid artifacts expire after 24 hours. First payment provides a fixed window of up to seven days for downloads in the owning browser session. Expired artifact copies are deleted and stored mapping JSON is cleared.</p>
<p>A first-party browser cookie establishes order ownership for seven days. We count campaign visits, example loads, previews, successful validations, checkout creation, verified purchases, downloads and refunds. Example and customer-file workflows are reported separately. Marketing records do not contain source rows, schema contents, vendor labels or reviewer names. Visit and pre-purchase event records expire after 30 days.</p>
<p>Payment identities, amounts, refund state and financial event records are retained for servicing orders and financial reconciliation. A private, keyed hash of the billing email deduplicates paying buyers; the marketing store does not retain the email itself. A purchase does not subscribe you to marketing. Stripe handles payment details under its own terms.</p>
<p>Support requests store your supplied contact email and message for 30 days. The merchant reviews these requests; they are not an automatic refund or response-time promise. Contact <a href="mailto:founder@contentsweep.ai">founder@contentsweep.ai</a> for help.</p>""", origin)
