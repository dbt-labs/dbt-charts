"""Tests for the chart.support_table primitive (authoring + validation).

These tests describe the v1 authoring surface behavior — entry shape,
grammar rules G1..G5, validation rules from the chart.support_table spec §3.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.chart.authored import (
    AuthoredChart,
    ChartSupportTable,
    ChartSupportTableAggregate,
    ChartSupportTableEntry,
    ChartSupportTableOrList,
    ChartSupportTableSource,
)

_chart_patch_adapter = TypeAdapter(AuthoredChart)
# ChartSupportTable/ChartSupportTableSource accept only object shapes on their
# own -- the bare-list/bare-string authoring shorthand is declared on
# ChartSupportTableOrList/ChartSupportTableEntry (chart/authored/_support_table.py),
# not on the classes themselves, so shorthand input goes through these adapters.
_support_table_adapter = TypeAdapter(ChartSupportTableOrList)
_support_table_entry_adapter = TypeAdapter(ChartSupportTableEntry)

# =============================================================================
# ENTRY SHAPE — source-only form
# =============================================================================


def test_support_table_source_entry_minimum():
    entry = ChartSupportTableSource.model_validate({"source": "revenue"})
    assert entry.source == "revenue"
    assert entry.format is None
    assert entry.label is None


def test_support_table_source_entry_with_format_and_label():
    entry = ChartSupportTableSource.model_validate(
        {"source": "mrr", "format": "$.2s", "label": "MRR"}
    )
    assert entry.source == "mrr"
    assert entry.format == "$.2s"
    assert entry.label == "MRR"


def test_support_table_source_entry_requires_source():
    with pytest.raises(ValidationError):
        ChartSupportTableSource.model_validate({"format": ",d"})


def test_support_table_source_entry_rejects_unknown_keys():
    # `extra=forbid` — no `column:`, `field:`, no inline styling per spec §2.3
    with pytest.raises(ValidationError):
        ChartSupportTableSource.model_validate({"source": "revenue", "column": "x"})
    with pytest.raises(ValidationError):
        ChartSupportTableSource.model_validate({"source": "revenue", "style": {}})


# =============================================================================
# ENTRY SHAPE — aggregate form
# =============================================================================


def test_support_table_aggregate_entry_minimum():
    entry = ChartSupportTableAggregate.model_validate(
        {"aggregate": "sum", "source": "revenue"}
    )
    assert entry.aggregate == "sum"
    assert entry.source == "revenue"


def test_support_table_aggregate_entry_requires_source():
    # Grammar rule G2 — aggregate always requires source.
    with pytest.raises(ValidationError) as excinfo:
        ChartSupportTableAggregate.model_validate({"aggregate": "sum"})
    msg = str(excinfo.value).lower()
    assert "source" in msg


@pytest.mark.parametrize(
    "op", ["sum", "avg", "min", "max", "median", "count", "count_distinct"]
)
def test_support_table_aggregate_accepts_supported_ops(op):
    entry = ChartSupportTableAggregate.model_validate(
        {"aggregate": op, "source": "revenue"}
    )
    assert entry.aggregate == op


@pytest.mark.parametrize("op", ["mean", "total", "distinct", "stdev", "first"])
def test_support_table_aggregate_rejects_unknown_ops(op):
    # Grammar rule G4 — exact names, no aliases.
    with pytest.raises(ValidationError):
        ChartSupportTableAggregate.model_validate(
            {"aggregate": op, "source": "revenue"}
        )


# =============================================================================
# LIST-LEVEL — mixing source and aggregate entries
# =============================================================================


def test_chart_patch_support_table_mixed_entries():
    patch = _chart_patch_adapter.validate_python(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "support_table": [
                {"source": "sample_size", "format": ",d"},
                {"aggregate": "sum", "source": "revenue", "label": "Total"},
            ],
        }
    )
    assert patch.support_table is not None
    assert len(patch.support_table.entries) == 2
    assert isinstance(patch.support_table.entries[0], ChartSupportTableSource)
    assert isinstance(patch.support_table.entries[1], ChartSupportTableAggregate)


def test_chart_patch_support_table_preserves_list_order():
    # Grammar rule G5 — entries render top-to-bottom in list order.
    patch = _chart_patch_adapter.validate_python(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "support_table": [
                {"source": "a"},
                {"source": "b"},
                {"source": "c"},
            ],
        }
    )
    assert [e.source for e in patch.support_table.entries] == ["a", "b", "c"]


def test_chart_patch_support_table_disambiguates_source_vs_aggregate():
    # source-only shape is NOT parsed as aggregate even if "source" is shared.
    patch = _chart_patch_adapter.validate_python(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "support_table": [{"source": "revenue"}],
        }
    )
    assert isinstance(patch.support_table.entries[0], ChartSupportTableSource)


def test_chart_patch_empty_support_table_list_rejects():
    # A block with zero rows is pointless — reject rather than silently drop.
    with pytest.raises(ValidationError):
        _chart_patch_adapter.validate_python(
            {
                "type": "bar",
                "x": "month",
                "y": "revenue",
                "support_table": [],
            }
        )


def test_chart_patch_support_table_optional():
    # Default state: no support_table block present.
    patch = _chart_patch_adapter.validate_python(
        {"type": "bar", "x": "month", "y": "revenue"}
    )
    assert patch.support_table is None


# =============================================================================
# CHART-TYPE VALIDATION (spec §1, §3.1 — unsupported chart types)
# =============================================================================


@pytest.mark.parametrize("chart_type", ["bar", "line", "area"])
def test_chart_patch_support_table_accepts_supported_chart_types(chart_type):
    patch = _chart_patch_adapter.validate_python(
        {
            "type": chart_type,
            "x": "month",
            "y": "revenue",
            "support_table": [{"source": "revenue"}],
        }
    )
    assert patch.support_table is not None
    assert patch.type == chart_type


@pytest.mark.parametrize(
    ("chart_type", "payload"),
    [
        ("pie", {"theta": "revenue"}),
        ("donut", {"theta": "revenue"}),
        ("scatter", {"x": "x", "y": "y"}),
        ("kpi", {"value": "revenue"}),
        ("table", {}),
        ("geoshape", {}),
    ],
)
def test_chart_patch_support_table_rejects_unsupported_chart_types(chart_type, payload):
    # support_table is only declared on cartesian family patches (bar/line/area) and
    # layered. All other chart types forbid it via extra="forbid" on their patch
    # class, or via the _validate_support_table validator on _CartesianChartFields.
    full = {
        "type": chart_type,
        "support_table": [{"source": "y"}],
        **payload,
    }
    with pytest.raises(ValidationError) as excinfo:
        _chart_patch_adapter.validate_python(full)
    msg = str(excinfo.value).lower()
    assert "support_table" in msg


# =============================================================================
# ROW COUNT — NOT capped at 40 (CRITICAL 1 regression)
# =============================================================================
# Spec §3.1 caps x-axis TICK count (dataset cardinality) at 40, not entry
# count. The authoring surface places no ceiling on entries. 40 rows of
# support_table on a 5-tick chart is valid.


def test_chart_patch_support_table_allows_many_rows_at_authoring_time():
    entries = [{"source": f"col_{i}"} for i in range(60)]
    patch = _chart_patch_adapter.validate_python(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "support_table": entries,
        }
    )
    assert len(patch.support_table.entries) == 60


# =============================================================================
# DUPLICATE ENTRIES — CRITICAL 4 (spec §3.2)
# =============================================================================


def test_chart_patch_support_table_rejects_duplicate_source_only_entries():
    with pytest.raises(ValidationError) as excinfo:
        _chart_patch_adapter.validate_python(
            {
                "type": "bar",
                "x": "month",
                "y": "revenue",
                "support_table": [
                    {"source": "revenue"},
                    {"source": "revenue"},  # same source, no aggregate
                ],
            }
        )
    msg = str(excinfo.value).lower()
    assert "duplicate" in msg


def test_chart_patch_support_table_rejects_duplicate_aggregate_entries():
    with pytest.raises(ValidationError) as excinfo:
        _chart_patch_adapter.validate_python(
            {
                "type": "bar",
                "x": "month",
                "y": "revenue",
                "support_table": [
                    {"aggregate": "sum", "source": "revenue"},
                    {"aggregate": "sum", "source": "revenue"},
                ],
            }
        )
    msg = str(excinfo.value).lower()
    assert "duplicate" in msg


def test_chart_patch_support_table_allows_same_source_with_different_aggregate():
    # Different aggregate → different keyed row, not a duplicate.
    patch = _chart_patch_adapter.validate_python(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "support_table": [
                {"aggregate": "sum", "source": "revenue"},
                {"aggregate": "avg", "source": "revenue"},
            ],
        }
    )
    assert len(patch.support_table.entries) == 2


def test_chart_patch_support_table_allows_raw_and_aggregate_of_same_column():
    # Raw row and an aggregate row on the same column are different keyed rows.
    patch = _chart_patch_adapter.validate_python(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "support_table": [
                {"source": "revenue"},
                {"aggregate": "sum", "source": "revenue"},
            ],
        }
    )
    assert len(patch.support_table.entries) == 2


# =============================================================================
# MULTI-Y / LAYERED — CRITICAL 5 (fail fast at authoring level)
# =============================================================================


def test_chart_patch_rejects_support_table_on_multi_y_list():
    # A chart with y: [a, b] cannot carry a support_table in v1 — the x→multiple-rows
    # collision the spec warns about is guaranteed. Fail fast rather than
    # silently drop in the renderer.
    with pytest.raises(ValidationError) as excinfo:
        _chart_patch_adapter.validate_python(
            {
                "type": "line",
                "x": "month",
                "y": ["a", "b"],
                "support_table": [{"source": "a"}],
            }
        )
    msg = str(excinfo.value).lower()
    assert "support_table" in msg
    assert "multi" in msg or "list" in msg or "layered" in msg


# =============================================================================
# PHANTOM "column" — CRITICAL 3 (supported set must be valid ChartType values)
# =============================================================================


def test_chart_support_table_supported_types_are_all_real_chart_types():
    # Guard against the phantom-shape regression: every string in the supported
    # frozenset must be a real ChartType value.
    from dbt_charts.core.compile.models.chart.authored import (
        CHART_SUPPORT_TABLE_SUPPORTED_TYPES,
        ChartType,
    )

    real_types = {ct.value for ct in ChartType}
    for t in CHART_SUPPORT_TABLE_SUPPORTED_TYPES:
        assert t in real_types, f"{t!r} is not a valid ChartType value"


# =============================================================================
# CONTAINER — ChartSupportTable wrapper structure
# =============================================================================


def test_chart_support_table_container_accepts_plain_list():
    # Authoring shape is a bare list; the model may wrap it in a container, but
    # parsing a bare list must be accepted so author YAML stays clean.
    table = _support_table_adapter.validate_python(
        [{"source": "revenue"}, {"aggregate": "sum", "source": "revenue"}]
    )
    assert len(table.entries) == 2


# =============================================================================
# PER_SERIES ENTRY SHAPE — new §2.x (third shape)
# =============================================================================


def test_per_series_entry_minimum():
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTablePerSeries,
    )

    entry = ChartSupportTablePerSeries.model_validate({"per_series": "revenue"})
    assert entry.per_series == "revenue"
    assert entry.format is None


def test_per_series_entry_with_format():
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTablePerSeries,
    )

    entry = ChartSupportTablePerSeries.model_validate(
        {"per_series": "revenue", "format": "$,.0f"}
    )
    assert entry.per_series == "revenue"
    assert entry.format == "$,.0f"


def test_per_series_entry_rejects_unknown_keys():
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTablePerSeries,
    )

    with pytest.raises(ValidationError):
        ChartSupportTablePerSeries.model_validate(
            {"per_series": "revenue", "unknown_field": "x"}
        )


def test_per_series_entry_accepts_label():
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTablePerSeries,
    )

    entry = ChartSupportTablePerSeries.model_validate(
        {"per_series": "revenue", "label": "Revenue", "by_measure": True}
    )
    assert entry.label == "Revenue"


def test_per_series_entry_is_parsed_from_chart_support_table():
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTablePerSeries,
    )

    table = _support_table_adapter.validate_python(
        [{"per_series": "revenue", "format": "$,.0f"}]
    )
    assert len(table.entries) == 1
    assert isinstance(table.entries[0], ChartSupportTablePerSeries)


def test_chart_patch_accepts_per_series_entry_with_color():
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTablePerSeries,
    )

    patch = _chart_patch_adapter.validate_python(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "color": "category",
            "support_table": [{"per_series": "revenue", "format": "$,.0f"}],
        }
    )
    assert patch.support_table is not None
    assert isinstance(patch.support_table.entries[0], ChartSupportTablePerSeries)


def test_chart_patch_rejects_per_series_without_color():
    # Compile error: per_series: on a chart with no color: channel is meaningless.
    with pytest.raises(ValidationError) as excinfo:
        _chart_patch_adapter.validate_python(
            {
                "type": "bar",
                "x": "month",
                "y": "revenue",
                # no color:
                "support_table": [{"per_series": "revenue"}],
            }
        )
    msg = str(excinfo.value).lower()
    assert "per_series" in msg or "color" in msg


def test_chart_patch_rejects_mixed_per_series_and_aggregate_in_same_list():
    # per_series and aggregate are valid separately but not combined in the same
    # list entry — the discriminator routes each dict to its shape independently,
    # but mixing them in the SAME dict is an error.
    # This tests that per_series: + aggregate: in one dict is rejected.
    with pytest.raises(ValidationError):
        _support_table_adapter.validate_python(
            [{"per_series": "revenue", "aggregate": "sum"}]
        )


# =============================================================================
# by_measure field on ChartSupportTablePerSeries
# =============================================================================


def test_per_series_by_measure_field_default_false():
    """by_measure defaults to False (existing per_series behavior unchanged)."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    entry = ChartSupportTablePerSeries.model_validate({"per_series": "revenue"})
    assert entry.by_measure is False


