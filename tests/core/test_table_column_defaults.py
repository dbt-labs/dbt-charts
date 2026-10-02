"""Tests for table-level column defaults (style.column_defaults).

A table author can set label/width/align/format/background/font once at the
chart style level; every column inherits those values unless the column
config explicitly overrides them. Materialization happens once, during chart
resolution — ``ResolvedTableChart.columns`` always carries the complete,
final per-column config.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def _resolve_table(style: dict, data: list[dict]):
    chart = TableChart(id="test", type="table", style=style)
    return resolve(chart, data, chart_style_context=_BOARD_STYLE)


class TestColumnDefaultsMaterializedAtResolve:
    """column_defaults, explicit columns, and runtime facts merge once at resolve."""

    def test_defaults_applied_when_no_per_column_override(self) -> None:
        resolved = _resolve_table(
            {
                "column_defaults": {"label": " ", "width": 36, "align": "center"},
                "columns": {"c1": {}, "c2": {}, "c3": {}},
            },
            data=[{"c1": 1, "c2": 2, "c3": 3}],
        )
        assert resolved.columns is not None
        assert set(resolved.columns) == {"c1", "c2", "c3"}
        for col in ("c1", "c2", "c3"):
            cfg = resolved.columns[col]
            assert cfg.label == " "
            assert cfg.width == 36
            assert cfg.align == "center"

    def test_per_column_value_wins_over_default(self) -> None:
        resolved = _resolve_table(
            {
                "column_defaults": {"label": " ", "width": 36, "align": "center"},
                "columns": {
                    "c1": {},
                    "c2": {"align": "right", "background": "#fee2e2"},
                },
            },
            data=[{"c1": 1, "c2": 2}],
        )
        assert resolved.columns is not None
        assert resolved.columns["c1"].label == " "
        assert resolved.columns["c1"].width == 36
        assert resolved.columns["c1"].align == "center"
        assert resolved.columns["c1"].background is None
        assert resolved.columns["c2"].label == " "
        assert resolved.columns["c2"].width == 36
        assert resolved.columns["c2"].align == "right"
        assert resolved.columns["c2"].background == "#fee2e2"

    def test_no_defaults_no_change(self) -> None:
        """Without column_defaults the explicit per-column config passes through."""
        resolved = _resolve_table(
            {
                "columns": {
                    "c1": {"width": 50},
                    "c2": {"align": "right"},
                }
            },
            data=[{"c1": 1, "c2": 2}],
        )
        assert resolved.columns is not None
        assert resolved.columns["c1"].width == 50
        assert resolved.columns["c1"].align is None
        assert resolved.columns["c2"].width is None
        assert resolved.columns["c2"].align == "right"

    def test_defaults_with_no_explicit_columns_materializes_all_query_columns(
        self,
    ) -> None:
        """No columns: authored — column_defaults materializes over every
        query-inferred column, the "no columns list" authoring shape."""
        resolved = _resolve_table(
            {"column_defaults": {"label": " ", "width": 36, "align": "center"}},
            data=[{"a": 1, "b": 2, "c": 3}],
        )
        assert resolved.columns is not None
        assert set(resolved.columns) == {"a", "b", "c"}
        assert resolved.columns["a"].label == " "
        assert resolved.columns["b"].width == 36
        assert resolved.columns["c"].align == "center"

    def test_defaults_without_columns_or_data_yields_empty_mapping(self) -> None:
        resolved = _resolve_table(
            {"column_defaults": {"label": " ", "width": 36}}, data=[]
        )
        assert resolved.columns == {}

    def test_no_defaults_no_explicit_columns_no_links_still_classifies_align(
        self,
    ) -> None:
        """No explicit columns/defaults/links, but real data: columns is no
        longer None — every inferred column still gets materialized so its
        alignment can be classified from its values (here, plain numbers,
        so the verdict is "no verdict" and every other field stays unset,
        matching the pre-materialization render fallback)."""
        resolved = _resolve_table({}, data=[{"a": 1, "b": 2}])
        assert resolved.columns is not None
        assert set(resolved.columns) == {"a", "b"}
        for col in ("a", "b"):
            cfg = resolved.columns[col]
            assert cfg.align is None
            assert cfg.label is None
            assert cfg.width is None

    def test_no_defaults_no_explicit_columns_no_data_leaves_columns_none(
        self,
    ) -> None:
        """Nothing to materialize AND no data to classify from: columns
        stays None; render falls back to plain query columns."""
        resolved = _resolve_table({}, data=[])
        assert resolved.columns is None

    def test_partial_defaults_only_fill_none_fields(self) -> None:
        """Only None fields in the explicit column config are filled from defaults."""
        resolved = _resolve_table(
            {
                "column_defaults": {
                    "label": "default-label",
                    "width": 100,
                    "background": "#eeeeee",
                },
                "columns": {"c1": {"width": 50}},  # explicit — must not be overridden
            },
            data=[{"c1": 1}],
        )
        assert resolved.columns is not None
        assert resolved.columns["c1"].width == 50  # per-column wins
        assert resolved.columns["c1"].label == "default-label"  # default fills
        assert resolved.columns["c1"].background == "#eeeeee"  # default fills

    def test_font_default_propagates(self) -> None:
        resolved = _resolve_table(
            {
                "column_defaults": {"font": {"weight": 700}},
                "columns": {"c1": {}, "c2": {"font": {"size": 10}}},
            },
            data=[{"c1": 1, "c2": 2}],
        )
        assert resolved.columns is not None
        assert resolved.columns["c1"].font is not None
        assert resolved.columns["c1"].font.weight == 700
        # c2 has its own font — defaults.font does not override it
        assert resolved.columns["c2"].font is not None
        assert resolved.columns["c2"].font.size == 10
        assert resolved.columns["c2"].font.weight is None

    def test_format_default_propagates(self) -> None:
        resolved = _resolve_table(
            {
                "column_defaults": {"format": ",.0f"},
                "columns": {"c1": {}, "c2": {"format": "$,.2f"}},
            },
            data=[{"c1": 1, "c2": 2}],
        )
        assert resolved.columns is not None
        assert resolved.columns["c1"].format.spec == ",.0f"
        assert resolved.columns["c2"].format.spec == "$,.2f"

    def test_explicit_column_order_preserved(self) -> None:
        """Authored columns order is honored in the resolved mapping."""
        resolved = _resolve_table(
            {"columns": {"c": {}, "a": {}, "b": {}}},
            data=[{"c": 1, "a": 2, "b": 3}],
        )
        assert resolved.columns is not None
        assert list(resolved.columns) == ["c", "a", "b"]

    def test_width_default_does_not_collide_with_explicit_max_width(self) -> None:
        """width/max_width are one sizing slot — a column that claims max_width
        keeps it; a table-level width default must not also fill it in and
        trip TableColumnConfig's mutual-exclusivity validator."""
        resolved = _resolve_table(
            {
                "column_defaults": {"width": 120},
                "columns": {"c1": {"max_width": 200}, "c2": {}},
            },
            data=[{"c1": "x", "c2": 1}],
        )
        assert resolved.columns is not None
        assert resolved.columns["c1"].max_width == 200
        assert resolved.columns["c1"].width is None
        assert resolved.columns["c2"].width == 120
        assert resolved.columns["c2"].max_width is None

    def test_pivot_with_no_explicit_columns_excludes_measure_but_keeps_row_dims(
        self,
    ) -> None:
        """A pivoting chart's measure column ("amount") has no key space at
        resolve — its real leaf/value columns only exist after render's pivot
        transform, so column_defaults is applied to it there instead (see
        render-side coverage below). Row/pivot-dimension columns that survive
        pivoting unchanged ("region", "month") are ordinary inferred columns
        and get column_defaults here like any other."""
        chart = TableChart(
            id="pivoted",
            type="table",
            rows=["region"],
            columns=["month"],
            values=["amount"],
            style={"column_defaults": {"align": "center"}},
        )
        resolved = resolve(
            chart,
            [
                {"region": "US", "month": "Jan", "amount": 100},
                {"region": "EU", "month": "Jan", "amount": 150},
            ],
            chart_style_context=_BOARD_STYLE,
        )
        assert resolved.columns is not None
        assert set(resolved.columns) == {"region", "month"}
        assert resolved.columns["region"].align == "center"
        assert resolved.columns["month"].align == "center"

    def test_pivot_render_applies_column_defaults_to_leaf_columns(self) -> None:
        """render_table_svg on a pivoted chart with column_defaults and no
        explicit columns renders the real pivoted headers (not corrupted or
        garbled), with column_defaults' background actually landing on the
        pivot-leaf cells — applied by render for its own render-native key
        space, not just present without effect."""
        data = [
            {"region": "US", "month": "Jan", "amount": 100},
            {"region": "US", "month": "Feb", "amount": 200},
            {"region": "EU", "month": "Jan", "amount": 150},
            {"region": "EU", "month": "Feb", "amount": 250},
        ]
        chart = TableChart(
            id="pivoted",
            type="table",
            rows=["region"],
            columns=["month"],
            values=["amount"],
            style={"column_defaults": {"background": "#123456"}},
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            resolved, data, board_style=resolve_style(get_theme_style())
        )
        assert "<svg" in svg
        assert "\x1e" not in svg
        assert "Jan" in svg and "Feb" in svg
        assert "#123456" in svg
        # Header for the row dimension ("Region") must not be corrupted into
        # a bogus visible-column list — real leaf columns (Jan/Feb) render.
        assert "Region" in svg

    def test_pivot_multi_measure_render_applies_column_defaults_to_leaves(
        self,
    ) -> None:
        """Multi-measure pivot: column_defaults' background actually reaches
        each measure's leaf cells via render's leaf-key expansion, without
        corrupting the group-header structure or leaking the leaf separator
        into the SVG."""
        data = [
            {"region": "US", "month": "Jan", "amount": 100, "orders": 5},
            {"region": "EU", "month": "Jan", "amount": 150, "orders": 7},
        ]
        chart = TableChart(
            id="pivoted",
            type="table",
            rows=["region"],
            columns=["month"],
            values=["amount", "orders"],
            style={"column_defaults": {"background": "#123456"}},
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            resolved, data, board_style=resolve_style(get_theme_style())
        )
        assert "<svg" in svg
        assert "\x1e" not in svg
        assert "Amount" in svg and "Orders" in svg
        assert "#123456" in svg

    def test_pivot_values_omitted_still_excludes_inferred_measures(self) -> None:
        """Omitting `values:` (inferred from data shape) must not change
        which columns resolve treats as measures — an inferred measure gets
        no key space at resolve either, same as an explicit one, so render's
        leaf-key expansion still finds no pre-existing label-less entry to
        wrongly reuse (which would leak the raw leaf key, including the
        `\\x1e` separator, into the rendered header)."""
        data = [
            {"region": "US", "month": "Jan", "amount": 100, "orders": 5},
            {"region": "EU", "month": "Jan", "amount": 150, "orders": 7},
        ]
        chart = TableChart(
            id="pivoted",
            type="table",
            rows=["region"],
            columns=["month"],  # values omitted — inferred as amount, orders
            style={"column_defaults": {"align": "center"}},
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        assert resolved.columns is not None
        assert "amount" not in resolved.columns
        assert "orders" not in resolved.columns
        assert set(resolved.columns) == {"region", "month"}

        svg = render_table_svg(
            resolved, data, board_style=resolve_style(get_theme_style())
        )
        assert "\x1e" not in svg
        assert "Amount" in svg and "Orders" in svg

    def test_pivot_explicit_leaf_keyed_columns_do_not_narrow_the_mapping(self) -> None:
        """Explicit `style.columns` keyed by the bare pivoted values (the
        single-dim single-measure authoring shape) styles those columns only:
        the resolved mapping still covers every query-inferred column plus
        the authored keys, and the `rows:` dimension renders unless it is
        explicitly `visible: false`."""
        data = [
            {"row_sort": 1, "cell_column": "c1", "cell_value": "x"},
            {"row_sort": 1, "cell_column": "c2", "cell_value": "y"},
            {"row_sort": 2, "cell_column": "c1", "cell_value": "z"},
            {"row_sort": 2, "cell_column": "c2", "cell_value": "w"},
        ]
        chart = TableChart(
            id="pivoted",
            type="table",
            rows=["row_sort"],
            columns=["cell_column"],
            values=["cell_value"],
            style={"columns": {"c1": {"visible": True}, "c2": {}}},
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        assert resolved.columns is not None
        assert set(resolved.columns) == {"row_sort", "cell_column", "c1", "c2"}

        svg = render_table_svg(
            resolved, data, board_style=resolve_style(get_theme_style())
        )
        assert "Row Sort" in svg

    def test_pivot_row_dimension_visible_false_hides_it(self) -> None:
        """`visible: false` is the explicit way to hide a `rows:` dimension
        that is a sort key rather than display data."""
        data = [
            {"row_sort": 1, "cell_column": "c1", "cell_value": "x"},
            {"row_sort": 1, "cell_column": "c2", "cell_value": "y"},
            {"row_sort": 2, "cell_column": "c1", "cell_value": "z"},
            {"row_sort": 2, "cell_column": "c2", "cell_value": "w"},
        ]
        chart = TableChart(
            id="pivoted",
            type="table",
            rows=["row_sort"],
            columns=["cell_column"],
            values=["cell_value"],
            style={"columns": {"row_sort": {"visible": False}}},
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            resolved, data, board_style=resolve_style(get_theme_style())
        )
        assert ">x<" in svg
        assert "Row Sort" not in svg

    def test_spark_shorthand_normalized_at_resolve(self) -> None:
        """`spark: <type>` shorthand is normalized to SparkConfig by the time
        resolution publishes the final column mapping."""
        from dbt_charts.core.compile.models.chart.authored import SparkConfig

        resolved = _resolve_table(
            {"columns": {"trend": {"spark": "line"}}},
            data=[{"trend": [1, 2, 3]}],
        )
        assert resolved.columns is not None
        assert isinstance(resolved.columns["trend"].spark, SparkConfig)
        assert resolved.columns["trend"].spark.type == "line"


class TestColumnDefaultsModelValidation:
    """TableColumnDefaultsConfig rejects unknown fields."""

    def test_rejects_non_defaultable_field(self) -> None:
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.authored import ChartStylePatch

        with pytest.raises(ValidationError):
            ChartStylePatch.model_validate(
                {
                    "table": {
                        "column_defaults": {
                            "link": "/detail/{{id}}"  # link is not a valid default
                        }
                    }
                }
            )

    def test_rejects_spark_as_default(self) -> None:
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.authored import ChartStylePatch

        with pytest.raises(ValidationError):
            ChartStylePatch.model_validate(
                {
                    "table": {"column_defaults": {"spark": "line"}}
                }  # spark is not a valid default
            )


class TestColumnDefaultsThroughRender:
    """column_defaults survives the resolve() pipeline and reaches the renderer."""

    def test_column_defaults_applied_to_query_inferred_columns_at_render(self) -> None:
        """render_table_svg renders column_defaults applied to all query columns
        when no explicit style.columns is authored — the no-columns-list shape."""
        resolved = _resolve_table(
            {"column_defaults": {"label": " ", "width": 50, "align": "center"}},
            data=[{"x": 1, "y": 2}, {"x": 3, "y": 4}],
        )
        svg = render_table_svg(
            resolved,
            [{"x": 1, "y": 2}, {"x": 3, "y": 4}],
            board_style=resolve_style(get_theme_style()),
        )
        assert isinstance(svg, str) and "<svg" in svg
