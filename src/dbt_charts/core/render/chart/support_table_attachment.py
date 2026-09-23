"""Vega-Lite attachment for the chart.support_table primitive.

Post-pass on a chart-body Vega-Lite spec. Given a validated ChartSupportTable
and the chart's x-encoding, emit:
- An optional strip-top divider rule (when divider.width > 0).
- One text layer per row — source rows use a format() calculate transform;
  aggregate rows add a Vega-Lite aggregate transform grouped by x.
- N-1 inter-row rule layers when row.rule.width > 0.
- One label text layer per row that carries label: — emitted at the
  y-axis tick label's x-anchor (right gutter for right-oriented axes,
  left gutter for left-oriented axes), derived from the resolved axis_y.orient.

No new primitives: every output layer is standard Vega-Lite (mark: text,
mark: rule).

Y-positioning is pixel-literal (`{"y": {"value": <px>}}`), computed from
the spec's explicit height plus per-row offsets. Vega-Lite treats
`{"y": {"expr": "..."}}` as a SCALED data value (not a pixel literal),
so the previous expr-based approach collapsed every row to one pixel
position via the parent's quantitative y-scale. Spec.height must be set
when support_table is non-None — auto-sized specs have no anchor.

The caller owns the space reservation for position:bottom via
``bump_padding_bottom``. For position:top the mechanism depends on whether
the chart has a title (not subtitle alone):
- Titled charts: ``title.offset = strip_h`` (the probe value) so the title's
  absolute position is invariant to the offset amount (verified: under
  autosize:pad the title stays fixed regardless of title.offset value).
  ``layout_sizing._correct_support_table_height`` calibrates the probe offset
  downward so the true gap between title baseline and strip top equals
  strip_h rather than strip_h + VL's natural baseline gap.
- Subtitle-only and titleless charts: ``bump_padding_top`` — VL does not emit
  a role-title-text element that _measure_vl_title_plot_gap can anchor on
  when there is no title, so the original padding mechanism is used instead.
The renderer's downstream finalize step respects the external padding kwarg
when supplied.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from d3_format import FormatSpec, parse as _d3_parse
from d3_format.errors import D3FormatError
from dbt_charts.core.colors import ensure_readable_ink
from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.format import decimal_pad_table_for, resolve_format
from dbt_charts.core.compile.models.chart.authored import (
    ChartSort,
    ChartSupportTable,
    ChartSupportTableAggregate,
    ChartSupportTableAggregateOp,
    ChartSupportTablePerSeries,
    ChartSupportTableSource,
)
from dbt_charts.core.compile.models.chart.resolved import (
    FormatState,
    effective_color_field,
)
from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
)
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedAxisStyle,
    ResolvedChartDefaults,
)
from dbt_charts.core.compile.models.style.theme import SupportTableStyle
from dbt_charts.core.compile.models.style.theme.category_colors import (
    category_scale_for,
    ink_at,
)
from dbt_charts.core.compile.support_table import row_height
from dbt_charts.core.diagnostics import ERR_INPUT_INVALID
from dbt_charts.core.font_measure import compose_decimal_units, get_font_measurer
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.artifacts import ChartRenderData
from dbt_charts.core.render.chart.emitters._cartesian import (
    chart_sort_to_vl,
    companion_color_for_fill,
    emitted_categorical_color_scale,
)
from dbt_charts.core.render.chart.plot_width_floor_record import (
    record_plot_width_share_warning,
)
from dbt_charts.core.render.chart.spec_builders import (
    bump_padding_bottom,
    bump_padding_left,
    bump_padding_right,
    bump_padding_top,
)
from dbt_charts.core.render.chart.table import _table_numeric_cell_font
from dbt_charts.core.render.chart.time_unit_detect import (
    detect_time_unit,
    normalize_labeled_temporal,
    opens_label_period,
    resolve_label_time_unit,
    resolve_temporal_label_visibility,
)
from dbt_charts.core.render.chart.type_inference import (
    is_lex_sortable_date_like,
    temporal_edge_labels_flushed,
)
from dbt_charts.core.render.chart.vl_field_maps import _apply_decimal_pad
from dbt_charts.core.render.chart.x_domain import vl_sort_op
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.format_utils import format_value
from dbt_charts.core.render.utils import font_style_to_mark
from dbt_charts.core.text.case import inferred_display_name
from dbt_charts.core.text.numeral_scale import (
    SuffixMode,
    build_decimal_pad_table,
    column_digit_format,
    column_shares_one_printed_unit,
    decimal_pad_for,
    fractional_digit_count,
    shared_scale_for_column,
    suffix_at_register,
    tier_distance,
)
from dbt_charts.core.text.predefined_formats import PREDEFINED_NUMBER_NAMES
from dbt_charts.core.utils import (
    VlSortOp,
    sorted_series_by_stack_order,
    x_domain_order,
)

# Map authoring-surface aggregate names to Vega-Lite aggregate ops.
# Authoring names stay exact; the compiler is free to translate to VL ops.
# Keys are the ChartSupportTableAggregateOp Literal values; the test guard
# test_agg_op_map_keys_are_all_authoring_surface_ops enforces coverage.
_AGG_OP_TO_VL: dict[ChartSupportTableAggregateOp, str] = {
    "sum": "sum",
    "avg": "mean",
    "min": "min",
    "max": "max",
    "median": "median",
    "count": "count",
    "count_distinct": "distinct",
}

# Row labels share the y-axis tick column (~50–70 px for typical numeric
# formats). 80 px accommodates labels up to ~12 chars at 11 px Inter without
# the label reaching visibly into the legend zone or over-shrinking the plot.
_LABEL_STUB_LIMIT_PX = 80.0


def _strip_height(style: SupportTableStyle, n_rows: int) -> float:
    """Total pixel height for the attached strip (position-agnostic)."""
    row_h = row_height(style)
    divider_gap = get_chart_rendering().support_table.divider_gap
    divider = (style.divider.width + divider_gap) if style.divider.width > 0 else 0.0
    inter_row = (
        style.row.rule.width * (n_rows - 1)
        if style.row.rule.width > 0 and n_rows > 1
        else 0.0
    )
    return (
        style.padding_top + divider + row_h * n_rows + inter_row + style.padding_bottom
    )


def support_table_strip_height(
    support_table: ChartSupportTable | None,
    style: SupportTableStyle,
    axis_offset_value: float | None,
    series_count: int = 0,
) -> float:
    """Pixel height to reserve on the appropriate padding side when attaching a strip.

    Data-aware: ``series_count`` is the size of the color domain the query
    produced, so this runs at render time alongside ``attach_support_table``.

    For ``position: bottom``: includes the axis-label gap between the plot bottom
    and the strip top; callers pass the result to ``padding.bottom``.

    For ``position: top``: no axis gap (x-axis is below the plot); callers pass
    the result to ``padding.top``.

    Returns 0 when no strip is attached.

    Args:
        support_table: The support_table block, or None for no strip.
        style: Resolved SupportTableStyle (must carry ``position``).
        axis_offset_value: The resolved chart's own baked
            ``support_table_axis_offset`` (compile/resolve time — see
            ``compile.resolve._kwargs._support_table_geometry``). Required
            (non-None) when ``style.position == "bottom"``.
        series_count: How many series each ``per_series:`` entry expands to.
            All per_series entries on the same chart share one color domain,
            so one int suffices for the strip-height accounting. Required
            (>0) when any entry is ``ChartSupportTablePerSeries`` — leaving it
            at 0 with a per_series entry present silently under-sizes the
            strip and the expanded rows render below the reserved padding.
            RenderError is raised in that case rather than guessing 1.
    """
    if support_table is None or not support_table.entries:
        return 0.0
    n_rows = 0
    for entry in support_table.entries:
        if isinstance(entry, ChartSupportTablePerSeries):
            if entry.by_measure:
                n_rows += 1
            else:
                if series_count <= 0:
                    raise RenderError.from_code(
                        ERR_INPUT_INVALID,
                        message=(
                            "support_table_strip_height needs series_count > 0 when any "
                            "entry is `per_series:` — pass the number of color-domain "
                            "series the chart expands to. Defaulting to 1 silently "
                            "under-sizes the strip and the expanded rows render below "
                            "the reserved padding."
                        ),
                    )
                n_rows += series_count
        else:
            n_rows += 1
    strip_h = _strip_height(style, n_rows)
    if style.position == "bottom":
        assert axis_offset_value is not None, (
            "axis_offset_value is required when style.position == 'bottom' — "
            "the resolved chart's support_table_axis_offset must be baked (see "
            "compile.resolve._kwargs._support_table_geometry)"
        )
        return axis_offset_value + strip_h
    # top: x-axis is below the plot — no axis gap above the strip.
    return strip_h


def validate_support_table_against_data(
    support_table: ChartSupportTable,
    x_field: str | None,
    data: list[dict[str, Any]],
    x_type: str | None = None,
) -> int:
    """Data-aware validation of a support_table block.

    Runs at render time where the actual query output is available.
    Complements the data-free checks in AuthoredChart.validate_support_table
    (duplicate entries, chart-type, multi-y).

    For temporal/quantitative x axes with more than
    ``chart_rendering.support_table.chart_support_table_max_x_ticks`` rows, sampling
    is applied instead of failing. Returns the sampling step (>= 1); step=1
    means no sampling is needed.

    For ordinal/nominal/unknown x_type exceeding the cap, raises RenderError
    (fail-closed: dropping categories silently would be wrong).

    Raises:
        RenderError (ERR-INPUT-INVALID) on violation. Messages are
        explicit — they name the offending field and point at the resolution
        (e.g., "Add 'aggregate: <op>'...").
    """
    # x-axis existence check.
    if not x_field:
        raise RenderError.from_code(
            ERR_INPUT_INVALID,
            message=(
                "chart.support_table requires an x-encoding; the chart has no "
                "`x:` field. Add `x: <column>` or remove the support_table block."
            ),
        )

    # x-axis cardinality cap.
    x_values = {row.get(x_field) for row in data if x_field in row}
    n = len(x_values)
    sampling_step = 1
    max_x_ticks = get_chart_rendering().support_table.chart_support_table_max_x_ticks
    if n > max_x_ticks:
        if x_type in ("temporal", "quantitative"):
            # Temporal/quantitative: thin the strip labels via Vega-Lite
            # window+filter transforms. The chart data layers are unaffected.
            # Continue validating the checks below — an early return here
            # would silently skip the source-column-presence and
            # ambiguous-aggregation checks.
            sampling_step = math.ceil(n / max_x_ticks)
        else:
            raise RenderError.from_code(
                ERR_INPUT_INVALID,
                message=(
                    f"chart.support_table supports at most "
                    f"{max_x_ticks} x-axis ticks; got "
                    f"{n}. Aggregate or filter in the query before "
                    "rendering an attached table."
                ),
            )

    # Source column presence check: every referenced column must be present
    # in the query output. Use the first row as the schema sample (mirrors
    # how the rest of the render pipeline reads row shape).
    if data:
        available = set(data[0].keys())
        for entry in support_table.entries:
            if isinstance(entry, ChartSupportTablePerSeries):
                source_col = entry.per_series
            else:
                source_col = entry.source
            if source_col not in available:
                cols = ", ".join(sorted(available))
                raise RenderError.from_code(
                    ERR_INPUT_INVALID,
                    message=(
                        f"chart.support_table entry references source column "
                        f"{source_col!r} which is not in the query output. "
                        f"Available columns: {cols}."
                    ),
                )

    # Ambiguous-aggregation guard: a bare `source:` entry is ambiguous if the
    # query returns multiple rows per x. Point the author at `aggregate:`.
    # Normal per_series entries (by_measure=False) are exempt — they aggregate
    # groupby [x, color] and expect multiple rows per x.
    # by_measure entries are NOT exempt — they read datum[field] directly with
    # no aggregation transform, so multiple rows per x produce a wrong result.
    if data and x_field:
        counts = Counter(row.get(x_field) for row in data)
        multi_row = any(c > 1 for c in counts.values())
        if multi_row:
            for entry in support_table.entries:
                is_aggregate = isinstance(entry, ChartSupportTableAggregate)
                is_normal_per_series = (
                    isinstance(entry, ChartSupportTablePerSeries)
                    and not entry.by_measure
                )
                if not is_aggregate and not is_normal_per_series:
                    source_col = (
                        entry.per_series
                        if isinstance(entry, ChartSupportTablePerSeries)
                        else entry.source
                    )
                    raise RenderError.from_code(
                        ERR_INPUT_INVALID,
                        message=(
                            f"chart.support_table entry references column {source_col!r} "
                            f"which is ambiguous on this chart — {x_field!r} "
                            "returns multiple rows per x. Add "
                            f"'aggregate: <op>' (e.g. 'aggregate: sum, "
                            f"source: {source_col}') to resolve."
                        ),
                    )

    return sampling_step


def _row_y_pixel(
    index: int,
    style: SupportTableStyle,
    axis_offset_value: float | None,
    spec_height: float,
) -> float:
    """Pixel y of the centroid of the ith row in the attached strip.

    For ``position: bottom``: y > spec_height (strip below the plot). Row 0 is
    closest to the plot (first below the divider); row index increases downward.

    For ``position: top``: y < 0 (strip above the plot, in padding.top zone).
    Row 0 is closest to the plot top (y nearest 0); row index increases upward
    (y becomes more negative). No axis-offset is needed since the x-axis is below.
    """
    divider_off = (
        style.divider.width + get_chart_rendering().support_table.divider_gap
        if style.divider.width > 0
        else 0.0
    )
    row_h = row_height(style)
    inter_row = (
        style.row.rule.width * index if style.row.rule.width > 0 and index > 0 else 0.0
    )
    if style.position == "bottom":
        assert axis_offset_value is not None, (
            "axis_offset_value is required when style.position == 'bottom'"
        )
        offset = (
            style.padding_top
            + axis_offset_value
            + divider_off
            + index * row_h
            + inter_row
            + row_h / 2.0
        )
        return spec_height + offset
    # top: measure from y=0 (plot top) going upward (negative y).
    # Row 0 is closest to the plot; row N is furthest above.
    # Layout (from y=0 upward): padding_bottom, then rows (0…N), then padding_top.
    # Divider sits just above y=0, between padding_bottom and rows.
    dist_from_plot_top = (
        style.padding_bottom + divider_off + index * row_h + inter_row + row_h / 2.0
    )
    return -dist_from_plot_top


def _divider_y_pixel(
    style: SupportTableStyle,
    axis_offset_value: float | None,
    spec_height: float,
) -> float:
    """Pixel y of the divider rule.

    For ``position: bottom``: divider sits between the axis and the strip rows
    (just below axis labels), at spec_height + padding_top + axis_offset.

    For ``position: top``: divider sits between the strip rows and the plot top
    (just above y=0), at -(padding_bottom).
    """
    if style.position == "bottom":
        assert axis_offset_value is not None, (
            "axis_offset_value is required when style.position == 'bottom'"
        )
        return spec_height + style.padding_top + axis_offset_value
    # top: divider is the boundary between strip and plot.
    return -style.padding_bottom


def _inter_row_rule_y_pixel(
    index: int,
    style: SupportTableStyle,
    axis_offset_value: float | None,
    spec_height: float,
) -> float:
    """Pixel y of the inter-row rule between row `index` and row `index + 1`.

    Centroid sits in the middle of the inter-row gap so the rule's stroke
    splits evenly between the two adjacent rows; without the half-stroke
    offset the rule biases visually toward the row above.

    For ``position: bottom``: row y values increase downward, so the rule
    between row 0 and row 1 is *below* row 0's centroid → add half-row +
    half-stroke.

    For ``position: top``: row y values are negative (above the plot) and
    become *more negative* as index increases. The rule between row 0 and
    row 1 must be *more negative* than row 0's centroid → subtract.
    """
    row_y = _row_y_pixel(index, style, axis_offset_value, spec_height)
    half_gap = row_height(style) / 2.0 + style.row.rule.width / 2.0
    if style.position == "bottom":
        return row_y + half_gap
    # top: "between rows" is more negative (further above plot)
    return row_y - half_gap


# Row number over the cells that survive every filter, so "the first drawn
# cell" is true by construction. Distinct from ``__support_table_row_index``,
# which _sampling_transforms numbers *before* its own filter and which would
# therefore anchor on a row that was thinned away.
DRAWN_INDEX_FIELD = "__support_table_drawn_index"
# 1 on every drawn cell that can carry the unit (valid, finite, non-zero), 0
# otherwise. A cumulative sum of this over the drawn cells makes the anchor the
# first cell that CAN carry the affix, not merely the first drawn -- a metric
# whose first period is 0 would otherwise anchor on "$0" and declare the
# magnitude nowhere.
CARRIES_FIELD = "__support_table_carries"


def _drawn_index_transform(
    x_field: str,
    value_field: str,
    suffix_is_magnitude: bool,
    sort_ascending: bool = True,
) -> list[VLDict]:
    """Rank drawn cells so the anchor lands on the first unit-carrying one.

    Flags each drawn cell as carrying then takes a running sum:
    the first carrying cell is the one where both the flag and the running
    total equal 1.

    For a magnitude suffix the zero check is part of "carrying": "0mn" is
    meaningless, so a zero cell cannot be the anchor. For percent and currency-
    prefix-only rows a zero cell carries its affix perfectly well ($0 and 0%
    are real readings), so the ``!== 0`` gate is omitted there. The axis
    ruler's own currency prefix (``scale.py``'s ``prefix_repeats``,
    ``axis_cascade.py``'s non-compacting ``tick_label`` branch) makes the
    opposite call and excludes zero.

    When sort_ascending is True (default, temporal path) the window sorts by
    x ascending to match VL's own paint order. When False (sort:null category
    path), the window uses the input row order, which for sort:null axes is
    the data-insertion order — the same order VL uses to assign bands.
    """
    nonzero = (
        f" && datum[{json.dumps(value_field)}] !== 0" if suffix_is_magnitude else ""
    )
    carries = (
        f"isValid(datum[{json.dumps(value_field)}]) "
        f"&& isFinite(datum[{json.dumps(value_field)}])"
        f"{nonzero} ? 1 : 0"
    )
    window: VLDict = {
        "window": [{"op": "sum", "field": CARRIES_FIELD, "as": DRAWN_INDEX_FIELD}],
        "frame": [None, 0],
    }
    if sort_ascending:
        window["sort"] = [{"field": x_field, "order": "ascending"}]
    return [{"calculate": carries, "as": CARRIES_FIELD}, window]


@dataclass(frozen=True)
class StripAnchor:
    """Which single cell declares the row's unit, and how the spec asks.

    Three mechanisms covering the three axis families.

    An **ordered** axis can be thinned (sampling, label-period openers), so the
    first cell painted is not the first row of data. A row number computed
    *after* every filter answers that: ``by_drawn_index`` sorts the window
    ascending (matching VL's own paint order) for true temporal axes.

    A **sort:null category** axis tells VL to paint the domain in data-insertion
    order; ``by_drawn_index_data_order`` uses an unsorted window so the index
    follows that same insertion order. This path is immune to period-filter
    (window runs after filter) and datetime normalization (no x-value comparison).

    ``nowhere`` covers everything that can't be anchored (authored sort, left-
    aligned strip, quantitative x): no window, no conditional expression, just
    affix-free per-cell formatting.
    """

    test: str
    needs_drawn_index: bool
    # True when the window must run in data-insertion order (sort:null category
    # path) rather than ascending-x order (temporal path).
    data_order: bool = False

    @staticmethod
    def by_drawn_index() -> StripAnchor:
        """Temporal path: window sorted ascending by x, matching VL's paint order."""
        return StripAnchor(
            f"datum.{CARRIES_FIELD} === 1 && datum.{DRAWN_INDEX_FIELD} === 1", True
        )

    @staticmethod
    def by_drawn_index_data_order() -> StripAnchor:
        """sort:null category path: unsorted window follows data-insertion order.

        Immune to query-order swaps, datetime normalization mismatches, and
        period-filter dropping the x-equality anchor cell — the window runs
        after every filter so the first surviving cell in data order gets index 1.
        """
        return StripAnchor(
            f"datum.{CARRIES_FIELD} === 1 && datum.{DRAWN_INDEX_FIELD} === 1",
            True,
            data_order=True,
        )

    @staticmethod
    def nowhere() -> StripAnchor:
        """No cell declares the unit — the row repeats it, as it always has.

        Used where the leftmost painted cell cannot be identified: an authored
        ``sort:``, a quantitative x, or a time-format spec. Carries no test
        expression because it pairs only with affix-free numerals, which emit
        no conditional at all — a test string here would be one the emitter can
        never reach.
        """
        return StripAnchor("", False)


@dataclass(frozen=True)
class StripNumerals:
    """How one strip row spells its numbers.

    Split from the entry's format once, by ``strip_numerals_for_values``,
    rather than re-parsed at emission — the same bake-once shape
    ``ResolvedRulerAxis`` uses, for the same reason: the text a cell paints and
    the width the band-centering dx is measured against must come from one
    computation, not two.

    Two spellings, because only one family needs the author's spec rewritten:

    ``$,.3s`` → ``$441mn  448  456``   COMPOSED: d3's ``s`` type re-picks a
        tier per value, which is the behavior this feature exists to
        override, so the row divides by one shared magnitude and formats the
        digits through a rebuilt fixed-point spec.
    ``,.1%``  → ``5.5%    5.9  6.2``   TRIMMED: nothing about the number
    ``$,.0f`` → ``$441    448  456``   changes, so every cell formats through
        the author's own spec untouched — sign, fill, width, zero pad and trim
        all survive — and the cells that don't declare the unit simply have
        the repeated affix removed from the formatted string.
    """

    # The d3 spec each cell formats through: the author's own on a trimmed
    # row, a rebuilt fixed-point spec on a composed one.
    digit_spec: str
    # Which single cell declares the unit. Carried here rather than threaded
    # beside this object: a row that declares nothing has no anchor to speak
    # of, so the two are never meaningfully independent.
    anchor: StripAnchor
    prefix: str = ""
    suffix: str = ""
    # Composed rows only. The shared tier every cell divides by, plus the two
    # rules a magnitude suffix follows that a unit suffix does not: it is
    # dropped on a zero cell ("0mn" is meaningless, and zero is zero at any
    # scale, unlike 0% which is a real reading), and it repeats once the row
    # outgrows the tier it names (numeral_scale's own rule).
    divisor: float = 1.0
    suffix_is_magnitude: bool = False
    repeat_suffix: bool = False
    # Which side the anchor's affix overhangs. "left" (default): affix leads
    # the digits, hanging into the y-axis gutter — current right-oriented axis
    # behavior. "right": affix trails the digits, hanging into the anchor cell's
    # own band — left-oriented axis behavior, which keeps digits left-aligned.
    hang: Literal["left", "right"] = "left"
    # Plain trim-`f` columns only (see decimal_pad_table_for's own gate) --
    # measured trailing pad that lines cells up on the decimal point without
    # padding a whole number with false-precision zeros. Empty for every
    # SI/currency/percent row: those declare a shared unit through prefix/
    # suffix instead, and SI's shared-magnitude bake already puts every cell
    # at one column-wide decimal depth.
    decimal_pad_table: tuple[str, ...] = ()

    def bare_text(self, value: float) -> str:
        """The text a cell that does NOT declare the unit paints.

        The width pass measures this rather than the anchor's: the anchor
        carries affixes no sibling does, so sizing the lane to it would push
        every bare cell off the band it labels. A REPEAT row is the exception —
        there every non-zero cell paints the suffix, so it belongs in the width.
        """
        text = format_value(value / self.divisor, self.digit_spec, None)
        if self.decimal_pad_table:
            text += decimal_pad_for(self.decimal_pad_table, text)
        if self.suffix_is_magnitude:
            return text + self.suffix if self.repeat_suffix and value != 0 else text
        for affix in (self.prefix, self.suffix):
            if affix:
                text = text.replace(affix, "", 1)
        return text

    def anchor_text(self, value: float) -> str:
        """The text the anchor cell paints — all affixes included.

        Used only for width measurement so the band budget accounts for the
        widest *painted* cell, not just the bare one. Temporal anchors may
        land on band 2+ (when the first period is zero), so the band must be
        at least as wide as the anchor to prevent overlap with its neighbor.
        """
        if not (self.prefix or self.suffix):
            return self.bare_text(value)
        if self.suffix_is_magnitude:
            digits = format_value(abs(value) / self.divisor, self.digit_spec, None)
            if self.decimal_pad_table:
                digits += decimal_pad_for(self.decimal_pad_table, digits)
            sign = "−" if value < 0 else ""
            suffix = self.suffix if value != 0 else ""
            return sign + self.prefix + digits + suffix
        # Trimmed: the d3 format output already contains the affix.
        return format_value(value, self.digit_spec, None)

    @property
    def needs_drawn_index_window(self) -> bool:
        """Whether the emitted expression actually reads the drawn index.

        Only an affix gated on the anchor does. A REPEAT row's suffix rides
        every non-zero cell, so a REPEAT row with no prefix reads no index and
        must not carry the window -- a transform nothing references is exactly
        the surprise this render layer refuses to emit.
        """
        if not self.anchor.needs_drawn_index:
            return False
        anchored = bool(self.prefix) or (bool(self.suffix) and not self.repeat_suffix)
        return anchored


