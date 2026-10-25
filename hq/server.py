"""Zero-dependency server: python -m hq.server  ->  http://localhost:8750"""
import json
import mimetypes
import os
import re
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import clocks
from .agent import ReplayTransport, Run, live_transport
from .data import Data, ROOT, iso, parse
from . import tts
from .tools import Tools

def load_env():
    """Read KEY=VALUE lines from honest-queue/.env into this process only (never committed; see .env.example)."""
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
WEB = os.path.join(ROOT, "web")
TRACES = os.path.join(ROOT, "traces")
STATE = os.path.join(ROOT, "state")
D = Data()
RUNS = {}
LOG = []  # approval + execution events (mock). Mirrors state/events.jsonl
LOCK = threading.Lock()


def log(kind, **kw):
    ev = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, **kw}
    LOG.append(ev)
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def now_from(q):
    v = (q.get("now") or [None])[0]
    n = parse(v) if v else D.snapshot
    return max(n, D.snapshot)


def slug(make, model):
    return re.sub(r"[^a-z0-9]+", "-", f"{make} {model}".lower()).strip("-")


def facts(oid):
    """Plain-language identity of an order: car, route, who."""
    o = D.orders[oid]
    veh = next((D.get("vehicles", i["vehicle_id"]) for i in D.rows(oid, "order_items")), None) or {}
    city = lambda aid: (D.get("addresses", aid) or {}).get("city")
    drv = next((D.get("drivers", a["driver_id"]) for a in D.rows(oid, "work_assignments") if a["status"] != "CANCELLED"), None)
    cus = D.get("customers", o["customer_id"]) or {}
    return {"car": f"{veh.get('make', 'Vehicle')} {veh.get('model', '')}".strip(), "image": slug(veh.get("make", ""), veh.get("model", "")) if veh else None,
            "year": veh.get("model_year"), "from_city": city(o["pickup_address_id"]), "to_city": city(o["delivery_address_id"]),
            "customer": cus.get("name"), "driver": drv.get("display_name") if drv else None}


def plain(text):
    """'FERRY_SOLD_OUT: Current crossing ...' -> 'Current crossing ...'"""
    return re.sub(r"^[A-Z0-9_]{4,}: ", "", text or "")


def slim(c):
    k = c["clock"]
    return {**facts(c["order_id"]), "headline": plain((c["evidence"] or [{"text": c.get("ledger_reason", "")}])[0]["text"]),
            **_slim(c, k)}


def _slim(c, k):
    return {"order_id": c["order_id"], "reference": c["reference"], "status": c["status"], "route_kind": c["route_kind"],
            "tier": c["tier"], "tier_name": clocks.TIER_INFO[c["tier"]][0], "clock": k, "severity": c["severity"],
            "why": [e["text"] for e in c["evidence"]][:3], "evidence_kinds": sorted({e["kind"] for e in c["evidence"]}),
            "has_trace": os.path.exists(os.path.join(TRACES, c["order_id"] + ".json")),
            "ledger_reason": c.get("ledger_reason"), "watch": c.get("watch", [])}


def api_queue(q):
    b = clocks.build(D, now_from(q))
    return {"now": b["now"], "snapshot": iso(D.snapshot), "counts": b["counts"], "tiers": {t: {"name": clocks.TIER_INFO[t][0], "help": clocks.TIER_INFO[t][1]} for t in clocks.TIERS},
            "queue": [slim(c) for c in b["queue"]], "ledger": [slim(c) for c in b["ledger"]]}


def api_glance(q):
    """Everything the top of the page needs, from the live queue plus the recorded run for the top order."""
    b = api_queue(q)
    top = next((c for c in b["queue"] if c["has_trace"]), b["queue"][0] if b["queue"] else None)
    out = {"counts": b["counts"], "now": b["now"], "top": top, "agent": None}
    if top and top["has_trace"]:
        t = json.load(open(os.path.join(TRACES, top["order_id"] + ".json"), encoding="utf-8"))
        f, g = t["final"], t["grounding"]
        out["agent"] = {"headline": f["headline"], "stop_reason": f["stop_reason"], "decision": f["decision"], "lookups": t["calls"],
                        "kept": g["kept"], "claims": g["claims"], "model": t["model"]}
    return out


