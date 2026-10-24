"""Regression: the pre-resolve variables-Jinja pass must not touch ``link``.

Chart ``link:`` templates carry datum-scoped placeholders (``{{ x }}``,
``{{ color }}``, table ``{{ column_id }}``) that the render layer substitutes
per mark/row — click_interactivity's Vega calculate expression for VL charts,
``resolve_cell_link_with_board`` for table cells. The variables Jinja pass in
``_require_resolved`` runs earlier, with the board *variables* context at
``strict=False``; before the fix it rendered ``link`` too, silently blanking
every datum placeholder on any board that declares variables
(``?priority={{ x }}`` → ``?priority=``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.primitives import BoardLinks
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.layout_sizing import SizingRenderCtx, _require_resolved

_ROWS = [
    {"priority": "low", "ticket_count": 3},
    {"priority": "urgent", "ticket_count": 7},
]


def _make_bar_chart(**overrides: Any) -> Chart:
    payload: dict[str, Any] = {
        "id": "c1",
        "query": SqlQuery(sql="SELECT 1", source="test"),
        "query_name": "q",
        "type": "bar",
        "x": "priority",
        "y": "ticket_count",
        "style": None,
        **overrides,
    }
    return TypeAdapter(Chart).validate_python(payload)


def _make_ctx() -> SizingRenderCtx:
    theme = get_theme_style()
    return SizingRenderCtx(
        resolved_style=resolve_style(theme),
        chart_style_context=resolve_chart_style_context(theme),
    )


def _make_executor() -> MagicMock:
    executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    executor.rendered_at = None
    executor.render_with_range_filters.return_value = None
    executor.execute_query.return_value = _ROWS
    return executor


def test_link_datum_placeholders_survive_variables_pass() -> None:
    """``{{ x }}`` in link is a datum ref, not a variable — it must pass through."""
    chart = _make_bar_chart(link="/zendesk/backlog/?priority={{ x }}")
    ctx = _make_ctx()

    resolved = _require_resolved(
        ctx,
        chart,
        _make_executor(),
        {"region": "EU"},
        400.0,
        ctx.resolved_style,
        ctx.chart_style_context,
    )

    assert resolved.link == "/zendesk/backlog/?priority={{ x }}"


def test_title_still_substitutes_variables() -> None:
    """Presentation text keeps variable interpolation; only ``link`` is exempt."""
    chart = _make_bar_chart(
        title="Tickets — {{ region }}",
        link="?priority={{ x }}",
    )
    ctx = _make_ctx()

    resolved = _require_resolved(
        ctx,
        chart,
        _make_executor(),
        {"region": "EU"},
        400.0,
        ctx.resolved_style,
        ctx.chart_style_context,
    )

    assert resolved.title == "Tickets — EU"
    assert resolved.link == "?priority={{ x }}"
