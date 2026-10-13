"""Shared helper for tests that need a rendered-SVG output of a minimal board."""

import re
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dbt_charts.agent_api.project_session import ProjectSession
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import raise_on_dashboard_failure
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project
from dbt_charts.core.render import render
from dbt_charts.core.render_format import RenderFormat

SAMPLE_BOARD_YAML = """
title: Test Dashboard
queries:
  q1:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
rows:
  - c1
"""


PINNED_RENDER_TIME = datetime(2001, 2, 3, 4, 5, 6, tzinfo=UTC)


def pinned_clock() -> datetime:
    """A clock that never moves, so two renders carry the same stamp."""
    return PINNED_RENDER_TIME


def render_board_to_svg(
    yaml_content: str = SAMPLE_BOARD_YAML, *, controls: bool = False
) -> str:
    """``controls=True`` renders as a live host would — the board a page binds,
    rather than a static export (which alone still carries the pagination
    script for a multi-page table)."""
    result = compile(yaml_content)
    assert result.success
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
        clock=pinned_clock,
    )
    return render(result.board, executor, format="svg", controls=controls).output


def board_with_mark(
    family: str, **mark_overrides: Any
) -> tuple[ResolvedStyle, ChartStyleContext]:
    """Resolve a board with the given family's mark style overridden.

    family is "line" or "area"; mark_overrides go onto charts.<family>.marks.<family>.
    halo_multiplier=0 keeps the spec to a single foreground mark for easy assertions.

    Returns (ResolvedStyle, ChartStyleContext) — unpack both at each call site.
    """
    compiled = get_theme_style("clarity")
    fam_style = getattr(compiled.charts, family)
    base_mark = getattr(fam_style.marks, family)
    new_mark = base_mark.model_copy(update={"halo_multiplier": 0.0, **mark_overrides})
    new_marks = fam_style.marks.model_copy(update={family: new_mark})
    new_fam = fam_style.model_copy(update={"marks": new_marks})
    charts = compiled.charts.model_copy(update={family: new_fam})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def cumulative_ink_x(svg: str, authored_path: str, until: str) -> float:
    """Absolute local x of the first ``until``-tagged element under the
    authored group at ``authored_path``, summing every ``translate(x, ...)``
    wrapper along the way (a padded box wraps its content in its own inset
    translate, on top of the group's own position) and skipping the
    selection-boundary rects, which carry no offset of their own.
    """
    open_tag = re.search(
        r'<g transform="translate\(([-\d.]+),\s*[-\d.]+\)"[^>]*'
        rf'data-authored-path="{re.escape(authored_path)}"[^>]*>',
        svg,
    )
    assert open_tag, f"no tagged group for authored path {authored_path!r}"
    total = float(open_tag.group(1))
    rest = svg[open_tag.end() :]
    while True:
        m = re.match(
            r'<g[^>]*transform="translate\(([-\d.]+),\s*[-\d.]+\)"[^>]*>\s*', rest
        )
        if m:
            total += float(m.group(1))
            rest = rest[m.end() :]
            continue
        m = re.match(r'<rect class="dbt-box-(?:outer|inner)"[^>]*/>\s*', rest)
        if m:
            rest = rest[m.end() :]
            continue
        break
    assert rest.startswith(f"<{until}"), f"expected <{until}, found: {rest[:80]!r}"
    return total


def legend_label_order(svg: str) -> list[str]:
    """Rendered color-legend label text, in SVG document (top-to-bottom) order.

    Mirrors ``chart_interactivity.js``'s ``legendSeriesOrder`` — the DOM order
    a real browser (or the JS tooltip runtime) sees. Vega's own SVG renderer
    marks each legend label group with the ``role-legend-label`` class
    (unrelated to the JS runtime's aria-label role markers).
    """
    labels = []
    for m in re.finditer(r'class="[^"]*\brole-legend-label\b[^"]*"', svg):
        text_match = re.search(
            r"<text[^>]*>([^<]*)</text>", svg[m.end() : m.end() + 300]
        )
        if text_match:
            labels.append(text_match.group(1))
    return labels


def render_board_file(
    path: Path,
    *,
    format: RenderFormat = "svg",
    project: Project | None = None,
    **render_options: Any,
) -> str | bytes:
    """Render an on-disk board file through the standard ProjectSession path.

    Mirrors the composition-root pattern used by Cloud and Playground (open a
    session, render, raise on failure, close) so tests exercising a saved board
    file don't hand-roll that plumbing. ``project`` defaults to the project
    discovered upward from ``path`` (matching a board with no explicit project).
    """
    session = (
        ProjectSession.from_project(project)
        if project is not None
        else ProjectSession.from_board(path)
    )
    try:
        board = session.project.path_for_fspath(path).read_board()
        rendered = session.render_board(board=board, format=format, **render_options)
        raise_on_dashboard_failure(rendered)
        # This helper renders to embeddable output (svg/html/png/pdf/text/yaml);
        # the json arm's dict payload is out of contract — fail loudly, not silently.
        assert isinstance(rendered.data, (str, bytes))
        return rendered.data
    finally:
        session.close()


