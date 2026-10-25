"""Cutoff core: typed clocks, needs-a-human evidence, tier-first ranking.

Rules this module never breaks:
  * A clock is only created from a field the packet actually states (source recorded).
  * Soft clocks (work-item reminders) are never promoted to hard ones.
  * Position in the queue is explained by tier. Time is only compared inside a tier.
  * "Needs a human" comes from event evidence (open alert, unresolved incident, open work item,
    pending/failed proposal, thread waiting on ops, window already missed), never from a label.
"""
from .data import iso, parse

TIERS = ["PROMISE", "CUTOFF", "SLOT", "OPTION", "REMINDER", "NONE"]
TIER_INFO = {
    "PROMISE": ("Customer promise", "A deadline the customer agreed to (commitment_deadline_at)."),
    "CUTOFF": ("Carrier cut-off", "Check-in closes on a ferry that is actually booked."),
    "SLOT": ("Planned slot", "End of a selected pickup slot or delivery window. Planned, not a commitment."),
    "OPTION": ("Option expiry", "Cut-off on a quote that is NOT booked. The option dies if nobody books it."),
    "REMINDER": ("Soft reminder", "A work-item due time. The schema calls it a reminder, not a promise."),
    "NONE": ("No deadline found", "Nothing in the data gives a clock. The order is shown by evidence only."),
}
SEV = {"critical": 4, "high": 3, "medium": 2, "low": 1, "routine": 1}
OPEN_ALERT = {"OPEN", "ACKNOWLEDGED"}
KIND_RANK = {"WORK_ITEM": 0, "ALERT": 1, "ETA_MISSES_WINDOW": 0, "MISSED_WINDOW": 2, "PROPOSAL": 3, "THREAD": 4, "INCIDENT": 5}  # which sentence reads best as a headline


def human(at):
    return parse(at).strftime("%d %b %H:%M UTC").lstrip("0")


def _clock(tier, at, label, table, rid, field, tz="UTC", basis=""):
    return {"tier": tier, "at": at, "label": label, "tz": tz, "basis": basis,
            "source": {"table": table, "id": rid, "field": field}}


def clocks_for(d, oid):
    o = d.orders[oid]
    out = []
    status = o["status"]
    for w in d.rows(oid, "time_windows"):
        tz = w.get("timezone", "UTC")
        if w["purpose"] == "CUSTOMER_DELIVERY_PROMISE" and w.get("commitment_deadline_at") and status != "DELIVERED":
            out.append(_clock("PROMISE", w["commitment_deadline_at"], "Customer-agreed delivery deadline",
                              "time_windows", w["id"], "commitment_deadline_at", tz,
                              f"{w.get('deadline_source')}, stated {w.get('deadline_stated_at')}"))
        elif w["purpose"] == "CUSTOMER_DELIVERY_PROMISE" and w.get("is_selected") and status != "DELIVERED":
            out.append(_clock("SLOT", w["end_at"], "Selected delivery window ends", "time_windows", w["id"], "end_at", tz,
                              "Customer-selected window, no commitment deadline recorded"))
        elif w["purpose"] == "SELECTED_PICKUP_SLOT" and w.get("is_selected") and status in ("NEW", "WAITING_FOR_PICKUP"):
            out.append(_clock("SLOT", w["end_at"], "Selected pickup slot ends", "time_windows", w["id"], "end_at", tz,
                              f"Slot source: {w.get('source')}"))
    for r in d.rows(oid, "travel_reservations"):
        cut = r.get("check_in_cutoff_at")
        if r["kind"] == "FERRY" and cut and r.get("status") == "BOOKED" and r.get("check_in_confirmation") == "UNCONFIRMED" and status != "DELIVERED":
            out.append(_clock("CUTOFF", cut, f"Ferry check-in closes ({r.get('booking_reference')})", "travel_reservations",
                              r["id"], "check_in_cutoff_at", "UTC", "Booked, check-in not confirmed by a gate scan"))
    for t in d.rows(oid, "travel_options"):
        if t.get("status") == "AVAILABLE_QUOTE" and t.get("check_in_cutoff_at") and status != "DELIVERED":
            out.append(_clock("OPTION", t["check_in_cutoff_at"], f"Quoted sailing check-in closes ({t.get('provider')})",
                              "travel_options", t["id"], "check_in_cutoff_at", "UTC",
                              f"Quote checked {t.get('checked_at')}, not a booking"))
    for w in d.rows(oid, "work_items"):
        if w["status"] == "OPEN" and w.get("due_at"):
            out.append(_clock("REMINDER", w["due_at"], f"Work item due: {w['kind']}", "work_items", w["id"], "due_at", "UTC",
                              "due_at is a reminder, not a promise"))
    return out


