"use strict";

// Pure helpers for the Sweep view. No DOM here, so Node can test them directly: building the
// request, checking the form, reading the plan the server sends back, and following progress.

(function (root) {
  const GUARDRAILS = ["none", "input", "output", "both"];
  const MODES = ["isolated", "campaign"];
  const OBJECTIVES = ["leak_canary", "leak_system_prompt", "out_of_scope"];
  const PROVIDERS = ["mock", "ollama", "openai"];
  const MAX_WORKERS = 4;
  const DEFAULT_SECONDS_PER_CALL = 13;      // measured on one 8 GB GPU with model swaps, rough

  // One target per line, provider:model. A repeat is dropped, a bad line is reported.
  function parseTargets(text) {
    const targets = [];
    const errors = [];
    String(text || "").split("\n").forEach(function (raw) {
      const line = raw.trim();
      if (!line) return;
      const cut = line.indexOf(":");
      const provider = cut > 0 ? line.slice(0, cut) : "";
      const model = cut > 0 ? line.slice(cut + 1).trim() : "";
      if (PROVIDERS.indexOf(provider) < 0 || !model) {
        errors.push("'" + line + "' is not provider:model with provider one of " + PROVIDERS.join(", "));
      } else if (targets.indexOf(provider + ":" + model) < 0) {
        targets.push(provider + ":" + model);
      }
    });
    return { targets: targets, errors: errors };
  }

  // What is wrong with the Setup form, which the sweep takes as its base. A sweep needs the
  // system prompt as text and a fixed canary, so every run uses the same secret.
  function setupProblems(cfg) {
    const problems = [];
    if (!cfg || !String(cfg.system_prompt_text || "").trim()) problems.push("the system prompt is empty");
    if (cfg && cfg.target && !String(cfg.target.model || "").trim()) problems.push("the target model is empty");
    if (cfg && !cfg.canary) problems.push("a sweep needs a canary, so every run uses the same secret");
    return problems;
  }

  // What is wrong with the sweep's own fields. Each problem is one short sentence.
  function fieldProblems(f) {
    const problems = [];
    if (!/[a-z0-9]/i.test(String(f.name || ""))) problems.push("give the sweep a name with letters or digits");
    if (!f.guardrails || f.guardrails.length === 0) problems.push("pick at least one guardrail mode");
    if (!f.modes || f.modes.length === 0) problems.push("pick at least one mode");
    if (!f.objectives || f.objectives.length === 0) problems.push("pick at least one objective");
    if (!(f.repeats >= 1)) problems.push("repeats must be 1 or more");
    if (!(f.max_turns >= 1)) problems.push("max turns must be 1 or more");
    if (!(f.workers >= 1 && f.workers <= MAX_WORKERS)) problems.push("workers must be 1 to " + MAX_WORKERS);
    if (f.strategy === "adaptive" && f.modes && f.modes.indexOf("isolated") >= 0) {
      problems.push("isolated mode needs the corpus strategy");
    }
    (parseTargets(f.targets).errors).forEach(function (e) { problems.push(e); });
    return problems;
  }

  // The body for POST /sweeps and /sweeps/plan. The base is the Setup config with the sweep's
  // own turn limit and judge mode. An empty target list takes the Setup target.
  function buildRequest(setupCfg, f) {
    const base = JSON.parse(JSON.stringify(setupCfg));
    base.attacker = Object.assign({}, base.attacker, {
      max_turns: f.max_turns, judge_mode: f.judge_mode, strategy: f.strategy,
    });
    const grid = { guardrail: f.guardrails.slice(), mode: f.modes.slice(), objective: f.objectives.slice(),
                   strategy: [f.strategy] };
    const targets = parseTargets(f.targets).targets;
    if (targets.length > 0) grid.target = targets;
    return { name: f.name.trim(), base: base, repeats: f.repeats, workers: f.workers, grid: grid };
  }

  function estimateSeconds(calls, secondsPerCall) {
    if (!(calls >= 0) || !(secondsPerCall > 0)) return null;
    return calls * secondsPerCall;
  }

  function formatDuration(seconds) {
    if (seconds == null || !isFinite(seconds)) return "";
    if (seconds < 90) return Math.max(1, Math.round(seconds)) + " s";
    if (seconds < 5400) return Math.round(seconds / 60) + " min";
    return (seconds / 3600).toFixed(1) + " h";
  }

  // The plan the server returns, as lines for the preview. A max_turns warning is severe: with a
  // turn limit below the corpus size the later vectors are never tried, and a first run read
  // as "held" when the attack that breaks the target had never been sent.
  function classifyWarnings(plan) {
    const out = [];
    ((plan && plan.warnings) || []).forEach(function (text) {
      out.push({ text: text, severe: /max_turns/.test(text) });
    });
    if (plan && plan.state === "different") {
      out.push({ severe: true, text: "A sweep with this name already exists and was made from a different plan. " +
                                    "Use another name, or delete its folder to start over." });
    }
    return out;
  }

  // Whether Start is allowed: the form is complete, the server accepted the plan, nothing is
  // running, and the plan does not collide with a different sweep of the same name.
  function canStart(problems, plan, planError, busy) {
    if (busy || problems.length > 0 || planError || !plan) return false;
    return plan.state !== "different" && plan.runs > 0;
  }

  function planSummary(plan) {
    const bits = [plan.n_cells + " cell(s) x " + plan.repeats + " repeat(s) = " + plan.runs + " run(s)",
                  "up to " + plan.max_target_calls + " target call(s)"];
    if (plan.on_disk > 0) bits.push(plan.on_disk + " already on disk, " + plan.pending + " to run");
    return bits.join(", ");
  }

  // Following a running sweep. The server puts the counters on every event, so the state is
  // taken from the last event and only the list of active runs is kept here.
  function newState(info) {
    info = info || {};
    return {
      status: info.status || "running", total: info.total || 0, done: info.done || 0,
      failed: Object.keys(info.failed || {}).length, skipped: info.skipped || 0,
      pending: info.pending || 0, active: (info.active || []).slice(), last: "",
    };
  }

  function applyEvent(state, ev) {
    const next = Object.assign({}, state, { active: state.active.slice() });
    if (ev.done != null) next.done = ev.done;
    if (ev.failed != null) next.failed = ev.failed;
    if (ev.skipped != null) next.skipped = ev.skipped;
    if (ev.pending != null) next.pending = ev.pending;
    if (ev.kind === "run_started") next.active.push(ev.run_id);
    if (ev.kind === "run_done" || ev.kind === "run_failed") {
      next.active = next.active.filter(function (id) { return id !== ev.run_id; });
      next.last = ev.run_id + (ev.kind === "run_failed" ? " failed" : " done");
    }
    if (ev.kind === "sweep_finished") { next.status = ev.status; next.active = []; }
    return next;
  }

  // Runs finished, failed or skipped over every run of the grid.
  function fraction(state) {
    if (state.status === "done") return 1;
    if (!(state.total > 0)) return 0;
    const settled = state.done + state.failed + state.skipped;
    return Math.min(Math.max(settled / state.total, 0), 1);
  }

  function isTerminal(status) { return status === "done" || status === "cancelled" || status === "error"; }

  const api = {
    GUARDRAILS: GUARDRAILS, MODES: MODES, OBJECTIVES: OBJECTIVES, PROVIDERS: PROVIDERS,
    MAX_WORKERS: MAX_WORKERS, DEFAULT_SECONDS_PER_CALL: DEFAULT_SECONDS_PER_CALL,
    parseTargets: parseTargets, setupProblems: setupProblems, fieldProblems: fieldProblems,
    buildRequest: buildRequest, estimateSeconds: estimateSeconds, formatDuration: formatDuration,
    classifyWarnings: classifyWarnings, canStart: canStart, planSummary: planSummary,
    newState: newState, applyEvent: applyEvent, fraction: fraction, isTerminal: isTerminal,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CthulhuSweep = api;
})(typeof window !== "undefined" ? window : this);
