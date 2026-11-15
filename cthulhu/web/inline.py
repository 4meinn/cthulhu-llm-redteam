"""Configs that arrive over HTTP name no files.

A request must not make the server read a path of the client's choosing: with a Host check
as the only guard, DNS rebinding plus a path in the config would be an arbitrary file read,
because what the target model sees can come back out in its replies. So a web request gives
the system prompt as text, the bundled corpus is the only corpus, and the output folder is
the server's. The command line keeps all of these as options, it is the user's own shell.
"""

from __future__ import annotations

from pathlib import Path

from ..config import RunConfig


def inline_config(cfg: RunConfig) -> RunConfig:
    """The config with every file path removed, or ValueError when the prompt is not inline."""
    if cfg.system_prompt_text is None:
        raise ValueError("files are not read over HTTP, give the system prompt as text "
                         "(system_prompt_text)")
    if cfg.attacker is None:
        raise ValueError("config has no 'attacker' section")
    return cfg.model_copy(update={"corpus_dir": None, "system_prompt_file": None, "output_dir": Path("out")})
