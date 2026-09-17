"""Constructor tests for the small-multiples panel split.

``partition()`` panelizes query rows into a ``ChartDataset`` once, before
resolve; ``regroup()`` re-applies the baked ``panel_axes`` at render;
``map_panels()`` is the panel-iteration combinator gap-fill and other
per-panel work runs through.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.models.chart.authored import MultiplesConfig
from dbt_charts.core.compile.models.chart.resolved import PartitionAxis
from dbt_charts.core.compile.resolve.chart._chart_rows import (
    PanelRows,
    fold_panels,
    map_panels,
    partition,
    regroup,
    restripe,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError


def _rows(*dicts: dict[str, Any]) -> list[dict[str, Any]]:
    return list(dicts)


class TestPartitionNonFaceted:
    """multiples is None: the N=1 case, not a special one."""

    def test_none_multiples_yields_one_panel_with_all_rows(self):
        rows = _rows({"x": 1, "y": 2}, {"x": 2, "y": 3})
        ds = partition(None, rows)
        assert ds.axes == ()
        assert len(ds.panels) == 1
        assert ds.panels[0].key == ()
        assert ds.panels[0].rows == rows

    def test_empty_rows_yields_one_empty_panel_no_column_check(self):
        # No missing-column check even though "region" isn't a column of [] —
        # mirrors FacetFeature's old `if data:` guard.
        ds = partition(MultiplesConfig(rows="region"), [])
        assert ds.axes == ()
        assert len(ds.panels) == 1
        assert ds.panels[0].rows == []


class TestPartitionRowsOnly:
    def test_rows_only_splits_and_strips_partition_column(self):
        rows = _rows(
            {"month": "Jan", "region": "West", "revenue": 10},
            {"month": "Feb", "region": "West", "revenue": 20},
            {"month": "Jan", "region": "East", "revenue": 5},
        )
        ds = partition(MultiplesConfig(rows="region"), rows)
        assert ds.axes == (PartitionAxis(field="region", values=('"West"', '"East"')),)
        assert [p.key for p in ds.panels] == [("West",), ("East",)]
        west = next(p for p in ds.panels if p.key == ("West",)).rows
        assert west == [
            {"month": "Jan", "revenue": 10},
            {"month": "Feb", "revenue": 20},
        ]
        assert "region" not in west[0]

    def test_missing_field_raises_with_available_columns(self):
        rows = _rows({"month": "Jan", "revenue": 10})
        with pytest.raises(ChartDataError, match="not found in the query result"):
            partition(MultiplesConfig(rows="regionn"), rows)

    def test_a_row_missing_the_field_raises_even_when_the_first_row_has_it(self):
        """Ragged inline data: the first row carries `region`, a later one
        doesn't. The missing-column guard must check every row, not just
        `rows[0]` — otherwise `row.get(field)` defaults the later row's
        value to `None` and silently builds a phantom `(None,)` panel
        instead of raising, reintroducing the original null-panel defect
        through the one function meant to make it unrepresentable.
        """
        rows = _rows(
            {"region": "West", "month": "Jan", "revenue": 1},
            {"month": "Feb", "revenue": 2},  # region omitted
        )
        with pytest.raises(ChartDataError, match="not found in the query result"):
            partition(MultiplesConfig(rows="region"), rows)

    def test_a_real_sql_null_partition_value_stays_a_legitimate_panel(self):
        """The field is PRESENT with value `None` (a real SQL NULL) on every
        row — distinct from the ragged-row case above, where the field is
        ABSENT. A real NULL is a legitimate panel value, not an error.
        """
        rows = _rows(
            {"region": None, "month": "Jan", "revenue": 1},
            {"region": None, "month": "Feb", "revenue": 2},
        )
        ds = partition(MultiplesConfig(rows="region"), rows)
        assert [p.key for p in ds.panels] == [(None,)]


class TestPartitionGrid:
    def test_grid_keys_are_observed_cells_only(self):
        """No cell is synthesized for a (row, col) combination never seen."""
        rows = _rows(
            {"region": "West", "product": "Widgets", "revenue": 1},
            {"region": "East", "product": "Gadgets", "revenue": 2},
        )
        ds = partition(MultiplesConfig(rows="region", columns="product"), rows)
        assert [p.key for p in ds.panels] == [
            ("West", "Widgets"),
            ("East", "Gadgets"),
        ]

    def test_same_column_on_both_axes_only_diagonal_observed(self):
        rows = _rows(
            {"series": "A", "revenue": 1},
            {"series": "B", "revenue": 2},
        )
        ds = partition(MultiplesConfig(rows="series", columns="series"), rows)
        assert [p.key for p in ds.panels] == [("A", "A"), ("B", "B")]


class TestPartitionRowOrder:
    """Rule 2a: within a panel, query row order; across panels, baked axis order."""

    def test_within_panel_row_order_is_query_order(self):
        rows = _rows(
            {"region": "West", "x": 3},
            {"region": "East", "x": 1},
            {"region": "West", "x": 2},
        )
        ds = partition(MultiplesConfig(rows="region"), rows)
        west = next(p for p in ds.panels if p.key == ("West",))
        assert [r["x"] for r in west.rows] == [3, 2]

    def test_panel_order_follows_first_encounter_of_each_axis_value(self):
        # "East" is encountered before "West" in the data.
        rows = _rows(
            {"region": "East", "x": 1},
            {"region": "West", "x": 2},
        )
        ds = partition(MultiplesConfig(rows="region"), rows)
        assert [p.key for p in ds.panels] == [("East",), ("West",)]
        assert [r["region"] for r in ds.all_rows()] == ["East", "West"]

    def test_all_rows_re_stamps_original_partition_value_and_reinterleaves_query_order(
        self,
    ):
        """``all_rows()`` restores the ORIGINAL query row order (not the
        panel-grouped order ``partition()`` splits into) — the flat VL wire
        format must not move the shared nominal x domain just because panels
        interleaved differently than the query returned them."""
        rows = _rows(
            {"region": "East", "x": 1},
            {"region": "West", "x": 2},
            {"region": "East", "x": 3},
        )
        ds = partition(MultiplesConfig(rows="region"), rows)
        assert ds.all_rows() == [
            {"x": 1, "region": "East"},
            {"x": 2, "region": "West"},
            {"x": 3, "region": "East"},
        ]


class TestTwoCurrencies:
    """`Panel.key` holds the ORIGINAL partition value; `PartitionAxis.values`
    holds its CANONICAL string form; `all_rows()` re-stamps the original.
    Every other test in this file uses a plain `str` partition value, whose
    canonical form (a quoted JSON string) still differs from the original —
    but a `date` value shows the divergence more clearly (`canonical_key`
    runs it through `pydantic_core.to_json`, `Panel.key` keeps the `date`).
    """

    def test_date_partition_value_keeps_two_distinct_currencies(self):
        import datetime

        rows = _rows(
            {"month": datetime.date(2024, 1, 1), "x": 1},
            {"month": datetime.date(2024, 2, 1), "x": 2},
        )
        ds = partition(MultiplesConfig(rows="month"), rows)

        assert ds.axes == (
            PartitionAxis(field="month", values=('"2024-01-01"', '"2024-02-01"')),
        )
        panel = next(p for p in ds.panels if p.key == (datetime.date(2024, 1, 1),))
        assert isinstance(panel.key[0], datetime.date)

        restamped = ds.all_rows()
        stamped_month = next(r["month"] for r in restamped if r["x"] == 1)
        assert stamped_month == datetime.date(2024, 1, 1)
        assert isinstance(stamped_month, datetime.date)
        assert not isinstance(stamped_month, str)


class TestCanonicalCollision:
    def test_stringification_collision_raises_instead_of_merging(self):
        from decimal import Decimal

        # Decimal("1.50") and the str "1.50" both canonicalize to the JSON
        # string `"1.50"` under pydantic_core.to_json.
        rows = _rows({"n": Decimal("1.50"), "x": 1}, {"n": "1.50", "x": 2})
        with pytest.raises(ChartDataError, match="stringify"):
            partition(MultiplesConfig(rows="n"), rows)

    def test_two_nan_rows_do_not_raise_a_spurious_collision(self):
        """`nan != nan` under IEEE754 — two rows both carrying the same NaN
        partition value must fold into one panel, not raise a collision
        between what the naive `previous != value` check sees as two
        distinct values."""
        rows = _rows({"n": float("nan"), "x": 1}, {"n": float("nan"), "x": 2})
        ds = partition(MultiplesConfig(rows="n"), rows)
        assert len(ds.panels) == 1


class TestAllRowsNonFaceted:
    def test_non_faceted_all_rows_is_identity(self):
        rows = _rows({"x": 1}, {"x": 2})
        assert partition(None, rows).all_rows() == rows


class TestColumnValues:
    def test_partition_field_reads_original_values_from_axes(self):
        rows = _rows({"region": "West", "x": 1}, {"region": "East", "x": 2})
        ds = partition(MultiplesConfig(rows="region"), rows)
        assert ds.column_values("region") == ("West", "East")

    def test_non_partition_field_unions_across_panels(self):
        rows = _rows(
            {"region": "West", "series": "A"},
            {"region": "East", "series": "A"},
            {"region": "East", "series": "B"},
        )
        ds = partition(MultiplesConfig(rows="region"), rows)
        assert ds.column_values("series") == ("A", "B")

    def test_second_axis_of_a_grid_reads_its_own_query_order_not_panel_order(self):
        """Panels are ordered by the axes' cartesian product (row-major over
        the row axis), which is NOT the column axis's own first-encounter
        order in a sparse grid: column "B" is query-first (row 1) but its
        only panel sits under row-axis value "R2", traversed after "R1"."""
        rows = _rows(
            {"r": "R1", "c": "B"},
            {"r": "R2", "c": "A"},
            {"r": "R1", "c": "C"},
        )
        ds = partition(MultiplesConfig(rows="r", columns="c"), rows)
        assert ds.column_values("r") == ("R1", "R2")
        assert ds.column_values("c") == ("B", "A", "C")

    def test_regrouped_subset_skips_a_baked_axis_value_with_no_panel(self):
        """The row-truncated render path (`regroup()`) can hold fewer rows
        than resolve saw: a baked axis value absent from this subset has no
        panel to recover an original value from, and must be skipped rather
        than raising."""
        full_rows = _rows(
            {"region": "West", "x": 1},
            {"region": "East", "x": 2},
            {"region": "North", "x": 3},
        )
        axes = partition(MultiplesConfig(rows="region"), full_rows).axes
        truncated_rows = [row for row in full_rows if row["region"] != "North"]
        ds = regroup(axes, truncated_rows)
        assert ds.column_values("region") == ("West", "East")


class TestRegroup:
    def test_no_axes_is_one_panel_with_all_rows(self):
        rows = _rows({"x": 1}, {"x": 2})
        ds = regroup((), rows)
        assert ds.axes == ()
        assert ds.panels[0].rows == rows

    def test_regroups_by_baked_axes_in_baked_order(self):
        axes = (PartitionAxis(field="region", values=('"West"', '"East"')),)
        rows = _rows(
            {"region": "East", "x": 1},
            {"region": "West", "x": 2},
        )
        ds = regroup(axes, rows)
        assert ds.axes == axes
        assert [p.key for p in ds.panels] == [("West",), ("East",)]

    def test_non_empty_axes_with_zero_rows_yields_zero_panels(self):
        """Unlike ``partition()``, which always bakes one (possibly empty)
        panel for zero rows, ``regroup()`` with baked (non-empty) axes and
        zero rows yields zero panels — there is nothing to render for any
        of the baked panel values, as distinct from a chart resolved
        against no data at all (``axes == ()``, the trivial N=1 case,
        where one empty panel is exactly right — see
        ``test_no_axes_is_one_panel_with_all_rows``)."""
        axes = (PartitionAxis(field="region", values=('"West"', '"East"')),)
        ds = regroup(axes, [])
        assert ds.axes == axes
        assert ds.panels == ()

    def test_raises_on_value_absent_from_baked_axes(self):
        axes = (PartitionAxis(field="region", values=('"West"',)),)
        rows = _rows({"region": "North", "x": 1})
        with pytest.raises(ChartDataError, match="region"):
            regroup(axes, rows)

    def test_raises_on_a_row_missing_the_field_even_when_none_is_a_baked_panel(self):
        """A row missing `region` entirely must raise, never silently
        misroute into a `(None,)` panel — even when a `(None,)` panel is
        itself legitimately baked (from a real SQL NULL resolve saw). If
        the guard used `row.get(field)` here, the missing field would
        default to `None` and match that legitimate panel by accident.

        This is a distinct error from "value not among the resolved axes":
        a `(None,)` panel is itself legitimate here, so the message must not
        claim the row's value *was* `None` (it has no value for `region` at
        all) — a different code names the actual defect, a ragged row.
        """
        axes = (PartitionAxis(field="region", values=('"West"', "null")),)
        rows = _rows({"x": 1})  # region entirely absent from this row
        with pytest.raises(ChartDataError, match="region") as exc_info:
            regroup(axes, rows)
        assert exc_info.value.code is not None
        assert exc_info.value.code.code == "ERR-MULTIPLES-ROW-MISSING-PARTITION-FIELD"
        assert "None" not in str(exc_info.value)


class TestMapPanels:
    def test_runs_fn_per_panel_and_reassembles(self):
        rows = _rows({"region": "West", "x": 1}, {"region": "East", "x": 2})
        ds = partition(MultiplesConfig(rows="region"), rows)
        out = map_panels(
            ds,
            None,
            lambda color, panel_rows: PanelRows(
                [{**r, "seen": color} for r in panel_rows]
            ),
        )
        assert out.axes == ds.axes
        assert [p.rows for p in out.panels] == [
            [{"x": 1, "seen": None}],
            [{"x": 2, "seen": None}],
        ]

    def test_nulls_color_field_when_it_is_a_partition_field(self):
        rows = _rows({"series": "A", "x": 1})
        ds = partition(MultiplesConfig(rows="series"), rows)
        seen_colors: list[str | None] = []

        def fn(color: str | None, panel_rows: PanelRows) -> PanelRows:
            seen_colors.append(color)
            return panel_rows

        map_panels(ds, "series", fn)
        assert seen_colors == [None]

    def test_keeps_color_field_when_not_a_partition_field(self):
        rows = _rows({"region": "West", "series": "A", "x": 1})
        ds = partition(MultiplesConfig(rows="region"), rows)
        seen_colors: list[str | None] = []

        def fn(color: str | None, panel_rows: PanelRows) -> PanelRows:
            seen_colors.append(color)
            return panel_rows

        map_panels(ds, "series", fn)
        assert seen_colors == ["series"]

    def test_preserves_within_panel_row_order_on_identity_fn(self):
        rows = _rows(
            {"region": "West", "x": 3},
            {"region": "West", "x": 1},
        )
        ds = partition(MultiplesConfig(rows="region"), rows)
        out = map_panels(ds, None, lambda _color, panel_rows: panel_rows)
        assert [r["x"] for r in out.panels[0].rows] == [3, 1]

    def test_output_panels_are_untracked(self):
        """map_panels()'s fn can add/drop/reorder rows (gap-fill), so a
        panel's original query-row indices can no longer be trusted —
        every output panel must come back "untracked" (indices is None,
        distinct from () — a real, tracked zero-row panel), which routes
        its all_rows() through the panel-order fallback instead of
        reinterleaving into a now-invalid query order."""
        rows = _rows({"region": "West", "x": 1}, {"region": "East", "x": 2})
        ds = partition(MultiplesConfig(rows="region"), rows)
        out = map_panels(ds, None, lambda _color, panel_rows: panel_rows)
        assert all(panel.indices is None for panel in out.panels)


class TestAllRowsFallsBackWhenUntracked:
    def test_untracked_dataset_concatenates_in_panel_order(self):
        """An interleaved-query dataset run through map_panels() loses index
        tracking; all_rows() must fall back to panel-order concatenation
        (not raise, not silently reinterleave garbage indices)."""
        rows = _rows(
            {"region": "East", "x": 1},
            {"region": "West", "x": 2},
            {"region": "East", "x": 3},
        )
        ds = partition(MultiplesConfig(rows="region"), rows)
        out = map_panels(ds, None, lambda _color, panel_rows: panel_rows)
        assert out.all_rows() == [
            {"x": 1, "region": "East"},
            {"x": 3, "region": "East"},
            {"x": 2, "region": "West"},
        ]


class TestRestripe:
    def test_reslices_by_recorded_index_not_position_in_rows(self):
        """restripe() must re-split a mutated flat row list by each panel's
        recorded original index, not by position — the caller's mutated
        list may be query-ordered (interleaved across panels), which no
        longer lines up with a panel-order positional slice."""
        rows = _rows(
            {"region": "A", "x": 1},
            {"region": "B", "x": 2},
            {"region": "A", "x": 3},
            {"region": "B", "x": 4},
        )
        ds = partition(MultiplesConfig(rows="region"), rows)
        # Mutate every row's "x" in place, keeping rows in the SAME (query)
        # order restripe() must align against via indices, not position.
        mutated = [{**r, "x": r["x"] * 10} for r in rows]
        out = restripe(ds, mutated)
        panel_a = next(p for p in out.panels if p.key == ("A",))
        panel_b = next(p for p in out.panels if p.key == ("B",))
        assert [r["x"] for r in panel_a.rows] == [10, 30]
        assert [r["x"] for r in panel_b.rows] == [20, 40]
        assert "region" not in panel_a.rows[0]

    def test_raises_on_untracked_dataset(self):
        """A dataset that already lost index tracking (map_panels()) cannot
        be restriped by index — this must raise, not silently trust
        position (the exact escape hatch that caused the panel-membership
        corruption this fix exists to close)."""
        rows = _rows({"region": "West", "x": 1}, {"region": "East", "x": 2})
        ds = partition(MultiplesConfig(rows="region"), rows)
        untracked = map_panels(ds, None, lambda _color, panel_rows: panel_rows)
        with pytest.raises(ValueError, match="original row indices"):
            restripe(untracked, rows)


class TestFoldPanels:
    def test_shared_reduces_max_across_panels(self):
        rows = _rows(
            {"region": "West", "x": 10.0},
            {"region": "East", "x": 30.0},
            {"region": "East", "x": 5.0},
        )
        ds = partition(MultiplesConfig(rows="region"), rows)
        result = fold_panels(
            ds,
            "shared",
            lambda panel_rows: max((r["x"] for r in panel_rows), default=None),
        )
        assert result == 30.0

    def test_independent_bakes_nothing(self):
        rows = _rows({"region": "West", "x": 10.0}, {"region": "East", "x": 30.0})
        ds = partition(MultiplesConfig(rows="region"), rows)
        result = fold_panels(
            ds,
            "independent",
            lambda panel_rows: max((r["x"] for r in panel_rows), default=None),
        )
        assert result is None

    def test_non_faceted_shared_equals_whole_dataset_computation(self):
        rows = _rows({"x": 10.0}, {"x": 30.0})
        ds = partition(None, rows)
        result = fold_panels(
            ds,
            "shared",
            lambda panel_rows: max((r["x"] for r in panel_rows), default=None),
        )
        assert result == 30.0
