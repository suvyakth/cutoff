"""The travel-disruption agent: a language model in a tool loop, with a budget, a verifier
and a hard stop. The model never acts. It looks things up, then either finishes with a
reviewable plan or says why it stopped.

Protocol (one JSON object per model turn):
  {"tool": "<name>", "args": {...}, "why": "<short>"}   call a lookup
  {"say": "<text>"}                                      answer a person without changing the plan
  {"final": {...}}                                       hand over a plan / decision
"""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from .data import iso
from .tools import Tools, collect_ids

MAX_TOOL_CALLS = 8
CHAT_EXTRA_CALLS = 4
ACTIONS = {
    "hold_and_verify": ("Hold and verify", False),
    "message_driver": ("Message the driver", False),
    "book_hotel": ("Book a hotel", True),
    "book_driver_travel": ("Book driver travel", True),
    "book_ferry": ("Book the ferry", True),
    "cancel_booking": ("Cancel a booking", True),
    "ask_customer_approval": ("Ask the customer to approve (a person does this)", True),
    "update_delivery_promise": ("Change the delivery promise (a person does this)", True),
}
HUMAN_ONLY = {"ask_customer_approval", "update_delivery_promise"}
STRENGTH_ORDER = ["SIGNAL_ONLY", "QUOTE_ONLY", "SIMULATED", "REPORTED_UNVERIFIED", "BOOKED_UNCONFIRMED", "RECORDED"]
PREFIX_STRENGTH = {"alert": "SIGNAL_ONLY", "travel": "QUOTE_ONLY", "sim": "SIMULATED", "msg": "REPORTED_UNVERIFIED",
                   "res": "BOOKED_UNCONFIRMED"}

SYSTEM = """You are the travel agent in Bahn's car-delivery control room. A person in operations will read what you hand over. You help with travel disruptions: sold-out or timed-out ferry bookings, a driver who may miss a connection.

RULES (hard):
- You only LOOK THINGS UP. You cannot book, cancel, change a date, or message a customer. Customers are never contacted by you.
- A quote is not a booking. An alert is a signal, not a diagnosis. A booking attempt that timed out is UNKNOWN until reconciled. Never propose a retry before the outcome is verified.
- Do the cheap work first: read the order, the messages, the travel data, then verify the booking outcome, then compute timing. Ask the (simulated) driver only what you need.
- Every fact you hand over must cite the ids you actually saw in tool results. Anything you cannot cite will be dropped and shown as dropped.
- Know when to stop. If sources conflict, a reply is unrecognised, or you cannot make a safe plan, STOP and hand over what you found with an honest stop_reason. Do not guess.
- Order plan steps reversible-first. Give each option its downside and a compensation plan if a later step fails.
- Costs and ETAs: use only numbers present in tool results (quote_cents/100). If a number is not in the data, say null. Do not invent prices.

TOOLS:
{tools}

OUTPUT: exactly one JSON object per turn and nothing else. Formats:
{"tool": "get_order", "args": {"order_id": "ord-00000"}, "why": "..."}
{"say": "short answer to the person"}
{"final": {
  "headline": "one line a busy person can act on",
  "stop_reason": "why you stopped here (budget, conflict, needs a human decision...)",
  "known": [{"fact": "...", "source_ids": ["msg-...", "travel-..."]}],
  "options": [{"id": "A", "title": "...", "cost_eur": null, "eta": "...", "downside": "...",
               "steps": [{"action": "hold_and_verify|message_driver|book_hotel|book_driver_travel|book_ferry|cancel_booking|ask_customer_approval|update_delivery_promise", "detail": "...", "reversible": true, "cost_eur": null}],
               "compensation": "what happens if a later step fails"}],
  "recommended": "A",
  "decision": "the one decision the person must make",
  "needs_from_human": ["..."]}}
"""


