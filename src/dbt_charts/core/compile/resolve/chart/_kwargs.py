"""Shared kwargs assembly for chart resolvers: text templating, width, and the family style-slot map."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from dbt_charts.core.compile.merge import merge_onto_base, to_padding_style
from dbt_charts.core.compile.models.chart.authored import ChartSupportTable
from dbt_charts.core.compile.models.chart.normalized import (
    Chart,
    KpiChart,
)
from dbt_charts.core.compile.models.chart.normalized._base import (
    _BaseChartFields,
    _CartesianChartFields,
    _GeoChartFields,
    _SharedChartFields,
)
from dbt_charts.core.compile.models.chart.resolved import PartitionAxis
from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
from dbt_charts.core.compile.models.style.authored import (
    LegendStylePatch,
    PaddingStylePatch,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedChartDefaults
from dbt_charts.core.compile.models.style.theme import (
    LegendPosition,
    LegendStyle,
    PaddingStyle,
    SupportTableStyle,
)
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
)
from dbt_charts.core.compile.resolve.style.category_colors import (
    categorical_channel_fields,
)
from dbt_charts.core.compile.resolve.style.palette import substitute_for_alias
from dbt_charts.core.compile.resolve.style.tokens import _resolve_color_tokens
from dbt_charts.core.compile.resolve.style.typography import resolve_title_font
from dbt_charts.core.compile.template.jinja import resolve_jinja_template
from dbt_charts.core.text.predefined_formats import (
    PredefinedNumberFormat,
)

__all__ = [
    "AutomaticLinkCandidate",
    "ChartTextVariables",
    "_EMPTY_CHART_TEXT_VARIABLES",
    "_FAMILY_SLOT",
    "_base_kwargs",
    "_cartesian_kwargs",
    "_geo_kwargs",
    "_resolve_text",
    "_shared_kwargs",
    "_title_font",
    "default_chart_width",
    "preferred_chart_width",
    "resolve_chart_display_title",
]


AutomaticLinkCandidate = str | None


class _LegendPositionOverridePatch(BaseModel):
    """All-Optional carrier for ``ResolvedLegendStyle.position_overridden_by_width``
    -- the one legend fold below with no ``LegendStylePatch`` to ride: that
    field is resolved-only (declared on ``ResolvedLegendStyle``, not on
    ``LegendStyle``/``LegendStylePatch``, the authored surface), so an author
    can never set it even through this carrier. Exists purely so
    ``_base_kwargs`` can fold it in through the same construction-final
    ``merge_onto_base`` path as every other legend decision below, instead of
    a post-hoc ``model_copy(update=...)`` on the already-built
    ``ResolvedLegendStyle`` (banned -- see ``compile/models/AGENTS.md`` and
    ``tests/test_no_replace_on_resolved.py``).
    """

    model_config = ConfigDict(extra="forbid")

    position_overridden_by_width: LegendPosition | None = None


def _bound_scales(
    normalized: _BaseChartFields,
    chart_style_context: ChartStyleContext,
    palette: Sequence[str],
) -> tuple[CategoryColorScale, ...]:
    """Narrow the board's scales to the fields THIS chart actually encodes.

    Every chart carries the board context, but a chart that never draws
    `category` has no business emitting a `category` scale — Vega-Lite would
    reserve legend entries for values the chart does not contain.

    A scale is planned against the BOARD palette, but ``palette`` here is
    this chart's own EFFECTIVE palette — a chart-local ``style.color.
    categorical.palette`` override, or a nested board's shorter theme, can be
    shorter than that. A scale whose highest slot this chart's palette can't
    seat is dropped rather than handed through: `category_colors._slot_for`
    would raise the moment the chart tried to paint that value, turning a board
    that rendered fine before this feature existed into an error just
    because a sibling happens to share the field. Declining here is the same
    "keep the coloring it had today" policy `plan_category_colors` already
    applies to palette exhaustion and the two-chart threshold.
    """
    if not chart_style_context.category_colors:
        return ()
    drawn = categorical_channel_fields(normalized)
    return tuple(
        s
        for s in chart_style_context.category_colors
        if s.field in drawn and max(s.slots.values(), default=-1) < len(palette)
    )


def _base_kwargs(
    normalized: _BaseChartFields,
    chart_style_context: ChartStyleContext,
    channels: dict[str, Any],
    legend_patch: LegendStyle | None = None,
    palette: list[str] | None = None,
    *,
    requested_alias_palette: str | None,
    automatic_link_candidate: AutomaticLinkCandidate,
    layout_padding: PaddingStyle | PaddingStylePatch,
    suppress_legend: bool = False,
    top_legend: Literal["compact", "row", "off"] = "off",
    force_legend_visible: bool = False,
    legend_position_overridden_by_width: LegendPosition | None = None,
) -> dict[str, Any]:
    """Common kwargs for all _BaseResolvedChartFields subclasses.

    Projects palette and legend from the baked cascade into the typed
    shared-envelope fields on _BaseResolvedChartFields. The fat
    ChartStyleContext bag is NOT passed through — only palette, channels,
    and the pre-merged legend are projected.

    legend: caller passes merge_onto_base(chart_style_context.legend, family.legend)
    where family is already the result of merge_onto_base(chart_style_context.<type>, primary).
    The merge layer handles family→chart-local legend cascade declaratively;
    _base_kwargs receives the final resolved value.
    palette: when a chart-local palette override is in effect, pass it here;
    otherwise falls back to chart_style_context.palette.
    requested_alias_palette: pass _effective_requested_alias_palette(chart_style_context,
    primary) — required, no fallback, so a forgotten call site is a type error
    rather than a silently-lost WARN-PALETTE-UNSUPPORTED detector signal.
    layout_padding: pass the caller's already-merged per-family padding (e.g.
    ``bar.padding``) — the same object build_chart_style_context/merge_onto_base
    produced for the rest of that family's style slice.
    suppress_legend: True when endpoint labels replace the color legend. Folds
    visible=False into the legend patch BEFORE the cascade merge so the resolved
    legend is built once with its final visibility — no post-hoc model_copy.
    top_legend: force the legend above the plot, reading left to right like the
    marks it names. "compact" wraps at compact_columns for cards too narrow to
    hold a row; "row" leaves the entry count to the renderer.
    force_legend_visible: True when the chart's own style named a legend
    position, OR the chart's shape itself wants a legend (SeriesNaming's
    top_legend_series was not None) regardless of whether that legend landed
    at the top or fell back to the theme's own position. A theme that hides
    this family's legend by default (line and area do) would otherwise leave
    either case with a legend that never renders -- an authored position with
    nothing to show it, or a top-legend fit rule whose "off" outcome means
    "not at the top", not "not at all". It is folded with the cascade, ahead
    of suppress_legend, so it outranks the theme but never the caller's
    suppression -- bar's suppress_legend carries _stack_legend_should_yield,
    a plot-collapse guard the author does not get to overrule (the chart
    would raise ERR-CHART-PAINTED-NO-MARKS).
    legend_position_overridden_by_width: the author's own position when the
    tiny-width tier forced this legend back to top over it
    (SeriesNaming.legend_position_overridden_by_width), None otherwise. Folded
    in with _LegendPositionOverridePatch rather than LegendStylePatch like the
    others above -- the field is resolved-only (LegendStyle/LegendStylePatch,
    the authored surface, declare no such field), but it still goes through
    the same merge_onto_base construction as every other legend decision here,
    not a post-hoc patch on the finished ResolvedLegendStyle.
    """
    legend = merge_onto_base(chart_style_context.legend, legend_patch)
    if force_legend_visible:
        shown = LegendStylePatch.model_validate({"visible": True})
        legend = merge_onto_base(legend, shown)
    if legend_position_overridden_by_width is not None:
        overridden = _LegendPositionOverridePatch(
            position_overridden_by_width=legend_position_overridden_by_width
        )
        legend = merge_onto_base(legend, overridden)
    if top_legend != "off":
        top = LegendStylePatch.model_validate(
            {
                "visible": True,
                "position": "top",
                "direction": "horizontal",
                "columns": legend.compact_columns if top_legend == "compact" else 0,
            }
        )
        legend = merge_onto_base(legend, top)
    if suppress_legend:
        hidden = LegendStylePatch.model_validate({"visible": False})
        legend = merge_onto_base(legend, hidden)
    if (
        legend.position == "top"
        and legend.direction == "horizontal"
        and legend.title.visible is None
    ):
        no_title = LegendStylePatch.model_validate({"title": {"visible": False}})
        legend = merge_onto_base(legend, no_title)
    # link: false = authored auto_link opt-out — resolves to no link at all.
    link = None if normalized.link is False else normalized.link
    if normalized.link is None:
        link = automatic_link_candidate
    effective_palette = palette if palette is not None else chart_style_context.palette
    # Color tokens in conditional_formatting (including role-indirected ones
    # like category[1]) resolve here, against the active theme's
    # palettes/roles — normalize time has no theme context available yet.
    # Passed as a dict (not per-entry) so the walk's keyed-collection guard
    # applies: only background/font.color/glyph_color are color-bearing, so a
    # scalar predicate value or glyph text that happens to look like a token
    # (e.g. eq: "category[1]") is left alone rather than silently rewritten
    # to hex. The guard is field-name-based, not type-based, so a
    # token-shaped string inside a list field (e.g. `in: [...]`) is not
    # covered — lists carry no field name to check.
    conditional_formatting = (
        _resolve_color_tokens(
            normalized.conditional_formatting,
            chart_style_context.palettes,
            chart_style_context.roles,
        )
        if normalized.conditional_formatting
        else None
    )
    return {
        "id": normalized.id,
        "source_path": normalized.source_path,
        "defined_in_other_file": normalized.defined_in_other_file,
        "query": normalized.query,
        "query_name": normalized.query_name,
        "variable_dependencies": normalized.variable_dependencies,
        "notes": normalized.notes,
        "link": link,
        "conditional_formatting": conditional_formatting,
        "palette": tuple(effective_palette),
        "requested_alias_palette": requested_alias_palette,
        "requested_alias_substitute": (
            substitute_for_alias(requested_alias_palette)
            if requested_alias_palette is not None
            else None
        ),
        "category_colors": _bound_scales(
            normalized, chart_style_context, effective_palette
        ),
        "resolved_channels": channels,
        "legend": legend,
        "layout_padding": to_padding_style(layout_padding),
    }


ChartTextVariables = Mapping[str, Any]
_EMPTY_CHART_TEXT_VARIABLES: ChartTextVariables = MappingProxyType({})


def _resolve_text(value: str, variables: ChartTextVariables) -> str:
    if not variables:
        return value
    return resolve_jinja_template(value, variables, strict=False)


def resolve_chart_display_title(
    normalized: Chart, variables: ChartTextVariables, strict: bool = True
) -> str:
    """Resolve the one title-like field used by normalized terminal charts."""
    value = normalized.label if isinstance(normalized, KpiChart) else normalized.title
    return resolve_jinja_template(value, variables, strict=strict) if value else ""


def _title_font(
    normalized: Chart, chart_local_style_context: ChartStyleContext, width: float
) -> ResolvedFontStyle:
    """Resolve the chart's title font, honoring a chart-local style.title.font override.

    Must receive the chart-local context (``build_chart_style_context``'s
    result), not the board-level one: ``resolve_title_font`` only threads the
    ``family``/``size``/``weight`` triple through its explicit override
    argument — ``color``/``style``/``decoration``/``case``/``line_height``
    are read straight off ``chart_style_context.title.font``, which only
    carries this chart's ``style.title.font`` override once
    ``build_chart_style_context`` has merged it in.
    """
    _s = normalized.style
    _st = getattr(_s, "title", None) if _s is not None else None
    return resolve_title_font(
        chart_local_style_context, width, _st.font if _st is not None else None
    )


def _shared_kwargs(
    normalized: _SharedChartFields,
    variables: ChartTextVariables,
    chart_style_context: ChartStyleContext,
) -> dict[str, Any]:
    """Extra kwargs for _SharedResolvedChartFields subclasses.

    chart_style_context is the chart's own merged style (build_chart_style_context output) —
    background and title overflow are chart-local decisions, so they must
    read the chart-local merge, not the board-level bag.

    ``title``/``subtitle`` are jinja-resolved only, never case-transformed:
    ``chart.title`` also backs non-VL consumers (e.g. the ``data-chart-title``
    wire attribute in rendering.py) that have always emitted the raw authored
    text. Title-case is a VL-presentation-only concern, applied at the VL
    assembly boundary from ``chart.title_style.font.case`` (see
    ``render/chart/translate.py``), not baked in here.
    """
    return {
        "title": _resolve_text(normalized.title, variables),
        "subtitle": _resolve_text(normalized.subtitle, variables),
        "background": chart_style_context.background,
        "title_style": chart_style_context.title,
    }


# Family-style slot on ChartStyleContext per normalized chart type; type
# aliases (donut, map, bubble_map) share their family's slot. layered is absent
# on purpose — it sizes from the board-level charts default.
_FAMILY_SLOT: dict[str, str] = {
    "bar": "bar",
    "histogram": "histogram",
    "line": "line",
    "area": "area",
    "scatter": "scatter",
    "heatmap": "heatmap",
    "pie": "pie",
    "donut": "pie",
    "kpi": "kpi",
    "table": "table",
    "geoshape": "geoshape",
    "map": "geoshape",
    "point_map": "point_map",
    "bubble_map": "point_map",
    "spark_bar": "spark_bar",
    "callout": "callout",
}


def preferred_chart_width(
    normalized: Chart, chart_style_context: ChartStyleContext
) -> float:
    """The preferred width resolve() bakes into ResolvedChart, from config alone.

    Authored chart ``width`` wins; otherwise the chart-local style patch merged
    onto the theme family style supplies ``preferred_width``. Needs no data and
    no full resolution, so the intrinsic-width measurement in
    ``render/sizing.py`` can call it before any chart is resolved.
    """
    authored = normalized.__dict__.get("width")  # cartesian/pie/geo only
    if authored is not None:
        return float(authored)
    family_base = getattr(chart_style_context, _FAMILY_SLOT[normalized.type])
    family = merge_onto_base(family_base, normalized.style)
    return float(family.preferred_width)


def default_chart_width(
    chart_type: str, chart_defaults: ResolvedChartDefaults
) -> float:
    """Board-wide default width for a resolved chart's family, no chart-local patch.

    For render fallback paths that only have a ``ResolvedChart`` in scope —
    no normalized ``Chart``, so no chart-local style patch to merge
    (``render_resolved_chart()``'s public entry point). Callers holding a
    normalized ``Chart`` use ``preferred_chart_width()`` against
    ``ChartStyleContext`` instead, since that also merges any chart-local
    override.
    """
    family_base = getattr(chart_defaults, _FAMILY_SLOT[chart_type])
    return float(family_base.preferred_width)


def _column_is_numeric(data: list[dict[str, Any]], field: str) -> bool:
    """True when the column has values and every non-null one is a real number.

    Gates the compact number-format inheritance below: a string ``y`` column
    (category labels) must NOT get a d3 numeric format grafted on —
    ``format(datum, '.3~s')`` on a string yields NaN and the cell renders
    ``-`` instead of the label. Bools (an ``int`` subclass) are treated as
    non-numeric labels.

    Deliberately stricter than compile's own ``classify_column_type`` — it
    also rejects numeric strings and ``Decimal``, so a value d3 ``format()``
    would choke on renders as its raw label rather than a broken ``-``.
    """
    seen = False
    for row in data:
        raw = row.get(field)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return False
        seen = True
    return seen


def _resolved_support_table(
    support_table: ChartSupportTable | None,
    y: str | list[str] | None,
    data: list[dict[str, Any]],
) -> ChartSupportTable | None:
    """Bake the final support_table: stamp the theme's default number format
    onto entries reading a single *numeric* string y column that carry no
    authored format.

    Single final value — every cartesian family resolver already receives
    the same query rows render sees, so the "is y actually numeric" gate
    (a string y column must keep format=None so the raw label renders
    rather than a formatted "-") is decidable here instead of at render.
    """
    if (
        support_table is None
        or not isinstance(y, str)
        or not _column_is_numeric(data, y)
    ):
        return support_table
    # Use the predefined name, not the resolved spec, so downstream callers
    # (apply_measure_format_to_support_table) preserve the house-notation signal.
    engine_default = str(PredefinedNumberFormat.number)
    # Deferred like the one in _support_table_geometry below: support_table.py imports
    # resolve.style.axis_cascade, so a module-level import here closes a
    # support_table ↔ resolve cycle that only stays quiet while some other module
    # happens to import resolve first.
    from dbt_charts.core.compile.support_table import (  # noqa: PLC0415
        apply_measure_format_to_support_table,
    )

    return apply_measure_format_to_support_table(support_table, engine_default, y)


def _support_table_geometry(
    support_table: ChartSupportTable | None,
    chart_style_context: ChartStyleContext,
    chart_type: str,
    x_label_authored: bool,
    chart_id: str,
    category_axis_vertical: bool,
    axis_y_orient: Literal["left", "right"],
) -> tuple[SupportTableStyle | None, float | None]:
    """Bake the support_table strip's compile-owned geometry: the chart's
    effective SupportTableStyle (with ``position`` resolved to a concrete
    side) and the axis-offset pixel gap the strip reserves below the plot.

    Both are pure functions of the per-chart-cascaded ``chart_style_context``
    plus ``x_label_authored`` (the caller's own ``bool(normalized.x_label)``,
    truthy not ``is not None``, driving the axis cascade's Layer-5
    title-forcing default) —
    the same values ``render/chart/support_table_attachment.py`` used to
    recompute at render time by reaching into ``compile/support_table.py`` with
    a full ``ChartStyleContext``. Baking them here closes that reach-back:
    render reads ``resolved.effective_support_table_style``/
    ``.support_table_axis_offset`` directly, never the cascade context.

    ``category_axis_vertical``/``axis_y_orient`` are the caller's own already-
    resolved orientation facts (a horizontal bar's category axis is vertical;
    ``axis_y_orient`` is that chart's baked ``axis_y.position``) — resolving
    ``position`` here, once, means the ``Resolved*`` contract always carries a
    concrete ``"top"``/``"bottom"``/``"left"``/``"right"``, and a mismatched
    `position:` is a compile-time error rather than a render-time surprise.
    """
    if support_table is None:
        return None, None
    from dbt_charts.core.compile.support_table import (  # noqa: PLC0415
        axis_offset,
        resolve_effective_support_table_style,
        resolve_support_table_position,
    )

    effective_style = resolve_effective_support_table_style(
        chart_style_context, chart_type
    )
    resolved_position = resolve_support_table_position(
        effective_style.position, category_axis_vertical, axis_y_orient, chart_id
    )
    effective_style = effective_style.model_copy(update={"position": resolved_position})
    return effective_style, axis_offset(
        chart_style_context,
        effective_style,
        x_label_authored=x_label_authored,
        chart_type=chart_type,
    )


def _cartesian_kwargs(
    normalized: _CartesianChartFields,
    chart_style_context: ChartStyleContext,
    variables: ChartTextVariables,
    data: list[dict[str, Any]],
    chart_type: str,
    panel_axes: tuple[PartitionAxis, ...],
    category_axis_vertical: bool = False,
    axis_y_orient: Literal["left", "right"] = "left",
) -> dict[str, Any]:
    """Extra kwargs for _CartesianResolvedChartFields subclasses.

    ``chart_type`` is the resolved chart's own ``chart_type`` literal (e.g. "bar",
    "histogram") — not read off ``normalized.type``, which the shared
    ``_CartesianChartFields`` base does not declare (each family subclass declares
    its own ``type`` literal). ``panel_axes`` is the small-multiples partition
    baked once by ``partition()`` at the top of the caller's resolver — every
    cartesian family bakes it here so the field can never be forgotten on a
    new resolver.

    ``category_axis_vertical``/``axis_y_orient`` feed ``support_table.position``
    resolution. Every family but bar has a horizontal category axis
    unconditionally, so they take the defaults (``False``/unused); bar passes
    its own already-resolved ``orientation == "horizontal"`` and baked
    ``axis_y.position``.
    """
    resolved_support_table = _resolved_support_table(
        normalized.support_table, normalized.y, data
    )
    effective_support_table_style, support_table_axis_offset = _support_table_geometry(
        resolved_support_table,
        chart_style_context,
        chart_type,
        bool(normalized.x_label),
        normalized.id,
        category_axis_vertical,
        axis_y_orient,
    )
    return {
        **_shared_kwargs(normalized, variables, chart_style_context),
        "x": normalized.x,
        "y": normalized.y,
        "color": normalized.color,
        "x_label": normalized.x_label,
        "y_label": normalized.y_label,
        "sort": normalized.sort,
        "multiples": normalized.multiples,
        "panel_axes": panel_axes,
        "support_table": resolved_support_table,
        "effective_support_table_style": effective_support_table_style,
        "support_table_axis_offset": support_table_axis_offset,
        "aspect_ratio": normalized.aspect_ratio,
        "min_height": normalized.min_height,
        "max_height": normalized.max_height,
        "format": normalized.format,
    }


def _geo_kwargs(
    normalized: _GeoChartFields,
    chart_style_context: ChartStyleContext,
    variables: ChartTextVariables,
) -> dict[str, Any]:
    """Extra kwargs for _GeoResolvedChartFields subclasses (projection only)."""
    return {
        **_shared_kwargs(normalized, variables, chart_style_context),
        "projection": normalized.projection,
    }
