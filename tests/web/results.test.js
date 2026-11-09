"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const R = require("../../cthulhu/web/static/results.js");

function load(name) {
  return JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", name), "utf8"));
}
const withOutput = load("matrix-output.json");     // a mock sweep over none, input, output and both
const smoke = load("matrix-smoke.json");           // real runs, none and input only

function texts(table) {
  const out = table.head.slice();
  table.rows.forEach(function (r) { r.cells.forEach(function (c) { out.push(c.text); }); });
  return out;
}

// numbers and labels
assert.strictEqual(R.pct(0.19), "19.0%");
assert.strictEqual(R.pct(null), "n/a");
assert.strictEqual(R.fraction(55, 290, 0.1897), "55/290 (19.0%)");
assert.strictEqual(R.fraction(0, 0, null), "0/0");
assert.strictEqual(R.ciText([0.149, 0.239]), "14.9% to 23.9%");
assert.strictEqual(R.ciText(null), "");
assert.strictEqual(R.lowN(9), true);
assert.strictEqual(R.lowN(10), false);
assert.strictEqual(R.lowN(0), false);
assert.strictEqual(R.configLabel({ target: "ollama:qwen3:8b", guardrail: "both", strategy: "corpus", attacker_model: "", mode: "campaign" }),
  "ollama:qwen3:8b, guardrail both, corpus, campaign");
assert.ok(R.configLabel({ target: "t", guardrail: "none", strategy: "adaptive", attacker_model: "ollama:d", mode: "campaign" }).indexOf("attacker ollama:d") > 0);

// the ASR table: proven and judge only apart, caught beside filtered, an interval on every scored row
const asr = R.asrTable(withOutput.asr, "config");
assert.deepStrictEqual(asr.head, ["Config", "Turns (n)", "Proven", "95% CI", "Judge only", "Blocked", "Filtered", "Caught", "Errors", "Sessions"]);
assert.strictEqual(asr.rows.length, 8);
const byGuardrail = {};
asr.rows.forEach(function (r) { byGuardrail[r.cells[0].text] = r.cells; });
const outIso = byGuardrail["mock:qwen3:8b, guardrail output, corpus, isolated"];
assert.strictEqual(outIso[2].text, "0/29 (0.0%)");                    // proven is 0 ...
assert.ok(/^\d+$/.test(outIso[7].text) && Number(outIso[7].text) > 0);  // ... and caught is not
assert.strictEqual(outIso[7].cls, "num caught");
assert.ok(outIso[3].text.indexOf(" to ") > 0);                         // the interval is shown, not just 0%
assert.strictEqual(outIso[4].text, "0/29 (0.0%)");                    // judge only is its own column
const noneIso = byGuardrail["mock:qwen3:8b, guardrail none, corpus, isolated"];
assert.ok(noneIso[2].text.indexOf("22/29") === 0);
assert.strictEqual(noneIso[7].cls, "num");                            // nothing caught without an output stage

// order: guardrail none, input, output, both
const order = asr.rows.map(function (r) { return r.cells[0].text.split(",")[1].trim(); });
assert.deepStrictEqual(order.filter(function (g, i) { return order.indexOf(g) === i; }),
  ["guardrail none", "guardrail input", "guardrail output", "guardrail both"]);

// a small n gets the dagger and the low mark, a large one does not
const small = R.asrTable(withOutput.asr, "config").rows.filter(function (r) { return r.cells[1].text.indexOf(R.MARK) >= 0; });
assert.ok(small.length >= 1 && small.every(function (r) { return r.cls === "low"; }));
assert.ok(small.every(function (r) { return parseInt(r.cells[1].text, 10) < 10; }));
assert.ok(asr.rows.filter(function (r) { return parseInt(r.cells[1].text, 10) >= 10; }).every(function (r) { return r.cells[1].text.indexOf(R.MARK) < 0; }));

// category and vector levels carry their own column
assert.strictEqual(R.asrTable(withOutput.asr, "category").head[1], "Category");
assert.strictEqual(R.asrTable(withOutput.asr, "vector").head[1], "Vector");
assert.ok(R.asrTable(withOutput.asr, "vector").rows.length > R.asrTable(withOutput.asr, "category").rows.length);
assert.deepStrictEqual(R.asrTable({}, "config").rows, []);
assert.deepStrictEqual(R.asrTable(null, "vector").rows, []);

// a row with no scored turn says "no result", it does not show 0%
const empty = R.asrCells({ key: { target: "t", guardrail: "none", strategy: "corpus", attacker_model: "", mode: "campaign" },
  n: 0, proven: 0, judge_only: 0, blocked: 0, filtered: 0, caught: 0, error: 3, sessions: 1, sessions_proven: 0, sessions_judge_only: 0 }, "config");
assert.strictEqual(empty[2].text, "no result");
assert.strictEqual(empty[8].cls, "num err");

