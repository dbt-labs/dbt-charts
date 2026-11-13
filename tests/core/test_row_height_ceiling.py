"""End-to-end: an authored row `height:` is a ceiling, not a floor.

Settled 2026-08-07: when a row's content resolves shorter than its authored height, the row
shrinks to the content. The authored height still drives the content's own
resolution — a chart given room to use it may use it — it simply stops being
the row's own allocated height when the content didn't use all of it.

These tests run the full ``compile()`` -> ``render()`` pipeline and assert on
the emitted SVG (board ``viewBox`` height, tile ``data-chart-height``) rather
than an intermediate sizing structure — the bug lived in how the sizing pass's
per-item height fed the render pass's nested-board floor
(``render_nested_board``'s ``max(natural_board_height, allocated_board_height)``
in ``render/boards.py``), so only the emitted SVG proves the fix reaches it.

Assertions are relative (authored-ceiling case vs. natural-content case)
rather than pinned to exact theme-derived pixel constants — see
``dbt-charts/AGENTS.md`` "Don't pin theme/default values in tests."
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from .._run import Run

_VIEWBOX_RE = re.compile(r'viewBox="0 0 [\d.]+ ([\d.]+)"')
_CHART_HEIGHT_RE = re.compile(r'data-chart-height="([\d.]+)"')


def _render_svg(yaml_body: str, local_project: Callable[..., FilesystemProject]) -> str:
    result = compile_board(yaml_body)
    assert result.success, result.errors
    assert result.board is not None
    executor = Run(
        result.board,
        env=build_exec_env(local_project(Path.cwd())),
    )
    render_result = render(result.board, executor.ctx, executor.ledger, format="svg")
    assert not render_result.warnings or all(
        d.code != "WARN-LAYOUT-MIN-EXCEEDS-HEIGHT" for d in render_result.warnings
    ), "unexpected height-overflow warning"
    output = render_result.output
    assert isinstance(output, str)
    return output


def _board_height(svg: str) -> float:
    match = _VIEWBOX_RE.search(svg)
    assert match, f"no board viewBox in SVG: {svg[:200]}"
    return float(match.group(1))


def _chart_heights(svg: str) -> list[float]:
    return [float(h) for h in _CHART_HEIGHT_RE.findall(svg)]


def _table_rows(n: int) -> str:
    return "\n".join(
        f"      - {{region: R{i}, total: {i * 10}}}" for i in range(1, n + 1)
    )


def _bar_rows(n: int) -> str:
    return "\n".join(f"      - {{cat: C{i}, value: {i * 10}}}" for i in range(1, n + 1))


def _table_board(height: str | None, n_rows: int = 3) -> str:
    row = f"  - height: {height}\n    rows:\n      - c1" if height else "  - c1"
    return f"""\
theme: clarity
title: Test
width: 872
rows:
{row}
queries:
  q:
    type: values
    rows:
{_table_rows(n_rows)}
charts:
  c1:
    query: q
    type: table
"""


def _bar_board(height: str | None, n_cats: int = 3) -> str:
    row = f"  - height: {height}\n    rows:\n      - c1" if height else "  - c1"
    return f"""\
theme: clarity
title: Test
width: 872
rows:
{row}
queries:
  q:
    type: values
    rows:
{_bar_rows(n_cats)}
charts:
  c1:
    query: q
    type: bar
    x: cat
    y: value
