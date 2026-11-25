"""`style.key.direct.overlay_x`: a line or area names each series at an x of its own."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.normalized import AreaChart, LineChart
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.style.resolved._cartesian import (
    ResolvedDirectOverlayX,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

NOW = datetime(2024, 6, 14, 9, 30, tzinfo=UTC)
MONTHS = [date(2024, m, 1) for m in range(1, 7)]
ROWS: list[dict[str, Any]] = [
    {"month": m, "region": region, "revenue": float(i + 1) * scale}
    for i, m in enumerate(MONTHS)
    for region, scale in (("EMEA", 1.0), ("APAC", 2.0))
    if not (region == "APAC" and i == 5)
]


def _direct(at: dict[str, Any], **direct: Any) -> dict[str, Any]:
    return {"key": {"type": "direct", "direct": {"overlay_x": at, **direct}}}


def _chart(
    kind: str,
    at: dict[str, Any],
    rows: list[dict[str, Any]] = ROWS,
    **chart: Any,
) -> ResolvedLineChart | ResolvedAreaChart:
    model = LineChart if kind == "line" else AreaChart
    normalized = model.model_validate(
        {
            "id": "rev",
            "type": kind,
            "x": "month",
            "y": "revenue",
            "color": "region",
            "query_name": "q",
            "style": _direct(at),
            **chart,
        }
    )
    resolved = resolve(
        normalized,
        rows,
        resolve_chart_style_context(get_theme_style(get_default_theme_name())),
        rendered_at=NOW,
    )
    assert isinstance(resolved, ResolvedLineChart | ResolvedAreaChart)
    return resolved


@pytest.mark.parametrize("kind", ["line", "area"])
def test_a_date_x_resolves_per_series(kind: str) -> None:
    labels = _chart(kind, {"EMEA": date(2024, 3, 1)}).style.endpoint_labels
    [at] = labels.overlay_x
    assert at.series == "EMEA"
    assert at.x.kind == "value" and at.x.value == "2024-03-01"
    assert labels.unseen_overlay_x == ()


def test_max_reads_each_series_own_span() -> None:
    labels = _chart("line", {"EMEA": "max()", "APAC": "max()"}).style.endpoint_labels
    assert {at.series: at.x.value for at in labels.overlay_x} == {
        "EMEA": "2024-06-01",
        "APAC": "2024-05-01",
    }


def test_now_reads_the_render_time() -> None:
    [at] = _chart("line", {"EMEA": "now()"}).style.endpoint_labels.overlay_x
    assert at.x.value.startswith("2024-06-14")


def test_a_number_x_resolves_as_a_number() -> None:
    rows = [{"n": n, "region": "A", "v": float(n)} for n in range(5)]
    labels = _chart("line", {"A": 3}, rows, x="n", y="v", color="region")
    [at] = labels.style.endpoint_labels.overlay_x
    assert at.x.value == 3.0


def test_a_category_x_resolves_to_a_category() -> None:
    rows = [
        {"q": q, "region": r, "v": 1.0 + i}
        for i, q in enumerate(["Q1", "Q2", "Q3"])
        for r in ("A", "B")
    ]
    labels = _chart("line", {"A": "Q2"}, rows, x="q", y="v", color="region")
    [at] = labels.style.endpoint_labels.overlay_x
    assert at.x.kind == "category" and at.x.value == "Q2"


def test_a_wide_chart_keys_by_column_name() -> None:
    rows = [
        {"month": m, "emea": float(i), "apac": float(i * 2)}
        for i, m in enumerate(MONTHS)
    ]
    labels = _chart(
        "line", {"apac": "max()"}, rows, y=["emea", "apac"], color=None
    ).style.endpoint_labels
    assert [at.series for at in labels.overlay_x] == ["apac"]


def test_an_unseen_series_is_collected_not_refused() -> None:
    labels = _chart(
        "line", {"EMEA": date(2024, 3, 1), "Oceania": date(2024, 3, 1)}
    ).style.endpoint_labels
    assert [at.series for at in labels.overlay_x] == ["EMEA"]
    assert labels.unseen_overlay_x == ("Oceania",)


def test_an_x_that_does_not_fit_the_axis_is_refused() -> None:
    with pytest.raises(CompilationError, match=r"direct\.overlay_x.*EMEA"):
        _chart("line", {"EMEA": "not a date"})


def test_a_range_function_is_refused() -> None:
    with pytest.raises(CompilationError, match="EMEA"):
        _chart("line", {"EMEA": "current(month)"})


def test_the_resolved_anchor_is_a_resolved_model() -> None:
    [at] = _chart("line", {"EMEA": "max()"}).style.endpoint_labels.overlay_x
    assert isinstance(at, ResolvedDirectOverlayX)


_BOARD = """title: T
queries:
  q1: {{sql: "select 1 as a", source: test}}
{board_style}charts:
  c1:
    type: {kind}
    query: q1
    x: a
    y: a
{chart_style}rows: [c1]
"""
_AT = """key:
  direct:
    overlay_x:
      EMEA: 2024-03-01
"""


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "".join(f"{pad}{line}\n" for line in text.splitlines())


def _board(kind: str, board_style: str = "", chart_style: str = "") -> Any:
    return yaml.safe_load(
        _BOARD.format(kind=kind, board_style=board_style, chart_style=chart_style)
    )


@pytest.mark.parametrize("scope", ["line", "area"])
def test_theme_or_board_scope_refuses_at(scope: str) -> None:
    doc = _board(scope, "style:\n  charts:\n    " + scope + ":\n" + _indent(_AT, 6))
    with pytest.raises(
        ValidationError,
        match=rf"`key\.direct\.overlay_x` is chart-local.*style\.charts\.{scope}",
    ):
        AuthoredBoard.model_validate(doc)


def test_chart_scope_accepts_at() -> None:
    doc = _board("line", chart_style="    style:\n" + _indent(_AT, 6))
    AuthoredBoard.model_validate(doc)


def test_a_bar_does_not_take_at() -> None:
    doc = _board("bar", chart_style="    style:\n" + _indent(_AT, 6))
    with pytest.raises(ValidationError, match="overlay_x"):
        AuthoredBoard.model_validate(doc)


def test_the_engine_sets_a_name_at_its_authored_x() -> None:
    from ...._svg_render import render_board_svg_result

    def name_x(at: str) -> float:
        board = f"""
queries:
  q:
    type: values
    rows: {json.dumps(ROWS, default=str)}
charts:
  c:
    type: line
    query: q
    x: month
    y: revenue
    color: region
    style:
      marks:
        point:
          size: 0
      key:
        type: direct
        direct:
          position: overlay
          overlay_x:
            EMEA: {at}
rows:
  - c
"""
        svg = render_board_svg_result(board).output
        # A name along its line is a tspan per glyph; its first glyph's x reads like a
        # straight name's.
        tag = next(
            t
            for t in re.findall(r"<text[^>]*data-dbt-series.*?</text>", svg, re.S)
            if re.sub(r"<[^>]+>", "", t) == "EMEA"
        )
        return float(re.search(r' x="([\d.]+)', tag).group(1))

    assert name_x("2024-05-01") - name_x("2024-02-01") > 100