def evidence_for(d, oid, now):
    """Event-based reasons a person is needed, each with a source."""
    ev = []
    o = d.orders[oid]
    for a in d.rows(oid, "alerts"):
        if a["status"] in OPEN_ALERT and parse(a["triggered_at"]) <= now:
            ev.append({"kind": "ALERT", "text": f"{a['signal']}: {a['summary']}", "severity": a["severity"],
                       "at": a["triggered_at"], "source": {"table": "alerts", "id": a["id"]}})
    for i in d.rows(oid, "incidents"):
        if not i.get("resolved_at"):
            ev.append({"kind": "INCIDENT", "text": f"Incident: {i['kind'].replace('_', ' ').lower()} ({i['status'].replace('_', ' ').lower()}).", "severity": "high",
                       "at": i["opened_at"], "source": {"table": "incidents", "id": i["id"]}})
    for w in d.rows(oid, "work_items"):
        if w["status"] == "OPEN":
            ev.append({"kind": "WORK_ITEM", "text": w["summary"], "severity": "high" if w["priority"] == "high" else "low",
                       "at": w["opened_at"], "source": {"table": "work_items", "id": w["id"]}})
    for p in d.rows(oid, "agent_proposals"):
        failed = (p.get("execution") or {}).get("status") == "FAILED"
        if p["status"] == "PENDING_REVIEW" or failed:
            ev.append({"kind": "PROPOSAL", "text": ("FAILED execution: " if failed else "Awaiting review: ") + p["summary"],
                       "severity": "high", "at": p["created_at"], "source": {"table": "agent_proposals", "id": p["id"]}})
    for c in d.rows(oid, "conversations"):
        if c["status"] == "OPEN" and c.get("waiting_on"):
            ev.append({"kind": "THREAD", "text": f"Waiting on {c['waiting_on'].replace('_', ' ').lower()} (owner: {c.get('owner')}).",
                       "severity": "medium", "at": c["updated_at"], "source": {"table": "conversations", "id": c["id"]}})
    if o["status"] == "IN_TRANSIT":
        obs = [t for t in d.rows(oid, "tracking_observations") if parse(t["observed_at"]) <= now]
        win = [w for w in d.rows(oid, "time_windows") if w["purpose"] == "CUSTOMER_DELIVERY_PROMISE" and w.get("is_selected")]
        if obs and win:
            last = max(obs, key=lambda t: t["observed_at"])
            late = (parse(last["estimated_delivery_at"]) - parse(win[0]["end_at"])).total_seconds() / 60
            if late > 0:
                ev.append({"kind": "ETA_MISSES_WINDOW", "severity": "high", "at": last["observed_at"],
                           "text": f"The car is now expected {human(last['estimated_delivery_at'])}, {int(late)} min after the delivery window closes ({human(win[0]['end_at'])}). Nobody has raised an alert.",
                           "source": {"table": "tracking_observations", "id": last["id"]}})
    if o["status"] in ("NEW", "WAITING_FOR_PICKUP"):
        for w in d.rows(oid, "time_windows"):
            if w["purpose"] == "SELECTED_PICKUP_SLOT" and w.get("is_selected") and parse(w["end_at"]) < now:
                ev.append({"kind": "MISSED_WINDOW", "text": "Selected pickup slot has ended and the order is not picked up",
                           "severity": "high", "at": w["end_at"], "source": {"table": "time_windows", "id": w["id"]}})
    if o["status"] in ("NEW", "WAITING_FOR_PICKUP", "IN_TRANSIT"):
        for w in d.rows(oid, "time_windows"):
            if w["purpose"] == "CUSTOMER_DELIVERY_PROMISE" and w.get("is_selected") and parse(w["end_at"]) < now:
                ev.append({"kind": "MISSED_WINDOW", "text": "Selected delivery window has ended and the order is not delivered",
                           "severity": "high", "at": w["end_at"], "source": {"table": "time_windows", "id": w["id"]}})
    return ev