def _strip_numerals_text_expr(value_expr: str, numerals: StripNumerals) -> str:
    """Cell text for a row whose unit is declared once.

    A magnitude suffix speaks the narrative register (``441mn``), not
    ``SharedScale.register``'s mode-derived answer: a strip's cells are already
    separated by band space, so the analytic form's leading space would be a
    second separator competing with the first.

    The prefix anchors even when the suffix repeats — repeating a currency
    symbol on every cell disambiguates nothing (the convention
    ``non_compacting_tick_format`` documents, shared with the axis).
    """
    anchor_test = numerals.anchor.test

    if not (numerals.prefix or numerals.suffix):
        # Nothing repeats, so nothing is declared: every cell paints the
        # author's own spec, byte for byte what this row emitted before --
        # plus a trailing decimal-alignment pad when the plain trim-`f`
        # column carries one (empty for every other row, so this is a no-op
        # there: see StripNumerals.decimal_pad_table).
        formatted_e = f"format({value_expr}, '{numerals.digit_spec}')"
        return _apply_decimal_pad(formatted_e, numerals.decimal_pad_table)

    if not numerals.suffix_is_magnitude:
        # Trimmed: format through the author's spec, then remove the affix it
        # repeats. Rebuilding the spec from parts is what silently dropped a
        # `+` sign or a zero pad; removing a known affix from the result
        # cannot, whatever else the spec asks for.
        full = f"format({value_expr}, '{numerals.digit_spec}')"
        bare = full
        for affix in (numerals.prefix, numerals.suffix):
            if affix:
                bare = f"replace({bare}, {json.dumps(affix)}, '')"
        if numerals.hang == "left":
            return f"({anchor_test} ? {full} : {bare})"
        # hang="right" (left-axis): prefix-carrying formats route to nowhere before
        # reaching here, so this path only ever carries a suffix (percent). The
        # early `if not (prefix or suffix)` return above already excludes the
        # empty case, so suffix is always truthy by the time control reaches here.
        # Anchor arm is bare + conditional suffix so digit left-edges align.
        anchor_expr = f"({bare} + ({anchor_test} ? {json.dumps(numerals.suffix)} : ''))"
        return f"({anchor_test} ? {anchor_expr} : {bare})"

    # The sign expression leads the composition: d3 renders a negative currency
    # as "-$448", so the sign must precede the anchored symbol. All reachable
    # predefined SI specs use the default "-" sign convention (negative \u2192
    # U+2212, positive \u2192 nothing).
    scaled_value_e = f"abs({value_expr}) / {numerals.divisor!r}"
    digits = _apply_decimal_pad(
        f"format({scaled_value_e}, '{numerals.digit_spec}')",
        numerals.decimal_pad_table,
    )
    nonzero = f"{value_expr} !== 0"
    present = nonzero if numerals.repeat_suffix else f"{nonzero} && {anchor_test}"
    suffix_part = f"({present} ? {json.dumps(numerals.suffix)} : '')"
    sign_part = f"({value_expr} < 0 ? '\u2212' : '')"
    parts = [sign_part, digits, suffix_part]
    if numerals.prefix:
        # Prefix leads digits, hanging into the y-axis gutter (right-axis only;
        # prefix-carrying formats on left-axis route to StripAnchor.nowhere()).
        parts.insert(1, f"({anchor_test} ? {json.dumps(numerals.prefix)} : '')")
    return " + ".join(parts)


def _shared_scale_decimal_pad(
    values: list[float], digit_spec: str, magnitude: float, font_family: str
) -> tuple[str, ...]:
    """Decimal pad table for a shared-scale (SI-composed) row's digit portion.

    Mirrors ``_table.py``'s shared-scale column bake (``_with_resolved_scale_stops``):
    build the pad from the already-scaled values and the rebuilt fixed-point
    ``digit_spec`` -- never the original SI spec, since ``decimal_pad_table_for``
    only accepts a trim-enabled ``"f"`` type, which is exactly what
    ``column_digit_format`` produces -- and cap the table at the *observed* max
    fractional depth rather than ``digit_spec``'s own declared precision, which
    can bake far wider than any row's actual trimmed depth ever reaches.

    Returns ``()`` when every cell already prints the same number of fractional
    digits (nothing to align) -- the same "uniform depth" gate the axis and
    table sides apply -- or when ``digit_spec`` itself isn't a pad candidate
    (``decimal_pad_table_for``'s own self-gate).
    """
    spec_table = decimal_pad_table_for(digit_spec, font_family)
    if not spec_table:
        return ()
    pad_values = [v / magnitude for v in values if math.isfinite(v)]
    if not pad_values:
        return ()
    actual_fracs = {
        fractional_digit_count(format_value(v, digit_spec, None)) for v in pad_values
    }
    if len(actual_fracs) <= 1:
        return ()
    return decimal_pad_table_for(
        digit_spec, font_family, max_precision=max(actual_fracs)
    )


def _unscaled_decimal_pad(
    values: Sequence[float], digit_spec: str, font_family: str
) -> tuple[str, ...]:
    """Decimal pad table for a row with no shared magnitude to divide by --
    every cell keeps its own per-cell significant-figures spec. Self-gates on
    ``column_shares_one_printed_unit``: returns ``()`` when the row's cells
    don't all print the same SI suffix (a genuine mix across natural tiers,
    e.g. board 26's 30-128 magnitude mixed with a real "k"-tier value), since
    a bare whole number and a "k"-suffixed one share no decimal position to
    pad to.

    ``decimal_pad_table_for`` cannot build this: it needs a declared, fixed
    decimal precision from a type ``"f"`` spec, and a raw significant-figures
    spec's "full" width genuinely varies by each value's own integer-digit
    count, so there IS no single declared precision to read. This sidesteps
    that: instead of asking "what would this spec's full, untrimmed form look
    like", it asks the column's OWN cells what they actually printed, and
    pads every cell up to the deepest one observed -- a pure string-level
    question (how many fractional digits does this trimmed string show, out
    of the column's own max) that ``decimal_pad_for`` already answers
    identically regardless of *why* a cell trimmed short, fixed-decimal spec
    or significant figures alike.

    Returns ``()`` when every cell already prints the same number of
    fractional digits (nothing to align).
    """
    texts = [(v, format_value(v, digit_spec, None)) for v in values if math.isfinite(v)]
    if not column_shares_one_printed_unit(texts):
        return ()
    fracs = {fractional_digit_count(t) for _, t in texts}
    if len(fracs) <= 1:
        return ()
    digit_unit, dot_unit = compose_decimal_units(font_family)
    return build_decimal_pad_table(max(fracs), digit_unit, dot_unit)


def _magnitude_numerals(
    values: list[float],
    resolved: str,
    parsed: FormatSpec,
    anchor: StripAnchor,
    font_family: str,
    hang: Literal["left", "right"] = "left",
) -> StripNumerals:
    """Numerals for an SI row: one shared tier, and a derived decimal depth.

    Decimal depth is set by ``column_digit_format``, which pins precision to
    the finest (smallest non-zero) scaled value so that every cell in the row
    reaches its significant-figure count — the same formula the table's
    shared-scale column bake uses, ensuring strip and table never diverge.

    Zero and non-finite cells are excluded from that vote, the way
    ``shared_scale_for_column`` already excludes zeros from its tier vote: a
    zero scales to "0", one integer digit, and would otherwise buy the whole
    row a significant figure it never asked for.

    If the finest value is more than one SI tier below the shared majority
    (``tier_distance > 1``) the shared bake is refused: one outlier would
    inflate every other cell's precision far beyond the format's own
    significant-figure count. The row then keeps the author's spec, the
    same guard ``_table.py``'s shared-scale column bake applies.

    Every refusal path below still needs a decimal pad when the row's own
    cells all print the same SI suffix -- but not when the row instead has a
    genuine mix (different suffixes, or a suffix mixed with none): those have
    no shared decimal position to align on at all, so each refusal routes
    through ``_unscaled_decimal_pad``, which self-gates on
    ``column_shares_one_printed_unit`` rather than baking a pad from a
    mismatched set of magnitudes.

    For a left-oriented axis (``hang="right"``), a currency prefix cannot anchor
    without leaving the unit absent from non-anchor cells. Such formats route to
    ``StripAnchor.nowhere()`` so every cell formats with its own spec unchanged.
    """
    if hang == "right" and parsed.symbol:
        return StripNumerals(digit_spec=resolved, anchor=StripAnchor.nowhere())
    # Filter before the scale call: shared_scale_for_column floors and counts
    # digits, which raises on NaN and overflows on inf.
    voters = [v for v in values if v != 0 and math.isfinite(v)]
    scale = shared_scale_for_column(voters) if voters else None
    if scale is None or not voters:
        # No shared magnitude to declare — the row keeps the author's spec.
        return StripNumerals(
            digit_spec=resolved,
            anchor=anchor,
            hang=hang,
            decimal_pad_table=_unscaled_decimal_pad(values, resolved, font_family),
        )
    magnitude = 10.0**scale.exponent
    finest = min(voters, key=abs)
    distance = tier_distance(finest, scale.exponent)
    if distance is None or distance > 1:
        # Finest value is below all SI tiers, or more than one tier below the
        # majority — the shared bake would need so many decimals for that one
        # value that every sibling cell inherits unwarranted precision. Refuse.
        return StripNumerals(
            digit_spec=resolved,
            anchor=anchor,
            hang=hang,
            decimal_pad_table=_unscaled_decimal_pad(values, resolved, font_family),
        )
    finest_scaled = abs(finest) / magnitude
    _, digit_spec = column_digit_format(resolved, finest_scaled)
    # column_digit_format calls _digit_spec(parsed, max(0, ...)), so precision is
    # always a non-negative int — None is structurally impossible here.
    parsed_digits = _d3_parse(digit_spec)
    decimals = parsed_digits.precision or 0  # type-state: silent_fallback — always set
    # Second guard from _table.py: even within tier_distance <= 1, a low
    # authored sig-fig count can leave a sub-tier value formatting as "0",
    # indistinguishable from a genuine zero row. Refuse when any non-zero voter
    # carries no nonzero digit in the shared digit_spec.
    if any(
        not any(
            c.isdigit() and c != "0"
            for c in format_value(v / magnitude, digit_spec, None)
        )
        for v in voters
    ):
        return StripNumerals(
            digit_spec=resolved,
            anchor=anchor,
            hang=hang,
            decimal_pad_table=_unscaled_decimal_pad(values, resolved, font_family),
        )
    # shared_scale_for_column picks the mode from a *truncated* digit count,
    # which is right for a ruler writing values out in full. A strip rounds to
    # the derived precision first, so re-check against the number the cell
    # actually prints: 999,600 at `.3s` truncates to 3 digits but prints
    # "1,000", the four-digit case the mode rule exists to catch.
    printed_extreme = round(max(abs(v) for v in voters) / magnitude, decimals)
    # numeral_scale applies this same 4-digit rule to the truncated value; the
    # strip re-checks on the rounded value a cell actually prints, because a
    # value that truncates to 3 digits can round to 4 (e.g. 999,600 → "1,000").
    repeat = scale.mode is SuffixMode.REPEAT or len(str(int(printed_extreme))) >= 4
    pad_table = _shared_scale_decimal_pad(values, digit_spec, magnitude, font_family)
    return StripNumerals(
        digit_spec=digit_spec,
        anchor=anchor,
        prefix=parsed.symbol,
        suffix=suffix_at_register(scale.exponent, "narrative"),
        divisor=magnitude,
        suffix_is_magnitude=True,
        repeat_suffix=repeat,
        hang=hang,
        decimal_pad_table=pad_table,
    )


def strip_numerals_for_values(
    values: list[float],
    resolved: str,
    anchor: StripAnchor,
    *,
    font_family: str,
    hang: Literal["left", "right"] = "left",
) -> StripNumerals:
    """How a strip row spells its numbers.

    Every row gets an answer — a row with no unit to declare is spelled by the
    author's own spec with no affixes, which the emitter paints identically in
    every cell. That is the same output the row produced before this feature
    existed, so "declares nothing" is a kind of spelling rather than an absence.

    The author needs no new field to opt in: a format carrying a currency
    symbol, a percent sign, or an SI type has already said what the row's unit
    is. Reading it out of the spec they wrote is what keeps this off the
    authored surface entirely.

    Every non-SI type is spelled by the author's spec unchanged, whatever it is
    — ``p``, ``g``, a bare ``$,`` — because that path never rewrites the spec
    and so cannot misread one. Only ``s`` is rebuilt, and only there does the
    type need to be understood.

    For a left-oriented axis (``hang="right"``), a currency prefix cannot anchor
    without leaving the unit absent from non-anchor cells or using non-standard
    trailing-prefix typography. Such formats route to ``StripAnchor.nowhere()``
    so every cell formats with its own spec and the symbol always appears.

    Called only after the provenance gate in ``_entry_numerals`` confirms
    ``resolved`` is a predefined number name, so a time format or an unresolved
    alias never reaches here. Parse safety itself comes from
    ``compile/validate/formats.py``'s ``ERR_FORMAT_NATIVE_IN_VEGA_SLOT`` check,
    which rejects a ``PREDEFINED_NATIVE`` member (bypasses d3 entirely) on a
    Vega-painted slot like ``support_table`` at compile time -- not from the gate
    itself, which only narrows by name.
    """
    parsed = _d3_parse(resolved)
    if parsed.type == "s":
        return _magnitude_numerals(values, resolved, parsed, anchor, font_family, hang)
    if hang == "right" and parsed.symbol:
        # A prefix on a left-axis strip cannot anchor without losing the unit
        # on non-anchor cells. Every cell formats through its own spec instead.
        return StripNumerals(digit_spec=resolved, anchor=StripAnchor.nowhere())
    suffix = "%" if parsed.type == "%" else ""
    return StripNumerals(
        digit_spec=resolved,
        anchor=anchor,
        prefix=parsed.symbol,
        suffix=suffix,
        hang=hang,
    )


def _vl_format_calc(
    source: str,
    format_spec: FormatState,
    as_name: str,
    numerals: StripNumerals,
) -> dict[str, Any]:
    """Build a Vega-Lite calculate transform that formats or dashes invalid values."""
    value_expr = f"datum[{json.dumps(source)}]"
    valid_expr = (
        f"isValid({value_expr}) && (!isNumber({value_expr}) || isFinite({value_expr}))"
    )
    if format_spec is None:
        return {"calculate": f"{valid_expr} ? {value_expr} : '-'", "as": as_name}
    # The alias is already resolved: numerals.digit_spec is what
    # strip_numerals_for_values was handed, so nothing is re-resolved here.
    text_expr = _strip_numerals_text_expr(value_expr, numerals)
    return {
        "calculate": f"{valid_expr} ? {text_expr} : '-'",
        "as": as_name,
    }


def _default_support_table_label(
    entry: ChartSupportTableSource | ChartSupportTableAggregate,
) -> str:
    return entry.label or inferred_display_name(entry.source, case="title")


def _per_series_column_header(entry: ChartSupportTablePerSeries) -> str:
    """Header for a grouped bar's single-column per_series entry.

    A grouped bar's per_series entry paints one column holding every series'
    cell (see ``attach_support_table_columns``), so there is no per-series
    header slot to name a series in — the series themselves are named by the
    chart's own legend, same as an ordinary grouped bar. The column itself
    still needs a name, so it takes the entry's own authored ``label:``, or
    else the same title-cased default the source/aggregate columns use.
    """
    return entry.label or inferred_display_name(entry.per_series, case="title")


def _series_names_or_empty(series_order: list[str] | None) -> list[str]:
    return series_order or []  # type-state: silent_fallback — measured


def _wrap_base_as_layer(spec: dict[str, Any]) -> dict[str, Any]:
    """Lift a single-mark spec into {layer: [base, ...]} form if needed."""
    if "layer" in spec:
        return dict(spec)
    base_layer: dict[str, Any] = {}
    for key in ("mark", "encoding", "transform"):
        if key in spec:
            base_layer[key] = spec[key]
    new_spec = {k: v for k, v in spec.items() if k not in ("mark", "encoding")}
    # `transform` stays on the base layer; the attached layers carry their own.
    if "transform" in new_spec:
        del new_spec["transform"]
    # Promote tooltip to spec-level so strip layers inherit it (PR #1877).
    # Strip text marks have only x + a calc text field; without an inherited
    # tooltip, hover shows the raw channel field (e.g. ``revenue: 100.5``)
    # instead of titled-and-formatted output.
    #
    # Two paths:
    # - If the base encoding carries an explicit tooltip array (legacy callers,
    #   unit tests that hand-feed one): promote it as-is.
    # - Otherwise: synthesize from the base's x/y channels — the bar layer's
    #   channels carry title + format after this PR, so the synthesized array
    #   is the same content the legacy tooltip array used to carry.
    #
    # Dict comprehension (not .pop) — base_layer["encoding"] is a shared
    # reference to spec["encoding"] and mutation would corrupt the input.
    base_encoding = base_layer.get("encoding")
    if isinstance(base_encoding, dict):
        existing_tooltip = base_encoding.get("tooltip")
        if existing_tooltip is not None:
            base_layer["encoding"] = {
                k: v for k, v in base_encoding.items() if k != "tooltip"
            }
            new_spec["encoding"] = {"tooltip": existing_tooltip}
        else:
            spec_tooltip = [
                {k: enc[k] for k in ("field", "type", "title", "format") if k in enc}
                for ch in ("x", "y")
                if (enc := base_encoding.get(ch, {})) and enc.get("field")
            ]
            if spec_tooltip:
                new_spec["encoding"] = {"tooltip": spec_tooltip}
    new_spec["layer"] = [base_layer]
    return new_spec


def _disable_inherited_tooltip_description(
    layers: list[VLDict], base_layer_count: int
) -> list[VLDict]:
    """Opt every strip/column layer THIS MODULE appends out of the base
    chart's inherited structured-tooltip ``description`` encoding.

    ``attach_support_table``/``attach_support_table_columns`` run as a
    post-pass on the already-assembled Vega-Lite spec, so when the base
    chart is already multi-layered before attachment (line/area's halo/
    foreground/hover-point trio, a combo overlay, ...) the structured
    tooltip feature has already stamped ``description`` -- correctly -- on
    those base layers (``translate.py``'s ``_apply_structured_tooltip``,
    which runs inside ``assemble_final_vl``, before this post-pass). Every
    layer appended here shares that same outer ``layer:`` array and would
    silently inherit it too, but a strip cell's own transform (aggregate/
    window/calculate, grouped only by x for a bare ``aggregate:`` entry)
    produces a datum shaped nothing like any base layer's row, so the
    inherited expr reads undefined color/series/value fields off it,
    producing an aria-label chart_interactivity.js's x-unified tooltip
    collector cannot tell apart from a real series sharing the same x,
    rendering a phantom "undefined NaN" row on hover
    (dbt-labs/dbt-charts#29).

    ``base_layer_count`` is ``len(layers)`` captured right after
    ``list(out["layer"])`` in the caller, before anything is appended --
    every layer at or past that index is one this module built and gets an
    explicit ``description: None`` override (Vega-Lite's own "explicit None
    disables an inherited channel" convention); every layer before it is
    the base chart's own and is returned untouched, keeping whichever
    description (inherited or, for a combo overlay, its own explicit one)
    it already carried.
    """
    disabled: list[VLDict] = list(layers[:base_layer_count])
    for layer in layers[base_layer_count:]:
        encoding = layer.get("encoding")
        disabled.append(
            {
                **layer,
                "encoding": {
                    **(encoding if encoding is not None else {}),
                    "description": None,
                },
            }
        )
    return disabled


def _shared_x_encoding(
    parent_x_enc: dict[str, Any], mark_is_bar: bool
) -> dict[str, Any]:
    """Shared x-encoding for all attached layers.

    Copies field, type, timeUnit (when present), and sort (when present) from
    the parent chart's x-encoding so strip layers share the same scale type
    AND the same category order. Mismatched types (e.g. parent temporal vs
    strip ordinal) cause a vl-convert null-deref. Mismatched *sort* is
    quieter but just as wrong: Vega-Lite merges every layer sharing this
    field into one scale, and a layer that declares no ``sort`` at all pulls
    the merged domain back to natural/alphabetical order even when every
    other layer agrees on an authored one -- confirmed empirically (a
    layered OR unlayered bar with `chart.sort` authored and a
    `support_table:` attached silently reverted to alphabetical before this
    copy existed, in every orientation). Copying the key verbatim (including
    an explicit ``sort: None``, which some anchor logic elsewhere reads as
    "data order" and must not be conflated with "key absent") keeps every
    attached layer's declaration identical to the base mark's, so Vega-Lite
    never has two conflicting answers to reconcile.

    bandPosition rules — the cell must ride the same x position as the base mark:
      - ordinal / nominal → 0.5 (band-center anchor; marks render at the band
        center either way — bars add dx to center the number column, line/area
        lean toward the row label via align).
      - temporal + timeUnit → 0.5 only for a BAR: a bar spans the whole time
        band, so its number column centers on the band. A line/area point sits
        on its exact date (the grid tick), so its cell must ride that same
        per-point position — NO bandPosition. Anchoring a line/area cell to the
        band would both offset it half a band off the point AND pull the band's
        ``_end`` into the x-scale domain union, widening the axis past the data.
      - quantitative / plain temporal → omitted (continuous scale, no bands).

    No axis: null — setting axis to None crashes vl-convert with a TypeError
    in parseAxesAndHeaders. Omitting the axis key entirely is correct; Vega-Lite
    infers axis rendering from context and the text mark does not need axis ticks.
    """
    enc: dict[str, Any] = {"field": parent_x_enc["field"], "type": parent_x_enc["type"]}
    if "sort" in parent_x_enc:
        enc["sort"] = parent_x_enc["sort"]
    if "timeUnit" in parent_x_enc:
        enc["timeUnit"] = parent_x_enc["timeUnit"]
        if mark_is_bar:
            enc["bandPosition"] = 0.5
    elif parent_x_enc["type"] in ("ordinal", "nominal"):
        enc["bandPosition"] = 0.5
    return enc


