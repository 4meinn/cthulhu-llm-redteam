"""Defensive JSON extraction for messy model output.

Local models wrap JSON in prose, think-blocks and code fences, and emit trailing
commas or single quotes. These helpers pull a usable object out anyway. The
judge scorer and the adaptive attacker both read structured output this way.
"""

from __future__ import annotations

import json
import re

_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def extract_objects(text: str) -> list[str]:
    """Every balanced ``{...}`` span in order, ignoring braces inside strings."""
    out, depth, start, in_str, esc = [], 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                out.append(text[start : i + 1])
    return out


def loads_lenient(blob: str) -> dict | None:
    """Parse one object, tolerating trailing commas and single quotes."""
    for attempt in (blob, _TRAILING_COMMA.sub(r"\1", blob),
                    _TRAILING_COMMA.sub(r"\1", blob).replace("'", '"')):
        try:
            obj = json.loads(attempt)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def first_object_with(text: str, *keys: str) -> dict | None:
    """First balanced object in ``text`` that parses and contains all ``keys``."""
    for blob in extract_objects(text):
        obj = loads_lenient(blob)
        if obj is not None and all(k in obj for k in keys):
            return obj
    return None
