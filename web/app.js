"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const TIER_COLOR = { PROMISE: "var(--t-PROMISE)", CUTOFF: "var(--t-CUTOFF)", SLOT: "var(--t-SLOT)", OPTION: "var(--t-OPTION)", REMINDER: "var(--t-REMINDER)", NONE: "var(--t-NONE)" };
const TIER = {
  PROMISE: ["Customer promise", "The customer agreed to this deadline."],
  CUTOFF: ["Ferry check-in closing", "Check-in closes on a ferry that is booked."],
  SLOT: ["Slot running out", "A planned pickup slot or delivery window. Planned, not promised."],
  OPTION: ["Quote that can expire", "A sailing that is only quoted, not booked."],
  REMINDER: ["Soft reminder", "A to-do due time. Not a real deadline, so it never jumps the queue."],
  NONE: ["No deadline in the data", "Nothing states a clock for these."],
};
const STRENGTH = {
  SIGNAL_ONLY: ["Alert only", "An alert is a signal to look, not a diagnosis."], QUOTE_ONLY: ["Quote, not booked", "A quote, or a number built on one. Not a booking."],
  SIMULATED: ["Simulated", "Said by the scripted simulated driver. Not real."], REPORTED_UNVERIFIED: ["Reported, unchecked", "A person said so. Nobody has checked."],
  BOOKED_UNCONFIRMED: ["Booked, not confirmed", "Booked, but check-in or boarding is not confirmed."], RECORDED: ["On record", "A recorded fact in the packet."],
};
const SPK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4z" fill="currentColor"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/></svg>';
const SNAP = Date.parse("2026-10-21T09:00:00Z");
let S = { tab: "all", sel: null, hours: 0, q: null, glance: null, eval: null, info: {}, order: null, run: null, poll: null };

const nowIso = () => new Date(SNAP + S.hours * 3600e3).toISOString().replace(".000Z", "Z");
const api = async (p, body) => {
  const r = await fetch(p, body ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || r.statusText), { status: r.status });
  return j;
};
function left(sec) {
  const a = Math.abs(sec), d = Math.floor(a / 86400), h = Math.floor((a % 86400) / 3600), m = Math.floor((a % 3600) / 60);
  const t = d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
  return sec < 0 ? `${t} overdue` : `${t} left`;
}
function spokenLeft(sec) {
  const a = Math.abs(sec), d = Math.floor(a / 86400), h = Math.floor((a % 86400) / 3600), m = Math.floor((a % 3600) / 60);
  const p = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const t = d ? `${p(d, "day")} ${p(h, "hour")}` : h ? (m ? `${p(h, "hour")} ${p(m, "minute")}` : p(h, "hour")) : p(m, "minute");
  return sec < 0 ? `already overdue by ${t}` : `${t} left`;
}
function local(at, tz) {
  try { return new Date(at).toLocaleString("en-GB", { timeZone: tz, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZoneName: "short" }); } catch { return at; }
}
const ago = (at) => { const s = (Date.parse(nowIso()) - Date.parse(at)) / 1000; return s < 0 ? "in the future" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; };
const src = (s) => `<span class="src" title="${esc(s.table)} · ${esc(s.id)}${s.field ? " · " + esc(s.field) : ""}">${esc(s.table)}</span>`;
const idChip = (id) => `<span class="src" title="${esc(id)}">${esc(id.length > 20 ? id.slice(0, 19) + "…" : id)}</span>`;
const pretty = (s) => String(s).replaceAll("_", " ").toLowerCase();
const route = (c) => (c.from_city && c.to_city ? `${esc(c.from_city)} → ${esc(c.to_city)}` : "");
const img = (c, eager) => c.image ? `<img src="cars/${esc(c.image)}.webp" alt=""${eager ? "" : ' loading="lazy"'}>` : `<img alt="">`;
const speech = (t) => String(t).replace(/\b[A-Z]{2,4}-\d{4,}\b/g, "").replace(/\b(?:before|at|by|on|after|until)\s+\d{4}-\d{2}-\d{2}T[\d:]+Z\b/g, "").replace(/\b\d{4}-\d{2}-\d{2}T[\d:]+Z\b/g, "").replace(/\bfor\s+([;,.:])/g, "$1").replace(/\s+([;,.:])/g, "$1").replace(/\s+/g, " ").trim();

