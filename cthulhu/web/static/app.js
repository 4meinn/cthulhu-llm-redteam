"use strict";

// Every piece of text that comes from the API (model replies, judge reasons,
// tactics, notes) is written with textContent, never innerHTML. A jailbroken
// target can return markup, and the tool must never run it.

function el(tag, opts) {
  opts = opts || {};
  const node = document.createElement(tag);
  if (opts.cls) node.className = opts.cls;
  if (opts.text != null) node.textContent = String(opts.text);
  return node;
}

function qs(sel) { return document.querySelector(sel); }

const views = { setup: qs("#setup"), live: qs("#live"), report: qs("#report") };
let currentRun = null;
let liveSource = null;

function showView(name) {
  for (const key in views) views[key].classList.toggle("hidden", key !== name);
  document.querySelectorAll("#steps span").forEach(function (s) {
    s.classList.toggle("active", s.dataset.step === name);
  });
}

// --- setup -----------------------------------------------------------------

function buildConfig(form) {
  const val = function (name) { return form.elements[name].value; };
  const objectives = Array.prototype.map.call(
    form.querySelectorAll('input[name="objective"]:checked'), function (i) { return i.value; });
  const canary = val("canary").trim();
  return {
    target: { provider: val("target_provider"), base_url: val("target_base_url"), model: val("target_model") },
    judge: { provider: val("judge_provider"), base_url: val("judge_base_url"), model: val("judge_model") },
    system_prompt_text: val("system_prompt"),
    canary: canary || null,
    guardrail: form.elements.guardrail.checked,
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
  startLive(data.run_id);
}

// --- live ------------------------------------------------------------------

function outcome(ev) {
  if (ev.blocked) return { text: "blocked by guardrail", cls: "safe" };
  if (ev.violated) return { text: "objective met", cls: "finding" };
  if (ev.leaked) return { text: "canary leaked", cls: "finding" };
  return { text: "held", cls: "muted" };
}

function startLive(runId) {
  currentRun = runId;
  showView("live");
  qs("#live-log").replaceChildren();
  qs("#live-status").textContent = "running";
  qs("#to-report-btn").classList.add("hidden");
  if (liveSource) liveSource.close();
  liveSource = new EventSource("/runs/" + encodeURIComponent(runId) + "/events");
  liveSource.onmessage = function (e) { handleEvent(JSON.parse(e.data)); };
  liveSource.onerror = function () { /* the stream closes itself on run_finished */ };
}

function handleEvent(ev) {
  const log = qs("#live-log");
  if (ev.kind === "session_start") {
    log.append(el("div", { cls: "sess", text: ev.phase + " / " + ev.objective }));
  } else if (ev.kind === "turn") {
    const o = outcome(ev);
    const row = el("div", { cls: "turn" });
    row.append(el("span", { cls: "tnum", text: ev.phase + " t" + ev.turn }));
    row.append(el("span", { cls: "tactic", text: ev.tactic || "" }));
    row.append(el("span", { cls: "tag " + o.cls, text: o.text }));
    if (!ev.blocked && ev.response) row.append(el("div", { cls: "reply", text: ev.response }));
    if (ev.reason) row.append(el("div", { cls: "reason", text: "judge: " + ev.reason }));
    log.append(row);
  } else if (ev.kind === "cancelled") {
    log.append(el("div", { cls: "note", text: "cancelled" }));
  } else if (ev.kind === "error") {
    log.append(el("div", { cls: "error", text: ev.message || "error" }));
  } else if (ev.kind === "run_finished") {
    if (liveSource) liveSource.close();
    qs("#live-status").textContent = ev.status;
    qs("#to-report-btn").classList.remove("hidden");
    loadReport(currentRun);
  }
  log.scrollTop = log.scrollHeight;
}

// --- report ----------------------------------------------------------------

async function loadReport(runId) {
  const resp = await fetch("/runs/" + encodeURIComponent(runId) + "/report");
  if (!resp.ok) return;
  renderReport(await resp.json());
  qs("#md-link").href = "/runs/" + encodeURIComponent(runId) + "/report.md";
}

function sessionsByObjective(sessions) {
  const map = {};
  (sessions || []).forEach(function (s) { map[s.objective.id] = s; });
  return map;
}

function metLabel(session) {
  if (!session || !session.success) return "held";
  return (session.needs_review ? "met (review)" : "met") + " @ turn " + session.success_turn;
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
    "guardrail " + (guarded ? "on" : "off"),
  ];
  if (meta.judge_agreement != null) metaLine.push("agreement " + Math.round(meta.judge_agreement * 100) + "%");
  body.append(el("p", { cls: "meta", text: metaLine.join("  |  ") }));

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
  const review = [];
  phases.forEach(function (p) {
    (p[1] || []).forEach(function (s) { if (s.needs_review) review.push([p[0], s]); });
  });
  body.append(el("h2", { text: "Needs review (judge only)" }));
  if (review.length === 0) {
    body.append(el("p", { cls: "muted", text: "None. Every success had a deterministic signal." }));
  } else {
    review.forEach(function (r) {
      body.append(el("div", { cls: "review-line", text:
        r[1].objective.id + " (" + r[0] + ", turn " + r[1].success_turn + ")" }));
    });
  }

  // transcripts
  body.append(el("h2", { text: "Transcripts" }));
  phases.forEach(function (p) {
    (p[1] || []).forEach(function (s) {
      body.append(el("h3", { text: s.objective.id + " (" + p[0] + ")" }));
      s.turns.forEach(function (t, i) {
        const o = outcome(t);
        const turn = el("div", { cls: "turn" });
        turn.append(el("span", { cls: "tnum", text: "t" + (i + 1) }));
        turn.append(el("span", { cls: "tactic", text: t.tactic || "" }));
        turn.append(el("span", { cls: "tag " + o.cls, text: o.text }));
        turn.append(el("div", { cls: "sent", text: "sent: " + t.sent }));
        if (!t.blocked) turn.append(el("div", { cls: "reply", text: "reply: " + t.response }));
        if (t.note) turn.append(el("div", { cls: "reason", text: t.note }));
        body.append(turn);
      });
    });
  });
}

// --- wiring ----------------------------------------------------------------

document.addEventListener("DOMContentLoaded", function () {
  const form = qs("#run-form");
  form.addEventListener("submit", submitRun);
  form.addEventListener("change", function () { updateOosWarning(form); });
  updateOosWarning(form);

  qs("#cancel-btn").addEventListener("click", function () {
    if (currentRun) fetch("/runs/" + encodeURIComponent(currentRun) + "/cancel", { method: "POST" });
  });
  qs("#to-report-btn").addEventListener("click", function () { showView("report"); });
  qs("#back-to-setup").addEventListener("click", function () { showView("setup"); });
});