def test_per_series_by_measure_field_true():
    """by_measure can be set to True."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    entry = ChartSupportTablePerSeries.model_validate(
        {"per_series": "revenue", "by_measure": True}
    )
    assert entry.by_measure is True
    assert entry.per_series == "revenue"


def test_chart_patch_accepts_by_measure_on_multi_y_chart():
    """by_measure=True entries are accepted on a chart with y: list."""
    patch = _chart_patch_adapter.validate_python(
        {
            "type": "bar",
            "x": "month",
            "y": ["revenue", "cost"],
            "support_table": [
                {"per_series": "revenue", "by_measure": True},
                {"per_series": "cost", "by_measure": True},
            ],
        }
    )
    assert patch.support_table is not None
    assert len(patch.support_table.entries) == 2


def test_chart_patch_rejects_non_by_measure_per_series_on_multi_y():
    """Normal per_series (by_measure=False) is rejected on multi-y chart."""
    with pytest.raises(ValidationError) as excinfo:
        _chart_patch_adapter.validate_python(
            {
                "type": "bar",
                "x": "month",
                "y": ["revenue", "cost"],
                "color": "segment",
                "support_table": [{"per_series": "revenue"}],
            }
        )
    msg = str(excinfo.value)
    assert "by_measure" in msg or "multi-field" in msg


def test_chart_patch_rejects_source_entry_on_multi_y():
    """Plain source entries are rejected on multi-y charts."""
    with pytest.raises(ValidationError) as excinfo:
        _chart_patch_adapter.validate_python(
            {
                "type": "bar",
                "x": "month",
                "y": ["revenue", "cost"],
                "support_table": [{"source": "revenue"}],
            }
        )
    msg = str(excinfo.value)
    assert "by_measure" in msg or "multi-field" in msg


def test_chart_patch_by_measure_no_color_required():
    """by_measure entries do not require a color: channel on the chart."""
    # No ValidationError — no color on a single-y or multi-y chart with by_measure
    _chart_patch_adapter.validate_python(
        {
            "type": "bar",
            "x": "month",
            "y": ["revenue", "cost"],
            # no color: field
            "support_table": [
                {"per_series": "revenue", "by_measure": True},
                {"per_series": "cost", "by_measure": True},
            ],
        }
    )


def test_support_table_entry_format_inherits_chart_format_at_normalize_time():
    """After normalize_chart(), a source entry reading chart.y inherits style.number_format."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTableSource
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    chart = normalize_chart(
        "t",
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "style": {"number_format": "~s"},
            "query": {"sql": "SELECT 1", "source": "test"},
            "support_table": [{"source": "revenue"}, {"source": "count"}],
        },
        {},
        None,
        None,
        "charts.t",
        sources={},
    )
    assert chart.support_table is not None
    entries = chart.support_table.entries
    assert isinstance(entries[0], ChartSupportTableSource)
    assert entries[0].format == "~s", "measure entry must inherit style.number_format"
    assert isinstance(entries[1], ChartSupportTableSource)
    assert entries[1].format is None, "non-measure entry must not inherit"


