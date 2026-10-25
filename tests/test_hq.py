import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hq import clocks
from hq.agent import ReplayTransport, Run, verify_final
from hq.data import Data, parse, ROOT
from hq.tools import Tools

D = Data()
SNAP = D.snapshot


def replay(order):
    rec = json.load(open(os.path.join(ROOT, "traces", order + ".json"), encoding="utf-8"))
    run = Run(D, SNAP, order, ReplayTransport(rec["model_outputs"]), "x")
    run.advance()
    return run


class Engine(unittest.TestCase):
    def test_snapshot_counts(self):
        b = clocks.build(D)
        self.assertEqual(b["counts"]["queue"] + b["counts"]["ledger"], 77)  # 100 orders, 21 delivered, 2 cancelled

    def test_soft_reminders_never_promoted(self):
        for c in clocks.build(D)["queue"]:
            if c["tier"] == "REMINDER":
                self.assertTrue(all(k["tier"] == "REMINDER" for k in c["clocks"]))

    def test_tier_first_then_time(self):
        q = clocks.build(D)["queue"]
        idx = [clocks.TIERS.index(c["tier"]) for c in q]
        self.assertEqual(idx, sorted(idx))
        for t in ("CUTOFF", "SLOT"):
            secs = [c["clock"]["seconds_left"] for c in q if c["tier"] == t]
            self.assertEqual(secs, sorted(secs))

    def test_every_clock_has_a_source_in_the_data(self):
        for c in clocks.build(D)["queue"]:
            for k in c["clocks"]:
                self.assertIsNotNone(D.get(k["source"]["table"], k["source"]["id"]), k)

    def test_hidden_orders_have_no_open_evidence(self):
        for c in clocks.build(D)["ledger"]:
            self.assertEqual(c["evidence"], [])

    def test_cancelled_orders_are_not_active(self):
        b = clocks.build(D)
        ids = {c["order_id"] for c in b["queue"] + b["ledger"]}
        self.assertFalse({"ord-00201", "ord-00419"} & ids)

    def test_eta_after_window_is_surfaced(self):  # the order the first version of the rules hid
        self.assertIn("ord-00439", {c["order_id"] for c in clocks.build(D)["queue"]})

    def test_no_clock_means_no_invented_urgency(self):
        for c in clocks.build(D)["queue"]:
            if not c["clocks"]:
                self.assertEqual(c["tier"], "NONE")

    def test_later_clock_only_adds_orders(self):
        a = {c["order_id"] for c in clocks.build(D)["queue"]}
        b = {c["order_id"] for c in clocks.build(D, parse("2026-10-24T09:00:00Z"))["queue"]}
        self.assertTrue(a <= b)

    def test_missed_slot_is_flagged_after_it_ends(self):
        b = clocks.build(D, parse("2026-10-24T09:00:00Z"))
        c = next(x for x in b["queue"] if x["order_id"] == "ord-00299")
        self.assertIn("MISSED_WINDOW", {e["kind"] for e in c["evidence"]})


