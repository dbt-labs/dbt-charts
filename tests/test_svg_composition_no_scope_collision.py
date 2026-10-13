"""Standing guard: two boards on different themes must not collide when their
SVGs land in one HTML document (the docs themes page, or any Cloud surface
stacking more than one board — review diffs, merge conflicts, chat embeds).

Inline SVG in an HTML page has no style scope: every CSS rule a board's SVG
emits is document-global, so the last rule for a selector wins for every board
on the page. That is the general form of the bug fixed here one emitter at a
time — ``.md-heading`` in mdsvg, ``.dbt-table-row-link:hover`` in the table engine.
Identical duplicates (constant rules, ``@font-face``) are fine; two byte-equal
rules cannot conflict.

Scope: CSS selectors only. Element ids are the other document-global namespace
and have the same defect; their guard lives in
``test_svg_composition_no_id_collision.py``, which renders in separate
subprocesses because an in-process assertion would pass vacuously (vl-convert's
id counter keeps advancing across renders in one process).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")

_BOARD_YAML = """\
theme: {theme}
title: Board Title
queries:
  q1:
    type: values
    rows:
      - {{id: 1, name: "Apex"}}
      - {{id: 2, name: "Bright"}}
charts:
  c1:
    type: table
    query: q1
    link: "/detail/{{{{ id }}}}"
rows:
  - c1
"""


def setup_function() -> None:
    reset_config()


def teardown_function() -> None:
    reset_config()


def _render_svg(theme: str, local_project: Callable[..., FilesystemProject]) -> str:
    result = compile_board(_BOARD_YAML.format(theme=theme))
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor, format="svg").output
    assert isinstance(output, str)
    return output


def _css_rules(document: str) -> dict[str, set[str]]:
    """Map each CSS selector to the set of distinct declaration bodies it
    carries across every ``<style>`` block in the document (whitespace- and
    order-insensitive)."""
    rules: dict[str, set[str]] = {}
    for block in _STYLE_BLOCK_RE.findall(document):
        for raw_selector, raw_body in _CSS_RULE_RE.findall(block):
            selector = raw_selector.strip()
            if not selector or selector.startswith("@"):
                continue
            body = ";".join(
                sorted(part.strip() for part in raw_body.split(";") if part.strip())
            )
            rules.setdefault(selector, set()).add(body)
    return rules


def test_two_themed_boards_share_no_conflicting_css_selector(
    local_project: Callable[..., FilesystemProject],
) -> None:
    clarity_svg = _render_svg("clarity", local_project)
    neon_svg = _render_svg("neon", local_project)
    combined = f"<html><body>{clarity_svg}{neon_svg}</body></html>"

    rules = _css_rules(combined)
    # Positive control: `assert not conflicting` is also true of an empty dict,
    # so a change to the style-block format would turn this guard green instead
    # of red. Both boards must have contributed a scoped heading rule.
    headings = [sel for sel in rules if re.fullmatch(r"\.md-[0-9a-f]{8}-heading", sel)]
    assert len(headings) == 2, f"expected one scoped heading rule per board: {headings}"

    conflicting = {sel: bodies for sel, bodies in rules.items() if len(bodies) > 1}
    assert not conflicting, f"selectors with conflicting bodies: {conflicting}"
