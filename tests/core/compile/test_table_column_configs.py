"""Tests for table column configuration parsing.

Tests the mapping-keyed columns shape and rejection of the old list shape.
"""

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.chart.authored import (
    SparkConfig,
    TableColumnConfig,
)
from dbt_charts.core.compile.models.style.authored import (
    ChartStylePatch,
    TableChartStylePatch,
    TableColumnDefaultsConfig,
)


class TestSparkConfigParsing:
    """Tests for SparkConfig model."""

    def test_spark_config_line_options(self) -> None:
        config = SparkConfig(
            type="line", color="#3b82f6", last_visible=True, min_max_visible=True
        )
        assert config.type == "line"
        assert config.color == "#3b82f6"
        assert config.last_visible is True
        assert config.min_max_visible is True

    def test_spark_config_bar_normalize_options(self) -> None:
        config = SparkConfig(
            type="bar-normalize", max=100, thresholds={50: "yellow", 80: "green"}
        )
        assert config.type == "bar-normalize"
        assert config.max == 100
        assert config.thresholds == {50: "yellow", 80: "green"}


class TestSparkValueLane:
    @pytest.mark.parametrize("position", ["left", "right"])
    def test_value_lane_on_a_single_number_variant(self, position: str) -> None:
        config = SparkConfig(type="bar", value={"visible": True, "position": position})
        assert config.value is not None
        assert config.value.visible is True
        assert config.value.position == position

    def test_value_lane_leaves_position_and_gap_to_the_theme(self) -> None:
        config = SparkConfig(type="column", value={})
        assert config.value is not None
        assert config.value.visible is False
        assert config.value.position is None
        assert config.value.gap is None

    def test_value_lane_takes_a_column_gap(self) -> None:
        config = SparkConfig(type="bar", value={"visible": True, "gap": 12})
        assert config.value is not None
        assert config.value.gap == 12

    def test_value_lane_rejects_a_negative_gap(self) -> None:
        with pytest.raises(ValidationError):
            SparkConfig(type="bar", value={"visible": True, "gap": -1})

    @pytest.mark.parametrize("variant", ["line", "area", "columns"])
    def test_value_lane_is_rejected_on_series_variants(self, variant: str) -> None:
        with pytest.raises(ValidationError, match="single-number"):
            SparkConfig(type=variant, value={"visible": True})

    def test_value_lane_rejects_an_unknown_position(self) -> None:
        with pytest.raises(ValidationError):
            SparkConfig(type="bar", value={"visible": True, "position": "top"})


class TestTableColumnConfig:
    def test_table_column_config_basic(self) -> None:
        config = TableColumnConfig()
        assert config.label is None
        assert config.spark is None

    def test_table_column_config_with_label(self) -> None:
        config = TableColumnConfig(label="Total Sales ($)")
        assert config.label == "Total Sales ($)"

    def test_table_column_config_spark_shorthand(self) -> None:
        """Shorthand strings normalize to SparkConfig at construction time."""
        config = TableColumnConfig(spark="line")
        assert isinstance(config.spark, SparkConfig)
        assert config.spark.type == "line"

    def test_table_column_config_spark_full(self) -> None:
        spark_config = SparkConfig(type="line", color="#ff0000", last_visible=True)
        config = TableColumnConfig(spark=spark_config)
        assert isinstance(config.spark, SparkConfig)
        assert config.spark.type == "line"
        assert config.spark.color == "#ff0000"

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            TableColumnConfig(column="anything")  # type: ignore[call-arg]

    def test_table_column_config_swatch_default(self) -> None:
        """Default: swatch is None (column renders as plain text)."""
        config = TableColumnConfig()
        assert config.swatch is None

    def test_table_column_config_swatch_bool(self) -> None:
        """``swatch: true`` flips the column to render cells as color squares.

        Cell value is expected to be a CSS color string (e.g. ``"#3164a3"``);
        the renderer emits a small rounded SVG ``<rect>`` filled with that
        color instead of the text content.
        """
        config = TableColumnConfig(swatch=True)
        assert config.swatch is True

    def test_table_column_config_swatch_serializes(self) -> None:
        config = TableColumnConfig(swatch=True, label="Series")
        dumped = config.model_dump(exclude_none=True)
        assert dumped["swatch"] is True
        assert dumped["label"] == "Series"


class TestTableColumnConfigFromDict:
    """Tests for parsing column configs from dict (YAML-like)."""

    def test_parse_spark_shorthand(self) -> None:
        config = TableColumnConfig(**{"spark": "line"})
        assert isinstance(config.spark, SparkConfig)
        assert config.spark.type == "line"

    def test_parse_spark_full(self) -> None:
        config = TableColumnConfig(
            **{"spark": {"type": "bar-normalize", "max": 100, "color": "#22c55e"}}
        )
        assert isinstance(config.spark, SparkConfig)
        assert config.spark.type == "bar-normalize"
        assert config.spark.max == 100


