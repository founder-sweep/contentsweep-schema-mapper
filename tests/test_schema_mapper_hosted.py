from __future__ import annotations

import hashlib
import hmac
import io
import json
import time
import zipfile
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mcp_products.schema_mapper.api import create_app
from mcp_products.schema_mapper.billing import Billing, BillingConfig
from mcp_products.schema_mapper.serialization import loads
from mcp_products.schema_mapper.service import Service

ORIGIN = "https://mapper.example.invalid"
SOURCE = "id,amount\n001,12345678901234567890.123456789\n002,2.05\n"
TARGET = '{"properties":{"id":{"type":"string"},"amount":{"type":"number","minimum":0}},"required":["id","amount"]}'


class Provider:
    def __init__(self):
        self.creates = 0
        self.sessions = {}
        self.charges = {}
        self.v1 = SimpleNamespace(
            accounts=SimpleNamespace(retrieve_current=lambda: {"id": "acct_fixture"}),
            balance=SimpleNamespace(retrieve=lambda: {"livemode": False}),
            prices=SimpleNamespace(retrieve=lambda *a: {
                "livemode": False, "active": True, "unit_amount": 900, "currency": "usd",
                "product": {"livemode": False, "metadata": {"product_namespace": "schema-mapper"}}}),
            checkout=SimpleNamespace(sessions=SimpleNamespace(create=self.create, retrieve=lambda i: self.sessions[i])),
            payment_intents=SimpleNamespace(retrieve=lambda i, *a: {"latest_charge": self.charges[i]}),
            charges=SimpleNamespace(retrieve=lambda i: next(c for c in self.charges.values() if c["id"] == i)),
        )

    def create(self, params, *, options):
        self.creates += 1
        value = {"id": "cs_test_" + params["client_reference_id"], "url": "https://checkout.stripe.com/c/pay/fixture",
                 "status": "open", "mode": "payment", "livemode": False,
                 "metadata": params["metadata"], "client_reference_id": params["client_reference_id"],
                 "amount_total": 900, "amount_subtotal": 900, "currency": "usd",
                 "payment_status": "unpaid", "payment_intent": None, "total_details": {}}
        self.sessions[value["id"]] = value
        self.last_params, self.options = params, options
        return value

    def pay(self, session):
        intent = "pi_" + session["client_reference_id"]
        session.update(payment_status="paid", payment_intent=intent, status="complete")
        self.charges[intent] = {"id": "ch_" + intent, "livemode": False, "paid": True,
                                "payment_intent": intent, "amount": 900, "currency": "usd", "amount_refunded": 0}


def billing():
    config = BillingConfig(mode="test", origin=ORIGIN, key="rk_test_fixture", price_id="price_fixture",
                           webhook_secret="fixture-signature-secret", expected_account="acct_fixture",
                           enabled=True, integration_identifier="schema_mapper_abcdefgh")
    provider = Provider()
    return Billing(config, provider), provider


def order(service, owner="fixture"):
    proposed = service.propose(owner, source=SOURCE, format="csv", target_json=TARGET,
                               vendor="vendor", domain="customers")
    approved = service.approve(owner, proposed["id"], expected_fingerprint=proposed["review"]["mapping_fingerprint"],
                               decisions={"id": "id", "amount": "amount"}, reviewer="fixture",
                               vendor="vendor", domain="customers")
    return approved


