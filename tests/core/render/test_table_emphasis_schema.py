"""Tests for ScaleTargetConfig schema extensions: palette string/list, hinge, arm_mode.

Covers piece 1B.6a (schema extensions only). Resolution helper tests are in
test_table_emphasis_resolution.py.
"""

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.chart.authored import (
    ScaleTargetConfig,
)


class TestPaletteField:
    """palette accepts named string OR inline hex list."""

    def test_inline_list_unchanged(self) -> None:
        cfg = ScaleTargetConfig(palette=["#ff0000", "#ffffff", "#0000ff"])
        assert cfg.palette == ["#ff0000", "#ffffff", "#0000ff"]

    def test_named_string_palette(self) -> None:
        cfg = ScaleTargetConfig(palette="dbt-seq-blue")
        assert cfg.palette == "dbt-seq-blue"

    def test_named_palette_with_modifier(self) -> None:
        # Shorthand like "dbt-seq-blue:5" or "dbt-seq-blue_r" must also pass through.
        cfg = ScaleTargetConfig(palette="dbt-seq-blue:5")
        assert cfg.palette == "dbt-seq-blue:5"

    def test_named_palette_reversed(self) -> None:
        cfg = ScaleTargetConfig(palette="dbt-div-blue-red_r")
        assert cfg.palette == "dbt-div-blue-red_r"


class TestHingeField:
    """hinge defaults to None (sequential), accepts 'auto' or explicit float."""

    def test_default_none_sequential(self) -> None:
        cfg = ScaleTargetConfig(palette=["#000", "#fff"])
        assert cfg.hinge is None

    def test_auto_string(self) -> None:
        cfg = ScaleTargetConfig(palette=["#000", "#fff"], hinge="auto")
        assert cfg.hinge == "auto"

    def test_explicit_float(self) -> None:
        cfg = ScaleTargetConfig(palette=["#ff0000", "#ffffff", "#0000ff"], hinge=0.0)
        assert cfg.hinge == 0.0

    def test_explicit_positive_float(self) -> None:
        cfg = ScaleTargetConfig(palette=["#ff0000", "#ffffff", "#0000ff"], hinge=1.0)
        assert cfg.hinge == 1.0

    def test_explicit_negative_float(self) -> None:
        cfg = ScaleTargetConfig(palette=["#ff0000", "#ffffff", "#0000ff"], hinge=-5.0)
        assert cfg.hinge == -5.0

    def test_integer_coerces_to_float(self) -> None:
        # Pydantic coerces int to float for float fields.
        cfg = ScaleTargetConfig(palette=["#000", "#fff"], hinge=0)
        assert cfg.hinge == 0.0

    def test_invalid_string_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScaleTargetConfig.model_validate(
                {"palette": ["#000", "#fff"], "hinge": "center"}
            )


class TestArmModeField:
    """arm_mode defaults to 'asymmetric' and only accepts 'asymmetric' | 'symmetric'."""

    def test_default_asymmetric(self) -> None:
        cfg = ScaleTargetConfig(palette=["#000", "#fff"])
        assert cfg.arm_mode == "asymmetric"

    def test_symmetric_accepted(self) -> None:
        cfg = ScaleTargetConfig(
            palette=["#ff0000", "#ffffff", "#0000ff"],
            hinge="auto",
            arm_mode="symmetric",
        )
        assert cfg.arm_mode == "symmetric"

    def test_invalid_arm_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScaleTargetConfig.model_validate(
                {"palette": ["#000", "#fff"], "arm_mode": "balanced"}
            )


class TestBackwardCompatibility:
    """Existing configs without hinge/arm_mode parse without changes."""

    def test_existing_config_unchanged(self) -> None:
        cfg = ScaleTargetConfig(
            palette=["#fff7fb", "#023858"],
            domain=[0.0, 100.0],
            null_color="#cccccc",
        )
        assert cfg.hinge is None
        assert cfg.arm_mode == "asymmetric"
        assert cfg.palette == ["#fff7fb", "#023858"]
        assert cfg.domain is not None
        assert cfg.domain.min == 0.0
        assert cfg.domain.max == 100.0
        assert cfg.null_color == "#cccccc"

    def test_dict_round_trip_no_hinge(self) -> None:
        raw = {"palette": ["#ff0000", "#ffffff"]}
        cfg = ScaleTargetConfig.model_validate(raw)
        assert cfg.hinge is None
        assert cfg.arm_mode == "asymmetric"
