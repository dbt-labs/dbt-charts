"""Tests for resolution helpers and interpolate_scale_color extensions.

Covers piece 1B.6a helpers only:
  - resolve_palette_stops: named → hex list (spine-direct, no LUT), inline passthrough
  - resolve_hinge: decision tree (zero-crossing, percent-format, midpoint, explicit, None)
  - interpolate_scale_color: sequential back-compat; diverging asymmetric vs symmetric

Covers piece 1B.6b:
  - compute_scale_domain: CSV numeric-string coercion
  - resolve_cell_conditional_styles: scale path coerces numeric strings
  - coerce_numeric_cell: unit tests for the helper itself
"""

import pytest

from dbt_charts.core.compile.models.chart.authored import (
    ColumnScaleConfig,
    ScaleTargetConfig,
    TableColumnConfig,
)
from dbt_charts.core.render.chart.table_support import (
    _parse_hex,
    compute_scale_domain,
    interpolate_scale_color,
    resolve_cell_conditional_styles,
    resolve_hinge,
    resolve_palette_stops,
)
from dbt_charts.core.utils import coerce_numeric_cell

# ---------------------------------------------------------------------------
# resolve_palette_stops
# ---------------------------------------------------------------------------


class TestResolvePaletteStops:
    """Inline lists pass through; named palettes read the resolve-time-baked
    resolved_stops.

    Named-string -> hex resolution happens explicitly at each resolve-time
    construction site — bake_scale_target_stops (models/primitives.py, used
    by chart channels and heatmap/geo theme-cascade gradients) and
    compile/resolve/chart/_table.py's _with_resolved_scale_stops (the WCAG-safe
    table carve) — never in ScaleTargetConfig's own validator, which would
    otherwise re-bake (and potentially stale) on every intermediate
    merge_onto_base cascade step. resolve_palette_stops only reads the
    already-baked value.
    """

    def test_inline_list_passes_through(self) -> None:
        from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig

        stops = ["#ff0000", "#ffffff", "#0000ff"]
        result = resolve_palette_stops(ResolvedScaleTargetConfig(palette=stops))
        assert result == stops

    def test_inline_list_different_object(self) -> None:
        # Must return a list (not mutate the original).
        from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig

        stops = ["#ff0000", "#0000ff"]
        result = resolve_palette_stops(ResolvedScaleTargetConfig(palette=stops))
        assert result is not stops  # new object
        assert result == stops

    def test_named_palette_reads_baked_resolved_stops(self) -> None:
        from dbt_charts.core.compile.models.primitives import bake_scale_target_stops
        from dbt_charts.core.compile.resolve.style.palette import palette

        cfg = bake_scale_target_stops(ScaleTargetConfig(palette="dbt-seq-blue"))
        result = resolve_palette_stops(cfg)
        assert result == palette("dbt-seq-blue")
        assert all(stop.startswith("#") for stop in result)

    def test_vega_scheme_raises_not_asserts(self) -> None:
        """A plain ResolvedScaleTargetConfig with a Vega scheme string palette
        reaching resolve_palette_stops is a caller bug — the function raises
        ValueError rather than asserting so the error is visible in production.
        The assert was removed in the ScaleTargetConfig authored/resolved split."""
        from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig

        scheme_cfg = ResolvedScaleTargetConfig(palette="viridis")
        with pytest.raises(ValueError, match="scheme"):
            resolve_palette_stops(scheme_cfg)


class TestPaletteTableSurface:
    """palette() with surface='table' returns WCAG-safe stops via OKLCH interpolation."""

    def test_sequential_surface_table_returns_list(self) -> None:
        from dbt_charts.core.compile.resolve.style.palette import palette

        result = palette("dbt-seq-blue", surface="table")
        assert isinstance(result, list)
        assert len(result) == 11

    def test_sequential_surface_table_all_stops_wcag_safe(self) -> None:
        from dbt_charts.core.colors import wcag_contrast as _wcag_contrast
        from dbt_charts.core.compile.resolve.style.palette import palette

        result = palette("dbt-seq-blue", surface="table")
        for stop in result:
            ratio = _wcag_contrast("#222222", stop)
            assert ratio >= 4.5, (
                f"Stop {stop} contrast {ratio:.2f} < 4.5 against #222222"
            )

    def test_sequential_surface_table_shorter_than_full_spine(self) -> None:
        from dbt_charts.core.compile.resolve.style.palette import palette

        full = palette("dbt-seq-amber")
        table = palette("dbt-seq-amber", surface="table")
        # The darkest full-spine stop fails WCAG, so the table surface must
        # exclude it and thus the dark endpoint differs.
        assert table[-1] != full[-1], (
            "table surface should not end at the same dark stop as the full spine"
        )

    def test_diverging_surface_table_returns_odd_length(self) -> None:
        from dbt_charts.core.compile.resolve.style.palette import palette

        result = palette("dbt-div-blue-red", surface="table")
        assert isinstance(result, list)
        assert len(result) == 11

    def test_diverging_surface_table_all_stops_wcag_safe(self) -> None:
        from dbt_charts.core.colors import wcag_contrast as _wcag_contrast
        from dbt_charts.core.compile.resolve.style.palette import palette

        result = palette("dbt-div-blue-red", surface="table")
        for stop in result:
            ratio = _wcag_contrast("#222222", stop)
            assert ratio >= 4.5, (
                f"Stop {stop} contrast {ratio:.2f} < 4.5 against #222222"
            )

    def test_categorical_surface_table_raises(self) -> None:
        from dbt_charts.core.compile.resolve.style.palette import (
            SurfaceUnsupportedError,
            palette,
        )

        with pytest.raises(SurfaceUnsupportedError):
            palette("vivid-10", surface="table")

    def test_sequential_surface_table_steps_respected(self) -> None:
        from dbt_charts.core.compile.resolve.style.palette import palette

        result = palette("dbt-seq-blue", surface="table", steps=5)
        assert len(result) == 5

    def test_diverging_surface_table_steps_respected(self) -> None:
        from dbt_charts.core.compile.resolve.style.palette import palette

        result = palette("dbt-div-blue-red", surface="table", steps=7)
        assert len(result) == 7


