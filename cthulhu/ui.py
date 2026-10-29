"""Terminal styling. Crimson theme; everything degrades to plain text."""

from __future__ import annotations

import os
import sys

PALETTE = {
    "primary": (224, 106, 144),
    "primary_bright": (255, 111, 174),
    "crimson": (214, 48, 79),
    "teal": (63, 199, 158),
    "muted": (188, 154, 164),
}

BANNER = "cthulhu - it stirs, and your prompts answer"


def color_enabled(no_color: bool = False) -> bool:
    if no_color or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def paint(text: str, name: str, enabled: bool) -> str:
    if not enabled:
        return text
    r, g, b = PALETTE[name]
    return f"\x1b[38;2;{r};{g};{b}m{text}\x1b[0m"