def _text_mark_props(
    style: SupportTableStyle,
    align: Literal["left", "right"],
    dx: float | None = None,
    font_family_override: str | None = None,
) -> dict[str, Any]:
    """Common mark properties for row-text layers.

    `align` mirrors the label side so values lean toward their row labels:
    axis_y orient left (labels on the left) → "left"; orient right → "right".

    `dx`, when set (bar charts only), centers the number column on the band
    midpoint.  Sign convention: align=right → +dx (right edge at band_center +
    max_w/2); align=left → -dx (left edge at band_center - max_w/2).

    For line/area charts dx is None: the aligned edge sits straight on the
    mark's x position (bandPosition pins the anchor to the band center).

    `font_family_override`, when set, wins over `style.font.family` for the
    mark's own font — the column block's forced-tabular digit typeface (see
    `_column_cell_layer`). Row-strip callers never pass this: the row strip
    paints whatever `style.font.family` says, unchanged.
    """
    mark: dict[str, Any] = {
        "type": "text",
        "align": align,
        "baseline": "middle",
    }
    if dx is not None:
        mark["dx"] = -dx if align == "left" else dx
    mark.update(font_style_to_mark(style.font))
    if font_family_override is not None:
        mark["font"] = font_family_override
    return mark


def _row_text_layer(
    index: int,
    entry: Any,
    parent_x_enc: dict[str, Any],
    style: SupportTableStyle,
    axis_offset_value: float | None,
    spec_height: float,
    numerals: StripNumerals,
    value_format: FormatState = None,
    sampling_step: int = 1,
    dx: float | None = None,
    value_align: Literal["left", "right"] = "right",
    label_period_filter_expr: str | None = None,
) -> dict[str, Any]:
    """Emit a text layer for one support_table row (source or aggregate).

    When sampling_step > 1, a Vega-Lite window+filter chain is inserted so
    only every Nth x position renders a cell. Transform ordering matters:
    - For aggregate entries: [aggregate, (period_filter), window, filter, format_calc]
      The aggregate collapses multi-row-per-x data first; period filter then thins
      to label-period openers; sampling then thins further if needed.
    - For source entries (1 row per x, enforced by the ambiguous-aggregation guard
      in validate_support_table_against_data): [(period_filter), window, filter, format_calc]

    When label_period_filter_expr is set, a filter transform is inserted to
    keep only x-values that open a new label period (e.g. quarterly openers on
    a monthly-band axis). This prevents the support_table strip from rendering one
    cell per band when the axis labels are at a coarser cadence.
    """
    is_agg = isinstance(entry, ChartSupportTableAggregate)
    x_field = parent_x_enc["field"]
    # Internal field name for the formatted cell value.
    cell_name = f"__support_table_{index}"

    transforms: list[dict[str, Any]] = []
    if is_agg:
        vl_op = _AGG_OP_TO_VL[entry.aggregate]
        agg_name = f"{cell_name}_val"
        transforms.append(
            {
                "aggregate": [{"op": vl_op, "field": entry.source, "as": agg_name}],
                "groupby": [x_field],
            }
        )
        # Period filter after aggregate: one row per x, filter to label-period openers.
        if label_period_filter_expr is not None:
            transforms.append({"filter": label_period_filter_expr})
        # Sampling after period filter: thin further if still over the cap.
        if sampling_step > 1:
            transforms.extend(_sampling_transforms(x_field, sampling_step))
        if numerals.needs_drawn_index_window:
            transforms.extend(
                _drawn_index_transform(
                    x_field,
                    agg_name,
                    numerals.suffix_is_magnitude,
                    sort_ascending=not numerals.anchor.data_order,
                )
            )
        transforms.append(_vl_format_calc(agg_name, value_format, cell_name, numerals))
    else:
        # Source entry: the ambiguous-aggregation guard guarantees 1 row per x.
        # Period filter before sampling: thin to label-period openers first.
        if label_period_filter_expr is not None:
            transforms.append({"filter": label_period_filter_expr})
        if sampling_step > 1:
            transforms.extend(_sampling_transforms(x_field, sampling_step))
        if numerals.needs_drawn_index_window:
            transforms.extend(
                _drawn_index_transform(
                    x_field,
                    entry.source,
                    numerals.suffix_is_magnitude,
                    sort_ascending=not numerals.anchor.data_order,
                )
            )
        transforms.append(
            _vl_format_calc(entry.source, value_format, cell_name, numerals)
        )

    y_pixel = _row_y_pixel(index, style, axis_offset_value, spec_height)
    layer: dict[str, Any] = {
        "mark": _text_mark_props(style, value_align, dx=dx),
        "encoding": {
            "x": _shared_x_encoding(parent_x_enc, mark_is_bar=dx is not None),
            "y": {"value": y_pixel},
            "text": {"field": cell_name},
            # Opt out of inherited color encoding. Without this, VL sees own data
            # that lacks the series field, adds null to the categorical domain, and
            # null sorts first — consuming palette[0] and shifting every series mark
            # one slot up (the dark-companion off-by-one).
            "color": None,
        },
        "transform": transforms,
    }
    return layer


def _per_series_row_layers(
    row_start_index: int,
    entry: ChartSupportTablePerSeries,
    parent_x_enc: dict[str, Any],
    color_field: str | None,
    series_order: list[str],
    dark_fills: list[str] | None,
    style: SupportTableStyle,
    axis_offset_value: float | None,
    spec_height: float,
    spec_width: float | None,
    axis_label_padding: float,
    numerals: StripNumerals,
    value_format: FormatState = None,
    sampling_step: int = 1,
    dx: float | None = None,
    label_period_filter_expr: str | None = None,
    axis_y_orient: Literal["left", "right"] = "left",
    label_limit: float | None = None,
    suppress_series_labels: bool = False,
) -> list[dict[str, Any]]:
    """Emit cell + label layers for one ``per_series:`` support_table entry.

    When ``entry.by_measure`` is True, a single row reads the named field
    directly (no per-series expansion). Otherwise, emits one cell + one
    label layer per series in series_order, used to resolve dark companion
    label ink.

    Args:
        row_start_index: pixel-row index of the first series row.
        entry: ChartSupportTablePerSeries entry.
        parent_x_enc: parent chart's x-encoding dict.
        color_field: column name backing the color channel. Required for
            normal mode; may be None when ``entry.by_measure`` is True.
        series_order: Ordered series names.
        dark_fills: dark-companion label-ink color for each series in
            series_order (already resolved by the caller). When None, label
            fill is omitted.
        style: Resolved SupportTableStyle.
        axis_offset_value: the resolved chart's own baked support_table_axis_offset.
        spec_height: pixel height.
        spec_width: pixel width. Required (non-None) when
            ``axis_y_orient == "right"``; ignored for left-cap.
        axis_label_padding: resolved axis_y label padding, pixel offset for
            the label stub column — see ``_label_stub_left_layer``.
        sampling_step: thinning factor for dense x axes (>1 thins cells).
        axis_y_orient: Resolved y-axis orient — "right" or "left".
            Determines which label emitter fires and the mark's x-anchor
            and text-anchor.
        label_limit: mark.limit (pixels) applied to every label layer.
            When None, no limit is set. Pass _LABEL_STUB_LIMIT_PX to
            prevent labels from reaching into the legend zone.

    Returns:
        Flat list of Vega-Lite layer dicts (cell text + label text per series).

    Raises:
        RenderError (ERR-INPUT-INVALID) when ``axis_y_orient == "right"``
        but ``spec_width`` is None.
    """
    if axis_y_orient == "right" and spec_width is None:
        raise RenderError.from_code(
            ERR_INPUT_INVALID,
            message=(
                "attach_support_table requires the spec to carry an explicit "
                "width when right-cap labels are present on per_series entries — "
                "pixel-literal label positioning has no anchor otherwise."
            ),
        )

    # by_measure mode: one row reading the named field directly.
    if entry.by_measure:
        row_idx = row_start_index
        bm_cell_name = f"__support_table_{row_idx}"
        bm_transforms: list[dict[str, Any]] = []
        if label_period_filter_expr is not None:
            bm_transforms.append({"filter": label_period_filter_expr})
        if sampling_step > 1:
            bm_transforms.extend(
                _sampling_transforms(parent_x_enc["field"], sampling_step)
            )
        if numerals.needs_drawn_index_window:
            bm_transforms.extend(
                _drawn_index_transform(
                    parent_x_enc["field"],
                    entry.per_series,
                    numerals.suffix_is_magnitude,
                    sort_ascending=not numerals.anchor.data_order,
                )
            )
        bm_transforms.append(
            _vl_format_calc(entry.per_series, value_format, bm_cell_name, numerals)
        )
        bm_y_pixel = _row_y_pixel(row_idx, style, axis_offset_value, spec_height)
        # bm_transforms always carries at least the format_calc appended above,
        # so it's never empty — set it unconditionally, matching _row_text_layer.
        bm_cell_layer: dict[str, Any] = {
            "mark": _text_mark_props(style, axis_y_orient, dx=dx),
            "encoding": {
                "x": _shared_x_encoding(parent_x_enc, mark_is_bar=dx is not None),
                "y": {"value": bm_y_pixel},
                "text": {"field": bm_cell_name},
            },
            "transform": bm_transforms,
        }
        if suppress_series_labels:
            return [bm_cell_layer]
        # Label stub: use entry.label when set, else fall back to the field name.
        bm_label_text = entry.label if entry.label is not None else entry.per_series
        if axis_y_orient == "right":
            assert spec_width is not None  # pre-check above guarantees this
            bm_label_layer = _label_stub_right_layer(
                row_idx,
                bm_label_text,
                style=style,
                axis_offset_value=axis_offset_value,
                spec_height=spec_height,
                spec_width=spec_width,
                axis_label_padding=axis_label_padding,
                limit=label_limit,
            )
        else:
            bm_label_layer = _label_stub_left_layer(
                row_idx,
                bm_label_text,
                style=style,
                axis_offset_value=axis_offset_value,
                spec_height=spec_height,
                axis_label_padding=axis_label_padding,
                limit=label_limit,
            )
        return [bm_cell_layer, bm_label_layer]

    # Normal mode: one cell + label per series in series_order.
    # color_field is guaranteed non-None here: attach_support_table raises RenderError
    # before calling this function for normal-mode entries when color_field is None.
    resolved_dark_fills: list[str | None] = (
        list(dark_fills) if dark_fills is not None else [None] * len(series_order)
    )

    x_field = parent_x_enc["field"]
    layers: list[dict[str, Any]] = []

    for series_idx, series_value in enumerate(series_order):
        row_idx = row_start_index + series_idx
        cell_name = f"__support_table_{row_idx}"
        agg_name = f"{cell_name}_val"

        transforms: list[dict[str, Any]] = [
            {
                "aggregate": [{"op": "sum", "field": entry.per_series, "as": agg_name}],
                "groupby": [x_field, color_field],
            },
        ]
        # Filter to this series first, THEN apply period filter, THEN sample.
        # Sampling after the aggregate but before the per-series filter would
        # window over the (x, series) cross-product, where ties on x_field have
        # unspecified row-number order — so series A might keep x1/x3/x5
        # while series B keeps x2/x4/x6, with cells at inconsistent
        # x positions across the strip's rows. Sampling AFTER the
        # per-series filter gives each series an independent 1-row-per-x
        # input that thins consistently. Period filter goes between the
        # per-series filter and sampling: it operates on per-series 1-row-per-x
        # data and further thins to label-period openers.
        safe_val = str(series_value).replace("\\", "\\\\").replace("'", "\\'")
        transforms.append({"filter": f"datum['{color_field}'] === '{safe_val}'"})
        if label_period_filter_expr is not None:
            transforms.append({"filter": label_period_filter_expr})
        if sampling_step > 1:
            transforms.extend(_sampling_transforms(x_field, sampling_step))
        # After the per-series filter, so each series numbers its own drawn
        # cells and anchors its own leftmost one -- not whichever row sorted
        # first across the (x, series) cross-product.
        if numerals.needs_drawn_index_window:
            transforms.extend(
                _drawn_index_transform(
                    x_field,
                    agg_name,
                    numerals.suffix_is_magnitude,
                    sort_ascending=not numerals.anchor.data_order,
                )
            )
        transforms.append(_vl_format_calc(agg_name, value_format, cell_name, numerals))

        y_pixel = _row_y_pixel(row_idx, style, axis_offset_value, spec_height)
        cell_layer: dict[str, Any] = {
            "mark": _text_mark_props(style, axis_y_orient, dx=dx),
            "encoding": {
                "x": _shared_x_encoding(parent_x_enc, mark_is_bar=dx is not None),
                "y": {"value": y_pixel},
                "text": {"field": cell_name},
            },
            "transform": transforms,
        }
        layers.append(cell_layer)

        if suppress_series_labels:
            continue

        # Label layer — series name with companion-resolved fill ink.
        # Delegates to _label_stub_right_layer / _label_stub_left_layer so
        # the orient-derived geometry (x, align, limit) lives in one place.
        dark_fill = (
            resolved_dark_fills[series_idx]
            if series_idx < len(resolved_dark_fills)
            else None
        )
        if axis_y_orient == "right":
            assert spec_width is not None  # pre-loop check guarantees this
            label_layer = _label_stub_right_layer(
                row_idx,
                str(series_value),
                style=style,
                axis_offset_value=axis_offset_value,
                spec_height=spec_height,
                spec_width=spec_width,
                axis_label_padding=axis_label_padding,
                limit=label_limit,
            )
        else:
            label_layer = _label_stub_left_layer(
                row_idx,
                str(series_value),
                style=style,
                axis_offset_value=axis_offset_value,
                spec_height=spec_height,
                axis_label_padding=axis_label_padding,
                limit=label_limit,
            )
        if dark_fill is not None:
            label_layer["mark"]["fill"] = dark_fill
        layers.append(label_layer)

    return layers


def _divider_rule_layer(
    style: SupportTableStyle,
    axis_offset_value: float | None,
    spec_height: float,
) -> dict[str, Any]:
    """Strip-top divider rule (ADR-006: rule-only, no header text).

    ``x`` is explicitly nulled out — the same opt-out already used for
    ``yOffset``/``color`` elsewhere in this module. This layer's own inline
    ``data`` (``{{}}``) carries no x-field; an unset (rather than nulled) x
    channel would otherwise silently inherit the chart's shared top-level x
    encoding, evaluate to ``undefined``, and on an ordinal/nominal x scale
    (``sort: null``) get added as a real extra category — a phantom tick or
    bar, not merely a mispositioned rule.

    ``aria: False`` — a decorative rule with no data of its own otherwise
    inherits the chart's shared top-level tooltip/description encodings,
    announcing a screen reader label built from fields this layer never
    binds (garbage text, not a missing one).
    """
    mark: dict[str, Any] = {
        "type": "rule",
        "strokeWidth": style.divider.width,
        "aria": False,
    }
    if style.divider.color is not None:
        mark["stroke"] = style.divider.color
    return {
        "data": {"values": [{}]},
        "mark": mark,
        "encoding": {
            "y": {"value": _divider_y_pixel(style, axis_offset_value, spec_height)},
            "x": None,
        },
    }


def _row_rule_layer(
    index: int,
    style: SupportTableStyle,
    axis_offset_value: float | None,
    spec_height: float,
) -> dict[str, Any]:
    """Inter-row rule between row `index` and row `index + 1`.

    ``x`` is explicitly nulled out for the same reason ``_divider_rule_layer``
    nulls it: this layer's inline data carries no x-field, and an unset
    channel would otherwise silently inherit the shared top-level x encoding
    and add a phantom "undefined" category to an ordinal/nominal x domain.

    ``aria: False`` — same reason as ``_divider_rule_layer``: a decorative
    rule with no data of its own otherwise announces a screen reader label
    built from fields it never binds.
    """
    mark: dict[str, Any] = {
        "type": "rule",
        "strokeWidth": style.row.rule.width,
        "aria": False,
    }
    if style.row.rule.color is not None:
        mark["stroke"] = style.row.rule.color
    return {
        "data": {"values": [{}]},
        "mark": mark,
        "encoding": {
            "y": {
                "value": _inter_row_rule_y_pixel(
                    index, style, axis_offset_value, spec_height
                )
            },
            "x": None,
        },
    }


def _label_mark_props(style: SupportTableStyle, align: str) -> VLDict:
    """Common mark properties shared by left-stub and right-cap label layers.

    `align` is derived from the chart's resolved axis_y.orient at the call site:
    right-oriented → "left" (text-anchor start); left-oriented → "right" (text-anchor end).
    """
    mark: dict[str, Any] = {
        "type": "text",
        "align": align,
        "baseline": "middle",
    }
    mark.update(font_style_to_mark(style.label.font))
    return mark


def _label_stub_left_layer(
    index: int,
    label_text: str,
    style: SupportTableStyle,
    axis_offset_value: float | None,
    spec_height: float,
    axis_label_padding: float,
    limit: float | None = None,
) -> dict[str, Any]:
    """Left-gutter label layer anchored to the left y-axis tick column.

    x = -axis_y.labels.padding (same column as left-oriented y-axis tick labels,
    which extend leftward from x=0 with text-anchor end / align right).
    mark.limit caps the text width so VL's autosize does not shrink the plot.

    axis_label_padding is the caller's already-resolved ``resolved_chart.style.axis_y.labels.padding``
    (the fully chart-local-cascaded axis, not the board-level ``charts_style.axis_y``,
    which is authored-only/SkipInheritSlots and stays sparse until merged with axis).
    """
    x_pixel = -axis_label_padding
    mark = _label_mark_props(style, align="right")
    if limit is not None:
        mark["limit"] = limit
    return {
        "data": {"values": [{"__label": label_text}]},
        "mark": mark,
        "encoding": {
            "x": {"value": x_pixel},
            "y": {"value": _row_y_pixel(index, style, axis_offset_value, spec_height)},
            "text": {"field": "__label"},
            # Opt out of inherited color encoding. Without this, VL sees own data
            # that lacks the series field, adds null to the categorical domain, and
            # null sorts first — consuming palette[0] and shifting every series mark
            # one slot up (the dark-companion off-by-one).
            "color": None,
        },
    }


def _label_stub_right_layer(
    index: int,
    label_text: str,
    style: SupportTableStyle,
    axis_offset_value: float | None,
    spec_height: float,
    spec_width: float,
    axis_label_padding: float,
    limit: float | None = None,
) -> dict[str, Any]:
    """Right-cap label layer anchored to the right y-axis tick column.

    x = spec_width + axis_y.labels.padding (same column as right-oriented y-axis
    tick labels, which extend rightward from that anchor with text-anchor start /
    align left). mark.limit caps the text width so VL's autosize does not shrink
    the plot.

    axis_label_padding is the caller's already-resolved ``resolved_chart.style.axis_y.labels.padding``
    — see ``_label_stub_left_layer`` for why this isn't recomputed from ``charts_style.axis_y``.
    """
    x_pixel = spec_width + axis_label_padding
    mark = _label_mark_props(style, align="left")
    if limit is not None:
        mark["limit"] = limit
    return {
        "data": {"values": [{"__label": label_text}]},
        "mark": mark,
        "encoding": {
            "x": {"value": x_pixel},
            "y": {"value": _row_y_pixel(index, style, axis_offset_value, spec_height)},
            "text": {"field": "__label"},
            # Opt out of inherited color encoding — same fix as _label_stub_left_layer.
            "color": None,
        },
    }


def _sampling_transforms(x_field: str, step: int) -> list[dict[str, Any]]:
    """Vega-Lite window + filter transforms for strip-cell sampling.

    Assigns a 1-indexed row_number sorted ascending by x_field, then filters
    to rows where (row_number - 1) % step == 0, keeping the first row and
    every step-th row after it.
    """
    return [
        {
            "window": [{"op": "row_number", "as": "__support_table_row_index"}],
            "sort": [{"field": x_field, "order": "ascending"}],
        },
        {"filter": f"(datum.__support_table_row_index - 1) % {step} === 0"},
    ]