/* ---------- top of the page: the 10-second view ---------- */
function renderHero() {
  const g = S.glance, t = g.top, a = g.agent, n = g.counts.queue;
  if (!t) { $("#hero").innerHTML = `<div class="wrap"><h1 class="hero-h">Nothing needs you.</h1></div>`; return; }
  const k = t.clock;
  const first = k ? `<em>${left(k.seconds_left).replace(" left", "")}</em> until the ${t.tier === "CUTOFF" ? "first ferry closes check-in" : "first hard deadline"}.` : "";
  $("#hero").innerHTML = `<div class="wrap hgrid"><div>
      <div class="eyebrow">Bahn · car delivery control room</div>
      <h1 class="hero-h">${n} orders need you. ${first}</h1>
      <p class="hero-p">Cutoff ranks every order by <b>how real its deadline is</b>, and an agent tells you plainly when it <b>can't tell</b>.</p>
      <div class="bigbtns"><button class="cta" id="brief">${SPK}Brief me</button><button class="cta alt" id="opentop">Open the top order →</button></div>
      
    </div>
    <div class="topcard">${img(t, true)}<div class="tb">
      <div class="tc-top"><div><div class="tc-car">${esc(t.car)}</div><div class="tc-route">${route(t)} · ${esc(t.reference)}</div></div>${k ? `<span class="count" style="--c:${TIER_COLOR[t.tier]}">${left(k.seconds_left)}</span>` : ""}</div>
      <p class="tc-what">${esc(t.headline)}</p>
      ${a ? `<div class="quote"><b>The agent looked, then said</b><p>${esc(a.stop_reason)}</p><div class="meta">${a.kept} of ${a.claims} claims checked against the data · ${a.lookups} lookups · it booked nothing</div></div><button class="say" id="readhero">${SPK} Read it to me</button>` : ""}
    </div></div></div>`;
  $("#brief").onclick = () => startTalk(true);
  $("#opentop").onclick = () => { openOrder(t.order_id); $("#work").scrollIntoView(); };
  const rh = $("#readhero"); if (rh) rh.onclick = () => { dockOn(true); speak(T.hero()); };
}
function renderDiff() {
  const g = S.glance, c = g.counts, tt = c.by_tier, a = g.agent, e = S.eval;
  const lg = (k) => tt[k] ? `<div class="lg" style="--c:${TIER_COLOR[k]}"><i>${tt[k]}</i> ${esc(TIER[k][0])}</div>` : "";
  const noPromise = !tt.PROMISE ? `<div class="lg" style="--c:${TIER_COLOR.PROMISE}"><i>0</i> Customer promise <span class="f">none open, so none shown</span></div>` : "";
  const row = (L) => { const r = (e.table || []).find((x) => x.latency_min === L); if (!r) return ""; const lose = r.tier_first > r.soonest; return `<tr><td>${L} min</td><td>${r.severity}</td><td class="us">${r.tier_first}</td><td class="${lose ? "lose" : ""}">${r.soonest}</td></tr>`; };
  $("#diff").innerHTML = `<div class="wrap diffgrid">
    <div class="dcard" style="--c:var(--cutoff)"><div class="n">Not another timer</div><h3>Every deadline is graded by how real it is</h3>
      <p>A ferry check-in on a booked ticket is real. A to-do reminder is not. They never share a rank.</p>
      <div class="legend">${lg("CUTOFF")}${lg("SLOT")}${lg("OPTION")}${lg("REMINDER")}${noPromise}</div></div>
    <div class="dcard" style="--c:var(--slot)"><div class="n">An agent that admits it</div><h3>"The data can't tell" is a valid answer</h3>
      ${a ? `<div class="bigstat">${a.kept} of ${a.claims}</div><p>claims checked against what its tools returned. Anything it can't source is dropped and shown. It cannot book, cancel or contact a customer.</p>` : "<p>The agent checks every claim against its tools.</p>"}</div>
    <div class="dcard" style="--c:var(--ok)"><div class="n">Proof, including where we lose</div><h3>Deadlines missed, one person working the queue</h3>
      <table class="mini"><tr><th>Time per order</th><th>Severity board</th><th>Cutoff</th><th>Soonest first</th></tr>${row(30)}${row(120)}</table>
      <p style="margin-top:10px">We beat a severity board. A plain "soonest first" sort beats us when people are slow, and we say so.</p></div></div>`;
}

/* ---------- the queue (required) ---------- */
async function loadQueue() {
  S.q = await api("/api/queue?now=" + encodeURIComponent(nowIso()));
  renderSummary(); renderList();
}
function renderSummary() {
  const c = S.q.counts, t = c.by_tier;
  const tab = (key, label, n, color) => n || key === "all" ? `<button class="tab ${S.tab === key ? "on" : ""}" data-tab="${key}" style="--c:${color}" title="${esc(TIER[key] ? TIER[key][1] : "")}">${esc(label)} <b>${n}</b></button>` : "";
  $("#summary").innerHTML = `<div class="tabs" role="tablist">${tab("all", "All that need you", c.queue, "var(--ink)")}${["PROMISE", "CUTOFF", "OPTION", "SLOT", "REMINDER", "NONE"].map((k) => tab(k, TIER[k][0], t[k], TIER_COLOR[k])).join("")}${tab("left", "Left alone", c.ledger, "var(--mute)")}</div>
    <div class="note">${S.tab === "left" ? "Nothing open on these. Each one says what was checked." : t.PROMISE ? "" : "No customer-promise deadline is open right now, and this tool will not invent one."}</div>`;
  document.querySelectorAll("#summary .tab").forEach((b) => b.onclick = () => { S.tab = b.dataset.tab; renderSummary(); renderList(); $("#list").scrollTop = 0; });
}
function row(c, text) {
  const k = c.clock;
  const when = k ? `<span class="count sm" style="--c:${TIER_COLOR[c.tier]}">${left(k.seconds_left)}</span>` : `<span class="note">no deadline</span>`;
  return `<button class="row ${S.sel === c.order_id ? "sel" : ""}" data-id="${c.order_id}" style="--c:${TIER_COLOR[c.tier]}">
    ${img(c)}
    <div><div class="car">${esc(c.car)}</div><div class="route">${route(c)} · ${esc(c.reference)}</div></div><div>${when}</div>
    <div class="why">${esc(text || c.headline)}</div>
    <div class="meta">${esc(pretty(c.status))}${c.has_trace ? " · the agent has looked at this" : ""}</div></button>`;
}
function renderList() {
  let html = "", last = null;
  if (S.tab === "left") {
    html = S.q.ledger.map((c) => row(c, c.ledger_reason + (c.watch.length ? " WATCH: " + c.watch.join(" ") : ""))).join("");
  } else {
    const list = S.tab === "all" ? S.q.queue : S.q.queue.filter((c) => c.tier === S.tab);
    for (const c of list) {
      if (S.tab === "all" && c.tier !== last) { html += `<div class="grp" style="--c:${TIER_COLOR[c.tier]}"><b>${esc(TIER[c.tier][0])}</b><small>${esc(TIER[c.tier][1])}</small></div>`; last = c.tier; }
      html += row(c);
    }
    if (!list.length) html = `<div class="empty">Nothing in this group right now.</div>`;
  }
  $("#list").innerHTML = html;
  document.querySelectorAll("#list .row").forEach((b) => b.onclick = () => openOrder(b.dataset.id));
}
async function openOrder(id) {
  S.sel = id; S.run = null; clearInterval(S.poll);
  document.querySelectorAll("#list .row").forEach((b) => b.classList.toggle("sel", b.dataset.id === id));
  S.order = await api(`/api/order/${id}?now=${encodeURIComponent(nowIso())}`);
  renderOrder();
}
function renderOrder() {
  const o = S.order, c = o.card, run = S.run, k = c.clock;
  const ORDER = ["PROMISE", "CUTOFF", "SLOT", "OPTION", "REMINDER"];
  const real = c.clocks.filter((x) => ["PROMISE", "CUTOFF", "SLOT", "OPTION"].includes(x.tier));
  const clocks = c.clocks.length ? [...c.clocks].sort((a, b) => ORDER.indexOf(a.tier) - ORDER.indexOf(b.tier) || a.seconds_left - b.seconds_left)
    .map((x) => `<div class="clk" style="--c:${TIER_COLOR[x.tier]}"><div class="t">${left(x.seconds_left)}</div><div><b>${esc(TIER[x.tier][0])}</b> · ${esc(x.label)}<div class="d">${esc(local(x.at, x.tz))} · ${esc(x.basis)} ${src(x.source)}</div></div></div>`).join("")
    : `<div class="note">The data states no deadline for this order, so none is shown. The tool does not invent urgency.</div>`;
  const why = c.evidence.length ? c.evidence.map((e) => `<div class="fact"><span class="badge">${esc(pretty(e.kind))}</span>${esc(e.text)} ${src(e.source)}<span class="age">${ago(e.at)}</span></div>`).join("")
    : `<div class="note">${esc(c.ledger_reason || "")}</div>${(c.watch || []).map((w) => `<div class="warnbox">WATCH: ${esc(w)}</div>`).join("")}`;
  const chips = [route(c), c.customer && "for " + esc(c.customer), c.driver ? "driver " + esc(c.driver) : "no driver yet", esc(pretty(c.status)), esc(c.reference)].filter(Boolean).map((x) => `<span class="chip">${x}</span>`).join("");
  $("#detail").innerHTML = `
    <div class="hero2">${img(c, true)}<div class="cap">Illustration of the model, not a photo of this car.</div>
      <div class="body"><h3 class="d-car">${esc(c.car)}</h3><div class="chips">${chips}</div><p class="headline">${esc(c.headline)}</p>
      ${k ? `<span class="count" style="--c:${TIER_COLOR[c.tier]}">${left(k.seconds_left)}</span> <span class="note"><b>${esc(TIER[c.tier][0])}</b> · ${esc(k.label)}</span>` : ""}</div></div>
    <div class="zone"><h3><span class="step-n">1</span>Why this needs you</h3>${why}
      <h3 style="margin-top:20px;font-size:21px">Deadlines, strongest first</h3><div class="clocks">${clocks}</div>
      ${!real.length && c.clocks.length ? `<div class="warnbox">Only soft reminders here. A work-item due time is a to-do, not a promise, so it is not treated as a point of no return.</div>` : ""}</div>
    <div class="zone"><h3><span class="step-n">2</span>What we know</h3>${knownHtml(o, run)}</div>
    <div class="zone decide"><h3><span class="step-n">3</span>Your decision</h3>${decideHtml(o, run)}</div>`;
  wireDecide();
}
function knownHtml(o, run) {
  if (run && run.final && run.final.known.length) {
    const g = run.grounding || {};
    return run.final.known.map((k) => { const s = STRENGTH[k.strength] || [k.strength, ""]; return `<div class="fact"><span class="badge ${k.strength}" title="${esc(s[1])}">${esc(s[0])}</span>${esc(k.fact)}<div>${k.source_ids.map(idChip).join("")}</div></div>`; }).join("")
      + `<div class="note" style="margin-top:12px">${g.kept} of ${g.claims} claims checked against what the tools returned. ${(g.dropped || []).length ? `<b>${g.dropped.length} dropped</b> because no tool returned the id they cited: ${g.dropped.map((d) => esc((d.fact || "").slice(0, 70))).join("; ")}.` : "None dropped."} The label on each fact comes from the data's own state, not from the model.</div>`;
  }
  const m = o.messages.slice(-6).map((x) => `<div class="msg"><div class="who">${esc(x.from)} · ${esc(x.channel)} · ${esc(x.at.replace("T", " ").slice(0, 16))} UTC</div>${esc(x.body)}</div>`).join("");
  const p = o.proposals.length ? `<h3 style="margin-top:16px;font-size:20px">Earlier agent proposals in the packet</h3>` + o.proposals.map((p) => `<div class="fact"><span class="badge">${esc(pretty(p.status))}</span>${esc(p.summary)}${p.error ? `<div class="note">${esc(p.error)}</div>` : ""}</div>`).join("") : "";
  return (m || `<div class="note">No messages on this order yet. Ask the agent below to look at what the data holds.</div>`) + p;
}
function decideHtml(o, run) {
  const live = S.info.live_model, rec = o.existing_trace;
  let h = "";
  if (!run) {
    h += `<p class="note" style="margin-top:0">The agent looks things up first. It cannot book, cancel, change a date or contact a customer. Every change waits for your yes.</p>`;
    h += `<button class="btn primary" id="runrec" ${rec ? "" : "disabled"}>${rec ? "Ask the agent to investigate" : "The agent has not looked at this order"}</button> `;
    h += `<button class="btn" id="runlive" ${live ? "" : "disabled"} title="${live ? "" : "Needs a model key. See the README."}">Run live${live ? " (" + esc(live) + ")" : " · needs a model key"}</button>`;
    h += `<p class="note">${rec ? "This replays a run recorded from a real model. The tools and checks run again live against the data." : "Only three travel-disruption orders have a recorded run."}</p>`;
    return h;
  }
  h += `<div class="note">Run ${esc(run.id)} · ${esc(run.mode)} · ${esc(run.model)} · ${run.calls} of ${run.max_calls} lookups used</div><div class="trace">`;
  for (const s of run.steps) {
    if (s.kind === "TOOL") h += `<div class="tstep"><span class="k">Looked up</span> ${esc(s.tool.replaceAll("_", " "))}<div class="note">${esc(s.why || "")}</div><code>${esc(JSON.stringify(s.args))}</code></div>`;
    else if (s.kind === "USER") h += `<div class="tstep"><span class="k">You</span> ${esc(s.text)}</div>`;
    else if (s.kind === "SAY") h += `<div class="tstep"><span class="k">Agent</span> ${esc(s.text)}</div>`;
    else if (s.kind === "FINAL") h += `<div class="tstep"><span class="k">Handed over</span> · ${esc(s.grounding)}</div>`;
    else h += `<div class="tstep"><span class="k">${esc(s.kind)}</span> ${esc(s.text)}</div>`;
  }
  h += `</div>`;
  if (run.status === "running") return h + `<div><span class="spin"></span>Working…</div>`;
  if (run.status === "error" && !run.final) return h + `<div class="warnbox">${esc(run.error || run.stop_reason || "The run failed.")}</div>`;
  const f = run.final; if (!f) return h;
  h += `<h3 style="font-size:26px;margin-top:14px">${esc(f.headline)}</h3><button class="btn small" id="readout">${SPK} Read the handover aloud</button>`;
  h += `<div class="warnbox"><b>Why the agent stopped here:</b> ${esc(f.stop_reason)}</div>`;
  h += `<p style="font-size:20px"><b>The decision for you:</b> ${esc(f.decision)}</p>`;
  const ds = run.done_steps || {};
  for (const op of f.options || []) {
    h += `<div class="opt ${op.id === f.recommended ? "rec" : ""}"><b style="font-size:20px">Option ${esc(op.id)}${op.id === f.recommended ? " · the agent suggests this" : ""}</b> · ${esc(op.title)}
      <div class="note">Cost: ${op.cost_eur == null ? "not in the data" : "€" + esc(op.cost_eur)} · ETA: ${esc(op.eta)}</div>
      <div class="note"><b>Downside:</b> ${esc(op.downside)}</div>`;
    for (const s of op.steps) {
      const st = ds[s.id], doneAll = (op.steps.slice(0, op.steps.indexOf(s))).every((x) => ["DONE", "CONFIRMED"].includes((ds[x.id] || {}).status));
      const tag = [s.reversible ? "can be undone" : "cannot be undone", s.human_only ? "a person does this one" : s.needs_approval ? "needs your yes" : "no approval needed"].join(" · ");
      h += `<div class="step"><div>${esc(s.label)}: ${esc(s.detail)}<div class="tag">${tag}${s.cost_eur != null ? " · €" + esc(s.cost_eur) : ""}</div>${st ? `<div class="tag st-${st.status}">${esc(st.status)}: ${esc(st.note)} ${st.ref ? idChip(st.ref) : ""}${st.compensation ? `<br><b>Compensation plan:</b> ${esc(st.compensation)}` : ""}</div>` : ""}</div>
        <div>${st && st.status === "HANDED" ? `<button class="btn small" data-confirm="${esc(s.id)}" data-opt="${esc(op.id)}">A person confirms it is done</button>` : !st || st.status === "FAILED" ? `<button class="btn small primary" data-approve="${esc(s.id)}" data-opt="${esc(op.id)}" ${doneAll ? "" : "disabled"}>${s.needs_approval ? "Approve" : "Run"}</button>` : st.status === "DONE" && s.reversible ? `<button class="btn small" data-undo="${esc(s.id)}" data-opt="${esc(op.id)}">Undo</button>` : ""}</div></div>`;
    }
    h += `<div class="note" style="margin-top:8px"><b>If a later step fails:</b> ${esc(op.compensation)}</div></div>`;
  }
  h += `<label class="note"><input type="checkbox" id="failsim"> Demo: make the next approved step fail (a mock provider failure) to show the compensation plan</label>`;
  if (f.needs_from_human && f.needs_from_human.length) h += `<div class="note" style="margin-top:10px"><b>The agent needs from a person:</b> ${f.needs_from_human.map(esc).join(" · ")}</div>`;
  h += `<div style="margin-top:14px"><input type="text" id="say" placeholder="${run.mode === "live" ? "Ask the agent anything about this order…" : "Ask why it stopped, or where a fact came from… (press Enter)"}"></div>`;
  return h;
}
function wireDecide() {
  const b = (id, fn) => { const e = $(id); if (e) e.onclick = fn; };
  b("#runrec", () => startRun("recorded")); b("#runlive", () => startRun("live"));
  b("#readout", () => { dockOn(true); speakHandover(); });
  document.querySelectorAll("[data-approve]").forEach((e) => e.onclick = () => approve(e.dataset.opt, e.dataset.approve, false));
  document.querySelectorAll("[data-confirm]").forEach((e) => e.onclick = () => approve(e.dataset.opt, e.dataset.confirm, false, true));
  document.querySelectorAll("[data-undo]").forEach((e) => e.onclick = () => approve(e.dataset.opt, e.dataset.undo, true));
  const say = $("#say");
  if (say) say.onkeydown = async (ev) => { if (ev.key === "Enter" && say.value.trim()) { const t = say.value; say.value = ""; await api(`/api/run/${S.run.id}/say`, { text: t }); poll(); } };
}
async function startRun(mode) {
  try {
    const r = await api("/api/run", { order_id: S.sel, mode, now: nowIso(), pace: new URLSearchParams(location.search).has("video") ? 0 : undefined });
    S.run = { id: r.id, status: "running", steps: [], mode, model: "", calls: 0, max_calls: 8 };
    renderOrder(); poll();
  } catch (e) { alert(e.message); }
}
function poll() {
  clearInterval(S.poll);
  S.poll = setInterval(async () => {
    S.run = await api("/api/run/" + S.run.id); renderOrder();
    if (S.run.status !== "running") clearInterval(S.poll);
  }, 500);
}
async function approve(opt, step, undo, confirm) {
  try {
    await api(`/api/run/${S.run.id}/approve`, { option: opt, step_id: step, undo, confirm: !!confirm, simulate_failure: !!($("#failsim") && $("#failsim").checked) });
    S.run = await api("/api/run/" + S.run.id); renderOrder();
  } catch (e) { alert(e.message); }
}

