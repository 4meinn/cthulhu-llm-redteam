"use strict";

// Every piece of text that comes from the API (model replies, judge reasons,
// tactics, notes, run names) is written with textContent, never innerHTML. A
// jailbroken target can return markup, and the tool must never run it.

const Layout = window.CthulhuLayout;
const Panel = window.CthulhuPanel;
const Sweep = window.CthulhuSweep;

function el(tag, opts) {
  opts = opts || {};
  const node = document.createElement(tag);
  if (opts.cls) node.className = opts.cls;
  if (opts.text != null) node.textContent = String(opts.text);
  return node;
}

function qs(sel) { return document.querySelector(sel); }

function safeStorage() {
  try { return window.localStorage; } catch (e) { return null; }
}

const views = { setup: qs("#setup"), live: qs("#live"), report: qs("#report"), sweep: qs("#sweep") };

// The live board: every run keeps its own stream, its own transcript and its own
// list entry. The layout is only a class on the board, so switching between the
// list view and the columns never touches a stream.
const runs = new Map();     // run id -> run
const order = [];           // run ids, oldest first
let selectedRun = null;
let layoutPref = "C";

function showView(name) {
  for (const key in views) views[key].classList.toggle("hidden", key !== name);
  document.querySelectorAll("#steps span").forEach(function (s) {
    s.classList.toggle("active", s.dataset.step === name);
  });
}

// --- setup -----------------------------------------------------------------

// The guardrail mode is four radio buttons drawn as one segmented control. The
// labels and the one line explanations come from panel.js, so there is one list.
function buildModePicker(form) {
  const picker = qs("#mode-picker");
  Panel.MODES.forEach(function (m) {
    const label = el("label", { text: m.label });
    const input = el("input");
    input.type = "radio";
    input.name = "guardrail_mode";
    input.value = m.value;
    input.checked = m.value === Panel.DEFAULT_MODE;
    label.prepend(input);
    picker.append(label);
  });
  syncModePicker(form);
}

function selectedMode(form) {
  const checked = form.querySelector('input[name="guardrail_mode"]:checked');
  return checked ? checked.value : Panel.DEFAULT_MODE;
}

function syncModePicker(form) {
  const mode = selectedMode(form);
  qs("#mode-picker").querySelectorAll("label").forEach(function (l) {
    l.classList.toggle("on", l.firstChild.value === mode);
  });
  const info = Panel.MODES.filter(function (m) { return m.value === mode; })[0];
  qs("#mode-help").textContent = info ? info.text : "";
}

function buildConfig(form) {
  const val = function (name) { return form.elements[name].value; };
  const objectives = Array.prototype.map.call(
    form.querySelectorAll('input[name="objective"]:checked'), function (i) { return i.value; });
  const canary = val("canary").trim();
  const label = val("label").trim();
  const gr = Panel.modeConfig(selectedMode(form));
  return {
    target: { provider: val("target_provider"), base_url: val("target_base_url"), model: val("target_model") },
    judge: { provider: val("judge_provider"), base_url: val("judge_base_url"), model: val("judge_model") },
    system_prompt_text: val("system_prompt"),
    canary: canary || null,
    label: label || null,
    guardrail: gr.guardrail,
    guardrail_mode: gr.guardrail_mode,
    scoring: "any",
    attacker: {
      strategy: val("attacker_strategy"),
      endpoint: { provider: val("attacker_provider"), base_url: val("attacker_base_url"), model: val("attacker_model") },
      objectives: objectives,
      max_turns: parseInt(val("max_turns"), 10),
      judge_mode: val("judge_mode"),
    },
  };
}

function updateOosWarning(form) {
  const oos = form.querySelector('input[value="out_of_scope"]').checked;
  const perTurn = form.elements.judge_mode.value === "per_turn";
  qs("#oos-warning").classList.toggle("hidden", !(oos && !perTurn));
}