def attach_support_table(
    spec: dict[str, Any],
    *,
    support_table: ChartSupportTable | None,
    style: SupportTableStyle,
    charts_style: ResolvedChartDefaults | None = None,
    axis_offset_value: float | None = None,
    axis_label_padding: float,
    sampling_step: int = 1,
    entry_dx: list[float] | None = None,
    entry_numerals: Sequence[StripNumerals],
    series_order: list[str] | None = None,
    dark_fills: list[str] | None = None,
    label_period_filter_expr: str | None = None,
    axis_y_orient: Literal["left", "right"],
    suppress_series_labels: bool = False,
) -> dict[str, Any]:
    """Append attached-support-table layers to a Vega-Lite chart spec.

    Y-positioning is pixel-literal (`{"y": {"value": <px>}}`), anchored to
    the spec's explicit height. Vega-Lite scales `{"y": {"expr": ...}}`
    through the parent's y-scale rather than treating it as a pixel
    literal, so the spec must carry an explicit height (dbt charts'
    standard renderer always sets one).

    Args:
        spec: Base Vega-Lite chart spec (mark + encoding, or already a layer spec).
        support_table: Parsed ChartSupportTable block, or None for no-op.
        style: Resolved SupportTableStyle (already cascade-resolved).
        charts_style: ResolvedChartDefaults for x-axis label offset computation.
            Required when support_table is non-None.
        axis_label_padding: resolved_chart.style.axis_y.labels.padding — the
            chart-local-cascaded axis label padding used to anchor the label
            stub column. Callers read this off the resolved chart directly;
            it is not recomputed here.
        sampling_step: When > 1, prepend a Vega-Lite window+filter transform
            chain to each text-mark layer so only every Nth row renders a
            cell. The chart's bar/line/area base layer is not affected.
            Computed by validate_support_table_against_data for temporal/
            quantitative x axes that exceed ``chart_rendering.support_table.chart_support_table_max_x_ticks``.
        entry_dx: Per-entry dx half-widths (pixels) for band-centering on bar
            charts. When set, entry_dx[i] is applied as the VL mark ``dx``
            offset, centering the number column on the band midpoint. None (the
            default) means no dx — aligned edge sits straight on the mark.
        series_order: For per_series entries — ordered list of series names
            in the chart's stack/scale order. Required when any entry is a
            ChartSupportTablePerSeries. Callers resolve this from the parent
            chart's color encoding domain.
        dark_fills: For per_series entries — the already-resolved dark
            companion label-ink color for each series in series_order.
            When None, label fill is omitted.
        label_period_filter_expr: Vega filter expression that restricts strip
            cells to label-period openers. When set, each text-mark layer
            receives a ``{"filter": label_period_filter_expr}`` transform so
            only x-values that open a new label period (e.g. quarterly openers
            on a monthly-band axis) emit a cell. Computed by the render layer
            from the chart's encoding_time_unit / label_time_unit pair.
        axis_y_orient: Resolved y-axis orient — ``"right"`` or ``"left"``
            (line charts with right-edge endpoint labels). Determines which label
            emitter fires: right-oriented → label at ``spec_width +
            axis_y.labels.padding`` with ``align: left``; left-oriented → label at
            ``-axis_y.labels.padding`` with ``align: right``. Required — callers must
            resolve this from the chart's axis_y.orient before calling.

    Returns:
        New spec dict with attached layers appended and ``autosize`` set
        to ``pad`` so the outer SVG expands to include the strip rather
        than shrinking the plot. Padding is NOT touched — callers must
        reserve the strip's height themselves via
        ``support_table_strip_height``. Input spec not mutated.
    """
    if support_table is None or not support_table.entries:
        return spec

    if charts_style is None:
        raise RenderError.from_code(
            ERR_INPUT_INVALID,
            message=(
                "attach_support_table requires charts_style when support_table is "
                "non-None — the height-synthesis fallback needs it."
            ),
        )
    if style.position == "bottom" and axis_offset_value is None:
        raise RenderError.from_code(
            ERR_INPUT_INVALID,
            message=(
                "attach_support_table requires axis_offset_value when support_table "
                "is non-None and style.position is 'bottom' — pass the "
                "resolved chart's own support_table_axis_offset."
            ),
        )

    raw_width = spec.get("width")
    spec_width = (
        float(raw_width)
        if isinstance(raw_width, (int, float)) and raw_width > 0
        else None
    )

    spec_height = spec.get("height")
    if not isinstance(spec_height, (int, float)) or spec_height <= 0:
        # Optional callers (warnings detector, diagnostic scripts) reach
        # generate_vega_lite_spec without a board-level layout sizer, so the
        # spec arrives without an explicit height. Synthesize one from
        # charts_style.aspect_ratio clamped to [min_height, max_height] —
        # the same shape the layout sizer's static estimate uses. Width is
        # still required: it's the anchor we project the aspect ratio onto.
        if spec_width is None:
            raise RenderError.from_code(
                ERR_INPUT_INVALID,
                message=(
                    "attach_support_table requires the spec to carry an explicit "
                    "width when height is unset — no anchor exists to synthesize "
                    "a height default from."
                ),
            )
        synthesized_height = spec_width / charts_style.aspect_ratio
        synthesized_height = max(
            charts_style.min_height,
            min(charts_style.max_height, synthesized_height),
        )
        spec = {**spec, "height": synthesized_height}
        spec_height = synthesized_height

    # Compute the total number of visual rows.
    # by_measure entries each expand to 1 row; normal per_series entries expand
    # to N rows (one per series); source/aggregate entries are 1 row each.
    n_visual_rows = 0
    for entry in support_table.entries:
        if isinstance(entry, ChartSupportTablePerSeries):
            if entry.by_measure:
                n_visual_rows += 1
            else:
                n_visual_rows += len(series_order) if series_order else 1
        else:
            n_visual_rows += 1

    # Extract parent x-encoding before wrapping (wrap moves encoding into the base layer).
    #
    # Three shapes are accepted:
    #
    # 1. Standard top-level encoding — `spec["encoding"]["x"]` is set. The bulk
    #    of charts arrive here.
    # 2. Layered VL spec with x per-layer only (no top-level encoding.x) — an
    #    overlay chart's top-level `spec["encoding"]` may be empty/absent while
    #    every `spec["layer"][i]["encoding"]` carries the same x.
    #    We anchor against `layer[0]`'s x.
    # 3. Any other shape — raise ChartDataError. The strip needs an unambiguous
    #    anchor; silently picking layer-0's x when other layers disagree (or when
    #    resolve.scale.x is independent) would misalign the strip against the
    #    other layers' x-buckets, which is the "wrong result that looks right"
    #    failure mode the repo non-negotiables forbid.
    _raw_top_encoding = spec.get("encoding")
    top_encoding: dict[str, Any] = (
        _raw_top_encoding if isinstance(_raw_top_encoding, dict) else {}
    )
    if "x" in top_encoding:
        parent_x_enc = top_encoding["x"]
    else:
        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        layers_inline = spec.get("layer")
        if not isinstance(layers_inline, list) or not layers_inline:
            raise ChartDataError(
                "support_table attachment requires an x-encoding on the chart spec "
                "or its layers; none found."
            )
        if spec.get("resolve", {}).get("scale", {}).get("x") == "independent":
            raise ChartDataError(
                "support_table strip cannot anchor to a layer spec with "
                "resolve.scale.x == 'independent'; the layers have per-dataset "
                "x scales and the strip cannot anchor coherently."
            )
        layer_x_encs = [
            layer.get("encoding", {}).get("x")
            for layer in layers_inline
            if isinstance(layer, dict)
        ]
        non_null_x_encs = [enc for enc in layer_x_encs if isinstance(enc, dict)]
        if not non_null_x_encs:
            raise ChartDataError(
                "support_table attachment requires an x-encoding on the chart spec "
                "or its layers; none found."
            )
        first_x_field = non_null_x_encs[0].get("field")
        for enc in non_null_x_encs[1:]:
            if enc.get("field") != first_x_field:
                raise ChartDataError(
                    "support_table strip cannot anchor to a layer spec whose "
                    "per-layer x-encodings disagree on field; lift x to chart "
                    "level or remove support_table."
                )
        parent_x_enc = non_null_x_encs[0]
    color_field: str | None = None
    for color_encoding in _emitted_color_encodings(spec):
        emitted_field = color_encoding.get("field")
        if isinstance(emitted_field, str):
            color_field = emitted_field
            break

    out = _wrap_base_as_layer(spec)
    layers: list[dict[str, Any]] = list(out["layer"])
    base_layer_count = len(layers)

    if style.divider.width > 0:
        layers.append(_divider_rule_layer(style, axis_offset_value, spec_height))

    _label_stub_limit: float = _LABEL_STUB_LIMIT_PX

    # Track the current visual row index as we iterate entries. Source and
    # aggregate entries each consume 1 row; per_series entries consume N rows.
    visual_row_idx = 0
    for entry_idx, entry in enumerate(support_table.entries):
        row_dx = entry_dx[entry_idx] if entry_dx is not None else None
        row_numerals = entry_numerals[entry_idx]
        row_format = entry.format
        if isinstance(entry, ChartSupportTablePerSeries):
            if entry.by_measure:
                # by_measure: one row per entry, no series expansion needed.
                bm_layers = _per_series_row_layers(
                    visual_row_idx,
                    entry,
                    parent_x_enc=parent_x_enc,
                    color_field=None,
                    series_order=[],
                    dark_fills=None,
                    style=style,
                    axis_offset_value=axis_offset_value,
                    spec_height=spec_height,
                    spec_width=spec_width,
                    axis_label_padding=axis_label_padding,
                    value_format=row_format,
                    sampling_step=sampling_step,
                    dx=row_dx,
                    label_period_filter_expr=label_period_filter_expr,
                    axis_y_orient=axis_y_orient,
                    label_limit=_label_stub_limit,
                    suppress_series_labels=suppress_series_labels,
                    numerals=row_numerals,
                )
                layers.extend(bm_layers)
                if style.row.rule.width > 0 and visual_row_idx < n_visual_rows - 1:
                    layers.append(
                        _row_rule_layer(
                            visual_row_idx, style, axis_offset_value, spec_height
                        )
                    )
                visual_row_idx += 1
            else:
                if not series_order:
                    raise RenderError.from_code(
                        ERR_INPUT_INVALID,
                        message=(
                            "attach_support_table requires series_order when per_series "
                            "entries are present — callers must supply the ordered "
                            "series list resolved from the parent chart's color "
                            "encoding domain."
                        ),
                    )
                if color_field is None:
                    raise RenderError.from_code(
                        ERR_INPUT_INVALID,
                        message=(
                            "attach_support_table requires the spec to carry a color "
                            "encoding when per_series entries are present."
                        ),
                    )
                per_series_layers = _per_series_row_layers(
                    visual_row_idx,
                    entry,
                    parent_x_enc=parent_x_enc,
                    color_field=color_field,
                    series_order=series_order,
                    dark_fills=dark_fills,
                    style=style,
                    axis_offset_value=axis_offset_value,
                    spec_height=spec_height,
                    spec_width=spec_width,
                    axis_label_padding=axis_label_padding,
                    value_format=row_format,
                    sampling_step=sampling_step,
                    dx=row_dx,
                    label_period_filter_expr=label_period_filter_expr,
                    axis_y_orient=axis_y_orient,
                    label_limit=_label_stub_limit,
                    suppress_series_labels=suppress_series_labels,
                    numerals=row_numerals,
                )
                layers.extend(per_series_layers)
                n_series = len(series_order)
                # Inter-row rules: emit ONE per pair of adjacent visual rows
                # within the per_series block (n_series - 1 rules) plus one
                # trailing rule between this block and the next entry — same
                # cadence as source/aggregate rows, where every adjacent visual
                # pair gets a rule. Without the within-block rules, the strip
                # height accounting (which includes inter-row spacing for every
                # visual row) reserves gaps where no rules are drawn.
                if style.row.rule.width > 0:
                    last_in_block = visual_row_idx + n_series - 1
                    # Within-block rules: between each pair of adjacent series.
                    for inner_idx in range(visual_row_idx, last_in_block):
                        layers.append(
                            _row_rule_layer(
                                inner_idx, style, axis_offset_value, spec_height
                            )
                        )
                    # Trailing rule: between this block and the next entry.
                    if last_in_block < n_visual_rows - 1:
                        layers.append(
                            _row_rule_layer(
                                last_in_block, style, axis_offset_value, spec_height
                            )
                        )
                visual_row_idx += n_series
        else:
            row_layer = _row_text_layer(
                visual_row_idx,
                entry,
                parent_x_enc=parent_x_enc,
                style=style,
                axis_offset_value=axis_offset_value,
                spec_height=spec_height,
                value_format=row_format,
                sampling_step=sampling_step,
                dx=row_dx,
                value_align=axis_y_orient,
                label_period_filter_expr=label_period_filter_expr,
                numerals=row_numerals,
            )
            layers.append(row_layer)
            label = _default_support_table_label(entry)
            if label:
                if axis_y_orient == "right":
                    if spec_width is None:
                        raise RenderError.from_code(
                            ERR_INPUT_INVALID,
                            message=(
                                "attach_support_table requires the spec to carry an "
                                "explicit width when right-cap labels are present — "
                                "pixel-literal label positioning has no anchor "
                                "otherwise."
                            ),
                        )
                    layers.append(
                        _label_stub_right_layer(
                            visual_row_idx,
                            label,
                            style=style,
                            axis_offset_value=axis_offset_value,
                            spec_height=spec_height,
                            spec_width=spec_width,
                            axis_label_padding=axis_label_padding,
                            limit=_label_stub_limit,
                        )
                    )
                else:
                    layers.append(
                        _label_stub_left_layer(
                            visual_row_idx,
                            label,
                            style=style,
                            axis_offset_value=axis_offset_value,
                            spec_height=spec_height,
                            axis_label_padding=axis_label_padding,
                            limit=_label_stub_limit,
                        )
                    )
            if style.row.rule.width > 0 and visual_row_idx < n_visual_rows - 1:
                layers.append(
                    _row_rule_layer(
                        visual_row_idx, style, axis_offset_value, spec_height
                    )
                )
            visual_row_idx += 1

    out["layer"] = _disable_inherited_tooltip_description(layers, base_layer_count)
    # Strip rows live outside the plot area (pixel y > spec.height for bottom;
    # pixel y < 0 for top). Override the chart-wide `autosize: fit` default with
    # `pad` so the outer SVG grows to include the strip rather than shrinking the plot. Trade-off: under
    # `pad`, spec.width refers to the inner plot, so the outer SVG is wider
    # than the requested width by axis-y label + padding overhead (constant
    # regardless of requested width — see
    # test_render_pipeline_svg_width_overhead_independent_of_requested_width).
    # layout_sizing.py compensates symmetrically on both axes. The initial
    # render-first sizing pass (_make_data_aware_height_provider) renders once
    # to measure the width overhead, then re-renders with spec.width pre-shrunk.
    # Cols alignment (_align_cols_heights) has a hard target_height, so after
    # its alignment re-render it measures any height overhead and re-renders
    # with spec.height pre-shrunk too. Both axes fit the slot.
    out["autosize"] = {"type": "pad", "contains": "padding"}
    return out


def _aggregate_by_x(
    data: list[dict[str, Any]],
    source: str,
    x_field: str,
    op: str,
) -> list[float]:
    """Group data by x_field, apply aggregate op, return resulting values.

    Mirrors the VL aggregate transform used in _row_text_layer so that width
    measurements reflect the actual rendered strings.  op must be one of:
    sum, avg, min, max, median, count, count_distinct.

    count/count_distinct do not coerce source values to float — they count
    rows / distinct raw values so non-numeric sources are first-class.
    For sum/avg/min/max/median, float() is intentionally not suppressed:
    a non-numeric source with a numeric aggregate is a query authoring error
    and should surface as ValueError, not silently produce wrong dx.

    Both ops share the _AGG_OP_TO_VL enum in support_table_attachment.py;
    update that mapping when adding a new op here.
    """
    # count/count_distinct: key on raw values directly (no float coercion)
    if op in ("count", "count_distinct"):
        raw_groups: dict[Any, list[Any]] = defaultdict(list)
        for row in data:
            x_val = row.get(x_field)
            raw = row.get(source)
            if x_val is None:
                continue
            raw_groups[x_val].append(raw)
        if op == "count":
            return [float(len(vals)) for vals in raw_groups.values() if vals]
        return [
            float(len({v for v in vals if v is not None}))
            for vals in raw_groups.values()
            if vals
        ]

    # numeric ops: float() is intentionally not suppressed — non-numeric source
    # values here mean a query authoring error, not an expected no-op.
    groups: dict[Any, list[float]] = defaultdict(list)
    for row in data:
        x_val = row.get(x_field)
        raw = row.get(source)
        if x_val is None or raw is None:
            continue
        groups[x_val].append(float(raw))

    results: list[float] = []
    for vals in groups.values():
        if not vals:
            continue
        if op == "sum":
            results.append(sum(vals))
        elif op == "avg":
            results.append(sum(vals) / len(vals))
        elif op == "min":
            results.append(min(vals))
        elif op == "max":
            results.append(max(vals))
        elif op == "median":
            results.append(statistics.median(vals))
        else:
            raise ValueError(f"unknown aggregate op {op!r}")
    return results


def _aggregate_by_x_and_color(
    data: list[dict[str, Any]],
    source: str,
    x_field: str,
    color_field: str,
) -> list[float]:
    """Sum ``source`` grouped by (x, color), mirroring the per_series VL transform.

    The per_series text layer in :mod:`support_table_attachment` emits a sum
    aggregate keyed on ``[x_field, color_field]`` and a per-series filter,
    so each rendered cell shows that one series' value at that x. This
    helper produces the matching width-measurement input — sum-per-(x,
    series) — so dx reflects the *actual* rendered cell widths rather
    than the across-series sum.
    """
    groups: dict[tuple[Any, Any], list[float]] = defaultdict(list)
    for row in data:
        x_val = row.get(x_field)
        c_val = row.get(color_field)
        raw = row.get(source)
        if x_val is None or c_val is None or raw is None:
            continue
        groups[(x_val, c_val)].append(float(raw))
    return [sum(vals) for vals in groups.values() if vals]


def _numeric_column(data: ChartRenderData, source: str) -> list[float]:
    """Coerce a column's non-null values to float, skipping non-numeric ones.

    Bare source columns can hold strings (Vega renders them as labels); those
    simply contribute no measurable numeric width.
    """
    values: list[float] = []
    for row in data:
        raw = row.get(source)
        if raw is None:
            continue
        with contextlib.suppress(ValueError, TypeError):
            values.append(float(raw))
    return values


def _entry_values(
    support_table: ChartSupportTable,
    data: ChartRenderData,
    x_field: str | None,
    color_field: str | None,
) -> list[list[float]]:
    """The numeric values each entry's cells will render, one list per entry.

    The single place that answers "what does this row actually show" —
    per-series ``(x, color)`` aggregate, aggregate per x, or the raw source
    column. Cell widths, the shared magnitude, and the band-centering dx all
    read this one list, so a row cannot be measured against different numbers
    than it is scaled by.
    """
    per_entry: list[list[float]] = []
    for entry in support_table.entries:
        if isinstance(entry, ChartSupportTablePerSeries):
            source = entry.per_series
            if x_field is not None and color_field is not None:
                values = _aggregate_by_x_and_color(data, source, x_field, color_field)
            else:
                values = _numeric_column(data, source)
        elif isinstance(entry, ChartSupportTableAggregate) and x_field is not None:
            values = _aggregate_by_x(data, entry.source, x_field, entry.aggregate)
        else:
            values = _numeric_column(data, entry.source)
        per_entry.append(values)
    return per_entry


def resolved_pad_font_family(dt_style: SupportTableStyle) -> str:
    """The concrete font family the decimal-pad measurement runs against.

    ``dt_style.font.family`` is theme-populated but ``Optional`` at the type
    level (``FontStyle`` is a shared patch type reused at non-required
    contexts too); a fully-resolved ``effective_support_table_style`` always
    sets it, so ``None`` here means the theme cascade is misconfigured.
    Matches the family ``_entry_cell_widths``/dx already measure cell width
    against, row or column — one font, one computation, everywhere in this
    module.
    """
    family = dt_style.font.family
    if family is None:
        raise ValueError(
            "support_table.font.family must be set by the theme cascade; "
            "a missing value means the theme is misconfigured"
        )
    return family


def plain_numerals(
    support_table: ChartSupportTable,
    formats: dict[str, str] | None,
    font_family: str,
    entry_values: Sequence[Sequence[float]],
) -> list[StripNumerals]:
    """Numerals that declare nothing: every cell paints the author's own spec.

    The shape a strip had before this feature existed, and the one it keeps
    wherever the leftmost painted cell cannot be identified (no anchor, so no
    declare-once affix -- ``strip_numerals_for_values``'s SI shared-tier bake
    needs an anchor cell to paint the tier's affix on, and there is none
    here). A plain trim-`f` spec still gets its decimal_pad_table
    (``decimal_pad_table_for`` self-gates to ``()`` for every other spec
    shape). An SI-typed spec (predefined ``number``/an inline ``~s`` literal)
    gets the observed-depth pad instead (``_unscaled_decimal_pad``, the same
    one ``_magnitude_numerals``'s no-shared-tier refusals use, self-gated on
    ``column_shares_one_printed_unit``): no shared magnitude to divide by
    here either, but the column can still align each cell's own per-cell
    significant-figures output on the deepest one observed. Currency/percent
    are unaffected either way.
    """
    result: list[StripNumerals] = []
    for entry, values in zip(support_table.entries, entry_values, strict=True):
        resolved = resolve_format(entry.format, formats)
        pad_table = decimal_pad_table_for(resolved, font_family)
        if not pad_table and resolved:
            try:
                parsed = _d3_parse(resolved)
            except D3FormatError:
                parsed = None
            if parsed is not None and parsed.type == "s":
                pad_table = _unscaled_decimal_pad(values, resolved, font_family)
        result.append(
            StripNumerals(
                digit_spec=resolved,
                anchor=StripAnchor.nowhere(),
                decimal_pad_table=pad_table,
            )
        )
    return result


def _entry_numerals(
    support_table: ChartSupportTable,
    entry_values: list[list[float]],
    formats: dict[str, str] | None,
    font_family: str,
    anchor: StripAnchor,
    hang: Literal["left", "right"] = "left",
) -> list[StripNumerals]:
    """How each entry spells its numbers, one per entry.

    An entry with no ``format:`` at all still gets numerals: ``resolve_format``
    hands back the theme's own default spec, so the row is spelled by that and
    declares nothing — the same text it painted before.

    House rules (narrative register, declare-once) apply only to engine-predefined
    format names. A raw d3 literal or user alias opts out: the anchor is a house
    convention layered on top of the engine's own vocabulary, not something an
    author's own literal spec should be silently opted into. It still earns a
    decimal_pad_table when it is a plain trim-`f` spec (``decimal_pad_table_for``
    gates that itself) -- the raw-literal opt-out is from the SI/currency/percent
    anchor convention, not from decimal-point alignment.
    """
    result: list[StripNumerals] = []
    for entry, values in zip(support_table.entries, entry_values, strict=True):
        resolved = resolve_format(entry.format, formats)
        raw_format = (
            entry.format.spec
            if isinstance(entry.format, FormatConfig)
            else entry.format
        )
        if raw_format is None or raw_format not in PREDEFINED_NUMBER_NAMES:
            result.append(
                StripNumerals(
                    digit_spec=resolved,
                    anchor=StripAnchor.nowhere(),
                    decimal_pad_table=decimal_pad_table_for(resolved, font_family),
                )
            )
            continue
        result.append(
            strip_numerals_for_values(
                values, resolved, anchor, font_family=font_family, hang=hang
            )
        )
    return result


def _entry_cell_widths(
    support_table: ChartSupportTable,
    values_per_entry: Sequence[Sequence[float]],
    dt_style: SupportTableStyle,
    entry_numerals: Sequence[StripNumerals],
    conservative: bool = False,
) -> list[float]:
    """Max pixel width of each entry's rendered cells (one per entry).

    Single measurement path shared by the band-centering dx
    (``_compute_support_table_entry_dx``), the row strip's width-aware
    thinning budget (``max(...)`` at the post-pass), and the column block's
    own width reservation (``_entry_column_widths``).

    Measures the **bare** cell when the row declares its unit once: the anchor
    carries affixes no other cell does, so sizing the lane to it would shift
    every non-anchor cell to accommodate one. Measuring bare keeps the cells'
    shared edges in one lane and lets the wider anchor overhang into the gutter
    (right-axis) or into its own band (left-axis) — safe there because the
    anchor is always the strip's first drawn cell, so nothing sits to the side
    it overhangs into.

    ``conservative=True`` measures the **anchor-decorated** cell instead. A
    column has no such free gutter: every column's cells align right, so any
    affix always overhangs left, and every column but the first has another
    column's own cells sitting there. Sizing to the bare cell would let the
    anchor's affix bleed into the neighboring column — a value painted so
    it visually merges with its neighbor is exactly the "clipped-looking"
    value this measurement must never produce, so the column path measures
    every cell as if it could be the anchor.
    """
    font_size = dt_style.font.size
    if font_size is None:
        raise ValueError(
            "support_table.font.size must be set by the theme cascade; "
            "a missing value means the theme is misconfigured"
        )
    measurer = get_font_measurer(dt_style.font.family, numeric=True)
    widths: list[float] = []
    for index, entry in enumerate(support_table.entries):
        numerals = entry_numerals[index]
        max_w = 0.0
        for num in values_per_entry[index]:
            # An entry with no authored format paints the raw value, so measure
            # that -- its numerals exist but are never emitted (see
            # _vl_format_calc's own format_spec guard).
            if entry.format is None:
                formatted = str(num)
            elif conservative:
                formatted = numerals.anchor_text(num)
            else:
                formatted = numerals.bare_text(num)
            w = measurer.measure(formatted, font_size)
            if w > max_w:
                max_w = w
        widths.append(max_w)
    return widths