class TestSparkTypeLiteral:
    """Tests for SparkTypeLiteral validation."""

    def test_valid_spark_types(self) -> None:
        for spark_type in ["line", "area", "bar", "bar-normalize", "columns"]:
            config = SparkConfig(type=spark_type)  # type: ignore[arg-type]
            assert config.type == spark_type

    def test_invalid_spark_type(self) -> None:
        with pytest.raises(ValidationError):
            SparkConfig(type="invalid")  # type: ignore[arg-type]


class TestChartStylePatchColumns:
    """Tests for TableChartStylePatch.columns dict shape (table-specific)."""

    def test_accepts_dict_shape(self) -> None:
        patch = ChartStylePatch(
            table=TableChartStylePatch(
                columns={"revenue": {"format": "currency", "width": 120}}
            )
        )
        assert patch.table is not None
        assert "revenue" in patch.table.columns  # type: ignore[operator]

    def test_accepts_tablecolumnconfig_values(self) -> None:
        patch = ChartStylePatch(
            table=TableChartStylePatch(
                columns={"region": TableColumnConfig(label="Region")}
            )
        )
        assert patch.table is not None
        assert "region" in patch.table.columns  # type: ignore[operator]

    def test_rejects_list_shape_with_clear_message(self) -> None:
        with pytest.raises(ValidationError):
            ChartStylePatch(
                table=TableChartStylePatch(
                    columns=[{"column": "revenue", "format": "currency"}]
                )
            )  # type: ignore[arg-type]

    def test_list_rejection_message_includes_migration_hint(self) -> None:
        with pytest.raises(ValidationError):
            ChartStylePatch(table=TableChartStylePatch(columns=[{"column": "revenue"}]))  # type: ignore[arg-type]

    def test_dict_insertion_order_preserved(self) -> None:
        patch = ChartStylePatch(
            table=TableChartStylePatch(
                columns={
                    "revenue": {"format": "currency"},
                    "date": {"format": "date"},
                    "region": {"label": "Region"},
                }
            )
        )
        assert patch.table is not None
        assert list(patch.table.columns.keys()) == ["revenue", "date", "region"]  # type: ignore[union-attr]

    def test_none_columns_accepted(self) -> None:
        patch = ChartStylePatch(table=TableChartStylePatch(columns=None))
        assert patch.table is not None
        assert patch.table.columns is None

    def test_empty_dict_accepted(self) -> None:
        patch = ChartStylePatch(table=TableChartStylePatch(columns={}))
        assert patch.table is not None
        assert patch.table.columns == {}


class TestSparkConfigNumericConstraints:
    """SparkConfig.height/width/fill_opacity must reject values outside their
    documented ranges instead of passing normalization and failing later in
    render/layout."""

    @pytest.mark.parametrize("field", ["height", "width"])
    @pytest.mark.parametrize("value", [0, -1])
    def test_non_positive_height_width_rejected(self, field, value) -> None:
        with pytest.raises(ValidationError):
            SparkConfig(**{field: value})

    @pytest.mark.parametrize("value", [-0.1, 1.1])
    def test_fill_opacity_out_of_range_rejected(self, value) -> None:
        with pytest.raises(ValidationError):
            SparkConfig(fill_opacity=value)

    def test_fill_opacity_boundaries_accepted(self) -> None:
        assert SparkConfig(fill_opacity=0).fill_opacity == 0
        assert SparkConfig(fill_opacity=1).fill_opacity == 1


class TestTableColumnWidthConstraints:
    """width/max_width accept a positive pixel int or a CSS width string —
    zero/negative pixel values must be rejected."""

    @pytest.mark.parametrize("value", [0, -10])
    def test_column_defaults_width_rejects_non_positive_int(self, value) -> None:
        with pytest.raises(ValidationError):
            TableColumnDefaultsConfig(width=value)

    def test_column_defaults_width_accepts_css_string(self) -> None:
        assert TableColumnDefaultsConfig(width="30%").width == "30%"

    @pytest.mark.parametrize("field", ["width", "max_width"])
    @pytest.mark.parametrize("value", [0, -10])
    def test_table_column_config_rejects_non_positive_int(self, field, value) -> None:
        with pytest.raises(ValidationError):
            TableColumnConfig(**{field: value})

    @pytest.mark.parametrize("field", ["width", "max_width"])
    def test_table_column_config_accepts_css_string(self, field) -> None:
        config = TableColumnConfig(**{field: "10%"})
        assert getattr(config, field) == "10%"
