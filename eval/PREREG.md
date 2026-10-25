# Replay eval: pre-registration

Written and committed BEFORE the first run of `python -m eval.replay`. Nothing below was tuned on the result.

## Question

If one person works the queue top to bottom, does tier-first ordering reach the orders with a real
clock before that clock runs out, compared with the severity-sorted board a team would normally have?

## Setup (fixed)

- Clock: the packet snapshot, `2026-10-21T09:00:00Z`. Nothing after it is used.
- Orders: every order that is not delivered at the snapshot.
- "Needs a human": built from event evidence only (open alert, unresolved incident, open work item,
  pending or failed agent proposal, thread waiting on ops, window already missed, tracking ETA past the
  delivery window). It does NOT use the README's "case history" label. Agreement with that label is
  reported as a secondary, heuristic number.
- "Real clock" of an order: the earliest clock in tiers PROMISE, CUTOFF, SLOT, OPTION that is still in
  the future at the snapshot. REMINDER clocks (work-item due_at) are soft and are never counted.
- Orders without a real clock cannot be missed under this metric. They are reported, not scored.
- One person, one order at a time, `L` minutes per order, so the k-th order in a queue is reached at `k * L`
  minutes (the first at 0). An order is MISSED if it is reached after its real clock.
- Orderings compared:
  1. `severity`: highest alert/work-item severity first, ties by oldest evidence (a normal board).
  2. `tier-first`: the Honest Queue order (tier, then time left inside the tier, then severity).
  3. `soonest`: earliest real clock first regardless of tier (a pure deadline sort, as a control).
- Human latency `L`: 2, 5, 10, 20, 30, 60, 120 minutes. No other values are tried.

## What counts as a result

The full table over all `L`, whichever way it goes. If tier-first does not beat `severity`, or loses to
`soonest`, that is reported as is. Tier-first exists to be honest about clock strength, not to win a race,
so losing to `soonest` on this metric would not be a surprise.

## Ledger audit (the "didn't bother you" list)

Independent of the rules that build the queue: a booked ferry past its cut-off with no gate confirmation,
a rejected proof, in-transit tracking last received more than 3 hours before the snapshot. Every hidden
order is checked by these. Plus a seeded random sample of 10 hidden orders (seed 7) read in full. Reported
as a miss count, not a rate with false precision.

## Known limits (written in advance)

- The packet is a synthetic design sample. Counts here say nothing about real frequency.
- Only a few orders carry a real clock inside the first hours after the snapshot, so differences between
  orderings can be small. The table shows that rather than hiding it.
