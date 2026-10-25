"""Replay eval. Pre-registered in eval/PREREG.md. Run: python -m eval.replay"""
import json
import random
import sys

from hq import clocks
from hq.data import Data, parse

LATENCIES = [2, 5, 10, 20, 30, 60, 120]
REAL = ("PROMISE", "CUTOFF", "SLOT", "OPTION")


def real_clock_minutes(card, now):
    left = [c["seconds_left"] for c in card["clocks"] if c["tier"] in REAL and c["seconds_left"] > 0]
    return min(left) / 60 if left else None


def misses(order, cards_by_id, now, latency):
    out = []
    for k, c in enumerate(order):
        t = real_clock_minutes(c, now)
        if t is not None and k * latency > t:
            out.append(c["order_id"])
    return out


def main():
    d = Data()
    now = d.snapshot
    built = clocks.build(d, now)
    queue = built["queue"]
    by_id = {c["order_id"]: c for c in queue}
    sev = clocks.severity_sorted(queue)
    tier = list(queue)
    soonest = sorted(queue, key=lambda c: (real_clock_minutes(c, now) if real_clock_minutes(c, now) is not None else 1e12, c["order_id"]))
    scored = [c for c in queue if real_clock_minutes(c, now) is not None]
    print(f"Active orders: {len(queue) + len(built['ledger'])}; in queue: {len(queue)}; hidden: {len(built['ledger'])}")
    print(f"Queue orders with a real future clock (scored): {len(scored)}; without one (not scored): {len(queue) - len(scored)}")
    print("Queue by tier:", built["counts"]["by_tier"])
    print()
    print("Missed real clocks (lower is better), one person, L minutes per order")
    print(f"{'L (min)':>8} | {'severity':>8} | {'tier-first':>10} | {'soonest':>8}")
    table = []
    for L in LATENCIES:
        a, b, c = (len(misses(o, by_id, now, L)) for o in (sev, tier, soonest))
        table.append({"latency_min": L, "severity": a, "tier_first": b, "soonest": c})
        print(f"{L:>8} | {a:>8} | {b:>10} | {c:>8}")

    # secondary: agreement with the README's case-history style label (heuristic)
    has_any = set()
    for oid in d.orders:
        if any(d.rows(oid, t) for t in ("alerts", "incidents", "endpoint_claims", "work_items")) or any(
                c.get("waiting_on") for c in d.rows(oid, "conversations")):
            has_any.add(oid)
    active = {c["order_id"] for c in queue + built["ledger"]}
    mine = {c["order_id"] for c in queue}
    lab = has_any & active
    tp = len(mine & lab)
    print()
    print(f"Secondary (heuristic label: any alert, incident, claim, work item or waiting thread): label={len(lab)}, queue={len(mine)}, "
          f"overlap={tp}, only in queue={sorted(mine - lab)}, only in label={sorted(lab - mine)}")

    # ledger audit: independent signals
    hidden = built["ledger"]
    flagged = []
    for c in hidden:
        oid = c["order_id"]
        why = []
        for r in d.rows(oid, "travel_reservations"):
            if r["kind"] == "FERRY" and r.get("status") == "BOOKED" and r.get("check_in_confirmation") == "UNCONFIRMED" \
                    and r.get("check_in_cutoff_at") and parse(r["check_in_cutoff_at"]) < now and d.orders[oid]["status"] != "DELIVERED":
                why.append(f"ferry cut-off passed unconfirmed ({r['id']})")
        for p in d.rows(oid, "proofs"):
            if p["status"] == "REJECTED":
                why.append(f"rejected proof ({p['id']})")
        if d.orders[oid]["status"] == "IN_TRANSIT":
            obs = [t for t in d.rows(oid, "tracking_observations") if parse(t["received_at"]) <= now]
            if not obs:
                why.append("in transit with no tracking at all")
            elif (now - max(parse(t["received_at"]) for t in obs)).total_seconds() > 3 * 3600:
                why.append("tracking stale > 3h")
        if why:
            flagged.append((oid, why))
    print()
    print(f"Ledger audit: {len(flagged)} of {len(hidden)} hidden orders trip an independent signal")
    for oid, why in flagged:
        print("  ", oid, "; ".join(why))
    rnd = random.Random(7)
    sample = sorted(rnd.sample([c["order_id"] for c in hidden], 10))
    print("Seeded sample (seed 7) for manual read:", sample)
    print("Sample orders tripping a signal:", [o for o, _ in flagged if o in sample])
    json.dump({"snapshot": "2026-10-21T09:00:00Z", "table": table, "scored": len(scored), "queue": len(queue), "hidden": len(hidden),
               "ledger_audit_flagged": [{"order": o, "why": w} for o, w in flagged], "sample": sample,
               "secondary": {"label": len(lab), "overlap": tp}}, open("eval/results.json", "w"), indent=1)


if __name__ == "__main__":
    main()