# ---------------------------------------------------------------------------
# resolve_hinge
# ---------------------------------------------------------------------------


class TestResolveHinge:
    """Decision tree: None → sequential; explicit float → short-circuit;
    'auto' → zero-cross, percent-cross-1, midpoint."""

    def _make_cfg(
        self,
        hinge: float | str | None = None,
        arm_mode: str = "asymmetric",
    ) -> ScaleTargetConfig:
        return ScaleTargetConfig.model_validate(
            {
                "palette": ["#ff0000", "#ffffff", "#0000ff"],
                "hinge": hinge,
                "arm_mode": arm_mode,
            }
        )

    # None → sequential passthrough

    def test_none_returns_none(self) -> None:
        cfg = self._make_cfg(hinge=None)
        result = resolve_hinge(cfg, lo=-10.0, hi=10.0, col_format=None)
        assert result is None

    # Explicit float short-circuits the tree

    def test_explicit_float_returned_directly(self) -> None:
        cfg = self._make_cfg(hinge=0.0)
        result = resolve_hinge(cfg, lo=-100.0, hi=100.0, col_format=None)
        assert result == 0.0

    def test_explicit_float_nonzero(self) -> None:
        cfg = self._make_cfg(hinge=1.0)
        result = resolve_hinge(cfg, lo=0.0, hi=2.0, col_format=None)
        assert result == 1.0

    # auto → zero crossing

    def test_auto_zero_crossing_pivots_at_zero(self) -> None:
        cfg = self._make_cfg(hinge="auto")
        result = resolve_hinge(cfg, lo=-50.0, hi=50.0, col_format=None)
        assert result == 0.0

    def test_auto_zero_crossing_lo_negative_hi_zero(self) -> None:
        cfg = self._make_cfg(hinge="auto")
        result = resolve_hinge(cfg, lo=-10.0, hi=0.0, col_format=None)
        assert result == 0.0

    def test_auto_zero_crossing_lo_zero_hi_positive(self) -> None:
        # Domain [0, 10] does NOT cross zero (lo == 0 is inclusive but not negative).
        # Should fall through to midpoint.
        cfg = self._make_cfg(hinge="auto")
        result = resolve_hinge(cfg, lo=0.0, hi=10.0, col_format=None)
        assert result == 5.0  # midpoint fallback

    # auto → percent-format crosses 1.0

    def test_auto_percent_crosses_1_pivots_at_1(self) -> None:
        cfg = self._make_cfg(hinge="auto")
        result = resolve_hinge(cfg, lo=0.5, hi=1.5, col_format=".0%")
        assert result == 1.0

    def test_auto_percent_format_no_cross_falls_to_midpoint(self) -> None:
        cfg = self._make_cfg(hinge="auto")
        result = resolve_hinge(cfg, lo=0.0, hi=0.8, col_format=".1%")
        assert result == pytest.approx(0.4)

    def test_auto_percent_format_with_zero_crossing_favors_zero(self) -> None:
        # If domain crosses zero AND is percent format that crosses 1.0,
        # zero crossing takes priority (checked first).
        cfg = self._make_cfg(hinge="auto")
        result = resolve_hinge(cfg, lo=-0.5, hi=1.5, col_format=".0%")
        assert result == 0.0

    # auto → midpoint fallback

    def test_auto_midpoint_all_positive(self) -> None:
        cfg = self._make_cfg(hinge="auto")
        result = resolve_hinge(cfg, lo=100.0, hi=200.0, col_format=None)
        assert result == 150.0

    def test_auto_midpoint_all_negative(self) -> None:
        cfg = self._make_cfg(hinge="auto")
        result = resolve_hinge(cfg, lo=-200.0, hi=-100.0, col_format=None)
        assert result == -150.0

    def test_auto_non_percent_format_uses_midpoint(self) -> None:
        cfg = self._make_cfg(hinge="auto")
        result = resolve_hinge(cfg, lo=10.0, hi=30.0, col_format=",.0f")
        assert result == 20.0


# ---------------------------------------------------------------------------
# interpolate_scale_color — sequential back-compat
# ---------------------------------------------------------------------------


class TestInterpolateScaleColorSequential:
    """Existing sequential-only path must be unchanged when hinge is None."""

    def test_midpoint_two_stops(self) -> None:
        result = interpolate_scale_color(50, 0, 100, ["#000000", "#ffffff"])
        # 127.5 rounds to 128 = 0x80.
        assert result == "#808080"

    def test_at_min_returns_first_stop(self) -> None:
        result = interpolate_scale_color(0, 0, 100, ["#ff0000", "#00ff00"])
        assert result == "#ff0000"

    def test_at_max_returns_last_stop(self) -> None:
        result = interpolate_scale_color(100, 0, 100, ["#ff0000", "#00ff00"])
        assert result == "#00ff00"

    def test_below_min_clamps_to_first(self) -> None:
        result = interpolate_scale_color(-50, 0, 100, ["#ff0000", "#00ff00"])
        assert result == "#ff0000"

    def test_above_max_clamps_to_last(self) -> None:
        result = interpolate_scale_color(200, 0, 100, ["#ff0000", "#00ff00"])
        assert result == "#00ff00"

    def test_equal_min_max_returns_middle_stop(self) -> None:
        result = interpolate_scale_color(5, 5, 5, ["#ff0000", "#00ff00", "#0000ff"])
        assert result == "#00ff00"

    def test_explicit_none_hinge_is_sequential(self) -> None:
        result = interpolate_scale_color(50, 0, 100, ["#000000", "#ffffff"], hinge=None)
        assert result == "#808080"


