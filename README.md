# Cthulhu

Prompt-injection and jailbreak testing for LLM applications you own.

Cthulhu runs a corpus of adversarial prompts against a target assistant whose
system prompt contains a planted secret, a **canary**, then decides, per
attack, whether the target broke its rules. It applies a defensive guardrail,
measures the before/after effect, and writes a JSON record plus a Markdown
report mapped to the OWASP LLM Top 10.

```
cthulhu run --config examples/config.yaml --guardrail
```

See [`samples/report.md`](samples/report.md) for a full report produced by the
offline mock, with no model and no network.

## Authorized use only

This tool is for systems you **own or are explicitly authorized to test**. The
intended setup is a **local, self-defined target**: a system prompt you wrote,
with a canary you planted, running on your own machine. Point it at nothing
else. It ships no malware and needs none, "success" means the target revealed
its own planted canary or broke one of its own stated rules.

## How it works

The pipeline is a sequence of small, independently testable stages.

1. **Config**, a YAML file names the target endpoint, the judge endpoint, the
   system-prompt file, the canary, which categories to run, the scoring policy,
   and whether the guardrail is on.
2. **Corpus**, adversarial prompts live in one YAML file per category and are
   loaded into a typed model. You can point at your own corpus directory.
3. **Runner**, each prompt is sent to the target (with retries). When the
   guardrail is on, it runs *before* the target sees the prompt.
4. **Scoring**, two independent scorers, recorded separately:
   - **Deterministic**, canary detection (including spaced/separated leaks)
     plus per-attack marker patterns.
   - **LLM judge**, a second model receives the target's rules, the attack, and
     the reply, and returns a structured `{"violated": …, "reason": …}`. The
     parser is defensive: it strips `<think>` blocks and code fences, tolerates
     trailing commas and single quotes, and falls back to the rules if the judge
     output can't be parsed.
   A configurable policy (`any`, `all`, `deterministic`, `judge`) combines them,
   and the report shows how often the two agreed.
5. **Guardrail**, an input filter plus prompt hardening (see below).
6. **Report**, JSON (every raw result) and Markdown (summary tables, OWASP
   mapping, a "what still bypasses" section, and anything the two scorers
   disagreed on).

The target and judge are called in separate stages, **every target call
finishes before any judge call begins**, so on memory-constrained hardware
each model is loaded once rather than swapped in and out.

## The guardrail and the holdout split

The guardrail has two layers:

- **Input filter**, known override, leak, and obfuscation markers. A strong
  match (instruction override, role-play jailbreak, prompt-leak) **blocks** the
  prompt so it never reaches the target; a weaker signal (an instruction
  embedded in quoted data, an encoded payload) is **sanitized**, the offending
  span is removed and the legitimate remainder is still sent.
- **Prompt hardening**, the target's system prompt is wrapped with an explicit
  reminder that user content is untrusted data, not instructions, and that its
  rules cannot be overridden or placed in any special mode.

A filter that is measured against the exact prompts it was written for will look
perfect. To keep the numbers honest, the corpus is split into **seen** and
**holdout** partitions by a stable hash of each attack id. The report shows the
guardrail's residual bypass rate on each, so you can see how it does against
phrasings it was *not* tuned on, which is the number that actually matters.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
cthulhu list-attacks                 # show the corpus (filter with --category)
cthulhu run --config examples/config.yaml
cthulhu run --config examples/config.yaml --guardrail
cthulhu run --config examples/config.yaml --category jailbreak --category encoding_evasion
cthulhu run --config examples/config.yaml --no-color --out ./out
```

`redteam` is installed as an alias for the same command. Output is colored when
writing to a terminal; `--no-color` (or the `NO_COLOR` environment variable)
forces plain text so it pipes cleanly.

### Configuration

```yaml
target:                      # the system under test
  provider: mock             # mock | ollama | openai
  base_url: http://localhost:11434
  model: qwen3:8b
judge:                       # the referee (omit to skip LLM-judge scoring)
  provider: mock
  base_url: http://localhost:11434
  model: qwen3:14b
system_prompt_file: target_system_prompt.txt   # must contain {{CANARY}}
canary: NW-7f3a9c21-ZEPHYR
corpus_dir: null             # null uses the bundled corpus
categories: []               # empty runs every category
scoring: any                 # any | all | deterministic | judge
guardrail: false
iterations: 1
output_dir: out
```

The target's system prompt uses the placeholder `{{CANARY}}`, which is replaced
with the configured canary at load time, the secret lives only in your config,
never in the corpus.

## Attack categories and OWASP mapping

| Category | What it tries | OWASP |
|---|---|---|
| `direct_injection` | Override the system prompt outright | LLM01 |
| `jailbreak` | Role-play / "unrestricted mode" framings | LLM01 |
| `system_prompt_leak` | Make the target repeat its instructions | LLM01 (sensitive-info disclosure) |
| `indirect_injection` | Instructions hidden inside data the bot processes | LLM01 |
| `encoding_evasion` | Base64 / hex / ROT13 / reversed / leetspeak payloads | Input-filter evasion |

Each corpus entry carries an `id`, `category`, `owasp_tag`, a `prompt` template,
a `success_hint`, and optional `markers` (regexes that signal a rule-break).

## Running against a real model

The defaults target a local [Ollama](https://ollama.com) server. The build and
the test suite use the offline mock, so a live run is an operator step:

```bash
ollama pull qwen3:8b        # target
ollama pull qwen3:14b       # judge
# set provider: ollama in examples/config.yaml, then:
cthulhu run --config examples/config.yaml --guardrail
```

A small, capable target with a stronger judge is the right split, the judge has
to out-reason the target to referee it. `llama3.1:8b` and `gemma2:9b` also work
and are config-swappable. Any OpenAI-compatible endpoint works with
`provider: openai` and an optional `api_key`. The model landscape moves quickly;
check what your hardware can host before downloading.

## Project layout

```
cthulhu/
  config.py            config models + YAML loader
  clients/             base interface, mock, ollama / openai-compatible
  corpus/              one YAML per category + loader + seen/holdout split
  runner.py            sends attacks, applies the guardrail, retries
  scoring/             deterministic.py, judge.py, combine.py
  guardrail.py         input filter + prompt hardening
  report.py            JSON + Markdown
  cli.py               `run`, `list-attacks`
examples/              config.yaml + target_system_prompt.txt
samples/               a report generated from the mock
tests/                 offline pytest suite
```

## Tests

```bash
pytest
```

The suite runs fully offline, no model, no network, using the mock client and
a mock judge that reproduces the messy output real local models return.

## Roadmap (v2)

- An **adaptive attacker**: an LLM that generates and adapts attacks itself,
  analyzing why an attempt was blocked and changing tactic. The MVP attacker is
  a static corpus only.
- **Multi-turn** conversational attacks.
- A browser dashboard for exploring results.