/* ---------- proof + how it was built (required, lower down) ---------- */
function renderProof() {
  const r = S.eval;
  $("#proofbody").innerHTML = `<h2>Proof</h2><p class="lede">One person works the queue top to bottom: ${r.queue} orders, ${r.scored} with a real deadline. The numbers are real deadlines missed. The method was written in <code>eval/PREREG.md</code> before the run.</p>
   <table class="bigtable"><tr><th>Minutes per order</th><th>Severity board</th><th>Cutoff (this tool)</th><th>Soonest deadline first</th></tr>
   ${(r.table || []).map((x) => `<tr><td>${x.latency_min}</td><td>${x.severity}</td><td class="us">${x.tier_first}</td><td class="${x.soonest < x.tier_first ? "lose" : ""}">${x.soonest}</td></tr>`).join("")}</table>
   <p class="note">Read honestly: this beats a severity board once a person needs 20+ minutes an order. A plain "soonest deadline first" sort beats it when people are slow (red), because this tool puts honesty about how real a deadline is ahead of raw speed. At 2 to 10 minutes an order nothing is missed. The data is a synthetic sample, so this says nothing about real-world frequency.</p>
   <h3 style="margin-top:24px">The "left alone" list, audited</h3>
   <p>${r.ledger_audit_flagged.length} of ${r.hidden} orders left alone trip a signal the queue rules do not use: ${r.ledger_audit_flagged.map((x) => `${esc(x.order)} (${esc(x.why.join("; "))})`).join(", ") || "none"}. They show as WATCH. The first version of the rules also left alone ord-00439, whose tracking ETA lands after its delivery window. This audit is what caught it.</p>
   <p class="note">A seeded sample (seed 7) was read in full: ${r.sample.join(", ")}. 9 of 10 were correctly left alone; ord-00735 is shown as WATCH. An earlier sample exposed two cancelled orders still carrying an active status (now excluded) and 38 submitted expenses open across 30 orders, which are back-office work and deliberately not in this queue.</p>`;
}

