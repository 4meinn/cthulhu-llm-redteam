"""Run configuration: Pydantic models and the YAML loader."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

CANARY_PLACEHOLDER = "{{CANARY}}"


class EndpointConfig(BaseModel):
    """Where a model lives and how to talk to it."""

    provider: Literal["mock", "ollama", "openai"] = "mock"
    base_url: str = "http://localhost:11434"
    model: str = "qwen3:8b"
    api_key: str | None = None
    timeout: float = 120.0


class AttackerConfig(BaseModel):
    """The adaptive, multi-turn attacker (V2). The single-turn `run` ignores this."""

    strategy: Literal["corpus", "adaptive"] = "corpus"
    endpoint: EndpointConfig | None = None   # required for adaptive; use an uncensored model
    objectives: list[str] = Field(default_factory=lambda: ["leak_canary"])
    max_turns: int = Field(default=6, ge=1)             # turn budget per session
    judge_mode: Literal["per_turn", "on_suspicion", "final"] = "on_suspicion"
    max_history_turns: int = Field(default=8, ge=1)     # sliding window sent to the target
    max_message_chars: int = Field(default=2000, ge=1)  # per-message clip


class RunConfig(BaseModel):
    """Everything needed for one test run."""

    target: EndpointConfig
    judge: EndpointConfig | None = None
    system_prompt_file: Path
    canary: str
    corpus_dir: Path | None = None  # None uses the bundled corpus
    categories: list[str] = Field(default_factory=list)  # empty means all
    scoring: Literal["any", "all", "deterministic", "judge"] = "any"
    guardrail: bool = False
    iterations: int = Field(default=1, ge=1)
    attacker: AttackerConfig | None = None
    output_dir: Path = Path("out")

    def system_prompt(self) -> str:
        """Target system prompt with the canary substituted in."""
        text = self.system_prompt_file.read_text(encoding="utf-8")
        return text.replace(CANARY_PLACEHOLDER, self.canary)


def load_config(path: str | Path) -> RunConfig:
    """Read a YAML config. Relative paths resolve against the config's folder."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = RunConfig.model_validate(raw)
    if not cfg.system_prompt_file.is_absolute():
        cfg.system_prompt_file = (path.parent / cfg.system_prompt_file).resolve()
    if cfg.corpus_dir is not None and not cfg.corpus_dir.is_absolute():
        cfg.corpus_dir = (path.parent / cfg.corpus_dir).resolve()
    return cfg