"""


class TestRowShrinksToContent:
    """A row's height must be the height its content actually resolved to."""

    def test_authored_heights_above_natural_content_all_collapse_to_the_same_board_height(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A 3-row table needs far less than 150/250/360px — every authored
        ceiling above that natural need must produce the identical board
        height (the row shrinks to content, not to whatever ceiling was
        authored)."""
        svg_360 = _render_svg(_table_board("360"), local_project)
        svg_250 = _render_svg(_table_board("250"), local_project)
        svg_150 = _render_svg(_table_board("150"), local_project)

        assert (
            _board_height(svg_360) == _board_height(svg_250) == _board_height(svg_150)
        )
        # The tile itself renders at its natural row-count height regardless
        # of the row's authored ceiling.
        assert (
            _chart_heights(svg_360)
            == _chart_heights(svg_250)
            == _chart_heights(svg_150)
        )

    def test_authored_height_still_drives_content_up_to_its_own_max_height_clamp(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The authored height still drives the content's own resolution: a
        3-category bar given 360px renders at 360px (an authored ceiling
        below the chart's own max_height clamp is not a no-op — it is
        honored exactly). Once the authored ceiling reaches or exceeds the
        chart's own max_height clamp (432px here), further increases (500,
        700) are pure no-ops — content, and so the row, stops growing."""
        svg_360 = _render_svg(_bar_board("360"), local_project)
        assert _chart_heights(svg_360) == [360.0]

        svg_432 = _render_svg(_bar_board("432"), local_project)
        svg_500 = _render_svg(_bar_board("500"), local_project)
        svg_700 = _render_svg(_bar_board("700"), local_project)

        assert (
            _chart_heights(svg_432)
            == _chart_heights(svg_500)
            == _chart_heights(svg_700)
        )
        assert (
            _board_height(svg_432) == _board_height(svg_500) == _board_height(svg_700)
        )
        # ...and that clamped ceiling is taller than the 360px case: growing
        # the authored height did resolve to more content, up to the clamp.
        assert _board_height(svg_432) > _board_height(svg_360)

    def test_content_taller_than_authored_keeps_the_ceiling_and_still_warns(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Content that needs MORE than the authored height is the opposite,
        out-of-scope failure mode (WARN-LAYOUT-MIN-EXCEEDS-HEIGHT, shipped
        2026-08-05) — this fix must not touch it. An 8-category horizontal
        bar authored at a too-small 150px must still render (and reserve
        board space for) its full readability-floor height rather than being
        shrunk down to 150px, and the warning must still fire."""
        result = compile_board(_bar_board("150", n_cats=8))
        assert result.success, result.errors
        assert result.board is not None
        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
        )
        render_result = render(
            result.board, executor.ctx, executor.ledger, format="svg"
        )
        codes = [d.code for d in render_result.warnings]
        assert "WARN-LAYOUT-MIN-EXCEEDS-HEIGHT" in codes

        svg = render_result.output
        assert isinstance(svg, str)
        chart_heights = _chart_heights(svg)
        assert len(chart_heights) == 1
        # Nowhere near shrunk to the 150px ceiling — the readability floor
        # for 8 category bands is well over double that.
        assert chart_heights[0] > 300.0
        assert _board_height(svg) > chart_heights[0]

    def test_nested_rows_ceiling_applies_at_each_level(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """An authored ceiling on an outer rows wrapper around another
        authored-ceiling rows wrapper shrinks at both levels, not just the
        innermost one."""
        yaml_body = f"""\
theme: clarity
title: Test
width: 872
rows:
  - height: 500
    rows:
      - height: 360
        rows:
          - c1
queries:
  q:
    type: values
    rows:
{_table_rows(3)}
charts:
  c1:
    query: q
    type: table
"""
        nested_svg = _render_svg(yaml_body, local_project)
        flat_svg = _render_svg(_table_board("360"), local_project)

        # The outer 500px ceiling must not add anything beyond what the inner
        # 360px ceiling (itself clamped to content) already resolved to.
        assert _board_height(nested_svg) == _board_height(flat_svg)

    def test_decorative_cell_with_no_measurable_content_keeps_its_authored_height(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A cell with nothing independently measurable inside — no chart, no
        text, just a colored background fill (the `apps/docs/docs/boards/
        imports.md` "Palette Swatches" pattern) — has no "actual resolution"
        to shrink to. Its authored height must stay authoritative rather than
        clamping to zero and vanishing (a real regression this fix's first
        version caused before the content>0 guard was added)."""
        yaml_body = """\
theme: clarity
title: Swatches
width: 400
rows:
  - height: 60px
    cols:
      - text: " "
        width: 60px
        style: {background: "vivid-10.1"}
      - text: " "
        width: 60px
        style: {background: "vivid-10.2"}
"""
        svg = _render_svg(yaml_body, local_project)
        # Match on the 60x60 swatch shape, not the bare height: the row wrapper
        # is also 60 tall (it keeps its authored height), so filtering on height
        # alone cannot tell a surviving swatch from the row containing it.
        # Parse floats rather than matching the literal "60.0" so a
        # float-formatting change in the SVG writer fails loudly instead of
        # silently passing.
        boxes = [
            (float(w), float(h))
            for w, h in re.findall(
                r'<svg[^>]*\swidth="([\d.]+)"[^>]*\sheight="([\d.]+)"', svg
            )
        ]
        swatches = [
            (w, h)
            for w, h in boxes
            if w == pytest.approx(60.0) and h == pytest.approx(60.0)
        ]
        assert len(swatches) == 2, (
            f"expected both authored 60x60 swatches to survive, got "
            f"{swatches} out of nested svg boxes {boxes}"
        )