def test_resolved_chart_bakes_number_format_for_numeric_y():
    """ResolvedChart.support_table stamps the theme default number format onto
    entries reading a single *numeric* string y column with no authored
    format — projected once at resolve time (gated on the actual query rows
    every cartesian resolver already receives) so render never calls
    apply_measure_format_to_support_table or re-derives the numeric gate
    itself."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTableSource
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_style = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        support_table=ChartSupportTable.model_validate(
            {"entries": [{"source": "revenue"}, {"source": "count", "format": "~s"}]}
        ),
    )
    data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
    resolved = resolve(chart, data, board_style)

    assert resolved.support_table is not None
    entries = resolved.support_table.entries
    assert isinstance(entries[0], ChartSupportTableSource)
    # "number" is predefined — the format name is stamped directly
    # (no theme formats dict lookup needed).
    assert entries[0].format.raw == "number"
    assert isinstance(entries[1], ChartSupportTableSource)
    assert entries[1].format.spec == "~s", (
        "already-authored format must not be overwritten"
    )


def test_resolved_chart_does_not_stamp_number_format_for_numeric_string_y():
    """A y column of numeric-looking *strings* must NOT get the numeric
    format stamped — compile's own classify_column_type accepts it as a bar
    measure, but a d3 format() call on a Python str yields NaN, so this gate
    is deliberately stricter than compile's own numeric check."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTableSource
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_style = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        support_table=ChartSupportTable.model_validate(
            {"entries": [{"source": "revenue"}]}
        ),
    )
    data = [{"month": "Jan", "revenue": "100"}, {"month": "Feb", "revenue": "200"}]
    resolved = resolve(chart, data, board_style)

    assert resolved.support_table is not None
    assert isinstance(resolved.support_table.entries[0], ChartSupportTableSource)
    assert resolved.support_table.entries[0].format is None


