# Cutoff

A control room for Bahn operations. It shows only the orders that need a person, ranks them by **how real their deadline is**, and puts one travel-disruption agent next to each one. The agent looks things up, cites every fact, and stops when the data cannot support a decision.

Named for the ferry check-in cut-off, the one deadline in the data that is truly hard. Built for the Bahn challenge, "The Car Delivery Control Room" (Prompt Operations Hackathon, Helsinki, 24-25 Oct 2026). All data is Bahn's fictional packet.

## The idea in four lines

1. Every deadline is **typed by how strongly the data states it**: customer promise, carrier cut-off on a *booked* ferry, planned slot, cut-off on an *unbooked quote*, soft reminder. The queue sorts by tier first. Time is only compared inside a tier. A work-item reminder is never promoted to a point of no return.
2. "Needs a person" comes from **events** (open alert, unresolved incident, open work item, pending or failed proposal, a thread waiting on ops, a window already missed, a tracking ETA past the delivery window), not from a label.
3. The agent is a language model in a tool loop with a **budget, a grounding verifier and a stop reason**. It cannot book, cancel, change a date or contact a customer. A quote is not a booking, and a timed-out booking is *unknown* until reconciled.
4. Everything it hands over has a source. A fact whose cited id no tool returned in that run is **dropped and shown as dropped**. Strength labels (signal, quote, reported, booked-unconfirmed, recorded) come from the data's own state, not from the model.

## Run it (no dependencies, Python 3.10+)

```bash
git clone <this repo> && cd cutoff
python -m hq.server            # http://localhost:8750
python -m unittest tests.test_hq   # 20 tests
python -m eval.replay          # the replay eval (pre-registered in eval/PREREG.md)
```

No Docker, no Postgres, no npm. The packet's JSONL files are in `data/` and loaded directly. Bahn's optional Next.js starter is not used.

Tour: press **Brief me out loud**, or open `CD-420603` (the only booked ferry about to close check-in) or `CD-420299` (sold-out sailing, timed-out booking, customer must approve a date change), press **Ask the agent to investigate**, then approve steps. Drag the **"What if it were later?"** slider forward and watch the queue change. "Left alone" lists what needed nobody, with reasons. "Proof" is the eval.

## Settings

The **Settings** button (top right) holds preferences saved in your browser: theme (auto, light, dark), text size, density, car images on or off, reduced motion, and the voice (speak or captions only, speed, which voice). There are no nested scroll boxes: the page scrolls as one.

## Voice

**Brief me** speaks a short briefing, walks the most urgent orders, asks "Shall I open it, or go to the next?", and reads the agent's handover aloud. It tries, in order:

1. **Studio clips**: recorded audio in `web/audio/` (named by the hash of the text). Works for anyone, with no key, in any browser.
2. **Generate on demand** through a text-to-speech endpoint if `.env` has a key (`HQ_OPENAI_KEY`, `HQ_TTS_MODEL`, `HQ_TTS_VOICE`; Groq works). New clips are cached into `web/audio/`.
3. **The browser's own voices** as a last resort (Settings lets you pick one).

To record every clip once: with the key in `.env`, start the server, open the page, and run `await prewarmVoice()` in the browser console. Then commit `web/audio/`.

Captions and buttons always show. Listening for "yes / next / stop" uses the browser's speech recognition (Chrome or Edge, microphone permission).

## Demo video (self-rendering)

`web/video/demo.html` is a self-playing, narrated, captioned tour of the live app that renders its own MP4. With the server running (`python -m hq.server`), open `http://localhost:8750/video/demo.html` in Microsoft Edge, press F11, click **Render MP4**, and in the share dialog choose **Entire screen** and tick **Share system audio**. Pass 1 records the browser's neural voice in real time (92 s). Pass 2 renders the picture frame by frame and muxes both into one MP4, which downloads when done. **Preview** plays it without recording. The recorder (`rec.js`) is reused from an earlier hackathon entry and needs the GSAP and mp4-muxer scripts from a CDN.

Not tested here: the render itself (it needs the share dialog and a visible Edge window). I verified the page loads, the live app is ready in its frame, and stepping through the whole timeline fires every scripted action with no errors.

