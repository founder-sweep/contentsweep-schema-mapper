"""Product-only Stripe Checkout and signed settlement; default-off live sales."""
from __future__ import annotations

import os
import secrets
import string
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from .serialization import loads

EVENTS = [
    "checkout.session.completed", "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed", "charge.refunded",
]


@dataclass(frozen=True)
class BillingConfig:
    mode: str
    origin: str
    key: str = ""
    price_id: str = ""
    webhook_secret: str = ""
    expected_account: str = ""
    enabled: bool = False
    qualified: bool = False
    tax_decision: str = ""
    payment_method_configuration: str = ""
    integration_identifier: str = ""
    checkout_origin: str = "https://checkout.stripe.com"

    @classmethod
    def environment(cls):
        return cls(
            mode=os.environ.get("SCHEMA_MAPPER_PAYMENT_MODE", "test"),
            origin=os.environ.get("SCHEMA_MAPPER_ORIGIN", "http://127.0.0.1:18083").rstrip("/"),
            key=os.environ.get("SCHEMA_MAPPER_STRIPE_RUNTIME_KEY", ""),
            price_id=os.environ.get("SCHEMA_MAPPER_STRIPE_PRICE_ID", ""),
            webhook_secret=os.environ.get("SCHEMA_MAPPER_STRIPE_WEBHOOK_SECRET", ""),
            expected_account=os.environ.get("SCHEMA_MAPPER_STRIPE_ACCOUNT_ID", ""),
            enabled=os.environ.get("SCHEMA_MAPPER_SALES_ENABLED") == "true",
            qualified=os.environ.get("SCHEMA_MAPPER_LIVE_QUALIFIED") == "true",
            tax_decision=os.environ.get("SCHEMA_MAPPER_TAX_DECISION", ""),
            payment_method_configuration=os.environ.get("SCHEMA_MAPPER_PAYMENT_METHOD_CONFIGURATION", ""),
            integration_identifier=os.environ.get("SCHEMA_MAPPER_INTEGRATION_IDENTIFIER", ""),
            checkout_origin=os.environ.get("SCHEMA_MAPPER_CHECKOUT_ORIGIN", "https://checkout.contentsweep.ai"),
        )