/* ---------- voice dock: an always-there orb you can talk to ---------- */
const synth = window.speechSynthesis;
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let V = { run: 0, rec: null, audio: null, idx: 0, chip: null, active: false };
let O = { mode: "idle", amp: 0, t: 0, an: null, data: null, ctx: null };
function sha1(msg) {
  const bytes = [...unescape(encodeURIComponent(msg))].map((c) => c.charCodeAt(0)), l = bytes.length;
  bytes.push(0x80); while (bytes.length % 64 !== 56) bytes.push(0);
  const bits = l * 8; for (let i = 7; i >= 0; i--) bytes.push(i > 3 ? 0 : (bits >>> (i * 8)) & 255);
  let h0 = 0x67452301, h1 = 0xEFCDAB89, h2 = 0x98BADCFE, h3 = 0x10325476, h4 = 0xC3D2E1F0;
  for (let o = 0; o < bytes.length; o += 64) {
    const w = new Array(80);
    for (let i = 0; i < 16; i++) w[i] = (bytes[o + 4 * i] << 24) | (bytes[o + 4 * i + 1] << 16) | (bytes[o + 4 * i + 2] << 8) | bytes[o + 4 * i + 3];
    for (let i = 16; i < 80; i++) { const x = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]; w[i] = (x << 1) | (x >>> 31); }
    let a = h0, b = h1, c = h2, d = h3, e = h4;
    for (let i = 0; i < 80; i++) {
      let f, k;
      if (i < 20) { f = (b & c) | (~b & d); k = 0x5A827999; } else if (i < 40) { f = b ^ c ^ d; k = 0x6ED9EBA1; } else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC; } else { f = b ^ c ^ d; k = 0xCA62C1D6; }
      const t = (((a << 5) | (a >>> 27)) + f + e + k + w[i]) | 0; e = d; d = c; c = (b << 30) | (b >>> 2); b = a; a = t;
    }
    h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0; h4 = (h4 + e) | 0;
  }
  return [h0, h1, h2, h3, h4].map((h) => (h >>> 0).toString(16).padStart(8, "0")).join("");
}
function pickVoice() {
  const v = (synth && synth.getVoices()) || [];
  const chosen = P.voiceName && v.find((x) => x.name === P.voiceName);
  if (chosen) return chosen;
  const order = [/Online \(Natural\).*(Ryan|Guy|Davis|Andrew|Brian)/i, /Online \(Natural\)/i, /Google UK English Male/i, /Ryan|Daniel|Arthur|George/i];
  for (const re of order) { const m = v.find((x) => re.test(x.name) && /^en/i.test(x.lang)); if (m) return m; }
  return v.find((x) => /en-GB/i.test(x.lang)) || v.find((x) => /^en/i.test(x.lang));
}

