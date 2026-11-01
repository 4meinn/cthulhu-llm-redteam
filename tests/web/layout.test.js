"use strict";

const assert = require("assert");
const L = require("../../cthulhu/web/static/layout.js");

// layout choice
assert.strictEqual(L.chooseLayout(0, "A"), "single");
assert.strictEqual(L.chooseLayout(1, "A"), "single");
assert.strictEqual(L.chooseLayout(2, "A"), "A");
assert.strictEqual(L.chooseLayout(2, "C"), "C");
assert.strictEqual(L.chooseLayout(5, undefined), "C");
assert.strictEqual(L.chooseLayout(3, "grid"), "C");
assert.strictEqual(L.chooseLayout(3, null), "C");

// remembered choice
function fakeStorage() {
  const data = {};
  return {
    getItem: function (k) { return k in data ? data[k] : null; },
    setItem: function (k, v) { data[k] = String(v); },
  };
}
const store = fakeStorage();
assert.strictEqual(L.loadPreference(store), "C");
assert.strictEqual(L.savePreference(store, "A"), true);
assert.strictEqual(L.loadPreference(store), "A");
L.savePreference(store, "nonsense");
assert.strictEqual(L.loadPreference(store), "C");

// storage that throws must not break the board
const broken = {
  getItem: function () { throw new Error("blocked"); },
  setItem: function () { throw new Error("blocked"); },
};
assert.strictEqual(L.loadPreference(broken), "C");
assert.strictEqual(L.savePreference(broken, "A"), false);

// progress: 2 objectives x 2 phases = 4 sessions of up to 6 turns
const plan = { objectives: 2, phases: 2, max_turns: 6 };
assert.strictEqual(L.progress(plan, "running", 0, 0), 0);
assert.strictEqual(L.progress(plan, "running", 0, 3), 0.125);   // half a session of four
assert.strictEqual(L.progress(plan, "running", 2, 0), 0.5);
assert.strictEqual(L.progress(plan, "running", 3, 6), 0.75 + 0.25);
assert.strictEqual(L.progress(plan, "running", 4, 6), 1);       // never above one
assert.strictEqual(L.progress(plan, "done", 1, 1), 1);
assert.strictEqual(L.progress(plan, "cancelled", 1, 1), 1);
assert.strictEqual(L.progress({}, "running", 0, 3), 0);         // no plan, no bar
assert.strictEqual(L.progress(plan, "running", 0, 99), 0.25);   // turns past the budget are capped

// selection in the detail pane
assert.strictEqual(L.pickSelected([], "x"), null);
assert.strictEqual(L.pickSelected(["a", "b"], "a"), "a");
assert.strictEqual(L.pickSelected(["a", "b"], "gone"), "b");
assert.strictEqual(L.pickSelected(["a", "b"], null), "b");

console.log("layout tests ok");
