"use strict";

// Pure helpers for the guardrail mode in the panel. No DOM here, so Node can test
// them directly. The API keeps its two fields: guardrail (a second, guarded phase
// after the baseline) and guardrail_mode (what that phase runs).

(function (root) {
  const MODES = [
    { value: "none", label: "none", text: "Baseline only. The model alone, no guardrail." },
    { value: "input", label: "input", text: "Pattern filter on the attacker message plus a hardening reminder in the system prompt." },
    { value: "output", label: "output", text: "Checks the reply for the secret and redacts it. No input filter, no hardening." },
    { value: "both", label: "both", text: "The input filter and the output filter together." },
  ];
  const DEFAULT_MODE = "input";

  function normalizeMode(value) {
    return MODES.some(function (m) { return m.value === value; }) ? value : DEFAULT_MODE;
  }

  // none is a baseline-only run. Any other mode adds the guarded phase after the
  // baseline and runs it in that mode, so the run compares unguarded with guarded.
  function modeConfig(mode) {
    const m = normalizeMode(mode);
    if (m === "none") return { guardrail: false, guardrail_mode: "input" };
    return { guardrail: true, guardrail_mode: m };
  }

  // The mode a run was started with, read back from the run meta. Runs from before
  // the selector only carry the boolean, which meant the input filter.
  function modeFromMeta(meta) {
    meta = meta || {};
    if (!meta.guardrail) return "none";
    return normalizeMode(meta.guardrail_mode);
  }

  // How many phases the run has: the baseline, and the guarded one unless the mode is none.
  function phaseCount(mode) {
    return normalizeMode(mode) === "none" ? 1 : 2;
  }

  // A turn is proven when a deterministic check backs the violation. Events and reports
  // from before the field existed only know "violated", which then meant the canary leaked.
  function isProven(turn) {
    if (!turn.violated) return false;
    return turn.proven != null ? !!turn.proven : !!turn.leaked;
  }

  // What happened in one turn, as badges. The first is the outcome, in the same order the
  // result files use: blocked, error, objective met, judge only, canary leaked, filtered,
  // held. A turn the output filter changed keeps its own badge even when another outcome
  // ranks higher, so a caught leak is never hidden behind something else.
  function turnBadges(turn) {
    const caught = { text: "caught by output filter", cls: "caught" };
    const changed = { text: "reply changed by filter", cls: "changed" };
    const filterBadge = turn.raw_leaked ? caught : changed;
    let main;
    if (turn.blocked) main = { text: "blocked by guardrail", cls: "safe" };
    else if (turn.error) main = { text: "error", cls: "error" };
    else if (turn.violated && isProven(turn)) main = { text: "objective met", cls: "finding" };
    else if (turn.violated) main = { text: "judge only, review", cls: "review" };
    else if (turn.leaked) main = { text: "canary leaked", cls: "finding" };
    else if (turn.filtered) main = filterBadge;
    else main = { text: "held", cls: "muted" };
    const badges = [main];
    if (turn.filtered && main !== filterBadge && !turn.blocked && !turn.error) badges.push(filterBadge);
    return badges;
  }

  // A line under the turn when the badge alone could be misread.
  function turnNote(turn) {
    if (turn.error) return "The target call failed, so this turn was not scored. It is not a held turn.";
    if (turn.violated && !isProven(turn)) return "The judge flagged this and no deterministic check did, so it is not counted as proven.";
    return "";
  }

  const api = {
    isProven: isProven,
    turnBadges: turnBadges,
    turnNote: turnNote,
    MODES: MODES,
    DEFAULT_MODE: DEFAULT_MODE,
    normalizeMode: normalizeMode,
    modeConfig: modeConfig,
    modeFromMeta: modeFromMeta,
    phaseCount: phaseCount,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CthulhuPanel = api;
})(typeof window !== "undefined" ? window : this);