def _compute_support_table_entry_dx(
    support_table: ChartSupportTable,
    dt_style: SupportTableStyle,
    has_time_unit: bool,
    entry_numerals: Sequence[StripNumerals],
    values_per_entry: Sequence[Sequence[float]],
    x_type: str | None = None,
) -> list[float] | None:
    """Compute per-entry dx offsets for band-centered number lanes.

    Returns None when no offset is needed.  dx is only meaningful on band-scale
    axes (ordinal/nominal) where bandPosition:0.5 pins the text anchor to the
    band center.  For continuous axes (temporal without timeUnit, quantitative)
    there are no bands — a non-zero dx would shift the text away from the data
    point rather than centering it.

    When dx is applicable, returns a list of floats, one per entry:
    dx = max_formatted_width / 2 so that right-aligned text's right edge sits
    at band_center + max_w/2, centering the column over the bar.

    For ChartSupportTableAggregate entries, data is pre-aggregated per x_field
    before measuring widths so the dx reflects the actual rendered values
    (aggregates are typically wider than any individual raw row).

    Mirrors the center-on-midpoint invariant of _compute_lane_positions
    (table.py): the number lane is centered; decimals still align within it.
    """
    # dx centers the number column on the band midpoint (paired with the strip's
    # bandPosition:0.5 anchor). Applies to banded scales only: ordinal/nominal,
    # or temporal bucketed by timeUnit. Continuous axes (temporal without
    # timeUnit, quantitative) have no bands — a non-zero dx would shift text off
    # the data point rather than centering it.
    if not (has_time_unit or x_type in ("ordinal", "nominal")):
        return None

    # dx = max_formatted_width / 2 so right-aligned text's right edge sits at
    # band_center + max_w/2, centering the column over the bar. Same measurement
    # the thinning budget uses (per-series (x,color) aggregate, aggregate per x,
    # or raw source).
    entry_dx = [
        w / 2.0
        for w in _entry_cell_widths(
            support_table, values_per_entry, dt_style, entry_numerals
        )
    ]
    return entry_dx if any(dx > 0 for dx in entry_dx) else None


# Below this per-band width the strip mirrors the axis's cadence thinning:
# sparse data (e.g. a handful of points) shows every cell, dense data thins to
# the label-period openers. Distinct from the cell-width `min_band_px` used by
# the width-based sampling path — this is a label-band density floor.
_LABEL_STRIP_MIN_BAND_PX: float = 45.0


def _label_period_filter_expr(
    spec: dict[str, Any],
    resolved_chart_style: ResolvedChartDefaults,
    data: list[dict[str, Any]],
    x_field: str,
    chart_type: str,
    chart_local_axis_x: ResolvedAxisStyle,
    axis_x_width: float | None,
) -> str | None:
    """Return a Vega filter expression restricting support_table cells to label-period openers.

    Returns None when no filtering is needed: label cadence equals band cadence,
    no temporal axis, no coarser label cadence, or opens_label_period returns None
    for an unsupported encoding/label-cadence pair.

    Semantics for aggregate: entries — the filter shows the per-opener-band
    aggregate, not a re-aggregated sum across all bands in the period. This is
    correct for the primary case (one source row per band), and re-aggregating
    across bands would require a different VL groupby granularity that changes
    what the agg op operates on. The filter matches the opener list used by
    labelExpr so every visible axis label has exactly one support_table cell.

    Both the temporal path (timeUnit in spec x encoding) and the ordinal
    bucketed-time path build the same kind of filter: opens_label_period's
    UTC-month/date predicate on the raw x field. Ordinal axes carry no VL
    timeUnit marker, so that path uses the same visibility resolver as the
    overlap path. Axis values may use a coarser resolved label-format cadence;
    width-driven visibility thinning does not change them.

    ``axis_x_width`` is the plot's real x-axis-bearing width — ``spec``'s own
    ``"width"`` key is still the unreduced card width for an hconcat spec (the
    endpoint-label rail pane's shrink happens later, in the converter's
    overshoot correction), so this must be the caller's pre-reduced figure,
    not a value re-read from ``spec``. Passing the same figure
    ``resolve_axis_x_overlap`` decided its cadence against is what keeps this
    filter and the axis's visible labels in agreement.
    """
    enc_x = spec.get("encoding", {}).get("x", {})
    x_type = enc_x.get("type")

    # Chart-type-specific axis_x patch (Layer 4, theme-level, sparse) —
    # used below by the ordinal path's own time_unit precedence, which needs
    # the raw patch rather than the fully-cascaded axis.
    ct_style = getattr(resolved_chart_style, chart_type, None)
    ct_axis_x = getattr(ct_style, "axis_x", None) if ct_style is not None else None

    # Temporal path: timeUnit present in spec x encoding.
    enc_time_unit = enc_x.get("timeUnit")
    if enc_time_unit:
        # vl_time_unit emits "utc<grain>" (e.g. "utcyearmonth") in the spec.
        # Strip that prefix so internal helpers that work with dbt charts grain
        # names ("yearmonth", "yearweek", …) receive the right string.
        base_time_unit = enc_time_unit.removeprefix("utc")
        # chart_local_axis_x is resolved_chart.style.axis_x — the fully
        # chart-local-cascaded axis (including the Layer 4 chart-type patch
        # and any chart-local override) already baked at resolve time.
        axis_st = chart_local_axis_x
        format_tu = resolve_label_time_unit(base_time_unit, axis_st.labels.time_unit)
        if not format_tu:
            return None
        # Same density guard as the ordinal branch below: only thin via the
        # label-period-opener gate when bands are too dense to show every
        # data point without overlap (< ~45px/band). Sparser data (e.g. 5
        # weekly points) must show every cell. A continuous temporal x scale
        # has no per-bucket axis.values list to reuse, so recompute distinct
        # count straight from the data.
        x_distinct = {
            row.get(x_field)
            for row in data
            if x_field in row and row.get(x_field) is not None
        }
        spec_width = axis_x_width
        if not isinstance(spec_width, (int, float)) or spec_width <= 0:
            return None
        if not x_distinct or spec_width / len(x_distinct) >= _LABEL_STRIP_MIN_BAND_PX:
            return None
        safe_field = x_field.replace("'", "\\'")
        font = axis_st.labels.font
        measurer = get_font_measurer(font.family)
        dates = sorted(datetime.date.fromisoformat(str(v)[:10]) for v in x_distinct)
        band = (spec_width * resolved_chart_style.label_usable_ratio) / len(dates)
        visibility_tu, _ = resolve_temporal_label_visibility(
            dates,
            base_time_unit,
            format_tu,
            measurer,
            font.size,
            band,
            axis_st.fiscal_year_start_month,
            allow_skip=(
                axis_st.labels.overlap.skip
                if axis_st.labels.overlap is not None
                else False
            ),
            edge_labels_flushed=temporal_edge_labels_flushed("temporal", axis_st),
        )
        if visibility_tu == base_time_unit:
            return None
        gate = opens_label_period(
            base_time_unit,
            visibility_tu,
            v=f"toDate(datum['{safe_field}'])",
            month="utcmonth",
            date="utcdate",
            fiscal_year_start_month=axis_st.fiscal_year_start_month,
        )
        if gate is None:
            return None
        return gate

    # Ordinal bucketed-time path: no VL timeUnit marker exists (an ordinal
    # domain is plain strings), so resolve the encoding grain and visibility
    # independently — preferring an authored axis_x.time_unit/label.time_unit
    # (board-level ct_axis_x, else chart-local), falling back to detecting the
    # grain straight from the data when nothing is authored. This mirrors
    # resolve_axis_x_overlap's own precedence so both paths agree on the same
    # grain and land on the same shared render-time visibility decision.
    if x_type == "ordinal":
        authored_encoding_tu = ct_axis_x.time_unit if ct_axis_x is not None else None
        if authored_encoding_tu is None:
            authored_encoding_tu = chart_local_axis_x.time_unit
        raw_x_values = [
            row.get(x_field)
            for row in data
            if x_field in row and row.get(x_field) is not None
        ]
        ord_time_unit: str | None
        if authored_encoding_tu not in (None, "auto", "none"):
            ord_time_unit = authored_encoding_tu
        else:
            try:
                # Mirrors build_cartesian_x_encoding's own degrade-on-ValueError:
                # date-like-but-unparseable bucket strings (half-year, etc.) fall
                # back to no recognizable grain rather than raising here.
                ord_time_unit = detect_time_unit(raw_x_values)
            except ValueError:
                ord_time_unit = None
        if ord_time_unit is None:
            return None

        # raw_x_values (built above for detect_time_unit) is the same projection.
        ord_x_distinct = sorted(set(raw_x_values), key=str)
        spec_width = axis_x_width
        if (
            not isinstance(spec_width, (int, float))
            or spec_width <= 0
            or not ord_x_distinct
        ):
            return None

        # chart_local_axis_x is resolved_chart.style.axis_x — the fully
        # chart-local-cascaded axis (font included; see the temporal branch
        # above) already baked at resolve time.
        axis_st = chart_local_axis_x
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        layout = resolve_axis_x_overlap(
            axis_st,
            x_field,
            data,
            resolved_chart_style.label_usable_ratio,
            bucket_aligned_temporal=True,
            edge_labels_flushed=False,
            chart_width=spec_width,
        )
        visibility_tu = layout.visibility_time_unit or layout.format_time_unit
        if visibility_tu == ord_time_unit:
            return None
        # Same density guard as the temporal path above: only filter the strip
        # when cells are too narrow to show every one without crowding — the
        # cadence walk already decided the LABEL cadence; this decides whether
        # the support_table should mirror that thinning.
        if spec_width / len(ord_x_distinct) >= _LABEL_STRIP_MIN_BAND_PX:
            return None
        safe_field = x_field.replace("'", "\\'")
        gate = opens_label_period(
            ord_time_unit,
            visibility_tu,
            axis_st.fiscal_year_start_month,
            v=f"toDate(datum['{safe_field}'])",
            month="utcmonth",
            date="utcdate",
        )
        if gate is None:
            return None
        return gate

    return None


def _chart_dark_companion_palette(
    resolved_chart: _CartesianResolvedChartFields,
    charts_style: ResolvedChartDefaults,
) -> tuple[str, ...]:
    """The dark-companion ink palette THIS chart's own marks would use.

    Bar/line/area resolve a per-chart ``style.series_label.dark_companion_palette``
    positionally aligned with ``chart.palette`` (``_resolved_series_label``,
    ``compile/resolve/chart/_palette.py``) -- if the chart authors a
    chart-local ``style.color.categorical.palette`` override, THIS is the ink
    that stays paired with it. Falling back to ``charts_style`` (the board's
    own default) would hand the strip a companion for a palette the chart
    isn't even painting with.

    Scatter/heatmap carry no ``series_label`` slice (data_table's per_series
    strip has no real use case there today), so they fall back to the board
    default -- the same value every other cartesian family used before this
    function existed.
    """
    if isinstance(
        resolved_chart, (ResolvedBarChart, ResolvedLineChart, ResolvedAreaChart)
    ):
        return resolved_chart.style.series_label.dark_companion_palette
    return charts_style.dark_companion_palette


def _series_order_strip(
    chart_type: str,
    resolved_chart: _CartesianResolvedChartFields,
    data: list[dict[str, Any]],
    color_field: str,
    distinct: set[str],
    stacked_series_order: list[str] | None,
) -> list[str]:
    """Return series names for the support table strip in row-index order.

    The default strip position is ``top`` (strip above the chart).  For
    ``position: top``, strip row 0 sits at the VISUAL BOTTOM of the strip
    (y ≈ 0, closest to the chart), so the strip reads top-to-bottom starting
    from the LAST row index.  To have the strip read top-to-bottom with the
    visual-top series first (matching how you read the chart top-to-bottom),
    series_order[0] must be the visual-BOTTOM series (largest sum / alpha-first /
    first-encounter for bars; lowest last-x y for lines) and series_order[N]
    must be the visual-TOP series.

    Bar (stacked, value order): series_order[0] = largest global sum (baseline).
    Bar (stacked, alphabetical): series_order[0] = alpha-FIRST (A, at baseline).
    Bar (stacked, data): series_order[0] = first-encountered (at baseline).
    Bar (stacked, degenerate -- color 1:1 with x, unauthored stack_order):
      series_order[0] = the x category's own rendered order (``chart.sort``
      when authored, else first-encountered), matching the same override
      ``emitters.bar`` applies to the chart's own legend/z-order -- a
      one-segment-per-bar "stack" has no real total to rank by, so the strip
      must not disagree with the chart it annotates. Sourced from
      ``stacked_series_order`` (below), never computed in this function.
    Bar (non-stacked / grouped): alphabetical ascending.
    Line / non-stacked area: series_order[0] = lowest last-x y (bottom of chart).
    Stacked area: series_order[0] = largest global sum (baseline).

    ``stacked_series_order`` is the emitter's OWN already-computed baseline
    order for a stacked bar (``ChartSpec.stacked_series_order``, stamped
    through as ``$df_stacked_series_order`` -- see ``vega_lite.py``),
    returned verbatim when present: computing that verdict a SECOND time
    here, from this function's own (possibly differently-shaped, e.g.
    pre-gap-fill) ``data``, is exactly the two-predicates-can-disagree
    failure this parameter exists to close. None for a stacked-bar shape
    the emitter doesn't stamp one for (an ordinal color column -- VL sorts
    that itself, no order channel is ever emitted; a wide-measure bar,
    which computes a baseline order but doesn't yet carry it onto the
    spec) -- there the value-order computation below reproduces the same
    total-ranked default VL itself applies with no order channel present
    (see ``sorted_series_by_stack_order``), matching this function's own
    pre-existing behavior for those shapes.
    """
    x_field = resolved_chart.x
    raw_y = resolved_chart.y
    y_field = raw_y if isinstance(raw_y, str) else None
    stack = (
        resolved_chart.stack
        if isinstance(resolved_chart, (ResolvedBarChart, ResolvedAreaChart))
        else None
    )

    if chart_type == "bar":
        is_gb = stack == "none" and effective_color_field(resolved_chart) is not None
        is_stacked = stack != "none" and not is_gb
        if not is_stacked:
            return sorted(distinct)
        if stacked_series_order is not None:
            return stacked_series_order
        assert isinstance(resolved_chart, ResolvedBarChart)
        if y_field is None:
            return sorted(distinct)
        return sorted_series_by_stack_order(
            sorted(distinct),
            data,
            color_field,
            resolved_chart.style.stack_order,
            y_field=y_field,
        )

    is_stacked_area = chart_type == "area" and stack not in (None, "none")
    if is_stacked_area:
        if y_field is None:
            return sorted(distinct)
        return sorted_series_by_stack_order(
            list(distinct), data, color_field, None, y_field=y_field
        )

    # Line / non-stacked area: highest last-x y at chart top → series_order[N].
    # series_order[0] = lowest last-x y (bottom of chart).
    if not x_field or not y_field or not data:
        return sorted(distinct)

    last_x = None
    for row in data:
        v = row.get(x_field)
        if v is None:
            continue
        if last_x is None or v > last_x:
            last_x = v

    last_y: dict[str, float] = dict.fromkeys(distinct, 0.0)
    if last_x is not None:
        for row in data:
            if row.get(x_field) != last_x:
                continue
            s = row.get(color_field)
            y = row.get(y_field)
            if s is None or y is None:
                continue
            with contextlib.suppress(ValueError, TypeError):
                last_y[str(s)] = float(y)

    # Ascending: lowest y = series_order[0]; highest y (chart top) = series_order[N].
    return sorted(distinct, key=lambda s: (last_y[s], s))


def _emitted_color_encodings(spec: VLDict) -> list[VLDict]:
    """Every color encoding dict anywhere in ``spec``'s layer tree.

    Recurses into nested ``layer`` lists so a dual-axis entry that
    ``nest_zero_rule`` wrapped in an extra ``mark="layered"`` level (no
    encoding of its own, the real one sitting one level deeper) still
    contributes its color encoding. A shallow single-level scan would only
    see the wrapper's empty encoding and silently drop it.
    """
    color_encodings: list[VLDict] = []
    top_encoding = spec.get("encoding")
    if isinstance(top_encoding, dict):
        top_color = top_encoding.get("color")
        if isinstance(top_color, dict):
            color_encodings.append(top_color)
    layers = spec.get("layer")
    if isinstance(layers, list):
        for layer in layers:
            if isinstance(layer, dict):
                color_encodings.extend(_emitted_color_encodings(layer))
    return color_encodings


def _first_layer_encoding(layer: VLDict) -> VLDict | None:
    """The encoding dict that actually describes ``layer``'s own marks.

    Descends into a nested ``layer[0]`` when ``layer`` is itself a wrapper
    with no direct encoding of its own (see ``nest_zero_rule``: a dual-axis
    zero rule nests one level inside the entry whose scale it shares).
    """
    enc = layer.get("encoding")
    if isinstance(enc, dict) and enc:
        return enc
    nested = layer.get("layer")
    if isinstance(nested, list) and nested and isinstance(nested[0], dict):
        return _first_layer_encoding(nested[0])
    return enc if isinstance(enc, dict) else None


def _suppress_series_legend(
    spec: dict[str, Any],  # type-state: explicit_any — VL fragment
) -> None:
    """Null out the color legend in place.

    Used when per_series entries already show series names via row labels
    (strip) or column headers (column block), making a side legend redundant
    ink. ``attach_support_table``/``attach_support_table_columns`` wrap the
    spec as a layered spec (``_wrap_base_as_layer``), moving encoding into
    ``spec["layer"][0]`` for single-mark (V1) specs. V2 emitters place shared
    channel encodings at the spec top-level before wrapping, so the color
    encoding may be at ``spec["encoding"]["color"]`` instead of in layer[0],
    or one level deeper still, when layer[0] is itself a dual-axis
    zero-rule wrapper (see ``_first_layer_encoding``). Check all locations;
    the first data-bound color wins.
    """
    layers = spec.get("layer")
    base_enc = (
        _first_layer_encoding(layers[0])
        if isinstance(layers, list) and layers and isinstance(layers[0], dict)
        else None
    )
    color_enc = base_enc.get("color") if isinstance(base_enc, dict) else None
    if not isinstance(color_enc, dict) or color_enc.get("field") is None:
        # Fall back to top-level encoding (V2 grouped/stacked bar path).
        top_enc = spec.get("encoding")
        color_enc = top_enc.get("color") if isinstance(top_enc, dict) else None
    if isinstance(color_enc, dict):
        color_enc["legend"] = None


def _shared_y_band_encoding(
    parent_y_enc: dict[str, Any],  # type-state: explicit_any — VL fragment
) -> dict[str, Any]:  # type-state: explicit_any — VL fragment
    """Shared y-encoding for column cell/header layers — the transpose of
    ``_shared_x_encoding``.

    Copies field, type, timeUnit (when present), and sort (when present)
    from the parent chart's y-encoding so column layers share the same
    scale AND the same category order -- see ``_shared_x_encoding``'s
    docstring for why omitting ``sort`` here silently drags a `chart.sort`-
    authored, otherwise-correctly-sorted bar back to alphabetical once a
    support_table's own layers join the same scale. The gate restricts the
    column path to a vertical category axis, which today is reachable only
    via a horizontal bar's ordinal/nominal band scale on y, so
    bandPosition:0.5 (band-center anchor) always applies — mirroring how a
    bar's own mark centers on its band.
    """
    enc: dict[str, Any] = {}  # type-state: explicit_any — VL fragment
    enc["field"] = parent_y_enc["field"]
    enc["type"] = parent_y_enc["type"]
    if "sort" in parent_y_enc:
        enc["sort"] = parent_y_enc["sort"]
    if "timeUnit" in parent_y_enc:
        enc["timeUnit"] = parent_y_enc["timeUnit"]
    enc["bandPosition"] = 0.5
    return enc


def _column_block_plot_gutter(
    style: SupportTableStyle, axis_label_padding: float
) -> float:
    """Extra clearance reserved between the column block and the plot edge.

    ``row.padding.horizontal`` alone (the column's own trailing padding) reads
    fine for a single label but not for a solid field of tabular digits
    running the full plot height: an axis label is one short string with
    white space above and below it, while the column block sits flush against
    the marks for its entire span, so it needs the same additional standoff a
    neighboring axis label would get. The gutter is therefore the column's
    own reserved padding *plus* the resolved axis's own label padding
    (``resolved_chart.style.axis_y.labels.padding``) — composed from the two
    existing theme tokens rather than a new constant, so a theme that tunes
    either one gets a proportional result. Applied identically on both
    placements' plot-facing edge: for ``position: "left"`` that is the last
    column's right edge; for ``position: "right"`` it is column 0's own left
    edge.
    """
    return style.row.padding.horizontal + axis_label_padding


def _column_edges(
    widths: Sequence[float],
    position: Literal["left", "right"],
    spec_width: float,
    plot_gutter: float = 0.0,
) -> list[tuple[float, float]]:
    """Left/right pixel edges for each column, in authored order.

    ``position == "left"``: the block spans ``[-(total_width + plot_gutter),
    -plot_gutter]`` — column 0 is leftmost (furthest from the plot, adjacent
    to the category labels), the last column's right edge sits ``plot_gutter``
    pixels short of ``x=0`` (the plot's own left edge). ``position ==
    "right"``: the block spans ``[spec_width + plot_gutter, spec_width +
    plot_gutter + total_width]`` — column 0 is leftmost (closest to the
    plot), the last column is furthest right. Column order is always
    authored order, left to right on screen, on both sides. ``plot_gutter``
    (see ``_column_block_plot_gutter``) is reserved identically on both sides.
    """
    total = sum(widths)
    cursor = -(total + plot_gutter) if position == "left" else spec_width + plot_gutter
    edges: list[tuple[float, float]] = []
    for w in widths:
        edges.append((cursor, cursor + w))
        cursor += w
    return edges


