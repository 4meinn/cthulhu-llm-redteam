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

// turn badges: the same precedence the result files use
function main(turn) { return P.turnBadges(turn)[0]; }
assert.deepStrictEqual(main({ blocked: true }), { text: "blocked by guardrail", cls: "safe" });
assert.strictEqual(main({ error: true, violated: false }).cls, "error");
assert.strictEqual(main({ blocked: true, error: true }).cls, "safe");
assert.deepStrictEqual(main({ violated: true, proven: true, leaked: true }), { text: "objective met", cls: "finding" });
assert.deepStrictEqual(main({ violated: true, proven: false, leaked: false }), { text: "judge only, review", cls: "review" });
assert.strictEqual(main({ violated: true, leaked: true }).text, "objective met");     // no proven field: the old meaning
assert.strictEqual(main({ violated: true, leaked: false }).text, "judge only, review");
assert.strictEqual(main({ violated: false, leaked: true }).text, "canary leaked");
assert.strictEqual(main({ filtered: true, raw_leaked: true }).text, "caught by output filter");
assert.strictEqual(main({ filtered: true, raw_leaked: false }).text, "reply changed by filter");
assert.strictEqual(main({}).text, "held");
assert.strictEqual(main({ error: false, violated: false, leaked: false, filtered: false }).text, "held");

// judge only never looks like proven, and an error never looks like held
assert.notStrictEqual(main({ violated: true, proven: false }).cls, main({ violated: true, proven: true }).cls);
assert.notStrictEqual(main({ error: true }).text, main({}).text);

// a caught leak keeps its badge next to a higher ranked outcome
const both = P.turnBadges({ filtered: true, raw_leaked: true, violated: true, proven: false });
assert.deepStrictEqual(both.map(function (b) { return b.cls; }), ["review", "caught"]);
assert.strictEqual(P.turnBadges({ filtered: true, raw_leaked: true }).length, 1);
assert.strictEqual(P.turnBadges({ blocked: true, filtered: true }).length, 1);
assert.strictEqual(P.turnBadges({ error: true, filtered: true }).length, 1);

// notes
assert.ok(P.turnNote({ error: true }).indexOf("not scored") >= 0);
assert.ok(P.turnNote({ violated: true, proven: false }).indexOf("not counted as proven") >= 0);
assert.strictEqual(P.turnNote({ violated: true, proven: true }), "");
assert.strictEqual(P.turnNote({}), "");

console.log("panel tests ok");
