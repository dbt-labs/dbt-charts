"""Markdown fenced code blocks, which board text keeps out of Jinja."""

import re
from collections.abc import Callable

_MARKDOWN_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})([^\r\n]*)$")


def resolve_outside_fences(text: str, resolve: Callable[[str], str]) -> str:
    masked, fences = mask_fenced_code(text)
    resolved = resolve(masked)
    for token, fence in fences.items():
        resolved = resolved.replace(token, fence)
    return resolved


def mask_fenced_code(text: str) -> tuple[str, dict[str, str]]:
    fences: dict[str, str] = {}
    result: list[str] = []
    fence_marker: str | None = None
    fence_lines: list[str] = []

    for line in text.splitlines(keepends=True):
        if fence_marker is not None:
            fence_lines.append(line)
            if _is_markdown_fence_close(line, fence_marker):
                token = f"__DCT_MARKDOWN_FENCE_{len(fences)}__"
                fences[token] = "".join(fence_lines)
                result.append(token)
                fence_marker = None
                fence_lines = []
            continue

        fence_marker = _markdown_fence_marker(line)
        if fence_marker is not None:
            fence_lines = [line]
            continue

        result.append(line)

    if fence_marker is not None:
        result.extend(fence_lines)

    return "".join(result), fences


def _markdown_fence_marker(line: str) -> str | None:
    match = _MARKDOWN_FENCE_RE.match(line)
    if match is None:
        return None
    return match.group(1)


def _is_markdown_fence_close(line: str, opening_marker: str) -> bool:
    marker_char = re.escape(opening_marker[0])
    min_width = len(opening_marker)
    return bool(re.match(rf"^\s*{marker_char}{{{min_width},}}\s*$", line))