async function submitRun(ev) {
  ev.preventDefault();
  const form = ev.target;
  const err = qs("#setup-error");
  err.classList.add("hidden");
  const cfg = buildConfig(form);
  if (cfg.attacker.objectives.length === 0) {
    err.textContent = "Pick at least one objective.";
    err.classList.remove("hidden");
    return;
  }
  let resp;
  try {
    resp = await fetch("/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    });
  } catch (e) {
    err.textContent = "Could not reach the backend.";
    err.classList.remove("hidden");
    return;
  }
  if (!resp.ok) {
    const body = await resp.json().catch(function () { return {}; });
    err.textContent = "Run rejected: " + (body.detail || resp.status);
    err.classList.remove("hidden");
    return;
  }
  const data = await resp.json();
  await addRun(data.run_id);
  showView("live");
}

// --- the board -------------------------------------------------------------

function isTerminal(status) {
  return status === "done" || status === "cancelled" || status === "error";
}

function dotClass(status) {
  return status === "running" ? "run" : status;
}

function message(who, text, cls) {
  const row = el("div", { cls: "msg" + (cls ? " " + cls : "") });
  row.append(el("span", { cls: "who", text: who + ": " }));
  row.append(document.createTextNode(text));
  return row;
}

// One exchange, used by the live feed and by the report transcripts.
function turnRow(label, t, reason) {
  const row = el("div", { cls: "turn" });
  const head = el("div", { cls: "turn-head" });
  head.append(el("span", { cls: "tnum", text: label }));
  head.append(el("span", { cls: "tactic", text: t.tactic || "" }));
  Panel.turnBadges(t).forEach(function (b) {
    head.append(el("span", { cls: "tag " + b.cls, text: b.text }));
  });
  row.append(head);
  if (t.sent) row.append(message("atk", t.sent, ""));
  if (!t.blocked && t.response) row.append(message("bot", t.response, "reply"));
  const hint = Panel.turnNote(t);
  if (hint) row.append(el("div", { cls: "hint-line", text: hint }));
  if (reason) row.append(el("div", { cls: "reason", text: reason }));
  return row;
}

function describeMeta(meta) {
  const bits = [];
  if (meta.target) bits.push(meta.target);
  if (meta.judge) bits.push("judge " + meta.judge);
  bits.push("guardrail " + Panel.modeFromMeta(meta));
  return bits.join("  |  ");
}

function buildParts(run) {
  const parts = {};

  // the run's own panel: header, actions and the transcript
  parts.panel = el("div", { cls: "panel" });
  const head = el("div", { cls: "run-head" });
  parts.dot = el("span", { cls: "dot run" });
  head.append(parts.dot);
  head.append(el("span", { cls: "run-title", text: run.label }));
  parts.status = el("span", { cls: "status run", text: "running" });
  head.append(parts.status);
  parts.panel.append(head);
  parts.panel.append(el("div", { cls: "run-sub", text: describeMeta(run.meta) }));

  const actions = el("div", { cls: "run-actions" });
  parts.cancel = el("button", { cls: "ghost small", text: "Cancel" });
  parts.cancel.type = "button";
  parts.cancel.addEventListener("click", function () {
    fetch("/runs/" + encodeURIComponent(run.id) + "/cancel", { method: "POST" });
  });
  parts.report = el("button", { cls: "small hidden", text: "View report" });
  parts.report.type = "button";
  parts.report.addEventListener("click", function () { showReport(run); });
  actions.append(parts.cancel, parts.report);
  parts.panel.append(actions);

  parts.feed = el("div", { cls: "feed" });
  parts.panel.append(parts.feed);

  // the run's entry in the list, with a progress bar
  parts.item = el("div", { cls: "list-item" });
  const itemHead = el("div", { cls: "run-head" });
  parts.itemDot = el("span", { cls: "dot run" });
  itemHead.append(parts.itemDot, el("span", { cls: "run-title", text: run.label }));
  parts.item.append(itemHead);
  parts.item.append(el("div", { cls: "run-sub", text: run.meta.target || "" }));
  const track = el("div", { cls: "progress" });
  parts.fill = el("i");
  track.append(parts.fill);
  parts.item.append(track);
  parts.item.addEventListener("click", function () { selectRun(run.id); });

  return parts;
}

function createRun(entry) {
  if (runs.has(entry.id)) return runs.get(entry.id);
  const run = {
    id: entry.id,
    label: entry.label || entry.id.slice(0, 6),
    meta: entry.meta || {},
    plan: entry.plan || {},
    status: "running",
    ended: 0,
    turn: 0,
    retries: 0,
    source: null,
  };
  run.parts = buildParts(run);
  qs("#run-panels").append(run.parts.panel);
  qs("#run-list").append(run.parts.item);
  runs.set(run.id, run);
  order.push(run.id);
  openStream(run);
  updateRun(run);
  return run;
}

async function addRun(runId) {
  let entry = { id: runId };
  try {
    const resp = await fetch("/runs/" + encodeURIComponent(runId));
    if (resp.ok) entry = await resp.json();
  } catch (e) { /* the placeholder entry is enough to follow the stream */ }
  createRun(entry);
  selectRun(runId);
}

function resetFeed(run) {
  run.parts.feed.replaceChildren();
  run.ended = 0;
  run.turn = 0;
}

// The stream replays every event from the start when it reconnects, so a dropped
// connection rebuilds the feed instead of doubling it.
function openStream(run) {
  if (run.source) run.source.close();
  run.source = new EventSource("/runs/" + encodeURIComponent(run.id) + "/events");
  run.source.onmessage = function (e) { handleEvent(run, JSON.parse(e.data)); };
  run.source.onerror = function () {
    if (isTerminal(run.status)) return;
    run.source.close();
    if (run.retries >= 5) return;
    run.retries += 1;
    resetFeed(run);
    setTimeout(function () { if (!isTerminal(run.status)) openStream(run); }, 1500);
  };
}

function endedText(ev) {
  const detail = ev.ended_detail ? " (" + ev.ended_detail + ")" : "";
  return (ev.ended_reason || "finished") + detail + " after " + ev.turns + " turn(s)";
}

function handleEvent(run, ev) {
  const feed = run.parts.feed;
  if (ev.kind === "session_start") {
    run.turn = 0;
    feed.append(el("div", { cls: "sess", text: ev.phase + " / " + ev.objective }));
  } else if (ev.kind === "turn") {
    run.turn = ev.turn;
    feed.append(turnRow(ev.phase + " t" + ev.turn, ev, ev.reason ? "judge: " + ev.reason : ""));
  } else if (ev.kind === "session_end") {
    run.ended += 1;
    run.turn = 0;
    feed.append(el("div", { cls: "note", text: "ended: " + endedText(ev) }));
  } else if (ev.kind === "cancelled") {
    feed.append(el("div", { cls: "note", text: "cancelled" }));
  } else if (ev.kind === "error") {
    feed.append(el("div", { cls: "error", text: ev.message || "error" }));
  } else if (ev.kind === "run_finished") {
    run.status = ev.status;
    if (run.source) run.source.close();
  }
  updateRun(run);
  feed.scrollTop = feed.scrollHeight;
}

function updateRun(run) {
  const p = run.parts;
  const cls = dotClass(run.status);
  p.dot.className = "dot " + cls;
  p.itemDot.className = "dot " + cls;
  p.status.className = "status " + cls;
  p.status.textContent = run.status;
  const frac = Layout.progress(run.plan, run.status, run.ended, run.turn);
  p.fill.style.width = Math.round(frac * 100) + "%";
  p.cancel.classList.toggle("hidden", isTerminal(run.status));
  p.report.classList.toggle("hidden", !(run.status === "done" || run.status === "cancelled"));
  renderBoard();
}

function renderBoard() {
  const choice = Layout.chooseLayout(order.length, layoutPref);
  const board = qs("#board");
  board.className = "board layout-" + choice;
  board.style.setProperty("--runs", String(Math.max(order.length, 1)));
  qs("#layout-toggle").classList.toggle("hidden", choice === "single");
  document.querySelectorAll("#layout-toggle button").forEach(function (b) {
    b.setAttribute("aria-pressed", String(b.dataset.layout === layoutPref));
  });
  selectedRun = Layout.pickSelected(order, selectedRun);
  let running = 0;
  order.forEach(function (id) {
    const run = runs.get(id);
    if (run.status === "running") running += 1;
    run.parts.panel.classList.toggle("selected", id === selectedRun);
    run.parts.item.classList.toggle("active", id === selectedRun);
  });
  qs("#board-meta").textContent =
    order.length + (order.length === 1 ? " run" : " runs") + ", " + running + " running";
  qs("#stop-all-btn").disabled = running === 0;
}

function selectRun(id) {
  selectedRun = id;
  renderBoard();
}

function setLayout(value) {
  layoutPref = Layout.normalizePreference(value);
  Layout.savePreference(safeStorage(), layoutPref);
  renderBoard();
}

// --- report ----------------------------------------------------------------

async function showReport(run) {
  const resp = await fetch("/runs/" + encodeURIComponent(run.id) + "/report");
  if (!resp.ok) return;
  renderReport(await resp.json());
  qs("#report-title").textContent = run.label;
  qs("#md-link").href = "/runs/" + encodeURIComponent(run.id) + "/report.md";
  showView("report");
}

function sessionsByObjective(sessions) {
  const map = {};
  (sessions || []).forEach(function (s) { map[s.objective.id] = s; });
  return map;
}

function metLabel(session) {
  if (!session || !session.success) return "held";
  return (session.needs_review ? "judge only (review)" : "proven") + " @ turn " + session.success_turn;
}

function renderReport(report) {
  const body = qs("#report-body");
  body.replaceChildren();
  const meta = report.meta || {};
  const guarded = meta.has_guarded;

  const metaLine = [
    "target " + (meta.target || "?"),
    "judge " + (meta.judge || "none"),
    "scoring " + (meta.scoring || "any"),
    "judge mode " + (meta.judge_mode || "?"),
    "guardrail " + Panel.modeFromMeta(meta),
  ];
  if (meta.judge_agreement != null) metaLine.push("agreement " + Math.round(meta.judge_agreement * 100) + "%");
  body.append(el("p", { cls: "meta", text: metaLine.join("  |  ") }));
  if (guarded && Panel.hasOutputStage(Panel.modeFromMeta(meta))) {
    body.append(el("p", { cls: "warn", text: Panel.OUTPUT_NOTE }));
  }

  // objectives table
  const bMap = sessionsByObjective(report.baseline);
  const gMap = sessionsByObjective(report.guarded);
  body.append(el("h2", { text: "Objectives" }));
  const table = el("table");
  const head = ["Objective", "Signal", "OWASP", "Baseline"];
  if (guarded) head.push("Guarded");
  const thead = el("tr");
  head.forEach(function (h) { thead.append(el("th", { text: h })); });
  table.append(thead);
  (report.objectives || []).forEach(function (obj) {
    const tr = el("tr");
    tr.append(el("td", { text: obj.id }));
    tr.append(el("td", { text: obj.deterministic ? "deterministic and judge" : "judge only" }));
    tr.append(el("td", { text: obj.owasp }));
    tr.append(el("td", { text: metLabel(bMap[obj.id]) }));
    if (guarded) tr.append(el("td", { text: metLabel(gMap[obj.id]) }));
    table.append(tr);
  });
  body.append(table);

  const phases = [["baseline", report.baseline]];
  if (guarded) phases.push(["guarded", report.guarded]);

  // turn counts per phase: proven and judge only apart, caught and error never folded into held
  body.append(el("h2", { text: "Turns" }));
  const totals = el("table");
  const totalsHead = el("tr");
  ["Phase", "Turns", "Blocked", "Errors", "Proven", "Judge only", "Caught", "Reply changed"].forEach(function (h) {
    totalsHead.append(el("th", { text: h }));
  });
  totals.append(totalsHead);
  phases.forEach(function (p) {
    const t = Panel.phaseTotals(p[1]);
    const tr = el("tr");
    [p[0], t.turns, t.blocked, t.errors, t.proven, t.judgeOnly, t.caught, t.changed].forEach(function (v) {
      tr.append(el("td", { text: v }));
    });
    totals.append(tr);
  });
  body.append(totals);

  // what broke
  body.append(el("h2", { text: "What broke and how" }));
  let broke = false;
  phases.forEach(function (p) {
    (p[1] || []).forEach(function (s) {
      if (s.success) {
        broke = true;
        body.append(el("div", { cls: "finding-line", text:
          s.objective.id + " (" + p[0] + "): " + metLabel(s) + " via " + s.source }));
      }
    });
  });
  if (!broke) body.append(el("p", { cls: "muted", text: "No objective was met." }));

  // needs review
  const review = Panel.reviewItems(phases.map(function (p) { return { name: p[0], sessions: p[1] }; }));
  body.append(el("h2", { text: "Needs review (judge only)" }));
  if (review.length === 0) {
    body.append(el("p", { cls: "muted", text: "None. No turn was flagged by the judge alone." }));
  } else {
    body.append(el("p", { cls: "muted", text:
      "The judge flagged these turns and no deterministic check did. They are not counted as proven. " +
      "Read each reply: it may be a leak the rules cannot see, or a judge reading too much into the text. " +
      "The reply is what the application returned, after any output filter." }));
    review.forEach(function (r) {
      body.append(el("h3", { text: r.objective + " (" + r.phase + ", turn " + r.turn + ")" }));
      body.append(turnRow("t" + r.turn, r.data, r.data.note));
    });
  }

  // transcripts
  body.append(el("h2", { text: "Transcripts" }));
  phases.forEach(function (p) {
    (p[1] || []).forEach(function (s) {
      body.append(el("h3", { text: s.objective.id + " (" + p[0] + ")" }));
      if (s.ended_reason) {
        const detail = s.ended_detail ? " (" + s.ended_detail + ")" : "";
        body.append(el("p", { cls: "muted", text: "ended: " + s.ended_reason + detail }));
      }
      s.turns.forEach(function (t, i) { body.append(turnRow("t" + (i + 1), t, t.note)); });
    });
  });
}

// --- sweep -----------------------------------------------------------------

// The Sweep view takes the Setup form as its base (prompt, canary, judge, attacker) and adds a
// grid on top. The preview asks the server for the plan on every change, after a short pause,
// and only the newest answer is drawn.
const sweepView = { plan: null, planError: "", token: 0, timer: null, busy: false };

function sweepChecks(containerId, name, values, checked) {
  const box = qs("#" + containerId);
  values.forEach(function (v) {
    const label = el("label", { text: v });
    const input = el("input");
    input.type = "checkbox";
    input.name = name;
    input.value = v;
    input.checked = checked.indexOf(v) >= 0;
    label.prepend(input);
    box.append(label);
  });
}

function buildSweepForm() {
  sweepChecks("sweep-guardrails", "sweep_guardrail", Sweep.GUARDRAILS, Sweep.GUARDRAILS);
  sweepChecks("sweep-modes", "sweep_mode", Sweep.MODES, Sweep.MODES);
  sweepChecks("sweep-objectives", "sweep_objective", Sweep.OBJECTIVES, ["leak_canary"]);
}

function readSweepFields(form) {
  const checked = function (name) {
    return Array.prototype.map.call(form.querySelectorAll('input[name="' + name + '"]:checked'),
      function (i) { return i.value; });
  };
  return {
    name: form.elements.sweep_name.value,
    guardrails: checked("sweep_guardrail"),
    modes: checked("sweep_mode"),
    objectives: checked("sweep_objective"),
    targets: form.elements.sweep_targets.value,
    strategy: form.elements.sweep_strategy.value,
    repeats: parseInt(form.elements.sweep_repeats.value, 10),
    max_turns: parseInt(form.elements.sweep_max_turns.value, 10),
    workers: parseInt(form.elements.sweep_workers.value, 10),
    judge_mode: form.elements.sweep_judge_mode.value,
  };
}

function sweepRequest() {
  const fields = readSweepFields(qs("#sweep-form"));
  const setupCfg = buildConfig(qs("#run-form"));
  return { fields: fields, setupCfg: setupCfg,
           setupProblems: Sweep.setupProblems(setupCfg), problems: Sweep.fieldProblems(fields) };
}

function previewLine(text, cls) {
  return el("p", { cls: cls || "", text: text });
}

function alertBox(kind, text) {
  const box = el("div", { cls: kind === "alert" ? "alert-box" : "warn-box" });
  box.append(el("b", { text: kind === "alert" ? "Warning" : "Note" }));
  box.append(document.createTextNode(text));
  return box;
}

function renderSweepPreview() {
  const req = sweepRequest();
  const warning = qs("#sweep-setup-warning");
  warning.replaceChildren();
  if (req.setupProblems.length > 0) {
    warning.append(el("b", { text: "Setup is not ready. " }));
    warning.append(document.createTextNode(
      "Go back to Setup and fix: " + req.setupProblems.join("; ") + "."));
    warning.classList.remove("hidden");
  } else {
    warning.classList.add("hidden");
  }
  const cfg = req.setupCfg;
  qs("#sweep-base").textContent = "Base from Setup: target " + cfg.target.provider + ":" + cfg.target.model +
    ", judge " + (cfg.judge.provider + ":" + cfg.judge.model) + ", canary " + (cfg.canary || "none");

  const box = qs("#sweep-preview");
  box.replaceChildren();
  const stop = req.setupProblems.concat(req.problems);
  if (stop.length > 0) {
    stop.forEach(function (p) { box.append(previewLine(p, "error")); });
    return;
  }
  if (sweepView.planError) {
    box.append(alertBox("alert", "The server did not accept this sweep: " + sweepView.planError));
    return;
  }
  const plan = sweepView.plan;
  if (!plan) {
    box.append(previewLine("Working out the cost...", "busy"));
    return;
  }
  // the warnings come first and are hard to miss: a turn limit below the corpus size means the
  // later vectors are never tried, and the sweep would read as clean when it never tried them
  Sweep.classifyWarnings(plan).forEach(function (w) {
    box.append(alertBox(w.severe ? "alert" : "note", w.text));
  });
  box.append(previewLine(Sweep.planSummary(plan), "big"));
  const spc = parseFloat(qs("#sweep-form").elements.sweep_spc.value);
  const secs = Sweep.estimateSeconds(plan.max_target_calls, spc);
  if (secs != null) {
    box.append(previewLine("Rough time, at most: " + Sweep.formatDuration(secs) + " (" + plan.max_target_calls +
      " calls x " + spc + " s). Real time depends on your hardware and on how early sessions end.", "muted"));
  }
  box.append(previewLine(plan.state === "resume"
    ? "This name already holds this sweep: it resumes and skips the runs on disk."
    : (plan.state === "different" ? "This name holds a different sweep." : "A new sweep."), "muted"));
}

async function refreshSweepPlan() {
  const req = sweepRequest();
  const mine = ++sweepView.token;
  if (req.setupProblems.length > 0 || req.problems.length > 0) {
    sweepView.plan = null;
    sweepView.planError = "";
    renderSweepPreview();
    return;
  }
  renderSweepPreview();
  try {
    const resp = await fetch("/sweeps/plan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Sweep.buildRequest(req.setupCfg, req.fields)),
    });
    const body = await resp.json().catch(function () { return {}; });
    if (mine !== sweepView.token) return;
    if (resp.ok) {
      sweepView.plan = body;
      sweepView.planError = "";
    } else {
      sweepView.plan = null;
      const detail = body.detail;
      sweepView.planError = typeof detail === "string" ? detail
        : (detail ? "the request is not valid (" + resp.status + ")" : "status " + resp.status);
    }
  } catch (e) {
    if (mine !== sweepView.token) return;
    sweepView.plan = null;
    sweepView.planError = "could not reach the backend";
  }
  renderSweepPreview();
}

