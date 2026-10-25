"""Tools the travel agent can call. All are read-only lookups or clearly simulated.

Nothing here books, changes or messages a customer. The only 'write-like' tool is
message_driver, which talks to a SCRIPTED simulated driver (the brief allows this).
"""
import re
import zlib
from .data import iso, parse

ID_RE = re.compile(r"^(msg|alert|travel|res|asg|agent|ord|step|win|incident|work|conv|evt|offer|doc|drv|cus|ep|track|terminal|plan|seg|veh|inote|sim)-[A-Za-z0-9-]+$")


def collect_ids(obj, out=None):
    out = set() if out is None else out
    if isinstance(obj, dict):
        for v in obj.values():
            collect_ids(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_ids(v, out)
    elif isinstance(obj, str) and ID_RE.match(obj):
        out.add(obj)
    return out


class Tools:
    SPEC = {
        "get_order": "get_order(order_id) -> status, route, windows, deadlines, endpoint details",
        "get_messages": "get_messages(order_id) -> every message on the order, with ids and authors",
        "get_travel": "get_travel(order_id) -> ferry/train/flight options and reservations with status, quote, cut-off, checked_at",
        "get_assignment": "get_assignment(order_id) -> assigned driver, assignment steps, fee",
        "verify_booking_outcome": "verify_booking_outcome(order_id) -> did earlier booking attempts leave a reservation or confirmation?",
        "check_timing": "check_timing(order_id, travel_option_id) -> can the driver reach the terminal before its cut-off, and is the delivery window still reachable after arrival? Computed from stated fields",
        "message_driver": "message_driver(order_id, text) -> reply from the SIMULATED driver (scripted)",
    }

    def __init__(self, data, now):
        self.d = data
        self.now = now

    def call(self, name, args):
        fn = getattr(self, "t_" + name, None)
        if not fn or name not in self.SPEC:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": f"bad arguments: {e}"}
        except KeyError as e:
            return {"error": f"not found: {e}"}

    # ---- lookups -------------------------------------------------------------
    def _order(self, oid):
        if oid not in self.d.orders:
            raise KeyError(oid)
        return self.d.orders[oid]

    def t_get_order(self, order_id):
        o = self._order(order_id)
        wins = [{"id": w["id"], "purpose": w["purpose"], "start_at": w["start_at"], "end_at": w["end_at"],
                 "timezone": w["timezone"], "selected": w["is_selected"], "commitment_deadline_at": w.get("commitment_deadline_at")}
                for w in self.d.rows(order_id, "time_windows") if w["is_selected"]]
        eps = [{"id": e["id"], "boundary": e["boundary"], "contact_status": e["contact_status"], "access_status": e["access_status"],
                "instruction": e["access_instruction"]} for e in self.d.rows(order_id, "order_endpoint_details")]
        items = [self.d.get("vehicles", i["vehicle_id"]) for i in self.d.rows(order_id, "order_items")]
        return {"id": order_id, "reference": o["reference"], "status": o["status"], "route_kind": o["route_kind"],
                "vehicles": [{"id": v["id"], "make": v.get("make"), "model": v.get("model")} for v in items if v],
                "selected_windows": wins, "endpoints": eps}

    def t_get_messages(self, order_id):
        self._order(order_id)
        ms = [m for m in self.d.rows(order_id, "messages") if parse(m["authored_at"]) <= self.now]
        return {"messages": [{"id": m["id"], "at": m["authored_at"], "from": f"{m['author_type']}:{m['author_id']}",
                              "channel": m["channel"], "visibility": m["visibility"], "body": m["body"]} for m in sorted(ms, key=lambda m: m["authored_at"])]}

    def t_get_travel(self, order_id):
        self._order(order_id)
        opts = [{k: t.get(k) for k in ("id", "kind", "provider", "status", "from_city", "to_city", "departure_at", "arrival_at",
                                      "check_in_cutoff_at", "quote_cents", "currency", "capacity_note", "checked_at", "road_time_to_terminal_hours")}
                for t in self.d.rows(order_id, "travel_options")]
        res = [{k: r.get(k) for k in ("id", "kind", "status", "booking_reference", "departure_at", "check_in_cutoff_at",
                                     "check_in_confirmation", "boarding_confirmation", "expected_arrival_at")} for r in self.d.rows(order_id, "travel_reservations")]
        return {"options": opts, "reservations": res,
                "note": "A quote (AVAILABLE_QUOTE) is not a booking. A reservation with UNCONFIRMED check-in is not proof of boarding."}

    def t_get_assignment(self, order_id):
        self._order(order_id)
        a = self.d.rows(order_id, "work_assignments")
        steps = self.d.rows(order_id, "assignment_steps")
        out = []
        for x in a:
            drv = self.d.get("drivers", x["driver_id"]) or {}
            out.append({"id": x["id"], "driver_id": x["driver_id"], "driver_home_city": drv.get("home_city"), "status": x["status"],
                        "fee_cents": x["fee_cents"], "currency": x["currency"]})
        return {"assignments": out, "steps": [{"id": s["id"], "kind": s["kind"], "title": s["title"], "status": s["status"],
                                                "scheduled_start_at": s["scheduled_start_at"], "scheduled_end_at": s["scheduled_end_at"]} for s in steps]}

    def t_verify_booking_outcome(self, order_id):
        self._order(order_id)
        attempts = []
        for p in self.d.rows(order_id, "agent_proposals"):
            ex = p.get("execution")
            if ex and p["command"].startswith("travel.book"):
                attempts.append({"proposal_id": p["id"], "command": p["command"], "attempted_at": ex.get("attempted_at"),
                                 "status": ex.get("status"), "error": ex.get("error"), "effect_event_ids": ex.get("effect_event_ids")})
        res = [r["id"] for r in self.d.rows(order_id, "travel_reservations") if r["kind"] == "FERRY"]
        failed = [a for a in attempts if a["status"] == "FAILED" and not a["effect_event_ids"]]
        if failed and not res:
            verdict = "UNKNOWN: a booking attempt timed out and no reservation or confirmation reference is recorded. Do not retry before reconciling with the provider."
        elif failed:
            verdict = "A failed attempt exists and a reservation exists for the order. Reconcile which reservation belongs to the attempt before any retry."
        else:
            verdict = "No failed booking attempt on record."
        return {"attempts": attempts, "reservation_ids": res, "verdict": verdict}

    def t_check_timing(self, order_id, travel_option_id):
        self._order(order_id)
        if travel_option_id.startswith("res-"):
            return self._reservation_timing(order_id, travel_option_id)
        t = self.d.get("travel_options", travel_option_id)
        if not t or t["order_id"] != order_id:
            raise KeyError(travel_option_id)
        pick = [w for w in self.d.rows(order_id, "time_windows") if w["purpose"] == "SELECTED_PICKUP_SLOT" and w["is_selected"]]
        win = [w for w in self.d.rows(order_id, "time_windows") if w["purpose"] == "CUSTOMER_DELIVERY_PROMISE" and w["is_selected"]]
        out = {"travel_option_id": t["id"], "option_status": t["status"], "inputs": {"check_in_cutoff_at": t["check_in_cutoff_at"], "arrival_at": t["arrival_at"],
                                                                                 "road_time_to_terminal_hours": t.get("road_time_to_terminal_hours")}}
        if pick and t.get("road_time_to_terminal_hours") is not None and t["check_in_cutoff_at"]:
            ready = parse(pick[0]["end_at"])
            arrive = ready.timestamp() + t["road_time_to_terminal_hours"] * 3600
            slack = (parse(t["check_in_cutoff_at"]).timestamp() - arrive) / 3600
            out["pickup_slot_end_at"] = pick[0]["end_at"]
            out["terminal_margin_hours_worst_case"] = round(slack, 1)
            out["terminal_reachable_before_cutoff"] = slack >= 0
            out["overnight_needed"] = slack > 8
            out["basis"] = f"pickup slot end ({pick[0]['id']}) + road_time_to_terminal_hours vs check_in_cutoff_at. Excludes breaks and traffic (the packet says so)."
        if win and t.get("arrival_at"):
            end = parse(win[0]["end_at"])
            arrive_t = parse(t["arrival_at"])
            out["delivery_window_end_at"] = win[0]["end_at"]
            out["hours_between_arrival_and_window_end"] = round((end - arrive_t).total_seconds() / 3600, 1)
            out["delivery_window_still_reachable"] = (end - arrive_t).total_seconds() > 3 * 3600
            out["delivery_basis"] = f"option arrival_at vs delivery window ({win[0]['id']}); 3h assumed for terminal to address. Not taken from data."
        return out

    def _reservation_timing(self, order_id, res_id):
        r = self.d.get("travel_reservations", res_id)
        if not r or r["order_id"] != order_id:
            raise KeyError(res_id)
        left = (parse(r["check_in_cutoff_at"]) - self.now).total_seconds() / 60 if r.get("check_in_cutoff_at") else None
        obs = [t for t in self.d.rows(order_id, "tracking_observations") if parse(t["observed_at"]) <= self.now]
        last = max(obs, key=lambda t: t["observed_at"]) if obs else None
        return {"reservation_id": r["id"], "status": r["status"], "check_in_cutoff_at": r.get("check_in_cutoff_at"),
                "check_in_confirmation": r.get("check_in_confirmation"), "boarding_confirmation": r.get("boarding_confirmation"),
                "minutes_to_check_in_cutoff_at_sim_clock": round(left) if left is not None else None,
                "latest_tracking": ({"id": last["id"], "observed_at": last["observed_at"], "location_quality": last["location_quality"],
                                     "distance_remaining_km": last.get("distance_remaining_km")} if last else None),
                "cannot_compute": "Whether the driver reaches the terminal before the cut-off. The packet has no live position or terminal ETA, and its road estimates cannot be applied to tracking progress. A person must ask the driver or the carrier.",
                "basis": "check_in_cutoff_at of the booked reservation vs the simulated clock."}

    # ---- simulated driver ----------------------------------------------------
    def t_message_driver(self, order_id, text):
        self._order(order_id)
        asg = self.d.rows(order_id, "work_assignments")
        if not asg:
            return {"error": "no driver assigned"}
        t = text.lower()
        drv = asg[0]["driver_id"]
        if any(k in t for k in ("later sailing", "next sailing", "alternative", "available", "can you")) and "hotel" not in t:
            body = "A later sailing works for me if the overnight stop and the way home are arranged. My pickup time is unchanged."
        elif any(k in t for k in ("hotel", "overnight", "stay", "rest")):
            body = "I would need a hotel near the terminal the night before and I can take the return train after delivery."
        elif any(k in t for k in ("confirm", "ready", "keys", "pickup")):
            body = "I have not changed my pickup time. I will message when I have the keys."
        else:
            return {"driver_id": drv, "simulated": True, "recognised": False,
                    "reply": None, "note": "UNRECOGNISED: the scripted driver has no answer to this. Stop and hand over to a person."}
        return {"driver_id": drv, "simulated": True, "recognised": True, "reply": body,
                "id": f"sim-{order_id}-{zlib.crc32(body.encode()) % 10000}", "note": "SIMULATED scripted reply"}
