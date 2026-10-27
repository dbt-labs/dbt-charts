"""Tests for chart enrichment behavior."""

import pytest

from dbt_charts.core.compile.resolve.chart.enrich import (
    ColumnProfile,
    _pick_scale,
)

# Chart types where zero anchoring is optional (smart-auto heuristic applies).
_OPTIONAL_ZERO_TYPES = ("line", "scatter")


class TestSmartAutoZero:
    """Smart auto-zero heuristic: extend to 0 when data_min/data_max <= 0.25.

    Only applies to chart types where zero is optional (line, scatter).
    """

    @pytest.mark.parametrize(
        ("data_min", "data_max", "expected_zero"),
        [
            # ratio = 0.25 (at threshold) → extend to 0 (zero: None, let VL default)
            (120, 480, None),
            # ratio ≈ 0.0125 (clearly close to 0) → extend to 0
            (50, 4000, None),
            # ratio ≈ 0.41 (above threshold) → fit-to-data (zero: False)
            (75000, 183000, False),
            # ratio ≈ 0.89 (very tight) → fit-to-data (zero: False)
            (7300, 8200, False),
            # ratio ≈ 0.71 (above threshold) → fit-to-data (zero: False)
            (0.65, 0.92, False),
        ],
    )
    def test_line_chart_auto_zero_heuristic(
        self, data_min: float, data_max: float, expected_zero: bool | None
    ) -> None:
        """Line chart with auto zero uses ratio threshold to decide domain."""
        profile = ColumnProfile(min_val=data_min, max_val=data_max)
        result = _pick_scale(profile, chart_type="line")
        if expected_zero is None:
            # extend to zero: either no override or zero=True
            assert result is None or result.get("zero") is not False
        else:
            assert result is not None
            assert result.get("zero") is False

    @pytest.mark.parametrize("chart_type", _OPTIONAL_ZERO_TYPES)
    def test_optional_zero_chart_types_apply_heuristic(self, chart_type: str) -> None:
        """Heuristic applies consistently to all optional-zero chart types."""
        # (75k, 183k) ratio=0.41 — should be fit-to-data on optional-zero types
        profile = ColumnProfile(min_val=75000, max_val=183000)
        result = _pick_scale(profile, chart_type=chart_type)
        assert result is not None
        assert result.get("zero") is False

    # ── all-negative data mirrors all-positive ───────────────────────────────
    # All-negative data takes the same branches as all-positive: "zero already
    # in domain" holds for data spanning zero, but not for data entirely below
    # it, where an unanchored area measures its fill from the plot floor
    # instead of from 0 — the truncation the magnitude rationale rejects.

    @pytest.mark.parametrize("chart_type", ["bar", "area", "line", "scatter"])
    @pytest.mark.parametrize("extent", [(0.0, 100.0), (-100.0, 0.0)])
    def test_data_touching_zero_needs_no_opinion(
        self, chart_type: str, extent: tuple[float, float]
    ) -> None:
        """Zero is already in the domain, so there is nothing to extend.
        Returning a pin here would suppress Vega-Lite's nice-rounding on a
        ladderless theme, moving the top edge of every ordinary count series
        that happens to have a zero bucket."""
        profile = ColumnProfile(min_val=extent[0], max_val=extent[1])
        assert _pick_scale(profile, chart_type=chart_type) is None

    @pytest.mark.parametrize("chart_type", ["bar", "area"])
    def test_non_optional_zero_types_extend_to_zero_when_all_negative(
        self, chart_type: str
    ) -> None:
        """A magnitude encoding is truncated by a floor at -104 exactly as it
        is by one at +76. Both signs extend."""
        profile = ColumnProfile(min_val=-103.0, max_val=-90.0)
        result = _pick_scale(profile, chart_type=chart_type)
        assert result is not None
        assert result.get("zero") is True

    @pytest.mark.parametrize("chart_type", _OPTIONAL_ZERO_TYPES)
    def test_optional_zero_types_fit_all_negative_data_far_from_zero(
        self, chart_type: str
    ) -> None:
        """Mirror of the positive ratio branch: -90/-103 leaves the near edge
        87% of the way from 0, so fit the data and bake the explicit False that
        keeps the baseline rule off it."""
        profile = ColumnProfile(min_val=-103.0, max_val=-90.0)
        result = _pick_scale(profile, chart_type=chart_type)
        assert result is not None
        assert result.get("zero") is False

    @pytest.mark.parametrize("chart_type", _OPTIONAL_ZERO_TYPES)
    def test_optional_zero_types_extend_all_negative_data_near_zero(
        self, chart_type: str
    ) -> None:
        """Near edge within the bottom quarter of [min, 0]: extending adds
        context without crushing the data, same as the positive branch."""
        profile = ColumnProfile(min_val=-100.0, max_val=-5.0)
        result = _pick_scale(profile, chart_type=chart_type)
        assert result is None or result.get("zero") is not False

    def test_data_spanning_zero_still_needs_no_override(self) -> None:
        """The half of the old guard that was right: zero is already in the
        domain, so nothing is pinned either way."""
        assert (
            _pick_scale(ColumnProfile(min_val=-50.0, max_val=50.0), chart_type="area")
            is None
        )

    @pytest.mark.parametrize("chart_type", ["bar", "area"])
    def test_non_optional_zero_types_skip_the_ratio_branch_for_mid_range(
        self, chart_type: str
    ) -> None:
        """Bar and area both skip the optional-zero branch for mid-range data
        (ratio=0.41): both fall through to the non-optional-zero path (always
        zero=True for all-positive data) — a bar's length and an area's fill
        are both absolute-magnitude encodings, and truncating either misleads.
        """
        # (75k, 183k) ratio=0.41 — not optional-zero, so always zero:True.
        profile = ColumnProfile(min_val=75000, max_val=183000)
        result = _pick_scale(profile, chart_type=chart_type)
        assert result is not None
        assert result.get("zero") is True