def _column_cell_transforms(
    entry: ChartSupportTableSource
    | ChartSupportTableAggregate
    | ChartSupportTablePerSeries,
    category_field: str,
    color_field: str | None,
    series_value: str | None,
    value_format: FormatState,
    cell_name: str,
    numerals: StripNumerals,
) -> list[dict[str, Any]]:  # type-state: explicit_any — VL fragment
    """Value-producing transform chain for one column cell.

    The transpose of the row strip's per-entry-type chains (``_row_text_layer``,
    ``_per_series_row_layers``) grouped by the category field instead of x, with
    no sampling or period-filter stage: the gate restricts the column path to a vertical
    category (band) axis, which is never thinned — over-cap cardinality is a
    hard error (``validate_support_table_against_data``'s ordinal/nominal
    branch), so the thinning branch used on the row path is unreachable here.

    ``numerals.needs_drawn_index_window`` inserts the same rank-the-drawn-cells
    window the row strip uses, grouped by ``category_field`` instead of x — its
    omission here was the bug: without it a declare-once magnitude row's anchor
    test never fires, so no cell ever paints the affix and every cell divides by
    the shared tier with nothing naming it (e.g. a $120,588 total prints "120").
    """
    if isinstance(entry, ChartSupportTableAggregate):
        vl_op = _AGG_OP_TO_VL[entry.aggregate]
        agg_name = f"{cell_name}_val"
        transforms: list[dict[str, Any]] = [  # type-state: explicit_any — VL fragment
            {
                "aggregate": [{"op": vl_op, "field": entry.source, "as": agg_name}],
                "groupby": [category_field],
            },
        ]
        if numerals.needs_drawn_index_window:
            transforms.extend(
                _drawn_index_transform(
                    category_field,
                    agg_name,
                    numerals.suffix_is_magnitude,
                    sort_ascending=not numerals.anchor.data_order,
                )
            )
        transforms.append(_vl_format_calc(agg_name, value_format, cell_name, numerals))
        return transforms
    if isinstance(entry, ChartSupportTablePerSeries):
        if entry.by_measure:
            transforms = []
            if numerals.needs_drawn_index_window:
                transforms.extend(
                    _drawn_index_transform(
                        category_field,
                        entry.per_series,
                        numerals.suffix_is_magnitude,
                        sort_ascending=not numerals.anchor.data_order,
                    )
                )
            transforms.append(
                _vl_format_calc(entry.per_series, value_format, cell_name, numerals)
            )
            return transforms
        if color_field is None or series_value is None:
            raise RenderError.from_code(
                ERR_INPUT_INVALID,
                message=(
                    "attach_support_table_columns requires color_field and a "
                    "series_value for a per_series entry that is not by_measure."
                ),
            )
        agg_name = f"{cell_name}_val"
        safe_val = str(series_value).replace("\\", "\\\\").replace("'", "\\'")
        transforms = [
            {
                "aggregate": [{"op": "sum", "field": entry.per_series, "as": agg_name}],
                "groupby": [category_field, color_field],
            },
            {"filter": f"datum['{color_field}'] === '{safe_val}'"},
        ]
        if numerals.needs_drawn_index_window:
            transforms.extend(
                _drawn_index_transform(
                    category_field,
                    agg_name,
                    numerals.suffix_is_magnitude,
                    sort_ascending=not numerals.anchor.data_order,
                )
            )
        transforms.append(_vl_format_calc(agg_name, value_format, cell_name, numerals))
        return transforms
    transforms = []
    if numerals.needs_drawn_index_window:
        transforms.extend(
            _drawn_index_transform(
                category_field,
                entry.source,
                numerals.suffix_is_magnitude,
                sort_ascending=not numerals.anchor.data_order,
            )
        )
    transforms.append(_vl_format_calc(entry.source, value_format, cell_name, numerals))
    return transforms


def _column_cell_layer(
    parent_y_enc: dict[str, Any],  # type-state: explicit_any — VL fragment
    style: SupportTableStyle,
    x_pixel: float,
    cell_name: str,
    transforms: list[dict[str, Any]],  # type-state: explicit_any — VL fragment
    yoffset_enc: dict[str, Any] | None = None,  # type-state: explicit_any — VL fragment
) -> dict[str, Any]:  # type-state: explicit_any — VL fragment
    """One value-cell text layer: pixel-literal x, category on the shared y-band.

    ``yOffset`` defaults to nulled out — the same opt-out already used for
    ``color`` above. A grouped bar's parent spec carries a shared ``yOffset``
    channel (one sub-band per series) that a layer would otherwise inherit;
    nulling it centers the cell on the category's own band, the transpose of
    a per_series row, which reads across as one table row, not a diagonal
    staircase. A grouped bar's single-column per_series entry is the one
    caller that opts back IN, passing the parent's own ``yOffset`` encoding
    verbatim so each series' cell lands on that series' own sub-band —
    vertically aligned with the bar it describes — instead of the category
    band center.

    The column block always paints digits in a tabular typeface — a column of
    numbers needs vertical digit alignment the way the row strip never does
    (each row-strip cell sits alone at its own x position). ``numeric=True``
    is already what ``_entry_column_widths`` measures against
    (``get_font_measurer(..., numeric=True)`` resolves to the same tabular-
    or-Source-Serif typeface ``_table_numeric_cell_font`` picks), so forcing the
    same typeface here is what makes the painted glyphs match the reserved
    width, not a second, independent decision.
    """
    return {
        "mark": _text_mark_props(
            style,
            "right",
            font_family_override=_table_numeric_cell_font(style.font.family),
        ),
        "encoding": {
            "y": _shared_y_band_encoding(parent_y_enc),
            "yOffset": yoffset_enc,
            "x": {"value": x_pixel},
            "text": {"field": cell_name},
            "color": None,
        },
        "transform": transforms,
    }


def _column_header_layer(
    style: SupportTableStyle,
    x_pixel: float,
    header_text: str,
    header_y_pixel: float,
    fill_override: str | None = None,
) -> dict[str, Any]:  # type-state: explicit_any — VL fragment
    """Column header text layer, right-aligned at the column's own right
    edge — the same edge its value cells align to, so header and values
    share one right edge and the header can never collide (the column is
    always at least as wide as its header).

    ``yOffset``/``color`` are explicitly nulled out (this layer's own inline
    ``data`` carries neither field) — same opt-out as ``_column_cell_layer``,
    defensively applied here too so a header never picks up a per-series
    sub-band offset from the parent spec's shared encoding.

    ``fill_override``: when set, replaces the header's default mark ink —
    a linked mark series' own companion color, standing in for a legend
    swatch (see ``_mark_series_header_fills``).
    """
    mark = _label_mark_props(style, align="right")
    if fill_override is not None:
        mark["fill"] = fill_override
    return {
        "data": {"values": [{"__header": header_text}]},
        "mark": mark,
        "encoding": {
            "x": {"value": x_pixel},
            "y": {"value": header_y_pixel},
            "yOffset": None,
            "text": {"field": "__header"},
            "color": None,
        },
    }


def _column_rule_layer(
    style: SupportTableStyle, boundary_x: float
) -> dict[str, Any]:  # type-state: explicit_any — VL fragment
    """Vertical rule between two adjacent columns — the transpose of
    ``_row_rule_layer``'s horizontal inter-row rule.

    Pixel-literal ``x``, ``y`` explicitly nulled — a rule mark with an unset
    channel spans the full extent of the other, so this spans the plot's
    full height the same way the row rule (only ``y`` set) spans its full
    width. ``y`` must be nulled rather than merely omitted: this layer's
    inline data carries no category field, and an unset channel would
    otherwise silently inherit the chart's shared top-level y encoding (the
    category field), evaluate to ``undefined``, and get added as a real
    extra category on the ordinal/nominal y scale — a phantom band, not
    merely a mispositioned rule (the same defect fixed in
    ``_row_rule_layer``/``_divider_rule_layer`` for their unused x channel).

    ``boundary_x`` is the shared coordinate between the two columns'
    contiguous edges (see ``_column_edges``) — but every column's own text
    right-aligns exactly AT that same coordinate (zero slack on that side;
    ``_entry_column_widths`` reserves ``2 * row.padding.horizontal`` per
    column, but ``_column_cell_layer``/``_column_header_layer`` anchor the
    text to the column's own right edge, so all of that padding lands as
    slack on the LEFT of the text, none on the right). Painting the rule at
    ``boundary_x`` itself therefore lands it on top of the left-hand
    column's own glyphs. The rule is offset one ``row.padding.horizontal``
    past the boundary — clear of the left-hand column's text (which has zero
    slack there) and still short of the right-hand column's own leftmost
    glyph (which has at least one full ``row.padding.horizontal`` of slack
    on its left, by the same reservation).

    ``aria: False`` — same reason as ``_row_rule_layer``/``_divider_rule_layer``:
    a decorative rule with no data of its own otherwise announces a screen
    reader label built from fields it never binds.
    """
    x_pixel = boundary_x + style.row.padding.horizontal
    mark: dict[str, Any] = {  # type-state: explicit_any — VL fragment
        "type": "rule",
        "strokeWidth": style.row.rule.width,
        "aria": False,
    }
    if style.row.rule.color is not None:
        mark["stroke"] = style.row.rule.color
    return {
        "data": {"values": [{}]},
        "mark": mark,
        "encoding": {"x": {"value": x_pixel}, "y": None},
    }


def _entry_column_widths(
    support_table: ChartSupportTable,
    values_per_entry: Sequence[Sequence[float]],
    dt_style: SupportTableStyle,
    entry_numerals: Sequence[StripNumerals],
    series_order: list[str] | None,
    single_column_per_series: bool,
) -> list[float]:
    """Per-entry column width: ``max(widest formatted value, header text)
    + row.padding.horizontal * 2``.

    One width per *entry*, not per expanded visual column: a ``per_series``
    entry's N series-columns share one width, sized to the widest header
    (series name) or value across all of them — the entry's own measured
    value width already pools every series (``_entry_values``'s per-(x,
    color) aggregate), so this only adds the header side of the max.

    ``single_column_per_series``: a grouped bar's per_series entry paints a
    single column (see ``attach_support_table_columns``), so its header is
    the entry's own label, not the widest series name — a stacked bar's
    per_series entry still measures against every series name, one per
    column it will actually paint.

    ``conservative=True``: a column has no free gutter for a declare-once
    anchor cell to overhang into (see ``_entry_cell_widths``), so every cell
    is measured as if it could be the one that paints the affix.
    """
    value_widths = _entry_cell_widths(
        support_table, values_per_entry, dt_style, entry_numerals, conservative=True
    )
    font_size = dt_style.label.font.size
    if font_size is None:
        raise RenderError.from_code(
            ERR_INPUT_INVALID,
            message=(
                "support_table.label.font.size must be set by the theme "
                "cascade; a missing value means the theme is misconfigured."
            ),
        )
    measurer = get_font_measurer(dt_style.label.font.family, numeric=False)
    widths: list[float] = []
    for entry, value_w in zip(support_table.entries, value_widths, strict=True):
        if isinstance(entry, ChartSupportTablePerSeries) and not entry.by_measure:
            if single_column_per_series:
                headers = [_per_series_column_header(entry)]
            else:
                headers = list(_series_names_or_empty(series_order))
        elif isinstance(entry, ChartSupportTablePerSeries):
            headers = [entry.label if entry.label is not None else entry.per_series]
        else:
            headers = [_default_support_table_label(entry)]
        header_w = max((measurer.measure(h, font_size) for h in headers), default=0.0)
        widths.append(max(value_w, header_w) + 2.0 * dt_style.row.padding.horizontal)
    return widths


def _visual_column_widths_and_headers(
    support_table: ChartSupportTable,
    entry_widths: Sequence[float],
    series_order: list[str] | None,
    single_column_per_series: bool,
) -> tuple[list[float], list[str]]:
    """Expand per-entry widths/headers into per-visual-column lists.

    The transpose of the row strip's ``visual_row_idx`` bookkeeping in
    ``attach_support_table``: a ``per_series`` entry that is not ``by_measure``
    expands into one column per series (header = series name) on a stacked
    bar, or stays a single column (header = the entry's own label) on a
    grouped bar — see ``single_column_per_series``; every other entry always
    contributes exactly one column.
    """
    widths: list[float] = []
    headers: list[str] = []
    for entry, width in zip(support_table.entries, entry_widths, strict=True):
        if isinstance(entry, ChartSupportTablePerSeries) and not entry.by_measure:
            if single_column_per_series:
                widths.append(width)
                headers.append(_per_series_column_header(entry))
            else:
                for series_value in _series_names_or_empty(series_order):
                    widths.append(width)
                    headers.append(str(series_value))
        elif isinstance(entry, ChartSupportTablePerSeries):
            widths.append(width)
            headers.append(entry.label if entry.label is not None else entry.per_series)
        else:
            widths.append(width)
            headers.append(_default_support_table_label(entry))
    return widths, headers


def _widen_first_layer_y_axis_padding(
    spec: dict[str, Any],  # type-state: explicit_any — VL fragment
    added_width: float,
) -> dict[str, Any]:  # type-state: explicit_any — VL fragment
    """Mirror the shared y-axis labelPadding widening onto the base layer's
    own duplicate y-encoding, on a ``chart.layers``-composed spec.

    ``render_cartesian_overlay`` (``emitters/_overlay.py``) lifts a copy of
    the category encoding to the spec's shared top-level ``encoding`` (which
    the widening above patches) but leaves the ORIGINAL, still-unwidened
    category encoding on ``layer[0]`` (the base bar) untouched -- the two
    are independent dicts by the time this runs. Vega-Lite's own layer/axis
    resolution renders a layered view's axis from the PER-LAYER encoding
    when one is present, not the shared one, so the shared-encoding widening
    above is silently never visible on a layered chart without this: the
    axis keeps its unwidened gutter and the support_table column block
    collides with the category labels it was supposed to make room for.
    Only patches ``layer[0]`` -- the base layer is the one whose category
    encoding VL actually reads; a color-split ``layers:`` overlay's own
    layer(s) carry their own (irrelevant to this axis) encoding.
    """
    layers = spec.get("layer")
    if not isinstance(layers, list) or not layers:
        return spec
    base_layer = layers[0]
    if not isinstance(base_layer, dict):
        return spec
    base_encoding = base_layer.get("encoding")
    if not isinstance(base_encoding, dict):
        return spec
    base_y = base_encoding.get("y")
    if not isinstance(base_y, dict) or not isinstance(base_y.get("axis"), dict):
        return spec
    widened_axis = dict(base_y["axis"])
    raw_padding = widened_axis.get(
        "labelPadding", 0.0
    )  # type-state: silent_fallback — VL's own labelPadding default is 0
    current_padding = float(raw_padding)
    widened_axis["labelPadding"] = current_padding + added_width
    widened_base_layer = {
        **base_layer,
        "encoding": {**base_encoding, "y": {**base_y, "axis": widened_axis}},
    }
    return {**spec, "layer": [widened_base_layer, *layers[1:]]}


def _effective_mark_color(mark: VLDict) -> str | None:
    """The color a reader actually SEES for this layer's mark, not just its
    palette fill.

    An outline-only mark (``fillOpacity: 0`` + a visible ``stroke``, e.g.
    26V's gold-bordered "Before" bar — see ``bar_mark_to_vl``, which maps
    ``style.marks.bar.opacity`` to VL's ``fillOpacity`` precisely so an
    outline mark can isolate stroke from fill) reads by its BORDER color, not
    its invisible fill — VL still carries ``mark.fill`` at its resolved
    palette slot even when ``fillOpacity`` hides it, so reading ``fill``
    unconditionally would color the header for a color nothing on the mark
    actually shows. A line mark has no fill at all — it reads by its own
    ``stroke``. Otherwise the fill IS what's visible, so that wins.
    """
    stroke = mark.get("stroke")
    if mark.get("fillOpacity") == 0 and isinstance(stroke, str):
        return stroke
    if mark.get("type") == "line":
        return stroke if isinstance(stroke, str) else None
    fill = mark.get("fill")
    return fill if isinstance(fill, str) else None


def _layer_own_mark(vl_layer: VLDict) -> VLDict | None:
    """The mark dict a compiled overlay layer actually paints with.

    A bar layer carries its mark directly (``vl_layer["mark"]`` is already
    the dict). A line or area layer wraps its real marks one level deeper as
    a ``mark: "layered"`` spec — halo, foreground, and an invisible hover-
    target sublayer (``emitters/_overlay.py``'s ``emit_line_layer``/
    ``emit_area_layer``, via ``emitters/_layers.py``) — so ``vl_layer["mark"]``
    itself is absent and the paintable mark lives at
    ``vl_layer["layer"][0]["mark"]`` instead. An overlay layer always
    suppresses its own halo (``suppress_halo=True``, the same call sites),
    so the FIRST sublayer is always that foreground mark — never an absent
    halo or the invisible hover-target point that follows it.
    """
    mark = vl_layer.get("mark")
    if isinstance(mark, dict):
        return mark
    sub_layers = vl_layer.get("layer")
    if isinstance(sub_layers, list) and sub_layers and isinstance(sub_layers[0], dict):
        sub_mark = sub_layers[0].get("mark")
        if isinstance(sub_mark, dict):
            return sub_mark
    return None


def _legend_is_visible(spec: VLDict) -> bool:
    """Whether any color encoding in *spec* still carries a live legend.

    ``apply_color_legend`` (``emitters/_channels.py``) sets a color
    encoding's ``legend`` key to ``None`` when the chart's resolved legend
    is hidden, or to a real config dict otherwise — the single source of
    truth this codebase already uses for "will a legend actually render
    here", including the per_series case's own post-hoc
    ``_suppress_series_legend`` nulling. A colored header stands in for a
    legend swatch; showing BOTH would name the same series twice.
    """
    return any(
        isinstance(color_encoding.get("legend"), dict)
        for color_encoding in _emitted_color_encodings(spec)
    )


def _mark_series_header_fills(
    spec: VLDict,
    support_table: ChartSupportTable,
    resolved_chart: _CartesianResolvedChartFields,
    background: str,
) -> dict[int, str] | None:
    """Header ink for each ``source:`` entry whose column names a mark
    series' own ``y`` measure — a colored header stands in for a legend
    swatch, so each series is still named (by color) exactly once, and
    stays coordinated with the mark by construction: change the mark's
    color, the header follows, since both read the same compiled ``mark``
    dict.

    Gated on the legend actually being hidden (``_legend_is_visible``), not
    on whether the chart AUTHORED ``legend.visible: false`` -- a chart
    where the legend renders for any other reason (an unauthored default,
    or a family that never suppressed it) must not ALSO recolor headers;
    that would name the same series twice, once by legend swatch and once
    by header ink.

    Scoped to a layered chart (``chart.layers``): base + each authored layer
    compile to one mark layer apiece, in that same order, at the FRONT of
    ``spec["layer"]`` (paint-order contract — see this file's module
    docstring's #9/#10 citations) — so ``spec["layer"][0]`` is the base's
    layer, ``spec["layer"][i]`` the (i-1)th authored layer's, 1:1 with
    ``[resolved_chart.y, *layer.y for layer in layers]``. Each layer's own
    mark dict is read via ``_layer_own_mark``, which handles both a direct
    mark (bar) and a line/area layer's one-level-deeper "layered" wrapper —
    a bar chart carrying a line OVERLAY layer (the canonical "hide the
    legend, let header color name the series" shape) is exactly as
    supported as an all-bar layer stack. A single-series chart (no layers,
    or fewer than two resolved y fields) returns ``None`` — its headers
    keep the board's default ink untouched.

    Each field's effective color (``_effective_mark_color``) is run through
    ``companion_color_for_fill`` (a no-op passthrough when the color isn't a
    literal member of the chart's own categorical palette -- true for most
    border colors, and for a fill the chart already resolved from that
    palette it substitutes the theme's matching dark companion), then
    ``ensure_readable_ink`` (a WCAG contrast floor against the card
    background, hue preserved) -- catching both a light palette override
    with no registered dark companion (e.g. a sequential-gray slot) and a
    border/fill color that was never a palette member to begin with (e.g. a
    named token like ``category.gold``) and so skipped the companion step
    entirely.
    """
    # Scatter is deliberately excluded: ResolvedScatterStyle carries no
    # series_label (no companion-ink concept for a scatter chart).
    if not isinstance(
        resolved_chart, ResolvedBarChart | ResolvedLineChart | ResolvedAreaChart
    ):
        return None
    if _legend_is_visible(spec):
        return None
    layers = resolved_chart.layers
    if not layers:
        return None
    y_fields = [f for f in (resolved_chart.y, *(layer.y for layer in layers)) if f]
    if len(y_fields) < 2:
        return None
    vl_layers = spec.get("layer")
    if not isinstance(vl_layers, list) or len(vl_layers) < len(y_fields):
        return None
    fill_by_field: dict[str, str] = {}
    for y_field, vl_layer in zip(y_fields, vl_layers, strict=False):
        mark = _layer_own_mark(vl_layer) if isinstance(vl_layer, dict) else None
        if mark is None:
            return None
        effective = _effective_mark_color(mark)
        if effective is None:
            return None
        fill_by_field[y_field] = effective
    palette = list(resolved_chart.palette)
    dark_companions = resolved_chart.style.series_label.dark_companion_palette
    result = {
        entry_idx: ensure_readable_ink(
            companion_color_for_fill(
                fill_by_field[entry.source], palette, dark_companions
            ),
            background,
        )
        for entry_idx, entry in enumerate(support_table.entries)
        if isinstance(entry, ChartSupportTableSource) and entry.source in fill_by_field
    }
    return result or None


