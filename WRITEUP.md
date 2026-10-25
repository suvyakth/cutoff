# Cutoff: submission write-up

**Bahn challenge: The Car Delivery Control Room.** Repo: _(public GitHub link goes here)_. Demo video: _(link goes here)_.

## What it is

A control room that shows only the 22 of 77 active orders that need a person and ranks them by **how real their deadline is**, with one travel-disruption agent that looks things up, cites every fact, and stops when the data cannot support a decision.

Most boards sort by severity. The packet taught me why that misleads: it states very few real clocks. Only 3 of 351 time windows carry a customer `commitment_deadline_at`. The strong clocks are carrier check-in cut-offs on *booked* ferries and, weaker, on *unbooked quotes*. So every deadline is typed (customer promise, carrier cut-off, planned slot, option expiry, soft reminder), the queue sorts by tier and only then by time, and a work-item reminder is never promoted to a point of no return. If nothing states a clock, the card says so.

## Voice

A persistent dock at the bottom of the page with an animated particle orb. Tap it and talk: "what needs me first", "next", "open it", "why did you stop", "what's the decision", "ferry", "in six hours". The briefing summarises ("twenty-two orders need a person, but only two are really against the clock") instead of reading headlines; anything else goes to the agent. It will not approve or book by voice. It minimizes to a small orb at the bottom-left. Output uses recorded studio clips if present, a speech endpoint if a key is set, or the browser's own voice. Captions and suggestion chips always show. Not tested: microphone recognition and real audio (the test pane blocks the mic); studio clips were not recorded because the free speech quota ran out.

## The control room

- **Only what needs a person.** "Needs a person" comes from events: open alert, unresolved incident, open work item, pending or failed proposal, a thread waiting on ops, a missed window, a tracking ETA past the delivery window. Not from a label. 55 other orders are under "Left alone" with a reason each, and 3 of them carry a WATCH note.
- **Three zones per order:** why you (evidence and clocks, each with its source), what is known (strength shown: signal, quote, reported, booked-unconfirmed, recorded), decide (options with cost, ETA, downside, per-step approval, compensation plan).
- **A "What if it were later?" slider.** Drag it and slots expire, windows are missed, the queue changes.
- **After a decision:** steps run reversible-first, one approval each. Steps that need the customer are handed to a person and later steps stay locked until a person confirms. A failed step shows the compensation plan; finished reversible steps can be undone. The executor is a mock and says MOCK everywhere.

## The agent

A language model in a tool loop (read order, read messages, read travel, read assignment, verify booking outcome, check timing, message the simulated driver). It cannot book, cancel, change a date or contact a customer. It has an 8-lookup budget, always states why it stopped, and passes a verifier: every fact must cite ids that a tool actually returned in that run, or it is dropped and shown as dropped. Fact strength is derived from the data's state, not from the model.

What it did on real orders (recorded runs, model: Claude Haiku):
- **ord-00299:** overnight ferry sold out, later sailing is quote-only (EUR 387), and an earlier booking attempt timed out with no reference. It refused to propose a retry before reconciling, found the later sailing still reaches the delivery window with 45 hours of slack (so the customer date may not need to move), and handed over a plan with a person asking the customer.
- **ord-00603:** the ferry is booked and check-in closes in 3 hours. The packet has no live terminal ETA, so the agent stopped and said exactly that instead of guessing.
- **ord-00154:** sold out, quotes only, driver positioning unpriced. It stopped for a human decision and listed what it could not price.

## The honest results

I pre-registered an eval (`eval/PREREG.md`) before running it: replay the snapshot with one person working the queue at 2 to 120 minutes per order. Tier-first beat a severity board once people need 20+ minutes an order (0 vs 3 missed at 20 min, 0 vs 5 at 30, 3 vs 5 at 60, 5 vs 8 at 120). **A plain soonest-deadline sort beat tier-first when people are slow** (1 vs 3 at 60 min, 4 vs 5 at 120), because tier-first puts honesty about clock strength before speed. Nothing is missed at 2 to 10 minutes. I report the table as it is. The packet is synthetic, so it says nothing about real frequency.

## How I built it with coding agents

I used Claude Code (Sonnet 5.5) for everything and Claude Haiku subagents in two roles.

**Idea, argued four rounds.** I wrote the idea and a Haiku critic attacked it. The first version, "Ripple" (a causal graph per order), died on the critic's point that a graph cannot show a queue of hundreds. The second, a deadline-sorted queue with earned autonomy, lost the autonomy feature (a standing spending cap reads as the agent approving its own spend, and it cannot be shown in 3 minutes). The third claimed to *extract deadlines from the documents*. Before building I read the real packet: the attachments are CMR PDFs, receipts and photos, not timetables, so I threw that claim away. The structured fields it did have (`check_in_cutoff_at` on reservations and quotes) saved the core idea in a narrower, truer form.

**What I checked instead of trusting.** WebFetch summaries gave me counts; I recounted with scripts. Things checking caught:
- My first rules hid `ord-00439`, whose latest tracking ETA lands 43 minutes after its delivery window closes with no alert raised. A ledger audit found it. I added the rule and a test.
- The model passed a reservation id to `check_timing`, which only accepted quote ids and returned "not found." I fixed the tool and **re-recorded that run** so the trace matches the code.
- Reading a seeded sample of hidden orders showed two cancelled orders still carrying an active status. Fixed, tested, the sample redrawn and read again.
- One of my own UI sentences said a sample was "read in full" before I had read it. I read it, then kept the sentence.

**What I threw away:** the graph, earned autonomy, document-extracted deadlines, the headline "N of 100 orders carry a deadline", and Bahn's Next.js + Postgres + Docker starter (this machine is a managed school laptop; the whole thing runs on the Python standard library instead).

## Limits, plainly

- Live mode (Anthropic key, any OpenAI-compatible key such as Groq, or a logged-in `claude`) is implemented but **not exercised end to end**. The recorded runs were produced by a real model through `hq/manual.py`, a transport where a Claude Code subagent supplies each turn, because the Claude CLI login had expired. The traces say so.
- One agent area only (travel disruption). The driver is a scripted reply tree. Bookings are a mock executor.
- The UI was checked at desktop width in one browser pane.
- I did not read Bahn's slide deck (it is images and the PDF renderer is not installed).
- 38 submitted expenses are open across 30 orders. That is back-office work, not a delivery decision, so it is not queued.

## Demo video (2:30)

1. **0:00** One line: most boards sort by severity; this one sorts by how real the deadline is, and says when there is none. Press **Brief me out loud** and let it talk for ten seconds.
2. **0:15** The header ("22 need a person; 2 carrier cut-offs, 16 planned slots, 4 soft reminders") and the grouped list. Drag the "What if it were later?" slider forward once.
3. **0:40** Open CD-420603, press Ask the agent. Narrate the trace, then the **honest stop**: "the data cannot tell whether the driver makes check-in."
4. **1:10** Open CD-420299. The timed-out booking, "verify before any retry," the 45-hour slack finding, the facts with source chips and strengths. Approve steps in order, hand the customer step to a person, confirm it, then tick "make the next step fail" and show the compensation plan and Undo.
5. **1:55** "Proof": show the table including where tier-first loses, and the audit that found `ord-00439`.
6. **2:15** How it was built: the four-round argument, the packet reading that killed an idea, the bugs checking caught. Show a trace file.
