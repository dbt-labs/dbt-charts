"""TDD tests: KPI channels are always column references.

Verifies that numeric literals at KPI `value:` and `support.value:` are
rejected at parse time (Pydantic validation), and that string values not
present in the query result raise ChartDataError with a migration hint.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.authored import (
    KpiChart as AuthoredKpiChart,
    KpiSupportConfig,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

from .._kpi_board import kpi_svg

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

_DUMMY_QUERY = SqlQuery(sql="SELECT 1", source="t")


def _board_style():
    return resolve_style(get_theme_style())


# ---------------------------------------------------------------------------
# 1. Integer literal at chart-root value: rejected at parse time
# ---------------------------------------------------------------------------


def test_kpi_value_rejects_integer_literal():
    with pytest.raises(ValidationError, match="numeric literal"):
        AuthoredKpiChart(type="kpi", value=1)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. Float literal at chart-root value: rejected at parse time
# ---------------------------------------------------------------------------


def test_kpi_value_rejects_float_literal():
    with pytest.raises(ValidationError, match="numeric literal"):
        AuthoredKpiChart(type="kpi", value=0.5)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. Numeric literal at support.value: rejected at parse time
# ---------------------------------------------------------------------------


def test_kpi_support_value_rejects_numeric_literal():
    with pytest.raises(ValidationError) as exc_info:
        KpiSupportConfig(value=0.124)
    msg = str(exc_info.value)
    assert "support.value" in msg
    assert "numeric literal" in msg


# ---------------------------------------------------------------------------
# 4. String column reference still accepted
# ---------------------------------------------------------------------------


def test_kpi_value_string_col_ref_still_accepted():
    p = AuthoredKpiChart(type="kpi", value="revenue")
    assert p.value == "revenue"


# ---------------------------------------------------------------------------
# 5. Inline values query renders status-style KPI (replacement for string literal)
# ---------------------------------------------------------------------------


def test_kpi_with_inline_values_query_renders_status_kpi():
    svg = kpi_svg({"value": "status", "label": "Pipeline"}, [{"status": "On track"}])
    assert "On track" in svg