def test_resolved_chart_does_not_stamp_number_format_without_rows():
    """No query rows means the numeric gate can't be proven — format stays
    unstamped rather than assuming numeric."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTableSource
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_style = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        support_table=ChartSupportTable.model_validate(
            {"entries": [{"source": "revenue"}]}
        ),
    )
    resolved = resolve(chart, [], board_style)

    assert resolved.support_table is not None
    assert isinstance(resolved.support_table.entries[0], ChartSupportTableSource)
    assert resolved.support_table.entries[0].format is None


def test_resolved_chart_support_table_none_without_authored_support_table():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_style = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    resolved = resolve(chart, [], board_style)
    assert resolved.support_table is None


def test_column_is_numeric_gates_number_inheritance():
    # The gate that guards number inheritance: only a column whose
    # non-null values are all real numbers is numeric. A string column
    # (category labels), empty column, bool column, or mixed column is NOT —
    # grafting a d3 numeric format onto it would emit format(<string>, '.3~s')
    # → NaN → the cell renders "-" instead of the label.
    from dbt_charts.core.compile.resolve.chart._kwargs import _column_is_numeric

    assert _column_is_numeric([{"v": 1.0}, {"v": 2}, {"v": None}], "v") is True
    assert _column_is_numeric([{"v": "Alpha"}, {"v": "Beta"}], "v") is False
    assert _column_is_numeric([{"v": None}, {"v": None}], "v") is False  # no values
    assert _column_is_numeric([], "v") is False
    assert _column_is_numeric([{"v": True}, {"v": 2}], "v") is False  # bool excluded
    assert _column_is_numeric([{"v": 1}, {"v": "x"}], "v") is False  # mixed


def test_support_table_entry_format_inherits_style_axis_y_format_at_normalize_time():
    """style.axis_y.format is the primary authoring path for cartesian charts.

    chart.format is a test-only fallback; production dashboards author format
    through style.bar.axis_y.format (or style.line/area equivalents). The
    normalize_chart() step must resolve the authored measure format, not bare
    chart.format, so strip cells inherit the same format as the chart axis.
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTableSource
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    chart = normalize_chart(
        "t",
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "style": {"axis_y": {"labels": {"format": "$,.0f"}}},
            "query": {"sql": "SELECT 1", "source": "test"},
            "support_table": [{"source": "revenue"}, {"source": "count"}],
        },
        {},
        None,
        None,
        "charts.t",
        sources={},
    )
    assert chart.support_table is not None
    entries = chart.support_table.entries
    assert isinstance(entries[0], ChartSupportTableSource)
    assert entries[0].format == "$,.0f", (
        "measure entry must inherit style.bar.axis_y.format at normalize time"
    )
    assert isinstance(entries[1], ChartSupportTableSource)
    assert entries[1].format is None, "non-measure entry must not inherit"


