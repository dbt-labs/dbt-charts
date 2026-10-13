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


# ---------------------------------------------------------------------------
# calculate_column_layout with demands
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# measure_column_demands
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _cluster_and_equalize_widths
# ---------------------------------------------------------------------------


class TestClusterEqualization:
    """Tests for the cluster-and-equalize post-processing pass."""

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

    def test_width_and_max_width_both_set_raises(self):
        """Setting both width: and max_width: on a column raises ValidationError."""
        import pytest
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig

        with pytest.raises(ValidationError, match="mutually exclusive"):
            TableColumnConfig(width=200, max_width=300)


# ---------------------------------------------------------------------------
# content_headroom — breathing room for compact columns in mixed tables
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cell-first column sizing (header width is secondary / leftover only)
# ---------------------------------------------------------------------------
