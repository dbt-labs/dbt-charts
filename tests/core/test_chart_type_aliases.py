"""Tests for chart type validation.

'point' and 'choropleth' were formerly silent aliases (rewritten to 'scatter'
and 'map' respectively).  They are now rejected like any other unknown type.
Canonical names ('scatter', 'map') continue to work.
"""

import pytest

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart


def _quick_registry():
    """Return a minimal query registry for normalization tests."""
    return {"q": SqlQuery(sql="SELECT 1", source="test")}


class TestPointRaisesError:
    """'point' is not a valid chart type — it must raise CompilationError."""

    def test_point_raises_compilation_error(self):
        registry = _quick_registry()
        with pytest.raises(CompilationError, match="unknown type 'point'"):
            normalize_chart("c1", {"type": "point", "query": "q"}, registry, sources={})

    def test_scatter_stays_scatter(self):
        registry = _quick_registry()
        chart = normalize_chart(
            "c1", {"type": "scatter", "query": "q"}, registry, sources={}
        )
        assert chart.type == "scatter"


class TestChoroplethRaisesError:
    """'choropleth' is not a valid chart type — it must raise CompilationError."""

    def test_choropleth_raises_compilation_error(self):
        registry = _quick_registry()
        with pytest.raises(CompilationError, match="unknown type 'choropleth'"):
            normalize_chart(
                "c1", {"type": "choropleth", "query": "q"}, registry, sources={}
            )

    def test_map_stays_map(self):
        registry = _quick_registry()
        chart = normalize_chart(
            "c1", {"type": "map", "query": "q"}, registry, sources={}
        )
        assert chart.type == "map"


class TestChartStylePatchMarksRejected:
    """ChartStylePatch has no root-level marks — authors must use style.<type>.marks.*."""

    def test_marks_at_root_rejected_by_chart_style_patch(self):
        """style.marks at root level raises a validation error (no such field).

        For bar/line/area/scatter the normalizer lifts marks into the family
        section before ChartStylePatch sees the dict. For types outside that
        normalizer (pie, kpi, table, layered) style.marks would have been an
        opaque no-op; now it fails loud.
        """
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.authored import ChartStylePatch

        with pytest.raises(ValidationError):
            ChartStylePatch.model_validate({"marks": {"bar": {"size": 20}}})


class TestProjectionsConfigRemoved:
    """Verify the projections section is no longer in config."""

    def test_no_projections_in_compiled_config(self):
        from dbt_charts.core.compile.config import get_config

        config = get_config()
        assert not hasattr(config, "projections")
