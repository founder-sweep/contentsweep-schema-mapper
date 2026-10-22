"""Independent durable sessions, artifact orders and payment identities."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .serialization import dumps, loads


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.files = self.root / "files"
        self.files.mkdir(exist_ok=True, mode=0o700)
        self.path = self.root / "schema-mapper.sqlite3"
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions(
                    owner TEXT PRIMARY KEY, csrf TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS previews(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, mapping TEXT NOT NULL,
                    format TEXT NOT NULL, source_path TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS orders(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    mapping TEXT NOT NULL, artifact TEXT NOT NULL, artifact_hash TEXT NOT NULL,
                    mode TEXT NOT NULL, amount INTEGER NOT NULL, currency TEXT NOT NULL,
                    metadata TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'prepared',
                    session_id TEXT UNIQUE, checkout_url TEXT, payment_intent TEXT UNIQUE,
                    refunded INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, expires REAL NOT NULL,
                    UNIQUE(owner,fingerprint,mode));
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, type TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS rate_limits(bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS support_requests(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, order_id TEXT NOT NULL,
                    email TEXT NOT NULL, message TEXT NOT NULL, created REAL NOT NULL);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def new_session(self):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (digest(token), csrf, time.time()))
        return token, csrf

    def session(self, token):
        if not isinstance(token, str) or len(token) > 128:
            raise KeyError("session")
        with self.connection() as db:
            row = db.execute("SELECT * FROM sessions WHERE owner=? AND created>?",
                             (digest(token), time.time() - 7 * 86400)).fetchone()
        if not row:
            raise KeyError("session")
        return dict(row)

    def rate_limit(self, bucket, *, limit, seconds):
        now = time.time()
        with self.connection() as db:
            db.execute("DELETE FROM rate_limits WHERE expires<?", (now,))
            row = db.execute("SELECT * FROM rate_limits WHERE bucket=?", (bucket,)).fetchone()
            if row and row["count"] >= limit:
                raise ValueError("request limit reached; retry later")
            if row:
                db.execute("UPDATE rate_limits SET count=count+1 WHERE bucket=?", (bucket,))
            else:
                db.execute("INSERT INTO rate_limits VALUES(?,?,?)", (bucket, 1, now + seconds))

    def save_preview(self, owner, mapping, format, raw):
        identifier = "map_" + secrets.token_hex(16)
        path = self.files / (identifier + "." + format)
        try:
            with self.connection() as db:
                count = db.execute("SELECT COUNT(*) FROM previews WHERE owner=? AND expires>?",
                                   (owner, time.time())).fetchone()[0]
                total = db.execute("SELECT COUNT(*) FROM previews WHERE expires>?", (time.time(),)).fetchone()[0]
                if count >= 5 or total >= 200:
                    raise ValueError("active preview capacity reached")
                path.write_bytes(raw)
                path.chmod(0o600)
                db.execute("INSERT INTO previews VALUES(?,?,?,?,?,?)",
                           (identifier, owner, dumps(mapping), format, path.name, time.time() + 3600))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return identifier

    def preview(self, owner, identifier):
        with self.connection() as db:
            row = db.execute("SELECT * FROM previews WHERE id=? AND owner=? AND expires>?",
                             (identifier, owner, time.time())).fetchone()
        if not row:
            raise KeyError("preview")
        return {**dict(row), "mapping": loads(row["mapping"])}

    def save_order(self, owner, mapping, artifact: bytes, mode: str):
        identifier = "order_" + secrets.token_hex(16)
        fp = mapping["mapping_fingerprint"]
        metadata = {"product_namespace": "schema-mapper", "order_id": identifier,
                    "mapping_fingerprint": fp, "owner_fingerprint": owner,
                    "purpose": "customer-artifact"}
        path = self.files / (identifier + ".zip")
        with self.connection() as db:
            existing = db.execute("SELECT * FROM orders WHERE owner=? AND fingerprint=? AND mode=?",
                                  (owner, fp, mode)).fetchone()
            if existing:
                return dict(existing)
            count = db.execute("SELECT COUNT(*) FROM orders WHERE owner=? AND expires>?",
                               (owner, time.time())).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM orders WHERE expires>?", (time.time(),)).fetchone()[0]
            if count >= 10 or total >= 1000:
                raise ValueError("artifact capacity reached")
            path.write_bytes(artifact)
            path.chmod(0o600)
            db.execute("""INSERT INTO orders(id,owner,fingerprint,mapping,artifact,artifact_hash,mode,
                amount,currency,metadata,created,expires) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (identifier, owner, fp, dumps(mapping), path.name,
                        hashlib.sha256(artifact).hexdigest(), mode, 900, "usd", dumps(metadata),
                        time.time(), time.time() + 86400))
            return dict(db.execute("SELECT * FROM orders WHERE id=?", (identifier,)).fetchone())

    def order(self, owner, identifier):
        with self.connection() as db:
            row = db.execute("SELECT * FROM orders WHERE id=? AND owner=?", (identifier, owner)).fetchone()
        if not row:
            raise KeyError("order")
        return dict(row)

    def attach_checkout(self, owner, identifier, session_id, url):
        with self.connection() as db:
            row = db.execute("SELECT * FROM orders WHERE owner=? AND id=?", (owner, identifier)).fetchone()
            if not row or row["status"] not in {"prepared", "checkout"}:
                raise ValueError("order cannot start checkout")
            if row["session_id"] and row["session_id"] != session_id:
                raise ValueError("checkout identity changed")
            db.execute("UPDATE orders SET status='checkout',session_id=?,checkout_url=? WHERE id=?",
                       (session_id, url, identifier))

    def fulfill(self, session: dict, *, mode: str, charge: dict | None = None,
                event_id: str | None = None, event_type="reconcile"):
        """Provider-retrieved settled session; transaction protects duplicate delivery."""
        with self.connection() as db:
            row = db.execute("SELECT * FROM orders WHERE session_id=?", (session.get("id"),)).fetchone()
            if not row:
                raise ValueError("unowned checkout session")
            if (row["mode"] != mode or session.get("livemode") is not (mode == "live")
                or session.get("mode") != "payment" or session.get("metadata") != loads(row["metadata"])
                or session.get("client_reference_id") != row["id"]
                or session.get("amount_subtotal") != row["amount"] or session.get("amount_total") != row["amount"]
                or session.get("currency") != row["currency"]
                or session.get("total_details", {}).get("amount_discount", 0) != 0
                or session.get("total_details", {}).get("amount_shipping", 0) != 0
                or session.get("total_details", {}).get("amount_tax", 0) != 0):
                raise ValueError("payment differs from immutable order")
            if session.get("payment_status") != "paid":
                return "pending"
            intent = session.get("payment_intent")
            if not isinstance(intent, str) or not intent.startswith("pi_"):
                raise ValueError("paid session has no payment identity")
            if row["payment_intent"] and row["payment_intent"] != intent:
                raise ValueError("payment identity changed")
            if (not charge or charge.get("payment_intent") != intent
                or charge.get("livemode") is not (mode == "live") or charge.get("amount") != row["amount"]
                or charge.get("currency") != row["currency"] or charge.get("paid") is not True
                or not isinstance(charge.get("amount_refunded"), int)
                or not 0 <= charge["amount_refunded"] <= row["amount"]):
                raise ValueError("settled charge readback differs from order")
            refunded = max(row["refunded"], charge["amount_refunded"])
            status = "refunded" if refunded else "paid"
            expires = max(row["expires"], time.time() + 7 * 86400) if not row["payment_intent"] else row["expires"]
            db.execute("UPDATE orders SET status=?,payment_intent=?,expires=?,refunded=? WHERE id=?",
                       (status, intent, expires, refunded, row["id"]))
            if event_id:
                db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?)", (event_id, event_type, time.time()))
            return status

    def refund(self, charge: dict, *, mode: str, event_id: str):
        with self.connection() as db:
            row = db.execute("SELECT * FROM orders WHERE payment_intent=?", (charge.get("payment_intent"),)).fetchone()
            if not row:
                return "ignored"
            if (row["mode"] != mode or charge.get("livemode") is not (mode == "live")
                or charge.get("currency") != row["currency"] or charge.get("amount") != row["amount"]):
                raise ValueError("refund differs from paid order")
            refunded = charge.get("amount_refunded")
            if not isinstance(refunded, int) or not 0 <= refunded <= row["amount"]:
                raise ValueError("invalid refund amount")
            refunded = max(refunded, row["refunded"])
            db.execute("UPDATE orders SET refunded=?,status=? WHERE id=?",
                       (refunded, "refunded" if refunded else row["status"], row["id"]))
            db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?)", (event_id, "charge.refunded", time.time()))
            return "refunded"

    def download(self, owner, identifier):
        order = self.order(owner, identifier)
        if order["status"] != "paid" or order["refunded"] or order["expires"] <= time.time():
            raise ValueError("settled non-refunded purchase required within retention period")
        path = self.files / order["artifact"]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != order["artifact_hash"]:
            raise ValueError("artifact integrity check failed")
        return data

    def cleanup(self):
        now = time.time()
        with self.connection() as db:
            for row in db.execute("SELECT source_path FROM previews WHERE expires<=?", (now,)):
                (self.files / row["source_path"]).unlink(missing_ok=True)
            db.execute("DELETE FROM previews WHERE expires<=?", (now,))
            for row in db.execute("SELECT artifact FROM orders WHERE expires<=?", (now,)):
                (self.files / row["artifact"]).unlink(missing_ok=True)
            db.execute("UPDATE orders SET mapping='{}' WHERE expires<=?", (now,))
            db.execute("DELETE FROM sessions WHERE created<?", (now - 7 * 86400,))
            db.execute("DELETE FROM support_requests WHERE created<?", (now - 30 * 86400,))

    def request_support(self, owner, order_id, *, email, message):
        self.order(owner, order_id)
        if not isinstance(email, str) or not 3 <= len(email) <= 254 or email.count("@") != 1 or "\n" in email:
            raise ValueError("a contact email is required")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000:
            raise ValueError("support message needs 1..2000 characters")
        self.rate_limit("support:" + owner, limit=5, seconds=86400)
        identifier = "support_" + secrets.token_hex(16)
        with self.connection() as db:
            db.execute("INSERT INTO support_requests VALUES(?,?,?,?,?,?)",
                       (identifier, owner, order_id, email, message, time.time()))
        return {"id": identifier, "status": "saved", "note": "Your request is saved for merchant review."}
