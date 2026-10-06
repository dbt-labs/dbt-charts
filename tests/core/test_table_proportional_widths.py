"""Tests for the proportional column-width allocator.

After this feature: auto-columns distribute available_width proportionally to
their measured content demand instead of equally. A wide-text column gets more
budget; a narrow-numeric column gets less.  All columns still sum to
available_width.  The anti-pathological cap (60%) prevents one giant column
from eating the whole budget.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Unit tests for _proportional_allocate
# ---------------------------------------------------------------------------


class TestProportionalAllocateHelper:
    """Direct tests for the internal allocation helper."""

    def test_proportional_to_demand(self):
        """Budget is split in proportion to demands when no column exceeds cap."""
        from dbt_charts.core.render.chart.table_support import _proportional_allocate

        # 3-column case: max share is 200/450 = 44.4% < 60% cap → exact proportional
        # When total_demand == budget, each column gets exactly its demand.
        demands = {"a": 100.0, "b": 200.0, "c": 150.0}
        result = _proportional_allocate(demands, 450.0)
        assert abs(result["a"] - 100.0) <= 0.5
        assert abs(result["b"] - 200.0) <= 0.5
        assert abs(result["c"] - 150.0) <= 0.5
        assert abs(sum(result.values()) - 450.0) <= 0.5

    def test_single_column_gets_full_budget(self):
        """A single auto-column always gets the full budget (no cap applies)."""
        from dbt_charts.core.render.chart.table_support import _proportional_allocate

        result = _proportional_allocate({"only": 500.0}, 600.0)
        assert abs(result["only"] - 600.0) <= 0.5

    def test_cap_prevents_pathological_dominance(self):
        """No column gets more than 60% of the budget."""
        from dbt_charts.core.render.chart.table_support import _proportional_allocate

        # One giant column vs four tiny ones
        demands = {"wide": 1000.0, "a": 10.0, "b": 10.0, "c": 10.0, "d": 10.0}
        budget = 500.0
        result = _proportional_allocate(demands, budget)

        cap = budget * 0.6
        assert result["wide"] <= cap + 0.5, (
            f"Wide column got {result['wide']:.1f}px, expected cap {cap:.1f}px"
        )
        assert abs(sum(result.values()) - budget) <= 0.5

    def test_equal_demands_give_equal_shares(self):
        """When all demands are equal, allocation equals equal-distribution."""
        from dbt_charts.core.render.chart.table_support import _proportional_allocate

        demands = {"a": 100.0, "b": 100.0, "c": 100.0}
        result = _proportional_allocate(demands, 300.0)
        for col in demands:
            assert abs(result[col] - 100.0) <= 0.5

    def test_budget_always_fully_consumed(self):
        """All budget is allocated regardless of demand shape."""
        from dbt_charts.core.render.chart.table_support import _proportional_allocate

        demands = {"text": 250.0, "num1": 50.0, "num2": 50.0, "num3": 50.0}
        budget = 600.0
        result = _proportional_allocate(demands, budget)
        assert abs(sum(result.values()) - budget) <= 0.5


# ---------------------------------------------------------------------------
# calculate_column_layout with demands
# ---------------------------------------------------------------------------


class TestCalculateColumnLayoutWithDemands:
    """calculate_column_layout uses proportional allocation when demands are given."""

    def test_wide_text_column_gets_more_than_numerics(self):
        """Text column with wide demand gets proportionally more width than narrow numerics."""
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["description", "amount", "count", "rate", "score"]
        col_configs = {}
        available_width = 500.0

        # Text column wants 200px total; numerics want 60px each
        demands = {
            "description": 200.0,
            "amount": 60.0,
            "count": 60.0,
            "rate": 60.0,
            "score": 60.0,
        }

        col_widths, _, actual_w = calculate_column_layout(
            columns, col_configs, available_width, demands=demands
        )

        # Proportional: description ≈ 200/440 * 500 ≈ 227px; each numeric ≈ 68px
        for num_col in ("amount", "count", "rate", "score"):
            assert col_widths["description"] > col_widths[num_col], (
                f"description ({col_widths['description']:.1f}px) should be wider "
                f"than {num_col} ({col_widths[num_col]:.1f}px)"
            )
        assert abs(actual_w - available_width) <= 0.5

    def test_no_demands_falls_back_to_equal_distribution(self):
        """Without demands kwarg, equal distribution is preserved."""
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["a", "b", "c"]
        col_configs = {}
        available_width = 300.0

        col_widths, _, actual_w = calculate_column_layout(
            columns, col_configs, available_width
        )
        for col in columns:
            assert abs(col_widths[col] - 100.0) <= 0.5
        assert abs(actual_w - available_width) <= 0.5

    def test_explicit_width_excluded_from_proportional_pool(self):
        """Columns with explicit width: are not part of the proportional pool."""
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table_support import calculate_column_layout

        columns = ["label", "amount", "count"]
        col_configs = {"label": TableColumnConfig(width=150)}
        available_width = 600.0

        # After explicit 150px for label, 450px left for amount+count
        # amount wants 200px, count wants 50px → proportional: 360px and 90px
        demands = {"label": 300.0, "amount": 200.0, "count": 50.0}

        col_widths, _, actual_w = calculate_column_layout(
            columns, col_configs, available_width, demands=demands
        )

        assert abs(col_widths["label"] - 150.0) <= 0.5, "Explicit width must be honored"
        assert col_widths["amount"] > col_widths["count"], (
            "amount (high demand) should be wider than count (low demand)"
        )
        assert abs(actual_w - available_width) <= 0.5

    def test_proportional_widths_sum_to_available_width(self):
        """Proportional allocation always sums exactly to available_width."""
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["text", "n1", "n2", "n3"]
        col_configs = {}
        available_width = 750.0
        demands = {"text": 300.0, "n1": 70.0, "n2": 70.0, "n3": 70.0}

        col_widths, _, actual_w = calculate_column_layout(
            columns, col_configs, available_width, demands=demands
        )
        assert abs(actual_w - available_width) <= 0.5
        assert abs(sum(col_widths.values()) - available_width) <= 0.5


# ---------------------------------------------------------------------------
# measure_column_demands
# ---------------------------------------------------------------------------


class TestMeasureColumnDemands:
    """measure_column_demands returns realistic demand widths per column."""

    def test_wider_text_has_higher_demand_than_short_ints(self):
        """Long-text column cell demand exceeds short-integer column cell demand."""
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table_support import (
            measure_column_demands,
        )

        columns = ["description", "score"]
        col_configs = {}
        data = [
            {
                "description": "A very long product description that spans many characters",
                "score": 1,
            },
            {
                "description": "Another lengthy description text for this product item",
                "score": 2,
            },
            {"description": "Short desc", "score": 3},
        ]
        measurer = get_font_measurer()
        cell_demands, _header_demands = measure_column_demands(
            columns,
            col_configs,
            data,
            measurer,
            header_measurer=measurer,
            font_size=13.0,
            header_font_size=12.0,
            cell_pad=8,
            formats={"number": ".3~s"},
            column_when_rules={},
        )

        assert cell_demands["description"] > cell_demands["score"], (
            f"Long-text column cell demand ({cell_demands['description']:.1f}px) should exceed "
            f"short-int column cell demand ({cell_demands['score']:.1f}px)"
        )

    def test_header_tracked_in_header_demands_not_cell_demands(self) -> None:
        """Header width is tracked in header_demands, not cell_demands.

        With empty data, both columns have identical cell demand (just padding).
        The long header column has a higher header_demands value.
        """
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table_support import (
            measure_column_demands,
        )

        columns = ["x", "a_very_long_column_header"]
        col_configs = {}
        measurer = get_font_measurer()
        cell_demands, header_demands = measure_column_demands(
            columns,
            col_configs,
            [],  # empty data
            measurer,
            header_measurer=measurer,
            font_size=13.0,
            header_font_size=12.0,
            cell_pad=8,
            column_when_rules={},
        )

        # Cell demands are equal for empty data (just padding).
        assert (
            abs(cell_demands["a_very_long_column_header"] - cell_demands["x"]) <= 0.5
        ), (
            "Cell demands should be equal for empty data; "
            f"got {cell_demands['a_very_long_column_header']:.1f} vs {cell_demands['x']:.1f}"
        )
        # Long header must have higher header_demands.
        assert header_demands["a_very_long_column_header"] > header_demands["x"], (
            f"Long header demand ({header_demands['a_very_long_column_header']:.1f}px) "
            f"should exceed short header demand ({header_demands['x']:.1f}px)"
        )


# ---------------------------------------------------------------------------
# _cluster_and_equalize_widths
# ---------------------------------------------------------------------------


class TestClusterEqualization:
    """Tests for the cluster-and-equalize post-processing pass."""

    def test_five_near_equal_numerics_get_same_width(self):
        """Five numeric columns with close demands end up at the same width."""
        from dbt_charts.core.render.chart.table_support import (
            _cluster_and_equalize_widths,
        )

        # Build widths that _proportional_allocate would give for demands 78/82/85/88/91
        # with budget 500. Simulate those proportional results directly.
        total_demand = 78 + 82 + 85 + 88 + 91  # 424
        budget = 500.0
        widths = {
            "a": 78 / total_demand * budget,
            "b": 82 / total_demand * budget,
            "c": 85 / total_demand * budget,
            "d": 88 / total_demand * budget,
            "e": 91 / total_demand * budget,
        }
        result = _cluster_and_equalize_widths(widths, budget, threshold=0.8)
        # All five should be within 0.5px of each other
        vals = list(result.values())
        assert max(vals) - min(vals) <= 0.5
        assert abs(sum(result.values()) - budget) <= 0.5

    def test_two_clusters_stay_separate(self):
        """Six widths split into two clusters stay separate after equalization."""
        from dbt_charts.core.render.chart.table_support import (
            _cluster_and_equalize_widths,
        )

        # Widths 100/110/120/130/140/150 — first three cluster, last three cluster
        budget = 750.0
        widths = {
            "a": 100.0,
            "b": 110.0,
            "c": 120.0,
            "d": 130.0,
            "e": 140.0,
            "f": 150.0,
        }
        result = _cluster_and_equalize_widths(widths, budget, threshold=0.8)
        # Within first cluster: a == b == c (all snapped to 120, then rescaled)
        assert abs(result["a"] - result["b"]) < 1e-6
        assert abs(result["b"] - result["c"]) < 1e-6
        # Within second cluster: d == e == f
        assert abs(result["d"] - result["e"]) < 1e-6
        assert abs(result["e"] - result["f"]) < 1e-6
        # Two cluster widths differ by > 10% (ratio < 0.9)
        low = min(result["a"], result["d"])
        high = max(result["a"], result["d"])
        assert low / high < 0.9
        assert abs(sum(result.values()) - budget) <= 0.5

    def test_all_different_widths_pass_through_unchanged(self):
        """Widths with no adjacent pair meeting the threshold pass through unchanged."""
        from dbt_charts.core.render.chart.table_support import (
            _cluster_and_equalize_widths,
        )

        # Adjacent ratios: 80/110=0.73, 110/160=0.69, 160/240=0.67, 240/360=0.67
        # All < 0.8, so every column is its own cluster — no snapping.
        budget = 950.0
        widths = {"a": 80.0, "b": 110.0, "c": 160.0, "d": 240.0, "e": 360.0}
        result = _cluster_and_equalize_widths(widths, budget, threshold=0.8)
        for col, orig in widths.items():
            assert abs(result[col] - orig) <= 0.5, (
                f"Column {col}: expected ~{orig:.1f}, got {result[col]:.1f}"
            )

    def test_all_equal_widths_pass_through_unchanged(self):
        """All-equal widths form one cluster and rescale is a no-op."""
        from dbt_charts.core.render.chart.table_support import (
            _cluster_and_equalize_widths,
        )

        budget = 300.0
        widths = {"a": 100.0, "b": 100.0, "c": 100.0}
        result = _cluster_and_equalize_widths(widths, budget, threshold=0.8)
        for col in widths:
            assert abs(result[col] - 100.0) <= 0.5
        assert abs(sum(result.values()) - budget) <= 0.5

    def test_single_column_is_noop(self):
        """A single-column dict passes through with budget unchanged."""
        from dbt_charts.core.render.chart.table_support import (
            _cluster_and_equalize_widths,
        )

        budget = 400.0
        result = _cluster_and_equalize_widths({"only": budget}, budget, threshold=0.8)
        assert abs(result["only"] - budget) <= 0.5

    def test_budget_preserved_for_all_cases(self):
        """Sum of result widths always equals budget (within 1px)."""
        from dbt_charts.core.render.chart.table_support import (
            _cluster_and_equalize_widths,
        )

        cases = [
            # near-equal
            ({"a": 91.6, "b": 96.2, "c": 100.0, "d": 103.8, "e": 107.4}, 500.0),
            # two clusters
            (
                {
                    "a": 100.0,
                    "b": 110.0,
                    "c": 120.0,
                    "d": 130.0,
                    "e": 140.0,
                    "f": 150.0,
                },
                750.0,
            ),
            # all different
            ({"a": 80.0, "b": 110.0, "c": 160.0, "d": 240.0, "e": 360.0}, 950.0),
            # all equal
            ({"a": 100.0, "b": 100.0, "c": 100.0}, 300.0),
        ]
        for widths, budget in cases:
            result = _cluster_and_equalize_widths(widths, budget, threshold=0.8)
            assert abs(sum(result.values()) - budget) <= 1.0

    def test_explicit_widths_untouched_auto_columns_fill_budget(self):
        """Explicit-width columns are excluded; auto compact columns scale to fill."""
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.render.chart.table_support import calculate_column_layout

        columns = ["label", "a", "b", "c"]
        col_configs = {"label": TableColumnConfig(width=200)}
        available_width = 800.0
        # After 200px explicit, 600px for three auto columns with compact demands
        demands = {"label": 300.0, "a": 78.0, "b": 82.0, "c": 85.0}

        col_widths, _, actual_w = calculate_column_layout(
            columns, col_configs, available_width, demands=demands
        )
        assert abs(col_widths["label"] - 200.0) <= 0.5
        # Auto columns (all compact) scale proportionally to fill 600px budget
        assert abs(col_widths["a"] + col_widths["b"] + col_widths["c"] - 600.0) <= 0.5
        # Ordering preserved: a < b < c (demand order maintained in proportional scale)
        assert col_widths["a"] < col_widths["b"] < col_widths["c"]
        assert abs(actual_w - available_width) <= 0.5

    def test_within_cluster_identicality_survives_rescale(self):
        """All members of a cluster are exactly equal (not just close) after rescale."""
        from dbt_charts.core.render.chart.table_support import (
            _cluster_and_equalize_widths,
        )

        budget = 500.0
        widths = {"a": 91.6, "b": 96.2, "c": 100.0, "d": 135.0, "e": 140.0}
        result = _cluster_and_equalize_widths(widths, budget, threshold=0.8)
        # a, b, c should be in the same cluster; d, e in another
        # Within each cluster members must be exactly equal (float-identical)
        # Cluster 1: a/b/c — all snapped to 100.0 then scaled equally
        assert result["a"] == result["b"] == result["c"]
        # Cluster 2: d/e — snapped to 140.0 then scaled equally
        assert result["d"] == result["e"]

    def test_integration_via_calculate_column_layout(self):
        """Full integration: 5-numeric (compact) table fills budget proportionally."""
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["rev", "cost", "profit", "margin", "count"]
        col_configs = {}
        available_width = 600.0
        # Near-equal compact demands — all < 220px so all compact
        demands = {
            "rev": 78.0,
            "cost": 82.0,
            "profit": 85.0,
            "margin": 88.0,
            "count": 91.0,
        }

        col_widths, _, actual_w = calculate_column_layout(
            columns, col_configs, available_width, demands=demands
        )
        # All compact: scale proportionally to fill budget
        assert abs(actual_w - available_width) <= 0.5
        assert abs(sum(col_widths.values()) - available_width) <= 0.5
        # Ordering preserved: proportional scale keeps relative order
        assert col_widths["rev"] < col_widths["count"]

    def test_threshold_10_disables_clustering(self):
        """threshold=1.0 means only exactly-equal widths cluster — near-equal stay distinct."""
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["a", "b", "c", "d", "e"]
        col_configs = {}
        available_width = 500.0
        demands = {"a": 78.0, "b": 82.0, "c": 85.0, "d": 88.0, "e": 91.0}

        col_widths, _, actual_w = calculate_column_layout(
            columns,
            col_configs,
            available_width,
            demands=demands,
            width_similarity_threshold=1.0,
        )
        # With threshold=1.0, near-equal widths do NOT cluster — all distinct
        vals = sorted(col_widths.values())
        assert vals[-1] - vals[0] > 5.0, "Widths should be distinct when threshold=1.0"
        assert abs(actual_w - available_width) <= 0.5

    def test_threshold_00_forces_equal_within_text_pool(self):
        """threshold=0.0 collapses text-pool columns to equal width."""
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        # Two text columns with very different demands should equalize when threshold=0.0.
        # Compact column stays at its proportional pin.
        columns = ["a", "text1", "text2"]
        col_configs = {}
        available_width = 900.0
        # "a" is compact (50px), text1 and text2 are text (> 220px)
        demands = {"a": 50.0, "text1": 300.0, "text2": 600.0}

        col_widths, _, actual_w = calculate_column_layout(
            columns,
            col_configs,
            available_width,
            demands=demands,
            width_similarity_threshold=0.0,
        )
        # Text columns should be equal (threshold=0.0 forces all-same cluster)
        assert abs(col_widths["text1"] - col_widths["text2"]) <= 0.5
        assert abs(actual_w - available_width) <= 0.5

    def test_pydantic_rejects_out_of_range_threshold(self):
        """ValidationError is raised for width_similarity_threshold outside [0, 1]."""
        import pytest
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import TableColumnsStyle

        with pytest.raises(ValidationError, match="width_similarity_threshold"):
            TableColumnsStyle(
                default_width=100.0,
                cell_padding=8.0,
                width_similarity_threshold=-0.1,
                content_headroom=0.10,
            )

        with pytest.raises(ValidationError, match="width_similarity_threshold"):
            TableColumnsStyle(
                default_width=100.0,
                cell_padding=8.0,
                width_similarity_threshold=1.1,
                content_headroom=0.10,
            )

    def test_cascade_override_changes_clustering(self):
        """A chart-level style override for width_similarity_threshold propagates."""
        from dbt_charts.core.compile.models.style.theme import TableColumnsStyle

        # Build a baseline style with default threshold
        baseline = TableColumnsStyle(
            default_width=100.0,
            cell_padding=8.0,
            width_similarity_threshold=0.8,
            content_headroom=0.10,
        )
        # Override to 1.0 (disable clustering)
        overridden = baseline.model_copy(update={"width_similarity_threshold": 1.0})
        assert overridden.width_similarity_threshold == 1.0
        # Baseline unchanged
        assert baseline.width_similarity_threshold == 0.8


# ---------------------------------------------------------------------------
# Compact-vs-text classifier layout integration tests
# ---------------------------------------------------------------------------


class TestCompactVsTextLayout:
    """calculate_column_layout pins compact columns; allocates remainder to text."""

    def test_salesforce_scenario_compact_pinned_text_gets_remainder(self):
        """Headline scenario: 5 compact + 1 text column, 1200px budget.

        Compact columns are pinned to their measured demand (~100–180px each,
        ~800px combined).  The text column gets the remaining ~400px.
        No compact column should be squished below its natural demand.
        """
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["csm_name", "account", "arr", "stage", "plan_type", "next_steps"]
        col_configs = {}
        available_width = 1200.0

        # compact columns: demands 100–180px; text column: 2500px
        demands = {
            "csm_name": 150.0,
            "account": 180.0,
            "arr": 100.0,
            "stage": 130.0,
            "plan_type": 120.0,
            "next_steps": 2500.0,
        }

        col_widths, _, actual_w = calculate_column_layout(
            columns, col_configs, available_width, demands=demands
        )

        compact_cols = ["csm_name", "account", "arr", "stage", "plan_type"]
        for col in compact_cols:
            assert col_widths[col] >= demands[col] - 1.0, (
                f"Compact column '{col}' got {col_widths[col]:.1f}px; "
                f"expected >= {demands[col]:.1f}px (its measured demand)"
            )

        # text column gets the remainder (available - sum_of_compact_demands)
        compact_total = sum(demands[c] for c in compact_cols)
        remaining = available_width - compact_total
        assert col_widths["next_steps"] >= remaining - 1.0, (
            f"Text column got {col_widths['next_steps']:.1f}px; "
            f"expected >= {remaining:.1f}px (remaining budget after compact)"
        )

        assert abs(actual_w - available_width) <= 1.0

    def test_all_compact_fills_budget(self):
        """When all columns are compact and their sum < budget, widths are stretched to fill."""
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["a", "b", "c"]
        col_configs = {}
        available_width = 600.0
        # All compact (demands <= 220px)
        demands = {"a": 100.0, "b": 120.0, "c": 90.0}

        col_widths, _, actual_w = calculate_column_layout(
            columns, col_configs, available_width, demands=demands
        )

        # Budget must be fully consumed
        assert abs(actual_w - available_width) <= 1.0
        # All widths >= their demands (since we're stretching to fill, not squishing)
        for col in columns:
            assert col_widths[col] >= demands[col] - 1.0

    def test_single_text_column_gets_full_budget(self):
        """Single text column with huge demand: gets the full available budget."""
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["notes"]
        col_configs = {}
        available_width = 800.0
        demands = {"notes": 3000.0}

        col_widths, _, actual_w = calculate_column_layout(
            columns, col_configs, available_width, demands=demands
        )
        assert abs(col_widths["notes"] - available_width) <= 1.0
        assert abs(actual_w - available_width) <= 1.0

    def test_max_width_caps_text_column(self):
        """A text column with max_width: set does not exceed that cap."""
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.render.chart.table_support import calculate_column_layout

        columns = ["name", "notes", "count"]
        col_configs = {
            "notes": TableColumnConfig(max_width=300),
        }
        available_width = 900.0
        # notes is text (demand > ceiling); name and count are compact
        demands = {"name": 150.0, "notes": 2500.0, "count": 100.0}

        col_widths, _, actual_w = calculate_column_layout(
            columns, col_configs, available_width, demands=demands
        )
        assert col_widths["notes"] <= 300.0 + 1.0, (
            f"notes column exceeded max_width=300; got {col_widths['notes']:.1f}px"
        )

    def test_width_and_max_width_both_set_raises(self):
        """Setting both width: and max_width: on a column raises ValidationError."""
        import pytest
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig

        with pytest.raises(ValidationError, match="mutually exclusive"):
            TableColumnConfig(width=200, max_width=300)

    def test_no_compact_column_drops_below_demand(self):
        """In a mixed table, no compact column is squished below its measured demand."""
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["status", "revenue", "notes"]
        col_configs = {}
        available_width = 700.0
        # status and revenue are compact; notes is text
        demands = {"status": 100.0, "revenue": 110.0, "notes": 2000.0}

        col_widths, _, actual_w = calculate_column_layout(
            columns, col_configs, available_width, demands=demands
        )
        assert col_widths["status"] >= demands["status"] - 1.0
        assert col_widths["revenue"] >= demands["revenue"] - 1.0
        assert abs(actual_w - available_width) <= 1.0

    def test_max_width_never_exceeded_when_all_text_columns_capped(self):
        """When all text columns are capped and no compact exist, caps must still hold."""
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.render.chart.table_support import calculate_column_layout

        # All 3 columns have demand > 220 (text) and max_width=200
        columns = ["a", "b", "c"]
        col_configs = {c: TableColumnConfig(max_width=200) for c in columns}
        available_width = 1000.0
        demands = {"a": 500.0, "b": 500.0, "c": 500.0}

        col_widths, _, _ = calculate_column_layout(
            columns, col_configs, available_width, demands=demands
        )
        for col in columns:
            assert col_widths[col] <= 200.0 + 1.0, (
                f"Column '{col}' got {col_widths[col]:.1f}px, exceeded max_width=200"
            )

    def test_max_width_never_exceeded_via_cluster_equalization(self):
        """Cluster equalization must not push a capped column above its max_width."""
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.render.chart.table_support import calculate_column_layout

        # "b" has max_width=250; "a" and "c" have large demands (text) and no cap.
        # Without a fix, the cluster pass could snap "b" up to the "a"/"c" cluster max.
        columns = ["a", "b", "c"]
        col_configs = {"b": TableColumnConfig(max_width=250)}
        available_width = 1000.0
        demands = {"a": 600.0, "b": 350.0, "c": 350.0}

        col_widths, _, _ = calculate_column_layout(
            columns, col_configs, available_width, demands=demands
        )
        assert col_widths["b"] <= 250.0 + 1.0, (
            f"Column 'b' with max_width=250 got {col_widths['b']:.1f}px after cluster equalization"
        )

    def test_word_floors_floor_applied_to_text_columns(self):
        """Text column floor is enforced even when proportional allocation falls short.

        Parameters chosen so that after compact columns consume most of the budget,
        the text column's proportional share (text_budget) is less than its word floor.
        The floor must lift the allocation, preventing mid-word wrapping.
        """
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["s1", "s2", "s3", "s4", "notes"]
        col_configs = {}
        available_width = 700.0
        # Four compact columns each at 160px demand = 640px total compact.
        # text_budget = 700 - 640 = 60px, which is below the 150px word floor.
        # The floor should override: notes must get >= 150px.
        demands = {
            "s1": 160.0,
            "s2": 160.0,
            "s3": 160.0,
            "s4": 160.0,
            "notes": 2000.0,
        }
        word_floors = {"notes": 150.0}

        col_widths, _, _ = calculate_column_layout(
            columns,
            col_configs,
            available_width,
            demands=demands,
            word_floors=word_floors,
        )
        assert col_widths["notes"] >= 150.0 - 1.0, (
            f"notes column got {col_widths['notes']:.1f}px, below word floor 150px"
        )

    def test_word_floor_does_not_override_max_width_cap(self):
        """A word_floor wider than max_width does not raise the column above max_width."""
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.render.chart.table_support import calculate_column_layout

        columns = ["a", "notes"]
        col_configs = {"notes": TableColumnConfig(max_width=200)}
        available_width = 900.0
        # notes is text (demand > ceiling) with max_width=200
        # word_floor=300 is intentionally wider than the cap
        demands = {"a": 100.0, "notes": 2000.0}
        word_floors = {"a": 0.0, "notes": 300.0}

        col_widths, _, _ = calculate_column_layout(
            columns,
            col_configs,
            available_width,
            demands=demands,
            word_floors=word_floors,
        )
        assert col_widths["notes"] <= 200.0 + 1.0, (
            f"notes column got {col_widths['notes']:.1f}px; "
            f"word_floor=300 must not override max_width=200"
        )


# ---------------------------------------------------------------------------
# content_headroom — breathing room for compact columns in mixed tables
# ---------------------------------------------------------------------------


class TestContentHeadroom:
    """calculate_column_layout applies content_headroom to compact columns in mixed tables.

    In a mixed table (compact + text columns), headroom inflates each compact
    column's pinned width above its raw p95 demand.  The extra budget comes from
    the text column pool.  In an all-compact table the headroom is intentionally
    not applied — the proportional scale would cancel it exactly, producing zero
    effect while misleadingly reporting a behavioral change.
    """

    def test_mixed_compact_columns_get_headroom_above_demand(self):
        """In a mixed table, compact columns receive demand × (1 + headroom) width.

        Setup: 2 compact columns (160px demand) + 1 huge text column (2000px demand),
        available=900px.  Without headroom: compact pinned at 160px each, text gets
        580px.  With headroom=0.10: compact pinned at 176px each, text gets 548px.
        """
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["owner", "eng_type", "notes"]
        col_configs = {}
        available_width = 900.0

        # owner and eng_type are compact (< 220px ceiling).
        # notes is text (2000px >> 220px).
        demands = {
            "owner": 160.0,
            "eng_type": 160.0,
            "notes": 2000.0,
        }
        headroom = 0.10

        col_widths_with, _, actual_w = calculate_column_layout(
            columns,
            col_configs,
            available_width,
            demands=demands,
            content_headroom=headroom,
        )
        col_widths_without, _, _ = calculate_column_layout(
            columns,
            col_configs,
            available_width,
            demands=demands,
            content_headroom=0.0,
        )

        # Compact columns should be wider with headroom.
        min_expected = 160.0 * (1.0 + headroom)
        for col in ("owner", "eng_type"):
            assert col_widths_with[col] >= min_expected - 1.0, (
                f"Compact column '{col}' got {col_widths_with[col]:.1f}px with headroom; "
                f"expected >= {min_expected:.1f}px"
            )
            assert col_widths_with[col] > col_widths_without[col] - 1.0, (
                f"Headroom should widen compact '{col}': "
                f"got {col_widths_with[col]:.1f}px vs {col_widths_without[col]:.1f}px without"
            )

        # Text column should narrow (it funds the compact headroom).
        assert col_widths_with["notes"] < col_widths_without["notes"] + 1.0, (
            f"Text column should narrow when compact headroom is applied: "
            f"got {col_widths_with['notes']:.1f}px vs {col_widths_without['notes']:.1f}px"
        )

        assert abs(actual_w - available_width) <= 1.0

    def test_all_compact_table_unaffected_by_headroom(self):
        """An all-compact table produces identical widths regardless of headroom.

        In the all-compact path, proportional scaling already fills the budget.
        Applying a headroom factor would cancel exactly in the scale calculation,
        so the implementation intentionally skips headroom for all-compact tables.
        """
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["a", "b", "c"]
        col_configs = {}
        available_width = 600.0
        demands = {"a": 100.0, "b": 120.0, "c": 90.0}

        col_widths_base, _, actual_w_base = calculate_column_layout(
            columns, col_configs, available_width, demands=demands, content_headroom=0.0
        )
        col_widths_hr, _, actual_w_hr = calculate_column_layout(
            columns,
            col_configs,
            available_width,
            demands=demands,
            content_headroom=0.10,
        )

        # All-compact: headroom intentionally has no effect.
        for col in columns:
            assert abs(col_widths_base[col] - col_widths_hr[col]) < 1e-6, (
                f"Column '{col}': expected all-compact to be headroom-agnostic, "
                f"got base={col_widths_base[col]:.4f} vs hr={col_widths_hr[col]:.4f}"
            )
        assert abs(actual_w_base - available_width) <= 1.0

    def test_zero_headroom_leaves_compact_at_demand_in_mixed_table(self):
        """content_headroom=0.0: compact columns stay at their raw demand in mixed tables."""
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["status", "notes"]
        col_configs = {}
        available_width = 800.0
        # status is compact (150px), notes is text (3000px)
        demands = {"status": 150.0, "notes": 3000.0}

        col_widths, _, actual_w = calculate_column_layout(
            columns,
            col_configs,
            available_width,
            demands=demands,
            content_headroom=0.0,
        )

        # With zero headroom, compact pinned exactly at demand.
        assert abs(col_widths["status"] - 150.0) < 1.0, (
            f"Expected status ≈ 150px with zero headroom, got {col_widths['status']:.1f}px"
        )
        assert abs(actual_w - available_width) <= 1.0


# ---------------------------------------------------------------------------
# Cell-first column sizing (header width is secondary / leftover only)
# ---------------------------------------------------------------------------


class TestCellFirstColumnSizing:
    """Column demand is driven by p95 cell content, not header width.

    Long headers should wrap/clip; short cell values should not surrender
    budget to their column's header label.  Only when spare budget remains
    after cell-based allocation do columns grow toward their header widths.
    """

    def test_long_header_short_cells_narrows_column_vs_sibling_with_long_cells(
        self,
    ) -> None:
        """Core regression: long-header + short-cell column must be narrower
        than a sibling text column that genuinely has long cell values,
        given a constrained budget.

        Setup: two columns, tight available_width.
          - 'stage_date_week' header is ~30 chars; cells are ~12 chars ("1 Jun 2026").
          - 'description' header is short; cells are ~50 chars.
        After the fix the date column should be compact/narrow; the
        description column should be the text column that gets the wide budget.
        """
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
            measure_column_demands,
        )

        columns = ["stage_date_week", "description"]
        measurer = get_font_measurer()

        # Long header ("Opportunities Stage 1 Date Week"), short cell values.
        # Short header ("Notes"), long cell values.
        data = [
            {
                "stage_date_week": "1 Jun 2026",
                "description": "A very long description text that spans many characters and wraps",
            },
            {
                "stage_date_week": "8 Jun 2026",
                "description": "Another lengthy description of an opportunity with lots of detail",
            },
            {
                "stage_date_week": "15 Jun 2026",
                "description": "Yet more lengthy description text occupying significant space",
            },
        ]

        import dataclasses

        @dataclasses.dataclass
        class _FakeConfig:
            label: str | None = None

        # Inject long label for the date column via a custom config
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig

        col_configs_typed = {
            "stage_date_week": TableColumnConfig(
                label="Opportunities Stage 1 Date Week"
            ),
            "description": TableColumnConfig(label="Notes"),
        }

        cell_demands, header_demands = measure_column_demands(
            columns,
            col_configs_typed,
            data,
            measurer,
            header_measurer=measurer,
            font_size=13.0,
            header_font_size=12.0,
            cell_pad=8,
            column_when_rules={},
        )

        # Core invariant: cell demand drives allocation, not header width.
        # The description column has longer cell content — it should have
        # a higher cell demand than the date column.
        assert cell_demands["description"] > cell_demands["stage_date_week"], (
            f"description cell demand ({cell_demands['description']:.1f}px) should exceed "
            f"stage_date_week cell demand ({cell_demands['stage_date_week']:.1f}px)"
        )

        # Header demands are tracked separately.
        assert header_demands["stage_date_week"] > header_demands["description"], (
            f"long header 'stage_date_week' header demand ({header_demands['stage_date_week']:.1f}px) "
            f"should exceed short header 'description' header demand ({header_demands['description']:.1f}px)"
        )

        # Layout: with a constrained budget, the date column should be compact/narrow
        # and the description column should be the text column getting wide budget.
        available_width = 600.0
        col_widths, _, actual_w = calculate_column_layout(
            columns,
            col_configs_typed,
            available_width,
            demands=cell_demands,
            header_demands=header_demands,
        )

        assert col_widths["description"] > col_widths["stage_date_week"], (
            f"description ({col_widths['description']:.1f}px) should be wider than "
            f"stage_date_week ({col_widths['stage_date_week']:.1f}px): "
            "long header should not drive demand"
        )
        assert abs(actual_w - available_width) <= 1.0

    def test_generous_budget_grows_columns_toward_header_width(self) -> None:
        """When available_width is generous, columns grow up toward their header
        width from the spare budget after cell-based allocation.

        Setup: one compact column (short cells) with a long header.
        Budget is generous (3x the cell demand). The column should
        be wider than its cell demand alone (header demand applied from leftover).
        """
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        measurer = get_font_measurer()
        # Measure the header and a short cell value.
        long_header_label = "Opportunities Stage 1 Date Week"
        short_cell = "1 Jun 2026"
        header_w = measurer.measure(long_header_label, 12.0)
        cell_w = measurer.measure(short_cell, 13.0)
        cell_pad = 8

        # Synthesize demands/header_demands directly so the test does not depend
        # on measure_column_demands internals.
        cell_demands = {"date_col": cell_w + cell_pad * 2}
        header_demands = {"date_col": header_w}

        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig

        col_configs = {"date_col": TableColumnConfig()}

        # Generous budget: well above the cell demand and the header demand.
        generous_budget = (header_w + cell_pad * 2) * 3.0

        col_widths, _, actual_w = calculate_column_layout(
            ["date_col"],
            col_configs,
            generous_budget,
            demands=cell_demands,
            header_demands=header_demands,
        )
        # Single auto-column gets the full budget.
        assert abs(col_widths["date_col"] - generous_budget) <= 1.0
        assert abs(actual_w - generous_budget) <= 1.0

    def test_tight_budget_cell_content_wins_over_header(self) -> None:
        """Under tight budget, p95 cell content is preserved; header wraps/clips.

        A column with 80px of cell content and a 200px header should still
        receive enough width for the cell content when budget is tight.
        A sibling with 80px cell and 80px header should not be favored over it.
        Both columns' cells should have their content width allocated;
        headers may wrap or clip.
        """
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.render.chart.table_support import calculate_column_layout

        cell_pad = 8
        cell_demand_a = 80.0 + cell_pad * 2  # col_a: short cells, long header
        cell_demand_b = 80.0 + cell_pad * 2  # col_b: short cells, short header
        header_demand_a = 200.0  # long header on col_a
        header_demand_b = 80.0  # short header on col_b

        # Budget just enough for both cell contents.
        available_width = (cell_demand_a + cell_demand_b) * 1.2

        col_configs = {"col_a": TableColumnConfig(), "col_b": TableColumnConfig()}
        cell_demands = {"col_a": cell_demand_a, "col_b": cell_demand_b}
        header_demands = {"col_a": header_demand_a, "col_b": header_demand_b}

        col_widths, _, actual_w = calculate_column_layout(
            ["col_a", "col_b"],
            col_configs,
            available_width,
            demands=cell_demands,
            header_demands=header_demands,
        )

        # Both columns should receive at least their cell content width.
        assert col_widths["col_a"] >= cell_demand_a - 1.0, (
            f"col_a got {col_widths['col_a']:.1f}px, below cell demand {cell_demand_a:.1f}px"
        )
        assert col_widths["col_b"] >= cell_demand_b - 1.0, (
            f"col_b got {col_widths['col_b']:.1f}px, below cell demand {cell_demand_b:.1f}px"
        )
        assert abs(actual_w - available_width) <= 1.0

    def test_measure_column_demands_returns_tuple(self) -> None:
        """measure_column_demands returns (cell_demands, header_demands) tuple."""
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table_support import (
            measure_column_demands,
        )

        columns = ["name", "score"]
        col_configs = {}
        data = [{"name": "Alice", "score": 1}, {"name": "Bob", "score": 2}]
        measurer = get_font_measurer()

        result = measure_column_demands(
            columns,
            col_configs,
            data,
            measurer,
            header_measurer=measurer,
            font_size=13.0,
            header_font_size=12.0,
            cell_pad=8,
            formats={"number": ".3~s"},
            column_when_rules={},
        )

        # Must return a 2-tuple of dicts.
        assert isinstance(result, tuple) and len(result) == 2
        cell_demands, header_demands = result
        assert set(cell_demands) == {"name", "score"}
        assert set(header_demands) == {"name", "score"}

    def test_cell_demand_excludes_header_width(self) -> None:
        """Cell demand must NOT include the header width.

        A column with empty data (p95=0) should have demand = cell_pad*2
        regardless of how long the header is.
        """
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table_support import (
            measure_column_demands,
        )

        measurer = get_font_measurer()
        cell_pad = 8
        # Very long header label, no data at all.
        col_configs = {
            "short": TableColumnConfig(label="X"),
            "long_header": TableColumnConfig(label="Opportunities Stage 1 Date Week"),
        }

        cell_demands, header_demands = measure_column_demands(
            ["short", "long_header"],
            col_configs,
            [],  # empty data
            measurer,
            header_measurer=measurer,
            font_size=13.0,
            header_font_size=12.0,
            cell_pad=cell_pad,
            column_when_rules={},
        )

        # With no data, both cell demands should be identical (just padding).
        assert abs(cell_demands["short"] - cell_demands["long_header"]) <= 0.5, (
            f"Cell demands should be equal for empty data: "
            f"short={cell_demands['short']:.1f}, "
            f"long_header={cell_demands['long_header']:.1f}"
        )
        # Header demands should differ.
        assert header_demands["long_header"] > header_demands["short"], (
            f"Long header demand ({header_demands['long_header']:.1f}) should exceed "
            f"short header demand ({header_demands['short']:.1f})"
        )
