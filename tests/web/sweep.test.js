"use strict";

const assert = require("assert");
const S = require("../../cthulhu/web/static/sweep.js");

// targets: one per line, provider:model, repeats dropped, bad lines reported
assert.deepStrictEqual(S.parseTargets("ollama:qwen3:8b\n  mock:x \n\nollama:qwen3:8b"),
  { targets: ["ollama:qwen3:8b", "mock:x"], errors: [] });
assert.deepStrictEqual(S.parseTargets("").targets, []);
assert.strictEqual(S.parseTargets("qwen3").errors.length, 1);
assert.strictEqual(S.parseTargets("bogus:model").errors.length, 1);
assert.strictEqual(S.parseTargets("ollama:").errors.length, 1);
assert.strictEqual(S.parseTargets("ollama:ok\nbad").targets.length, 1);

// the Setup form must be filled: prompt text, a target model, a fixed canary
const good = { system_prompt_text: "code {{CANARY}}", target: { model: "qwen3:8b" }, canary: "NW-1" };
assert.deepStrictEqual(S.setupProblems(good), []);
assert.strictEqual(S.setupProblems(Object.assign({}, good, { system_prompt_text: "  " })).length, 1);
assert.strictEqual(S.setupProblems(Object.assign({}, good, { canary: null })).length, 1);
assert.strictEqual(S.setupProblems({ system_prompt_text: "", target: { model: "" }, canary: null }).length, 3);
assert.ok(S.setupProblems(null).length >= 1);

// the sweep's own fields
const fields = { name: "night 1", guardrails: ["none", "output"], modes: ["isolated"], objectives: ["leak_canary"],
                 repeats: 3, max_turns: 30, workers: 1, strategy: "corpus", judge_mode: "on_suspicion", targets: "" };
assert.deepStrictEqual(S.fieldProblems(fields), []);
assert.ok(S.fieldProblems(Object.assign({}, fields, { name: "---" })).length === 1);
assert.ok(S.fieldProblems(Object.assign({}, fields, { guardrails: [] })).length === 1);
assert.ok(S.fieldProblems(Object.assign({}, fields, { modes: [] })).length === 1);
assert.ok(S.fieldProblems(Object.assign({}, fields, { objectives: [] })).length === 1);
assert.ok(S.fieldProblems(Object.assign({}, fields, { repeats: 0 })).length === 1);
assert.ok(S.fieldProblems(Object.assign({}, fields, { max_turns: NaN })).length === 1);
assert.ok(S.fieldProblems(Object.assign({}, fields, { workers: 5 })).length === 1);
assert.ok(S.fieldProblems(Object.assign({}, fields, { workers: 4 })).length === 0);
assert.ok(S.fieldProblems(Object.assign({}, fields, { strategy: "adaptive" })).length === 1);          // adaptive cannot be isolated
assert.ok(S.fieldProblems(Object.assign({}, fields, { strategy: "adaptive", modes: ["campaign"] })).length === 0);
assert.ok(S.fieldProblems(Object.assign({}, fields, { targets: "nope" })).length === 1);

// the request: Setup is the base, the sweep sets its own turn limit, judge mode and strategy
const setup = { target: { provider: "mock", model: "m" }, system_prompt_text: "p", canary: "NW-1",
                attacker: { strategy: "adaptive", max_turns: 6, objectives: ["leak_canary"], judge_mode: "per_turn" } };
const req = S.buildRequest(setup, fields);
assert.strictEqual(req.name, "night 1");
assert.deepStrictEqual(req.grid, { guardrail: ["none", "output"], mode: ["isolated"], objective: ["leak_canary"], strategy: ["corpus"] });
assert.deepStrictEqual([req.base.attacker.max_turns, req.base.attacker.judge_mode, req.base.attacker.strategy], [30, "on_suspicion", "corpus"]);
assert.strictEqual(setup.attacker.max_turns, 6);                                  // the Setup config is not changed
assert.deepStrictEqual([req.repeats, req.workers], [3, 1]);
assert.ok(!("target" in req.grid));                                               // empty: the Setup target
assert.deepStrictEqual(S.buildRequest(setup, Object.assign({}, fields, { targets: "ollama:a\nollama:b" })).grid.target, ["ollama:a", "ollama:b"]);
req.grid.guardrail.push("both");
assert.strictEqual(fields.guardrails.length, 2);                                  // the form's arrays are copied

