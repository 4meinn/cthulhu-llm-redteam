"""Build the corpus subset for the jb-002 probe from the results of sweep A.

The question: does jb-002 (the story-framed jailbreak) leak from an unguarded campaign if the
conversation before it has no winning vector in it? In the full corpus an unguarded campaign ends
on an earlier vector and never reaches it. So the vectors that win against the unguarded target
(a proven success with guardrail none, in isolated or campaign runs) are removed from the part of
the corpus that comes before jb-002, and the corpus ends at jb-002.

The context is not the same as in the full corpus, where jb-002 is attack 19 and the window holds
other vectors. It is jb-002 after a conversation of non-winning turns, and it is reported that way.

    python research/make_probe_corpus.py out/research/a            # show what would be removed
    python research/make_probe_corpus.py out/research/a --write    # and write the corpus
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from cthulhu.asr import merge_results, result_files
from cthulhu.corpus import load_corpus
from cthulhu.corpus.loader import _data_dir
from cthulhu.results import SessionRecord, is_proven

TARGET = "jb-002"
WINDOW = 8                                  # max_history_turns in the base config


def corpus_order() -> list[str]:
    return [a.id for a in load_corpus([])]


def winners(records: list[SessionRecord], order: list[str]) -> dict[str, int]:
    """Vectors before the target with a proven success against the unguarded target, and how many."""
    before = set(order[: order.index(TARGET)])
    found: dict[str, int] = {}
    for rec in records:
        if rec.guardrail != "none":
            continue
        for turn in rec.turns:
            if turn.outcome == "objective_met" and is_proven(rec, turn) and turn.vector in before:
                found[turn.vector] = found.get(turn.vector, 0) + 1
    return dict(sorted(found.items(), key=lambda kv: order.index(kv[0])))


def subset_order(order: list[str], removed: set[str]) -> list[str]:
    return [i for i in order[: order.index(TARGET) + 1] if i not in removed]


def write_corpus(keep: list[str], out_dir: Path) -> None:
    """Copy the bundled corpus files, keeping only the attacks in keep, in their own order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.yaml"):
        old.unlink()
    for path in sorted(_data_dir().glob("*.yaml")):
        entries = [e for e in yaml.safe_load(path.read_text(encoding="utf-8")) if e["id"] in keep]
        if entries:
            text = yaml.safe_dump(entries, sort_keys=False, allow_unicode=True, width=10_000)
            (out_dir / path.name).write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("results", type=Path, nargs="+", help="sweep A folder (or results files)")
    p.add_argument("--out", type=Path, default=Path("out/research/probe-corpus"))
    p.add_argument("--write", action="store_true", help="write the corpus, otherwise only show it")
    args = p.parse_args(argv)

    records, _ = merge_results(result_files(args.results))
    order = corpus_order()
    won = winners(records, order)
    keep = subset_order(order, set(won))

    print(f"{len(records)} session(s) read, {sum(r.guardrail == 'none' for r in records)} unguarded")
    print(f"removed (proven success with guardrail none, before {TARGET}):")
    for vector, count in won.items():
        print(f"  {vector}: {count} proven success(es)")
    if not won:
        print(f"  none, so the subset only drops what comes after {TARGET}")
    print(f"corpus of {len(keep)}: {' '.join(keep)}")
    pos = keep.index(TARGET) + 1
    print(f"{TARGET} is attack {pos}; the {WINDOW} turns before it: "
          f"{' '.join(keep[max(0, pos - 1 - WINDOW):pos - 1])}")

    if not args.write:
        print("(not written, add --write)")
        return 0
    write_corpus(keep, args.out)
    manifest = {"source": [str(r) for r in args.results], "removed": won, "corpus": keep,
                "target": TARGET, "window": WINDOW}
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