# =============================================================================
# BARE STRING SUGAR — `support_table: [revenue]` === `support_table: [{source: revenue}]`
# =============================================================================


def test_bare_string_entry_parses_as_source():
    entry = _support_table_entry_adapter.validate_python("revenue")
    assert isinstance(entry, ChartSupportTableSource)
    assert entry.source == "revenue"
    assert entry.format is None
    assert entry.label is None


def test_chart_support_table_container_accepts_bare_string_entries():
    table = _support_table_adapter.validate_python(["revenue", "cost"])
    assert len(table.entries) == 2
    assert all(isinstance(e, ChartSupportTableSource) for e in table.entries)
    assert [e.source for e in table.entries] == ["revenue", "cost"]


def test_bare_string_and_mapping_entries_parse_identically():
    bare = _support_table_adapter.validate_python(["revenue"])
    mapped = _support_table_adapter.validate_python([{"source": "revenue"}])
    assert bare.entries[0] == mapped.entries[0]


def test_chart_patch_support_table_accepts_bare_string_list():
    patch = _chart_patch_adapter.validate_python(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "support_table": ["revenue"],
        }
    )
    assert patch.support_table is not None
    assert len(patch.support_table.entries) == 1
    entry = patch.support_table.entries[0]
    assert isinstance(entry, ChartSupportTableSource)
    assert entry.source == "revenue"


