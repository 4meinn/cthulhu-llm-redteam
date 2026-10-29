<div align="center">

<img src="assets/banner.png" alt="Cthulhu, LLM Red-Team Toolkit" width="880">

<p>
  <img src="assets/badges/license.svg" alt="License: MIT">
  <img src="assets/badges/python.svg" alt="Python 3.11+">
  <img src="assets/badges/owasp.svg" alt="OWASP LLM Top 10">
  <img src="assets/badges/offline.svg" alt="tests: offline">
</p>

</div>

Prompt injection and jailbreak testing for LLM applications you own. Cthulhu runs a corpus of adversarial prompts against a target assistant whose system prompt hides a planted secret (a canary), decides whether each attack broke the target's rules, applies a defensive guardrail, and reports the before and after mapped to the OWASP LLM Top 10.

- **Type**: command line security tool (red-team harness for LLM applications)
- **Stack**: Python 3.11+, httpx, Pydantic, PyYAML, pytest
- **Model runtime**: Ollama or any OpenAI-compatible endpoint, with an offline mock for tests
- **Attack categories**: direct injection, jailbreak, system-prompt leak, indirect injection, encoding evasion
- **Scoring**: deterministic rules plus an LLM judge, recorded separately
- **License**: MIT
- **Status**: v0.1.0, core MVP

## Authorized use

This tool tests systems you own or are explicitly authorized to test. The intended setup is a local target you define yourself: a system prompt you wrote, with a canary you planted. No third-party or production systems are involved, and it ships no malware. A bypass means the target revealed its own planted canary or broke one of its own stated rules, nothing more.

## How it works

The run is a short pipeline, each stage small enough to test on its own.

1. **Config.** A YAML file names the target endpoint, the judge endpoint, the system-prompt file, the canary, which categories to run, the scoring policy, and whether the guardrail is on.
2. **Corpus.** Adversarial prompts live in one YAML file per category and load into a typed model. You can point at your own corpus directory instead of the bundled one.
3. **Runner.** Each prompt goes to the target with retries. With the guardrail on, it runs before the target ever sees the prompt.
4. **Scoring.** Two scorers, recorded separately. The deterministic one detects the canary (including leaks with altered spacing or separators) and per-attack marker patterns. The LLM judge receives the target's rules, the attack and the reply, and returns a structured `{"violated": ..., "reason": ...}`. The judge parser is written for real local models: it strips think-blocks and code fences, tolerates trailing commas and single quotes, and falls back to the rules when the output cannot be parsed.
5. **Guardrail.** An input filter plus prompt hardening, applied before the prompt reaches the target.
6. **Report.** JSON with every raw result, and Markdown with the summary tables, the OWASP mapping, a list of what still bypasses, and anything the two scorers disagreed on.

The target and the judge run in separate stages, every target call before any judge call, so on a memory-bound machine each model loads once instead of being swapped in and out.

## The guardrail and the holdout split

The guardrail has two layers. The **input filter** matches known override, leak and obfuscation markers: a strong match (instruction override, role-play jailbreak, prompt-leak) blocks the prompt so it never reaches the target, while a weaker signal (an instruction buried in quoted data, an encoded payload) is sanitized, meaning the offending span is removed and the legitimate remainder is still sent. **Prompt hardening** wraps the target's system prompt with a reminder that user content is data, not instructions, and that its rules cannot be overridden or placed in a special mode.

A filter scored against the exact prompts it was built for will always look perfect. To keep the numbers honest, the corpus is split into a `seen` and a `holdout` partition by a stable hash of each attack id. The report gives the residual bypass rate on each, so the number that matters is the one on phrasings the filter was never tuned against.

## Attack categories and OWASP mapping

| Category | What it tries | OWASP |
|---|---|---|
| `direct_injection` | Override the system prompt outright | LLM01 |
| `jailbreak` | Role-play and "unrestricted mode" framings | LLM01 |
| `system_prompt_leak` | Make the target repeat its own instructions | LLM01, sensitive information disclosure |
| `indirect_injection` | Instructions hidden inside data the bot processes | LLM01 |
| `encoding_evasion` | Base64, hex, ROT13, reversed and leetspeak payloads | input-filter evasion |

