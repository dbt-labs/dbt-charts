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
from dbt_charts.core.compile.format import (
    column_format,
    resolve_format,
)
from dbt_charts.core.compile.format_value import (
    format_kpi_parts,
    format_temporal_value,
    is_temporal_value,
)
from dbt_charts.core.compile.models.chart.authored import (
    ConditionalRule,
    match_predicate,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedKpiChart,
    ResolvedKpiSupportConfig,
    ResolvedStyleChannel,
)
from dbt_charts.core.compile.models.primitives import (
    FormatConfig,
    FormatTable,
    ResolvedFormat,
    SingleLineFontStyle,
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
    font_weight_as_css,
)
from dbt_charts.core.diagnostics import ERR_INTERNAL
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_KPI_FORMAT_KIND_MISMATCH,
    ERR_KPI_MULTIROW,
    ERR_KPI_NO_DATA,
    ERR_KPI_TEMPORAL_FORMAT_INVALID,
    ERR_KPI_VALUE_COLUMN_NOT_FOUND,
)
from dbt_charts.core.render.board_links import get_link_context, resolve_href
from dbt_charts.core.render.chart.artifacts import ChartRenderData
from dbt_charts.core.render.chart.table_support import (
    compute_scale_domain,
    interpolate_scale_color,
    resolve_conditional_styles,
    resolve_hinge,
    resolve_palette_stops,
)
from dbt_charts.core.render.chart.text_truncation import record_text_truncation
from dbt_charts.core.render.chart.title_overflow import (
    TitleOverflowMode,
    compute_title_limit,
    prepare_title_text,
)
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.svg_utils import (
    authored_kind_attr,
    card_box,
    checked_href,
    escape_attr,
)
from dbt_charts.core.render.utils import resolve_tone_color
from dbt_charts.core.text.case import CaseValue, apply_case
from dbt_charts.core.text.format_d3 import is_time_format
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

# What an SVG text run without a ``font-weight`` attribute paints at.
_BROWSER_DEFAULT_WEIGHT = "400"

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
    raise ChartDataError.from_code(
        ERR_KPI_VALUE_COLUMN_NOT_FOUND,
        chart_id=chart_id,
        value_column=raw,
    )


