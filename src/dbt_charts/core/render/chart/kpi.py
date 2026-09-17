"""Custom SVG renderer for KPI quantitative-text-object charts.

KPI is rendered as a left-aligned three-element typographic object — no
default card chrome:

    1. Value     (large primary line, optionally prefixed by a glyph)
    2. Label     (Inter 14, dark, may wrap to two lines)
    3. Support   (optional: glyph + value + neutral trailing explainer)

Layout uses fixed internal slots so that labels wrapping to two lines do
not push neighboring KPIs' value baselines out of alignment when several
KPIs sit side-by-side in a row.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from dbt_charts.core.colors import sanitize_color
from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.format import resolve_format
from dbt_charts.core.compile.models.chart.authored import (
    ConditionalRule,
    KpiSupportConfig,
    match_predicate,
)
from dbt_charts.core.compile.models.chart.resolved import (
    FormatState,
    ResolvedKpiChart,
    ResolvedStyleChannel,
)
from dbt_charts.core.compile.models.primitives import (
    SpacingValues,
    ToneLiteral,
)
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedStyle,
)
from dbt_charts.core.compile.models.style.theme import (
    VALID_FONT_WEIGHTS,
    KpiChartStyle,
    KpiTonesStyle,
    TitleStyle,
    font_weight_as_css,
)
from dbt_charts.core.diagnostics import (
    ERR_KPI_FORMAT_KIND_MISMATCH,
    ERR_KPI_TEMPORAL_FORMAT_INVALID,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.board_links import get_link_context, resolve_href
from dbt_charts.core.render.chart.artifacts import ChartRenderData
from dbt_charts.core.render.chart.table_support import (
    compute_scale_domain,
    format_temporal_value,
    interpolate_scale_color,
    is_temporal_value,
    resolve_hinge,
    resolve_palette_stops,
)
from dbt_charts.core.render.chart.text_truncation import record_text_truncation
from dbt_charts.core.render.chart.title_overflow import (
    compute_title_limit,
    prepare_title_text,
    resolve_title_overflow,
)
from dbt_charts.core.render.format_utils import format_kpi_parts
from dbt_charts.core.render.svg_utils import (
    authored_kind_attr,
    border_dash_attrs,
    card_box,
)
from dbt_charts.core.render.utils import resolve_tone_color
from dbt_charts.core.text.format_d3 import is_time_format
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_TIME_SPECS,
    PredefinedTimeFormat,
)
from dbt_charts.core.utils import coerce_numeric_cell

# Typographic ratios for KPI geometry. Tuned by eye against the playground
# specimen.
#
#   _CAP_HEIGHT_RATIO     — fraction of font-size occupied by lining figures.
#                           Used for top-padding (visible whitespace from
#                           card edge to ink) and value→label gap math.
#   _DESCENDER_RATIO      — fraction of font-size occupied below baseline.
#                           Used to size the support row's bottom gutter so
#                           visible bottom padding equals ``pad.bottom``.
#   _AFFIX_ELEVATION      — fraction of (value_font − affix_font) that the
#                           prefix/glyph baseline rides above the value
#                           baseline. Single coefficient; affix and glyph
#                           pick up different absolute elevations because
#                           their size deltas differ. The 0.37 value is
#                           empirical (eyeballed against the specimen);
#                           it does not correspond to a typographic
#                           landmark like cap-top or midpoint.
_CAP_HEIGHT_RATIO = 0.7
_DESCENDER_RATIO = 0.2
_AFFIX_ELEVATION = 0.37

# Horizontal gap between adjacent slots in the inline / compact variants
# (value↔label, label↔support, value↔right-column). Kept identical across
# both new variants so they share a single rhythm. Lives as a module
# constant rather than a theme YAML key because no shipped theme currently
# overrides it; promote to kpi.inline.gap / kpi.compact.column_gap when a
# theme needs to diverge.
_INLINE_GAP = 14.0

# Always "start". Alignment is achieved by shifting a run's `x` (see
# `_resolve_run_x`), never by an "end"/"middle" anchor: every value/label/
# support tspan in this file carries its own absolute `y` (or, for wrapped
# label lines, its own absolute `x`), and SVG 1.1 section 10.5 treats an
# absolute-positioned tspan as opening a new text chunk — an "end"/"middle"
# anchor re-anchors each chunk independently and the run overlaps itself.
_TEXT_ANCHOR = "start"


def _card_align(
    align: Literal["left", "center", "right"] | None,
    available_width: float,
    run_widths: Sequence[float],
    chart_id: str,
    authored_text: str,
) -> Literal["left", "center", "right"] | None:
    """The alignment the card can actually honor, decided once for all runs.

    ``align`` is a whole-card choice (see ``KpiChartStyle.align``): value, label
    and support form one stacked text column at a shared content edge. So the
    decision must be made from the *widest* run, not per run — clamping each run
    independently right-aligns the ones that fit and snaps the overflowing one
    back to the left edge, producing a card with two different alignments that is
    neither what the author asked for nor the pre-``align`` geometry.

    When any run overflows, ``align`` is dropped for the whole card and the
    degradation is reported once via ``WARN-KPI-ALIGN-OVERFLOW``. Shifting an
    overflowing run would give it a negative ``x``, and the card's SVG viewport
    clips at ``x = 0`` — destroying the run's *leading* characters, so a
    right-aligned ``1,234,567,890`` would read as a well-formed, wrong
    ``234,567,890``. Left is the pre-``align`` direction, where overflow spills
    right and reads as visibly truncated.
    """
    if align not in ("right", "center"):
        return align
    if run_widths and max(run_widths) > available_width:
        record_text_truncation(
            chart_id, "kpi_align_overflow", authored_text, "style.align"
        )
        return None
    return align


def _resolve_run_x(
    content_x: float,
    available_width: float,
    run_width: float,
    align: Literal["left", "center", "right"] | None,
) -> float:
    """``x`` for a text run of ``run_width``, given the card's effective align.

    ``align`` must already have come through ``_card_align``, which drops it to
    ``None`` when any run on the card overflows — so ``available_width -
    run_width`` is non-negative here for every run and no clamp is needed.
    """
    if align == "right":
        return content_x + available_width - run_width
    if align == "center":
        return content_x + (available_width - run_width) / 2
    return content_x


def _tone_color(tone: ToneLiteral | None, tones: KpiTonesStyle) -> str | None:
    """Resolve a semantic tone name to the theme-provided color.

    ``tones`` is the board-level ``KpiTonesStyle`` from ``resolved_style.tones``
    — the renderer reads tone hexes from theme YAML rather than hardcoding
    them so themes can rebrand the semantic vocabulary. ``tone`` is typed
    ``ToneLiteral`` so the compile boundary guarantees a valid value; the
    None guard covers KPIs authored without a tone.
    """
    if tone is None:
        return None
    return resolve_tone_color(tone, tones)


def _rule_output_for_channel(rule: ConditionalRule, channel_name: str) -> str | None:
    """Extract the style value from a ConditionalRule for a given KPI channel."""
    if channel_name == "background":
        return rule.background
    if channel_name == "color":
        return rule.font.color if rule.font is not None else None
    return None


def _evaluate_channel_for_row(
    resolved_channels: dict[str, Any],
    channel_name: str,
    row: dict[str, Any],
    fallback: str | None = None,
    col_format: str | None = None,
) -> str | None:
    """Evaluate a style channel against a single data row."""
    ch = resolved_channels.get(channel_name)
    if ch is None:
        return fallback

    if ch.mode == "literal":
        return ch.literal_value

    if ch.mode == "conditional":
        cell_value = row.get(ch.data_field)
        matched: str | None = fallback
        matched_any = False
        default_rule: ConditionalRule | None = None
        for rule in ch.rules:
            if rule.default is True:
                default_rule = rule
                continue
            if match_predicate(rule, cell_value):
                output = _rule_output_for_channel(rule, channel_name)
                if output is not None:
                    matched = output
                matched_any = True
        if not matched_any and default_rule is not None:
            output = _rule_output_for_channel(default_rule, channel_name)
            if output is not None:
                matched = output
        # When no threshold rule matched and a fallback gradient scale is present,
        # evaluate the gradient — this is the "scale shows through otherwise"
        # behavior that mirrors Looker's threshold-over-scale priority.
        if matched is fallback and ch.fallback_scale is not None:
            numeric = coerce_numeric_cell(cell_value)
            if numeric is None:
                return ch.fallback_scale.null_color or fallback
            lo, hi = compute_scale_domain([row], ch.data_field, ch.fallback_scale)
            hinge = resolve_hinge(ch.fallback_scale, lo, hi, col_format)
            return interpolate_scale_color(
                numeric,
                lo,
                hi,
                resolve_palette_stops(ch.fallback_scale),
                hinge=hinge,
                arm_mode=ch.fallback_scale.arm_mode,
            )
        return matched

    if ch.mode == "gradient":
        scale = ch.scale
        if scale is None:
            return fallback
        numeric = coerce_numeric_cell(row.get(ch.data_field))
        if numeric is None:
            return scale.null_color or fallback
        lo, hi = compute_scale_domain([row], ch.data_field, scale)
        hinge = resolve_hinge(scale, lo, hi, col_format)
        return interpolate_scale_color(
            numeric,
            lo,
            hi,
            resolve_palette_stops(scale),
            hinge=hinge,
            arm_mode=scale.arm_mode,
        )

    return fallback


def _resolve_value(raw: str, row: dict[str, Any], chart_id: str) -> tuple[Any, str]:
    """Return ``(cell_value, column_name)`` for a KPI/support block.

    ``raw`` must be a column reference. Raises ``ChartDataError`` when the
    column is not present in the query result row.
    """
    if raw in row:
        return row[raw], raw
    raise ChartDataError(
        f"KPI value column '{raw}' not found in query result.\n"
        "Channels are always column references; constant values come from the query.\n"
        "For a string status value:\n"
        f"  query:\n"
        f"    rows:\n"
        f'      - status: "{raw}"\n'
        f"  value: status\n"
        "For a numeric literal:\n"
        "  query:\n"
        "    rows:\n"
        "      - count: <value>\n"
        "  value: count",
        chart_id=chart_id,
    )


def _format_value_parts(
    cell: Any,
    format_input: FormatState,
    chart_id: str,
    formats: dict[str, Any] | None = None,
    native: bool = False,
    format_may_be_cascaded: bool = False,
) -> tuple[str, str, str, bool]:
    """Format a KPI cell into ``(prefix, number_str, suffix, is_numeric)``.

    Temporal cells are formatted with a strftime spec (``date_short``, or a
    ``style.formats`` alias resolving to one -- an inline ``%``-spec is
    compile-rejected on this slot), defaulting to ``date_short`` when
    unformatted. ``format_may_be_cascaded`` is True only for the headline
    value slot, whose format can be a board-wide cascade default shared
    across every KPI on the board: a chart-local ``style.value.format``
    override and a board-level default merge into the same field before this
    point, so a mismatched (non-strftime) spec there falls back to
    ``date_short`` instead of erroring the whole render -- distinguishing the
    two here would mean carrying provenance on a resolved model, which core's
    philosophy forbids. ``support.format`` is never cascaded
    (``ResolvedKpiChart.support`` is the authored config verbatim), so a
    mismatch there always raises, the same as an equivalent mistake would on
    a table column. A genuinely broken strftime directive raises either way.
    Other non-numeric, non-temporal cells render as a plain string with
    empty affixes -- used for status-style KPIs like ``"At risk"``.
    """
    if is_temporal_value(cell):
        resolved = resolve_format(format_input, formats)
        if not resolved or is_time_format(resolved):
            spec = resolved or PREDEFINED_TIME_SPECS[PredefinedTimeFormat.date_short]
        elif format_may_be_cascaded:
            spec = PREDEFINED_TIME_SPECS[PredefinedTimeFormat.date_short]
        else:
            raise ChartDataError.from_code(
                ERR_KPI_FORMAT_KIND_MISMATCH, chart_id=chart_id, spec=resolved
            )
        try:
            return "", format_temporal_value(cell, spec), "", False
        except ValueError as e:
            raise ChartDataError.from_code(
                ERR_KPI_TEMPORAL_FORMAT_INVALID,
                chart_id=chart_id,
                cell=cell,
                spec=spec,
                reason=str(e),
            ) from e

    numeric = coerce_numeric_cell(cell)
    if numeric is None:
        text = "" if cell is None else str(cell)
        return "", text, "", False
    prefix, number_str, suffix = format_kpi_parts(
        numeric, format_input, formats, native=native
    )
    return prefix, number_str, suffix, True


def _explicit_color_override(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return sanitize_color(value, None)


def _resolve_value_color(
    channel_color: str | None,
    value_font_color: str | None,
    fallback: str,
) -> str:
    """Resolve KPI value color with deterministic precedence.

    Highest → lowest:

    1. ``channel_color`` — data-driven color from ``conditional_formatting``
    2. ``style.value.font.color`` — names this slot alone
    3. ``style.font.color`` (via ``fallback``) — the whole-card slot every
       other family already types this way; theme ``kpi.font.color`` when
       unauthored

    The specific key beats the shared one, as every other style patch resolves.
    ``value_font_color`` is a cascade-managed sentinel (``KpiValueStyle.font``
    excludes ``color`` from its InheritSlot), so ``None`` means unauthored
    rather than "the theme's ink arrived here".

    No tone arm: the headline value is direction-neutral by design (NYT/FT
    convention) — tone lives on the block it paints, the support row
    (``support.tone``), not here. The glyph slot shares this fill (see
    ``_resolve_kpi_colors``) since it has no independent tone source either.
    """
    if channel_color is not None:
        return channel_color
    if value_font_color:
        return sanitize_color(value_font_color, fallback)
    return fallback


@dataclass(frozen=True)
class _KpiLayout:
    """Vertical slot dimensions for the value / label / support layout."""

    width: float
    height: float
    content_x: float
    value_baseline: float
    value_font: float
    affix_font: float
    glyph_font: float
    value_weight: str
    # Vertical baselines for affixes within the value line.
    # Prefix ($ etc.) rides at cap height of the value (top-aligned).
    # Glyph (▲▼●) sits at vertical midpoint. Suffix stays on the value baseline.
    prefix_baseline: float
    glyph_baseline: float
    label_baseline_first: float
    label_font_size: float
    label_font_family: str
    label_weight: str
    label_line_height: float
    label_lines: tuple[str, ...]
    label_original: str
    label_truncated: bool  # True when rendered label differs from original
    support_baseline: float
    support_font_size: float
    # None = inherit body weight (browser/document default). Set explicitly
    # (e.g. "500") to override — wired from ``kpi.font.weight``.
    support_weight: str | None


@dataclass(frozen=True)
class _KpiColors:
    """Resolved fill colors for the value / glyph / label / card surfaces."""

    value_fill: str
    glyph_fill: str
    label_fill: str
    card_fill: str | None
    border_color: str | None
    muted: str


@dataclass(frozen=True)
class _SupportRow:
    """Resolved support-row content + colors. ``None`` means no row to emit."""

    glyph: str
    value_str: str
    explainer: str
    value_fill: str
    glyph_fill: str


def _support_run_text(support_row: _SupportRow) -> str:
    """Plain-text content of a support row's glyph + value + explainer run.

    Mirrors the tspan sequence ``_emit_kpi_stacked``/``_emit_kpi_inline`` build
    (same spacer rule), so a measurer sees exactly what gets painted.
    """
    text = ""
    if support_row.glyph:
        text += support_row.glyph + " "
    if support_row.value_str:
        text += support_row.value_str
    if support_row.explainer:
        spacer = " " if support_row.value_str or support_row.glyph else ""
        text += spacer + support_row.explainer
    return text


def _resolve_label_weight(kpi_config: Any) -> str:
    """Cascade-resolved label font weight; falls back to 500 for non-CSS weights.

    Shared across all three variants. The default theme's ``kpi.label.font.weight``
    cascades from ``kpi.font.weight`` (currently 500); emitting this on the label
    tspan preserves the theme intent rather than silently letting the cascade
    drop through to the SVG default of 400.
    """
    w = kpi_config.label.font.weight
    return (
        font_weight_as_css(w)
        if w is not None and font_weight_as_css(w) in VALID_FONT_WEIGHTS
        else "500"
    )


def _resolve_support_weight(kpi_config: Any) -> str | None:
    """Cascade-resolved support font weight; ``None`` means inherit body weight.

    Stacked emits the weight only when the cascade sets it explicitly. Inline
    and compact follow the same contract so their support tspans match
    stacked's rendering in the default theme.
    """
    w = kpi_config.font.weight
    return (
        font_weight_as_css(w)
        if w is not None and font_weight_as_css(w) in VALID_FONT_WEIGHTS
        else None
    )


def _emit_card_chrome(
    palette: _KpiColors,
    kpi_config: KpiChartStyle,
    w: float,
    h: float,
    inset: dict[str, float] | None,
) -> str | None:
    """Emit the card background rect, or ``None`` when no chrome to draw."""
    if palette.card_fill is None and palette.border_color is None:
        return None
    x, y, w, h = card_box(w, h, inset)
    rect = (
        f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" '
        f'rx="{kpi_config.border.radius:g}" '
        f'fill="{palette.card_fill or "none"}"'
    )
    if palette.border_color:
        rect += (
            f' stroke="{palette.border_color}" stroke-width="1"'
            f"{border_dash_attrs(kpi_config.border)}"
        )
    return rect + "/>"


def _resolve_kpi_link(chart_link: str | None) -> str | None:
    """Return the link-context-resolved href if the chart has a link, else ``None``."""
    if not chart_link:
        return None
    ctx = get_link_context()
    return resolve_href(chart_link, ctx) if ctx else chart_link


def _emit_value_tspans(
    kpi_config: Any,
    palette: _KpiColors,
    prefix: str,
    number_str: str,
    suffix: str,
    value_is_numeric: bool,
    value_baseline: float,
    prefix_baseline: float,
    glyph_baseline: float,
    value_font: float,
    affix_font: float,
    glyph_font: float,
) -> list[str]:
    """Build the glyph + prefix + number + suffix tspans for a KPI's value run.

    Used by all three variants — the value composition is variant-invariant; only
    the surrounding layout changes. Each tspan carries its own ``y`` so affixes
    ride their cap-aware baselines.
    """
    tspans: list[str] = []
    glyph_char = kpi_config.glyph.character or ""
    if glyph_char:
        tspans.append(
            f'<tspan y="{glyph_baseline}" font-size="{glyph_font}" '
            f'fill="{palette.glyph_fill}">{html.escape(glyph_char)} </tspan>'
        )
    if value_is_numeric and prefix:
        tspans.append(
            f'<tspan y="{prefix_baseline}" font-size="{affix_font}" '
            f'fill="{palette.value_fill}">{html.escape(prefix)}</tspan>'
        )
    tspans.append(
        f'<tspan y="{value_baseline}" font-size="{value_font}" '
        f'fill="{palette.value_fill}">{html.escape(number_str)}</tspan>'
    )
    if value_is_numeric and suffix:
        tspans.append(
            f'<tspan y="{value_baseline}" font-size="{affix_font}" '
            f'fill="{palette.value_fill}" dx="2">{html.escape(suffix)}</tspan>'
        )
    return tspans


def _resolve_value_font_spec(
    kpi_config: KpiChartStyle,
) -> tuple[float, str, float, float]:
    """Return ``(value_font, value_weight, affix_font, glyph_font)``.

    Font sizes come from authored theme YAML (kpi.value.font.size, etc.).
    No clamping — theme sets the exact size.
    """
    assert kpi_config.value.font.size is not None, (
        "theme must supply kpi.value.font.size"
    )
    value_font = float(kpi_config.value.font.size)

    weight_input = kpi_config.value.font.weight
    value_weight = (
        font_weight_as_css(weight_input)
        if weight_input is not None
        and font_weight_as_css(weight_input) in VALID_FONT_WEIGHTS
        else "500"
    )

    assert kpi_config.affix.font.size is not None, (
        "theme must supply kpi.affix.font.size"
    )
    affix_font = float(kpi_config.affix.font.size)
    assert kpi_config.glyph.font.size is not None, (
        "theme must supply kpi.glyph.font.size"
    )
    glyph_font = float(kpi_config.glyph.font.size)
    return value_font, value_weight, affix_font, glyph_font


def _wrap_label_lines(
    label_text: str,
    requested_width: float,
    pad: SpacingValues,
    label_font_size: float,
    label_font_family: str,
    title_style: TitleStyle | None,
) -> tuple[tuple[str, ...], bool]:
    """Run the title-overflow wrap and report ``(lines, truncated)``.

    Direct comparison of rendered vs original catches every overflow mode
    (clip, truncate, wrap-two); the "…" sniff misses clip mode which
    shortens without ellipsis. Empty label short-circuits — the slot is
    reserved by the layout but no `<text>` is emitted (see _emit_kpi_stacked).
    """
    if not label_text:
        return (), False
    rendered_label, truncated = prepare_title_text(
        label_text,
        overflow=resolve_title_overflow(title_style),
        limit=compute_title_limit(
            requested_width,
            {"left": pad.left, "right": pad.right},
        ),
        font_size=label_font_size,
        font_family=label_font_family,
    )
    label_lines = tuple((rendered_label.splitlines() or [label_text])[:2])
    return label_lines, truncated


def _resolve_kpi_layout(
    label_text: str,
    requested_width: float,
    requested_height: float,
    kpi_config: KpiChartStyle,
    title_style: TitleStyle,
    has_support: bool,
) -> _KpiLayout:
    kpi_rendering = get_chart_rendering().kpi

    # Label slot reads from kpi.label.font (cascade fills from kpi.font).
    # Sizes and family are guaranteed non-None after resolve_style(); asserts
    # confirm the cascade contract. Weight falls back to "500" for non-CSS values
    # (e.g. theme supplies 701 which isn't a valid CSS weight keyword).
    _label_size = kpi_config.label.font.size
    _support_size = kpi_config.font.size
    assert _label_size is not None, (
        "kpi.label.font.size unresolved — call resolve_style() first"
    )
    assert _support_size is not None, (
        "kpi.font.size unresolved — call resolve_style() first"
    )
    label_font_size = float(_label_size)
    label_weight = _resolve_label_weight(kpi_config)
    _label_family = kpi_config.label.font.family
    assert _label_family is not None, (
        "kpi.label.font.family unresolved — call resolve_style() first"
    )
    label_font_family = _label_family
    support_font_size = float(_support_size)
    pad = kpi_config.content_padding

    value_font, value_weight, affix_font, glyph_font = _resolve_value_font_spec(
        kpi_config
    )
    support_weight = _resolve_support_weight(kpi_config)

    label_line_height = label_font_size + 4.0
    label_top_padding = float(kpi_rendering.minimum_title_value_gap)

    # Inner edge padding (per side) reads from ``kpi.content_padding``.
    # Cap-height + descender awareness make ``pad.top`` / ``pad.bottom``
    # mean *visible whitespace from card edge to ink* (not baseline-to-edge),
    # so 18 vs 5 top/bottom asymmetry is gone.
    cap_height = value_font * _CAP_HEIGHT_RATIO

    # Value + label + support is a fixed 3-line text block at one consistent
    # rhythm:
    #   line 1: label
    #   line 2: label continuation, or blank
    #   line 3: support, riding the third label baseline regardless of its
    #           own font size.
    value_baseline = pad.top + cap_height
    prefix_baseline = value_baseline - (value_font - affix_font) * _AFFIX_ELEVATION
    glyph_baseline = value_baseline - (value_font - glyph_font) * _AFFIX_ELEVATION
    # Cap-height-aware on the label side too: ``label_top_padding`` reads
    # as visible whitespace from the value's bottom (digits sit on the
    # baseline; lining figures don't descend) to the label's cap top.
    # Adding ``label_font_size`` here would inflate the visible gap by
    # ~30% — the formula now gives the config token its literal meaning.
    label_cap_height = label_font_size * _CAP_HEIGHT_RATIO
    label_baseline_first = value_baseline + label_top_padding + label_cap_height
    # When title.overflow forces single-line rendering, no label can wrap to
    # two lines, so the second reserved line is wasted vertical. Row-baseline
    # alignment is preserved because the overflow mode is theme-wide — every
    # KPI in the board shifts by the same amount.
    overflow_mode = resolve_title_overflow(title_style)
    label_slot_lines = 1 if overflow_mode in {"clip", "truncate"} else 2
    support_baseline = label_baseline_first + label_slot_lines * label_line_height

    # Visible bottom padding = pad.bottom from the last visible text line's
    # descender to card edge (matches the visible top padding above). When
    # ``has_support`` is false, no support <text> is emitted; collapse the
    # reserved third slot so the bottom edge sits at the label slot's last
    # descender instead of a phantom support line. Cards in a row of bare
    # label+value KPIs tighten together; rows that mix support and no-support
    # cards diverge in height — the row layout above picks max-of-children.
    support_descender = support_font_size * _DESCENDER_RATIO
    if has_support:
        minimum_card_h = support_baseline + support_descender + pad.bottom
    else:
        label_descender = label_font_size * _DESCENDER_RATIO
        label_slot_bottom = (
            label_baseline_first + (label_slot_lines - 1) * label_line_height
        )
        minimum_card_h = label_slot_bottom + label_descender + pad.bottom
    height = max(requested_height, minimum_card_h)

    label_lines, label_truncated = _wrap_label_lines(
        label_text,
        requested_width,
        pad,
        label_font_size,
        label_font_family,
        title_style,
    )

    return _KpiLayout(
        width=requested_width,
        height=height,
        content_x=pad.left,
        value_baseline=value_baseline,
        value_font=value_font,
        affix_font=affix_font,
        glyph_font=glyph_font,
        value_weight=value_weight,
        prefix_baseline=prefix_baseline,
        glyph_baseline=glyph_baseline,
        label_baseline_first=label_baseline_first,
        label_font_size=label_font_size,
        label_font_family=label_font_family,
        label_weight=label_weight,
        label_line_height=label_line_height,
        label_lines=label_lines,
        label_original=label_text,
        label_truncated=label_truncated,
        support_baseline=support_baseline,
        support_font_size=support_font_size,
        support_weight=support_weight,
    )


def _resolve_kpi_colors(
    row: dict[str, Any],
    main_format: FormatState,
    kpi_config: KpiChartStyle,
    resolved_channels: dict[str, ResolvedStyleChannel],
    chart_background: str | None,
    formats: dict[str, str] | None = None,
    *,
    board_style: ResolvedStyle,
) -> _KpiColors:
    """Resolve the four surface fills (value/glyph/title/card) and border."""
    _ms = board_style

    kpi_format = resolve_format(main_format, formats) or None
    channel_bg = _evaluate_channel_for_row(
        resolved_channels, "background", row, col_format=kpi_format
    )
    channel_color = _evaluate_channel_for_row(
        resolved_channels, "color", row, col_format=kpi_format
    )
    # Card background: channel conditional > explicit chart-level override.
    # chart_background is always the effective background (board or
    # chart-local override). Use it as card fill only when it genuinely differs
    # from the board background — i.e. a chart author explicitly set a different
    # color. Board propagation (same color) is transparent to the KPI card.
    _explicit_chart_bg = (
        chart_background if chart_background != _ms.background else None
    )
    card_fill = (
        channel_bg
        if channel_bg is not None
        else _explicit_color_override(_explicit_chart_bg)
    )

    # kpi.font.color is an InheritSlot filled from charts.font.color by the
    # cascade. After cascade it is always non-None; assert to surface any
    # missing-theme misconfiguration rather than silently passing None.
    kpi_font_color = kpi_config.font.color
    assert kpi_font_color is not None, (
        "kpi_config.font.color must be non-None after cascade"
    )
    value_fill = _resolve_value_color(
        channel_color=channel_color,
        value_font_color=kpi_config.value.font.color,
        fallback=kpi_font_color,
    )
    # Glyph has no independent tone source (tone lives on support only) — it
    # shares the value's neutral/channel-driven fill.
    glyph_fill = value_fill

    # Label color: the label's own slot, else body text color. The legacy
    # "label picks up style.title.font.color" coupling tied two distinct slots
    # together (chart-section title and KPI label) and only worked by reading
    # the authored Patch — i.e. by discriminating "did the chart author
    # override style.title.font.color." After the cascade rename,
    # resolved_style.title.font.color is always populated by the theme
    # default, so the legacy fallthrough to body color silently flipped to a
    # different theme value. The typed slot replaces it: `style.label.font.
    # color` names the label and nothing else. `style.value.font.color`
    # deliberately does not reach here — it is the value's own slot, and
    # pulling the label along would recreate the coupling that was removed.
    label_fill = sanitize_color(kpi_config.label.font.color, kpi_font_color)
    assert label_fill is not None, (
        "sanitize_color with non-None fallback must return str"
    )

    _bc = kpi_config.border.color
    border_color = (
        _explicit_color_override(_bc)
        if _bc.lower() not in {"transparent", "none", ""}
        else None
    )

    return _KpiColors(
        value_fill=value_fill,
        glyph_fill=glyph_fill,
        label_fill=label_fill,
        card_fill=card_fill,
        border_color=border_color,
        muted=_ms.muted,
    )


def _resolve_support_row(
    support: KpiSupportConfig | None,
    row: dict[str, Any],
    tones: KpiTonesStyle,
    muted: str,
    chart_id: str,
    formats: dict[str, str] | None = None,
) -> _SupportRow | None:
    """Resolve the support row's text and colors. Returns None when the row
    is absent or carries no content to emit."""
    if support is None:
        return None
    s_cell = None
    if support.value is not None:
        s_cell, _ = _resolve_value(support.value, row, chart_id)
    # Support keeps the BI default register — its dense numeric form
    # belongs alongside axis ticks and table cells, not the hero number
    # above it.
    s_prefix, s_number_str, s_suffix, _ = _format_value_parts(
        s_cell, support.format, chart_id, formats
    )
    if s_prefix or s_suffix:
        value_str = f"{s_prefix}{s_number_str}{s_suffix}"
    else:
        value_str = s_number_str

    glyph = support.glyph or ""
    explainer = support.label or ""
    if not (value_str or explainer or glyph):
        return None

    tone_color = _tone_color(support.tone, tones)
    value_fill = tone_color if tone_color is not None else muted
    glyph_fill = tone_color if tone_color is not None else value_fill

    return _SupportRow(
        glyph=glyph,
        value_str=value_str,
        explainer=explainer,
        value_fill=value_fill,
        glyph_fill=glyph_fill,
    )


def _render_kpi_svg_core(
    *,
    chart: ResolvedKpiChart,
    chart_background: str | None,
    kpi_config: KpiChartStyle,
    title_style: TitleStyle,
    formats: dict[str, str] | None,
    board_style: ResolvedStyle,
    width: float | None,
    height: float | None,
    data: ChartRenderData,
    inset: dict[str, float] | None,
) -> str:
    """Mechanical KPI SVG assembly.

    ``chart`` carries the scalar KPI fields common to both chart models by
    name (id/value/label/support/variant/link/resolved_channels) — reading
    them via plain attribute access lets one function serve both without
    re-declaring their types. Style-derived fields (kpi_config, title_style,
    chart_background, formats) differ in how each caller maps its own
    style tree, so those are resolved by the caller and passed in directly.
    """
    chart_id = chart.id
    requested_w = kpi_config.preferred_width if width is None else width
    requested_h: float = height or kpi_config.default_height

    if not data:
        raise ChartDataError(
            f"KPI chart '{chart_id}' has no data — query returned 0 rows",
            chart_id=chart_id,
        )
    if len(data) > 1:
        from dbt_charts.core.diagnostics import ERR_KPI_MULTIROW

        raise ChartDataError.from_code(
            ERR_KPI_MULTIROW,
            chart_id=chart_id,
            row_count=len(data),
        )

    raw_value = chart.value
    row = data[0]
    cell, _value_column = _resolve_value(raw_value, row, chart_id)
    # chart.format is the final headline format — resolve() already applied
    # the narrative-notation and SI-compaction defaults against this row's
    # value; render just consumes it (support-block format stays analytic,
    # resolved separately in _resolve_support_row).
    main_format = chart.format
    prefix, number_str, suffix, value_is_numeric = _format_value_parts(
        cell,
        main_format,
        chart_id,
        formats,
        native=chart.format_native,
        format_may_be_cascaded=True,
    )
    # Empty string honored as "no label" — renderer skips emission while
    # keeping the slot reserved (so multi-up KPI rows stay aligned).
    label_text = chart.label or ""
    if label_text:
        from dbt_charts.core.text.case import apply_case

        _label_case = kpi_config.label.font.case
        if _label_case is not None and _label_case != "none":
            label_text = apply_case(label_text, _label_case)

    palette = _resolve_kpi_colors(
        row,
        main_format,
        kpi_config,
        chart.resolved_channels,
        chart_background,
        formats,
        board_style=board_style,
    )
    support_row = _resolve_support_row(
        chart.support, row, board_style.tones, palette.muted, chart_id, formats
    )
    layout = _resolve_kpi_layout(
        label_text,
        requested_w,
        requested_h,
        kpi_config,
        title_style,
        has_support=support_row is not None,
    )
    if layout.label_truncated:
        record_text_truncation(chart_id, "kpi_label", layout.label_original, "label")

    # Family resolution for KPI text. The cascade fills ``kpi.font.family``
    # from root ``style.font.family`` and ``kpi.value.font.family`` from
    # ``kpi.font.family``. Read the resolved values directly. If a caller
    # bypasses the cascade and leaves either field None, the theme/contract
    # is broken — raise loudly rather than emit ``font-family="None"``.
    body_font_family = kpi_config.font.family
    value_font_family = kpi_config.value.font.family
    if body_font_family is None or value_font_family is None:
        raise ValueError(
            "KPI font family is unresolved — call resolve_style() so the "
            "cascade fills kpi.font.family from style.font.family and "
            "kpi.value.font.family from kpi.font.family before rendering."
        )

    # KPI charts always carry a concrete variant (KpiChart defaults to
    # "stacked"); the `| None` case (non-KPI charts, or a pre-variant-field
    # resolve) falls through to stacked — no defensive `or` shim needed.
    if chart.variant == "inline":
        return _emit_kpi_inline(
            chart=chart,
            label_text=label_text,
            requested_w=requested_w,
            requested_h=requested_h,
            palette=palette,
            support_row=support_row,
            prefix=prefix,
            number_str=number_str,
            suffix=suffix,
            value_is_numeric=value_is_numeric,
            value_font_family=value_font_family,
            body_font_family=body_font_family,
            kpi_config=kpi_config,
            layout=layout,
            inset=inset,
        )
    if chart.variant == "compact":
        return _emit_kpi_compact(
            chart=chart,
            label_text=label_text,
            requested_w=requested_w,
            requested_h=requested_h,
            palette=palette,
            support_row=support_row,
            prefix=prefix,
            number_str=number_str,
            suffix=suffix,
            value_is_numeric=value_is_numeric,
            value_font_family=value_font_family,
            body_font_family=body_font_family,
            kpi_config=kpi_config,
            inset=inset,
        )
    return _emit_kpi_stacked(
        chart=chart,
        layout=layout,
        palette=palette,
        support_row=support_row,
        prefix=prefix,
        number_str=number_str,
        suffix=suffix,
        value_is_numeric=value_is_numeric,
        value_font_family=value_font_family,
        body_font_family=body_font_family,
        kpi_config=kpi_config,
        inset=inset,
    )


def render_kpi_svg(
    chart: ResolvedKpiChart,
    data: ChartRenderData,
    width: float | None = None,
    height: float | None = None,
    *,
    board_style: ResolvedStyle,
    inset: dict[str, float] | None = None,
) -> str:
    """Render a KPI from the typed ``ResolvedKpiChart`` model.

    Mapping from V1: ``chart.resolved_style.kpi.X`` -> ``chart.style.kpi.X``;
    ``chart.resolved_style.background/title`` (whole-chart-type merged
    fields with no equivalent) -> the per-family override on
    ``chart.style.kpi`` if authored, else the board-level default — the same
    fallback build_chart_style_context() applies upstream in V1.
    """
    kpi_config = chart.style.kpi
    if kpi_config is None:
        raise ValueError(
            f"KPI chart '{chart.id}' has no resolved kpi style — resolve() must "
            "populate ResolvedKpiStyle.kpi before rendering."
        )
    board_charts = board_style.chart_defaults
    # background: kpi_config.background is None whenever no chart-local or
    # per-family theme override is set. Passed through as-is (no board
    # fallback) — _resolve_kpi_colors's None-vs-board-background comparison
    # and _explicit_color_override(None) both collapse to "no override" in
    # that case, exactly matching V1's materialized-background behavior.
    title_style = (
        kpi_config.title if kpi_config.title is not None else board_charts.title
    )
    return _render_kpi_svg_core(
        chart=chart,
        chart_background=kpi_config.background,
        kpi_config=kpi_config,
        title_style=title_style,
        formats=board_charts.formats,
        board_style=board_style,
        width=width,
        height=height,
        data=data,
        inset=inset,
    )


def _emit_kpi_stacked(
    chart: ResolvedKpiChart,
    layout: _KpiLayout,
    palette: _KpiColors,
    support_row: _SupportRow | None,
    prefix: str,
    number_str: str,
    suffix: str,
    value_is_numeric: bool,
    value_font_family: str,
    body_font_family: str,
    kpi_config: KpiChartStyle,
    inset: dict[str, float] | None,
) -> str:
    """Mechanical SVG assembly given pre-resolved layout/palette/support."""
    from dbt_charts.core.font_measure import get_font_measurer  # noqa: PLC0415

    parts: list[str] = []

    chrome = _emit_card_chrome(palette, kpi_config, layout.width, layout.height, inset)
    if chrome is not None:
        parts.append(chrome)

    # Usable content width, shared by every row's align computation below —
    # value/label/support each anchor to this same right edge/centerline,
    # measuring their own run width independently (see task worksheet for why
    # this is a computed `x`, not `text-anchor="end"`/`"middle"`).
    available_width = layout.width - layout.content_x - kpi_config.content_padding.right

    value_tspans = _emit_value_tspans(
        kpi_config,
        palette,
        prefix,
        number_str,
        suffix,
        value_is_numeric,
        layout.value_baseline,
        layout.prefix_baseline,
        layout.glyph_baseline,
        layout.value_font,
        layout.affix_font,
        layout.glyph_font,
    )
    value_run_width = _measure_kpi_value_run(
        value_font_family,
        kpi_config,
        prefix,
        number_str,
        suffix,
        value_is_numeric,
        layout.value_font,
        layout.affix_font,
        layout.glyph_font,
    )
    # Alignment is a whole-card choice, so every run on the card must be
    # measured before it is decided — see _card_align.
    _label_measurer = get_font_measurer(layout.label_font_family)
    _run_widths = [value_run_width]
    if layout.label_original:
        _run_widths += [
            _label_measurer.measure(line, layout.label_font_size)
            for line in layout.label_lines
        ]
    if support_row is not None:
        _run_widths.append(
            get_font_measurer(body_font_family).measure(
                _support_run_text(support_row), layout.support_font_size
            )
        )
    card_align = _card_align(
        kpi_config.align,
        available_width,
        _run_widths,
        chart.id,
        layout.label_original,
    )
    value_x = _resolve_run_x(
        layout.content_x, available_width, value_run_width, card_align
    )
    # font-weight on the parent <text> so glyph/prefix/value/suffix all inherit
    # the same weight. The cascade resolves it from ``kpi.value.font.weight``.
    kpi_link = _resolve_kpi_link(chart.link)
    if kpi_link:
        parts.append(f'<a href="{html.escape(kpi_link, quote=True)}">')
    parts.append(
        f'<text x="{value_x}" y="{layout.value_baseline}" '
        f'text-anchor="{_TEXT_ANCHOR}" font-family="{value_font_family}" '
        f'font-weight="{layout.value_weight}">'
        f"{''.join(value_tspans)}</text>"
    )
    if kpi_link:
        parts.append("</a>")

    # Label — fixed two-line slot, top-aligned within it. The slot is always
    # reserved (so support baselines align across a row of mixed-length
    # labels) even when the author set ``label: ""`` — only the <text> is
    # skipped in that case.
    if layout.label_original:
        # Emit inner <title> when the rendered text differs from the
        # original — catches all overflow modes (clip, truncate, wrap-two).
        inner_title = (
            f"<title>{html.escape(layout.label_original)}</title>"
            if layout.label_truncated
            else ""
        )
        line_xs = [
            _resolve_run_x(
                layout.content_x,
                available_width,
                _label_measurer.measure(line, layout.label_font_size),
                card_align,
            )
            for line in layout.label_lines
        ]
        parts.append(
            f'<text x="{line_xs[0]}" y="{layout.label_baseline_first}"'
            f"{authored_kind_attr('label')} "
            f'text-anchor="{_TEXT_ANCHOR}" font-family="{layout.label_font_family}" '
            f'font-size="{layout.label_font_size}" fill="{palette.label_fill}" '
            f'font-weight="{layout.label_weight}">'
            f"{inner_title}"
        )
        for line_index, (line, line_x) in enumerate(
            zip(layout.label_lines, line_xs, strict=True)
        ):
            line_y = layout.label_baseline_first + line_index * layout.label_line_height
            parts.append(
                f'<tspan x="{line_x}" y="{line_y}">{html.escape(line)}</tspan>'
            )
        parts.append("</text>")

    # Support row — glyph + value + neutral explainer.
    if support_row is not None:
        s_tspans: list[str] = []
        if support_row.glyph:
            s_tspans.append(
                f'<tspan fill="{support_row.glyph_fill}">'
                f"{html.escape(support_row.glyph)} </tspan>"
            )
        if support_row.value_str:
            s_tspans.append(
                f'<tspan fill="{support_row.value_fill}">'
                f"{html.escape(support_row.value_str)}</tspan>"
            )
        if support_row.explainer:
            spacer = " " if support_row.value_str or support_row.glyph else ""
            s_tspans.append(
                f'<tspan fill="{palette.muted}">'
                f"{html.escape(spacer + support_row.explainer)}</tspan>"
            )
        # Support always uses the body sans, never the value family — keeps
        # editorial KPIs (serif value) reading right (sans support).
        weight_attr = (
            f' font-weight="{layout.support_weight}"' if layout.support_weight else ""
        )
        support_x = _resolve_run_x(
            layout.content_x,
            available_width,
            get_font_measurer(body_font_family).measure(
                _support_run_text(support_row), layout.support_font_size
            ),
            card_align,
        )
        parts.append(
            f'<text x="{support_x}" y="{layout.support_baseline}" '
            f'text-anchor="{_TEXT_ANCHOR}" font-family="{body_font_family}" '
            f'font-size="{layout.support_font_size}"{weight_attr}>'
            f"{''.join(s_tspans)}</text>"
        )

    inner = "\n".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{layout.width}" height="{layout.height}" '
        f'viewBox="0 0 {layout.width} {layout.height}">'
        f"{inner}</svg>"
    )


def _emit_kpi_inline(
    chart: ResolvedKpiChart,
    label_text: str,
    requested_w: float,
    requested_h: float,
    palette: _KpiColors,
    support_row: _SupportRow | None,
    prefix: str,
    number_str: str,
    suffix: str,
    value_is_numeric: bool,
    value_font_family: str,
    body_font_family: str,
    kpi_config: Any,
    layout: _KpiLayout,
    inset: dict[str, float] | None,
) -> str:
    """Inline variant — value, label, support baseline-aligned on a single row.

    Authoring rules (from the variant design):
    - All four cases (full, label-only, support-only, value-only) render on
      one row.
    - Tone color stays on the support glyph + value; the support label and the
      metric `label` stay muted (Vercel's "color is meaningful only" rule).
    - The horizontal gap between adjacent slots is `_INLINE_GAP`, matching
      compact's column gap.

    Implementation: emit a single ``<text>`` with sequential ``<tspan>``s that
    flow left-to-right using ``dx`` to insert gaps. Affixes still ride their
    cap-aware baselines via per-tspan ``y``; the row baseline is the value's.

    When the assembled run does not fit the card (measured with the same
    primitive compact uses for its value column, ``_measure_kpi_value_run``),
    fall back to the stacked arrangement for this card instead of painting
    past the card edge — ``layout`` is the stacked layout ``_render_kpi_svg_core``
    already computed unconditionally, so the fallback costs nothing extra.
    """
    value_font, value_weight, affix_font, glyph_font = _resolve_value_font_spec(
        kpi_config
    )

    pad = kpi_config.content_padding
    content_x = pad.left
    cap_height = value_font * _CAP_HEIGHT_RATIO
    # Vertically center the value's cap-box in the card.
    value_baseline = (requested_h + cap_height) / 2
    prefix_baseline = value_baseline - (value_font - affix_font) * _AFFIX_ELEVATION
    glyph_baseline = value_baseline - (value_font - glyph_font) * _AFFIX_ELEVATION

    _label_size = kpi_config.label.font.size
    _support_size = kpi_config.font.size
    assert _label_size is not None, (
        "kpi.label.font.size unresolved — call resolve_style() first"
    )
    assert _support_size is not None, (
        "kpi.font.size unresolved — call resolve_style() first"
    )
    label_font_size = float(_label_size)
    support_font_size = float(_support_size)

    _label_family = kpi_config.label.font.family
    assert _label_family is not None, (
        "kpi.label.font.family unresolved — call resolve_style() first"
    )
    label_font_family = _label_family

    label_weight = _resolve_label_weight(kpi_config)
    support_weight = _resolve_support_weight(kpi_config)

    run_width = _measure_kpi_inline_run_width(
        value_font_family,
        body_font_family,
        label_font_family,
        kpi_config,
        prefix,
        number_str,
        suffix,
        value_is_numeric,
        value_font,
        affix_font,
        glyph_font,
        label_text,
        label_font_size,
        support_row,
        support_font_size,
    )
    available_width = requested_w - pad.horizontal
    # A bare value (no label, no support) has nothing for the stacked
    # arrangement to move to its own line — `_emit_kpi_stacked` paints the
    # same untruncated number at the same x and size, so falling back would
    # only grow the card and break the row's shared baseline while leaving
    # the identical overflow in place. Stacking can't help; don't try it.
    can_fall_back = label_text or support_row is not None
    if run_width > available_width and can_fall_back:
        record_text_truncation(
            chart.id, "kpi_inline_fallback", chart.variant, "variant"
        )
        return _emit_kpi_stacked(
            chart=chart,
            layout=layout,
            palette=palette,
            support_row=support_row,
            prefix=prefix,
            number_str=number_str,
            suffix=suffix,
            value_is_numeric=value_is_numeric,
            value_font_family=value_font_family,
            body_font_family=body_font_family,
            kpi_config=kpi_config,
            inset=inset,
        )

    parts: list[str] = []
    chrome = _emit_card_chrome(palette, kpi_config, requested_w, requested_h, inset)
    if chrome is not None:
        parts.append(chrome)

    kpi_link = _resolve_kpi_link(chart.link)
    if kpi_link:
        parts.append(f'<a href="{html.escape(kpi_link, quote=True)}">')

    tspans = _emit_value_tspans(
        kpi_config,
        palette,
        prefix,
        number_str,
        suffix,
        value_is_numeric,
        value_baseline,
        prefix_baseline,
        glyph_baseline,
        value_font,
        affix_font,
        glyph_font,
    )

    if label_text:
        tspans.append(
            f'<tspan y="{value_baseline}"'
            f"{authored_kind_attr('label')} "
            f'font-family="{label_font_family}" '
            f'font-size="{label_font_size}" '
            f'font-weight="{label_weight}" '
            f'fill="{palette.label_fill}" dx="{_INLINE_GAP}">'
            f"{html.escape(label_text)}</tspan>"
        )

    if support_row is not None:
        support_weight_attr = (
            f' font-weight="{support_weight}"' if support_weight else ""
        )
        first_dx = _INLINE_GAP
        if support_row.glyph:
            tspans.append(
                f'<tspan y="{value_baseline}" font-family="{body_font_family}" '
                f'font-size="{support_font_size}"{support_weight_attr} '
                f'fill="{support_row.glyph_fill}" dx="{first_dx}">'
                f"{html.escape(support_row.glyph)} </tspan>"
            )
            first_dx = 0
        if support_row.value_str:
            dx_attr = f' dx="{first_dx}"' if first_dx else ""
            tspans.append(
                f'<tspan y="{value_baseline}" font-family="{body_font_family}" '
                f'font-size="{support_font_size}"{support_weight_attr} '
                f'fill="{support_row.value_fill}"{dx_attr}>'
                f"{html.escape(support_row.value_str)}</tspan>"
            )
            first_dx = 0
        if support_row.explainer:
            spacer = " " if support_row.value_str or support_row.glyph else ""
            dx_attr = f' dx="{first_dx}"' if first_dx else ""
            tspans.append(
                f'<tspan y="{value_baseline}" font-family="{body_font_family}" '
                f'font-size="{support_font_size}"{support_weight_attr} '
                f'fill="{palette.muted}"{dx_attr}>'
                f"{html.escape(spacer + support_row.explainer)}</tspan>"
            )

    row_x = _resolve_run_x(
        content_x,
        available_width,
        run_width,
        _card_align(
            kpi_config.align, available_width, [run_width], chart.id, label_text
        ),
    )
    parts.append(
        f'<text x="{row_x}" y="{value_baseline}" text-anchor="{_TEXT_ANCHOR}" '
        f'font-family="{value_font_family}" font-weight="{value_weight}">'
        f"{''.join(tspans)}</text>"
    )

    if kpi_link:
        parts.append("</a>")

    inner = "\n".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{requested_w}" height="{requested_h}" '
        f'viewBox="0 0 {requested_w} {requested_h}">'
        f"{inner}</svg>"
    )


def _measure_kpi_value_run(
    value_font_family: str,
    kpi_config: Any,
    prefix: str,
    number_str: str,
    suffix: str,
    value_is_numeric: bool,
    value_font: float,
    affix_font: float,
    glyph_font: float,
) -> float:
    """Measured width of the value run for compact right-column positioning.

    Uses the same font measurer the rest of the render layer relies on for
    text layout (``font_measure.get_font_measurer``), with ``numeric=True``
    on the number portion so we measure the tabular-figures advance the SVG
    will actually render. The trailing ``+2.0`` mirrors the ``dx="2"`` kern
    emitted before the suffix tspan.

    Not exact: ``get_font_measurer`` never passes a weight, so every slot
    measures the regular (400) instance while the markup can emit a heavier
    ``font-weight``; ``get_font_path`` special-cases only Source Serif, so a
    body-font slot measures the generic Inter stand-in even when the theme
    paints a different family. Both are one-directional under-measures — the
    real advance is always at or above this number, never below — which is
    exactly the safe direction for compact's right-column offset (worst case,
    a little tighter than ideal, never overlapping) and for
    ``_measure_kpi_inline_run_width``'s fit predicate (worst case, a
    should-have-fallen-back run that doesn't, not a false fallback).
    """
    from dbt_charts.core.font_measure import (  # noqa: PLC0415
        get_font_measurer,
    )

    main = get_font_measurer(value_font_family)
    numeric = get_font_measurer(value_font_family, numeric=True)

    width = 0.0
    glyph_char_value = kpi_config.glyph.character or ""
    if glyph_char_value:
        width += main.measure(glyph_char_value + " ", glyph_font)
    if value_is_numeric and prefix:
        width += main.measure(prefix, affix_font)
    width += numeric.measure(number_str, value_font)
    if value_is_numeric and suffix:
        width += main.measure(suffix, affix_font) + 2.0
    return width


def _measure_kpi_inline_run_width(
    value_font_family: str,
    body_font_family: str,
    label_font_family: str,
    kpi_config: KpiChartStyle,
    prefix: str,
    number_str: str,
    suffix: str,
    value_is_numeric: bool,
    value_font: float,
    affix_font: float,
    glyph_font: float,
    label_text: str,
    label_font_size: float,
    support_row: _SupportRow | None,
    support_font_size: float,
) -> float:
    """Measured width of the inline variant's full baseline run.

    Built on ``_measure_kpi_value_run`` (the value-run primitive `_emit_kpi_compact`
    also uses) plus the label and support slots, one ``_INLINE_GAP`` before each
    slot that is actually emitted — mirroring the ``dx`` sequence
    ``_emit_kpi_inline`` renders, where the support glyph/value/explainer chain
    reads as one joined string with no gap between its own sub-parts.
    """
    from dbt_charts.core.font_measure import (  # noqa: PLC0415
        get_font_measurer,
    )

    width = _measure_kpi_value_run(
        value_font_family,
        kpi_config,
        prefix,
        number_str,
        suffix,
        value_is_numeric,
        value_font,
        affix_font,
        glyph_font,
    )
    if label_text:
        label_measurer = get_font_measurer(label_font_family)
        width += _INLINE_GAP + label_measurer.measure(label_text, label_font_size)
    if support_row is not None:
        support_measurer = get_font_measurer(body_font_family)
        width += _INLINE_GAP + support_measurer.measure(
            _support_run_text(support_row), support_font_size
        )
    return width


def _measure_payload_width(
    payload: list[tuple[str, float, str | None, str, str, str]],
) -> float:
    """Total measured width of a compact right-column payload's tspans.

    ``payload`` is ``(font_family, font_size, font_weight, leaf_kind, fill,
    text)`` per tspan — see ``_emit_kpi_compact``'s top/bottom payload shape.
    """
    from dbt_charts.core.font_measure import get_font_measurer  # noqa: PLC0415

    return sum(
        get_font_measurer(family).measure(text, size)
        for family, size, _weight, _kind, _fill, text in payload
    )


def _emit_kpi_compact(
    chart: ResolvedKpiChart,
    label_text: str,
    requested_w: float,
    requested_h: float,
    palette: _KpiColors,
    support_row: _SupportRow | None,
    prefix: str,
    number_str: str,
    suffix: str,
    value_is_numeric: bool,
    value_font_family: str,
    body_font_family: str,
    kpi_config: Any,
    inset: dict[str, float] | None,
) -> str:
    """Compact variant — 2-column with bottom right baseline-aligned to value.

    Right column rules (single rule across all cases):
    - Up to 2 lines; the bottom line always sits on the value baseline.
    - ``label + support`` → top = label, bottom = support (atomic). Canonical.
    - ``only label`` → bottom = label, top empty.
    - ``only support`` with both ``value`` and ``label`` sub-parts → top =
      ``glyph + value``, bottom = ``support label``. Split for compactness.
    - ``only support`` with a single sub-part → that piece on the baseline.
    - ``neither`` → just the value.

    Tone color stays on the glyph + support value; the bottom split-out
    support label is muted (matches inline + stacked treatment).
    """
    value_font, value_weight, affix_font, glyph_font = _resolve_value_font_spec(
        kpi_config
    )

    pad = kpi_config.content_padding
    content_x = pad.left
    cap_height = value_font * _CAP_HEIGHT_RATIO
    value_baseline = (requested_h + cap_height) / 2
    prefix_baseline = value_baseline - (value_font - affix_font) * _AFFIX_ELEVATION
    glyph_baseline = value_baseline - (value_font - glyph_font) * _AFFIX_ELEVATION

    _label_size = kpi_config.label.font.size
    _support_size = kpi_config.font.size
    assert _label_size is not None, (
        "kpi.label.font.size unresolved — call resolve_style() first"
    )
    assert _support_size is not None, (
        "kpi.font.size unresolved — call resolve_style() first"
    )
    label_font_size = float(_label_size)
    support_font_size = float(_support_size)

    _label_family = kpi_config.label.font.family
    assert _label_family is not None, (
        "kpi.label.font.family unresolved — call resolve_style() first"
    )
    label_font_family = _label_family

    label_weight = _resolve_label_weight(kpi_config)
    support_weight = _resolve_support_weight(kpi_config)
    # Support weight inherits the body weight when unset; tspans must skip the
    # attribute entirely in that case (an empty string would render as the
    # browser default), so a separate sentinel keeps the attribute off.
    # The trailing element is the leaf kind: the label is an authored key
    # (`label:`), the support row is derived, so only one slot names one.
    label_slot = (label_font_family, label_font_size, label_weight, "label")
    support_slot = (body_font_family, support_font_size, support_weight, "")

    # Build right-column payloads. Each tspan carries its own font-size and
    # font-weight so the cascade reaches it — themes that diverge label and
    # support sizes or weights get the right value per slot. Tuple shape:
    # (font_family, font_size, font_weight_or_None, leaf_kind_or_empty, fill, text).
    top_payload: list[tuple[str, float, str | None, str, str, str]] = []
    bottom_payload: list[tuple[str, float, str | None, str, str, str]] = []
    glyph_char_support = support_row.glyph if support_row else ""
    has_label = bool(label_text)
    has_support = support_row is not None
    if has_label and has_support:
        assert support_row is not None  # narrow for mypy
        top_payload.append((*label_slot, palette.label_fill, label_text))
        if glyph_char_support:
            bottom_payload.append(
                (*support_slot, support_row.glyph_fill, glyph_char_support + " ")
            )
        if support_row.value_str:
            bottom_payload.append(
                (*support_slot, support_row.value_fill, support_row.value_str)
            )
        if support_row.explainer:
            spacer = " " if bottom_payload else ""
            bottom_payload.append(
                (*support_slot, palette.muted, spacer + support_row.explainer)
            )
    elif has_label:
        bottom_payload.append((*label_slot, palette.label_fill, label_text))
    elif has_support:
        assert support_row is not None  # narrow for mypy
        has_value = bool(support_row.value_str)
        has_explainer = bool(support_row.explainer)
        if has_value and has_explainer:
            # Split case — glyph + value on top, explainer on baseline.
            if glyph_char_support:
                top_payload.append(
                    (*support_slot, support_row.glyph_fill, glyph_char_support + " ")
                )
            top_payload.append(
                (*support_slot, support_row.value_fill, support_row.value_str)
            )
            bottom_payload.append((*support_slot, palette.muted, support_row.explainer))
        else:
            # Single sub-part — keep it on baseline as atomic support.
            if glyph_char_support:
                bottom_payload.append(
                    (*support_slot, support_row.glyph_fill, glyph_char_support + " ")
                )
            if has_value:
                bottom_payload.append(
                    (*support_slot, support_row.value_fill, support_row.value_str)
                )
            if has_explainer:
                spacer = " " if bottom_payload else ""
                bottom_payload.append(
                    (*support_slot, palette.muted, spacer + support_row.explainer)
                )

    # Measured (not estimated) width of the value run — measurer reads the
    # actual font metrics, with numeric=True picking the tabular-figures
    # advance for the number itself.
    value_run_width = _measure_kpi_value_run(
        value_font_family,
        kpi_config,
        prefix,
        number_str,
        suffix,
        value_is_numeric,
        value_font,
        affix_font,
        glyph_font,
    )
    # Compact's value column and right column are one rigid block under
    # `align` — the right column's x is derived from the value run's own
    # width, so both must shift by the same amount or the columns separate.
    available_width = requested_w - pad.horizontal
    right_block_width = max(
        _measure_payload_width(top_payload), _measure_payload_width(bottom_payload)
    )
    has_right_column = bool(top_payload or bottom_payload)
    block_width = (
        value_run_width + _INLINE_GAP + right_block_width
        if has_right_column
        else value_run_width
    )
    block_x = _resolve_run_x(
        content_x,
        available_width,
        block_width,
        _card_align(
            kpi_config.align, available_width, [block_width], chart.id, label_text
        ),
    )
    right_column_x = block_x + value_run_width + _INLINE_GAP

    # Top line sits one top-line-font line-height above the baseline so the
    # bottom line stays flush with the value baseline. The top line is the
    # label when label + support are both present (label_font_size); otherwise
    # it's a split-support piece (support_font_size). Themes that diverge the
    # two sizes get the correct spacing per case.
    top_line_font_size = (
        label_font_size if (has_label and has_support) else support_font_size
    )
    top_line_dy = top_line_font_size + 4.0
    top_baseline = value_baseline - top_line_dy

    parts: list[str] = []
    chrome = _emit_card_chrome(palette, kpi_config, requested_w, requested_h, inset)
    if chrome is not None:
        parts.append(chrome)

    kpi_link = _resolve_kpi_link(chart.link)
    if kpi_link:
        parts.append(f'<a href="{html.escape(kpi_link, quote=True)}">')

    value_tspans = _emit_value_tspans(
        kpi_config,
        palette,
        prefix,
        number_str,
        suffix,
        value_is_numeric,
        value_baseline,
        prefix_baseline,
        glyph_baseline,
        value_font,
        affix_font,
        glyph_font,
    )
    parts.append(
        f'<text x="{block_x}" y="{value_baseline}" text-anchor="{_TEXT_ANCHOR}" '
        f'font-family="{value_font_family}" font-weight="{value_weight}">'
        f"{''.join(value_tspans)}</text>"
    )

    # Right-column lines — each tspan carries its own font-size and
    # font-weight so kpi.label.font.* and kpi.font.* both flow through the
    # cascade (an earlier bug let support_font_size and the SVG default
    # weight 400 leak across label tspans, suppressing theme intent).
    def _render_right_column_text(
        payload: list[tuple[str, float, str | None, str, str, str]],
        baseline: float,
    ) -> str:
        body = "".join(
            f'<tspan font-family="{family}" font-size="{size}"'
            + (f' font-weight="{weight}"' if weight else "")
            + (authored_kind_attr(kind) if kind else "")
            + f' fill="{fill}">{html.escape(text)}</tspan>'
            for family, size, weight, kind, fill, text in payload
        )
        return (
            f'<text x="{right_column_x}" y="{baseline}" '
            f'text-anchor="{_TEXT_ANCHOR}">{body}</text>'
        )

    if top_payload:
        parts.append(_render_right_column_text(top_payload, top_baseline))
    if bottom_payload:
        parts.append(_render_right_column_text(bottom_payload, value_baseline))

    if kpi_link:
        parts.append("</a>")

    inner = "\n".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{requested_w}" height="{requested_h}" '
        f'viewBox="0 0 {requested_w} {requested_h}">'
        f"{inner}</svg>"
    )
