"""First-party campaign counts, separate from mapping approval and payment authority."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time

DEMO_SOURCE = "customer_id,first_name,credit_limit\n000401,Ada,123456789012.123456789\n000402,Grace,2500.05\n"


class Marketing:
    def __init__(self, store):
        self.store = store
        path = store.root / "marketing-hmac.key"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(secrets.token_bytes(32))
        self.key = path.read_bytes()
        if len(self.key) != 32:
            raise ValueError("marketing identity key is unavailable")
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS marketing_visitors(
                    owner TEXT PRIMARY KEY, campaign TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS marketing_events(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, campaign TEXT NOT NULL,
                    event TEXT NOT NULL, object_id TEXT NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS marketing_orders(
                    order_id TEXT PRIMARY KEY, campaign TEXT NOT NULL, kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS marketing_payments(
                    order_id TEXT PRIMARY KEY, buyer_hash TEXT, fee_cents INTEGER,
                    charge_net_cents INTEGER, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS marketing_refund_fees(
                    order_id TEXT PRIMARY KEY, fee_cents INTEGER NOT NULL);
            """)

    def visit(self, owner, campaign="direct"):
        if not isinstance(campaign, str) or not re.fullmatch(r"[a-z0-9_-]{1,64}", campaign):
            campaign = "direct"
        with self.store.connection() as db:
            db.execute("INSERT OR IGNORE INTO marketing_visitors VALUES(?,?,?)", (owner, campaign, time.time()))
        self.event(owner, "visit", str(int(time.time() // 86400)))

    def event(self, owner, event, object_id="", kind="unknown"):
        if event not in {"visit", "example_loaded", "preview_started", "preview_failed", "preview", "validated", "checkout", "purchase", "download", "refund"}:
            raise ValueError("unknown campaign event")
        with self.store.connection() as db:
            visitor = db.execute("SELECT campaign FROM marketing_visitors WHERE owner=?", (owner,)).fetchone()
            campaign = visitor[0] if visitor else "unattributed"
            if event in {"validated", "checkout", "purchase", "download", "refund"}:
                order = db.execute("SELECT campaign,kind FROM marketing_orders WHERE order_id=?", (object_id,)).fetchone()
                if order:
                    campaign, kind = order
            key = hashlib.sha256((owner + ":" + event + ":" + object_id).encode()).hexdigest()
            db.execute("INSERT OR IGNORE INTO marketing_events VALUES(?,?,?,?,?,?,?)",
                       (key, owner, campaign, event, object_id, kind, time.time()))

    def preview(self, owner, identifier, source):
        kind = "example" if source == DEMO_SOURCE else "customer"
        self.event(owner, "preview", identifier, kind)

    def validated(self, owner, preview_id, order_id):
        with self.store.connection() as db:
            preview = db.execute("SELECT campaign,kind FROM marketing_events WHERE owner=? AND event='preview' AND object_id=?",
                                 (owner, preview_id)).fetchone()
            db.execute("INSERT OR IGNORE INTO marketing_orders VALUES(?,?,?)",
                       (order_id, preview[0] if preview else "unattributed", preview[1] if preview else "unknown"))
        self.event(owner, "validated", order_id)

    def settlement(self, session, charge):
        if not charge:
            return
        identifier = session.get("client_reference_id")
        with self.store.connection() as db:
            order = db.execute("SELECT * FROM orders WHERE id=?", (identifier,)).fetchone()
            if not order or order["mode"] != "live":
                return
            from .serialization import loads
            if loads(order["metadata"]).get("purpose") != "customer-artifact":
                return
            email = (session.get("customer_details") or {}).get("email")
            buyer = hmac.new(self.key, email.strip().casefold().encode(), hashlib.sha256).hexdigest() if isinstance(email, str) and 3 <= len(email) <= 254 else None
            transaction = charge.get("balance_transaction")
            fee = transaction.get("fee") if isinstance(transaction, dict) else None
            net = transaction.get("net") if isinstance(transaction, dict) else None
            db.execute("""INSERT INTO marketing_payments VALUES(?,?,?,?,?) ON CONFLICT(order_id)
                DO UPDATE SET buyer_hash=COALESCE(excluded.buyer_hash,marketing_payments.buyer_hash),
                fee_cents=COALESCE(excluded.fee_cents,marketing_payments.fee_cents),
                charge_net_cents=COALESCE(excluded.charge_net_cents,marketing_payments.charge_net_cents)""",
                       (identifier, buyer, fee, net, time.time()))
            owner, refunded = order["owner"], order["refunded"]
        self.event(owner, "purchase", identifier)
        if refunded:
            self.event(owner, "refund", identifier)

    def refunded(self, intent):
        with self.store.connection() as db:
            row = db.execute("SELECT owner,id FROM orders WHERE payment_intent=? AND refunded>0", (intent,)).fetchone()
            eligible = row and db.execute("SELECT 1 FROM marketing_payments WHERE order_id=?", (row["id"],)).fetchone()
        if eligible:
            self.event(row["owner"], "refund", row["id"])

    def report(self, days=30):
        if not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("report window needs 1..365 days")
        since = time.time() - days * 86400
        with self.store.connection() as db:
            events = [dict(r) for r in db.execute("""SELECT campaign,event,kind,COUNT(*) AS count
                FROM marketing_events WHERE created>=? AND campaign NOT LIKE 'internal-%'
                GROUP BY campaign,event,kind ORDER BY campaign,event,kind""", (since,))]
            money = dict(db.execute("""SELECT COUNT(*) AS paid_orders,
                COUNT(DISTINCT CASE WHEN o.refunded=0 THEN p.buyer_hash END) AS retained_buyers,
                COALESCE(SUM(o.amount),0) AS gross_cents,COALESCE(SUM(o.refunded),0) AS refunded_cents,
                COALESCE(SUM(p.fee_cents),0) AS known_processing_fees_cents,
                COALESCE(SUM(f.fee_cents),0) AS known_refund_fees_cents,
                COALESCE(SUM(CASE WHEN o.refunded>0 AND f.order_id IS NULL THEN 1 ELSE 0 END),0) AS orders_with_unknown_refund_fees,
                COALESCE(SUM(CASE WHEN p.fee_cents IS NULL THEN 1 ELSE 0 END),0) AS orders_with_unknown_fees,
                COALESCE(SUM(CASE WHEN p.buyer_hash IS NULL THEN 1 ELSE 0 END),0) AS orders_with_unknown_buyer
                FROM marketing_payments p JOIN orders o ON o.id=p.order_id
                LEFT JOIN marketing_refund_fees f ON f.order_id=o.id WHERE p.created>=?""", (since,)).fetchone())
            by_campaign = [dict(r) for r in db.execute("""SELECT COALESCE(a.campaign,'unattributed') AS campaign,
                COUNT(*) AS paid_orders,COALESCE(SUM(o.amount-o.refunded),0) AS revenue_after_refunds_cents
                FROM marketing_payments p JOIN orders o ON o.id=p.order_id LEFT JOIN marketing_orders a ON a.order_id=o.id
                WHERE p.created>=? GROUP BY 1""", (since,))]
        money["revenue_after_refunds_and_known_fees_cents"] = money["gross_cents"] - money["refunded_cents"] - money["known_processing_fees_cents"] - money["known_refund_fees_cents"]
        visits = sum(e["count"] for e in events if e["event"] == "visit")
        previews = sum(e["count"] for e in events if e["event"] == "preview_started")
        validated = sum(e["count"] for e in events if e["event"] == "validated" and e["kind"] == "customer")
        decisions = []
        if visits >= 100 and previews < 10:
            decisions.append("Improve homepage/example before expanding distribution.")
        if validated >= 20 and not money["paid_orders"]:
            decisions.append("Investigate artifact value and purchase friction before features or pricing changes.")
        return {"window_days": days, "target_retained_buyers": 10, "events": events, "payments": money,
                "revenue_by_campaign": by_campaign, "decisions": decisions,
                "fees_complete": not money["orders_with_unknown_fees"] and not money["orders_with_unknown_refund_fees"],
                "scope": "observed first-party events; internal qualification excluded; no raw files or email stored"}

    def reconcile_financials(self, service, billing):
        """Operator-only reads of owned provider objects; never creates a payment/refund."""
        with self.store.connection() as db:
            rows = db.execute("SELECT id,owner,payment_intent,refunded FROM orders WHERE mode='live' AND payment_intent IS NOT NULL").fetchall()
        from .serialization import loads
        for row in rows:
            order = self.store.order(row["owner"], row["id"])
            if loads(order["metadata"]).get("purpose") != "customer-artifact":
                continue
            billing.reconcile(service, row["owner"], row["id"])
            order = self.store.order(row["owner"], row["id"])
            if order["refunded"]:
                refunds = billing.provider().v1.refunds.list({"payment_intent": row["payment_intent"], "limit": 100,
                    "expand": ["data.balance_transaction"]})
                from .billing import plain
                refunds = plain(refunds)
                transactions = [r.get("balance_transaction") for r in refunds["data"] if r.get("status") == "succeeded"]
                if not refunds.get("has_more") and transactions and all(isinstance(t, dict) and isinstance(t.get("fee"), int) for t in transactions):
                    with self.store.connection() as db:
                        db.execute("INSERT OR REPLACE INTO marketing_refund_fees VALUES(?,?)",
                                   (row["id"], sum(t["fee"] for t in transactions)))

    def cleanup(self):
        with self.store.connection() as db:
            db.execute("DELETE FROM marketing_visitors WHERE created<?", (time.time() - 30 * 86400,))
            db.execute("DELETE FROM marketing_events WHERE created<? AND event NOT IN ('purchase','refund','download')",
                       (time.time() - 30 * 86400,))