Each corpus entry carries an `id`, `category`, `owasp_tag`, a `prompt` template, a `success_hint`, and optional `markers` (regexes that signal a rule-break).

## Install

```
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```
cthulhu list-attacks
cthulhu run --config examples/config.yaml
cthulhu run --config examples/config.yaml --guardrail
cthulhu run --config examples/config.yaml --category jailbreak --category encoding_evasion
cthulhu run --config examples/config.yaml --no-color --out ./out
```

`redteam` is installed as an alias for the same command. Output is colored when it writes to a terminal; `--no-color`, or the `NO_COLOR` environment variable, forces plain text so it pipes cleanly.

## Configuration

```yaml
target:                      # the system under test
  provider: mock             # mock, ollama or openai
  base_url: http://localhost:11434
  model: qwen3:8b
judge:                       # the referee, omit to skip LLM-judge scoring
  provider: mock
  base_url: http://localhost:11434
  model: qwen3:14b
system_prompt_file: target_system_prompt.txt   # must contain {{CANARY}}
canary: NW-7f3a9c21-ZEPHYR
corpus_dir: null             # null uses the bundled corpus
categories: []               # empty runs every category
scoring: any                 # any, all, deterministic or judge
guardrail: false
iterations: 1
output_dir: out
```

The system prompt uses the placeholder `{{CANARY}}`, replaced with the configured canary at load time, so the secret lives only in your config and never in the corpus.

## Running against a real model

The defaults point at a local [Ollama](https://ollama.com) server. The build and the tests use the offline mock, so a live run is an operator step:

```
ollama pull qwen3:8b
ollama pull qwen3:14b
```

Set `provider: ollama` under `target` and `judge` in `examples/config.yaml`, then run. A small, capable target with a stronger judge is the right split, since the judge has to out-reason the target to referee it. `llama3.1:8b` and `gemma2:9b` also fit 8 GB and are config-swappable. Any OpenAI-compatible endpoint works with `provider: openai` and an optional `api_key`.

## Example output

[`samples/report.md`](samples/report.md) is a full report produced by the offline mock with the guardrail on. Baseline bypasses 23 of 29 attacks; with the guardrail on, 5 still get through, and the holdout partition carries the honest residual rate.

## Project layout

```
cthulhu/
  config.py            config models and the YAML loader
  clients/             client interface, mock, ollama and openai-compatible
  corpus/              one YAML per category, loader, seen/holdout split
  runner.py            sends attacks, applies the guardrail, retries
  scoring/             deterministic.py, judge.py, combine.py
  guardrail.py         input filter and prompt hardening
  report.py            JSON and Markdown
  cli.py               run and list-attacks
examples/              config.yaml and target_system_prompt.txt
samples/               a report generated from the mock
tests/                 the offline pytest suite
```

## Tests

```
pytest
```

The suite runs fully offline, no model and no network, using the mock client and a mock judge that reproduces the messy output real local models return.

## Roadmap

- An adaptive attacker: a model that writes and adapts its own attacks, reads why an attempt was blocked and changes tactic. The MVP attacker is a static corpus.
- Multi-turn conversational attacks.
- A dashboard for exploring results.

## Known limitations

- The attacker is a fixed corpus, so coverage is only as wide as the prompts shipped in `cthulhu/corpus/data`.
- The guardrail is pattern-based. It catches phrasings it was written for and misses novel ones, which is exactly what the holdout number is there to show.
- The LLM judge is only as reliable as the model behind it; a weak judge disagrees with the deterministic rules more often, which is why both verdicts are kept.

## License

[MIT](LICENSE) © 2026 Metin Berkay Şen

## Key takeaway

Measuring a guardrail honestly is harder than writing one. A filter scored against the prompts it was built for always looks perfect, so the number that means anything is the residual bypass rate on the holdout split, the phrasings it never saw. Keeping the deterministic rules and the model judge as two separate verdicts, instead of collapsing them into one, is what makes a disagreement visible enough to pull aside and review by hand.