// the output box shows when an output stage is in the data, and only then
const viewOut = R.matrixView(withOutput);
assert.strictEqual(viewOut.outputBox, R.OUTPUT_BOX);
assert.ok(R.OUTPUT_BOX.indexOf("by construction") >= 0 && R.OUTPUT_BOX.indexOf("does not mean the target is safe") >= 0);
assert.strictEqual(R.matrixView(smoke).outputBox, "");
assert.strictEqual(R.matrixView(smoke).empty, false);

// the defence matrix: the server's own cell text, a column per mode, a delta per guarded mode
const def = viewOut.defence[0];
assert.deepStrictEqual(def.head.slice(0, 5), ["Category", "none", "input", "output", "both"]);
assert.deepStrictEqual(def.head.slice(5), ["input vs none", "output vs none", "both vs none"]);
assert.ok(def.title.indexOf("(isolated)") > 0);
const outCol = def.head.indexOf("output");
assert.ok(def.rows.every(function (r) { return /^\d+\/\d+ \(\d+\.\d%\)/.test(r.cells[outCol].text); }));
assert.ok(def.rows.some(function (r) { return /\(\d+ caught\)/.test(r.cells[outCol].text); }));   // caught is in the cell
assert.ok(def.rows.filter(function (r) { return r.cells[0].text !== "all"; })                      // n of 5 or 6 per category: dagger
  .every(function (r) { return r.cells[outCol].text.indexOf(R.MARK) >= 0; }));
assert.ok(def.rows.filter(function (r) { return r.cells[0].text === "all"; })                      // n of 29: no dagger
  .every(function (r) { return r.cells[outCol].text.indexOf(R.MARK) < 0; }));
const deltas = [];
def.rows.forEach(function (r) { r.cells.slice(5).forEach(function (c) { deltas.push(c); }); });
assert.ok(deltas.every(function (c) { return /intervals (apart|overlap)/.test(c.text) || c.text === "n/a"; }));
assert.ok(deltas.every(function (c) { return (c.cls === "apart") === (c.text.indexOf("intervals apart") >= 0) || c.text === "n/a"; }));
assert.ok(deltas.some(function (c) { return c.text.indexOf("overlap, unclear") >= 0; }) || deltas.every(function (c) { return c.cls === "apart"; }));

// a cell with no run says "not run", which is not the same as 0
const sparse = R.defenceTable({ target: "t", view: "vector", guardrails: ["none", "input"],
  rows: [{ label: "di-001", cells: { none: { text: "1/1 (100.0%) †", low_n: true }, input: null }, deltas: { input: null } }] });
assert.strictEqual(sparse.rows[0].cells[2].text, "not run");
assert.strictEqual(sparse.rows[0].cells[3].text, "n/a");
assert.strictEqual(sparse.rows[0].cells[1].cls, "low");
assert.strictEqual(sparse.head[0], "Vector");
const noBase = R.defenceTable({ target: "t", view: "vector", guardrails: ["input", "output"], rows: [] });
assert.deepStrictEqual(noBase.head, ["Vector", "input", "output"]);        // no unguarded column, no "vs none"

// context and session tables
assert.ok(viewOut.context.length >= 1);
const ctx = viewOut.context[0];
assert.deepStrictEqual(ctx.head, ["Vector", "Isolated", "Campaign tried", "Campaign won", "Note"]);
assert.ok(ctx.title.indexOf("guardrail") > 0 && ctx.rows.length > 0);
assert.ok(viewOut.contextNote.length > 0);
assert.ok(viewOut.session.head.length === 1 + 4);                          // attacker + the four defenders
assert.ok(viewOut.session.rows[0].cells.every(function (c) { return c.text.length > 0; }));
assert.strictEqual(R.sessionTable(null), null);
assert.strictEqual(R.matrixView({ matrix: { empty: true } }).empty, true);
assert.strictEqual(R.matrixView(null).empty, true);

// the reading notes come with the data, intervals apart and overlap among them
assert.ok(viewOut.notes.some(function (n) { return n.indexOf("not a significance test") >= 0; }));
assert.ok(viewOut.notes.some(function (n) { return n.indexOf("does not mean there is no difference") >= 0; }));

// nothing in the Results text calls a zero safe
const everything = [R.OUTPUT_BOX].concat(texts(asr), texts(def), texts(ctx), viewOut.notes).join("\n");
assert.ok(!/\b(is safe|are safe|secure|protected)\b/i.test(everything.replace("does not mean the target is safe", "")));

// partial data is named
assert.strictEqual(R.partialBanner({ partial: false, runs: 4 }, "done"), "");
assert.ok(R.partialBanner({ partial: true, runs: 7 }, "running").indexOf("7 finished run(s)") >= 0);
assert.ok(R.partialBanner({ partial: false, runs: 7 }, "running").indexOf("Partial data") === 0);
assert.strictEqual(R.partialBanner(null, "running"), "");

// the selector label
assert.strictEqual(R.sweepOption({ id: "night-1", live: false, status: "idle", done: 80, total: 80 }), "night-1 (on disk, 80 of 80 runs)");
assert.strictEqual(R.sweepOption({ id: "x", live: true, status: "running", done: 3, total: 9 }), "x (running, 3 of 9 runs)");

console.log("results tests ok");
