"""Loads the Bahn packet (fictional, JSONL) once and indexes it by order."""
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

SNAPSHOT = "2026-10-21T09:00:00Z"  # data/manifest.json: the one snapshot time of the packet


def parse(ts):
    if ts is None:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Data:
    def __init__(self, data_dir=DATA_DIR):
        self.t = {}
        for name in os.listdir(data_dir):
            if name.endswith(".jsonl"):
                with open(os.path.join(data_dir, name), encoding="utf-8") as f:
                    self.t[name[:-6]] = [json.loads(line) for line in f if line.strip()]
        self.by_id = {k: {r["id"]: r for r in v if "id" in r} for k, v in self.t.items()}
        self.by_order = defaultdict(lambda: defaultdict(list))
        for table, rows in self.t.items():
            for r in rows:
                oid = r.get("order_id")
                if oid:
                    self.by_order[oid][table].append(r)
        self.orders = {o["id"]: o for o in self.t["orders"]}
        self.snapshot = parse(SNAPSHOT)

    def rows(self, oid, table):
        return self.by_order[oid].get(table, [])

    def get(self, table, rid):
        return self.by_id.get(table, {}).get(rid)

    def tz_of_order(self, oid):
        """Local zone of the order's selected delivery window (display only)."""
        for w in self.rows(oid, "time_windows"):
            if w["purpose"] == "CUSTOMER_DELIVERY_PROMISE" and w.get("is_selected"):
                return w.get("timezone", "UTC")
        return "UTC"
