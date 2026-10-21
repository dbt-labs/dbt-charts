"""Rich-formatted output for Diagnostic lists (errors and warnings alike).

In plain mode (agent or pipe context) we construct the Console with
``force_terminal=False, no_color=True`` and skip the surrounding
``Panel`` wrapper. Inline Rich markup like ``[bold]``, ``[dim]`` is
consumed by Rich's parser either way — when the Console isn't emitting
ANSI, the markup leaves no trace in the output. So a single body
composition handles both surfaces; only the Panel wrapper needs gating.

One formatter for both severities: each Diagnostic carries its own `level`
(resolved from the registry), so color and treatment come from the instance,
not from a caller-supplied flag.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rich.markup import escape

from dbt_charts.agent_api import Diagnostic, posix_relpath
from dbt_charts.agent_api.diagnostics import REGISTRY, display_message
from dbt_charts.cli._console import dct_console


def _display_path(abs_path: str, line: int | None, cwd: Path | None = None) -> str:
    """Return path relative to cwd when possible, with optional :line suffix."""
    p = Path(abs_path)
    base = cwd if cwd is not None else Path.cwd()
    try:
        display = posix_relpath(p, base)
    except ValueError:
        display = abs_path
    return f"{display}:{line}" if line is not None else display


def print_error(message: str) -> None:
    """Print a plain top-level CLI error to stderr — no Diagnostic, no traceback.

    For startup-time failures that precede any Diagnostic (e.g. project
    discovery) rather than a compile/render error tied to a file or chart.
    """
    console = dct_console(stderr=True)
    if console.no_color:
        console.print(f"Error: {message}")
    else:
        console.print(f"[bold red]Error:[/] {message}")


def print_warning(message: str) -> None:
    """Print a plain top-level CLI warning to stderr — non-fatal, no Diagnostic.

    For advisory conditions detected outside any compile/render pass (e.g. a
    workspace/build mismatch) that must never affect the subcommand's exit
    code. `soft_wrap=True` keeps it to one line rather than wrapping at the
    console width, so a machine reading stderr (e.g. `--diagnostics-json`)
    sees one stray line instead of several.
    """
    console = dct_console(stderr=True)
    # `message` is caller-supplied prose carrying filesystem paths; a bracketed
    # directory name is markup to Rich either way (`no_color` suppresses ANSI,
    # not parsing), so an unescaped `[old]` segment vanishes from the path and
    # a `[/]` raises MarkupError — turning a non-fatal advisory into a crash.
    body = escape(message)
    if console.no_color:
        console.print(f"Warning: {body}", soft_wrap=True)
    else:
        console.print(f"[bold yellow]Warning:[/] {body}", soft_wrap=True)


def emit_diagnostics_jsonl(diags: list[Diagnostic]) -> None:
    """Write diagnostics as JSON Lines to stderr — one compact JSON object per line.

    Callers use this for the ``--diagnostics-json`` path: every diagnostic
    (error or warning) is emitted to stderr so stdout stays the render payload,
    and line-delimited JSON survives a truncated read with whole objects intact.

    The full model is emitted, nulls included: a reader branching on ``range``
    or ``columns`` gets the field either way, rather than having to tell an
    omitted key from one it does not understand.
    """
    for d in diags:
        print(json.dumps(d.model_dump(mode="json")), file=sys.stderr)


def _blank_names(text: str, d: Diagnostic) -> str:
    # Only quoted names and the "(query: x)" suffix: messages quote names with
    # repr, while a bare substring match would blank "int" inside "expected int".
    for name in (d.chart, d.query):
        if name:
            for spelled in (f"'{name}'", f'"{name}"', f"(query: {name})"):
                text = text.replace(spelled, "<name>")
    return text


def _signature(d: Diagnostic) -> tuple[str, ...]:
    """Every human-visible text of `d` with its own chart/query name blanked, so
    instances of one code that differ only by which chart they hit compare equal."""
    evidence = "; ".join(d.fields.get("evidence") or [])
    texts = (
        display_message(d),
        d.fix,
        d.hint,
        d.detail,
        d.fields.get("detail"),
        evidence,
    )
    return tuple(_blank_names(str(text or ""), d) for text in texts)


def group_diagnostics(
    diags: list[Diagnostic], *, path: str | None = None
) -> list[list[Diagnostic]]:
    """Group diagnostics for the human text output: one group per code.

    A diagnostic with no chart, query, range, or caller `path` has nothing to
    tell it apart from its siblings, so it stays its own group rather than being
    folded into a count that hides which one fired. Errors also split on text:
    collapsing differing ones would hide a problem the reader has to fix.
    Insertion order is kept.
    """
    groups: dict[tuple[str, tuple[str, ...] | None, int | None], list[Diagnostic]] = {}
    for index, d in enumerate(diags):
        located = path or d.range or d.chart or d.query
        key = (
            d.code,
            _signature(d) if d.level == "error" else None,
            None if located else index,
        )
        groups.setdefault(key, []).append(d)
    return list(groups.values())


_RESTATED_CODES = (
    "WARN-RESTATED-STYLE",
    "WARN-RESTATED-FORMAT",
    "WARN-RESTATED-INFERRED",
)  # most specific cause first: it wins when one key is reported twice


def one_per_path(warnings: list[Diagnostic]) -> list[Diagnostic]:
    """Keep one RESTATED warning per authored (file, path): the most specific
    cause. Other warnings pass through, ahead of the restated ones."""
    best: dict[tuple[str | None, str | None], Diagnostic] = {}
    others = [w for w in warnings if w.code not in _RESTATED_CODES]
    for w in warnings:
        if w.code not in _RESTATED_CODES:
            continue
        key = (w.range.file if w.range else None, w.path)
        held = best.get(key)
        if held is None or _RESTATED_CODES.index(w.code) < _RESTATED_CODES.index(
            held.code
        ):
            best[key] = w
    return [*others, *best.values()]


def _restated_block(warnings: list[Diagnostic], path: str | None) -> list[str]:
    n = len(warnings)
    lines = [
        f"{n} {'key restates' if n == 1 else 'keys restate'} a default; delete {'it' if n == 1 else 'them'}:"
    ]
    rows = []
    for w in warnings:
        where = (
            _display_path(w.range.file, w.range.start_line) if w.range else path or ""
        )
        rows.append((where, f"{w.path}: {w.fields['value']}", w.fields["why"]))
    width = max(len(where) for where, _, _ in rows)
    lines += [f"  {where:<{width}}  {shown}  ({why})" for where, shown, why in rows]
    return lines


def _error_lines(group: list[Diagnostic], path: str | None) -> list[str]:
    d = group[0]
    message = display_message(d)
    if len(group) == 1 and d.chart:
        message = f"{d.chart}: {message}"
    count = f" x{len(group)}" if len(group) > 1 else ""
    head = f"{d.level}[{d.code}]{count}: " + message.replace("\n", "\n  ")
    lines = [head]
    body: list[str] = []
    if len(group) > 1:
        names = dict.fromkeys(i.chart or i.query for i in group)
        if applies_to := [name for name in names if name]:
            body.append(f"Applies to: {', '.join(applies_to)}")
        unshown = sum(_signature(i) != _signature(d) for i in group)
        if unshown:
            body.append(f"and {unshown} more with different details")
    if d.hint:
        body.append(f"Hint: {d.hint}")
    if d.fix:
        body.append(f"Fix: {d.fix}")
    if d.field:
        body.append(f"Field: {d.field}")
    for detail in (d.detail, d.fields.get("detail")):
        text = str(detail or "").strip()
        if text and text.rstrip(".") not in message:
            body.append(f"Detail: {text}")
            break
    if evidence := d.fields.get("evidence"):
        body.append(f"Evidence: {'; '.join(evidence)}")
    locations = dict.fromkeys(
        _display_path(i.range.file, i.range.start_line) if i.range else path
        for i in group
    )
    if shown := [loc for loc in locations if loc is not None]:
        if d.level == "error" or len(shown) == 1:
            body.append(f"At: {', '.join(shown)}")
        else:
            body.append(f"At: {shown[0]} (+{len(shown) - 1} more)")
    return lines + [f"  {line}" for line in body]


def render_diagnostics(diags: list[Diagnostic], *, path: str | None = None) -> str:
    """The human text for `diags`: errors first, then warnings, rustc-style.

    One entry per code and text (repeats share it with an "Applies to:" line),
    detail lines indented under the headline, a blank line between entries, and
    one compact block for every WARN-RESTATED-* key. Each distinct code's docs
    link is printed once at the end. Text only: JSON, LSP and MCP carry every
    diagnostic with its own code, path, range, fix and docs.
    """
    errors = [d for d in diags if d.level == "error"]
    warnings = [d for d in diags if d.level != "error"]
    warnings = one_per_path(warnings)
    restated = [w for w in warnings if w.code in _RESTATED_CODES]
    others = [w for w in warnings if w.code not in _RESTATED_CODES]
    entries = [
        _error_lines(group, path)
        for group in group_diagnostics([*errors, *others], path=path)
    ]
    if restated:
        entries.append(_restated_block(restated, path))
    codes = dict.fromkeys(d.code for d in [*errors, *others, *restated])
    docs = "  ".join(f"{code} {REGISTRY.get(code).doc_url}" for code in codes)
    text = "\n\n".join("\n".join(entry) for entry in entries)
    return f"{text}\n\nDocs: {docs}" if entries else ""


def print_diagnostics(
    diags: list[Diagnostic],
    *,
    path: str | None = None,
) -> None:
    """Print Diagnostics to stderr: errors before warnings, one entry per code.

    `path` is a fallback "At:" location used only when a diagnostic has no
    `range` of its own. Does not hard-wrap.
    """
    text = render_diagnostics(diags, path=path)
    if not text:
        return
    console = dct_console(stderr=True)
    styled = []
    for line in text.split("\n"):
        style = (
            "bold red"
            if line.startswith("error[")
            else "bold yellow"
            if line.startswith("warning[") or "a default; delete " in line
            else None
        )
        styled.append(f"[{style}]{escape(line)}[/]" if style else escape(line))
    console.print("\n".join(styled), soft_wrap=True, highlight=False)
