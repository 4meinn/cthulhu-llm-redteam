"use strict";

// Pure helpers for the live board: which layout to show, the remembered choice
// and how far along a run is. No DOM here, so Node can test them directly.

(function (root) {
  const KEY = "cthulhu.layout";
  const LAYOUTS = ["C", "A"];

  // One run fills the page. Two or more use the remembered choice, C by default:
  // C is a run list beside the selected run, A puts each run in its own column.
  function chooseLayout(runCount, preference) {
    if (runCount <= 1) return "single";
    return normalizePreference(preference);
  }

  function normalizePreference(value) {
    return LAYOUTS.indexOf(value) >= 0 ? value : "C";
  }

  // Storage may be missing or throw (private windows, blocked site data), and the
  // board has to work without it.
  function loadPreference(storage) {
    try {
      return normalizePreference(storage.getItem(KEY));
    } catch (e) {
      return "C";
    }
  }

  function savePreference(storage, value) {
    try {
      storage.setItem(KEY, normalizePreference(value));
      return true;
    } catch (e) {
      return false;
    }
  }

  // A run is objectives times phases sessions. Each ended session counts as one,
  // and the session in progress counts by how many of its turns have been played.
  function progress(plan, status, endedSessions, currentTurn) {
    if (status === "done" || status === "cancelled" || status === "error") return 1;
    const total = (plan.objectives || 0) * (plan.phases || 0);
    if (total <= 0) return 0;
    const turns = Math.max(plan.max_turns || 1, 1);
    const partial = Math.min(Math.max(currentTurn || 0, 0) / turns, 1);
    const value = (endedSessions + (endedSessions < total ? partial : 0)) / total;
    return Math.min(Math.max(value, 0), 1);
  }

  // The run to show in the detail pane. Keep the current choice if it still exists,
  // otherwise the newest run.
  function pickSelected(runIds, selected) {
    if (runIds.length === 0) return null;
    return runIds.indexOf(selected) >= 0 ? selected : runIds[runIds.length - 1];
  }

  const api = {
    LAYOUTS: LAYOUTS,
    chooseLayout: chooseLayout,
    normalizePreference: normalizePreference,
    loadPreference: loadPreference,
    savePreference: savePreference,
    progress: progress,
    pickSelected: pickSelected,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CthulhuLayout = api;
})(typeof window !== "undefined" ? window : this);