const SPEEDS = [0.9, 1, 1.15, 1.3, 1.5, 1.75];
const speedVal = () => { const v = parseFloat(P.rate); return v >= 0.5 && v <= 3 ? v : 1; };
const speedLabel = () => (speedVal() + "").replace(/^1$/, "1.0") + "×";
function cycleSpeed() {
  const i = SPEEDS.findIndex((x) => Math.abs(x - speedVal()) < 0.02);
  P.rate = String(SPEEDS[(i + 1) % SPEEDS.length]);
  try { localStorage.setItem("cutoff.prefs", JSON.stringify(P)); } catch {}
  if (!SPEEDS.some((x) => Math.abs(x - speedVal()) < 0.02)) P.rate = "1";   // old saved values snap to a real step
  $("#dspeed").textContent = speedLabel();
  if (V.audio) V.audio.playbackRate = speedVal();   // takes effect on the clip that is playing right now
}
/* the orb: a particle sphere that listens, thinks and speaks */
const orbEl = $("#orb"), octx = orbEl.getContext("2d");
const PTS = Array.from({ length: 460 }, (_, i) => { const y = 1 - (i / 459) * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963; return [Math.cos(th) * r, y, Math.sin(th) * r, Math.random()]; });
const ORB_COLOR = { idle: "159,176,224", listening: "138,180,255", thinking: "255,178,122", speaking: "255,255,255" };
function setMode(m, label) { O.mode = m; if (label !== undefined) $("#dstate").textContent = label; $("#dock").dataset.mode = m; }
function drawOrb(ts) {
  requestAnimationFrame(drawOrb);
  if (document.hidden) return;
  const dpr = devicePixelRatio || 1, size = Math.round(orbEl.clientWidth * dpr);
  if (orbEl.width !== size) { orbEl.width = size; orbEl.height = size; }
  const t = ts / 1000, dt = t - O.t; O.t = t;
  let target = { idle: 0.05, listening: 0.14 + 0.08 * Math.sin(t * 2.4), thinking: 0.1, speaking: 0.34 + 0.2 * Math.sin(t * 7.1) + 0.12 * Math.sin(t * 13.3) }[O.mode];
  if (O.an && O.mode === "speaking") { O.an.getByteTimeDomainData(O.data); let sum = 0; for (const v of O.data) sum += (v - 128) ** 2; target = Math.min(1, Math.sqrt(sum / O.data.length) / 40); }
  O.amp += (target - O.amp) * 0.18;
  const R = size * 0.36, cx = size / 2, ang = t * (O.mode === "thinking" ? 1.4 : 0.3), ca = Math.cos(ang), sa = Math.sin(ang), col = ORB_COLOR[O.mode];
  octx.clearRect(0, 0, size, size);
  for (const p of PTS) {
    const x = p[0] * ca + p[2] * sa, z = -p[0] * sa + p[2] * ca, y = p[1];
    const k = 1 + O.amp * 0.55 * Math.sin(t * 4 + p[3] * 20), depth = (z + 1) / 2;
    octx.fillStyle = `rgba(${col},${0.15 + 0.85 * depth * depth})`;
    octx.beginPath(); octx.arc(cx + x * R * k, cx + y * R * k, (0.45 + 1.0 * depth * depth) * dpr, 0, 6.283); octx.fill();
  }
}
requestAnimationFrame(drawOrb);

