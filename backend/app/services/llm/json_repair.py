"""Tolerant JSON extraction for LLM output.

Models (especially in plain-text / json_object mode) return JSON wrapped in ways a bare ``json.loads``
rejects. This module recovers the object in these cases, cheapest first:

1. clean JSON                                   ``{"recommendations": [...]}``
2. a fenced block anywhere in the text           ``Here you go:\n```json\n{...}\n```\nEnjoy!``
3. an unterminated fence (output was cut off)    ```` ```json\n{"recommendations": [{...}, {"poi ````
4. prose before/after the object                 ``Sure! {...} Let me know...``
5. trailing commas                               ``{"a": [1, 2,],}``
6. truncation: cut back to the last complete value and close the open brackets, so the complete
   recommendations are kept and the half-written one is dropped.

It never invents content: a repair only removes text or adds the closing brackets that are missing.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCED = re.compile(r"```[ \t]*(?:json|JSON|javascript)?[ \t]*\r?\n?(.*?)```", re.DOTALL)
_OPEN_FENCE = re.compile(r"```[ \t]*(?:json|JSON|javascript)?[ \t]*\r?\n?")
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_MAX_REPAIR_TRIES = 64


class JSONExtractionError(ValueError):
    """No JSON object could be recovered from the text."""


def extract_json(text: str) -> tuple[Any, bool]:
    """Return ``(value, repaired)``. ``repaired`` is True when truncation repair was needed.

    Raises ``JSONExtractionError`` when nothing usable can be recovered.
    """
    s = (text or "").strip().lstrip("﻿")
    if not s:
        raise JSONExtractionError("empty text")

    if (fenced := _FENCED.search(s)) is not None:
        s = fenced.group(1).strip()
    elif (opened := _OPEN_FENCE.search(s)) is not None:  # fence opened but never closed (cut off)
        s = s[opened.end():].strip()

    try:
        return json.loads(s), False
    except json.JSONDecodeError:
        pass

    start = _first_container(s)
    if start < 0:
        raise JSONExtractionError("no JSON object or array in text")
    s = s[start:]

    try:  # complete object followed by prose
        value, _ = json.JSONDecoder().raw_decode(s)
        return value, False
    except json.JSONDecodeError:
        pass

    cleaned = _TRAILING_COMMA.sub(r"\1", s)
    try:
        value, _ = json.JSONDecoder().raw_decode(cleaned)
        return value, False
    except json.JSONDecodeError:
        pass

    value = _repair_truncated(cleaned)
    if value is None:
        raise JSONExtractionError("could not repair JSON")
    return value, True


def _first_container(s: str) -> int:
    positions = [p for p in (s.find("{"), s.find("[")) if p >= 0]
    return min(positions) if positions else -1


def _repair_truncated(s: str) -> Any | None:
    """Cut back to a point after a complete value and append the missing closers."""
    stack: list[str] = []
    in_string = escaped = False
    # (cut_index, closers_needed_at_that_point) for every point where a value just finished
    cuts: list[tuple[int, str]] = []
    for i, ch in enumerate(s):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack:
                break
            stack.pop()
            cuts.append((i + 1, "".join(reversed(stack))))
            if not stack:
                break
        elif ch == ",":
            cuts.append((i, "".join(reversed(stack))))  # cut just before the comma
    for cut, closers in reversed(cuts[-_MAX_REPAIR_TRIES:]):
        candidate = _TRAILING_COMMA.sub(r"\1", s[:cut].rstrip().rstrip(",") + closers)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None
