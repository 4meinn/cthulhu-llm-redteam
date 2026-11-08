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

  const api = {
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