def attach_support_table_columns(
    spec: dict[str, Any],  # type-state: explicit_any — VL fragment
    *,
    support_table: ChartSupportTable,
    style: SupportTableStyle,
    entry_numerals: Sequence[StripNumerals],
    value_formats: Sequence[FormatState],
    column_widths: Sequence[float],
    column_headers: Sequence[str],
    category_field: str,
    color_field: str | None,
    series_order: list[str] | None,
    dark_fills: list[str] | None,
    header_fills: dict[int, str] | None,
    axis_y_orient: Literal["left", "right"],
    axis_label_padding: float = 0.0,
    single_column_per_series: bool = False,
) -> dict[str, Any]:  # type-state: explicit_any — VL fragment
    """Append attached support_table VALUE-COLUMN layers to a Vega-Lite chart spec.

    The exact transpose of ``attach_support_table``: where the row strip emits
    one text layer per row at a pixel-literal y sharing the chart's x-band
    encoding, this emits one text layer per visual column at a pixel-literal
    x sharing the chart's y-band encoding. ``column_widths``/
    ``column_headers`` are already expanded to one entry per visual column
    (``_visual_column_widths_and_headers``) — ``style.position`` must already
    be resolved to a concrete ``"left"``/``"right"`` (see
    ``compile.support_table.resolve_support_table_position``, baked at
    compile time onto the resolved chart's ``effective_support_table_style``).

    ``single_column_per_series`` (see ``_column_block_plot_gutter``'s twin
    concern below): a grouped bar's per_series entry paints ONE column, every
    series' cell vertically aligned to its own sub-band, instead of one
    column per series.

    ``header_fills`` (see ``_mark_series_header_fills``): a ``source:``
    entry's header ink, keyed by that entry's own index in
    ``support_table.entries`` — overrides the header's default fill only
    for entries a layered chart's series linking matched; every other
    header keeps ``style``'s own ink.

    When ``style.position`` sits on the same side as the category axis's own
    labels (``axis_y_orient``), this widens that axis's own measured
    ``labelPadding`` (see ``emitters/_measured_label_padding.py``) by the
    column block's total footprint (widths plus ``axis_label_padding``'s own
    plot-facing gutter — see ``_column_block_plot_gutter``), pushing the
    axis's own labels further from the plot so the value columns fit in the
    gap this opens between them and the plot edge. On the opposite side there
    is no existing gutter to make room in, so no labelPadding change is made.
    """
    raw_width = spec.get("width")
    spec_width = (
        float(raw_width)
        if isinstance(raw_width, (int, float)) and raw_width > 0
        else None
    )
    if spec_width is None:
        raise RenderError.from_code(
            ERR_INPUT_INVALID,
            message=(
                "attach_support_table_columns requires the spec to carry an "
                "explicit width — pixel-literal column positioning has no "
                "anchor otherwise."
            ),
        )
    position = style.position
    assert position == "left" or position == "right", (
        f"attach_support_table_columns requires style.position to be already "
        f"resolved to 'left' or 'right' — got {position!r}. Every caller reaches "
        "this with a compile-baked style (see "
        "compile.support_table.resolve_support_table_position)."
    )

    _raw_top_encoding = spec.get("encoding")
    top_encoding: dict[str, Any] = {}  # type-state: explicit_any — VL fragment
    if isinstance(_raw_top_encoding, dict):
        top_encoding = _raw_top_encoding
    parent_y_enc = top_encoding.get("y")
    if not isinstance(parent_y_enc, dict) or "field" not in parent_y_enc:
        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        raise ChartDataError(
            "support_table column attachment requires a category y-encoding "
            "on the chart spec; none found."
        )

    plot_gutter = _column_block_plot_gutter(style, axis_label_padding)
    total_width = sum(column_widths) + plot_gutter
    if position == axis_y_orient and total_width > 0:
        raw_axis = parent_y_enc.get("axis") or {}  # type-state: silent_fallback — unset
        axis_block = dict(raw_axis)
        current_padding = axis_block.get(
            "labelPadding", 0.0
        )  # type-state: silent_fallback — VL's own labelPadding default is 0
        current_padding = float(current_padding)
        axis_block["labelPadding"] = current_padding + total_width
        parent_y_enc = {**parent_y_enc, "axis": axis_block}
        spec = {
            **spec,
            "encoding": {**top_encoding, "y": parent_y_enc},
        }
        spec = _widen_first_layer_y_axis_padding(spec, total_width)

    out = _wrap_base_as_layer(spec)
    layers: list[dict[str, Any]] = list(  # type-state: explicit_any — VL fragment
        out["layer"]
    )
    base_layer_count = len(layers)

    edges = _column_edges(column_widths, position, spec_width, plot_gutter)
    # The header band is one row tall.
    header_y = -(row_height(style) / 2.0)

    visual_idx = 0
    for entry_idx, entry in enumerate(support_table.entries):
        numerals = entry_numerals[entry_idx]
        value_format = value_formats[entry_idx]
        if isinstance(entry, ChartSupportTablePerSeries) and not entry.by_measure:
            if not series_order:
                raise RenderError.from_code(
                    ERR_INPUT_INVALID,
                    message=(
                        "attach_support_table_columns requires series_order "
                        "when per_series entries are present."
                    ),
                )
            if single_column_per_series:
                # A grouped bar's own layout: every series' cell shares this
                # one column's x, each landing on its own series' sub-band —
                # the parent's own yOffset encoding, copied verbatim so the
                # cell's offset scale resolves against the same domain the
                # bar mark itself uses. A color field the bar chose not to
                # offset (e.g. 1:1 with x) carries no yOffset at all, and the
                # cells then simply share the category band center, same as
                # the bar's own marks in that case.
                _left, right = edges[visual_idx]
                raw_yoffset = top_encoding.get("yOffset")
                yoffset_enc = (
                    {"field": raw_yoffset["field"], "type": raw_yoffset["type"]}
                    if isinstance(raw_yoffset, dict) and "field" in raw_yoffset
                    else None
                )
                for series_idx, series_value in enumerate(series_order):
                    cell_name = f"__support_table_col_{visual_idx}_{series_idx}"
                    transforms = _column_cell_transforms(
                        entry,
                        category_field,
                        color_field,
                        series_value,
                        value_format,
                        cell_name,
                        numerals,
                    )
                    layer = _column_cell_layer(
                        parent_y_enc, style, right, cell_name, transforms, yoffset_enc
                    )
                    if dark_fills is not None and series_idx < len(dark_fills):
                        layer["mark"]["fill"] = dark_fills[series_idx]
                    layers.append(layer)
                layers.append(
                    _column_header_layer(
                        style, right, column_headers[visual_idx], header_y
                    )
                )
                visual_idx += 1
                if style.row.rule.width > 0 and visual_idx < len(column_widths):
                    layers.append(_column_rule_layer(style, right))
            else:
                for series_idx, series_value in enumerate(series_order):
                    _left, right = edges[visual_idx]
                    cell_name = f"__support_table_col_{visual_idx}"
                    transforms = _column_cell_transforms(
                        entry,
                        category_field,
                        color_field,
                        series_value,
                        value_format,
                        cell_name,
                        numerals,
                    )
                    layer = _column_cell_layer(
                        parent_y_enc, style, right, cell_name, transforms
                    )
                    if dark_fills is not None and series_idx < len(dark_fills):
                        layer["mark"]["fill"] = dark_fills[series_idx]
                    layers.append(layer)
                    layers.append(
                        _column_header_layer(
                            style, right, column_headers[visual_idx], header_y
                        )
                    )
                    visual_idx += 1
                    if style.row.rule.width > 0 and visual_idx < len(column_widths):
                        layers.append(_column_rule_layer(style, right))
        else:
            _left, right = edges[visual_idx]
            cell_name = f"__support_table_col_{visual_idx}"
            transforms = _column_cell_transforms(
                entry,
                category_field,
                color_field,
                None,
                value_format,
                cell_name,
                numerals,
            )
            layers.append(
                _column_cell_layer(parent_y_enc, style, right, cell_name, transforms)
            )
            header_fill = (
                header_fills.get(entry_idx) if header_fills is not None else None
            )
            layers.append(
                _column_header_layer(
                    style, right, column_headers[visual_idx], header_y, header_fill
                )
            )
            visual_idx += 1
            if style.row.rule.width > 0 and visual_idx < len(column_widths):
                layers.append(_column_rule_layer(style, right))

    out["layer"] = _disable_inherited_tooltip_description(layers, base_layer_count)
    out["autosize"] = {"type": "pad", "contains": "padding"}
    return out


def _sorted_category_domain(
    sort: ChartSort | None,
    category_field: str,
    data: list[dict[str, Any]],  # type-state: explicit_any — VL fragment
    op: VlSortOp,
    emitted_sort: VLDict | None,
) -> list[Any] | None:  # type-state: explicit_any — a domain value, any JSON scalar
    """The category domain in Vega-Lite's own rendered order for an
    EXPLICITLY authored ``chart.sort``, or for an emitted ENGINE-DEFAULT
    sort on the category encoding when none was authored, or ``None`` when
    neither exists -- the caller then leaves the scale unpinned (Vega-Lite's
    own native handling, or the existing drawn-index anchor logic, already
    covers every other case).

    Takes the resolved chart's own ``sort: ChartSort | None`` first, since
    that's what an author actually wrote; *emitted_sort* (the caller's own
    compiled encoding ``sort`` key, e.g. a horizontal bar's engine-default
    largest-measure-first, see ``emitters/bar.py``) is the fallback,
    matching the precedence ``rendered_x_domain`` (``x_domain.py``) already
    applies to a layered chart's shared categorical scale.

    Delegates the actual ordering to ``x_domain_order``
    (``core/utils.py``) rather than reimplementing it: that helper already
    owns "a categorical x domain in Vega-Lite's rendered order" for
    ``rendered_x_domain`` and the resolve-time stacked-label predicate, and
    gets the two things a bespoke reimplementation here got wrong --
    ``EncodingSortField.op``'s aggregate (not first-occurrence;
    ``support_table``'s own ``aggregate:``/``per_series:`` entries permit
    multiple rows per category, e.g. board 26's stacked cells), and a
    category with no value for the sort field keeping its place at the END
    of the domain (where VL puts it) rather than being dropped entirely.
    ``op`` comes from the chart's own already-emitted category encoding, so
    the domain pinned here reproduces the order that encoding renders in.
    """
    vl_sort = chart_sort_to_vl(sort)
    if vl_sort is None:
        if isinstance(emitted_sort, dict) and emitted_sort.get("field"):
            vl_sort = emitted_sort
        else:
            return None
    domain = x_domain_order(
        data,
        category_field,
        vl_sort["field"],
        vl_sort["order"] == "descending",
        op=op,
    )
    return domain or None


def _pin_sorted_category_domain(
    spec: dict[str, Any],  # type-state: explicit_any — VL fragment
    channel: Literal["x", "y"],
    category_field: str,
    data: list[dict[str, Any]],  # type-state: explicit_any — VL fragment
    sort: ChartSort | None,
) -> dict[str, Any]:  # type-state: explicit_any — VL fragment
    """Pin an explicit, sorted ``scale.domain`` onto the shared category
    encoding when the chart AUTHORS an explicit ``chart.sort``, or -- absent
    one -- when the compiled encoding itself already carries an engine-default
    sort (e.g. a horizontal bar's largest-measure-first, see
    ``_sorted_category_domain``'s docstring); this reproduces whichever order
    Vega-Lite would otherwise natively render.

    Root cause this works around: Vega-Lite's own native sort-by-field
    resolves correctly across a plain layered chart (confirmed empirically),
    but silently reverts the shared categorical scale to alphabetical order
    the moment ANY sibling layer sharing that scale carries its own
    ``transform:`` pipeline -- exactly what every support_table cell/header
    layer carries (their value-producing ``calculate`` transform). This is
    the same failure mode ``emitters/_overlay.py``'s ``_reconcile_x_domain``
    already documents and works around for every layered chart; support_table
    hits the identical Vega-Lite limitation from its own, later post-pass, so
    it needs the same class of fix -- an explicit, pre-sorted domain always
    wins over ``sort`` in Vega-Lite, sidestepping the native per-layer merge
    entirely.

    Pinning only the TOP-LEVEL shared encoding is sufficient (confirmed
    empirically against a real layered spec): unlike the axis-label-gutter
    fix elsewhere in this module, Vega-Lite reads ``scale.domain`` from
    whichever layer states it first in the merge, and the shared encoding is
    exactly that for every layer that inherits its category encoding rather
    than re-declaring one.

    No-op (returns ``spec`` unchanged) when the channel's own encoding is
    missing, the encoding's scale is not categorical (its ``type`` is not
    ``nominal``/``ordinal``), the ``scale`` ALREADY carries a ``domain``, the
    computed domain isn't all plain JSON scalars, or neither an authored
    ``chart.sort`` nor an emitted engine-default sort exists (or its field
    has no data to sort by) -- ``_sorted_category_domain`` returning
    ``None`` is the single source of truth for that last case.

    The already-pinned-domain no-op matters for a layered chart:
    ``_reconcile_x_domain`` (``emitters/_overlay.py``) pins the shared
    categorical scale's ``domain`` earlier in the same render, as a UNION of
    the base rows and every layer's own rows. This function computes its
    domain from *data* alone (the base rows the support_table post-pass was
    handed, via ``_apply_support_table_strip``, never the per-layer rows),
    so recomputing here would silently drop a category that exists only in
    a layer's own diverging query -- exactly what that function's own
    docstring warns a narrower pin does to its union. Two other writers can
    also reach this ``scale.domain`` first -- ``pin_categorical_domain_order``
    (a sign-split bar's sub-layers) and ``pin_sorted_domain`` (line/area/
    heatmap) -- and the invariant that makes deferring to any of the three
    safe is the same one: each reads the identical compiled ``sort`` this
    function would.

    Three gates guard the pin, and they answer DIFFERENT questions -- all are
    load-bearing, none is defensive:

    1. The encoding type must be ``nominal``/``ordinal``. An explicit
       ``scale.domain`` array is a categorical concept; on a continuous
       scale (``quantitative``, or ``temporal`` without a timeUnit) d3 reads
       it as scale STOPS, pairing only ``min(len(domain), len(range))``
       entries and extrapolating the rest off-canvas -- marks render outside
       the plot and ticks disappear. A continuous x whose values happen to
       be JSON scalars (plain numbers, ISO date strings) sails through the
       value gate, so only the type gate keeps the pin off it.

    2. Every domain value must be a plain JSON scalar. A bar with a
       ``::date`` x column clamps to a band scale and compiles its encoding
       type to ``ordinal``/``nominal`` (it LOOKS categorical, passing the
       type gate), but the domain values are ``datetime.date`` objects,
       which Vega-Lite's JSON serializer rejects outright -- pinning one
       crashes the render. Only the value gate catches a scale that merely
       LOOKS categorical but carries unserializable values.

    3. The ``scale`` must not already carry a ``domain``. See the
       already-pinned-domain paragraph above.

    Gates 1 and 2 each cover a case the other misses -- a continuous
    scalar-valued x passes the value gate, a categorical date-valued x
    passes the type gate -- so the pin requires BOTH.
    """
    top_encoding = spec.get("encoding")
    if not isinstance(top_encoding, dict):
        return spec
    cat_enc = top_encoding.get(channel)
    if not isinstance(cat_enc, dict):
        return spec
    if cat_enc.get("type") not in ("nominal", "ordinal"):
        return spec
    existing_scale = cat_enc.get("scale")
    if isinstance(existing_scale, dict) and "domain" in existing_scale:
        return spec
    domain = _sorted_category_domain(
        sort,
        category_field,
        data,
        vl_sort_op(cat_enc.get("sort")),
        emitted_sort=cat_enc.get("sort"),
    )
    if domain is None or not _is_json_scalar_domain(domain):
        return spec
    new_scale = (
        {**existing_scale, "domain": domain} if existing_scale else {"domain": domain}
    )
    new_enc = {**cat_enc, "scale": new_scale}
    return {**spec, "encoding": {**top_encoding, channel: new_enc}}


def _is_json_scalar_domain(
    domain: list[Any],  # type-state: explicit_any — a domain value, any JSON scalar
) -> bool:
    """Whether every value in *domain* is a plain JSON scalar (str/int/
    float/bool) -- what Vega-Lite's own spec serializer accepts in
    ``scale.domain``.

    ``bool`` is a subclass of ``int`` in Python, so ``isinstance(v, (str,
    int, float))`` already covers it; listed explicitly anyway so a reader
    doesn't have to know that.  A ``datetime.date``/``datetime.datetime``
    domain value (a bar's ``::date`` x column, clamped to a band scale)
    fails this check -- exactly the case this function exists to catch.
    """
    return all(isinstance(v, str | int | float | bool) for v in domain)


def _apply_support_table_columns_post_pass(
    spec: dict[str, Any],  # type-state: explicit_any — VL fragment
    resolved_chart: _CartesianResolvedChartFields,
    charts_style: ResolvedChartDefaults,
    data: list[dict[str, Any]],  # type-state: explicit_any — VL fragment
    padding: dict[str, Any] | None,  # type-state: explicit_any — VL fragment
    dt_style: SupportTableStyle,
    axis_y_orient: Literal["left", "right"],
    axis_label_padding: float,
    stacked_series_order: list[str] | None,
) -> tuple[
    dict[str, Any],  # type-state: explicit_any — VL fragment
    dict[str, Any] | None,  # type-state: explicit_any — VL fragment
]:
    """Column-block counterpart of the row-strip post-pass, for a chart
    whose category axis is vertical.

    Shares every helper below the placement layer with the row-strip path
    (entry union, format resolution, ``strip_numerals_for_values``, per_series
    expansion, aggregate transforms, ``validate_support_table_against_data``);
    only the geometry — column widths/positions instead of row heights/y-
    pixels — is new. No sampling and no label-period filter: the gate restricts this
    path to a band-scale category axis, which is never thinned.
    """
    support_table = resolved_chart.support_table
    assert support_table is not None

    category_field = resolved_chart.x
    if not category_field:
        raise RenderError.from_code(
            ERR_INPUT_INVALID,
            message=(
                "chart.support_table requires an x-encoding; the chart has no "
                "`x:` field. Add `x: <column>` or remove the support_table block."
            ),
        )

    spec = _pin_sorted_category_domain(
        spec, "y", category_field, data, resolved_chart.sort
    )
    _encoding = spec.get("encoding")
    _y_encoding = _encoding.get("y") if isinstance(_encoding, dict) else None
    y_encoding: VLDict = _y_encoding if isinstance(_y_encoding, dict) else {}
    category_type: str | None = y_encoding.get("type")

    validate_support_table_against_data(
        support_table, category_field, data, x_type=category_type
    )

    color_field = effective_color_field(resolved_chart)

    # Declare-once unit anchoring — the same rule the row strip applies,
    # read off the category channel's own (y) encoding instead of x.
    if (
        y_encoding.get("field")
        and "sort" in y_encoding
        and y_encoding["sort"] is None
        and category_type in ("nominal", "ordinal")
    ):
        anchor: StripAnchor | None = StripAnchor.by_drawn_index_data_order()
    elif category_type in ("nominal", "ordinal") and "sort" in y_encoding:
        anchor = None
    elif category_type == "temporal":
        anchor = StripAnchor.by_drawn_index()
    else:
        anchor = None

    entry_values = _entry_values(support_table, data, category_field, color_field)
    pad_font_family = resolved_pad_font_family(dt_style)
    entry_numerals = (
        _entry_numerals(
            support_table,
            entry_values,
            charts_style.formats,
            pad_font_family,
            anchor,
            "left",
        )
        if anchor is not None
        else plain_numerals(
            support_table, charts_style.formats, pad_font_family, entry_values
        )
    )

    has_per_series = any(
        isinstance(e, ChartSupportTablePerSeries) for e in support_table.entries
    )
    # A per_series entry's column layout is implied by the chart's own series
    # layout, the same geometry-implied-by-the-chart principle the rest of
    # this feature rests on: a stacked bar gives every series its own segment
    # along the SAME band, so it also gets its own column (unchanged,
    # multi-column); a grouped bar gives every series its own sub-band, so a
    # per_series entry stays a single column with each series' cell placed at
    # its own sub-band's y instead — vertically aligned with the bar it
    # describes, the way the column adjacent to a stacked segment already is.
    is_stacked_bar = isinstance(
        resolved_chart, ResolvedBarChart
    ) and resolved_chart.stack not in (None, "none")
    single_column_per_series = has_per_series and not is_stacked_bar
    series_order: list[str] | None = None
    dark_fills: list[str] | None = None
    if has_per_series:
        if color_field is None:
            raise RenderError.from_code(
                ERR_INPUT_INVALID,
                message=(
                    "attach_support_table_columns requires the spec to carry a "
                    "color encoding when per_series entries are present."
                ),
            )
        if data:
            distinct: set[str] = set()
            for row in data:
                s = row.get(color_field)
                if s is not None:
                    distinct.add(str(s))
            series_order = _series_order_strip(
                "bar",
                resolved_chart,
                data,
                color_field,
                distinct,
                stacked_series_order,
            )
            # Column order reads left to right in the chart's own stack
            # order, on both placements: _column_edges lists every column in
            # authored/series_order order, left to right on screen, whether
            # the block sits at position "left" or "right" — a reader maps
            # visual column N to the chart's own segment N by screen
            # position, so the columns must not be reversed on either side.
            # dt_style.font.color is never actually None -- every built-in
            # theme gives support_table its own (muted) ink so aggregate/
            # source cells always paint something. The signal for "the author
            # asked for uniform ink" is therefore not None-ness but disagreement
            # with charts_style.support_table.font.color, the board-wide
            # default with no chart-local override folded in: equal means
            # nothing overrode it (today's per-series companion ink applies);
            # different means a chart-type or chart-local style.support_table.
            # font.color was authored, and it already painted every cell's
            # mark.fill via _text_mark_props -- dark_fills is simply left
            # uncomputed so nothing overrides that authored color.
            uniform_ink_authored = (
                dt_style.font.color != charts_style.support_table.font.color
            )
            if not uniform_ink_authored:
                dark_palette = list(charts_style.dark_companion_palette)
                if is_stacked_bar and isinstance(resolved_chart, ResolvedBarChart):
                    fill_by_series = emitted_categorical_color_scale(
                        *_emitted_color_encodings(spec)
                    )
                    if fill_by_series is not None:
                        dark_fills = [
                            companion_color_for_fill(
                                fill_by_series[series],
                                list(resolved_chart.palette),
                                resolved_chart.style.series_label.dark_companion_palette,
                            )
                            for series in series_order
                        ]
                if dark_fills is None:
                    alpha_index = {s: i for i, s in enumerate(sorted(series_order))}
                    dark_fills = [
                        dark_palette[alpha_index[s] % len(dark_palette)]
                        for s in series_order
                    ]

    column_widths = _entry_column_widths(
        support_table,
        entry_values,
        dt_style,
        entry_numerals,
        series_order,
        single_column_per_series,
    )
    visual_widths, column_headers = _visual_column_widths_and_headers(
        support_table, column_widths, series_order, single_column_per_series
    )
    value_formats = [entry.format for entry in support_table.entries]
    header_fills = _mark_series_header_fills(
        spec, support_table, resolved_chart, charts_style.background
    )

    spec = attach_support_table_columns(
        spec,
        support_table=support_table,
        style=dt_style,
        entry_numerals=entry_numerals,
        value_formats=value_formats,
        column_widths=visual_widths,
        column_headers=column_headers,
        category_field=category_field,
        color_field=color_field,
        series_order=series_order,
        dark_fills=dark_fills,
        header_fills=header_fills,
        axis_y_orient=axis_y_orient,
        axis_label_padding=axis_label_padding,
        single_column_per_series=single_column_per_series,
    )

    # A stacked bar's per_series columns already show series names via their
    # own headers — a side legend showing the same names is redundant ink,
    # same rationale as the row strip's label-gutter suppression. A grouped
    # bar's single column has no per-series header (see
    # _per_series_column_header), so its series are named and colored the
    # way an ordinary grouped bar's legend already does — never suppressed.
    legend_shows_series = color_field is not None and resolved_chart.legend.visible
    if has_per_series and legend_shows_series and is_stacked_bar:
        _suppress_series_legend(spec)

    total_width = sum(visual_widths) + _column_block_plot_gutter(
        dt_style, axis_label_padding
    )
    # Softer, earlier signal than the hard floor render/layout_sizing.py checks
    # once the true post-axis-chrome overhead is known: warn here, from the
    # column block's own reservation alone, when it already claims most of
    # the footprint it shares with the plot -- before any axis/legend chrome
    # is even subtracted.
    _raw_plot_width = spec.get("width")
    if (
        total_width > 0
        and isinstance(_raw_plot_width, (int, float))
        and _raw_plot_width > 0
    ):
        _plot_width = float(_raw_plot_width)
        _card_width_estimate = _plot_width + total_width
        _warn_ratio = get_chart_rendering().support_table.column_block_share_warn_ratio
        if total_width > _warn_ratio * _card_width_estimate:
            record_plot_width_share_warning(
                resolved_chart.id,
                column_block_width_px=total_width,
                plot_width_px=_plot_width,
                card_width_px=_card_width_estimate,
            )
    if total_width > 0 and dt_style.position != axis_y_orient:
        if dt_style.position == "left":
            if padding is not None:
                raw_left = padding.get(
                    "left", 0
                )  # type-state: silent_fallback — additive read
                padding = {**padding, "left": float(raw_left) + total_width}
            else:
                bump_padding_left(spec, total_width)
        else:
            if padding is not None:
                raw_right = padding.get(
                    "right", 0
                )  # type-state: silent_fallback — additive read
                padding = {**padding, "right": float(raw_right) + total_width}
            else:
                bump_padding_right(spec, total_width)

    header_h = row_height(dt_style)  # the header band is one row tall
    if header_h > 0:
        if padding is not None:
            raw_top = padding.get(
                "top", 0
            )  # type-state: silent_fallback — additive read
            padding = {**padding, "top": float(raw_top) + header_h}
        else:
            bump_padding_top(spec, header_h)

    return spec, padding


