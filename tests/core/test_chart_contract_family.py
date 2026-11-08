"""Tests for the chart contract family: AuthoredChart → Chart → ResolvedChart.

Validates that:
- Per-family patches are sparse and ergonomic for YAML authoring
- Chart is the complete normalized contract
- type: is mandatory; missing or unknown type raises ValidationError
- AuthoredChart union dispatches to the correct family
- AuthoredChart normalizes into Chart correctly
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.chart.authored import (
    BarChart,
    KpiChart,
    LineChart,
    PieChart,
    _BaseChartFields,
    _SharedChartFields,
)
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart as _NBarChart,
    Chart,
    GeoshapeChart,
    PieChart as _NPieChart,
)

# ============================================================================
# AuthoredChart — structural enforcement
# ============================================================================


class TestAuthoredChartStructure:
    """Per-family patches enforce structure via extra='forbid'."""

    def test_vl_passthrough_fields_rejected_on_bar(self):
        """VL escape-hatch fields are rejected by extra='forbid' on BarChart."""
        for name in (
            "spec",
            "config",
            "transform",
            "params",
            "resolve",
            "hconcat",
            "vconcat",
            "concat",
            "repeat",
        ):
            with pytest.raises(ValidationError):
                BarChart(type="bar", **{name: {"foo": "bar"}})

    def test_dead_data_transform_fields_raise_validation_error(self):
        """group_by and limit are not accepted (extra=forbid)."""
        with pytest.raises(ValidationError):
            BarChart(type="bar", group_by="category")  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            BarChart(type="bar", limit=10)  # type: ignore[call-arg]


# ============================================================================
# AuthoredChart union — dispatch
# ============================================================================


class TestAuthoredChart:
    """AuthoredChart is a discriminated union that dispatches to per-family patches."""

    def test_basic_yaml_input_via_line(self):
        """LineChart accepts typical YAML chart input."""
        patch = LineChart(
            query="sales_by_date",
            type="line",
            title="Revenue Trend",
            x="date",
            y="amount",
            color="category",
        )
        assert patch.type == "line"
        assert patch.x == "date"
        assert patch.y == "amount"

    def test_style_accepts_bar_chart_style_patch(self):
        """BarChart.style accepts BarChartStylePatch (per-family, not monolithic)."""
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        style = BarChartStylePatch()
        patch = BarChart(type="bar", style=style)
        assert patch.style is not None

    def test_kpi_value_may_be_left_to_bind_by_position(self):
        """A KPI naming no value binds the query's one column at render."""
        assert KpiChart(type="kpi").value is None

    def test_pie_forbids_extra_cartesian_fields(self):
        """PieChart rejects extra cartesian fields via extra='forbid'."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PieChart(type="pie", theta="amount", x="category")  # type: ignore[call-arg]

    @pytest.mark.parametrize(
        "chart_type",
        ["bar", "line", "area", "scatter", "heatmap", "histogram"],
    )
    def test_inner_radius_forbidden_on_cartesian_types(self, chart_type):
        """inner_radius is not declared on cartesian patches — extra='forbid' rejects it."""
        from pydantic import ValidationError

        patch_cls = {
            "bar": BarChart,
            "histogram": BarChart,
            "line": LineChart,
            "area": __import__(
                "dbt_charts.core.compile.models.chart.authored", fromlist=["AreaChart"]
            ).AreaChart,
            "scatter": __import__(
                "dbt_charts.core.compile.models.chart.authored",
                fromlist=["ScatterChart"],
            ).ScatterChart,
            "heatmap": __import__(
                "dbt_charts.core.compile.models.chart.authored",
                fromlist=["HeatmapChart"],
            ).HeatmapChart,
        }[chart_type]
        with pytest.raises(ValidationError):
            patch_cls(type=chart_type, inner_radius=0.5)  # type: ignore[call-arg]

    def test_inner_radius_in_style_allowed_on_pie(self):
        """Pie charts accept style.inner_radius directly on PieChartStylePatch."""
        patch = PieChart(type="pie", theta="amount", style={"inner_radius": 0.5})
        assert patch.style is not None
        assert patch.style.inner_radius == pytest.approx(0.5)

    def test_inner_radius_in_style_allowed_on_donut(self):
        """Donut is a valid alias for pie — style.inner_radius must be accepted."""
        patch = PieChart(type="donut", theta="amount", style={"inner_radius": 0.3})
        assert patch.style is not None
        assert patch.style.inner_radius == pytest.approx(0.3)

    def test_donut_rejects_extra_cartesian_fields(self):
        """Donut uses PieChart — extra cartesian fields rejected by extra='forbid'."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PieChart(type="donut", theta="amount", x="category")  # type: ignore[call-arg]

    @pytest.mark.parametrize("bad_value", [-0.1, 1.5, 2.0])
    def test_inner_radius_rejects_out_of_range(self, bad_value):
        """inner_radius must be between 0 and 1."""
        with pytest.raises(ValueError, match="inner_radius"):
            PieChart(type="pie", theta="amount", inner_radius=bad_value)  # type: ignore[call-arg]

    def test_bar_rejects_theta_as_extra(self) -> None:
        """theta is not declared on BarChart — extra='forbid' rejects it."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BarChart(type="bar", x="col", y="val", theta="slice_value")  # type: ignore[call-arg]

    def test_pie_rejects_sort_as_extra(self) -> None:
        """PieChart has no sort field — extra='forbid' rejects it."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PieChart(
                type="pie",
                theta="amount",
                sort={"by": "value", "order": "desc"},  # type: ignore[call-arg]
            )