def signed(event, secret="fixture-signature-secret", timestamp=None):
    raw = json.dumps(event).encode()
    timestamp = int(time.time()) if timestamp is None else timestamp
    signature = hmac.new(secret.encode(), str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return raw, f"t={timestamp},v1={signature}"


def test_actual_order_validates_before_checkout_and_keeps_precision(tmp_path):
    service = Service(tmp_path)
    prepared = order(service)
    raw_order = service.store.order("fixture", prepared["id"])
    assert prepared["receipt"]["records"] == 2
    with zipfile.ZipFile(io.BytesIO((service.store.files / raw_order["artifact"]).read_bytes())) as archive:
        assert set(archive.namelist()) == {"mapping.approved.json", "transform.py", "README.txt", "validation-receipt.json"}
        assert b"12345678901234567890" not in archive.read("mapping.approved.json")
        assert loads(archive.read("validation-receipt.json").decode())["records"] == 2
    with pytest.raises(ValueError, match="settled"):
        service.store.download("fixture", prepared["id"])
    with pytest.raises(KeyError):
        service.store.order("other", prepared["id"])


def test_checkout_retries_signed_fulfillment_refund_and_reconciliation(tmp_path):
    service = Service(tmp_path)
    payment, provider = billing()
    prepared = order(service)
    for _ in range(2):
        payment.checkout(service, "fixture", prepared["id"])
    assert provider.creates == 1
    assert "payment_method_types" not in provider.last_params
    session = next(iter(provider.sessions.values()))
    event = {"id": "evt_fixture", "object": "event", "type": "checkout.session.completed",
             "livemode": False, "data": {"object": {"id": session["id"], "metadata": session["metadata"]}}}
    raw, signature = signed(event)
    assert payment.webhook(service, raw, signature)["status"] == "pending"
    with pytest.raises(ValueError):
        service.store.download("fixture", prepared["id"])
    provider.pay(session)
    for _ in range(2):
        assert payment.webhook(service, raw, signature)["status"] == "paid"
    assert service.store.download("fixture", prepared["id"]).startswith(b"PK")
    paid_expiry = service.store.order("fixture", prepared["id"])["expires"]
    provider.charges[session["payment_intent"]]["amount_refunded"] = 450
    assert payment.reconcile(service, "fixture", prepared["id"])["status"] == "refunded"
    assert service.store.order("fixture", prepared["id"])["expires"] == paid_expiry
    with pytest.raises(ValueError):
        service.store.download("fixture", prepared["id"])
    provider.charges[session["payment_intent"]]["amount_refunded"] = 0
    assert payment.reconcile(service, "fixture", prepared["id"])["status"] == "refunded"
    assert service.store.order("fixture", prepared["id"])["expires"] == paid_expiry
    with service.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM events WHERE id='evt_fixture'").fetchone()[0] == 1


@pytest.mark.parametrize("mutation", [
    {"livemode": True}, {"amount_total": 901}, {"amount_subtotal": 899}, {"currency": "eur"},
    {"client_reference_id": "other"}, {"metadata": {"product_namespace": "data-quality-gate"}},
    {"total_details": {"amount_tax": 1}},
])
def test_payment_snapshots_fail_closed(tmp_path, mutation):
    service = Service(tmp_path)
    payment, provider = billing()
    prepared = order(service)
    payment.checkout(service, "fixture", prepared["id"])
    session = next(iter(provider.sessions.values()))
    provider.pay(session)
    session.update(mutation)
    with pytest.raises(ValueError):
        payment.reconcile(service, "fixture", prepared["id"])
    assert service.store.order("fixture", prepared["id"])["status"] == "checkout"


def test_refund_before_settlement_or_charge_read_failure_never_unlocks(tmp_path):
    service = Service(tmp_path)
    payment, provider = billing()
    prepared = order(service)
    payment.checkout(service, "fixture", prepared["id"])
    session = next(iter(provider.sessions.values()))
    provider.pay(session)
    charge = provider.charges.pop(session["payment_intent"])
    with pytest.raises(KeyError):
        payment.reconcile(service, "fixture", prepared["id"])
    assert service.store.order("fixture", prepared["id"])["status"] == "checkout"
    charge["amount_refunded"] = 900
    provider.charges[session["payment_intent"]] = charge
    assert payment.reconcile(service, "fixture", prepared["id"])["status"] == "refunded"


def test_webhook_signature_and_age_enforced(tmp_path):
    payment, _ = billing()
    service = Service(tmp_path)
    event = {"id": "evt_bad", "object": "event", "type": "other", "livemode": False, "data": {"object": {}}}
    raw, signature = signed(event)
    assert payment.webhook(service, raw, signature) == {"status": "ignored"}
    import stripe
    for bad in ("t=0,v1=bad", signed(event, timestamp=int(time.time()) - 1000)[1]):
        with pytest.raises(stripe.SignatureVerificationError):
            payment.webhook(service, raw, bad)


def test_live_guard_runtime_key_role_and_expired_order(tmp_path):
    payment, _ = billing()
    with pytest.raises(ValueError, match="restricted"):
        Billing(replace(payment.config, key="sk_test_setup"))
    live = Billing(replace(payment.config, mode="live", key="rk_live_fixture"))
    with pytest.raises(ValueError, match="qualification"):
        live.sales_ready()
    service = Service(tmp_path)
    prepared = order(service)
    with service.store.connection() as db:
        db.execute("UPDATE orders SET expires=0")
    with pytest.raises(ValueError, match="expired"):
        payment.checkout(service, "fixture", prepared["id"])
    service.store.cleanup()
    assert service.store.order("fixture", prepared["id"])["mapping"] == "{}"
    assert not list(service.store.files.glob("*.zip"))


def test_http_user_journey_csrf_ownership_and_explicit_review(tmp_path):
    payment, provider = billing()
    app = create_app(tmp_path, billing=payment)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/").status_code == 200
        assert client.get("/app.js").status_code == 200
        assert client.post("/api/session").status_code == 403
        session = client.post("/api/session", headers={"Origin": ORIGIN}).json()
        headers = {"Origin": ORIGIN, "X-CSRF-Token": session["csrf"]}
        payload = {"source": SOURCE, "format": "csv", "target_json": TARGET, "vendor": "vendor", "domain": "customers"}
        assert client.post("/api/proposals", json=payload, headers={"Origin": ORIGIN}).status_code == 403
        proposed = client.post("/api/proposals", json=payload, headers=headers)
        assert proposed.status_code == 200, proposed.text
        proposed = proposed.json()
        review = {"expected_fingerprint": proposed["review"]["mapping_fingerprint"], "decisions": {"id": "id", "amount": "amount"},
                  "reviewer": "fixture", "vendor": "vendor", "domain": "customers", "approve": False}
        assert client.post("/api/proposals/" + proposed["id"] + "/approve", json=review, headers=headers).status_code == 422
        review["approve"] = True
        approved = client.post("/api/proposals/" + proposed["id"] + "/approve", json=review, headers=headers).json()
        identifier = approved["id"]
        assert client.get(f"/api/orders/{identifier}/download").status_code == 422
        assert client.post(f"/api/orders/{identifier}/checkout", headers=headers).status_code == 200
        provider.pay(next(iter(provider.sessions.values())))
        assert client.post(f"/api/orders/{identifier}/reconcile", headers=headers).json()["download_available"]
        assert client.get(f"/api/orders/{identifier}/download").content.startswith(b"PK")
        with TestClient(app, base_url=ORIGIN) as stranger:
            stranger.post("/api/session", headers={"Origin": ORIGIN})
            assert stranger.get(f"/api/orders/{identifier}").status_code == 404
        assert client.post("/api/proposals", content=b"x" * (4 * 1024 * 1024 + 1), headers=headers).status_code == 413


def test_owned_checkout_custom_domain_and_wrong_host(tmp_path):
    service = Service(tmp_path)
    payment, provider = billing()
    payment.config = replace(payment.config, checkout_origin="https://checkout.contentsweep.ai")
    original = provider.create

    def custom(params, **kwargs):
        session = original(params, **kwargs)
        session["url"] = "https://checkout.contentsweep.ai/c/pay/fixture"
        return session

    provider.v1.checkout.sessions.create = custom
    prepared = order(service)
    assert payment.checkout(service, "fixture", prepared["id"])["checkout_url"].startswith("https://checkout.contentsweep.ai/")
    second = order(service, "other")

    def wrong(params, **kwargs):
        session = original(params, **kwargs)
        session["url"] = "https://checkout.contentsweep.ai.evil.invalid/pay"
        return session

    provider.v1.checkout.sessions.create = wrong
    with pytest.raises(ValueError, match="Checkout URL"):
        payment.checkout(service, "other", second["id"])