/* output: studio clip first, browser voice as the fallback; the caption is always shown */
function caption(t) { $("#vcaption").textContent = t; }
function setMin(m) { $("#dock").classList.toggle("min", m); document.documentElement.dataset.dockmin = String(m); P.dockMin = m; try { localStorage.setItem("cutoff.prefs", JSON.stringify(P)); } catch {} }
function dockOn(on) { V.active = on; $("#dock").classList.toggle("active", on); $("#dend").hidden = !on; }
function tap(audio) {
  try {
    O.ctx = O.ctx || new (window.AudioContext || window.webkitAudioContext)();
    const src = O.ctx.createMediaElementSource(audio), an = O.ctx.createAnalyser(); an.fftSize = 256;
    src.connect(an); an.connect(O.ctx.destination); O.an = an; O.data = new Uint8Array(an.fftSize);
  } catch { O.an = null; }
}
function stopAudio() {
  try { synth && synth.cancel(); } catch {}
  O.an = null;
  if (V.audio) { const a = V.audio; V.audio = null; a.pause(); a.dispatchEvent(new Event("ended")); }
}
async function playStudio(text) {
  const h = sha1(text); let url = null;
  try {
    let r = await fetch(`audio/${h}.wav`);
    if (!r.ok) {
      if (!S.info.tts) return false;
      r = await fetch("/api/tts", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
      if (!r.ok) { V.err = (await r.json().catch(() => ({}))).error || "speech failed"; return false; }
    }
    url = URL.createObjectURL(await r.blob());
  } catch { return false; }
  return new Promise((res) => {
    const a = new Audio(url); V.audio = a; a.preservesPitch = true; a.playbackRate = speedVal(); tap(a);
    a.onended = () => { O.an = null; res(true); }; a.onerror = () => res(false);
    a.play().catch(() => res(false));
  });
}
function browserSpeak(text) {
  return new Promise((res) => {
    if (!synth) { setTimeout(res, Math.min(5000, text.length * 45)); return; }
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text), v = pickVoice();
    if (v) u.voice = v;
    u.rate = speedVal(); u.pitch = 0.85; u.onend = res; u.onerror = res;
    synth.speak(u);
  });
}
async function speak(text) {
  text = speech(text); caption(text); stopAudio(); setMode("speaking", "Speaking");
  try {
    if (P.voice === "off") await new Promise((res) => setTimeout(res, Math.min(5000, text.length * 45)));
    else if (!(P.engine !== "browser" && await playStudio(text))) await browserSpeak(text);
  } finally { if (O.mode === "speaking") setMode(V.active ? "listening" : "idle", V.active ? "Listening" : "Tap the orb and talk to Cutoff"); }
}

/* input: speech recognition where the browser has it, quick-reply chips everywhere */
function listenOnce(ms = 9000) {
  return new Promise((res) => {
    if (!SR) return res(null);
    const r = new SR(); V.rec = r; r.lang = "en-GB"; r.interimResults = false;
    let done = false; const fin = (t) => { if (!done) { done = true; res(t); } };
    r.onresult = (e) => fin(e.results[0][0].transcript.toLowerCase());
    r.onerror = () => fin(null); r.onend = () => fin(null);
    setTimeout(() => { try { r.stop(); } catch {} fin(null); }, ms);
    try { r.start(); } catch { fin(null); }
  });
}
function chips() {
  const list = ["What needs me first?", "Next one", "Open it", "Why did you stop?", "What's the decision?", "In six hours?"];
  $("#dchips").innerHTML = list.map((c) => `<button class="chipbtn" data-say="${esc(c)}">${esc(c)}</button>`).join("");
  document.querySelectorAll("#dchips .chipbtn").forEach((b) => b.onclick = () => { if (!V.active) startTalk(false); if (V.chip) V.chip(b.dataset.say.toLowerCase()); else handleTyped(b.dataset.say.toLowerCase()); });
}
async function handleTyped(t) { const id = V.run; await handle(t, id); }

