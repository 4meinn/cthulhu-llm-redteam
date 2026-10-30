# Sample output

Two sets of reports are checked in. Both were produced with the guardrail on.

## Mock (`report.md`, `report.json`)

Generated from the offline mock target by running:

```bash
cthulhu run --config examples/config.yaml --guardrail --out samples
```

No model and no network are involved. The mock client stands in for a
moderately vulnerable support bot so the repository shows real report structure
without needing a live model. The numbers reflect the mock's behavior, not any
real system.

## Live (`live/report.md`, `live/report.json`)

Generated from a real run with `qwen3:8b` as the target and `qwen3:14b` as the
judge, both served by Ollama. The config is `examples/config.yaml` with
`provider: ollama` under `target` and `judge`:

```bash
cthulhu run --config examples/config.yaml --guardrail --out samples/live
```

The raw replies include the target's full answers, so `spl-005` shows the
canary leak and `enc-005` shows the case where the judge and the rules
disagreed.

Typographic quotes and dashes in the target's raw replies were replaced with
plain ASCII characters in the checked-in JSON. Nothing else was edited.
