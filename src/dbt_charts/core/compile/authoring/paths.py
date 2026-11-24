"""Authored paths: the dotted address of a node in board YAML.

A segment is written bare (`rows.0.title`) unless it is empty or holds `.`,
`[`, `]`, `"`, `\\`, a space or a control character; then it is a JSON string in
brackets (`tabs["Q1.5 / Q2"].rows.0`). The Rust engine writes the same
form.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

_NEEDS_QUOTES = re.compile(r'[.\[\]"\\\x00-\x20]')
_BARE = re.compile(r"[^.\[]+")
_QUOTED = re.compile(r'\["((?:[^"\\]|\\.)*)"\]')


def join_path(segments: Iterable[str | int]) -> str:
    out = ""
    for segment in map(str, segments):
        if segment and not _NEEDS_QUOTES.search(segment):
            out += f".{segment}" if out else segment
        else:
            out += f"[{json.dumps(segment, ensure_ascii=False)}]"
    return out


def split_path(path: str) -> list[str]:
    """The segments of `path`; raises ValueError on a malformed one."""
    segments: list[str] = []
    i = 0
    while i < len(path):
        if path[i] == "[":
            quoted = _QUOTED.match(path, i)
            if quoted is None:
                raise ValueError(f"Invalid path: {path!r}")
            segments.append(json.loads(f'"{quoted.group(1)}"'))
            i = quoted.end()
        else:
            bare = _BARE.match(path, i)
            if bare is None:
                raise ValueError(f"Invalid path: {path!r}")
            segments.append(bare.group())
            i = bare.end()
        if i < len(path):
            if path[i] == ".":
                i += 1
                if i == len(path):
                    raise ValueError(f"Invalid path: {path!r}")
            elif path[i] != "[":
                raise ValueError(f"Invalid path: {path!r}")
    return segments
