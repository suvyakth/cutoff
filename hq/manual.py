"""Drive the agent loop turn by turn from a terminal. A model (or a person) supplies each JSON turn.

  python -I -m hq.manual start ord-00299 <session-file>
  python -I -m hq.manual reply <session-file> '<one JSON object>'

Each call rebuilds the run from the recorded model turns (tools are deterministic), prints the tool
result to answer, and writes traces/<session>.json when the run reaches a final. Used to record real
model runs when no API key is configured. The trace says so in its `model` field.
"""
import json
import os
import sys

from .agent import ReplayTransport, Run
from .data import Data, ROOT

TRACES = os.path.join(ROOT, "traces")


def build(path):
    s = json.load(open(path, encoding="utf-8"))
    d = Data()
    tr = ReplayTransport(s["outputs"], model=s["label"], wait=True)
    run = Run(d, d.snapshot, s["order_id"], tr, TRACES)
    run.mode = "recorded"
    run.model = s["label"]
    run.advance()
    return s, run


def show(run):
    if run.status == "awaiting_model":
        last = run.msgs[-1]["content"]
        print(last)
        print(f"\n[lookups used {run.calls}/{run.max_calls}] Reply with exactly one JSON object.")
    else:
        print("RUN FINISHED:", run.status)
        print(json.dumps(run.export()["final"], indent=1, ensure_ascii=False)[:3000])
        print("grounding:", json.dumps(run.grounding)[:600])


def main():
    cmd = sys.argv[1]
    if cmd == "start":
        order, path = sys.argv[2], sys.argv[3]
        label = sys.argv[4] if len(sys.argv) > 4 else "claude-haiku via manual transport (Claude Code subagent)"
        json.dump({"order_id": order, "label": label, "outputs": []}, open(path, "w", encoding="utf-8"))
        s, run = build(path)
        print("SYSTEM PROMPT:\n" + run._system() + "\n\nFIRST MESSAGE:\n" + run.msgs[0]["content"])
        print(f"\n[lookups used 0/{run.max_calls}] Reply with exactly one JSON object.")
    elif cmd == "reply":
        path, raw = sys.argv[2], sys.argv[3]
        s = json.load(open(path, encoding="utf-8"))
        s["outputs"].append(raw)
        json.dump(s, open(path, "w", encoding="utf-8"))
        s, run = build(path)
        show(run)
        if run.status in ("done", "error"):
            run.id = os.path.splitext(os.path.basename(path))[0]
            print("trace:", run.save())


if __name__ == "__main__":
    main()
