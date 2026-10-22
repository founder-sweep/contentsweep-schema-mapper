from __future__ import annotations

import copy

import pytest

from mcp_products.schema_mapper.commerce import (
    classify_verified_sandbox_event,
    prepare_sandbox_checkout,
)
from mcp_products.schema_mapper.engine import approve_mapping, map_schema


def plan():
    contract = {"properties": {"id": {"type": "string"}}}
    approved = approve_mapping(map_schema(contract, contract), reviewer="synthetic reviewer",
                               scope={"user": "test", "vendor": "test", "domain": "contacts"})
    price = {"id": "price_fixture", "livemode": False, "active": True, "currency": "usd",
             "unit_amount": 900, "recurring": None,
             "product": {"livemode": False, "metadata": {"product_namespace": "schema-mapper"}}}
    return approved, price


def prepare(approved, price):
    return prepare_sandbox_checkout(approved, order_id="test-order", price_snapshot=price,
                                    return_origin="https://example.invalid",
                                    integration_identifier="schema_mapper_abcdefgh")


def test_sandbox_only_hypothesis_never_uses_other_product_or_live_price():
    approved, price = plan()
    prepared = prepare(approved, price)
    assert prepared["offer"]["unit_amount"] == 900
    assert not prepared["live_activation"] and not prepared["provider_verified"]
    assert "payment_method_types" not in prepared["checkout_params"]
    assert "user" not in prepared["checkout_params"]["metadata"]
    for mutate in (
        lambda p: p.update(livemode=True),
        lambda p: p.update(unit_amount=2000),
        lambda p: p.update(currency="eur"),
        lambda p: p.update(recurring={"interval": "month"}),
        lambda p: p["product"]["metadata"].update(product_namespace="data-quality-gate"),
    ):
        changed = copy.deepcopy(price)
        mutate(changed)
        with pytest.raises(ValueError):
            prepare(approved, changed)


def test_unpaid_wrong_mapping_and_live_events_cannot_settle():
    approved, price = plan()
    prepared = prepare(approved, price)
    session = {"livemode": False, "mode": "payment", "client_reference_id": "test-order",
               "metadata": prepared["checkout_params"]["metadata"], "amount_total": 900,
               "currency": "usd", "payment_status": "unpaid"}
    event = {"type": "checkout.session.completed", "livemode": False, "data": {"object": session}}
    assert classify_verified_sandbox_event(event, prepared) == "pending"
    session["payment_status"] = "paid"
    assert classify_verified_sandbox_event(event, prepared) == "settled"
    assert classify_verified_sandbox_event(event, prepared) == "settled"  # pure classifier grants nothing
    session["metadata"] = {**session["metadata"], "mapping_fingerprint": "wrong"}
    with pytest.raises(ValueError, match="immutable"):
        classify_verified_sandbox_event(event, prepared)
    event["livemode"] = True
    with pytest.raises(ValueError, match="disabled"):
        classify_verified_sandbox_event(event, prepared)
