"""Pure test-mode request preparation. No network, credentials or entitlement IO.

This is an integration scaffold, not an activated payment service. Production
requires owned storage, authenticated order ownership and verified webhooks.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from .engine import validate_mapping
from .serialization import fingerprint

NAMESPACE = "schema-mapper"
OFFER = {"id": "reviewed-artifact-v1", "unit_amount": 900, "currency": "usd", "quantity": 1}


def prepare_sandbox_checkout(mapping: dict[str, Any], *, order_id: str,
                             price_snapshot: dict[str, Any], return_origin: str,
                             integration_identifier: str) -> dict[str, Any]:
    """Prepare params from a separately read, owned sandbox price snapshot.

    No SDK call occurs here. A future adapter must verify sandbox/account identity
    before creating the request with its restricted test runtime StripeClient.
    """
    if not validate_mapping(mapping, mapping["target"])["accepted"]:
        raise ValueError("payment preparation requires approved exact mapping")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", order_id):
        raise ValueError("invalid independent order identity")
    if not re.fullmatch(r"schema_mapper_[a-z]{8}", integration_identifier):
        raise ValueError("integration identifier needs schema_mapper_ and 8 random letters")
    origin = urlsplit(return_origin)
    if origin.scheme != "https" or not origin.netloc or origin.username or origin.password or origin.path not in {"", "/"} or origin.query or origin.fragment:
        raise ValueError("a reviewed HTTPS return origin is required")
    product = price_snapshot.get("product")
    if not isinstance(product, dict) or product.get("metadata", {}).get("product_namespace") != NAMESPACE:
        raise ValueError("price must expand a separately owned schema-mapper product")
    if (price_snapshot.get("livemode") is not False or product.get("livemode") is not False
        or price_snapshot.get("currency") != OFFER["currency"]
        or price_snapshot.get("unit_amount") != OFFER["unit_amount"]
        or price_snapshot.get("recurring") is not None or price_snapshot.get("active") is not True
        or not isinstance(price_snapshot.get("id"), str) or not price_snapshot["id"].startswith("price_")):
        raise ValueError("only the exact owned test-mode one-time hypothesis is supported")
    metadata = {"product_namespace": NAMESPACE, "order_id": order_id,
                "mapping_fingerprint": mapping["mapping_fingerprint"],
                "scope_fingerprint": fingerprint(mapping["scope"]), "qualification": "internal-test"}
    origin_text = return_origin.rstrip("/")
    return {
        "mode": "test", "offer": OFFER.copy(), "live_activation": False,
        "pricing_status": "unapproved hypothesis", "provider_verified": False,
        "order_identity": fingerprint(metadata),
        "idempotency_key": f"schema-mapper:test:{order_id}",
        "checkout_params": {
            "mode": "payment", "client_reference_id": order_id,
            "line_items": [{"price": price_snapshot["id"], "quantity": 1}],
            "integration_identifier": integration_identifier,
            "success_url": origin_text + "/schema-mapper/payment-return?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": origin_text + "/schema-mapper/payment-cancel",
            "metadata": metadata,
        },
    }


def classify_verified_sandbox_event(event: dict[str, Any], plan: dict[str, Any]) -> str:
    """Classify an ALREADY signature-verified event; never grants paid access.

    Not a webhook endpoint/verifier. Caller must use Stripe's raw-body signature
    verification before invoking this predicate. No financial state is stored.
    """
    if plan.get("mode") != "test" or event.get("livemode") is not False:
        raise ValueError("live payment events are disabled")
    if event.get("type") not in {
        "checkout.session.completed", "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
    }:
        return "ignored"
    session = event.get("data", {}).get("object", {})
    params = plan["checkout_params"]
    if (session.get("livemode") is not False or session.get("mode") != "payment"
        or session.get("client_reference_id") != params["client_reference_id"]
        or session.get("metadata") != params["metadata"]
        or session.get("amount_total") != OFFER["unit_amount"]
        or session.get("currency") != OFFER["currency"]):
        raise ValueError("event differs from immutable test order")
    if event["type"] == "checkout.session.async_payment_failed":
        return "failed"
    return "settled" if session.get("payment_status") == "paid" else "pending"