/* what the voice says: summaries, not headlines read back */
const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"];
const TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];
const words = (n) => n < 20 ? WORDS[n] : TENS[Math.floor(n / 10)] + (n % 10 ? "-" + WORDS[n % 10] : "");
function gist(c) {
  const k = c.clock; if (!k) return "no deadline is stated";
  const w = spokenLeft(k.seconds_left).replace(" left", "");
  if (c.tier === "CUTOFF") return `ferry check-in closes in ${w}`;
  if (c.tier === "SLOT") return k.seconds_left < 0 ? `its planned window ended ${w.replace("already overdue by ", "")} ago` : `a planned window ends in ${w}`;
  if (c.tier === "REMINDER") return `only a soft reminder is due in ${w}, nothing hard`;
  return `${TIER[c.tier][0].toLowerCase()} in ${w}`;
}
const first = (t, n = 200) => {
  t = speech(t); const m = t.match(/^.*?[.!?](\s|$)/); let x = m ? m[0].trim() : t;
  if (x.length > n) { let cut = x.lastIndexOf(", ", n); if (cut < 70) cut = x.lastIndexOf(" ", n); x = x.slice(0, cut).replace(/[,;:]$/, "") + "."; }
  return x;
};
const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
function describe(c) {
  const g = S.glance; const ag = g && g.top && g.top.order_id === c.order_id && g.agent ? " The agent already looked, and it can't tell whether the driver makes it." : "";
  return `${c.car}, ${c.from_city} to ${c.to_city}: ${gist(c)}.${ag}`;
}
const T = {
  intro: () => { const q = S.q.queue, cut = S.q.counts.by_tier.CUTOFF || 0; return `Here is where we are. ${cap(words(q.length))} orders need a person, but ${cut ? `only ${words(cut)} ${cut === 1 ? "is" : "are"} really against the clock, and ${cut === 1 ? "it's a ferry" : "both are ferries"}.` : "none is against a hard clock."}`; },
  line: (c) => `${describe(c)} Want me to open it?`,
  hero: () => { const a = S.glance.agent; return `The agent looked at the ${S.glance.top.car}. ${first(a.stop_reason)} It booked nothing.`; },
  asking: "Asking the agent to look. It can't book anything.",
  handover: (f) => `The agent stopped. ${first(f.stop_reason, 190)} Over to you: ${first(f.decision, 170)} I won't book or change anything without your yes.`,
  done: "That's everything that needs you right now.", quiet: "Okay. Tap the orb whenever you want me.", noRun: "The agent hasn't looked at that one yet. The details are on screen.",
  help: "I didn't catch that. Try: what needs me first, next, open it, why did you stop, or what's the decision.",
  noApprove: "I won't approve or book anything by voice. Tap Approve on the card, so it's always you.",
};
async function prewarmVoice() {
  const texts = [T.intro(), ...S.q.queue.slice(0, 3).map(T.line), T.hero(), T.asking, T.done, T.quiet, T.noRun, T.help, T.noApprove];
  for (const id of S.info.recorded) { try { texts.push(T.handover(await api("/api/final/" + id))); } catch {} }
  const out = [];
  for (const t of texts) { const r = await fetch("/api/tts", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: speech(t) }) }); out.push(r.ok ? "ok" : (await r.text()).slice(0, 160)); caption(`Recording voice clips ${out.length} of ${texts.length}…`); }
  return out;
}
window.prewarmVoice = prewarmVoice; window.sha1 = sha1;