// the estimate and how it reads
assert.strictEqual(S.estimateSeconds(100, 13), 1300);
assert.strictEqual(S.estimateSeconds(100, 0), null);
assert.strictEqual(S.estimateSeconds(-1, 13), null);
assert.strictEqual(S.formatDuration(45), "45 s");
assert.strictEqual(S.formatDuration(600), "10 min");
assert.strictEqual(S.formatDuration(22320), "6.2 h");
assert.strictEqual(S.formatDuration(null), "");

// warnings: the max_turns trap is severe, an existing different sweep is severe
const trap = "1 campaign cell(s) use the corpus with max_turns 6, below the corpus size 29: the vectors past turn 6 are never tried";
const warned = S.classifyWarnings({ warnings: [trap, "other"], state: "new" });
assert.deepStrictEqual(warned.map(function (w) { return w.severe; }), [true, false]);
assert.strictEqual(S.classifyWarnings({ warnings: [], state: "different" })[0].severe, true);
assert.deepStrictEqual(S.classifyWarnings({ warnings: [], state: "resume" }), []);
assert.deepStrictEqual(S.classifyWarnings(null), []);

// Start is allowed only when the form is complete, the plan came back, and nothing collides
const plan = { n_cells: 4, repeats: 2, runs: 8, max_target_calls: 200, on_disk: 0, pending: 8, state: "new", warnings: [] };
assert.strictEqual(S.canStart([], plan, null, false), true);
assert.strictEqual(S.canStart(["x"], plan, null, false), false);
assert.strictEqual(S.canStart([], null, null, false), false);
assert.strictEqual(S.canStart([], plan, "bad", false), false);
assert.strictEqual(S.canStart([], plan, null, true), false);
assert.strictEqual(S.canStart([], Object.assign({}, plan, { state: "different" }), null, false), false);
assert.strictEqual(S.canStart([], Object.assign({}, plan, { state: "resume" }), null, false), true);
assert.strictEqual(S.canStart([], Object.assign({}, plan, { runs: 0 }), null, false), false);
assert.ok(S.planSummary(plan).indexOf("4 cell(s) x 2 repeat(s) = 8 run(s)") === 0);
assert.ok(S.planSummary(Object.assign({}, plan, { on_disk: 3, pending: 5 })).indexOf("3 already on disk, 5 to run") > 0);

// following a sweep from its events
let st = S.newState({ status: "running", total: 4, done: 0, skipped: 1, pending: 3, failed: {}, active: [] });
assert.strictEqual(S.fraction(st), 0.25);
st = S.applyEvent(st, { kind: "run_started", run_id: "a", done: 0, failed: 0, skipped: 1, pending: 3 });
assert.deepStrictEqual(st.active, ["a"]);
st = S.applyEvent(st, { kind: "run_done", run_id: "a", done: 1, failed: 0, skipped: 1, pending: 3 });
assert.deepStrictEqual([st.active, st.done, st.last], [[], 1, "a done"]);
assert.strictEqual(S.fraction(st), 0.5);
st = S.applyEvent(st, { kind: "run_failed", run_id: "b", done: 1, failed: 1, skipped: 1, pending: 3 });
assert.deepStrictEqual([st.failed, st.last, S.fraction(st)], [1, "b failed", 0.75]);
const before = st;
S.applyEvent(st, { kind: "run_started", run_id: "c" });
assert.deepStrictEqual(before.active, []);                                        // the old state is not changed
st = S.applyEvent(st, { kind: "sweep_finished", status: "cancelled" });
assert.deepStrictEqual([st.status, S.isTerminal(st.status)], ["cancelled", true]);
assert.strictEqual(S.fraction(Object.assign({}, st, { status: "done" })), 1);
assert.strictEqual(S.fraction(S.newState({ total: 0 })), 0);
assert.deepStrictEqual(S.newState({ active: ["x"], failed: { a: "why" } }).failed, 1);
assert.strictEqual(S.isTerminal("running"), false);

console.log("sweep tests ok");
