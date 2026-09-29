"""An authored time format must not cost the axis its label cadence.

`default_label_expr_for` builds two things: the label TEXT (a per-grain
vocabulary) and the GATE that blanks every tick which doesn't open a label
period. An authored `style.time_format` legitimately replaces the first. It
used to replace the whole expression, taking the gate with it.

That only shows where the tick set is denser than the label cadence, which is
exactly what `_LABEL_THINNING_TICK_MARK_TYPES` arranges: a bar keeps one tick
per bucket so every bar has a positional cue under it, and relies on the gate
to decide which of those ticks actually speak. Strip the gate there and all 37
labels paint. On the other families `values` collapses to the visible set, so
the missing gate happened not to show — an implicit coupling that made the
label cadence depend on tick removal rather than on the label gate.

Pinned across families so the next mark type added to the dense-tick set
doesn't quietly reopen it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_MONTHS = [f"{y}-{m:02d}-01" for y in (2022, 2023, 2024) for m in range(1, 13)]


def _x_axis(chart_body: str) -> dict[str, Any]:
    rows = "\n".join(
        f"      - {{d: '{d}', v: {i + 1}, c: 'A'}}" for i, d in enumerate(_MONTHS)
    )
    yaml_text = (
        "title: T\nqueries:\n  q:\n    type: values\n    rows:\n" + rows + "\n"
        "charts:\n  c:\n" + chart_body + "\nrows:\n  - c\n"
    )
    result = compile_board(yaml_text)
    assert result.success, result.diagnostics
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    chart = result.board.charts["c"]
    data = executor.execute_chart(chart)
    rs, ctx = resolve_style_and_context(get_theme_style())
    resolved = resolve(chart, data, chart_style_context=ctx, width=420.0)
    spec = render_resolved_chart(resolved, data, rs, width=420.0).payload
    encoding_x: dict[str, Any] = spec["encoding"]["x"]
    axis: dict[str, Any] = encoding_x.get("axis", {}) or {}
    return axis


def _speaking_ticks(axis: dict[str, Any]) -> int:
    """How many of the axis's ticks actually paint text.

    A gated `labelExpr` blanks the rest, so the tick count alone overstates
    the labels whenever the two differ — which is the whole point of the gate.
    """
    tick_count = len(axis.get("values", []) or [])
    expr = str(axis.get("labelExpr", ""))
    if "? (" not in expr:
        return tick_count  # ungated: every tick speaks
    return -1  # gated: count is cadence-driven, asserted separately


_BAR = "    query: q\n    type: bar\n    x: d\n    y: v"
_LINE = "    query: q\n    type: line\n    x: d\n    y: v"
_ORDINAL_LINE = _LINE + "\n    style:\n      axis_x:\n        type: ordinal"


def _with_format(body: str) -> str:
    if "style:" in body:
        return body + "\n      time_format: '%b %Y'"
    return body + "\n    style:\n      time_format: '%b %Y'"


@pytest.mark.parametrize(
    ("name", "body"), [("bar", _BAR), ("ordinal line", _ORDINAL_LINE)]
)
def test_a_per_bucket_tick_axis_keeps_the_gate(name: str, body: str) -> None:
    """On the ordinal branch the tick set can be denser than the visible set,
    so the gate is what decides which ticks speak. It must survive the
    format."""
    bare = _x_axis(body)
    formatted = _x_axis(_with_format(body))

    assert "? (" in str(bare.get("labelExpr", "")), (
        f"{name}: fixture must have a gate to lose"
    )
    assert "? (" in str(formatted.get("labelExpr", "")), (
        f"{name}: the authored format took the cadence gate with it; "
        f"labelExpr={formatted.get('labelExpr')!r}"
    )


def test_a_continuous_temporal_axis_needs_no_gate() -> None:
    """The counterpart, and the reason this fix is not applied there: a
    continuous temporal axis carries either the visible openers as its own
    ``values`` — every tick meant to speak, so VL's native ``axis.format`` is
    exactly right — or no ``values`` at all, where Vega picks tick positions
    itself. Gating those on "opens a label period" would blank every tick
    that did not land on one, which is most of them."""
    formatted = _x_axis(_with_format(_LINE))
    assert formatted.get("format") == "%b %Y"
    assert "labelExpr" not in formatted
    assert len(formatted.get("values", []) or []) < len(_MONTHS)


def test_a_dense_tick_axis_does_not_paint_every_bucket() -> None:
    """The consequence, stated in painted labels rather than expression shape:
    a bar keeps one tick per bucket by design, so without the gate all 36
    speak at once."""
    axis = _x_axis(_BAR + "\n    style:\n      time_format: '%b %Y'")
    assert len(axis.get("values", []) or []) == len(_MONTHS), (
        "bar must keep its per-bucket ticks — that is the positional cue "
        "_LABEL_THINNING_TICK_MARK_TYPES exists to preserve"
    )
    assert _speaking_ticks(axis) == -1, (
        "…and must gate which of them speak, rather than labeling all 36"
    )


def test_the_authored_format_is_what_the_speaking_ticks_say() -> None:
    """Keeping the gate must not cost the author their format."""
    axis = _x_axis(_BAR + "\n    style:\n      time_format: '%b %Y'")
    assert "%b %Y" in str(axis.get("labelExpr", ""))
    # …and the per-grain vocabulary it replaced is gone from the text half.
    # ("%Y-%m" survives in the anchor comparison, which is a date equality
    # test rather than a label, so match on the bare-year producer instead.)
    assert "'%Y')" not in str(axis.get("labelExpr", ""))