def api_order(oid, q):
    now = now_from(q)
    b = clocks.build(D, now)
    card = next((c for c in b["queue"] + b["ledger"] if c["order_id"] == oid), None)
    if not card:
        return None
    t = Tools(D, now)
    props = [{"id": p["id"], "command": p["command"], "status": p["status"], "summary": p["summary"], "created_at": p["created_at"],
              "reviewed": bool(p.get("review")), "error": (p.get("execution") or {}).get("error")} for p in D.rows(oid, "agent_proposals")]
    msgs = t.call("get_messages", {"order_id": oid})["messages"]
    return {"card": {**slim(card), "clocks": card["clocks"], "evidence": card["evidence"]}, "messages": msgs, "proposals": props,
            "existing_trace": os.path.exists(os.path.join(TRACES, oid + ".json")), "tz": D.tz_of_order(oid),
            "runs": [r.export() for r in RUNS.values() if r.order_id == oid]}


def start_run(body):
    oid, mode = body["order_id"], body.get("mode", "recorded")
    now = now_from({"now": [body.get("now")]})
    if mode == "live":
        tr = live_transport()
        if not tr:
            return 503, {"error": "No model configured for live runs. Set ANTHROPIC_API_KEY, or HQ_OPENAI_BASE + HQ_OPENAI_KEY (e.g. Groq free tier), or HQ_CLAUDE_BIN. See the README. Recorded mode works without one."}
    else:
        p = os.path.join(TRACES, oid + ".json")
        if not os.path.exists(p):
            return 404, {"error": f"No recorded run for {oid}."}
        rec = json.load(open(p, encoding="utf-8"))
        tr = ReplayTransport(rec["model_outputs"], model=rec["model"])
    run = Run(D, now if mode == "live" else D.snapshot, oid, tr, TRACES)
    run.pace = float(body.get("pace", 0.45)) if mode != "live" else 0  # the video page passes pace 0 so takes are exact
    with LOCK:
        RUNS[run.id] = run

    def go():
        run.advance()
        if mode == "live":
            run.save()
    threading.Thread(target=go, daemon=True).start()
    return 200, {"id": run.id}


def run_say(run, text):
    if run.mode == "live":
        threading.Thread(target=run.say, args=(text,), daemon=True).start()
        return {"ok": True}
    # recorded runs cannot reason about new questions: answer only from the trace and say so
    t = text.lower()
    if any(k in t for k in ("why", "stop", "reason")):
        ans = f"Stop reason on record: {run.stop or 'none recorded'}."
    elif any(k in t for k in ("source", "where", "cite")):
        n = len(run.final.get("known", [])) if run.final else 0
        ans = f"{n} grounded facts, each with source ids you can open on the card. {len((run.grounding or {}).get('dropped', []))} claims were dropped as ungrounded."
    else:
        ans = "This is a recorded run, so I can only answer from its trace: ask why it stopped, or where a fact came from. A live model key lets it look things up for new questions."
    run._step(kind="USER", text=text)
    run._step(kind="SAY", text=ans + " (answered from the trace, not by the model)")
    return {"ok": True}


