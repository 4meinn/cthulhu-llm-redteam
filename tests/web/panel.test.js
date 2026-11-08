"use strict";

const assert = require("assert");
const P = require("../../cthulhu/web/static/panel.js");

// the four modes, in order, with a one line explanation each
assert.deepStrictEqual(P.MODES.map(function (m) { return m.value; }), ["none", "input", "output", "both"]);
P.MODES.forEach(function (m) { assert.ok(m.text.length > 10 && m.text.indexOf("<") < 0); });
assert.strictEqual(P.DEFAULT_MODE, "input");

// none is a baseline-only run, the rest add the guarded phase in their own mode
assert.deepStrictEqual(P.modeConfig("none"), { guardrail: false, guardrail_mode: "input" });
assert.deepStrictEqual(P.modeConfig("input"), { guardrail: true, guardrail_mode: "input" });
assert.deepStrictEqual(P.modeConfig("output"), { guardrail: true, guardrail_mode: "output" });
assert.deepStrictEqual(P.modeConfig("both"), { guardrail: true, guardrail_mode: "both" });
assert.deepStrictEqual(P.modeConfig("bogus"), { guardrail: true, guardrail_mode: "input" });
assert.deepStrictEqual(P.modeConfig(undefined), { guardrail: true, guardrail_mode: "input" });

// reading the mode back from a run's meta
assert.strictEqual(P.modeFromMeta({ guardrail: false, guardrail_mode: "output" }), "none");
assert.strictEqual(P.modeFromMeta({ guardrail: true, guardrail_mode: "output" }), "output");
assert.strictEqual(P.modeFromMeta({ guardrail: true }), "input");
assert.strictEqual(P.modeFromMeta({}), "none");
assert.strictEqual(P.modeFromMeta(null), "none");

// phases: baseline only for none, baseline and guarded otherwise
assert.strictEqual(P.phaseCount("none"), 1);
assert.strictEqual(P.phaseCount("input"), 2);
assert.strictEqual(P.phaseCount("both"), 2);

// the choice survives a round trip through the config
P.MODES.forEach(function (m) {
  const cfg = P.modeConfig(m.value);
  assert.strictEqual(P.modeFromMeta(cfg), m.value);
  assert.strictEqual(P.phaseCount(m.value), cfg.guardrail ? 2 : 1);
});

console.log("panel tests ok");