## About the agent runs (read this)

- **Recorded runs** (`traces/ord-00154|00299|00603.json`) were produced by a **real model** (Claude Haiku) driving this exact tool loop. The tools executed live against the data for every turn. They were recorded through `hq/manual.py`, a transport where a Claude Code subagent supplies each JSON turn, because the installed Claude CLI's login had expired when I built this. The `model` field in each trace says so.
- **Replay** re-executes the recorded model turns; the tools, the verifier and the approval logic all run live again. It cannot answer new questions, so in recorded mode the chat box answers only from the trace and says so.
- **Live mode, quick setup (free Groq key):** copy `.env.example` to `.env`, paste the key after `HQ_OPENAI_KEY=`, restart `python -m hq.server`. The "Run live" button turns on. `.env` is git-ignored.
- **Live mode** is implemented for three backends and **has not been exercised end to end with a real key**: `ANTHROPIC_API_KEY`; or `HQ_OPENAI_BASE` + `HQ_OPENAI_KEY` (any OpenAI-compatible endpoint, for example a free Groq key); or `HQ_CLAUDE_BIN` (path to a logged-in `claude`). `HQ_MODEL` picks the model. Without one the "Run live" button is disabled and says why.
- The **driver is scripted** (a keyword reply tree), labelled SIMULATED everywhere, and says "unrecognised, stop" off script.
- Approvals are a **mock executor**: nothing real is booked. Failed steps show the compensation plan; reversible steps can be undone; steps that need the customer are handed to a person and later steps stay locked until a person confirms.

## The eval, and what it found

`python -m eval.replay` replays the 77 active orders from the 21 Oct 09:00Z snapshot with one person working the queue. The method was written in `eval/PREREG.md` before the run.

| minutes per order | severity board | tier-first (this tool) | soonest deadline (control) |
|---|---|---|---|
| 2 / 5 / 10 | 0 | 0 | 0 |
| 20 | 3 | 0 | 0 |
| 30 | 5 | 0 | 0 |
| 60 | 5 | 3 | 1 |
| 120 | 8 | 5 | 4 |

Counts are real clocks missed (18 of the 22 queued orders have one). Tier-first beats a severity board when a person needs 20+ minutes an order. **A plain soonest-deadline sort beats tier-first when people are slow**, because tier-first puts honesty about clock strength before raw speed. Nothing is missed at 2-10 minutes. The packet is a synthetic sample: this says nothing about real frequency.

The hidden list was audited: 3 of 55 hidden orders trip a signal the rules do not use (no or stale tracking) and are shown as WATCH. Two things the audit and reading 17 distinct hidden orders caught and fixed: an order whose tracking ETA lands after its delivery window (`ord-00439`) and two cancelled orders that still carry an active status.

## What it deliberately does not do

- Only **3 of 351** time windows carry a `commitment_deadline_at`, and none of those orders needs a person at the snapshot, so the queue's top is carrier cut-offs and planned slots, not customer promises. The tool says so in its header instead of inventing urgency.
- 38 submitted expenses across 30 orders are open. That is back-office work, not a delivery decision; it is not in this queue.
- Customer contact, date changes and real bookings are out of the agent's reach by construction.
- One agent area: travel disruption on ferry orders. Pickup phone calls, on-the-road breakdowns and driver support are not built.
- The UI was checked in one browser pane (desktop width). Phone width was not tested.

## Layout

```
hq/clocks.py   tiers, evidence, tier-first ranking          hq/agent.py   tool loop, budget, verifier, transports
hq/tools.py    read-only lookups + scripted driver           hq/server.py  stdlib server, mock executor
hq/manual.py   drive a run turn by turn (used to record)     web/          the control room (no build step)
eval/          PREREG.md, replay.py, results.json            tests/        20 tests    traces/  recorded runs
data/          Bahn's fictional packet (JSONL) + its README and SCHEMA
```

Car illustrations in `web/cars/` are Bahn's own generated model images from the challenge starter (see `web/cars/ASSET-SOURCES.md`); each shows a model, not the specific car. Data is Bahn Express's fictional challenge packet (github.com/Bahn-Express/car-delivery-control-room).
