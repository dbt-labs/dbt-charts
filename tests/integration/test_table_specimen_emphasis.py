"""Behavioral pinning tests for table-emphasis.yml.

These tests load the specimen board and render specific charts to SVG,
asserting the distinctions that prove emphasis wiring fired correctly.
We pin specific cell fills or text colors that differ under the two
possible code paths (correct vs broken), so each test fails when the
relevant production guard is reverted.

Test structure:
  - M2a: diverging auto-hinge — Focal Point Inc (-0.081) gets diverging fill
    (#d29fb2), not sequential fill (#bd7391). Fails if resolve_hinge stubbed.
  - M3b: Apex (positive growth) has non-red growth text; Cascade (negative) has red.
    Fails if the lt:0 predicate is inverted.
  - M4a: tiered threshold — narrow-range cells get narrow-range color (rule order).
  - M6a: scale on quarterly_revenue — total row (193.52M) does NOT get its
    expected fill (#00481d). Fails if the is_summary_role guard is removed.
  - M6b: when-rules fire on value rows; total row (empty growth_rate) is uncolored.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile_file
from dbt_charts.core.compile.config import load_project_sources
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.execute.file_source_materializer import FileSourceMaterializer
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

from .._paths import DBT_CHARTS_DIR

EXAMPLES_DIR = DBT_CHARTS_DIR / "examples"
SPECIMEN = EXAMPLES_DIR / "playground" / "charts" / "cards" / "table-emphasis.yml"


def _registry(local_project: Callable[..., FilesystemProject]) -> AdapterRegistry:
    reg = AdapterRegistry(
        project=local_project(EXAMPLES_DIR / "playground"),
        project_sources=load_project_sources(
            local_project(EXAMPLES_DIR / "playground")
        ),
    )
    return reg


def _compile_and_render(
    chart_id: str, local_project: Callable[..., FilesystemProject]
) -> str:
    """Compile the specimen board and render chart *chart_id* to SVG."""
    assert SPECIMEN.exists(), f"Specimen file not found: {SPECIMEN}"
    # Use absolute paths throughout — avoids os.chdir which is process-global
    # state and causes failures when running in parallel with other tests that
    # also chdir.
    project = local_project(EXAMPLES_DIR / "playground")
    result = compile_file(project.path("charts/cards/table-emphasis.yml").read_board())
    assert result.success, f"Compile failed: {result.errors}"
    board = result.board
    assert board is not None

    cache = TrivialDuckDBCache()
    mat = FileSourceMaterializer(project)
    executor = Executor(
        board,
        adapter_registry=_registry(local_project),
        query_registry=result.query_registry,
        result_cache=cache,
        file_materializer=mat,
    )

    # board.charts is a dict keyed by chart id.
    chart = board.charts.get(chart_id)
    assert chart is not None, (
        f"No chart with id '{chart_id}' found in specimen. "
        f"Available: {list(board.charts.keys())}"
    )

    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.rendering import render_chart_item

    data = executor.execute_chart(chart, {})
    resolved = resolve(
        board.charts[chart_id], data, chart_style_context=board.chart_style_context
    )
    svg, _ = render_chart_item(
        resolved,
        executor,
        {},
        800.0,
        2000.0,
        resolved_style=board.resolved_style,
        render_cache={},
    )
    return svg


class TestM2aDivergingAutoHinge:
    """M2a: diverging crimson-green with auto-hinge on growth_rate.

    company_revenue growth_rate domain: [-0.081, 0.304].  Auto-hinge fires the
    zero-crossing branch → hinge=0.0.

    Mutation probe: replacing resolve_hinge(...) with ``return None`` forces
    sequential interpolation.  Focal Point Inc (-0.081) is the minimum value.
      - Diverging asymmetric:  neg arm t = 0.081/0.304 ≈ 0.266 → #e0cec9
      - Sequential (hinge=None): t = 0/385 = 0 (min) → #bd7391 (first stop)
    These two fills are distinct → the test fails under the mutation.
    """

    def test_focal_point_negative_growth_gets_diverging_fill(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Focal Point Inc (-0.081) must render the diverging crimson-arm fill.

        The diverging path produces #e0cec9 (slight crimson tint, near-neutral
        because -0.081 is a small magnitude relative to the positive arm of 0.304).
        Sequential interpolation on the same domain puts -0.081 at the minimum
        (t=0), producing #bd7391 (the darkest crimson stop) — a completely
        different color.  Asserting the exact diverging hex means the test
        fails when resolve_hinge is stubbed to return None.
        """
        svg = _compile_and_render("m2_div_growth", local_project)

        # SVG structure: after ">Focal Point Inc</text>" the first <rect> element
        # carries the scale fill for the growth_rate column.
        focal_idx = svg.index(">Focal Point Inc</text>")
        focal_section = svg[focal_idx : focal_idx + 600]
        rect_fill = re.search(r'<rect[^>]+fill="(#[0-9a-fA-F]{6})"', focal_section)
        assert rect_fill is not None, (
            "No rect fill found in Focal Point Inc row — scale background missing"
        )
        actual = rect_fill.group(1).lower()
        # Diverging asymmetric on domain [-0.081, 0.304], hinge=0:
        #   neg arm t = 0.081 / max(0.081, 0.304) = 0.266
        #   interpolates on WCAG-safe table surface (OKLCH binary-search) → #e0cec9
        # Sequential on same domain: t=0 (min) → #bd7391 (first stop)
        assert actual == "#e0cec9", (
            f"Focal Point Inc growth_rate fill is {actual!r}. "
            "Expected #e0cec9 (diverging asymmetric, table surface). "
            "If resolve_hinge returns None (sequential mode), this cell gets "
            "#bd7391 instead — a failure proves diverging path is active."
        )

    def test_apex_positive_growth_gets_green_arm_fill(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Apex Technologies (+0.125) must render from the green arm of the palette.

        In diverging mode, positive values go to the green arm (stops 5-10).
        The table-surface darkest green stop is #4a985d; at t=0.411 the expected
        fill is #9cc3a3. In sequential mode, Apex (t=0.538 from domain min) lands
        in the mid-crimson range, not in the green arm at all.
        """
        svg = _compile_and_render("m2_div_growth", local_project)

        apex_idx = svg.index(">Apex Technologies</text>")
        apex_section = svg[apex_idx : apex_idx + 600]
        rect_fill = re.search(r'<rect[^>]+fill="(#[0-9a-fA-F]{6})"', apex_section)
        assert rect_fill is not None, (
            "No rect fill found in Apex Technologies row — scale background missing"
        )
        actual = rect_fill.group(1).lower()
        # Diverging: pos arm t = 0.125/0.304 = 0.411 → #9cc3a3 (green arm, OKLCH table surface)
        # Sequential: t = (0.125+0.081)/0.385 = 0.535 → different color
        assert actual == "#9cc3a3", (
            f"Apex Technologies growth_rate fill is {actual!r}. "
            "Expected #9cc3a3 (diverging green arm, table surface). "
            "Sequential mode produces a different color, proving this test is load-bearing."
        )


class TestM3bColoredText:
    """M3b: negative-growth cells have red text (#dc2626), positive don't.

    company_revenue has 2 rows with negative growth_rate:
      Cascade Systems: -0.023
      Focal Point Inc: -0.081

    The when rule: lt: 0 → color: "#dc2626", font_weight: "600"

    SVG column order after company label: quarterly_revenue, growth_rate, margin.
    We extract the second <text fill="..."> after each company label, which is
    the growth_rate cell's text color.

    Mutation probe: inverting the predicate (lt→gte) would make Apex red and
    Cascade non-red — both assertions below would fail.
    """

    def test_cascade_negative_growth_has_red_text(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Cascade Systems (-0.023) must have growth_rate text color #dc2626."""
        svg = _compile_and_render("m3_text_bold_negative", local_project)

        cascade_idx = svg.index(">Cascade Systems</text>")
        cascade_section = svg[cascade_idx : cascade_idx + 800]
        # Extract fill colors of <text> elements (column cells in order).
        text_fills = re.findall(r'<text[^>]*fill="(#[0-9a-fA-F]{6})"', cascade_section)
        # text_fills[0] = quarterly_revenue, text_fills[1] = growth_rate
        assert len(text_fills) >= 2, (
            f"Expected at least 2 text elements after Cascade Systems, got {len(text_fills)}"
        )
        growth_fill = text_fills[1].lower()
        assert growth_fill == "#dc2626", (
            f"Cascade Systems growth_rate text fill is {growth_fill!r}. "
            "Expected #dc2626 (red) — the lt:0 when-rule must fire for -0.023."
        )

    def test_apex_positive_growth_does_not_have_red_text(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Apex Technologies (+0.125) must NOT have growth_rate text color #dc2626."""
        svg = _compile_and_render("m3_text_bold_negative", local_project)

        apex_idx = svg.index(">Apex Technologies</text>")
        apex_section = svg[apex_idx : apex_idx + 800]
        text_fills = re.findall(r'<text[^>]*fill="(#[0-9a-fA-F]{6})"', apex_section)
        assert len(text_fills) >= 2, (
            f"Expected at least 2 text elements after Apex Technologies, got {len(text_fills)}"
        )
        growth_fill = text_fills[1].lower()
        assert growth_fill != "#dc2626", (
            f"Apex Technologies growth_rate text fill is {growth_fill!r}. "
            "Should NOT be red (#dc2626) — the lt:0 rule must not fire for +0.125."
        )


class TestM4aThresholdRuleOrder:
    """M4a: tiered threshold on margin — broader rule first, narrower rule last.

    Rules (last-match-wins):
      Rule 1: lt 0.30 → amber (#fffbeb bg, #d97706 text)
      Rule 2: lt 0.20 → red (#fef2f2 bg, #dc2626 text)

    Data rows with margin < 0.20: Delta Analytics (0.185), Horizon Labs (0.192).
    Data rows with 0.20 ≤ margin < 0.30: Bright (0.296), Focal (0.257).

    For margin = 0.185: both rules match; last rule wins → red (#dc2626).
    For margin = 0.296: only first rule matches → amber (#d97706).

    Assertions:
      - Red (#dc2626) appears (narrow range fires for extreme values).
      - Amber (#d97706) appears (broad range fires for middle values).
      - Both colors are distinct (rule order is correct).
    """

    def test_narrow_range_rows_get_red_color(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Rows with margin < 0.20 must render with the narrow-rule red color."""
        svg = _compile_and_render("m4_threshold_margin", local_project)
        assert "#dc2626" in svg, (
            "Red color #dc2626 not found in M4a. "
            "The narrow threshold rule (lt:0.20) must fire last (last-match-wins). "
            "Check that rule order in YAML is broader-first, narrower-last."
        )

    def test_mid_range_rows_get_amber_color(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Rows with 0.20 ≤ margin < 0.30 must render with the amber color."""
        svg = _compile_and_render("m4_threshold_margin", local_project)
        assert "#d97706" in svg, (
            "Amber color #d97706 not found in M4a. "
            "The broad threshold rule (lt:0.30) must fire for mid-range values."
        )

    def test_both_tier_colors_coexist(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Both amber and red must be present — different tiers rendered correctly."""
        svg = _compile_and_render("m4_threshold_margin", local_project)
        assert "#dc2626" in svg and "#d97706" in svg, (
            "Both tier colors (#dc2626 red, #d97706 amber) must appear. "
            "If only one appears, the rule-order fix is missing or a tier has no matching rows."
        )


class TestM5GlyphIndicators:
    """M5: inline colored glyphs in the prefix tspan lane.

    M5a paints ▲ #16a34a for growth_rate > 0 and ▼ #dc2626 for < 0.
    M5c paints a static ● #64748b on every revenue cell.

    Mutation probe: removing the glyph branch from the prefix tspan
    rendering (or skipping resolve_cell_glyph in _render_data_rows)
    drops the colored tspan from the SVG and these tests fail.
    """

    def test_negative_growth_renders_red_down_arrow(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        svg = _compile_and_render("m5_glyph_trend_arrows", local_project)
        pattern = re.compile(r'<tspan[^>]+fill="#dc2626"[^>]*>▼</tspan>')
        assert pattern.search(svg), "Expected a red ▼ tspan for the negative-growth row"

    def test_positive_growth_renders_green_up_arrow(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        svg = _compile_and_render("m5_glyph_trend_arrows", local_project)
        pattern = re.compile(r'<tspan[^>]+fill="#16a34a"[^>]*>▲</tspan>')
        assert pattern.search(svg), "Expected a green ▲ tspan for positive-growth rows"

    def test_static_dot_appears_on_every_revenue_row(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        svg = _compile_and_render("m5_glyph_static_dot", local_project)
        pattern = re.compile(r'<tspan[^>]+fill="#64748b"[^>]*>●</tspan>')
        # company_revenue.csv has 8 rows; static glyph fires for each.
        assert len(pattern.findall(svg)) >= 8, (
            "Expected at least 8 static ● tspans (one per company row)"
        )

    def test_warning_triangle_on_low_margin(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """M5b: amber ⚠ tspan fires for any margin < 0.30."""
        svg = _compile_and_render("m5_glyph_warning_threshold", local_project)
        pattern = re.compile(r'<tspan[^>]+fill="#d97706"[^>]*>⚠</tspan>')
        assert pattern.search(svg), (
            "Expected an amber ⚠ tspan for at least one low-margin row"
        )


class TestM6aScaleWithTotal:
    """M6a: sequential scale on quarterly_revenue + total row — total suppresses fill.

    company_revenue_with_total has 8 value rows + 1 total row (193.52M).
    Scale is on quarterly_revenue (always non-null, including on the total row).

    If the is_summary_role guard at table_support.py:965 is removed, the total
    row's quarterly_revenue cell (193.52M) gets interpolated via the scale path.
    Domain is [3.89M, 193.52M] (all rows), so 193.52M maps to t=1.0 → last stop
    of dbt-div-crimson-green table surface = #4a985d (darkest WCAG-safe green).

    No value row produces #4a985d (all are in the crimson/mauve range).

    Mutation probe: remove ``not is_summary_role(row_role)`` from line 965.
    The total row gets fill=#4a985d → test_total_row_has_no_green_fill fails.
    Unit-level coverage of the same guard: TestResolveCellConditionalStylesRowRole
    in tests/core/render/test_table_emphasis_resolution.py.
    """

    # Expected total-row fill if the summary-skip guard is absent.
    # Computed: quarterly_revenue=193520000, domain=[3890000, 193520000], t=1.0
    # → last stop of dbt-div-crimson-green table surface (OKLCH-interpolated) = #4a985d.
    _TOTAL_SENTINEL_FILL = "#4a985d"

    def test_value_rows_get_scale_fills(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Value rows must receive a non-default background from the scale."""
        svg = _compile_and_render("m6_scale_with_total", local_project)

        # Apex (24.73M) is a representative value row.
        apex_idx = svg.index(">Apex Technologies</text>")
        apex_section = svg[apex_idx : apex_idx + 500]
        rect_fill = re.search(r'<rect[^>]+fill="(#[0-9a-fA-F]{6})"', apex_section)
        assert rect_fill is not None, (
            "No scale rect found in Apex Technologies row — quarterly_revenue scale missing"
        )

    def test_total_row_has_no_green_fill(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The total row's quarterly_revenue cell must not receive the scale fill.

        #4a985d is the fill the total row WOULD get if the summary-skip guard
        is removed (t=1.0 on the dbt-div-crimson-green table surface).  No value
        row produces this color (all value rows lie in the crimson/mauve range).
        Its presence proves the guard is absent; its absence proves the guard works.
        """
        svg = _compile_and_render("m6_scale_with_total", local_project)

        assert self._TOTAL_SENTINEL_FILL not in svg.lower(), (
            f"Fill {self._TOTAL_SENTINEL_FILL!r} found in M6a SVG. "
            "This is the total row's quarterly_revenue scale fill — it must be "
            "suppressed by the is_summary_role guard in table_support.py."
        )


class TestM6bThresholdWithTotal:
    """M6b: when-rules apply to value rows; total row (empty growth_rate) is uncolored.

    m6_threshold_with_total rules on growth_rate:
      lt: 0    → background #fef2f2, color #dc2626
      gte: 0.15 → background #f0fdf4, color #16a34a

    company_revenue_with_total data (relevant rows):
      Cascade Systems: growth_rate=-0.023  → lt:0 fires → red text
      Delta Analytics: growth_rate=+0.216  → gte:0.15 fires → green text
      Total row:       growth_rate=empty   → no predicate matches → default text

    Design distinction from M6a: when-rules DO apply to all row roles (including
    total/summary); only scale fills are suppressed.  The total row here gets
    default text color because its growth_rate is empty/None — not because
    when-rules are skipped.
    """

    def test_cascade_negative_growth_gets_red_text(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Cascade (-0.023): lt:0 when-rule fires → growth_rate text = #dc2626."""
        svg = _compile_and_render("m6_threshold_with_total", local_project)

        cascade_idx = svg.index(">Cascade Systems</text>")
        cascade_section = svg[cascade_idx : cascade_idx + 800]
        text_fills = re.findall(r'<text[^>]*fill="(#[0-9a-fA-F]{6})"', cascade_section)
        assert len(text_fills) >= 2, (
            f"Expected at least 2 text elements after Cascade Systems, got {len(text_fills)}"
        )
        growth_fill = text_fills[1].lower()
        assert growth_fill == "#dc2626", (
            f"Cascade Systems growth_rate text fill is {growth_fill!r}. "
            "Expected #dc2626 — the lt:0 when-rule must fire for -0.023."
        )

    def test_delta_high_growth_gets_green_text(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Delta Analytics (+0.216): gte:0.15 when-rule fires → growth_rate text = #16a34a."""
        svg = _compile_and_render("m6_threshold_with_total", local_project)

        delta_idx = svg.index(">Delta Analytics</text>")
        delta_section = svg[delta_idx : delta_idx + 800]
        text_fills = re.findall(r'<text[^>]*fill="(#[0-9a-fA-F]{6})"', delta_section)
        assert len(text_fills) >= 2, (
            f"Expected at least 2 text elements after Delta Analytics, got {len(text_fills)}"
        )
        growth_fill = text_fills[1].lower()
        assert growth_fill == "#16a34a", (
            f"Delta Analytics growth_rate text fill is {growth_fill!r}. "
            "Expected #16a34a — the gte:0.15 when-rule must fire for +0.216."
        )

    def test_total_row_growth_cell_has_default_text(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Total row growth_rate is empty → no when-rule fires → default text color.

        This confirms the design distinction: when-rules don't skip total rows,
        but None values don't match any predicate, so no rule fires.
        """
        svg = _compile_and_render("m6_threshold_with_total", local_project)

        total_idx = svg.index(">Total</text>")
        total_section = svg[total_idx : total_idx + 800]
        text_fills = re.findall(r'<text[^>]*fill="(#[0-9a-fA-F]{6})"', total_section)
        # Total row: company (Total), quarterly_revenue, growth_rate (—), margin (—)
        # At minimum 2 text elements (revenue + growth_rate dash)
        assert len(text_fills) >= 2, (
            f"Expected at least 2 text elements after Total, got {len(text_fills)}"
        )
        growth_fill = text_fills[1].lower()
        # No when-rule fires for None → must be default ink color
        assert growth_fill not in ("#dc2626", "#16a34a"), (
            f"Total row growth_rate text fill is {growth_fill!r}. "
            "Expected default ink — no when-rule should fire for an empty growth_rate."
        )