def approve(run, body):
    """Mock executor. Nothing real happens; every action is logged and clearly marked MOCK."""
    opt = next((o for o in (run.final or {}).get("options", []) if o["id"] == body.get("option")), None)
    if not opt:
        return 400, {"error": "unknown option"}
    done = getattr(run, "done_steps", None)
    if done is None:
        done = run.done_steps = {}
    sid = body["step_id"]
    if body.get("undo"):
        if done.get(sid, {}).get("status") != "DONE":
            return 400, {"error": "that step is not executed"}
        done[sid]["status"] = "UNDONE"
        log("UNDO", run=run.id, step=sid, note="MOCK compensation applied")
        return 200, {"steps": done}
    steps = opt["steps"]
    idx = next((i for i, s in enumerate(steps) if s["id"] == sid), None)
    if idx is None:
        return 400, {"error": "unknown step"}
    for s in steps[:idx]:
        if done.get(s["id"], {}).get("status") not in ("DONE", "CONFIRMED"):
            return 409, {"error": f"Approve '{s['label']}' first. Steps go in order, reversible ones first."}
    s = steps[idx]
    if s["human_only"]:
        if body.get("confirm") and done.get(sid, {}).get("status") == "HANDED":
            done[sid] = {"status": "CONFIRMED", "note": "A person confirmed this is done. Later steps are unlocked."}
            log("PERSON_CONFIRMED", run=run.id, step=sid)
        else:
            done[sid] = {"status": "HANDED", "note": "Handed to a person. The agent never contacts a customer. Later steps stay locked until they confirm."}
            log("HANDED_TO_PERSON", run=run.id, step=sid)
        return 200, {"steps": done}
    if body.get("simulate_failure"):
        done[sid] = {"status": "FAILED", "note": "MOCK provider failure. Nothing was booked.", "compensation": opt.get("compensation")}
        log("EXECUTION_FAILED", run=run.id, step=sid)
        return 200, {"steps": done}
    done[sid] = {"status": "DONE", "note": f"MOCK executed: {s['label']}", "ref": f"MOCK-{zlib.crc32((sid + run.id).encode()) % 100000:05d}", "reversible": s.get("reversible", False)}
    log("EXECUTED", run=run.id, step=sid, ref=done[sid]["ref"], approved_by="operator (mock)")
    return 200, {"steps": done}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype == "application/json" else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        try:
            if p.startswith("/api/final/"):
                tp = os.path.join(TRACES, p.split("/")[3] + ".json")
                if not os.path.exists(tp):
                    return self.send(404, {"error": "no recorded run"})
                f = json.load(open(tp, encoding="utf-8"))["final"]
                return self.send(200, {k: f.get(k) for k in ("headline", "stop_reason", "decision")})
            if p == "/api/glance":
                return self.send(200, api_glance(q))
            if p == "/api/queue":
                return self.send(200, api_queue(q))
            if p.startswith("/api/order/"):
                r = api_order(p.split("/")[3], q)
                return self.send(200 if r else 404, r or {"error": "no such order"})
            if p == "/api/eval":
                res = os.path.join(ROOT, "eval", "results.json")
                return self.send(200, json.load(open(res)) if os.path.exists(res) else {})
            if p.startswith("/api/run/"):
                run = RUNS.get(p.split("/")[3])
                return self.send(200 if run else 404, {**run.export(), "done_steps": getattr(run, "done_steps", {})} if run else {"error": "no run"})
            if p == "/api/info":
                tr = live_transport()
                return self.send(200, {"live_model": tr.name if tr else None, "tts": tts.configured(), "tts_cached": len([f for f in os.listdir(tts.AUDIO_DIR) if f.endswith(".wav")]) if os.path.isdir(tts.AUDIO_DIR) else 0, "recorded": sorted(f[:-5] for f in os.listdir(TRACES) if f.endswith(".json") and f.startswith("ord-")) if os.path.isdir(TRACES) else [], "events": LOG[-20:]})
            fp = os.path.normpath(os.path.join(WEB, "index.html" if p == "/" else p.lstrip("/")))
            if fp.startswith(WEB) and os.path.isfile(fp):
                return self.send(200, open(fp, "rb").read(), mimetypes.guess_type(fp)[0] or "application/octet-stream")
            self.send(404, {"error": "not found"})
        except Exception as e:  # keep the demo up
            self.send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            if p == "/api/tts":
                text = (body.get("text") or "").strip()
                if not text or len(text) > 1500:
                    return self.send(400, {"error": "text missing or too long"})
                try:
                    return self.send(200, tts.synth(text), "audio/wav")
                except tts.TtsError as e:
                    return self.send(503, {"error": str(e)})
            if p == "/api/run":
                code, r = start_run(body)
                return self.send(code, r)
            parts = p.split("/")
            if len(parts) == 5 and parts[2] == "run":
                run = RUNS.get(parts[3])
                if not run:
                    return self.send(404, {"error": "no run"})
                if parts[4] == "say":
                    return self.send(200, run_say(run, body["text"]))
                if parts[4] == "approve":
                    code, r = approve(run, body)
                    return self.send(code, r)
            self.send(404, {"error": "not found"})
        except Exception as e:
            self.send(500, {"error": f"{type(e).__name__}: {e}"})


def main():
    port = int(os.environ.get("PORT", "8750"))
    print(f"Cutoff on http://localhost:{port}  (live model: {live_transport().name if live_transport() else 'none, recorded runs only'})")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