# ============================================================================
# Chart — all fields declared directly
# ============================================================================


class TestChartFields:
    """Chart declares data-mapping fields directly (no shared base with authored)."""

    def test_shared_fields_accessible(self):
        """Data-mapping fields are directly accessible on Chart."""
        chart = _NBarChart(id="test", type="bar")
        assert chart.x is None
        assert chart.y is None
        assert chart.color is None

    def test_compiled_specific_fields(self):
        """Chart has its own compiled-specific fields."""
        chart = _NBarChart(id="test", type="bar", title="Test")
        assert chart.id == "test"
        assert chart.title == "Test"
        assert chart.variable_dependencies == set()

    def test_chart_is_base_model(self):
        """Chart is a BaseModel, not inheriting from authored patches."""
        from pydantic import BaseModel

        assert issubclass(_NBarChart, BaseModel)


# ============================================================================
# _SharedChartFields — isinstance base
# ============================================================================


class TestSharedChartFieldsBase:
    """_BaseChartFields is the isinstance base for all chart patches."""

    def test_bar_patch_is_base_chart_fields(self):
        p = BarChart(type="bar", x="month", y="revenue")
        assert isinstance(p, _BaseChartFields)
        assert isinstance(p, _SharedChartFields)

    def test_kpi_patch_is_base_chart_fields(self):
        p = KpiChart(type="kpi", value="revenue")
        assert isinstance(p, _BaseChartFields)
        # KpiChart inherits from _BaseChartFields directly, not _SharedChartFields
        assert not isinstance(p, _SharedChartFields)

    def test_pie_patch_is_base_chart_fields(self):
        p = PieChart(type="pie", theta="amount")
        assert isinstance(p, _BaseChartFields)
        assert isinstance(p, _SharedChartFields)


# ============================================================================
# Contract family relationship
# ============================================================================


class TestContractFamily:
    """The three contracts form a coherent family."""

    def test_chart_patch_normalizes_to_compiled_chart(self):
        """A AuthoredChart dict can be expanded into Chart fields."""
        patch = LineChart(
            type="line",
            x="date",
            y="amount",
            color="region",
        )
        # Simulate normalization: dump patch fields, add compiled-specific fields
        patch_data = patch.model_dump(exclude_none=True)
        compiled_data = {
            **patch_data,
            "id": "revenue",
            "title": "Revenue",
            "type": patch_data.get("type", "auto"),
        }
        # Style needs to be removed or converted before dict emission.
        compiled_data.pop("style", None)
        chart = TypeAdapter(Chart).validate_python(dict(**compiled_data))
        assert chart.id == "revenue"
        assert chart.x == "date"
        assert chart.y == "amount"

    def test_field_overlap_between_patch_families_and_chart(self):
        """Per-family patches and Chart share data-mapping field names."""
        from dbt_charts.core.compile.models.chart.authored import (
            BarChart as _ABarChart,
            PieChart as _APieChart,
        )

        bar_fields = set(_ABarChart.model_fields.keys())
        pie_fields = set(_APieChart.model_fields.keys())
        compiled_fields = set(_NBarChart.model_fields.keys())
        # Cartesian fields exist on bar and in compiled Chart
        for name in ("x", "y", "color"):
            assert name in bar_fields, f"BarChart missing {name}"
            assert name in compiled_fields, f"Chart missing {name}"
        # Radial fields exist on pie and in compiled Chart
        assert "theta" in pie_fields
        assert "theta" in _NPieChart.model_fields


# ============================================================================
# Chart.to_dict() — projection round-tripping
# ============================================================================


class TestChartToDictProjection:
    """to_dict() correctly serializes projection (string and model forms)."""

    def test_to_dict_projection_string_preserved(self):
        chart = GeoshapeChart(
            id="test",
            type="map",
            projection="mercator",
        )
        d = chart.model_dump()
        assert d["projection"] == "mercator"

    def test_to_dict_projection_model_serialized(self):
        from dbt_charts.core.compile.models.vega_lite.contracts import Projection

        chart = GeoshapeChart(
            id="test",
            type="map",
            projection={"type": "albersUsa"},
        )
        assert isinstance(chart.projection, Projection)
        d = chart.model_dump()
        assert isinstance(d["projection"], dict)
        assert d["projection"]["type"] == "albersUsa"

    def test_to_dict_includes_theta(self):
        """Pie chart theta must round-trip through to_dict."""
        chart = _NPieChart(
            id="test",
            type="pie",
            theta="amount",
            color="category",
        )
        d = chart.model_dump()
        assert d["theta"] == "amount"
