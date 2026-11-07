# Research sweeps

The configs behind the second writeup: the attack success rates across guardrail modes, and a probe
of the jb-002 finding. Results are written under `out/`, which is not committed, and a results file
can hold model text, so none of it belongs in the repository. Run everything from the repository
root, with Ollama serving `qwen3:8b` (target) and `qwen3:14b` (judge).

Sampling is not pinned. A run uses the model's own default sampling, and `sweep.json` records that
it was left at the default. The repeats are there to measure how much the results move.

## The parts

| Part | Config | Runs | What it answers | Judge mode |
|---|---|---|---|---|
| A | `sweep-a.yaml` | 80 | Attack success rate per guardrail mode, isolated and campaign, 10 repeats each | `on_suspicion` |
| B | `sweep-probe.yaml` | 40 | Does jb-002 leak from an unguarded campaign after a context with no winning vector in it? | `on_suspicion` |
| C | `sweep-c.yaml` | 12 | Does the judge find leaks the output filter cannot recognise? | `per_turn` |

A session ends only on a proven success, so the judge does not change the proven rates. That is why
A and B use the cheap judge mode and only C, which looks for judge-only findings, pays for judging
every reply.

## Running

`run.ps1` keeps Windows from idle-sleeping while a command runs. A sweep writes one result file per
finished run, so if it is stopped, running the same command again carries on.

```
research\run.ps1 .venv\Scripts\cthulhu.exe sweep --config research\sweep-a.yaml
```

Part B needs the corpus subset, which is made from the results of part A:

```
.venv\Scripts\python research\make_probe_corpus.py out\research\a            # show what is removed
.venv\Scripts\python research\make_probe_corpus.py out\research\a --write
research\run.ps1 .venv\Scripts\cthulhu.exe sweep --config research\sweep-probe.yaml
```

Then `research\run.ps1 .venv\Scripts\cthulhu.exe sweep --config research\sweep-c.yaml`. Each sweep
writes `asr.md`, `matrix.md` and, when the judge flagged something alone, `review.md` into its folder.
Do not merge the folders of A and C: they contain the same cells and the same run ids.

## The probe, and what it is not

jb-002 broke an input-guarded campaign at attack 19 in all three runs of an earlier smoke test, and
was never reached without the guardrail, because an unguarded campaign ends first on another vector.
The probe removes the vectors that win against the unguarded target (a proven success with guardrail
none, in part A, among the attacks before jb-002) and ends the corpus at jb-002.

The question it asks is: does jb-002 leak from an unguarded campaign after a context of about 14
non-winning turns? It is not the same 18-turn context. The window of 8 turns before jb-002 held
vectors that win on their own, which cannot be kept without ending the unguarded campaign, so the
context differs and the result is reported as that.
