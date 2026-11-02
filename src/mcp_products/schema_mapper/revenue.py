"""Read-only Mapper acquisition and retained-purchase aggregates."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path


def report(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        channels = defaultdict(lambda: {'activated_accounts': set(), 'buyers': set(), 'orders': 0,
                                       'gross_cents': 0, 'refund_cents': 0, 'known_fees_cents': 0,
                                       'unknown_fee_orders': 0})
        for row in db.execute("SELECT campaign,owner FROM marketing_events WHERE event='validated' AND kind='customer' AND campaign NOT LIKE 'internal-%'"):
            channels[row['campaign']]['activated_accounts'].add(row['owner'])
        buyer_orders = defaultdict(int)
        for row in db.execute("SELECT o.*,p.buyer_hash,p.fee_cents,COALESCE(a.campaign,'unknown') AS campaign FROM orders o JOIN marketing_payments p ON p.order_id=o.id LEFT JOIN marketing_orders a ON a.order_id=o.id WHERE o.mode='live'"):
            if json.loads(row['metadata']).get('purpose') != 'customer-artifact' or row['campaign'].startswith('internal-'):
                continue
            c = channels[row['campaign']]
            c['orders'] += 1
            c['gross_cents'] += row['amount']
            c['refund_cents'] += row['refunded']
            c['known_fees_cents'] += row['fee_cents'] or 0
            c['unknown_fee_orders'] += row['fee_cents'] is None
            if row['amount'] > row['refunded'] and row['buyer_hash']:
                c['buyers'].add(row['buyer_hash'])
                buyer_orders[row['buyer_hash']] += 1
        rows = []
        for name, c in sorted(channels.items()):
            rows.append({'campaign': name, **{k: v for k, v in c.items() if not isinstance(v, set)},
                'activated_accounts': len(c['activated_accounts']), 'paying_accounts': len(c['buyers']),
                'cash_after_refunds_cents': c['gross_cents'] - c['refund_cents']})
        activated = len(set().union(*(c['activated_accounts'] for c in channels.values())))
        buyers = len(set().union(*(c['buyers'] for c in channels.values())))
        return {'product': 'schema-mapper', 'by_campaign': rows, 'activated_accounts': activated,
                'paying_accounts': buyers, 'repeat_paid_accounts': sum(v >= 2 for v in buyer_orders.values()),
                'cash_after_refunds_cents': sum(r['cash_after_refunds_cents'] for r in rows),
                'gross_cents': sum(r['gross_cents'] for r in rows), 'refund_cents': sum(r['refund_cents'] for r in rows),
                'hold_expansion': activated >= 20 and buyers == 0,
                'repeat_definition': 'two retained artifact purchases by the same known billing identity',
                'fees': 'only observed processing fees are known; refund fees may be unknown',
                'attribution': 'recorded campaign, not proof an AI agent made the request'}
    finally:
        db.close()
