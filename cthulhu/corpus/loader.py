"""Attack corpus: one YAML file per category."""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Attack(BaseModel):
    id: str
    category: str
    owasp_tag: str
    prompt: str
    success_hint: str
    markers: list[str] = Field(default_factory=list)  # regexes that signal success

    def render(self, **values: str) -> str:
        """Fill template placeholders; unknown ones are left as written."""
        merged = {"secret_label": "the access code", **values}
        out = self.prompt
        for key, val in merged.items():
            out = out.replace("{" + key + "}", val)
        return out


def partition(attack_id: str, holdout_every: int = 3) -> str:
    """Split attacks into 'seen' and 'holdout' deterministically by id.

    The guardrail rules are written with the 'seen' attacks in view; 'holdout'
    is evaluation-only, so its bypass rate reflects phrasings the filter was not
    tuned on. The split is a stable hash of the id, so it never changes between
    runs and needs no edits to the corpus files.
    """
    digest = hashlib.sha1(attack_id.encode()).hexdigest()
    return "holdout" if int(digest, 16) % holdout_every == 0 else "seen"


def _data_dir() -> Path:
    return Path(str(resources.files("cthulhu.corpus") / "data"))


def available_categories(data_dir: Path | None = None) -> list[str]:
    return sorted(p.stem for p in (data_dir or _data_dir()).glob("*.yaml"))


def load_corpus(
    categories: list[str] | None = None, data_dir: Path | None = None
) -> list[Attack]:
    """Load attacks, optionally limited to some categories."""
    data_dir = data_dir or _data_dir()
    known = available_categories(data_dir)
    wanted = categories or known
    unknown = [c for c in wanted if c not in known]
    if unknown:
        raise ValueError(
            f"unknown categories: {', '.join(unknown)} (available: {', '.join(known)})"
        )
    attacks: list[Attack] = []
    for name in wanted:
        entries = yaml.safe_load((data_dir / f"{name}.yaml").read_text(encoding="utf-8"))
        attacks.extend(Attack.model_validate(e) for e in entries)
    return attacks
