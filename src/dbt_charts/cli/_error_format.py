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
from rich.panel import Panel

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


def print_diagnostics(
    diags: list[Diagnostic],
    *,
    path: str | None = None,
) -> None:
    """Print Diagnostics as Rich panels to stderr.

    `path` is a fallback "At:" location used only when a diagnostic has no
    `range` of its own (e.g. compile-time warnings that don't carry position
    data yet).
    """
    console = dct_console(stderr=True)
    for d in diags:
        style = "red" if d.level == "error" else "yellow"
        chart_prefix = f"{d.chart}: " if d.chart else ""
        body_parts = [
            f"[bold {style}]{d.code}[/]  {chart_prefix}{escape(display_message(d))}"
        ]
        if d.hint:
            body_parts.append(f"[dim]Hint:[/] {escape(d.hint)}")
        if d.fix:
            body_parts.append(f"[dim]Fix:[/] {escape(d.fix)}")
        if d.field:
            body_parts.append(f"[dim]Field:[/] {d.field}")
        if d.detail:
            body_parts.append(f"[dim]Detail:[/] {escape(d.detail)}")
        # `fields` also carries programmatic-only metadata (chart_id,
        # severity, confidence) that other consumers key off of — only
        # `detail`/`evidence` are meant for a human to read.
        if detail := d.fields.get("detail"):
            body_parts.append(f"[dim]Detail:[/] {escape(str(detail))}")
        if evidence := d.fields.get("evidence"):
            body_parts.append(f"[dim]Evidence:[/] {escape('; '.join(evidence))}")
        at = _display_path(d.range.file, d.range.start_line) if d.range else path
        if at is not None:
            body_parts.append(f"[dim]At:[/] {at}")
        body_parts.append(f"[dim]Docs:[/] dct docs {REGISTRY.get(d.code).docs_topic}")
        body = "\n".join(body_parts)
        if console.no_color:
            console.print(body)
        else:
            console.print(Panel(body, border_style=style, expand=False))