class ToolsAndGuards(unittest.TestCase):
    def setUp(self):
        self.t = Tools(D, SNAP)

    def test_timed_out_booking_is_unknown(self):
        r = self.t.call("verify_booking_outcome", {"order_id": "ord-00299"})
        self.assertTrue(r["verdict"].startswith("UNKNOWN"))

    def test_timing_is_computed_from_stated_fields(self):
        r = self.t.call("check_timing", {"order_id": "ord-00299", "travel_option_id": "travel-xf29ed1732d895f37"})
        self.assertEqual(r["terminal_margin_hours_worst_case"], 16.0)
        self.assertTrue(r["delivery_window_still_reachable"])

    def test_reservation_timing_admits_what_it_cannot_compute(self):
        r = self.t.call("check_timing", {"order_id": "ord-00603", "travel_option_id": "res-x1082202e8d139d61"})
        self.assertEqual(r["minutes_to_check_in_cutoff_at_sim_clock"], 180)
        self.assertIn("cannot_compute", r)

    def test_unknown_tool_and_order(self):
        self.assertIn("error", self.t.call("book_ferry", {}))
        self.assertIn("error", self.t.call("get_order", {"order_id": "ord-nope"}))

    def test_unrecognised_driver_reply_stops(self):
        r = self.t.call("message_driver", {"order_id": "ord-00299", "text": "what is the meaning of life"})
        self.assertFalse(r["recognised"])

    def test_verifier_drops_ungrounded_and_ranks_strength_from_data(self):
        f = {"known": [{"fact": "ok", "source_ids": ["alert-a"]}, {"fact": "made up", "source_ids": ["msg-zzz"]}, {"fact": "none", "source_ids": []}],
             "options": [{"id": "A", "steps": [{"action": "book_ferry", "reversible": False}, {"action": "book_hotel", "reversible": True},
                                                {"action": "teleport", "reversible": True}, {"action": "hold_and_verify", "reversible": True}]}]}
        g = verify_final(f, {"alert-a"})
        self.assertEqual((g["kept"], len(g["dropped"])), (1, 2))
        self.assertEqual(f["known"][0]["strength"], "SIGNAL_ONLY")
        acts = [s["action"] for s in f["options"][0]["steps"]]
        self.assertEqual(acts[0], "hold_and_verify")
        self.assertEqual(acts[-1], "book_ferry")  # the step that cannot be undone goes last
        self.assertEqual(len(acts), 4)

    def test_customer_steps_are_human_only(self):
        f = {"known": [], "options": [{"id": "A", "steps": [{"action": "ask_customer_approval"}, {"action": "update_delivery_promise"}]}]}
        verify_final(f, set())
        self.assertTrue(all(s["human_only"] and s["needs_approval"] for s in f["options"][0]["steps"]))


class RecordedRuns(unittest.TestCase):
    def test_every_recording_replays_clean_and_grounded(self):
        files = [f[:-5] for f in os.listdir(os.path.join(ROOT, "traces")) if f.startswith("ord-")]
        self.assertGreaterEqual(len(files), 3)
        for o in files:
            run = replay(o)
            self.assertEqual(run.status, "done", o)
            self.assertLessEqual(run.calls, 8)
            self.assertEqual(len(run.grounding["dropped"]), 0, o)
            self.assertTrue(run.final["stop_reason"])
            for opt in run.final["options"]:
                for s in opt["steps"]:
                    if s["action"] in ("book_ferry", "cancel_booking", "book_hotel", "book_driver_travel"):
                        self.assertTrue(s["needs_approval"])

    def test_agent_reconciles_before_any_booking_on_00299(self):
        run = replay("ord-00299")
        for opt in run.final["options"]:
            acts = [s["action"] for s in opt["steps"]]
            if "book_ferry" in acts:
                self.assertEqual(acts[0], "hold_and_verify")


class Approvals(unittest.TestCase):
    def test_order_failure_and_human_gate(self):
        from hq import server
        run = replay("ord-00299")
        opt = run.final["options"][0]
        steps = opt["steps"]
        code, _ = server.approve(run, {"option": opt["id"], "step_id": steps[-1]["id"]})
        self.assertEqual(code, 409)
        hi = next(i for i, s in enumerate(steps) if s["human_only"])
        for s in steps[:hi]:
            self.assertEqual(server.approve(run, {"option": opt["id"], "step_id": s["id"]})[0], 200)
        server.approve(run, {"option": opt["id"], "step_id": steps[hi]["id"]})  # handed, not yet confirmed
        self.assertEqual(server.approve(run, {"option": opt["id"], "step_id": steps[hi + 1]["id"]})[0], 409)
        server.approve(run, {"option": opt["id"], "step_id": steps[hi]["id"], "confirm": True})
        self.assertEqual(server.approve(run, {"option": opt["id"], "step_id": steps[hi + 1]["id"], "simulate_failure": True})[0], 200)
        self.assertEqual(run.done_steps[steps[hi + 1]["id"]]["status"], "FAILED")


if __name__ == "__main__":
    unittest.main(verbosity=1)