def _format_value_parts(
    cell: Any,
    format_input: ResolvedFormat | None,
    chart_id: str,
    formats: FormatTable | None = None,
    native: bool = False,
    format_may_be_cascaded: bool = False,
    keep_affix_gaps: bool = False,
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
    philosophy forbids. A ``style.support.format`` mismatch always raises,
    the same as an equivalent mistake would on a table column. A genuinely
    broken strftime directive raises either way.
    Other non-numeric, non-temporal cells render as a plain string with
    empty affixes -- used for status-style KPIs like ``"At risk"``.
    """
    if is_temporal_value(cell):
        # Re-resolved as authored: a name the resolve-time table could not see (a
        # render-time alias) resolves here.
        authored = (
            FormatConfig(
                spec=format_input.spec or None,
                prefix=format_input.prefix or None,
                suffix=format_input.suffix or None,
                notation=format_input.notation,
            )
            if isinstance(format_input, ResolvedFormat)
            else format_input
        )
        dated = column_format("time", formats, authored)
        if not is_time_format(dated.spec):
            if not format_may_be_cascaded:
                raise ChartDataError.from_code(
                    ERR_KPI_FORMAT_KIND_MISMATCH, chart_id=chart_id, spec=dated.spec
                )
            dated = column_format("time", formats)
        spec, date_prefix, date_suffix = dated.spec, dated.prefix, dated.suffix
        try:
            date_text = format_temporal_value(cell, spec)
            return "", f"{date_prefix}{date_text}{date_suffix}", "", False
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
        numeric,
        format_input,
        formats,
        native=native,
        keep_affix_gaps=keep_affix_gaps,
        sign_in_prefix=True,
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
    ``value_font_color`` is a cascade-managed sentinel (KPI slot fonts exclude
    ``color`` from their InheritSlot), so ``None`` means unauthored rather than
    "the theme's ink arrived here".

    No tone arm: the headline value is direction-neutral by design (NYT/FT
    convention) — tone lives on the block it paints, the support row
    (``style.support.tone``), not here.
    """
    if channel_color is not None:
        return channel_color
    if value_font_color:
        return sanitize_color(value_font_color, fallback)
    return fallback


def _own_fill(color: str | None, fallback: str) -> str:
    """A slot's own color when authored, else ``fallback``."""
    fill = sanitize_color(color, fallback)
    assert fill is not None, "sanitize_color with non-None fallback must return str"
    return fill


@dataclass(frozen=True)
class _SlotFont:
    """One KPI text slot's cascade-complete typography."""

    family: str
    size: float
    weight: str
    style: Literal["normal", "italic"]
    decoration: Literal["none", "line-through", "underline"]
    case: CaseValue

    def attrs(self) -> str:
        """SVG font attributes, each led by a space."""
        out = (
            f' font-family="{escape_attr(self.family)}"'
            f' font-size="{escape_attr(self.size)}"'
            f' font-weight="{escape_attr(self.weight)}"'
        )
        if self.style != "normal":
            out += f' font-style="{escape_attr(self.style)}"'
        if self.decoration != "none":
            out += f' text-decoration="{escape_attr(self.decoration)}"'
        return out

    def cased(self, text: str) -> str:
        return apply_case(text, self.case)

    def measure(self, text: str, *, numeric: bool = False) -> float:
        from dbt_charts.core.font_measure import (  # noqa: PLC0415
            get_weighted_font_measurer,
        )

        return get_weighted_font_measurer(
            self.family, self.weight, italic=self.style == "italic", numeric=numeric
        ).measure(text, self.size)


def _slot_font(font: SingleLineFontStyle, path: str) -> _SlotFont:
    family, size, weight = font.family, font.size, font.weight
    style, decoration, case = font.style, font.decoration, font.case
    if (
        family is None
        or size is None
        or weight is None
        or style is None
        or decoration is None
        or case is None
    ):
        raise ValueError(
            f"KPI font {path} is unresolved ({font!r}) — call resolve_style() "
            "so the cascade fills every slot font from kpi.font."
        )
    css_weight = font_weight_as_css(weight)
    return _SlotFont(
        family=family,
        size=float(size),
        # A weight that is not a CSS weight paints as the theme's KPI weight.
        weight=css_weight if css_weight in VALID_FONT_WEIGHTS else "500",
        style=style,
        decoration=decoration,
        case=case,
    )


@dataclass(frozen=True)
class _KpiFonts:
    """Every KPI slot's typography, resolved once per card."""

    value: _SlotFont
    affix: _SlotFont
    glyph: _SlotFont
    label: _SlotFont
    support: _SlotFont
    support_glyph: _SlotFont


def _resolve_kpi_fonts(kpi_config: KpiChartStyle) -> _KpiFonts:
    return _KpiFonts(
        value=_slot_font(kpi_config.value.font, "value.font"),
        affix=_slot_font(kpi_config.value.affix.font, "value.affix.font"),
        glyph=_slot_font(kpi_config.value.glyph.font, "value.glyph.font"),
        label=_slot_font(kpi_config.label.font, "label.font"),
        support=_slot_font(kpi_config.support.font, "support.font"),
        support_glyph=_slot_font(kpi_config.support.glyph.font, "support.glyph.font"),
    )


@dataclass(frozen=True)
class _KpiLayout:
    """Vertical slot geometry for the stacked value / label / support layout."""

    width: float
    height: float
    content_x: float
    value_baseline: float
    # Vertical baselines for affixes within the value line.
    # Prefix ($ etc.) rides at cap height of the value (top-aligned).
    # Glyph (▲▼●) sits at vertical midpoint. Suffix stays on the value baseline.
    prefix_baseline: float
    glyph_baseline: float
    label_baseline_first: float
    label_line_height: float
    label_lines: tuple[str, ...]
    label_original: str
    label_truncated: bool  # True when rendered label differs from original
    support_baseline: float


@dataclass(frozen=True)
class _KpiColors:
    """Resolved fill colors for the value / affix / glyph / label / card surfaces."""

    value_fill: str
    affix_fill: str
    glyph_fill: str
    label_fill: str
    card_fill: str | None
    muted: str


@dataclass(frozen=True)
class _SupportRow:
    """Resolved support-row content + colors. ``None`` means no row to emit."""

    glyph: str
    value_str: str
    explainer: str
    value_fill: str
    glyph_fill: str


def _support_runs(
    support_row: _SupportRow, fonts: _KpiFonts, muted: str
) -> list[tuple[_SlotFont, str, str]]:
    """``(font, fill, text)`` per support tspan: glyph, value, explainer.

    The explainer reads every support font field except color — it stays muted.
    """
    runs: list[tuple[_SlotFont, str, str]] = []
    if support_row.glyph:
        runs.append(
            (fonts.support_glyph, support_row.glyph_fill, support_row.glyph + " ")
        )
    if support_row.value_str:
        runs.append((fonts.support, support_row.value_fill, support_row.value_str))
    if support_row.explainer:
        spacer = " " if support_row.value_str or support_row.glyph else ""
        runs.append((fonts.support, muted, spacer + support_row.explainer))
    return runs


def _runs_width(runs: Sequence[tuple[_SlotFont, str, str]]) -> float:
    return sum(font.measure(text) for font, _fill, text in runs)


def _run_tspans(runs: Sequence[tuple[_SlotFont, str, str]], extra: str = "") -> str:
    return "".join(
        f"<tspan{extra}{font.attrs()} "
        f'fill="{escape_attr(fill)}">{html.escape(text)}</tspan>'
        for font, fill, text in runs
    )


def _emit_card_chrome(
    palette: _KpiColors,
    kpi_config: KpiChartStyle,
    w: float,
    h: float,
    inset: dict[str, float] | None,
) -> str | None:
    """Emit the card background rect, or ``None`` when no chrome to draw."""
    if palette.card_fill is None:
        return None
    if kpi_config.border is None:
        raise RenderError.from_code(
            ERR_INTERNAL, message="kpi.border was not filled by the cascade"
        )
    x, y, w, h = card_box(w, h, inset)
    return (
        f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" '
        f'rx="{kpi_config.border.radius:g}" '
        f'fill="{escape_attr(palette.card_fill)}"/>'
    )


def _resolve_kpi_link(chart_link: str | None) -> str | None:
    """Return the link-context-resolved href if the chart has a link, else ``None``.

    Scheme safety is re-checked by ``checked_href`` at each of this
    function's three call sites, right where the value becomes an
    ``href="..."`` — not here, so the check stays visible at the emission
    point rather than several calls removed from it.
    """
    if not chart_link:
        return None
    ctx = get_link_context()
    return resolve_href(chart_link, ctx) if ctx else chart_link


@dataclass(frozen=True)
class _ValueRun:
    """The headline glyph + prefix + number + suffix, cased and formatted."""

    glyph: str
    prefix: str
    number: str
    suffix: str
    is_numeric: bool


def _emit_value_tspans(
    run: _ValueRun,
    fonts: _KpiFonts,
    palette: _KpiColors,
    value_baseline: float,
    prefix_baseline: float,
    glyph_baseline: float,
) -> list[str]:
    """Build the glyph + prefix + number + suffix tspans for a KPI's value run.

    Used by all three variants — the value composition is variant-invariant; only
    the surrounding layout changes. Each tspan carries its own ``y`` so affixes
    ride their cap-aware baselines.
    """
    tspans: list[str] = []
    if run.glyph:
        tspans.append(
            f'<tspan y="{escape_attr(glyph_baseline)}"{fonts.glyph.attrs()} '
            f'fill="{escape_attr(palette.glyph_fill)}">{html.escape(run.glyph)} </tspan>'
        )
    if run.is_numeric and run.prefix:
        tspans.append(
            f'<tspan y="{escape_attr(prefix_baseline)}"{fonts.affix.attrs()} '
            f'fill="{escape_attr(palette.affix_fill)}">{html.escape(run.prefix)}</tspan>'
        )
    tspans.append(
        f'<tspan y="{escape_attr(value_baseline)}"{fonts.value.attrs()} '
        f'fill="{escape_attr(palette.value_fill)}">{html.escape(run.number)}</tspan>'
    )
    if run.is_numeric and run.suffix:
        tspans.append(
            f'<tspan y="{escape_attr(value_baseline)}"{fonts.affix.attrs()} '
            f'fill="{escape_attr(palette.affix_fill)}" dx="2">{html.escape(run.suffix)}</tspan>'
        )
    return tspans


def _measure_kpi_value_run(run: _ValueRun, fonts: _KpiFonts) -> float:
    """Measured width of the value run, each part in its own slot font.

    ``numeric=True`` on the number measures the tabular-figures advance the
    SVG renders. The trailing ``+2.0`` mirrors the ``dx="2"`` kern emitted
    before the suffix tspan.
    """
    width = 0.0
    if run.glyph:
        width += fonts.glyph.measure(run.glyph + " ")
    if run.is_numeric and run.prefix:
        width += fonts.affix.measure(run.prefix)
    width += fonts.value.measure(run.number, numeric=True)
    if run.is_numeric and run.suffix:
        width += fonts.affix.measure(run.suffix) + 2.0
    return width


def _affix_baselines(value_baseline: float, fonts: _KpiFonts) -> tuple[float, float]:
    """``(prefix_baseline, glyph_baseline)`` riding above the value baseline."""
    return (
        value_baseline - (fonts.value.size - fonts.affix.size) * _AFFIX_ELEVATION,
        value_baseline - (fonts.value.size - fonts.glyph.size) * _AFFIX_ELEVATION,
    )


def _wrap_label_lines(
    label_text: str,
    requested_width: float,
    pad: SpacingValues,
    label: _SlotFont,
    overflow: TitleOverflowMode,
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
        overflow=overflow,
        limit=compute_title_limit(
            requested_width,
            {"left": pad.left, "right": pad.right},
        ),
        font_size=label.size,
        font_family=label.family,
    )
    label_lines = tuple((rendered_label.splitlines() or [label_text])[:2])
    return label_lines, truncated


def _resolve_kpi_layout(
    label_text: str,
    requested_width: float,
    requested_height: float,
    kpi_config: KpiChartStyle,
    fonts: _KpiFonts,
    has_support: bool,
) -> _KpiLayout:
    kpi_rendering = get_chart_rendering().kpi
    pad = kpi_config.content_padding
    overflow = kpi_config.label.overflow
    assert overflow is not None, "kpi.label.overflow unresolved after cascade"

    label_line_height = fonts.label.size + 4.0
    label_top_padding = float(kpi_rendering.minimum_title_value_gap)

    # Inner edge padding (per side) reads from ``kpi.content_padding``.
    # Cap-height + descender awareness make ``pad.top`` / ``pad.bottom``
    # mean *visible whitespace from card edge to ink* (not baseline-to-edge),
    # so 18 vs 5 top/bottom asymmetry is gone.
    cap_height = fonts.value.size * _CAP_HEIGHT_RATIO

    # Value + label + support is a fixed 3-line text block at one consistent
    # rhythm:
    #   line 1: label
    #   line 2: label continuation, or blank
    #   line 3: support, riding the third label baseline regardless of its
    #           own font size.
    value_baseline = pad.top + cap_height
    prefix_baseline, glyph_baseline = _affix_baselines(value_baseline, fonts)
    # Cap-height-aware on the label side too: ``label_top_padding`` reads
    # as visible whitespace from the value's bottom (digits sit on the
    # baseline; lining figures don't descend) to the label's cap top.
    # Adding ``label_font_size`` here would inflate the visible gap by
    # ~30% — the formula now gives the config token its literal meaning.
    label_cap_height = fonts.label.size * _CAP_HEIGHT_RATIO
    label_baseline_first = value_baseline + label_top_padding + label_cap_height
    # When label.overflow forces single-line rendering, no label can wrap to
    # two lines, so the second reserved line is wasted vertical. Row-baseline
    # alignment is preserved because the overflow mode is theme-wide — every
    # KPI in the board shifts by the same amount.
    label_slot_lines = 1 if overflow in {"clip", "truncate"} else 2
    support_baseline = label_baseline_first + label_slot_lines * label_line_height

    # Visible bottom padding = pad.bottom from the last visible text line's
    # descender to card edge (matches the visible top padding above). When
    # ``has_support`` is false, no support <text> is emitted; collapse the
    # reserved third slot so the bottom edge sits at the label slot's last
    # descender instead of a phantom support line. Cards in a row of bare
    # label+value KPIs tighten together; rows that mix support and no-support
    # cards diverge in height — the row layout above picks max-of-children.
    support_descender = fonts.support.size * _DESCENDER_RATIO
    if has_support:
        minimum_card_h = support_baseline + support_descender + pad.bottom
    else:
        label_descender = fonts.label.size * _DESCENDER_RATIO
        label_slot_bottom = (
            label_baseline_first + (label_slot_lines - 1) * label_line_height
        )
        minimum_card_h = label_slot_bottom + label_descender + pad.bottom
    height = max(requested_height, minimum_card_h)

    label_lines, label_truncated = _wrap_label_lines(
        label_text, requested_width, pad, fonts.label, overflow
    )

    return _KpiLayout(
        width=requested_width,
        height=height,
        content_x=pad.left,
        value_baseline=value_baseline,
        prefix_baseline=prefix_baseline,
        glyph_baseline=glyph_baseline,
        label_baseline_first=label_baseline_first,
        label_line_height=label_line_height,
        label_lines=label_lines,
        label_original=label_text,
        label_truncated=label_truncated,
        support_baseline=support_baseline,
    )


def _resolve_kpi_colors(
    row: dict[str, Any],
    main_format: ResolvedFormat | None,
    kpi_config: KpiChartStyle,
    resolved_channels: dict[str, ResolvedStyleChannel],
    chart_background: str | None,
    formats: FormatTable | None = None,
    *,
    board_style: ResolvedStyle,
) -> _KpiColors:
    """Resolve the value/affix/glyph/label fills, card fill, and border."""
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

    # Label color: the label's own slot, else body text color.
    # `style.value.font.color` deliberately does not reach here — it is the
    # value's own slot.
    label_fill = _own_fill(kpi_config.label.font.color, kpi_font_color)

    return _KpiColors(
        value_fill=value_fill,
        # Conditional formatting paints the whole value run, over slot colors.
        affix_fill=channel_color
        or _own_fill(kpi_config.value.affix.font.color, value_fill),
        glyph_fill=channel_color
        or _own_fill(kpi_config.value.glyph.font.color, value_fill),
        label_fill=label_fill,
        card_fill=card_fill,
        muted=_ms.muted,
    )


def kpi_value_text(
    cell: Any,  # type-state: explicit_any — a query cell, dynamically typed
    format_input: ResolvedFormat | None,
    chart_id: str,
    formats: FormatTable | None,
    *,
    native: bool = False,
    format_may_be_cascaded: bool = False,
) -> str:
    """A KPI value as one flat string, for a caller with no per-lane kerning.

    A flat string has no lane kerning, so lanes keep an affix's whitespace.
    """
    prefix, number_str, suffix, _ = _format_value_parts(
        cell,
        format_input,
        chart_id,
        formats,
        native=native,
        format_may_be_cascaded=format_may_be_cascaded,
        keep_affix_gaps=True,
    )
    return f"{prefix}{number_str}{suffix}"


def _resolve_support_row(
    support: ResolvedKpiSupportConfig | None,
    row: dict[str, Any],
    kpi_config: KpiChartStyle,
    fonts: _KpiFonts,
    tones: KpiTonesStyle,
    muted: str,
    chart_id: str,
    formats: FormatTable | None = None,
) -> _SupportRow | None:
    """Resolve the support row's text and colors. Returns None when the row
    is absent or carries no content to emit.

    Below the conditional-formatting outputs, which win over everything:

    - value: ``support.font.color`` > tone > muted
    - glyph: ``support.glyph.font.color`` > tone > ``support.font.color`` > muted

    Each slot's own color wins and tone is the default for both; the glyph
    falls back to the row color only when no tone is set.
    """
    if support is None:
        return None
    s_cell = None
    if support.value is not None:
        s_cell, _ = _resolve_value(support.value, row, chart_id)
    # Support keeps the BI default register — its dense numeric form
    # belongs alongside axis ticks and table cells, not the hero number
    # above it.
    value_str = fonts.support.cased(
        kpi_value_text(s_cell, support.format, chart_id, formats)
    )

    styled = resolve_conditional_styles(support.rules, s_cell, tones)
    authored_glyph = support.glyph or ""  # type-state: silent_fallback — unauthored
    glyph = fonts.support_glyph.cased(
        styled.get("glyph", authored_glyph)  # type-state: silent_fallback — unauthored
    )
    explainer = fonts.support.cased(
        support.label or ""  # type-state: silent_fallback — unauthored
    )
    if not (value_str or explainer or glyph):
        return None

    tone_color = _tone_color(kpi_config.support.tone, tones)
    row_color = _explicit_color_override(kpi_config.support.font.color)
    glyph_color = _explicit_color_override(kpi_config.support.glyph.font.color)
    cf_color = styled.get("color")
    value_fill = cf_color or row_color or tone_color or muted
    glyph_fill = (
        styled.get("glyph_color")
        or cf_color
        or glyph_color
        or tone_color
        or row_color
        or muted
    )

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
    formats: FormatTable | None,
    board_style: ResolvedStyle,
    width: float | None,
    height: float | None,
    data: ChartRenderData,
    inset: dict[str, float] | None,
) -> str:
    """Mechanical KPI SVG assembly."""
    chart_id = chart.id
    requested_w = kpi_config.preferred_width if width is None else width
    requested_h: float = height or kpi_config.default_height

    if not data:
        raise ChartDataError.from_code(ERR_KPI_NO_DATA, chart_id=chart_id)
    if len(data) > 1:
        raise ChartDataError.from_code(
            ERR_KPI_MULTIROW,
            chart_id=chart_id,
            row_count=len(data),
        )

    fonts = _resolve_kpi_fonts(kpi_config)
    row = data[0]
    cell, _value_column = _resolve_value(chart.value, row, chart_id)
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
    value_run = _ValueRun(
        glyph=fonts.glyph.cased(
            chart.glyph or ""  # type-state: silent_fallback — no glyph authored
        ),
        prefix=fonts.affix.cased(prefix),
        number=fonts.value.cased(number_str),
        suffix=fonts.affix.cased(suffix),
        is_numeric=value_is_numeric,
    )
    # Empty string honored as "no label" — renderer skips emission while
    # keeping the slot reserved (so multi-up KPI rows stay aligned).
    label_text = fonts.label.cased(chart.label)

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
        chart.support,
        row,
        kpi_config,
        fonts,
        board_style.tones,
        palette.muted,
        chart_id,
        formats,
    )
    layout = _resolve_kpi_layout(
        label_text,
        requested_w,
        requested_h,
        kpi_config,
        fonts,
        has_support=support_row is not None,
    )
    if layout.label_truncated:
        record_text_truncation(chart_id, "kpi_label", layout.label_original, "label")

    if chart.variant == "inline":
        return _emit_kpi_inline(
            chart=chart,
            label_text=label_text,
            requested_w=requested_w,
            requested_h=requested_h,
            palette=palette,
            support_row=support_row,
            value_run=value_run,
            fonts=fonts,
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
            value_run=value_run,
            fonts=fonts,
            kpi_config=kpi_config,
            inset=inset,
        )
    return _emit_kpi_stacked(
        chart=chart,
        layout=layout,
        palette=palette,
        support_row=support_row,
        value_run=value_run,
        fonts=fonts,
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

    ``chart.style.kpi.background`` is the per-family or chart-local override
    when authored, else ``None`` — the board-level default is applied by the
    color resolution, not here.
    """
    kpi_config = chart.style.kpi
    if kpi_config is None:
        raise ValueError(
            f"KPI chart '{chart.id}' has no resolved kpi style — resolve() must "
            "populate ResolvedKpiStyle.kpi before rendering."
        )
    # background: kpi_config.background is None whenever no chart-local or
    # per-family theme override is set. Passed through as-is (no board
    # fallback) — _resolve_kpi_colors's None-vs-board-background comparison
    # and _explicit_color_override(None) both collapse to "no override" in
    # that case.
    return _render_kpi_svg_core(
        chart=chart,
        chart_background=kpi_config.background,
        kpi_config=kpi_config,
        formats=board_style.chart_defaults.formats,
        board_style=board_style,
        width=width,
        height=height,
        data=data,
        inset=inset,
    )


def _svg(width: float, height: float, parts: list[str]) -> str:
    inner = "\n".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{escape_attr(width)}" height="{escape_attr(height)}" '
        f'viewBox="0 0 {escape_attr(width)} {escape_attr(height)}">'
        f"{inner}</svg>"
    )


def _emit_kpi_stacked(
    chart: ResolvedKpiChart,
    layout: _KpiLayout,
    palette: _KpiColors,
    support_row: _SupportRow | None,
    value_run: _ValueRun,
    fonts: _KpiFonts,
    kpi_config: KpiChartStyle,
    inset: dict[str, float] | None,
) -> str:
    """Mechanical SVG assembly given pre-resolved layout/palette/support."""
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
        value_run,
        fonts,
        palette,
        layout.value_baseline,
        layout.prefix_baseline,
        layout.glyph_baseline,
    )
    value_run_width = _measure_kpi_value_run(value_run, fonts)
    support_runs = (
        [] if support_row is None else _support_runs(support_row, fonts, palette.muted)
    )
    # Alignment is a whole-card choice, so every run on the card must be
    # measured before it is decided — see _card_align.
    label_widths = [fonts.label.measure(line) for line in layout.label_lines]
    run_widths = [value_run_width, *label_widths]
    if support_runs:
        run_widths.append(_runs_width(support_runs))
    card_align = _card_align(
        kpi_config.align,
        available_width,
        run_widths,
        chart.id,
        layout.label_original,
    )
    value_x = _resolve_run_x(
        layout.content_x, available_width, value_run_width, card_align
    )
    kpi_link = _resolve_kpi_link(chart.link)
    if kpi_link:
        parts.append(f'<a href="{escape_attr(checked_href(kpi_link))}">')
    parts.append(
        f'<text x="{escape_attr(value_x)}" y="{escape_attr(layout.value_baseline)}" '
        f'text-anchor="{escape_attr(_TEXT_ANCHOR)}">'
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
            _resolve_run_x(layout.content_x, available_width, w, card_align)
            for w in label_widths
        ]
        parts.append(
            f'<text x="{escape_attr(line_xs[0])}" y="{escape_attr(layout.label_baseline_first)}"'
            f"{authored_kind_attr('label')} "
            f'text-anchor="{escape_attr(_TEXT_ANCHOR)}"{fonts.label.attrs()} '
            f'fill="{escape_attr(palette.label_fill)}">'
            f"{inner_title}"
        )
        for line_index, (line, line_x) in enumerate(
            zip(layout.label_lines, line_xs, strict=True)
        ):
            line_y = layout.label_baseline_first + line_index * layout.label_line_height
            parts.append(
                f'<tspan x="{escape_attr(line_x)}" y="{escape_attr(line_y)}">{html.escape(line)}</tspan>'
            )
        parts.append("</text>")

    if support_runs:
        support_x = _resolve_run_x(
            layout.content_x, available_width, _runs_width(support_runs), card_align
        )
        parts.append(
            f'<text x="{escape_attr(support_x)}" y="{escape_attr(layout.support_baseline)}" '
            f'text-anchor="{escape_attr(_TEXT_ANCHOR)}">'
            f"{_run_tspans(support_runs)}</text>"
        )

    return _svg(layout.width, layout.height, parts)


def _emit_kpi_inline(
    chart: ResolvedKpiChart,
    label_text: str,
    requested_w: float,
    requested_h: float,
    palette: _KpiColors,
    support_row: _SupportRow | None,
    value_run: _ValueRun,
    fonts: _KpiFonts,
    kpi_config: KpiChartStyle,
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

    When the assembled run does not fit the card, fall back to the stacked
    arrangement for this card instead of painting past the card edge —
    ``layout`` is the stacked layout ``_render_kpi_svg_core`` already computed
    unconditionally, so the fallback costs nothing extra.
    """
    pad = kpi_config.content_padding
    content_x = pad.left
    cap_height = fonts.value.size * _CAP_HEIGHT_RATIO
    # Vertically center the value's cap-box in the card.
    value_baseline = (requested_h + cap_height) / 2
    prefix_baseline, glyph_baseline = _affix_baselines(value_baseline, fonts)

    support_runs = (
        [] if support_row is None else _support_runs(support_row, fonts, palette.muted)
    )
    # One _INLINE_GAP before each slot actually emitted; the support glyph/
    # value/explainer chain reads as one joined string with no inner gap.
    run_width = _measure_kpi_value_run(value_run, fonts)
    if label_text:
        run_width += _INLINE_GAP + fonts.label.measure(label_text)
    if support_runs:
        run_width += _INLINE_GAP + _runs_width(support_runs)
    available_width = requested_w - pad.horizontal
    # A bare value (no label, no support) has nothing for the stacked
    # arrangement to move to its own line — `_emit_kpi_stacked` paints the
    # same untruncated number at the same x and size, so falling back would
    # only grow the card and break the row's shared baseline while leaving
    # the identical overflow in place. Stacking can't help; don't try it.
    can_fall_back = label_text or support_row is not None
    if run_width > available_width and can_fall_back:
        record_text_truncation(
            chart.id, "kpi_inline_fallback", chart.variant, "style.variant"
        )
        return _emit_kpi_stacked(
            chart=chart,
            layout=layout,
            palette=palette,
            support_row=support_row,
            value_run=value_run,
            fonts=fonts,
            kpi_config=kpi_config,
            inset=inset,
        )

    parts: list[str] = []
    chrome = _emit_card_chrome(palette, kpi_config, requested_w, requested_h, inset)
    if chrome is not None:
        parts.append(chrome)

    kpi_link = _resolve_kpi_link(chart.link)
    if kpi_link:
        parts.append(f'<a href="{escape_attr(checked_href(kpi_link))}">')

    tspans = _emit_value_tspans(
        value_run, fonts, palette, value_baseline, prefix_baseline, glyph_baseline
    )
    baseline_attr = f' y="{escape_attr(value_baseline)}"'
    gap_attr = f' dx="{escape_attr(_INLINE_GAP)}"'
    if label_text:
        tspans.append(
            f"<tspan{baseline_attr}{authored_kind_attr('label')}{fonts.label.attrs()} "
            f'fill="{escape_attr(palette.label_fill)}"{gap_attr}>'
            f"{html.escape(label_text)}</tspan>"
        )
    if support_runs:
        tspans.append(_run_tspans(support_runs[:1], baseline_attr + gap_attr))
        tspans.append(_run_tspans(support_runs[1:], baseline_attr))

    row_x = _resolve_run_x(
        content_x,
        available_width,
        run_width,
        _card_align(
            kpi_config.align, available_width, [run_width], chart.id, label_text
        ),
    )
    parts.append(
        f'<text x="{escape_attr(row_x)}" y="{escape_attr(value_baseline)}" '
        f'text-anchor="{escape_attr(_TEXT_ANCHOR)}">{"".join(tspans)}</text>'
    )

    if kpi_link:
        parts.append("</a>")

    return _svg(requested_w, requested_h, parts)


def _emit_kpi_compact(
    chart: ResolvedKpiChart,
    label_text: str,
    requested_w: float,
    requested_h: float,
    palette: _KpiColors,
    support_row: _SupportRow | None,
    value_run: _ValueRun,
    fonts: _KpiFonts,
    kpi_config: KpiChartStyle,
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
    pad = kpi_config.content_padding
    content_x = pad.left
    cap_height = fonts.value.size * _CAP_HEIGHT_RATIO
    value_baseline = (requested_h + cap_height) / 2
    prefix_baseline, glyph_baseline = _affix_baselines(value_baseline, fonts)

    # Each payload entry is one tspan: (font, fill, text). Only the label
    # names an authored key (`label:`), so its <text> carries the leaf kind.
    label_runs = [(fonts.label, palette.label_fill, label_text)] if label_text else []
    support_runs = (
        [] if support_row is None else _support_runs(support_row, fonts, palette.muted)
    )
    top_payload: list[tuple[_SlotFont, str, str]] = []
    bottom_payload: list[tuple[_SlotFont, str, str]] = []
    if label_runs and support_runs:
        top_payload, bottom_payload = label_runs, support_runs
    elif label_runs:
        bottom_payload = label_runs
    elif support_row is not None and support_row.value_str and support_row.explainer:
        # Split case — glyph + value on top, explainer (unspaced) on baseline.
        top_payload = support_runs[:-1]
        bottom_payload = [(fonts.support, palette.muted, support_row.explainer)]
    else:
        bottom_payload = support_runs

    # Compact's value column and right column are one rigid block under
    # `align` — the right column's x is derived from the value run's own
    # width, so both must shift by the same amount or the columns separate.
    value_run_width = _measure_kpi_value_run(value_run, fonts)
    available_width = requested_w - pad.horizontal
    right_block_width = max(_runs_width(top_payload), _runs_width(bottom_payload))
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
    # bottom line stays flush with the value baseline: the label's when label
    # and support share the column, else a split-support piece's.
    top_line_size = (
        fonts.label.size if label_runs and support_runs else fonts.support.size
    )
    top_line_dy = top_line_size + 4.0
    top_baseline = value_baseline - top_line_dy

    parts: list[str] = []
    chrome = _emit_card_chrome(palette, kpi_config, requested_w, requested_h, inset)
    if chrome is not None:
        parts.append(chrome)

    kpi_link = _resolve_kpi_link(chart.link)
    if kpi_link:
        parts.append(f'<a href="{escape_attr(checked_href(kpi_link))}">')

    value_tspans = _emit_value_tspans(
        value_run, fonts, palette, value_baseline, prefix_baseline, glyph_baseline
    )
    parts.append(
        f'<text x="{escape_attr(block_x)}" y="{escape_attr(value_baseline)}" '
        f'text-anchor="{escape_attr(_TEXT_ANCHOR)}">{"".join(value_tspans)}</text>'
    )

    def _right_column_text(
        payload: list[tuple[_SlotFont, str, str]], baseline: float
    ) -> str:
        kind = authored_kind_attr("label") if payload is label_runs else ""
        return (
            f'<text x="{escape_attr(right_column_x)}" y="{escape_attr(baseline)}" '
            f'text-anchor="{escape_attr(_TEXT_ANCHOR)}">{_run_tspans(payload, kind)}</text>'
        )

    if top_payload:
        parts.append(_right_column_text(top_payload, top_baseline))
    if bottom_payload:
        parts.append(_right_column_text(bottom_payload, value_baseline))

    if kpi_link:
        parts.append("</a>")

    return _svg(requested_w, requested_h, parts)