function scheduleSweepPlan() {
  sweepView.plan = null;
  sweepView.planError = "";
  clearTimeout(sweepView.timer);
  sweepView.timer = setTimeout(refreshSweepPlan, 350);
  renderSweepPreview();
}

function openSweepView() {
  showView("sweep");
  scheduleSweepPlan();
}

// --- wiring ----------------------------------------------------------------

async function restoreBoard() {
  let list;
  try {
    const resp = await fetch("/runs");
    if (!resp.ok) return;
    list = (await resp.json()).runs || [];
  } catch (e) { return; }
  list.forEach(createRun);
  if (list.length > 0) showView("live");
}

document.addEventListener("DOMContentLoaded", function () {
  layoutPref = Layout.loadPreference(safeStorage());

  const form = qs("#run-form");
  form.addEventListener("submit", submitRun);
  buildModePicker(form);
  form.addEventListener("change", function () { updateOosWarning(form); syncModePicker(form); });
  updateOosWarning(form);

  qs("#new-run-btn").addEventListener("click", function () { showView("setup"); });
  qs("#stop-all-btn").addEventListener("click", function () {
    fetch("/runs/cancel-all", { method: "POST" });
  });
  document.querySelectorAll("#layout-toggle button").forEach(function (b) {
    b.addEventListener("click", function () { setLayout(b.dataset.layout); });
  });
  qs("#back-to-board").addEventListener("click", function () { showView("live"); });
  qs("#back-to-setup").addEventListener("click", function () { showView("setup"); });

  buildSweepForm();
  const sweepForm = qs("#sweep-form");
  sweepForm.addEventListener("input", scheduleSweepPlan);
  sweepForm.addEventListener("change", scheduleSweepPlan);
  sweepForm.addEventListener("submit", function (ev) { ev.preventDefault(); });
  qs("#sweep-tab").addEventListener("click", openSweepView);
  qs("#sweep-tab").addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openSweepView(); }
  });
  qs("#sweep-back").addEventListener("click", function () { showView("setup"); });

  renderBoard();
  restoreBoard();
});