def test_chart_patch_support_table_mixes_bare_string_and_object_entries():
    patch = _chart_patch_adapter.validate_python(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "support_table": [
                "revenue",
                {"aggregate": "sum", "source": "cost"},
            ],
        }
    )
    assert patch.support_table is not None
    entries = patch.support_table.entries
    assert len(entries) == 2
    assert isinstance(entries[0], ChartSupportTableSource)
    assert entries[0].source == "revenue"
    assert isinstance(entries[1], ChartSupportTableAggregate)
    assert entries[1].aggregate == "sum"
    assert entries[1].source == "cost"


def test_chart_patch_support_table_rejects_duplicate_across_bare_and_mapping_spellings():
    # A bare string and its equivalent {source: ...} mapping are the same
    # entry — duplicate validation must fire regardless of which spelling
    # authored it.
    with pytest.raises(ValidationError) as excinfo:
        _chart_patch_adapter.validate_python(
            {
                "type": "bar",
                "x": "month",
                "y": "revenue",
                "support_table": ["revenue", {"source": "revenue"}],
            }
        )
    msg = str(excinfo.value).lower()
    assert "duplicate" in msg


def test_chart_support_table_rejects_non_string_non_mapping_entry():
    # No silent fallback — an int (or any other non-string, non-mapping
    # value) still fails validation with the existing discriminator error,
    # not a new one carved out for the bare-string arm.
    with pytest.raises(ValidationError):
        _support_table_adapter.validate_python([123])