def build(d, now=None):
    now = now or d.snapshot
    cards = []
    for oid, o in d.orders.items():
        if o["status"] == "DELIVERED" or o.get("cancelled_at"):  # two cancelled orders still carry an active status
            continue
        clocks = clocks_for(d, oid)
        for c in clocks:
            c["seconds_left"] = int((parse(c["at"]) - now).total_seconds())
        ev = evidence_for(d, oid, now)
        ev.sort(key=lambda e: (-SEV.get(e["severity"], 1), KIND_RANK.get(e["kind"], 9), e["at"]))
        best = min(clocks, key=lambda c: (TIERS.index(c["tier"]), c["seconds_left"])) if clocks else None
        sev = max([SEV.get(e["severity"], 1) for e in ev], default=0)
        cards.append({"order_id": oid, "reference": o["reference"], "status": o["status"], "route_kind": o["route_kind"],
                      "tier": best["tier"] if best else "NONE", "clock": best, "clocks": clocks, "evidence": ev,
                      "severity": sev, "needs_human": bool(ev)})
    queue = [c for c in cards if c["needs_human"]]
    queue.sort(key=lambda c: (TIERS.index(c["tier"]),
                              c["clock"]["seconds_left"] if c["clock"] else 0,
                              -c["severity"], c["order_id"]))
    ledger = [c for c in cards if not c["needs_human"]]
    ledger.sort(key=lambda c: c["order_id"])
    for c in ledger:
        c["ledger_reason"] = ledger_reason(c)
        c["watch"] = watch_notes(d, c["order_id"], now)
    return {"now": iso(now), "queue": queue, "ledger": ledger, "counts": counts(queue, ledger)}


def ledger_reason(c):
    if c["tier"] != "NONE":
        return f"No open alert, incident, work item or pending proposal. Next clock is {TIERS[TIERS.index(c['tier'])].lower()} and nobody has flagged a problem."
    return "No open alert, incident, work item, pending proposal or waiting thread, and no deadline in the data."


def counts(queue, ledger):
    by = {t: 0 for t in TIERS}
    for c in queue:
        by[c["tier"]] += 1
    return {"queue": len(queue), "ledger": len(ledger), "by_tier": by}


def severity_sorted(queue):
    """The baseline a typical board uses: worst first, oldest first."""
    return sorted(queue, key=lambda c: (-c["severity"], min([e["at"] for e in c["evidence"]] or ["9"]), c["order_id"]))


def watch_notes(d, oid, now):
    """Things worth a glance on a hidden order. They do not put it in the queue: the data calls tracking coarse and sometimes missing."""
    out = []
    if d.orders[oid]["status"] == "IN_TRANSIT":
        obs = [t for t in d.rows(oid, "tracking_observations") if parse(t["received_at"]) <= now]
        if not obs:
            out.append("In transit with no tracking observation in the packet.")
        elif (now - max(parse(t["received_at"]) for t in obs)).total_seconds() > 3 * 3600:
            out.append("Last tracking update is more than 3 hours old.")
    return out
