from __future__ import annotations

import json

from fastapi.testclient import TestClient

from mcp_products.schema_mapper.api import create_app
from mcp_products.schema_mapper.billing import Billing, BillingConfig
from mcp_products.schema_mapper.marketing import DEMO_SOURCE
from mcp_products.schema_mapper.service import Service


def test_campaign_first_touch_deduplicates_and_never_stores_source_values(tmp_path):
    service = Service(tmp_path)
    marketing = service.marketing
    marketing.visit("owner", "github-launch")
    marketing.visit("owner", "changed-campaign")
    marketing.preview("owner", "example", DEMO_SOURCE)
    marketing.preview("owner", "customer", "id\nsecret-customer-value\n")
    marketing.validated("owner", "customer", "order-fixture")
    marketing.validated("owner", "customer", "order-fixture")
    report = marketing.report()
    assert {event["campaign"] for event in report["events"]} == {"github-launch"}
    assert sum(e["count"] for e in report["events"] if e["event"] == "visit") == 1
    assert sum(e["count"] for e in report["events"] if e["event"] == "validated") == 1
    assert {e["kind"] for e in report["events"] if e["event"] == "preview"} == {"example", "customer"}
    assert "secret-customer-value" not in json.dumps(report)


def test_public_guides_and_client_telemetry_cannot_forge_payment(tmp_path):
    config = BillingConfig(mode="test", origin="https://mapper.example.invalid")
    app = create_app(tmp_path, billing=Billing(config))
    with TestClient(app, base_url=config.origin) as client:
        session = client.post("/api/session", headers={"Origin": config.origin}, json={"campaign": "email-batch-1"}).json()
        headers = {"Origin": config.origin, "X-CSRF-Token": session["csrf"]}
        assert client.post("/api/marketing/events", headers=headers, json={"event": "example_loaded"}).status_code == 200
        assert client.post("/api/marketing/events", headers=headers, json={"event": "purchase"}).status_code == 422
        assert client.post("/api/marketing/events", json={"event": "example_loaded"}).status_code == 403
        assert client.post("/api/proposals", headers=headers, json={"source": "private-value", "format": "csv",
            "target_json": "{}", "vendor": "private-vendor", "domain": "private-domain"}).status_code == 422
        events = app.state.service.marketing.report()["events"]
        assert {"preview_started", "preview_failed"} <= {event["event"] for event in events}
        assert "private-value" not in json.dumps(events)
        assert client.get("/api/marketing/report").status_code == 404
        for path in ("/guides", "/guides/vendor-csv-to-app-contract", "/guides/preserve-identifiers-and-decimals",
                     "/guides/reuse-approved-mappings-and-drift", "/sample", "/privacy"):
            response = client.get(path)
            assert response.status_code == 200 and 'rel="canonical"' in response.text
        assert client.get("/guides/../private").status_code == 404
        assert client.get("/assets/marketing-hmac.key").status_code == 404
        assert "/api/" in client.get("/robots.txt").text
        assert "/guides/vendor-csv-to-app-contract" in client.get("/sitemap.xml").text
        assert app.state.service.marketing.report()["payments"]["retained_buyers"] == 0


def test_live_buyer_identity_duplicate_events_refunds_and_internal_exclusion(tmp_path):
    # Use the real order/charge validation fixture; telemetry runs after its authority.
    from test_schema_mapper_hosted import billing, order

    service = Service(tmp_path)
    payment, provider = billing()
    prepared = order(service)
    payment.checkout(service, "fixture", prepared["id"])
    session = next(iter(provider.sessions.values()))
    provider.pay(session)
    session["customer_details"] = {"email": " Buyer@example.com "}
    charge = provider.charges[session["payment_intent"]]
    charge["balance_transaction"] = {"fee": 56, "net": 844}
    # Test provider settlement remains isolated; only explicit live snapshots count.
    payment.reconcile(service, "fixture", prepared["id"])
    assert service.marketing.report()["payments"]["paid_orders"] == 0
    with service.store.connection() as db:
        db.execute("UPDATE orders SET mode='live' WHERE id=?", (prepared["id"],))
    for _ in range(2):
        service.marketing.settlement(session, charge)
    report = service.marketing.report()
    assert report["payments"]["retained_buyers"] == 1
    assert report["payments"]["paid_orders"] == 1
    assert report["payments"]["revenue_after_refunds_and_known_fees_cents"] == 844
    assert "buyer@example.com" not in tmp_path.joinpath("schema-mapper.sqlite3").read_bytes().decode(errors="ignore").casefold()
    with service.store.connection() as db:
        db.execute("UPDATE orders SET status='refunded',refunded=900 WHERE id=?", (prepared["id"],))
    service.marketing.refunded(session["payment_intent"])
    assert service.marketing.report()["payments"]["retained_buyers"] == 0
    with service.store.connection() as db:
        metadata = {**session["metadata"], "purpose": "owner-qualification"}
        db.execute("DELETE FROM marketing_payments")
        db.execute("UPDATE orders SET metadata=?", (json.dumps(metadata),))
    service.marketing.settlement({**session, "metadata": metadata}, charge)
    assert service.marketing.report()["payments"]["paid_orders"] == 0
