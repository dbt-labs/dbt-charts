"""Chart-level `tooltip:` — authored shape, resolved defaults, loud failures.

Defaults (label, format) are decided here, in Python resolve; the engine only
paints what it is handed.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.execute.chart_columns import chart_column_refs

_ROWS: list[dict[str, Any]] = [
    {
        "month": "Jan",
        "cost": 10,
        "dept": "A",
        "approved": "yes",
        "pct_of_budget": 0.25,
        "requests": 4,
        "closed_on": datetime.date(2024, 1, 31),
        "closed_iso": "2024-01-31",
    },
    {
        "month": "Feb",
        "cost": 20,
        "dept": "B",
        "approved": None,
        "pct_of_budget": 0.5,
        "requests": 9,
        "closed_on": datetime.date(2024, 2, 29),
        "closed_iso": "2024-02-29",
    },
]
_FAMILY_CHARTS: dict[str, dict[str, Any]] = {
    "bar": {"type": "bar", "x": "month", "y": "cost"},
    "line": {"type": "line", "x": "month", "y": "cost"},
    "area": {"type": "area", "x": "month", "y": "cost"},
    "scatter": {"type": "scatter", "x": "requests", "y": "cost"},
    "pie": {"type": "pie", "theta": "cost", "color": "dept"},
}


def _resolved(
    chart: dict[str, Any], formats: dict[str, str] | None = None
) -> ResolvedChart:
    _, board_ctx = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    registry = {"q": ValuesQuery(rows=_ROWS, formats=formats or {})}
    flat = normalize_chart("c", {"query": "q", **chart}, registry, sources={})
    return resolve(flat, _ROWS, chart_style_context=board_ctx)


def _authored(chart: dict[str, Any]) -> AuthoredBoard:
    return AuthoredBoard.model_validate({"title": "t", "charts": {"c": chart}})


@pytest.mark.parametrize("family", sorted(_FAMILY_CHARTS))
def test_a_bare_column_resolves_with_the_derived_label(family: str) -> None:
    resolved = _resolved({**_FAMILY_CHARTS[family], "tooltip": ["pct_of_budget"]})
    [field] = resolved.tooltip
    assert field.column == "pct_of_budget"
    assert field.label == "Pct of budget"


def test_field_labels_name_the_row_before_the_derived_label() -> None:
    resolved = _resolved(
        {
            **_FAMILY_CHARTS["bar"],
            "field_labels": {"requests": "Requests filed"},
            "tooltip": ["requests"],
        }
    )
    assert resolved.tooltip[0].label == "Requests filed"


def test_an_entry_label_and_format_win_over_the_defaults() -> None:
    resolved = _resolved(
        {
            **_FAMILY_CHARTS["bar"],
            "field_labels": {"pct_of_budget": "Ignored"},
            "tooltip": [
                {"column": "pct_of_budget", "label": "% of budget", "format": ".1%"}
            ],
        },
        formats={"pct_of_budget": ".0f"},
    )
    [field] = resolved.tooltip
    assert field.label == "% of budget"
    assert field.format is not None and field.format.spec == ".1%"


def test_the_query_format_is_the_default_format() -> None:
    resolved = _resolved(
        {**_FAMILY_CHARTS["bar"], "tooltip": ["pct_of_budget"]},
        formats={"pct_of_budget": ".1%"},
    )
    fmt = resolved.tooltip[0].format
    assert fmt is not None and fmt.spec == ".1%"


def test_a_text_column_carries_no_format() -> None:
    resolved = _resolved({**_FAMILY_CHARTS["bar"], "tooltip": ["approved"]})
    assert resolved.tooltip[0].format is None


def test_a_date_column_takes_a_time_format() -> None:
    resolved = _resolved({**_FAMILY_CHARTS["bar"], "tooltip": ["closed_on"]})
    fmt = resolved.tooltip[0].format
    assert fmt is not None and "%" in fmt.spec


def test_entries_keep_their_authored_order() -> None:
    resolved = _resolved(
        {**_FAMILY_CHARTS["bar"], "tooltip": ["requests", "approved", "dept"]}
    )
    assert [f.column for f in resolved.tooltip] == ["requests", "approved", "dept"]


def test_no_tooltip_resolves_to_no_fields() -> None:
    assert _resolved(_FAMILY_CHARTS["bar"]).tooltip == ()


def test_an_unknown_column_is_a_column_ref_of_the_chart() -> None:
    flat = normalize_chart(
        "c",
        {
            "query": "q",
            **_FAMILY_CHARTS["bar"],
            "tooltip": ["approved", {"column": "nope"}],
        },
        {"q": ValuesQuery(rows=_ROWS)},
        sources={},
    )
    refs = chart_column_refs(flat)
    assert ("q", "tooltip[0]", "approved") in refs
    assert ("q", "tooltip[1]", "nope") in refs


@pytest.mark.parametrize("family", sorted(_FAMILY_CHARTS))
def test_the_five_families_author_tooltip(family: str) -> None:
    _authored({**_FAMILY_CHARTS[family], "query": "q", "tooltip": ["approved"]})


@pytest.mark.parametrize(
    "chart",
    [
        {"type": "table"},
        {"type": "kpi", "value": "cost"},
        {"type": "heatmap", "x": "month", "y": "dept"},
        {"type": "histogram", "x": "cost"},
        {"type": "map"},
        {"type": "point_map"},
        {"type": "spark_bar"},
    ],
)
def test_other_families_reject_tooltip(chart: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="tooltip"):
        _authored({**chart, "query": "q", "tooltip": ["approved"]})


def test_an_entry_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError, match="divider"):
        _authored(
            {
                **_FAMILY_CHARTS["bar"],
                "query": "q",
                "tooltip": [{"column": "a", "divider": True}],
            }
        )


_LAYER = {"type": "line", "y": "cost"}


@pytest.mark.parametrize("family", ["bar", "line", "area", "scatter"])
def test_tooltip_with_layers_is_rejected(family: str) -> None:
    with pytest.raises(ValidationError, match="layers"):
        _authored(
            {
                **_FAMILY_CHARTS[family],
                "query": "q",
                "tooltip": ["approved"],
                "layers": [_LAYER],
            }
        )


def test_tooltip_with_a_ranged_bar_is_rejected() -> None:
    with pytest.raises(ValidationError, match="y_start"):
        _authored(
            {
                **_FAMILY_CHARTS["bar"],
                "query": "q",
                "y_start": "requests",
                "tooltip": ["approved"],
            }
        )


def test_tooltip_on_a_multi_measure_scatter_is_rejected() -> None:
    with pytest.raises(ValidationError, match="multi-measure"):
        _authored(
            {
                "type": "scatter",
                "query": "q",
                "x": "requests",
                "y": ["cost", "dept"],
                "tooltip": ["approved"],
            }
        )


@pytest.mark.parametrize("label", ["Share: YTD", "a; b"])
def test_a_label_with_a_separator_is_rejected(label: str) -> None:
    from dbt_charts.core.compile.errors import CompilationError

    with pytest.raises(CompilationError, match="cannot contain"):
        _resolved(
            {
                **_FAMILY_CHARTS["bar"],
                "tooltip": [{"column": "approved", "label": label}],
            }
        )


def test_a_format_on_a_text_column_is_rejected() -> None:
    from dbt_charts.core.compile.errors import CompilationError

    with pytest.raises(CompilationError, match="holds text"):
        _resolved(
            {
                **_FAMILY_CHARTS["bar"],
                "tooltip": [{"column": "approved", "format": ".1%"}],
            }
        )


def test_an_iso_date_string_column_takes_a_time_format() -> None:
    resolved = _resolved(
        {
            **_FAMILY_CHARTS["bar"],
            "tooltip": [{"column": "closed_iso", "format": "%b %Y"}],
        }
    )
    fmt = resolved.tooltip[0].format
    assert fmt is not None and fmt.spec == "%b %Y"


def test_a_query_format_on_a_column_that_prints_as_text_is_rejected() -> None:
    from dbt_charts.core.compile.errors import CompilationError

    with pytest.raises(CompilationError, match="approved"):
        _resolved(
            {**_FAMILY_CHARTS["bar"], "tooltip": ["approved"]},
            formats={"approved": ".1%"},
        )


def test_a_scatter_tooltip_beside_a_date_valued_y_is_rejected() -> None:
    from dbt_charts.core.compile.errors import CompilationError

    for y in ("closed_on", "closed_iso"):
        with pytest.raises(CompilationError, match="date"):
            _resolved(
                {"type": "scatter", "x": "requests", "y": y, "tooltip": ["approved"]}
            )
