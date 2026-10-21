"""Render the diagnostic registry as a per-level markdown reference page.

Mirrors `compile.schema.renderers.prompt.render_prompt` for the YAML field
reference: introspect a single source of truth (here, `REGISTRY`) into
markdown, rendered when the docs site builds, never committed. One function, filtered by `level`: errors and warnings
differ only in which codes match.
"""

from __future__ import annotations

from typing import Literal

from dbt_charts.core.diagnostics.registry import (
    REGISTRY,
    DiagnosticCode,
    ErrorCode,
    WarningCode,
)

# Importing dbt_charts.core.diagnostics.registry first runs dbt_charts/core/diagnostics/__init__.py
# (Python always executes a parent package's __init__ before a submodule), which
# imports every codes_*.py and registers every ERR-*/WARN-* code as a side effect.

_TITLE: dict[str, str] = {
    "error": "dbt Charts Error Reference",
    "warning": "dbt Charts Warning Reference",
}

_INTRO: dict[str, str] = {
    "error": (
        "Auto-generated from `dbt_charts.core.diagnostics.REGISTRY`. Every "
        "`ERR-*` code dbt charts can raise, grouped by docs topic."
    ),
    "warning": (
        "Auto-generated from `dbt_charts.core.diagnostics.REGISTRY`. Every "
        "`WARN-*` code dbt charts can emit, grouped by docs topic. All warnings "
        "are suppressible via a query's `ignore:` or a chart's "
        "`warnings_ignore:` field."
    ),
}


def _fix_template(dc: DiagnosticCode) -> str | None:
    """fix_template is declared on ErrorCode/WarningCode, not the shared base
    (see registry.py's ErrorCode docstring for why it can't be a base field).
    isinstance narrows to the concrete subclass to read it.
    """
    if isinstance(dc, WarningCode):
        return dc.fix_template
    if isinstance(dc, ErrorCode):
        return dc.fix_template
    raise TypeError(f"Unexpected DiagnosticCode subclass: {type(dc).__name__}")


def _fence_for(text: str) -> str:
    """Return a backtick fence one run longer than the longest backtick run in text.

    A plain single backtick around arbitrary registry text is unsafe: any
    backtick, unbalanced-parenthesis-adjacent `{placeholder}` (which
    `attr_list` reads as an inline attribute list), or embedded newline in
    the wrapped text corrupts the surrounding markdown. A fence wider than
    every backtick run in the content can never be closed early by that
    content, and naturally handles multi-line templates too.
    """
    longest_run = 0
    run = 0
    for char in text:
        if char == "`":
            run += 1
            longest_run = max(longest_run, run)
        else:
            run = 0
    return "`" * max(3, longest_run + 1)


def _code_section(dc: DiagnosticCode) -> str:
    heading = f"### {dc.code}: {dc.title} {{: #{dc.code.lower()} }}"
    fence = _fence_for(dc.message_template)
    lines = [
        heading,
        "",
        f"- **Level:** {dc.level}",
        f"- **Domain:** {dc.domain}",
        f"- **Suppressible:** {'yes' if dc.level == 'warning' else 'no'}",
        "",
        "**Message template:**",
        "",
        fence,
        dc.message_template,
        fence,
    ]
    fix_template = _fix_template(dc)
    if fix_template is not None:
        lines.append("")
        lines.append(f"**Fix:** {fix_template}")
    lines.append("")
    lines.append(dc.doc)
    return "\n".join(lines)


def render_reference(level: Literal["error", "warning"]) -> str:
    """Render every registered code at `level` as markdown, grouped by `docs_topic`.

    Sections are H3 headings carrying an explicit `{: #<code-lowercased> }`
    inline attribute list — MkDocs' `toc: {permalink: true}` has no custom
    slugify, so a bare heading would slug from the whole title instead of the
    code, breaking every `build_doc_url()` anchor.
    """
    codes = REGISTRY.all(level=level)
    topics: dict[str, list[DiagnosticCode]] = {}
    for dc in codes:
        topics.setdefault(dc.docs_topic, []).append(dc)

    sections = [f"# {_TITLE[level]}\n", f"{_INTRO[level]}\n"]
    for topic in sorted(topics):
        sections.append(f"## {topic}\n")
        for dc in sorted(topics[topic], key=lambda d: d.code):
            sections.append(_code_section(dc))
    return "\n\n".join(sections) + "\n"