def plain(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value.to_dict_recursive() if hasattr(value, "to_dict_recursive") else dict(value)


class Billing:
    def __init__(self, config: BillingConfig, client=None):
        self.config = config
        self.client = client
        if config.mode not in {"test", "live"}:
            raise ValueError("explicit payment mode required")
        if config.key and not config.key.startswith("rk_" + config.mode + "_"):
            raise ValueError("only product-owned mode-matching restricted runtime keys are supported")
        self._verified = False

    def provider(self):
        if self.client is None:
            import stripe
            self.client = stripe.StripeClient(self.config.key, max_network_retries=1)
        return self.client

    def sales_ready(self):
        c = self.config
        if not c.enabled or not c.key or not c.price_id or not c.webhook_secret or not c.expected_account:
            raise ValueError("purchasing is not enabled")
        if not c.integration_identifier.startswith("schema_mapper_") or len(c.integration_identifier) > 100:
            raise ValueError("product integration identity required")
        if c.mode == "live" and (
            not c.qualified or not c.payment_method_configuration
            or not c.tax_decision.startswith("merchant-reviewed:")
            or not c.origin.startswith("https://")
        ):
            raise ValueError("live payment, tax and payment-method qualification required")
        if not self._verified:
            provider = self.provider()
            account = plain(provider.v1.accounts.retrieve_current())
            balance = plain(provider.v1.balance.retrieve())
            price = plain(provider.v1.prices.retrieve(c.price_id, {"expand": ["product"]}))
            product = price.get("product", {})
            if (account.get("id") != c.expected_account or balance.get("livemode") is not (c.mode == "live")
                or price.get("livemode") is not (c.mode == "live") or price.get("active") is not True
                or price.get("currency") != "usd" or price.get("unit_amount") != 900 or price.get("recurring")
                or product.get("metadata", {}).get("product_namespace") != "schema-mapper"
                or product.get("livemode") is not (c.mode == "live")):
                raise ValueError("owned account/catalog verification failed")
            if c.mode == "live":
                methods = plain(provider.v1.payment_method_configurations.retrieve(c.payment_method_configuration))
                enabled = [name for name, value in methods.items() if isinstance(value, dict)
                           and value.get("display_preference", {}).get("value") == "on"]
                if (methods.get("name") != "Schema Mapper card only" or methods.get("livemode") is not True
                    or methods.get("is_default") or enabled != ["card"]):
                    raise ValueError("separate owned card-only method configuration required")
            self._verified = True

    def checkout(self, service, owner, order_id):
        self.sales_ready()
        order = service.store.order(owner, order_id)
        if order["mode"] != self.config.mode:
            raise ValueError("order payment mode differs")
        service.artifact_available(order)
        if order["status"] == "paid":
            return {"paid": True}
        if order["status"] == "refunded":
            raise ValueError("refunded order cannot be repurchased automatically")
        if order["session_id"]:
            session = plain(self.provider().v1.checkout.sessions.retrieve(order["session_id"]))
            if session.get("status") == "expired":
                raise ValueError("checkout expired; request a new reviewed order from support")
            self.settle(service, session)
            return {"checkout_url": order["checkout_url"]}
        params = {
            "mode": "payment", "client_reference_id": order_id,
            "line_items": [{"price": self.config.price_id, "quantity": 1}],
            "metadata": loads(order["metadata"]),
            "payment_intent_data": {"metadata": loads(order["metadata"])},
            "integration_identifier": self.config.integration_identifier,
            "success_url": self.config.origin + "/?order=" + order_id,
            "cancel_url": self.config.origin + "/?order=" + order_id + "&cancelled=1",
            "expires_at": int(time.time()) + 1800,
        }
        if self.config.payment_method_configuration:
            params["payment_method_configuration"] = self.config.payment_method_configuration
        session = plain(self.provider().v1.checkout.sessions.create(
            params, options={"idempotency_key": f"schema-mapper:{self.config.mode}:{order_id}"}))
        url = session.get("url", "")
        origin = urlsplit(url)
        if origin.scheme != "https" or "https://" + origin.netloc not in {
            "https://checkout.stripe.com", self.config.checkout_origin
        }:
            raise ValueError("provider did not return a hosted Checkout URL")
        service.store.attach_checkout(owner, order_id, session["id"], url)
        service.marketing.event(owner, "checkout", order_id)
        return {"checkout_url": url}

    def settle(self, service, session, *, event_id=None, event_type="reconcile"):
        charge = None
        if session.get("payment_status") == "paid":
            # Read settled charge state as well: a refund webhook may precede
            # session settlement or have been temporarily undeliverable.
            intent = plain(self.provider().v1.payment_intents.retrieve(
                session["payment_intent"], {"expand": ["latest_charge.balance_transaction"]}))
            charge = intent.get("latest_charge")
            if not isinstance(charge, dict):
                raise ValueError("settled charge readback is unavailable")
        result = service.store.fulfill(session, mode=self.config.mode, charge=charge,
                                       event_id=event_id, event_type=event_type)
        service.marketing.settlement(session, charge)
        return result

    def reconcile(self, service, owner, order_id):
        order = service.store.order(owner, order_id)
        if order["session_id"]:
            session = plain(self.provider().v1.checkout.sessions.retrieve(order["session_id"]))
            self.settle(service, session)
        return service.summary(service.store.order(owner, order_id))

    def webhook(self, service, raw, signature):
        if not self.config.webhook_secret:
            raise ValueError("webhook is unconfigured")
        import stripe
        event = plain(stripe.Webhook.construct_event(raw, signature, self.config.webhook_secret, tolerance=300))
        if event.get("livemode") is not (self.config.mode == "live"):
            raise ValueError("webhook payment mode differs")
        if event["type"] not in EVENTS:
            return {"status": "ignored"}
        obj = event["data"]["object"]
        if event["type"] == "charge.refunded":
            with service.store.connection() as db:
                owned = db.execute("SELECT id FROM orders WHERE payment_intent=?", (obj.get("payment_intent"),)).fetchone()
            if not owned:
                return {"status": "ignored"}
            charge = plain(self.provider().v1.charges.retrieve(obj["id"]))
            result = service.store.refund(charge, mode=self.config.mode, event_id=event["id"])
            service.marketing.refunded(charge.get("payment_intent"))
            return {"status": result}
        if obj.get("metadata", {}).get("product_namespace") != "schema-mapper":
            return {"status": "ignored"}
        session = plain(self.provider().v1.checkout.sessions.retrieve(obj["id"]))
        if session.get("metadata", {}).get("product_namespace") != "schema-mapper":
            return {"status": "ignored"}
        return {"status": self.settle(service, session, event_id=event["id"], event_type=event["type"])}


def integration_identity():
    return "schema_mapper_" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(8))