def _translate_walk(
    svg: str, box: re.Pattern[str]
) -> Generator[tuple[float, float, re.Match[str], str | None], None, None]:
    """Walk ``svg``'s ``<g>``/``</g>`` tags, resolving the nested
    ``translate(...)`` chain into cumulative ``(x, y)``, and yield one
    ``(cum_x, cum_y, match, path)`` per ``box`` match found between the
    previous tag and this one. ``path`` is the innermost
    ``data-authored-path`` in scope, or ``None`` if none is open.

    Shared scan-then-push walker behind ``authored_boxes`` (keys results by
    ``path``) and ``variables_box_x`` (ignores ``path``, takes the first
    match).
    """
    tag = re.compile(r"<(/?)g\b([^>]*)>")
    translate = re.compile(r"translate\(\s*(-?[\d.]+)[,\s]+(-?[\d.]+)\s*\)")

    stack: list[tuple[float, float, str | None]] = [(0.0, 0.0, None)]
    pos = 0
    for m in tag.finditer(svg):
        found = box.search(svg[pos : m.start()])
        pos = m.end()
        if found:
            x, y, path = stack[-1]
            yield x, y, found, path
        closing, attrs = m.groups()
        if closing:
            if len(stack) > 1:
                stack.pop()
            continue
        if attrs.rstrip().endswith("/"):
            continue
        x, y, path = stack[-1]
        moved = translate.search(attrs)
        if moved:
            x += float(moved.group(1))
            y += float(moved.group(2))
        named = re.search(r'data-authored-path="([^"]*)"', attrs)
        stack.append((x, y, named.group(1) if named else path))


def authored_boxes(
    svg: str, box_class: str
) -> dict[str, tuple[float, float, float, float]]:
    """Every authored block's selection box of ``box_class``, in absolute page
    coordinates (``dbt-box-outer`` or ``dbt-box-inner``).

    Resolves the nested ``translate(...)`` chain so boxes from different depths
    are comparable — which is what "do two blocks' marks overlap" needs and what
    a per-group local offset cannot answer. Both boxes carry their own ``x``/``y``
    on top of that chain: the inner one its padding inset, the outer one whatever
    ``selection_boxes`` seated its mark on, which is not the group's origin on a
    block whose mark is a line box inside it.

    Returns ``authored_path -> (x, y, width, height)``.
    """
    box = re.compile(
        rf'<rect class="{box_class}" x="(-?[\d.]+)" y="(-?[\d.]+)"'
        r'[^>]*?width="([\d.]+)" height="([\d.]+)"'
    )
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for x, y, found, path in _translate_walk(svg, box):
        if path is not None:
            boxes[path] = (
                x + float(found.group(1)),
                y + float(found.group(2)),
                float(found.group(3)),
                float(found.group(4)),
            )
    return boxes


def variables_box_x(svg: str) -> float:
    """Absolute local x of the variables strip's ``data-dbt-variables-box``
    anchor rect, resolving the ``translate(...)`` chain above it. The rect's
    own ``x`` is always ``"0"`` (see ``render_variables_strip_svg``), so its
    position on the board is entirely the transform chain — the same
    resolution ``authored_boxes`` does for authored blocks, but keyed to this
    unpathed anchor instead of a ``data-authored-path`` group.
    """
    box = re.compile(r'<rect data-dbt-variables-box="true"[^>]*\sx="(-?[\d.]+)"')
    for x, _y, found, _path in _translate_walk(svg, box):
        return x + float(found.group(1))
    raise AssertionError("no data-dbt-variables-box rect found in svg")


def leaf_kind_subtrees(svg: str, kind: str) -> list[str]:
    """Every ``data-authored-kind="<kind>"`` group, each as its whole subtree.

    Walks ``<g>``/``</g>`` depth to the *matching* close tag. A non-greedy
    ``(.*?)</g>`` regex stops at the first close instead, which on a group whose
    first child is itself a ``<g>`` returns that child and nothing else — a
    containment assertion written that way passes without ever seeing the
    content it was meant to reject.
    """
    out = []
    for opening in re.finditer(
        rf'<g[^>]*data-authored-kind="{re.escape(kind)}"[^>]*>', svg
    ):
        depth = 0
        for tag in re.finditer(r"<g\b[^>]*>|</g>", svg[opening.start() :]):
            depth += 1 if tag.group(0).startswith("<g") else -1
            if depth == 0:
                out.append(svg[opening.start() : opening.start() + tag.end()])
                break
    return out