/* the conversation */
const cur = () => S.q.queue[V.idx];
async function brief(id) {
  V.idx = 0; await speak(T.intro());
  if (V.run === id && cur()) await speak(T.line(cur()));
}
async function openAndBrief(oid, id) {
  await openOrder(oid); $("#work").scrollIntoView();
  if (!S.order.existing_trace) return speak(T.noRun);
  await speak(T.asking);
  await startRun("recorded");
  while (!S.run || S.run.status === "running") await new Promise((r) => setTimeout(r, 400));
  if (V.run === id) await speakHandover();
}
async function speakHandover() { const f = S.run && S.run.final; if (!f) return speak("There is no handover yet. Say open it and I'll ask the agent."); await speak(T.handover(f)); }
function showTab(t) { S.tab = t; renderSummary(); renderList(); $("#work").scrollIntoView(); }
async function setHours(h) { const b = document.querySelector(`#later button[data-h="${h}"]`); if (b) b.click(); await new Promise((r) => setTimeout(r, 700)); }
async function handle(t, id) {
  t = t.toLowerCase().replace(/[.,!?]/g, "").trim();
  const has = (re) => re.test(t);
  if (has(/\b(approve|book it|confirm|buy|pay|cancel)\b/)) return speak(T.noApprove);
  if (has(/\b(stop|quiet|bye|goodbye|that's all|thanks|thank you|end)\b/)) { await speak(T.quiet); endTalk(); return; }
  if (has(/\b(brief|summary|overview|status|what needs|what's going on|where are we|first)\b/)) return brief(id);
  if (has(/\b(next|skip|another|after that)\b/)) { V.idx = Math.min(V.idx + 1, S.q.queue.length - 1); return cur() ? speak(T.line(cur())) : speak(T.done); }
  if (has(/\b(previous|go back|before that|last one)\b/)) { V.idx = Math.max(V.idx - 1, 0); return speak(T.line(cur())); }
  if (has(/\b(why).*(stop|stopped)|reason\b/)) return S.run && S.run.final ? speak(first(S.run.final.stop_reason, 260)) : speak("I haven't looked at it yet. Say open it and I will.");
  if (has(/\b(decision|what do i|what should i|recommend|options?)\b/)) return S.run && S.run.final ? speak(first(S.run.final.decision, 260)) : speak("Say open it first and the agent will look.");
  if (has(/\b(read|handover|aloud)\b/)) return speakHandover();
  if (has(/\b(open|show|yes|yeah|yep|go ahead|that one|investigate|look into|ask the agent|check it|sure|please)\b/)) return cur() ? openAndBrief(cur().order_id, id) : speak(T.done);
  if (has(/\b(how many|count|number)\b/)) return speak(`${words(S.q.queue.length)} need you and ${words(S.q.counts.ledger)} were left alone.`);
  if (has(/\b(ferry|cut.?off|check.?in)\b/)) { showTab("CUTOFF"); V.idx = S.q.queue.findIndex((c) => c.tier === "CUTOFF"); const n = S.q.counts.by_tier.CUTOFF || 0; return speak(n ? `${words(n)} ferry check-in${n > 1 ? "s are" : " is"} closing. They're on screen now.` : "No ferry check-in is closing right now."); }
  if (has(/\b(left alone|ignored|fine|on track)\b/)) { showTab("left"); return speak(`${words(S.q.counts.ledger)} orders were left alone. Each says what was checked.`); }
  if (has(/\b(now|reset|today)\b/) && !has(/later/)) { await setHours(0); return speak(`Back to now. ${words(S.q.queue.length)} need you.`); }
  if (has(/\b(later|tomorrow|hours?|days?|what if|in \w+ )\b/)) {
    const h = has(/three days|72/) ? 72 : has(/two days|48/) ? 48 : has(/tomorrow|day|24/) ? 24 : 6;
    await setHours(h); return speak(`Jumping ahead ${h === 6 ? "six hours" : h === 24 ? "a day" : h === 48 ? "two days" : "three days"}. Now ${words(S.q.queue.length)} orders need you, and ${words(S.q.counts.by_tier.CUTOFF || 0)} ferry check-in${(S.q.counts.by_tier.CUTOFF || 0) === 1 ? " is" : "s are"} still open.`);
  }
  if (S.run && S.run.final) {  // anything else goes to the agent, in its own words
    const before = S.run.steps.length;
    await api(`/api/run/${S.run.id}/say`, { text: t });
    for (let i = 0; i < 40; i++) { S.run = await api("/api/run/" + S.run.id); const nw = S.run.steps.slice(before).filter((s) => s.kind === "SAY"); if (nw.length || (S.run.status !== "running" && i > 3)) { renderOrder(); return speak(first(nw.length ? nw[nw.length - 1].text : T.help, 300)); } await new Promise((r) => setTimeout(r, 700)); }
  }
  return speak(T.help);
}
async function startTalk(withBrief) {
  setMin(false);
  const id = ++V.run; stopAudio(); dockOn(true); chips(); setMode("listening", "Listening");
  let silent = 0;
  if (withBrief) await brief(id);
  else caption(SR ? "I'm listening. Try “what needs me first?”" : "Tap a suggestion below.");
  while (V.run === id) {
    setMode("listening", SR ? "Listening" : "Tap a suggestion");
    const heard = await Promise.race([listenOnce(), new Promise((r) => { V.chip = r; })]);
    V.chip = null; if (V.run !== id) return;
    if (!heard) {
      await new Promise((r) => setTimeout(r, 500));
      if (!SR || ++silent >= 3) {  // nothing heard (or no microphone): wait for a tap or a suggestion instead of looping
        setMode("idle", SR ? "Paused. Tap the orb to talk again" : "Tap a suggestion");
        caption(SR ? "I'm not hearing anything. Tap the orb to resume, or use a suggestion." : "Tap a suggestion below.");
        const t = await new Promise((r) => { V.chip = r; V.wake = r; });
        V.chip = V.wake = null; if (V.run !== id) return;
        silent = 0; setMode("thinking", "Thinking");
        await handle(t === "__wake__" ? "what needs me first" : t, id);
      }
      continue;
    }
    silent = 0; caption("“" + heard + "”"); setMode("thinking", "Thinking");
    await handle(heard, id);
  }
}
function endTalk() { V.run++; stopAudio(); try { V.rec && V.rec.abort(); } catch {} dockOn(false); setMode("idle", "Tap the orb and talk to Cutoff"); caption("Try: “what needs me first?”"); }
$("#dend").onclick = endTalk;
$("#dspeed").onclick = (e) => { e.stopPropagation(); cycleSpeed(); };
$("#dmin").onclick = (e) => { e.stopPropagation(); setMin(true); };
$("#dock").addEventListener("click", (e) => { if ($("#dock").classList.contains("min") && !e.target.closest(".dmin")) { e.stopPropagation(); setMin(false); } }, true);
$("#orbbtn").onclick = () => { if (!V.active) startTalk(false); else if (V.wake) V.wake("__wake__"); else if (O.mode === "speaking") stopAudio(); };
$("#dmic").onclick = () => { if (!V.active) startTalk(false); else if (V.wake) V.wake("__wake__"); };
chips();

/* ---------- settings (saved in this browser) ---------- */
const DEFAULTS = { theme: "auto", size: "m", density: "comfortable", images: "on", motion: "full", voice: "on", engine: "studio", rate: "1", voiceName: "", dockMin: false };
let P = { ...DEFAULTS };
try { P = { ...DEFAULTS, ...JSON.parse(localStorage.getItem("cutoff.prefs") || "{}") }; } catch {}
function applyPrefs() {
  const dark = P.theme === "dark" || (P.theme === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  const h = document.documentElement;
  h.dataset.theme = dark ? "dark" : "light"; h.dataset.size = P.size; h.dataset.density = P.density; h.dataset.images = P.images;
  h.dataset.motion = P.motion === "reduce" || matchMedia("(prefers-reduced-motion: reduce)").matches ? "reduce" : "full";
  document.querySelectorAll("#settings .seg").forEach((g) => g.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === String(P[g.dataset.key]))));
  const sel = $("#voicepick"); if (sel && sel.value !== P.voiceName) sel.value = P.voiceName;
  $("#dspeed").textContent = speedLabel();
  $("#dock").classList.toggle("min", !!P.dockMin); h.dataset.dockmin = String(!!P.dockMin);
}
function savePrefs() { try { localStorage.setItem("cutoff.prefs", JSON.stringify(P)); } catch {} applyPrefs(); }
function fillVoices() {
  const sel = $("#voicepick"); const v = (synth && synth.getVoices()) || [];
  const list = v.filter((x) => /^en/i.test(x.lang)); 
  sel.innerHTML = `<option value="">Automatic (best English voice)</option>` + (list.length ? list : v).map((x) => `<option value="${esc(x.name)}">${esc(x.name)}</option>`).join("");
  sel.value = P.voiceName;
}
$("#gear").onclick = () => { const o = $("#settings"); o.hidden = !o.hidden; $("#gear").setAttribute("aria-expanded", String(!o.hidden)); };
document.addEventListener("click", (e) => { const o = $("#settings"); if (!o.hidden && !o.contains(e.target) && !$("#gear").contains(e.target)) { o.hidden = true; $("#gear").setAttribute("aria-expanded", "false"); } });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#settings").hidden = true; } });
document.querySelectorAll("#settings .seg button").forEach((b) => b.onclick = () => { P[b.parentElement.dataset.key] = b.dataset.v; savePrefs(); });
$("#voicepick").onchange = (e) => { P.voiceName = e.target.value; savePrefs(); };
$("#reset").onclick = () => { P = { ...DEFAULTS }; savePrefs(); fillVoices(); };
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyPrefs);
if (synth) { synth.onvoiceschanged = fillVoices; }
fillVoices(); applyPrefs();

/* ---------- time buttons (no slider) ---------- */
document.querySelectorAll("#later button").forEach((b) => b.onclick = async () => {
  S.hours = +b.dataset.h;
  document.querySelectorAll("#later button").forEach((x) => x.classList.toggle("on", x === b));
  await loadQueue();
  if (S.sel) { S.order = await api(`/api/order/${S.sel}?now=${encodeURIComponent(nowIso())}`); S.run = null; renderOrder(); }
});

document.querySelector('nav a[href="#left"]').onclick = (e) => { e.preventDefault(); S.tab = "left"; renderSummary(); renderList(); $("#work").scrollIntoView(); };
(async () => {
  S.info = await api("/api/info");
  [S.glance, S.eval] = await Promise.all([api("/api/glance"), api("/api/eval")]);
  renderHero(); renderDiff(); renderProof(); await loadQueue();
})();