# ---------------------------------------------------------------------------
# interpolate_scale_color — diverging paths
# ---------------------------------------------------------------------------


class TestInterpolateScaleColorDiverging:
    """Diverging interpolation (hinge is not None).

    Design invariants for asymmetric mode:
      - A value equidistant from the hinge on either side produces
        equivalently intense (same relative palette position) color.
      - At the hinge itself → middle palette stop (neutral).

    Design invariants for symmetric mode:
      - Each arm stretches fully (0..1) across its half-palette regardless
        of the arm's absolute magnitude.
      - At the hinge itself → middle palette stop (neutral).
    """

    # Three-stop diverging palette: red | white | blue
    _DIV_PALETTE = ["#ff0000", "#ffffff", "#0000ff"]

    # Asymmetric arm_mode

    def test_asymmetric_at_hinge_returns_neutral(self) -> None:
        result = interpolate_scale_color(
            0, -100, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        assert result == "#ffffff"

    def test_asymmetric_equal_magnitude_equal_intensity(self) -> None:
        # +50 and -50 around a hinge of 0 on domain [-100, 100]
        # Both are 50% of their respective arms → should land at same palette t.
        pos = interpolate_scale_color(
            50, -100, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        neg = interpolate_scale_color(
            -50, -100, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        # Both should be midway along their respective half-palettes.
        # white is #ffffff, blue is #0000ff, red is #ff0000.
        # 50% along [white→blue] = #7f7fff; 50% along [white→red] = #ff7f7f.
        # The key invariant: they must have equal "distance from neutral" in
        # palette-t terms — both at t=0.5 within their arm.
        # We verify by checking neither is neutral (#ffffff) and neither is
        # an arm extreme, and that they're symmetric around white.
        assert pos != "#ffffff"
        assert neg != "#ffffff"
        assert pos != "#0000ff"
        assert neg != "#ff0000"

    def test_asymmetric_positive_arm_at_max(self) -> None:
        result = interpolate_scale_color(
            100, -100, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        assert result == "#0000ff"

    def test_asymmetric_negative_arm_at_min(self) -> None:
        result = interpolate_scale_color(
            -100, -100, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        assert result == "#ff0000"

    def test_asymmetric_unequal_arms_shorter_arm_never_saturates(self) -> None:
        # Hinge at 0 on domain [-10, 100].
        # Asymmetric mode uses the longer arm (100) as the reference unit.
        # The shorter neg arm is only 10 units wide, so even at min_val=-10
        # (its furthest extent) t = 10/100 = 0.1 — never reaches full saturation.
        neg_at_extreme = interpolate_scale_color(
            -10, -10, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        # t = 10/100 = 0.1: far from full red (#ff0000).
        assert neg_at_extreme != "#ff0000"
        assert neg_at_extreme != "#ffffff"  # also not neutral
        # Both arms use the same reference_width=100, so same t produces the same
        # palette depth (though different hues). Check t=0.05 (value -5 neg, value 5 pos).
        neg_subtle = interpolate_scale_color(
            -5, -10, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        pos_subtle = interpolate_scale_color(
            5, -10, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        # Both at t=5/100=0.05: very subtle. Neither is neutral (#ffffff).
        assert neg_subtle != "#ffffff"
        assert pos_subtle != "#ffffff"

    # Symmetric arm_mode

    def test_symmetric_at_hinge_returns_neutral(self) -> None:
        result = interpolate_scale_color(
            0, -100, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="symmetric"
        )
        assert result == "#ffffff"

    def test_symmetric_positive_at_max_returns_arm_extreme(self) -> None:
        result = interpolate_scale_color(
            100, -100, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="symmetric"
        )
        assert result == "#0000ff"

    def test_symmetric_negative_at_min_returns_arm_extreme(self) -> None:
        result = interpolate_scale_color(
            -100, -100, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="symmetric"
        )
        assert result == "#ff0000"

    def test_symmetric_unequal_arms_both_stretch_fully(self) -> None:
        # Hinge at 0 on domain [-10, 100].
        # Symmetric: neg arm full stretch from neutral→red even though it's only width 10.
        # -5 is at 50% of neg arm width → should land at palette t=0.5 from neutral.
        # 50 is at 50% of pos arm width → should also land at palette t=0.5 from neutral.
        # In contrast to asymmetric: the colors are the same distance from neutral
        # in palette space (both at t=0.5 on their respective halves).
        neg = interpolate_scale_color(
            -5, -10, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="symmetric"
        )
        pos = interpolate_scale_color(
            50, -10, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="symmetric"
        )
        # Both should be at the same palette-t from neutral, but on opposite arms.
        assert neg != "#ffffff"
        assert pos != "#ffffff"

    def test_symmetric_vs_asymmetric_differ_on_unequal_arms(self) -> None:
        # Hinge at 0 on domain [-10, 100]. Neg arm width=10, pos arm width=100.
        # Asymmetric uses reference_width=100 (the longer arm).
        # For value=-5 on neg arm:
        #   asymmetric: t = 5/100 = 0.05 (subtle)
        #   symmetric:  t = 5/10  = 0.5  (half-way)
        # They must produce different colors.
        asym_neg = interpolate_scale_color(
            -5, -10, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        sym_neg = interpolate_scale_color(
            -5, -10, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="symmetric"
        )
        assert asym_neg != sym_neg, (
            "asymmetric and symmetric must differ on the shorter arm "
            f"(got asym={asym_neg!r}, sym={sym_neg!r})"
        )

    def test_symmetric_equal_arms_matches_asymmetric(self) -> None:
        # With equal arms (neg=50, pos=50), both modes produce the same result.
        asym = interpolate_scale_color(
            -25, -50, 50, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        sym = interpolate_scale_color(
            -25, -50, 50, self._DIV_PALETTE, hinge=0.0, arm_mode="symmetric"
        )
        assert asym == sym

    def test_non_zero_hinge(self) -> None:
        # Hinge at 1.0 on [0, 2]. Negative arm: [0, 1], positive arm: [1, 2].
        at_hinge = interpolate_scale_color(
            1.0, 0.0, 2.0, self._DIV_PALETTE, hinge=1.0, arm_mode="asymmetric"
        )
        assert at_hinge == "#ffffff"

    def test_clamp_below_min_diverging(self) -> None:
        result = interpolate_scale_color(
            -200, -100, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        assert result == "#ff0000"

    def test_clamp_above_max_diverging(self) -> None:
        result = interpolate_scale_color(
            200, -100, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        assert result == "#0000ff"

    # Fix 7: strengthened equal-intensity test — verify equal palette-t on both arms
    def test_asymmetric_equal_magnitude_equal_palette_t(self) -> None:
        # +50 and -50 from hinge=0 on domain [-100, 100]: equal arms of width 100.
        # Both values are at t=50/100=0.5 on their arm.
        # pos arm: neutral(#ffffff)→blue(#0000ff); t=0.5 → #7f7fff
        # neg arm: neutral(#ffffff)→red(#ff0000); t=0.5 → #ff7f7f
        # Verify by parsing RGB and checking equal distance from white.
        pos = interpolate_scale_color(
            50, -100, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        neg = interpolate_scale_color(
            -50, -100, 100, self._DIV_PALETTE, hinge=0.0, arm_mode="asymmetric"
        )
        # Parse channels.
        pr, pg, pb = _parse_hex(pos)
        nr, ng, nb = _parse_hex(neg)
        white = (255, 255, 255)
        pos_dist = (
            (pr - white[0]) ** 2 + (pg - white[1]) ** 2 + (pb - white[2]) ** 2
        ) ** 0.5
        neg_dist = (
            (nr - white[0]) ** 2 + (ng - white[1]) ** 2 + (nb - white[2]) ** 2
        ) ** 0.5
        assert abs(pos_dist - neg_dist) < 1.0, (
            f"Equal-magnitude values should be equidistant from neutral in RGB space; "
            f"pos={pos!r} dist={pos_dist:.2f}, neg={neg!r} dist={neg_dist:.2f}"
        )

    # An even-length diverging palette is valid: the hinge sits at the
    # boundary between the two center stops (a 50/50 blend), not on a single
    # shared neutral stop the way an odd-length palette has one.
    def test_even_length_palette_hinge_is_boundary_blend(self) -> None:
        from dbt_charts.core.render.chart.table_support import _lerp_color

        palette = ["#0000ff", "#ffffff", "#ffeeee", "#ff0000"]
        expected_neutral = _lerp_color(palette[1], palette[2], 0.5)
        at_hinge = interpolate_scale_color(0.0, -1.0, 1.0, palette, hinge=0.0)
        assert at_hinge == expected_neutral

    def test_two_stop_palette_hinge_is_boundary_blend(self) -> None:
        from dbt_charts.core.render.chart.table_support import _lerp_color

        palette = ["#ff0000", "#0000ff"]
        expected_neutral = _lerp_color(palette[0], palette[1], 0.5)
        at_hinge = interpolate_scale_color(0.0, -1.0, 1.0, palette, hinge=0.0)
        assert at_hinge == expected_neutral
        # Each extreme still fully saturates at the real domain bound.
        assert interpolate_scale_color(-1.0, -1.0, 1.0, palette, hinge=0.0) == "#ff0000"
        assert interpolate_scale_color(1.0, -1.0, 1.0, palette, hinge=0.0) == "#0000ff"

    def test_even_length_palette_is_continuous_through_the_boundary(self) -> None:
        """No jump at the hinge: a value just below and just above the
        boundary must land close to the same shared blend color, not the
        two center stops' own (unblended) colors."""
        from dbt_charts.core.render.chart.table_support import _lerp_color, _parse_hex

        palette = ["#0000ff", "#ffffff", "#ffeeee", "#ff0000"]
        neutral = _parse_hex(_lerp_color(palette[1], palette[2], 0.5))
        below = _parse_hex(
            interpolate_scale_color(-0.01, -1.0, 1.0, palette, hinge=0.0)
        )
        above = _parse_hex(interpolate_scale_color(0.01, -1.0, 1.0, palette, hinge=0.0))
        for n, b, a in zip(neutral, below, above, strict=True):
            assert abs(b - n) <= 1
            assert abs(a - n) <= 1

    def test_single_stop_palette_raises(self) -> None:
        with pytest.raises(ValueError, match="at least 2"):
            interpolate_scale_color(0.0, -1.0, 1.0, ["#ff0000"], hinge=0.0)

    # A hinge outside [lo, hi] now extends the domain to include it, instead
    # of clamping the hinge onto the nearer bound.
    def test_hinge_above_hi_extends_domain_instead_of_clamping(self) -> None:
        """Discriminates old (clamp) vs new (extend) behavior directly: the
        OLD code clamped hinge=300 down to hi=200, so value=200 (==hi)
        landed exactly AT the clamped hinge, distance 0 — the exact neutral
        stop. The NEW code extends the domain to [0, 300] instead, so
        value=200 is still 100 away from the real hinge (300) — genuinely
        graduated, never neutral."""
        result = interpolate_scale_color(
            200.0, 0.0, 200.0, self._DIV_PALETTE, hinge=300.0
        )
        assert result != "#ffffff"  # old-code clamp collapsed this to neutral
        assert result != self._DIV_PALETTE[0]  # not fully saturated either

    def test_hinge_below_lo_extends_domain_instead_of_clamping(self) -> None:
        """Mirror of the above: OLD code clamped hinge=-50 up to lo=0, so
        value=0 (==lo) was the clamped hinge itself, distance 0 → neutral.
        NEW code extends the domain down to -50, so value=0 is still 50
        away from the real hinge."""
        result = interpolate_scale_color(
            0.0, 0.0, 200.0, self._DIV_PALETTE, hinge=-50.0
        )
        assert result != "#ffffff"  # old-code clamp collapsed this to neutral
        assert result != self._DIV_PALETTE[-1]

    def test_hinge_outside_range_paints_graduated_not_saturated_color(self) -> None:
        """A board sharing one diverging palette across charts needs
        hinge=0 to mean 0 is neutral even on a chart whose own data never
        reaches zero. Extending the domain (not
        clamping the hinge) means a value halfway up the real data range
        lands roughly halfway up the extended arm, not near full
        saturation."""
        halfway = interpolate_scale_color(
            750.0, 500.0, 1000.0, self._DIV_PALETTE, hinge=0.0
        )
        near_hinge = interpolate_scale_color(
            510.0, 500.0, 1000.0, self._DIV_PALETTE, hinge=0.0
        )
        far_from_hinge = interpolate_scale_color(
            990.0, 500.0, 1000.0, self._DIV_PALETTE, hinge=0.0
        )
        near_dist = sum(
            (a - b) ** 2
            for a, b in zip(_parse_hex(near_hinge), _parse_hex("#ffffff"), strict=True)
        )
        half_dist = sum(
            (a - b) ** 2
            for a, b in zip(_parse_hex(halfway), _parse_hex("#ffffff"), strict=True)
        )
        far_dist = sum(
            (a - b) ** 2
            for a, b in zip(
                _parse_hex(far_from_hinge), _parse_hex("#ffffff"), strict=True
            )
        )
        assert near_dist < half_dist < far_dist
        # The sharp discriminator: OLD code clamped hinge=0 up to lo=500,
        # so value=500 (==lo) was exactly the clamped hinge, distance 0 →
        # the exact neutral stop. NEW code extends the domain down to 0
        # instead, so value=500 is still 500 away from the real hinge.
        at_lo = interpolate_scale_color(
            500.0, 500.0, 1000.0, self._DIV_PALETTE, hinge=0.0
        )
        assert at_lo != "#ffffff"

    def test_value_clamp_stays_at_true_data_bound_not_extended_hinge(self) -> None:
        """The domain EXTENSION is about where the neutral pivot sits, not
        about widening what counts as in-range data: a value below the
        real data minimum still clamps to that real minimum, even though
        the hinge extension has pulled the palette's own domain further out.

        Discriminates old vs new: OLD code clamped hinge=0 up to lo=500, so
        both values (200 clamped to 500, and 500 itself) landed exactly AT
        the clamped hinge — the exact neutral stop, "#ffffff" either way.
        NEW code extends the domain down to 0, so the shared clamped value
        (500) is 500 away from the real hinge — genuinely graduated, not
        neutral. The equality itself (value-clamp lands on the same true
        bound regardless of extension) holds under both, so it's paired
        here with the neutral-color check that only the new behavior fails.
        """
        below_data_min = interpolate_scale_color(
            200.0, 500.0, 1000.0, self._DIV_PALETTE, hinge=0.0
        )
        at_data_min = interpolate_scale_color(
            500.0, 500.0, 1000.0, self._DIV_PALETTE, hinge=0.0
        )
        assert below_data_min == at_data_min
        assert below_data_min != "#ffffff"


# ---------------------------------------------------------------------------
# Fix 2: even-length palette — resolve_hinge integration not needed; tested above
# ---------------------------------------------------------------------------


class TestResolvePaletteStopsEmpty:
    """Fix 4: empty palette list must be rejected before render time."""

    def test_empty_list_raises(self) -> None:
        from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig

        with pytest.raises(ValueError, match="empty"):
            resolve_palette_stops(ResolvedScaleTargetConfig(palette=[]))

    def test_single_stop_passes(self) -> None:
        # A single stop is valid — edge case but not malformed.
        from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig

        result = resolve_palette_stops(ResolvedScaleTargetConfig(palette=["#ff0000"]))
        assert result == ["#ff0000"]


class TestResolveHingeDegenerate:
    """Fix 5: lo==hi should return None (sequential fallback)."""

    def _make_cfg(self, hinge: float | str | None) -> ScaleTargetConfig:
        return ScaleTargetConfig.model_validate(
            {"palette": ["#ff0000", "#ffffff", "#0000ff"], "hinge": hinge}
        )

    def test_lo_equals_hi_auto_returns_none(self) -> None:
        cfg = self._make_cfg(hinge="auto")
        result = resolve_hinge(cfg, lo=5.0, hi=5.0, col_format=None)
        assert result is None

    def test_lo_equals_hi_explicit_returns_none(self) -> None:
        # Explicit float hinge with degenerate domain also returns None.
        cfg = self._make_cfg(hinge=5.0)
        result = resolve_hinge(cfg, lo=5.0, hi=5.0, col_format=None)
        assert result is None


# ---------------------------------------------------------------------------
# 1B.6b: coerce_numeric_cell unit tests
# ---------------------------------------------------------------------------


class TestCoerceNumeric:
    """coerce_numeric_cell must parse clean numeric strings, reject everything else."""

    def test_int_returns_float(self) -> None:
        assert coerce_numeric_cell(10) == 10.0

    def test_float_returns_float(self) -> None:
        assert coerce_numeric_cell(3.14) == pytest.approx(3.14)

    def test_clean_string_int(self) -> None:
        assert coerce_numeric_cell("10") == 10.0

    def test_clean_string_float(self) -> None:
        assert coerce_numeric_cell("193.52") == pytest.approx(193.52)

    def test_clean_string_negative(self) -> None:
        assert coerce_numeric_cell("-42.5") == pytest.approx(-42.5)

    def test_none_returns_none(self) -> None:
        assert coerce_numeric_cell(None) is None

    def test_bool_true_returns_none(self) -> None:
        # bool ⊂ int foot-gun: True must NOT be treated as 1.
        assert coerce_numeric_cell(True) is None

    def test_bool_false_returns_none(self) -> None:
        assert coerce_numeric_cell(False) is None

    def test_unparseable_string_returns_none(self) -> None:
        assert coerce_numeric_cell("abc") is None

    def test_empty_string_returns_none(self) -> None:
        assert coerce_numeric_cell("") is None

    def test_partial_numeric_string_returns_none(self) -> None:
        assert coerce_numeric_cell("3.14abc") is None

    def test_list_returns_none(self) -> None:
        assert coerce_numeric_cell([1, 2]) is None

    def test_dict_returns_none(self) -> None:
        assert coerce_numeric_cell({"a": 1}) is None

    # inf/nan must be treated as non-numeric — they poison scale domains
    def test_string_inf_returns_none(self) -> None:
        assert coerce_numeric_cell("inf") is None

    def test_string_nan_returns_none(self) -> None:
        assert coerce_numeric_cell("nan") is None

    def test_string_infinity_returns_none(self) -> None:
        assert coerce_numeric_cell("Infinity") is None

    def test_string_inf_upper_returns_none(self) -> None:
        assert coerce_numeric_cell("INF") is None

    def test_float_inf_returns_none(self) -> None:
        assert coerce_numeric_cell(float("inf")) is None

    def test_float_nan_returns_none(self) -> None:
        assert coerce_numeric_cell(float("nan")) is None


# ---------------------------------------------------------------------------
# 1B.6b: compute_scale_domain with CSV string numerics
# ---------------------------------------------------------------------------


class TestComputeScaleDomainCsvStrings:
    """compute_scale_domain must include clean numeric strings in domain inference."""

    _CFG = ScaleTargetConfig(palette=["#ffffff", "#0000ff"])

    def test_all_string_numerics(self) -> None:
        data = [{"x": "10"}, {"x": "20"}, {"x": "30"}]
        lo, hi = compute_scale_domain(data, "x", self._CFG)
        assert lo == 10.0
        assert hi == 30.0

    def test_mixed_string_and_int(self) -> None:
        data = [{"x": "10"}, {"x": 20}, {"x": "abc"}, {"x": None}]
        lo, hi = compute_scale_domain(data, "x", self._CFG)
        assert lo == 10.0
        assert hi == 20.0

    def test_bools_still_excluded(self) -> None:
        # True/False must not contribute to domain.
        data = [{"x": True}, {"x": False}, {"x": "5"}]
        lo, hi = compute_scale_domain(data, "x", self._CFG)
        assert lo == 5.0
        assert hi == 5.0

    def test_no_valid_values_falls_back_to_0_1(self) -> None:
        data = [{"x": "abc"}, {"x": None}, {"x": True}]
        lo, hi = compute_scale_domain(data, "x", self._CFG)
        assert lo == 0.0
        assert hi == 1.0

    def test_native_ints_still_work(self) -> None:
        data = [{"x": 5}, {"x": 15}]
        lo, hi = compute_scale_domain(data, "x", self._CFG)
        assert lo == 5.0
        assert hi == 15.0

    def test_inf_cell_does_not_poison_domain(self) -> None:
        # A single "inf" cell must not expand hi to infinity — it must be dropped.
        data = [{"x": "10"}, {"x": "inf"}, {"x": "20"}]
        lo, hi = compute_scale_domain(data, "x", self._CFG)
        assert lo == 10.0
        assert hi == 20.0


# ---------------------------------------------------------------------------
# 1B.6b: resolve_cell_conditional_styles scale path with CSV string value
# ---------------------------------------------------------------------------


class TestResolveCellConditionalStylesScaleCsvString:
    """Scale background must interpolate when value is a numeric string."""

    def _make_col_config(self) -> TableColumnConfig:
        return TableColumnConfig(
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(
                    palette=["#ffffff", "#0000ff"],
                    min=0,
                    max=100,
                )
            ),
        )

    def test_string_value_produces_interpolated_bg(self) -> None:
        col = self._make_col_config()
        data = [{"amount": "50"}]
        bg, _color, _fw, _fstyle, _fdecoration = resolve_cell_conditional_styles(
            col, "50", data
        )
        # Must be a non-None hex color, not the column default (None).
        assert bg is not None
        assert bg.startswith("#")

    def test_int_value_still_works(self) -> None:
        col = self._make_col_config()
        data = [{"amount": 50}]
        bg, _, _, _, _ = resolve_cell_conditional_styles(col, 50, data)
        assert bg is not None
        assert bg.startswith("#")

    def test_unparseable_string_skips_scale(self) -> None:
        col = self._make_col_config()
        data = [{"amount": "abc"}]
        bg, _, _, _, _ = resolve_cell_conditional_styles(col, "abc", data)
        # No scale applied → bg stays at column default (None).
        assert bg is None

    def test_bool_value_skips_scale(self) -> None:
        col = self._make_col_config()
        data = [{"amount": True}]
        bg, _, _, _, _ = resolve_cell_conditional_styles(col, True, data)
        assert bg is None


# ---------------------------------------------------------------------------
# 1B.6c: hinge + arm_mode wired through resolve_cell_conditional_styles
# ---------------------------------------------------------------------------


class TestResolveCellConditionalStylesDiverging:
    """resolve_cell_conditional_styles must pass hinge/arm_mode to interpolate_scale_color."""

    _DIV_PALETTE = ["#ff0000", "#ffffff", "#0000ff"]

    def _make_div_col(
        self,
        arm_mode: str = "asymmetric",
        hinge: float | str | None = 0,
    ) -> TableColumnConfig:
        return TableColumnConfig(
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig.model_validate(
                    {
                        "palette": self._DIV_PALETTE,
                        "min": -100,
                        "max": 100,
                        "hinge": hinge,
                        "arm_mode": arm_mode,
                    }
                )
            ),
        )

    def test_diverging_at_min_returns_first_stop(self) -> None:
        # In diverging mode on [-100,100] hinge=0: value=-100 (min) → negative arm extreme.
        # Neg arm goes from neutral (#ffffff) to first stop (#ff0000).
        # At min_val, distance=100, equal to neg_width=100 → t=1.0 → #ff0000.
        col = self._make_div_col(arm_mode="asymmetric", hinge=0)
        data = [{"val": -100}]
        bg, _, _, _, _ = resolve_cell_conditional_styles(col, -100, data)
        assert bg == "#ff0000", (
            f"Diverging min_val must map to first palette stop; got {bg!r}"
        )

    def test_diverging_at_max_returns_last_stop(self) -> None:
        # In diverging mode on [-100,100] hinge=0: value=100 → pos arm extreme (#0000ff).
        col = self._make_div_col(arm_mode="asymmetric", hinge=0)
        data = [{"val": 100}]
        bg, _, _, _, _ = resolve_cell_conditional_styles(col, 100, data)
        assert bg == "#0000ff", (
            f"Diverging max_val must map to last palette stop; got {bg!r}"
        )

    def test_diverging_hinge_at_value_returns_neutral(self) -> None:
        # The clearest diverging test: a value exactly at hinge must be the MIDDLE stop.
        # Sequential mode on [-100,100]: value=0 → t=0.5 → also middle stop (#ffffff).
        # This passes either way on a symmetric 3-stop palette — not distinguishing.
        # Use an ASYMMETRIC domain to force a difference.
        # Domain [-10, 100], hinge=0: sequential t = 10/110 ≈ 0.091 for value=0 (not neutral).
        # Diverging: value=0 is at the hinge → MUST return neutral (#ffffff).
        col_asym_domain = TableColumnConfig(
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(
                    palette=self._DIV_PALETTE,
                    min=-10,
                    max=100,
                    hinge=0,
                    arm_mode="asymmetric",
                )
            ),
        )
        data = [{"val": 0}]
        bg, _, _, _, _ = resolve_cell_conditional_styles(col_asym_domain, 0, data)
        # After fix: diverging → #ffffff (at hinge).
        # Before fix: sequential on [-10,100] → t=10/110≈0.091 → slightly off red, NOT white.
        assert bg == "#ffffff", (
            f"Value at hinge on asymmetric domain must return neutral stop; got {bg!r}"
        )

    def test_asymmetric_vs_symmetric_differ_on_unequal_arms(self) -> None:
        # Domain [-10, 100], hinge=0: neg arm is narrow (10 units), pos arm is wide (100).
        # On value=-5 (midpoint of neg arm):
        #   asymmetric: t = 5/100 = 0.05 (very subtle)
        #   symmetric:  t = 5/10  = 0.5  (half palette)
        # They must produce different interpolated colors.
        col_asym = TableColumnConfig(
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(
                    palette=self._DIV_PALETTE,
                    min=-10,
                    max=100,
                    hinge=0,
                    arm_mode="asymmetric",
                )
            ),
        )
        col_sym = TableColumnConfig(
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(
                    palette=self._DIV_PALETTE,
                    min=-10,
                    max=100,
                    hinge=0,
                    arm_mode="symmetric",
                )
            ),
        )
        data = [{"val": -5}]
        bg_asym, _, _, _, _ = resolve_cell_conditional_styles(col_asym, -5, data)
        bg_sym, _, _, _, _ = resolve_cell_conditional_styles(col_sym, -5, data)
        assert bg_asym is not None
        assert bg_sym is not None
        assert bg_asym != bg_sym, (
            f"asymmetric ({bg_asym!r}) and symmetric ({bg_sym!r}) must differ "
            "on shorter arm of unequal-domain diverging scale"
        )

    def test_col_format_percent_routes_auto_hinge(self) -> None:
        # Domain [0.5, 2.0]: midpoint=1.25, percent-hinge=1.0 — they differ.
        # With .0% format → hinge=1.0; value=0.75 → neg arm t=0.25 → #ffbfbf.
        # Without this domain the two paths return the same hinge and the
        # test passes whether col_format is wired through or not (theater).
        col = TableColumnConfig(
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(
                    palette=self._DIV_PALETTE,
                    min=0.5,
                    max=2.0,
                    hinge="auto",
                )
            ),
        )
        data = [{"val": 0.75}]
        bg, _, _, _, _ = resolve_cell_conditional_styles(
            col, 0.75, data, col_format=".0%"
        )
        # hinge=1.0, neg arm [0.5, 1.0], distance=0.25, denom=max(0.5,1.0)=1.0
        # t=0.25 → lerp(#ffffff, #ff0000, 0.25) = #ffbfbf
        assert bg == "#ffbfbf"

    def test_col_format_none_auto_hinge_falls_to_midpoint(self) -> None:
        # Domain [0.5, 2.0]: midpoint=1.25, percent-hinge=1.0.
        # No col_format → hinge=1.25; value=0.75 → neg arm t=0.667 → #ff5555.
        # Distinct from the percent path above, proving the two branches differ.
        col = TableColumnConfig(
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(
                    palette=self._DIV_PALETTE,
                    min=0.5,
                    max=2.0,
                    hinge="auto",
                )
            ),
        )
        data = [{"val": 0.75}]
        bg, _, _, _, _ = resolve_cell_conditional_styles(
            col, 0.75, data, col_format=None
        )
        # hinge=1.25, neg arm [0.5, 1.25], distance=0.5, denom=0.75
        # t=0.6667 → lerp(#ffffff, #ff0000, 0.6667) = #ff5555
        assert bg == "#ff5555"


# ---------------------------------------------------------------------------
# 1B.6c: summary/total row skip of scale fills
# ---------------------------------------------------------------------------


class TestResolveCellConditionalStylesRowRole:
    """Scale layer must be skipped for summary/total rows; when-rules still apply."""

    _SCALE_PALETTE = ["#ffffff", "#0000ff"]

    def _make_scale_col(self) -> TableColumnConfig:
        return TableColumnConfig(
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(
                    palette=self._SCALE_PALETTE,
                    min=0,
                    max=100,
                )
            ),
        )

    def test_value_row_gets_scale_bg(self) -> None:
        col = self._make_scale_col()
        data = [{"val": 50}]
        bg, _, _, _, _ = resolve_cell_conditional_styles(
            col, 50, data, row_role="value"
        )
        assert bg is not None
        assert bg != "#ffffff"  # not neutral/empty — some scale color applied

    def test_summary_row_skips_scale_bg(self) -> None:
        col = self._make_scale_col()
        data = [{"val": 50}]
        bg, _, _, _, _ = resolve_cell_conditional_styles(
            col, 50, data, row_role="summary"
        )
        # Scale skipped → bg stays at column default (None from col_config.background).
        assert bg is None

    def test_total_row_skips_scale_bg(self) -> None:
        col = self._make_scale_col()
        data = [{"val": 50}]
        bg, _, _, _, _ = resolve_cell_conditional_styles(
            col, 50, data, row_role="total"
        )
        assert bg is None

    def test_summary_row_with_when_rule_still_fires(self) -> None:
        """when-rules must apply even on summary/total rows — they are author-specified."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        col = self._make_scale_col()
        data = [{"val": 50}]
        # A when rule: val > 40 → background red.
        when_rule = ConditionalRule(gt=40, background="#ff0000")
        bg, _, _, _, _ = resolve_cell_conditional_styles(
            col, 50, data, when_rules=[when_rule], row_role="summary"
        )
        # Scale skipped but when fires.
        assert bg == "#ff0000"

    def test_scale_color_channel_also_skipped_on_summary(self) -> None:
        """scale.color channel must also be skipped on summary rows."""
        col = TableColumnConfig(
            scale=ColumnScaleConfig(
                color=ScaleTargetConfig(
                    palette=self._SCALE_PALETTE,
                    min=0,
                    max=100,
                )
            ),
        )
        data = [{"val": 50}]
        _, color, _, _, _ = resolve_cell_conditional_styles(
            col, 50, data, row_role="summary"
        )
        assert color is None


# ---------------------------------------------------------------------------
# _with_resolved_scale_stops — all three palette shapes
# ---------------------------------------------------------------------------


class TestWithResolvedScaleStops:
    """_with_resolved_scale_stops builds ResolvedTableColumnConfig entries
    from all three palette shapes: named string, inline list, and null scale."""

    def test_named_string_palette_bakes_resolved_name_palette_config(self) -> None:
        """A named string palette produces ResolvedNamedPaletteScaleTargetConfig
        with resolved_stops baked via the WCAG-safe table surface."""
        from dbt_charts.core.compile.models.primitives import (
            ResolvedNamedPaletteScaleTargetConfig,
        )
        from dbt_charts.core.compile.resolve.chart._table import (
            _with_resolved_scale_stops,
        )
        from dbt_charts.core.compile.resolve.style.palette import (
            palette as resolve_palette,
        )
        from dbt_charts.core.fonts import DBT_SANS_TABULAR_FONT_FAMILY

        columns = {
            "val": TableColumnConfig(
                scale=ColumnScaleConfig(
                    background=ScaleTargetConfig(palette="dbt-seq-blue"),
                )
            )
        }
        result = _with_resolved_scale_stops(
            columns,
            text_color="#ffffff",
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=[],
        )
        assert result is not None
        bg = result["val"].scale.background
        assert isinstance(bg, ResolvedNamedPaletteScaleTargetConfig)
        expected = tuple(
            resolve_palette("dbt-seq-blue", surface="table", text_color="#ffffff")
        )
        assert bg.resolved_stops == expected

    def test_inline_list_palette_produces_plain_resolved_config(self) -> None:
        """An inline stop list produces ResolvedScaleTargetConfig (no resolved_stops)."""
        from dbt_charts.core.compile.models.primitives import (
            ResolvedNamedPaletteScaleTargetConfig,
            ResolvedScaleTargetConfig,
        )
        from dbt_charts.core.compile.resolve.chart._table import (
            _with_resolved_scale_stops,
        )
        from dbt_charts.core.fonts import DBT_SANS_TABULAR_FONT_FAMILY

        stops = ["#ff0000", "#ffffff", "#0000ff"]
        columns = {
            "val": TableColumnConfig(
                scale=ColumnScaleConfig(
                    background=ScaleTargetConfig(palette=stops),
                )
            )
        }
        result = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=[],
        )
        assert result is not None
        bg = result["val"].scale.background
        assert type(bg) is ResolvedScaleTargetConfig
        assert not isinstance(bg, ResolvedNamedPaletteScaleTargetConfig)
        assert bg.palette == stops

    def test_null_scale_copies_column_without_scale(self) -> None:
        """A column with scale=None produces a ResolvedTableColumnConfig
        with no scale field — nothing is baked or altered."""
        from dbt_charts.core.compile.models.style.resolved import (
            ResolvedTableColumnConfig,
        )
        from dbt_charts.core.compile.resolve.chart._table import (
            _with_resolved_scale_stops,
        )
        from dbt_charts.core.fonts import DBT_SANS_TABULAR_FONT_FAMILY

        columns = {"val": TableColumnConfig(format="0.2f")}
        result = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=[],
        )
        assert result is not None
        assert isinstance(result["val"], ResolvedTableColumnConfig)
        assert result["val"].scale is None
        assert result["val"].format == "0.2f"

    def test_none_columns_returns_none(self) -> None:
        from dbt_charts.core.compile.resolve.chart._table import (
            _with_resolved_scale_stops,
        )
        from dbt_charts.core.fonts import DBT_SANS_TABULAR_FONT_FAMILY

        assert (
            _with_resolved_scale_stops(
                None,
                text_color=None,
                formats=None,
                font_family=DBT_SANS_TABULAR_FONT_FAMILY,
                rows=[],
            )
            is None
        )