# ---- transports -------------------------------------------------------------
class AnthropicTransport:
    name = "anthropic-api"

    def __init__(self):
        self.key = os.environ["ANTHROPIC_API_KEY"]
        self.model = os.environ.get("HQ_MODEL", "claude-haiku-5-5")

    def complete(self, system, messages):
        body = json.dumps({"model": self.model, "max_tokens": 2500, "system": system, "messages": messages}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", body,
                                     {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json", "User-Agent": "cutoff-hackathon/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)["content"][0]["text"]


class OpenAICompatTransport:
    """Any OpenAI-compatible endpoint, e.g. Groq's free tier: HQ_OPENAI_BASE=https://api.groq.com/openai/v1"""
    name = "openai-compatible"

    def __init__(self):
        self.base = os.environ["HQ_OPENAI_BASE"].rstrip("/")
        self.key = os.environ["HQ_OPENAI_KEY"]
        self.model = os.environ.get("HQ_MODEL", "qwen/qwen3.8-27b")

    def complete(self, system, messages):
        payload = {"model": self.model, "temperature": 0, "messages": [{"role": "system", "content": system}] + messages}
        if os.environ.get("HQ_JSON_MODE", "1") != "0":
            payload["response_format"] = {"type": "json_object"}  # keeps the model inside the one-JSON-object protocol
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self.base + "/chat/completions", body,
                                     {"Authorization": "Bearer " + self.key, "content-type": "application/json", "User-Agent": "cutoff-hackathon/1.0"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    text = json.load(r)["choices"][0]["message"]["content"] or ""
                    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                if e.code == 429 and attempt < 4:  # free-tier rate limit: wait as long as the server asks, then retry
                    m = re.search(r"try again in (\d+(?:\.\d+)?)s", body)
                    time.sleep(min(float(m.group(1)) + 1.5 if m else 20, 45))
                    continue
                raise RuntimeError(f"{e.code} from the model endpoint: {body[:300]}")


class CliTransport:
    """The installed `claude` CLI on the user's own login (no API key). HQ_CLAUDE_BIN overrides the path."""
    name = "claude-cli"

    def __init__(self):
        self.bin = os.environ["HQ_CLAUDE_BIN"]
        self.model = os.environ.get("HQ_MODEL", "haiku")

    def complete(self, system, messages):
        convo = "\n\n".join(f"[{m['role'].upper()}]\n{m['content']}" for m in messages)
        prompt = system + "\n\n" + convo + "\n\n[ASSISTANT]\nRespond with exactly one JSON object."
        p = subprocess.run([self.bin, "-p", "--model", self.model, "--output-format", "text", "--tools", ""],
                           input=prompt, capture_output=True, text=True, timeout=180)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip() or p.stdout.strip())
        return p.stdout


class AwaitModel(Exception):
    """Raised by a transport that is waiting for the next model turn (manual driving)."""


class ReplayTransport:
    """Replays model outputs recorded from a real run. Tools still execute live against the data.
    With wait=True it stops with AwaitModel instead of failing once the recording runs out."""
    name = "replay"

    def __init__(self, outputs, model="recorded", wait=False):
        self.outputs = list(outputs)
        self.model = model
        self.wait = wait
        self.i = 0

    def complete(self, system, messages):
        if self.i >= len(self.outputs):
            if self.wait:
                raise AwaitModel()
            raise RuntimeError("replay exhausted: the recorded run has no further model turns")
        out = self.outputs[self.i]
        self.i += 1
        return out


def live_transport():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicTransport()
    if os.environ.get("HQ_OPENAI_BASE") and os.environ.get("HQ_OPENAI_KEY"):
        return OpenAICompatTransport()
    if os.environ.get("HQ_CLAUDE_BIN"):
        return CliTransport()
    return None


# ---- parsing + verification --------------------------------------------------
def parse_json(text):
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        for end in range(len(m.group(0)), 1, -1):
            try:
                return json.loads(m.group(0)[:end])
            except Exception:
                continue
    raise ValueError("model did not return JSON")


def weakest_strength(ids):
    ranks = []
    for i in ids:
        s = PREFIX_STRENGTH.get(i.split("-")[0], "RECORDED")
        ranks.append(STRENGTH_ORDER.index(s))
    return STRENGTH_ORDER[min(ranks)] if ranks else "RECORDED"


def verify_final(final, seen_ids):
    """Drop every fact whose cited ids were not seen in this run. Rank strength from the data, not the model."""
    kept, dropped = [], []
    for k in final.get("known", []) or []:
        ids = [i for i in (k.get("source_ids") or []) if isinstance(i, str)]
        if ids and all(i in seen_ids for i in ids):
            kept.append({"fact": k["fact"], "source_ids": ids, "strength": weakest_strength(ids)})
        else:
            dropped.append({"fact": k.get("fact"), "source_ids": ids,
                            "why": "no source id, or an id that no tool returned in this run"})
    final["known"] = kept
    steps_fixed = 0
    for o in final.get("options", []) or []:
        clean = []
        for s in o.get("steps", []) or []:
            a = s.get("action")
            if a not in ACTIONS:
                s["detail"] = f"[unknown action '{a}' replaced by a hold] " + str(s.get("detail", ""))
                s["action"] = a = "hold_and_verify"
            label, needs = ACTIONS[a]
            s["label"] = label
            s["needs_approval"] = needs
            s["human_only"] = a in HUMAN_ONLY
            if a in ("book_ferry", "cancel_booking"):
                s["reversible"] = False if a == "book_ferry" else s.get("reversible", False)
            clean.append(s)
        verifies = [s for s in clean if s["action"] == "hold_and_verify"]
        rest = [s for s in clean if s["action"] != "hold_and_verify"]
        rest.sort(key=lambda s: not s.get("reversible", False))  # stable: reversible first
        if [s["action"] for s in clean] != [s["action"] for s in verifies + rest]:
            steps_fixed += 1
        o["steps"] = verifies + rest
        for n, s in enumerate(o["steps"]):
            s["id"] = f"{o.get('id', 'X')}{n + 1}"
    return {"claims": len(kept) + len(dropped), "kept": len(kept), "dropped": dropped, "reordered_options": steps_fixed}


# ---- the run -------------------------------------------------------------------
class Run:
    def __init__(self, data, now, order_id, transport, trace_dir):
        self.id = "run-" + uuid.uuid4().hex[:8]
        self.d, self.now, self.order_id, self.tr = data, now, order_id, transport
        self.tools = Tools(data, now)
        self.trace_dir = trace_dir
        self.mode = "replay" if transport.name == "replay" else "live"
        self.model = f"{transport.name}:{getattr(transport, 'model', '')}"
        self.steps, self.final, self.grounding, self.status = [], None, None, "running"
        self.stop = None
        self.calls = 0
        self.max_calls = MAX_TOOL_CALLS
        self.seen = set()
        self.msgs = [{"role": "user", "content": f"Order {order_id}. The queue says it needs a person. Sim clock now: {iso(now)}. Investigate the travel situation and hand over."}]
        self.model_outputs = []
        self.created = time.time()
        self.error = None
        self.pace = 0

    def _system(self):
        return SYSTEM.replace("{tools}", "\n".join(f"- {v}" for v in Tools.SPEC.values()))

    def _step(self, **kw):
        kw["n"] = len(self.steps) + 1
        self.steps.append(kw)
        if self.pace:
            time.sleep(self.pace)

    def advance(self):
        """Run until a final/say or a hard stop."""
        for _ in range(self.max_calls + 4):
            try:
                raw = self.tr.complete(self._system(), self.msgs)
            except AwaitModel:
                self.status = "awaiting_model"
                return
            except Exception as e:  # transport failure is a stop, not a guess
                self.error = f"{type(e).__name__}: {e}"
                self._step(kind="STOP", text=f"Model unavailable: {self.error}")
                self.status = "error"
                return
            self.model_outputs.append(raw)
            try:
                act = parse_json(raw)
            except ValueError:
                self.msgs += [{"role": "assistant", "content": raw}, {"role": "user", "content": "Reply with exactly one JSON object in the stated formats."}]
                self._step(kind="NOTE", text="Model reply was not JSON; asked again.")
                continue
            self.msgs.append({"role": "assistant", "content": json.dumps(act)})
            if "tool" in act:
                if self.calls >= self.max_calls:
                    self.stop = f"Budget used: {self.calls} of {self.max_calls} lookups. Stopping instead of searching further."
                    self._step(kind="STOP", text=self.stop)
                    self.final = {"headline": "Stopped on budget. Findings are listed in the trace.", "stop_reason": self.stop,
                                  "known": [], "options": [], "decision": "A person should take over from the trace.", "needs_from_human": []}
                    self.grounding = {"claims": 0, "kept": 0, "dropped": [], "reordered_options": 0}
                    self.status = "done"
                    return
                self.calls += 1
                res = self.tools.call(act["tool"], act.get("args") or {})
                self.seen |= collect_ids(res)
                self._step(kind="TOOL", tool=act["tool"], args=act.get("args") or {}, why=act.get("why", ""), result=res,
                           cost="lookup, no approval needed")
                text = json.dumps(res)
                self.msgs.append({"role": "user", "content": "TOOL RESULT " + act["tool"] + ": " + text[:3000]})
                if act["tool"] == "message_driver" and res.get("recognised") is False:
                    self.msgs.append({"role": "user", "content": "The simulated driver did not recognise that. Do not improvise: stop and hand over."})
                continue
            if "say" in act:
                self._step(kind="SAY", text=act["say"])
                self.status = "done" if self.final else self.status
                return
            if "final" in act:
                f = act["final"]
                self.grounding = verify_final(f, self.seen)
                self.final = f
                self.stop = f.get("stop_reason")
                self._step(kind="FINAL", text=f.get("headline", ""),
                           grounding=f"{self.grounding['kept']} of {self.grounding['claims']} claims grounded, {len(self.grounding['dropped'])} dropped")
                self.status = "done"
                return
            self.msgs.append({"role": "user", "content": "Unrecognised object. Use tool, say or final."})
        self.stop = "Loop limit reached without a handover."
        self._step(kind="STOP", text=self.stop)
        self.status = "error"

    def say(self, text):
        """A person talks to the agent. It may look more things up (a few extra calls) or answer."""
        self._step(kind="USER", text=text)
        self.msgs.append({"role": "user", "content": "PERSON ASKS: " + text + "\nAnswer with {\"say\": ...} or look something up first. If this changes the plan, send a new {\"final\": ...}."})
        self.max_calls = self.calls + CHAT_EXTRA_CALLS
        self.status = "running"
        self.advance()

    def save(self):
        os.makedirs(self.trace_dir, exist_ok=True)
        p = os.path.join(self.trace_dir, self.id + ".json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.export(full=True), f, indent=1, ensure_ascii=False)
        return p

    def export(self, full=False):
        out = {"id": self.id, "order_id": self.order_id, "mode": self.mode, "model": self.model, "status": self.status,
               "now": iso(self.now), "calls": self.calls, "max_calls": self.max_calls, "steps": self.steps, "final": self.final,
               "grounding": self.grounding, "stop_reason": self.stop, "error": self.error}
        if full:
            out["model_outputs"] = self.model_outputs
        return out
