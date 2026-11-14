"use strict";

// Pure helpers for the Results section. No DOM here, so Node can test them directly. They turn
// what GET /sweeps/{id}/matrix returns into table specs ({head, rows}) the page draws with
// textContent. Nothing is recomputed: the counts, the rates and the Wilson intervals are the ones
// asr.py made, and each matrix cell already carries the text matrix.md prints, with its dagger,
// its "not run" and its delta. This file only lays them out.

(function (root) {
  const LOW_N = 10;
  const MARK = "†";            // the dagger: n under LOW_N, read the count, not the rate
  const ORDER = ["none", "input", "output", "both"];

  const OUTPUT_BOX =
    "Output stage in this sweep. A proven count of 0 for output and both is expected by " +
    "construction, because the filter and the scorer apply the same check. It does not mean the " +
    "target is safe. Caught is the number that carries information: the model's own reply held " +
    "the secret and the filter removed it. A leak the filter cannot recognise would show up, if " +
    "at all, as a judge only turn that needs review.";

  function pct(rate) {
    return rate == null ? "n/a" : (rate * 100).toFixed(1) + "%";
  }

  function fraction(k, n, rate) {
    return k + "/" + n + (n ? " (" + pct(rate != null ? rate : k / n) + ")" : "");
  }

  function ciText(ci) {
    return ci && ci.length === 2 ? pct(ci[0]) + " to " + pct(ci[1]) : "";
  }

  function lowN(n, limit) {
    return n > 0 && n < (limit || LOW_N);
  }

  function rank(guardrail) {
    const i = ORDER.indexOf(guardrail);
    return i >= 0 ? i : ORDER.length;
  }

  // The same label asr.md uses for a configuration.
  function configLabel(key) {
    const parts = [key.target, "guardrail " + key.guardrail, key.strategy];
    if (key.attacker_model) parts.push("attacker " + key.attacker_model);
    parts.push(key.mode);
    return parts.join(", ");
  }

  function sortRows(rows) {
    return rows.slice().sort(function (a, b) {
      const x = a.key, y = b.key;
      return String(x.target).localeCompare(String(y.target)) ||
        rank(x.guardrail) - rank(y.guardrail) ||
        String(x.mode).localeCompare(String(y.mode)) ||
        String(x.category || "").localeCompare(String(y.category || "")) ||
        String(x.vector || "").localeCompare(String(y.vector || ""));
    });
  }

  const ASR_HEAD = ["Config", "Turns (n)", "Proven", "95% CI", "Judge only", "Blocked",
                    "Filtered", "Caught", "Errors", "Sessions"];

  function sessionsText(row) {
    let text = row.sessions_proven + " of " + row.sessions + " proven";
    if (row.sessions_judge_only) text += ", " + row.sessions_judge_only + " judge only";
    return text;
  }

  // One ASR row. Proven and judge only are separate columns and judge only is never added to
  // proven. Caught is its own column beside filtered. A row with no scored turn says so instead
  // of showing a rate.
  function asrCells(row, level) {
    const n = row.n;
    const cells = [{ text: configLabel(row.key), cls: "label" }];
    if (level === "category") cells.push({ text: row.key.category || "", cls: "label" });
    if (level === "vector") cells.push({ text: row.key.vector || "", cls: "label" });
    if (level === "objective") cells.push({ text: row.key.objective || "", cls: "label" });
    cells.push({ text: String(n) + (lowN(n) ? " " + MARK : ""), cls: lowN(n) ? "num low" : "num" });
    if (!n) {
      cells.push({ text: "no result", cls: "muted" }, { text: "", cls: "" });
    } else {
      cells.push({ text: fraction(row.proven, n, row.proven_rate), cls: "num" },
                 { text: ciText(row.proven_ci), cls: "num muted" });
    }
    cells.push({ text: n ? fraction(row.judge_only, n, row.judge_only_rate) : "", cls: row.judge_only ? "num judge" : "num" });
    cells.push({ text: String(row.blocked), cls: "num" });
    cells.push({ text: String(row.filtered), cls: "num" });
    cells.push({ text: String(row.caught), cls: row.caught ? "num caught" : "num" });
    cells.push({ text: String(row.error), cls: row.error ? "num err" : "num" });
    cells.push({ text: sessionsText(row), cls: "muted" });
    return cells;
  }

  // level is config, category, vector or objective, the keys asr_section returns.
  function asrTable(section, level) {
    const rows = sortRows((section && section[level]) || []);
    const extra = { category: "Category", vector: "Vector", objective: "Objective" }[level];
    const head = ASR_HEAD.slice();
    if (extra) head.splice(1, 0, extra);
    return { head: head, rows: rows.map(function (r) { return { cls: lowN(r.n) ? "low" : "", cells: asrCells(r, level) }; }) };
  }

  function cellSpec(cell) {
    if (!cell) return { text: "not run", cls: "muted" };
    return { text: cell.text, cls: cell.low_n ? "low" : "" };
  }

  function deltaSpec(delta) {
    if (!delta) return { text: "n/a", cls: "muted" };
    return { text: delta.text, cls: delta.apart ? "apart" : "overlap" };
  }

  // The defence matrix: a vector or a category against the guardrail mode, one column per mode
  // that has data, and a column per mode for the change against the unguarded one.
  function defenceTable(table) {
    const versus = table.guardrails.indexOf("none") >= 0 ? table.guardrails.filter(function (g) { return g !== "none"; }) : [];
    const head = [table.view === "category" ? "Category" : "Vector"].concat(table.guardrails)
      .concat(versus.map(function (g) { return g + " vs none"; }));
    const rows = table.rows.map(function (r) {
      const cells = [{ text: r.label, cls: "label" }];
      table.guardrails.forEach(function (g) { cells.push(cellSpec(r.cells[g])); });
      versus.forEach(function (g) { cells.push(deltaSpec(r.deltas[g])); });
      return { cls: "", cells: cells };
    });
    return { title: "Defence matrix by " + table.view + ": " + table.target + " (isolated)", head: head, rows: rows };
  }

  function contextTable(table) {
    const head = ["Vector", "Isolated", "Campaign tried", "Campaign won", "Note"];
    const rows = table.rows.map(function (r) {
      return { cls: "", cells: [
        { text: r.vector, cls: "label" }, cellSpec(r.isolated),
        { text: r.tried, cls: "num" }, { text: r.won, cls: r.caught_turns.length ? "caught" : "num" },
        { text: r.note, cls: "muted" },
      ] };
    });
    return { title: "Context effect: " + table.target + ", guardrail " + table.guardrail, head: head, rows: rows };
  }

  function sessionTable(session) {
    if (!session) return null;
    const head = ["Attacker"].concat(session.defenders.map(function (d) { return d.target + ", guardrail " + d.guardrail; }));
    const rows = session.attackers.map(function (a) {
      const cells = [{ text: a.label, cls: "label" }];
      session.defenders.forEach(function (d) {
        const hit = session.cells.filter(function (c) {
          return c.attacker[0] === a.strategy && c.attacker[1] === a.model &&
                 c.defender[0] === d.target && c.defender[1] === d.guardrail;
        })[0];
        cells.push({ text: hit ? hit.text : "not run", cls: hit ? (hit.low_n ? "low" : "") : "muted" });
      });
      return { cls: "", cells: cells };
    });
    return { title: "Session matrix: attacker against defender (campaign sessions)", head: head, rows: rows };
  }

  // Everything the matrix view draws, in order. The output box comes before the tables it
  // qualifies, so a zero is never read before the reason for it.
  function matrixView(data) {
    const m = (data && data.matrix) || {};
    return {
      empty: !data || !m || m.empty === true,
      outputBox: m.output_stage ? OUTPUT_BOX : "",
      defence: (m.defence || []).map(defenceTable),
      vectors: (m.vectors || []).map(defenceTable),
      context: (m.context || []).map(contextTable),
      contextNote: m.context_note || "",
      session: sessionTable(m.session),
      notes: m.notes || [],
    };
  }

  // A line for a sweep that is not finished: the tables read the finished runs only.
  // A running sweep is partial and still moving. A cancelled, failed or older sweep with fewer
  // runs on disk than its grid is incomplete: it will not change, but it is not the whole grid.
  function partialBanner(data, status, total) {
    if (!data) return "";
    if (data.partial || status === "running") {
      return "Partial data: " + data.runs + " finished run(s) so far. The tables update as runs finish, and the " +
             "numbers can still move.";
    }
    if (total > 0 && data.runs < total) {
      return "Incomplete: " + data.runs + " of " + total + " run(s) finished" +
             (status === "cancelled" ? ", the sweep was cancelled" : (status === "error" ? ", the sweep stopped with an error" : "")) +
             ". Cells with fewer repeats than planned have a wider interval, and a cell with no run says not run.";
    }
    return "";
  }

  // --- the judge only turns --------------------------------------------------
  // A judge only turn is one the judge flagged and no deterministic check did. It is never proven.
  // The words are model output: the page shows them as text in a pre element and nothing here
  // builds markup from them.
  const REVIEW_CAP = 50;

  function reviewHeading(item) {
    return item.n + ". " + (item.run_id || "run") + ", " + item.objective + ", turn " + item.turn + " (" + item.vector + ")";
  }

  function reviewMeta(item) {
    return "target " + item.target + ", guardrail " + item.guardrail + ", " + item.mode + ", " + item.strategy;
  }

  // The frame every case is read in. judge only is its own badge; a leak the output filter
  // caught in the same turn is named, because that turn also counts as caught.
  function reviewBadges(item) {
    const badges = [{ text: "judge flagged, no deterministic signal", cls: "review" }];
    if (item.raw_leak) badges.push({ text: "model reply held the secret before the filter", cls: "caught" });
    return badges;
  }

  function reviewShown(items, cap) {
    const limit = cap || REVIEW_CAP;
    return { shown: (items || []).slice(0, limit), hidden: Math.max((items || []).length - limit, 0) };
  }

  const EXPORTS = ["asr.csv", "matrix.csv", "matrix.md", "review.md"];

  // The files the command line writes, for a sweep. review.md only when there is something to review.
  function exportLinks(id, hasReview) {
    return EXPORTS.filter(function (name) { return name !== "review.md" || hasReview; }).map(function (name) {
      return { name: name, href: "/sweeps/" + encodeURIComponent(id) + "/export/" + name };
    });
  }

  function sweepOption(s) {
    const state = s.live ? s.status : "on disk";
    return s.id + " (" + state + ", " + s.done + " of " + s.total + " runs)";
  }

  const api = {
    LOW_N: LOW_N, MARK: MARK, OUTPUT_BOX: OUTPUT_BOX, ASR_HEAD: ASR_HEAD,
    pct: pct, fraction: fraction, ciText: ciText, lowN: lowN, configLabel: configLabel, sortRows: sortRows,
    asrCells: asrCells, asrTable: asrTable, defenceTable: defenceTable, contextTable: contextTable,
    sessionTable: sessionTable, matrixView: matrixView, partialBanner: partialBanner, sweepOption: sweepOption,
    REVIEW_CAP: REVIEW_CAP, reviewHeading: reviewHeading, reviewMeta: reviewMeta, reviewBadges: reviewBadges,
    reviewShown: reviewShown, exportLinks: exportLinks,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CthulhuResults = api;
})(typeof window !== "undefined" ? window : this);