def _tilted_label_axis_offset(
    baked_offset: float,
    style: SupportTableStyle,
    axis_x: ResolvedAxisStyle,
    x_label_block_px: float | None,
) -> float:
    """Grow the compile-baked axis gap to clear a render-time label tilt.

    ``support_table_axis_offset`` reserves ``label_max_lines`` lines of
    *horizontal* label between the plot and the strip
    (``compile/support_table.py``'s ``axis_offset``). The angle the labels are
    actually drawn at is decided at render, from the data and the plot width,
    and a label rotated toward vertical is as tall as it is long — so the
    strip owes whatever that tilt costs beyond the lines already reserved.

    Nothing is measured here: ``x_label_block_px`` is what the emitter's own
    tilt resolution measured (``AxisLabelLayout.label_block_height``), which
    is the only place that holds both the chosen angle and — on a temporal
    axis — the formatted label strings. ``None`` means the emitter resolved
    no x labels at all, and the baked gap stands.

    Hidden labels are the bake's own carve-out: ``axis_offset`` reserves
    nothing for them, and the tilt resolver picks an angle regardless of
    visibility, so this gates on the same condition or it would grow a gap
    around labels that are never drawn.
    """
    labels = axis_x.labels
    if x_label_block_px is None or labels.visible is False or labels.font.size <= 0:
        return baked_offset
    reserved = labels.font.size * style.label_max_lines
    return baked_offset + max(0.0, x_label_block_px - reserved)


def apply_chart_support_table_post_pass(
    spec: dict[str, Any],
    resolved_chart: _CartesianResolvedChartFields,
    charts_style: ResolvedChartDefaults,
    data: list[dict[str, Any]],
    padding: dict[str, Any] | None,
    chart_type: str,
    x_label_block_px: float | None,
    stacked_series_order: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Post-pass: attach the support_table strip when the chart authors one.

    Single source of truth for the validate → resolve-style → attach →
    reserve-padding sequence. Called from every render branch that needs
    to honor `chart.support_table`. Returns the (possibly mutated) spec plus the
    (possibly augmented) padding kwarg the caller forwards to _finalize.

    Padding-bump destination depends on whether the caller supplied an
    external padding kwarg (which _finalize overwrites wholesale) or
    None (which leaves spec.padding alone).

    ``x_label_block_px`` is the emitted spec's own x-label block height,
    stashed by the emitter and handed over by the caller — see
    ``_tilted_label_axis_offset``. None when the chart resolved no x labels.

    ``stacked_series_order`` is a stacked bar's own already-computed
    baseline series order (``ChartSpec.stacked_series_order``, handed over
    by the caller from the ``$df_stacked_series_order`` stamp) — see
    ``_series_order_strip``'s docstring for why the strip must use this
    instead of re-deriving its own verdict. None for every other chart
    shape, and for a bar the caller couldn't stamp one for (no per-series
    entry, or called from a test without going through the real emitter).
    """
    support_table = resolved_chart.support_table
    if support_table is None:
        return spec, padding
    # The gate is written on the category axis's own orientation, not on
    # the chart family — today only a horizontal bar reaches a vertical
    # category axis, but a future vertical timeline satisfies this the same
    # way without a second gate to find and edit.
    category_axis_vertical = (
        isinstance(resolved_chart, ResolvedBarChart)
        and resolved_chart.orientation == "horizontal"
    )
    dt_style = resolved_chart.effective_support_table_style
    assert dt_style is not None, (
        "resolved_chart.effective_support_table_style must be baked when "
        "support_table is not None — see compile.resolve._kwargs._support_table_geometry"
    )
    # axis_y.position is baked to "left"/"right" at resolve time; read from the
    # resolved chart style (not the board-level charts_style which is "auto").
    _ay_style = getattr(
        resolved_chart, "style", None
    )  # type-state: silent_fallback — a resolved cartesian chart may carry no style; pre-existing, relocated by this diff
    resolved_ay = getattr(
        _ay_style, "axis_y", None
    )  # type-state: silent_fallback — optional axis_y off that style; pre-existing, relocated by this diff
    assert resolved_ay is not None, (
        f"cartesian resolved chart must carry style.axis_y — got {resolved_chart!r}"
    )
    axis_label_padding = resolved_ay.labels.padding
    assert axis_label_padding is not None, "axis_y.labels.padding unset"
    position = resolved_ay.position
    resolved_orient: str = position if position is not None else "right"
    if resolved_orient not in ("left", "right"):
        raise ValueError(
            f"axis_y.position resolved to unexpected value {resolved_orient!r}; "
            "expected 'left' or 'right'"
        )
    # mypy can't narrow `resolved_orient` from the ValueError guard above,
    # so the cast is required for mypy even though pyright sees it as redundant.
    axis_y_orient = cast(Literal["left", "right"], resolved_orient)  # pyright: ignore[reportUnnecessaryCast]  # type-state: cast — mypy cannot narrow resolved_orient past the ValueError guard above; pyright sees it redundant; pre-existing, relocated by this diff
    assert dt_style.position is not None, (
        "effective_support_table_style.position must already be resolved to a "
        "concrete side at compile time — see "
        "compile.support_table.resolve_support_table_position, called from "
        "compile.resolve.chart._kwargs._support_table_geometry"
    )
    if category_axis_vertical:
        return _apply_support_table_columns_post_pass(
            spec,
            resolved_chart,
            charts_style,
            data,
            padding,
            dt_style,
            axis_y_orient,
            axis_label_padding,
            stacked_series_order,
        )
    # spec["width"] is the unreduced card width — for an hconcat spec (chart
    # + endpoint-label rail pane) the rail's shrink is applied later, by the
    # converter's overshoot correction, not here. resolve_axis_x_overlap
    # already subtracts the rail's span before deciding the axis's label
    # cadence (emitters/line.py, area.py, bar.py); every width-driven
    # decision below (label-period filtering, width-based sampling) must
    # subtract the same span or it disagrees with the axis it mirrors.
    raw_spec_width = spec.get("width")
    axis_x_width: float | None = (
        float(raw_spec_width)
        if isinstance(raw_spec_width, (int, float)) and raw_spec_width > 0
        else None
    )
    if axis_x_width is not None and isinstance(
        resolved_chart, (ResolvedBarChart, ResolvedLineChart, ResolvedAreaChart)
    ):
        from dbt_charts.core.render.chart.emitters._endpoint_rail import (
            resolve_endpoint_rail_span,
        )

        axis_x_width -= resolve_endpoint_rail_span(resolved_chart, data, axis_x_width)
    # Normalize labeled temporal strings (e.g. "Jan 2024" → "2024-01-01") so
    # the validator sees lex-sortable ISO dates and the period-filter indexof
    # values match the ISO dates the V2 emitter produces in axis.values.
    # V1's render_standard_vega_spec does the same normalization before calling
    # validate_preaggregated_data; V2 emitters normalize internally but pass the
    # original data to this post-pass, so we mirror the normalization here.
    if resolved_chart.x:
        data = normalize_labeled_temporal(data, resolved_chart.x)
        spec = _pin_sorted_category_domain(
            spec, "x", resolved_chart.x, data, resolved_chart.sort
        )
    # Extract the x encoding type from the already-built spec.
    _encoding = spec.get("encoding")
    _x_encoding = _encoding.get("x") if isinstance(_encoding, dict) else None
    x_encoding: VLDict = _x_encoding if isinstance(_x_encoding, dict) else {}
    x_type: str | None = x_encoding.get("type")
    # For the validator, chronologically-ordered axes behave like temporal — the
    # window sort produces chronological order — so sampling the strip is safe.
    # This covers (a) date/datetime objects (e.g. a bar's `::date` x column,
    # which the bar mark clamps to a categorical scale but is really temporal),
    # and (b) lex-sortable date-like strings (e.g. "2024-01"). Non-lex-sortable
    # string patterns ("Jan 2024", "01/2024") are normalized to ISO by
    # normalize_labeled_temporal above, so they also pass here.
    # IMPORTANT: this reclassification is only for the validator.  x_type (the
    # actual spec encoding type) is kept separate so centering still applies to
    # categorical date axes — they render with bandPosition:0.5, so they need
    # dx exactly like any other ordinal/nominal axis.
    validator_x_type = x_type
    if validator_x_type in ("ordinal", "nominal") and resolved_chart.x and data:
        # Require ALL non-null values to be chronologically ordered — one stray
        # non-date value mid-column keeps the axis categorical (fail-closed:
        # dropping real categories via sampling would be wrong).
        field = resolved_chart.x
        all_chronological = True
        for row in data:
            v = row.get(field)
            if v is None:
                continue
            if isinstance(v, datetime.date):  # date/datetime (datetime subclasses date)
                continue
            if isinstance(v, str) and is_lex_sortable_date_like(v):
                continue
            all_chronological = False
            break
        if all_chronological:
            validator_x_type = "temporal"
    # color_field drives the series_order/palette resolution below.
    color_field = effective_color_field(resolved_chart)
    # How each row spells its numbers, resolved once from the values it will
    # render. Read three times below -- the width budget, the band-centering dx,
    # and the cells themselves -- so a row cannot be measured against different
    # numbers than it paints.
    # Declaring the unit once requires knowing which cell is painted leftmost.
    # An ordered axis can be thinned, so only a row number computed after every
    # filter answers that; a category axis is never thinned and carries
    # `sort: null`, so its leftmost cell is the first x in data order. An
    # authored `sort:` on a category axis defeats both, and there the strip
    # keeps today's per-cell formatting rather than stranding the unit mid-row.
    # dt_style/axis_label_padding/axis_y_orient are already resolved above
    # (shared with the column-path dispatch).
    # Which side the anchor's affix overhangs. Right-oriented y-axis: affix leads
    # digits, hanging into the gutter (left). Left-oriented y-axis: affix trails
    # digits, hanging into the anchor cell's own band (right), so every non-anchor
    # cell's left digit edge stays in one lane.
    hang: Literal["left", "right"] = "left" if axis_y_orient == "right" else "right"
    if (
        x_encoding.get("field")
        # Only `sort: null` (key present, value null) signals data order.
        # An absent sort key means VL picks alphabetical — not predictable from
        # data row order. Distinguish present-null from key-absent explicitly.
        and "sort" in x_encoding
        and x_encoding["sort"] is None
        and x_encoding.get("type") in ("nominal", "ordinal")
    ):
        # `sort: null` tells VL to paint the domain in data-insertion order.
        # An unsorted window follows the same order, so `by_drawn_index_data_order`
        # anchors the leftmost visible band regardless of query sort direction,
        # datetime normalization differences, or whether the period filter drops
        # the first emitter row.
        anchor = StripAnchor.by_drawn_index_data_order()
    elif x_encoding.get("type") in ("nominal", "ordinal") and "sort" in x_encoding:
        # Authored non-null sort: paint order is determined externally.
        # Fail-closed — no anchor.
        anchor = None
    elif validator_x_type == "temporal":
        # True temporal OR reclassified nominal/ordinal with no explicit sort:
        # VL sorts ascending by x, matching drawn_index's ascending window sort.
        anchor = StripAnchor.by_drawn_index()
    else:
        anchor = None
    entry_values = _entry_values(support_table, data, resolved_chart.x, color_field)
    pad_font_family = resolved_pad_font_family(dt_style)
    entry_numerals = (
        _entry_numerals(
            support_table,
            entry_values,
            charts_style.formats,
            pad_font_family,
            anchor,
            hang,
        )
        if anchor is not None
        else plain_numerals(
            support_table, charts_style.formats, pad_font_family, entry_values
        )
    )
    # Per-band pixel budget: measured cell width + horizontal cell padding.
    # Drives width-aware thinning; replaces the former hardcoded per-band literal.
    max_cell_w = max(
        _entry_cell_widths(support_table, entry_values, dt_style, entry_numerals),
        default=0.0,
    )
    # Temporal (drawn-index) anchors may land on band 2+ when the first cell
    # is zero — the CARRIES running-sum skips it and anchors on the next
    # non-zero cell. That anchor paints more glyphs than the bare digit-only
    # cell the budget was measured from. If the anchor's affix width is not
    # folded in, it overhangs into an adjacent cell's glyphs.
    # entry_dx stays on the bare measurement (keeps shared edges aligned);
    # only min_band_px is expanded here.
    band_budget_w = max_cell_w
    # _entry_cell_widths raises ValueError on a None font size, so this is reachable
    # only when font.size is set.
    _font_size = dt_style.font.size
    if _font_size is None:
        raise ValueError(
            "dt_style.font.size must be set; _entry_cell_widths should have raised first"
        )
    _measurer = get_font_measurer(dt_style.font.family, numeric=True)
    for _num, _vals in zip(entry_numerals, entry_values, strict=True):
        if not _num.anchor.needs_drawn_index:
            continue
        # Measure the max-abs value (closest to the actual anchor cell width).
        max_abs_val = max((_v for _v in _vals), key=abs, default=None)
        if max_abs_val is not None:
            _aw = _measurer.measure(_num.anchor_text(max_abs_val), _font_size)
            if _aw > band_budget_w:
                band_budget_w = _aw
    min_band_px = band_budget_w + 2.0 * dt_style.row.padding.horizontal
    sampling_step = validate_support_table_against_data(
        support_table, resolved_chart.x, data, x_type=validator_x_type
    )
    # Width-based step for ordered axes: if the measured cell width exceeds the
    # per-band budget, compute how many columns fit and thin accordingly.
    # Only ordered axes (validator_x_type == "temporal") may be width-sampled;
    # non-chronological ordinal axes are never sampled (dropping unordered
    # categories is silent data loss — fail-closed).
    if validator_x_type == "temporal" and resolved_chart.x and data and min_band_px > 0:
        spec_w = axis_x_width
        if isinstance(spec_w, (int, float)) and spec_w > 0:
            n_x = len(
                {
                    row.get(resolved_chart.x)
                    for row in data
                    if row.get(resolved_chart.x) is not None
                }
            )
            if n_x > 0:
                fit_cols = max(1, math.floor(spec_w / min_band_px))
                width_step = math.ceil(n_x / fit_cols)
                sampling_step = max(sampling_step, width_step)
    # When the overlap resolver allowed all labels (labelOverlap ≠ "parity"), the
    # axis does NOT drop any ticks, so the strip must show every cell too.  The
    # density gate in validate_support_table_against_data fires on row count alone;
    # override it to 1 when the emitted spec confirms all labels are visible.
    # Exception: temporal continuous x axes are gated out of this reset entirely
    # (regardless of labelOverlap) because VL places "nice" ticks rather than one
    # per data value. The strip still needs thinning via sampling_step — do not
    # reset it just because labelOverlap is not "parity".
    # Width-gate: do NOT reset when cells don't fit at the current density —
    # the spec axis may label OK (short ticks fit) while strip cells are too wide.
    if sampling_step > 1:
        enc = spec.get("encoding")
        x_enc = enc.get("x") if isinstance(enc, dict) else None
        x_axis = x_enc.get("axis") if isinstance(x_enc, dict) else None
        x_type = x_enc.get("type") if isinstance(x_enc, dict) else None
        # NOTE: We re-read the VL directive from the already-emitted spec rather
        # than threading the resolver's `label_overlap` directive here.  This is a
        # seam constraint — `attach_support_table` receives the finished spec, not the
        # resolver's return value.  The check assumes a flat `encoding.x.axis` shape;
        # faceted/multiples specs nest encoding differently and would bypass this path.
        if x_type != "temporal" and not (
            isinstance(x_axis, dict) and x_axis.get("labelOverlap") == "parity"
        ):
            # Only reset when cells actually fit; keep thinning when they overflow.
            spec_w = axis_x_width
            n_x = (
                len(
                    {
                        row.get(resolved_chart.x)
                        for row in data
                        if resolved_chart.x and row.get(resolved_chart.x) is not None
                    }
                )
                if resolved_chart.x and data
                else 0
            )
            cells_fit = (
                isinstance(spec_w, (int, float))
                and spec_w > 0
                and n_x > 0
                and spec_w / n_x >= min_band_px
            )
            if cells_fit:
                sampling_step = 1
    # Resolve series_order and dark_fills for per_series entries.
    has_per_series = any(
        isinstance(e, ChartSupportTablePerSeries) for e in support_table.entries
    )
    series_order: list[str] | None = None
    dark_fills: list[str] | None = None
    if has_per_series:
        # Resolve series order so the strip row adjacent to the plot matches
        # the chart's visual stack segment at that edge (adjacency invariant).
        # See _series_order_strip for full per-chart-type ordering rules.
        # Strip row 0 = visual bottom of strip (closest to chart); series_order[N]
        # = visual top of strip, matching the chart's own top-to-bottom reading.
        if color_field and data:
            distinct: set[str] = set()
            for row in data:
                s = row.get(color_field)
                if s is not None:
                    distinct.add(str(s))
            series_order = _series_order_strip(
                chart_type,
                resolved_chart,
                data,
                color_field,
                distinct,
                stacked_series_order,
            )
            dark_palette = list(
                _chart_dark_companion_palette(resolved_chart, charts_style)
            )
            color_scale = category_scale_for(
                resolved_chart.category_colors, color_field
            )
            if color_scale is not None:
                # This field is board-bound: key the companion ink off the
                # value's board SLOT, the same lookup its own mark fill uses
                # everywhere else on the board -- never off its position in
                # this chart's locally alphabetical color domain.
                dark_fills = [
                    ink_at(color_scale, s, dark_palette) for s in series_order
                ]
            elif isinstance(
                resolved_chart, (ResolvedBarChart, ResolvedAreaChart)
            ) and resolved_chart.stack not in (None, "none"):
                fill_by_series = emitted_categorical_color_scale(
                    *_emitted_color_encodings(spec),
                )
                if fill_by_series is not None:
                    dark_fills = [
                        companion_color_for_fill(
                            fill_by_series[series],
                            list(resolved_chart.palette),
                            resolved_chart.style.series_label.dark_companion_palette,
                        )
                        for series in series_order
                    ]
            if dark_fills is None:
                # Unbound: fall back to VL's own default -- palette[i] for the
                # i-th series in alphabetical color domain (dark_companion_palette
                # is positionally aligned with palette).
                alpha_index = {s: i for i, s in enumerate(sorted(series_order))}
                dark_fills = [
                    dark_palette[alpha_index[s] % len(dark_palette)]
                    for s in series_order
                ]
    series_count = len(series_order) if series_order else 0
    chart_style = getattr(resolved_chart, "style", None)
    axis_x_style = chart_style.axis_x if chart_style is not None else None
    assert axis_x_style is not None, (
        f"cartesian resolved chart must carry style.axis_x — got {resolved_chart!r}"
    )
    axis_offset_value = resolved_chart.support_table_axis_offset
    if dt_style.position == "bottom":
        assert axis_offset_value is not None, (
            "support_table_axis_offset must be baked for a bottom strip — see "
            "compile.resolve.chart._kwargs._support_table_geometry"
        )
        axis_offset_value = _tilted_label_axis_offset(
            axis_offset_value, dt_style, axis_x_style, x_label_block_px
        )
    period_filter = None
    if resolved_chart.x:
        period_filter = _label_period_filter_expr(
            spec,
            charts_style,
            data,
            resolved_chart.x,
            chart_type,
            chart_local_axis_x=axis_x_style,
            axis_x_width=axis_x_width,
        )
    # When a period_filter is active it already restricts strip cells to
    # label-period openers (e.g. one per month on a daily-data chart).
    # Applying sampling on top would further thin the strip — e.g. daily
    # data with 365 rows gives sampling_step=10, then the period filter
    # keeps 12 monthly openers, and sampling reduces that to ~2 cells.
    # The period filter is the correct thinning mechanism; suppress sampling.
    if period_filter is not None:
        sampling_step = 1
    # Per_series row labels repeat the series names; drop them when the chart
    # already shows those names via a legend or endpoint labels.
    # Resolved models carry .legend (shared base) with the per-chart resolved value.
    legend_visible = resolved_chart.legend.visible
    legend_shows_series = color_field is not None and legend_visible
    # When the table has per_series rows the strip labels serve as the series
    # legend.  Suppress the side legend to avoid redundant ink.  Endpoint labels
    # are orthogonal — both the label strip and the endpoint pane may be visible.
    suppress_legend = has_per_series and legend_shows_series
    # Bar charts: center the value column on the band (dx = max formatted width / 2).
    # Line/area charts: anchor the aligned edge straight on the mark — no dx.
    has_time_unit = bool(spec.get("encoding", {}).get("x", {}).get("timeUnit"))
    entry_dx = (
        _compute_support_table_entry_dx(
            support_table,
            dt_style,
            has_time_unit,
            entry_numerals,
            entry_values,
            x_type=x_type,
        )
        if chart_type == "bar" and resolved_chart.x
        else None
    )
    spec = attach_support_table(
        spec,
        support_table=support_table,
        style=dt_style,
        charts_style=charts_style,
        axis_offset_value=axis_offset_value,
        axis_label_padding=axis_label_padding,
        sampling_step=sampling_step,
        entry_dx=entry_dx,
        entry_numerals=entry_numerals,
        series_order=series_order,
        dark_fills=dark_fills,
        label_period_filter_expr=period_filter,
        axis_y_orient=axis_y_orient,
        suppress_series_labels=False,
    )
    if suppress_legend:
        _suppress_series_legend(spec)
    strip_h = support_table_strip_height(
        support_table,
        dt_style,
        axis_offset_value,
        series_count=series_count,
    )
    if strip_h > 0:
        if dt_style.position == "top":
            # Only charts with a title use title.offset — VL anchors the title
            # group above the outer container, so title.offset shifts it without
            # displacing the plot. Subtitle-only charts (no title element) use
            # bump_padding_top: _measure_vl_title_plot_gap cannot anchor on
            # role-title-text when no title is present.
            has_title = bool(resolved_chart.title)
            if has_title:
                # Use title.offset to reserve strip space so the title's absolute
                # position is invariant to the offset amount (verified under
                # autosize:pad: title stays fixed at (x, title_y) regardless of
                # title.offset value). bump_padding_top shifts the title down with
                # everything else, causing cross-row title misalignment.
                title_block = spec.get("title")
                assert isinstance(title_block, dict), (
                    "spec['title'] must be a dict when resolved_chart has a title; "
                    "finalize_vl (assemble_final_vl) must run before this post-pass"
                )
                # Seed the offset from the theme so config.title.offset is not
                # silently discarded when a spec-level offset overrides it.
                # The theme value lives in charts_style.title.position.offset;
                # None means "let VL decide" — treat as 0 for the spec-level write.
                theme_title_offset = charts_style.title.position.offset
                existing_offset = (
                    theme_title_offset if theme_title_offset is not None else 0.0
                )
                spec["title"] = {**title_block, "offset": existing_offset + strip_h}
            elif padding is not None:
                padding = {
                    **padding,
                    "top": float(padding.get("top", 0)) + strip_h,
                }
            else:
                bump_padding_top(spec, strip_h)
        else:
            if padding is not None:
                padding = {
                    **padding,
                    "bottom": float(padding.get("bottom", 0)) + strip_h,
                }
            else:
                bump_padding_bottom(spec, strip_h)
    return spec, padding
