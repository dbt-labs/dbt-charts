"""Shared kwargs assembly for chart resolvers: text templating, width, and the family style-slot map."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from dbt_charts.core.compile.format import column_format
from dbt_charts.core.compile.merge import to_padding_style
from dbt_charts.core.compile.models.chart.authored import ChartSupportTable
from dbt_charts.core.compile.models.chart.normalized import (
    Chart,
    PieChart,
)
from dbt_charts.core.compile.models.chart.normalized._base import (
    _BaseChartFields,
    _CartesianChartFields,
    _GeoChartFields,
    _SharedChartFields,
)
from dbt_charts.core.compile.models.chart.resolved import (
    PartitionAxis,
    ResolvedSupportTable,
)
from dbt_charts.core.compile.models.primitives import (
    FormatTable,
    ResolvedFormat,
)
from dbt_charts.core.compile.models.style.authored import (
    LegendStylePatch,
    PaddingStylePatch,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedChartDefaults
from dbt_charts.core.compile.models.style.theme import (
    LegendEdge,
    LegendStyle,
    PaddingStyle,
    PieChartStyle,
    PieLegendStyle,
    SupportTableStyle,
)
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
)
from dbt_charts.core.compile.resolve.chart._channels import _column_numeric_values
from dbt_charts.core.compile.resolve.style.category_colors import (
    categorical_channel_fields,
)
from dbt_charts.core.compile.resolve.style.chart_context import declared_column_format
from dbt_charts.core.compile.resolve.style.inherit_resolver import merge_unset
from dbt_charts.core.compile.resolve.style.legend_position import (
    decide_legend_direction,
    decide_legend_position,
    merge_authored_direction,
    merge_authored_position,
    merge_authored_title_visible,
    merge_authored_visible,
    merge_legend,
    validate_cartesian_position,
)
from dbt_charts.core.compile.resolve.style.palette import substitute_for_alias
from dbt_charts.core.compile.resolve.style.tokens import _resolve_color_tokens
from dbt_charts.core.compile.resolve.style.typography import concrete_title_style
from dbt_charts.core.compile.template.display_text import (
    ChartDisplayText,
    resolve_display_text,
)

__all__ = [
    "AutomaticLinkCandidate",
    "_FAMILY_SLOT",
    "donut_over_pie",
    "_base_kwargs",
    "_cartesian_kwargs",
    "_color_column_format",
    "_geo_kwargs",
    "_resolve_text",
    "_shared_kwargs",
    "default_chart_width",
    "preferred_chart_width",
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

    position_overridden_by_width: LegendEdge | None = None


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
    legend_patch: LegendStyle | PieLegendStyle | None = None,
    palette: list[str] | None = None,
    *,
    requested_alias_palette: str | None,
    automatic_link_candidate: AutomaticLinkCandidate,
    layout_padding: PaddingStyle | PaddingStylePatch,
    suppress_legend: bool = False,
    drop_legend: bool = False,
    top_legend: Literal["compact", "row", "off"] = "off",
    legend_position_overridden_by_width: LegendEdge | None = None,
    radial_legend: bool = False,
    legendless: bool = False,
    wrap_legend: bool,
) -> dict[str, Any]:
    """Common kwargs for all _BaseResolvedChartFields subclasses.

    Projects palette and legend from the baked cascade into the typed
    shared-envelope fields on _BaseResolvedChartFields. The fat
    ChartStyleContext bag is NOT passed through — only palette, channels,
    and the pre-merged legend are projected.

    legend: caller passes merge_unset(chart_style_context.legend, family.legend)
    where family is already the result of merge_unset(chart_style_context.<type>, primary).
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
    suppress_legend: True when direct labels (endpoint labels, pie slice labels)
    replace the color legend. Hides the legend only where ``visible`` is still
    ``auto``; a concrete value from any tier keeps it.
    drop_legend: True when an attached table replaces the legend. Hides it
    whatever ``visible`` says: both would name the same series twice.
    top_legend: force the legend above the plot, reading left to right like the
    marks it names. "compact" wraps at compact_columns for cards too narrow to
    hold a row; "row" leaves the entry count to the renderer.
    radial_legend: True for pie and donut, whose key never overlays
    (``decide_legend_position``'s ``radial``).
    wrap_legend: True when one horizontal row cannot hold the entries; wraps an
    inferred-horizontal legend at compact_columns. An authored direction or
    columns wins.
    legendless: True for families that draw no legend; the
    board's baked placement is left alone and never validated.
    legend_position_overridden_by_width: the author's own edge when the
    tiny-width tier forced this legend back to top over it
    (SeriesNaming.legend_position_overridden_by_width), None otherwise. Folded
    in with _LegendPositionOverridePatch rather than LegendStylePatch like the
    others above -- the field is resolved-only (LegendStyle/LegendStylePatch,
    the authored surface, declare no such field), but it still goes through
    the same merge_onto_base construction as every other legend decision here,
    not a post-hoc patch on the finished ResolvedLegendStyle.
    """
    legend = merge_legend(chart_style_context.legend, legend_patch)
    board_legend = chart_style_context.cascaded.charts.legend
    visible = merge_authored_visible(board_legend, legend_patch)
    title_visible = merge_authored_title_visible(board_legend, legend_patch)
    if legend_position_overridden_by_width is not None:
        overridden = _LegendPositionOverridePatch(
            position_overridden_by_width=legend_position_overridden_by_width
        )
        legend = merge_unset(legend, overridden)
    if top_legend != "off":
        top = LegendStylePatch.model_validate(
            {"columns": legend.compact_columns if top_legend == "compact" else 0}
        )
        legend = merge_unset(legend, top)
    if not legendless:
        authored_position = merge_authored_position(
            chart_style_context.cascaded.charts.legend.position, legend_patch
        )
        # Validated before the top strip rewrites the edge: width never decides.
        if not radial_legend:
            validate_cartesian_position(
                decide_legend_position(authored_position, top_strip=False),
                normalized.id,
            )
        decided = decide_legend_position(
            authored_position, top_strip=top_legend != "off", radial=radial_legend
        )
        # The top strip ignores an authored direction.
        direction = decide_legend_direction(
            "horizontal"
            if top_legend != "off"
            else merge_authored_direction(
                chart_style_context.cascaded.charts.legend, legend_patch
            ),
            decided.edge,
        )
        authored_direction = merge_authored_direction(
            chart_style_context.cascaded.charts.legend, legend_patch
        )
        wrap = (
            wrap_legend
            and top_legend == "off"
            and not decided.overlay
            and decided.edge in ("top", "bottom")
            and authored_direction is None
            and legend.columns == 0
        )
        legend = merge_unset(
            legend,
            LegendStylePatch.model_validate(
                {
                    "position": decided.model_dump(),
                    "direction": direction,
                    **({"columns": legend.compact_columns} if wrap else {}),
                }
            ),
        )
    if drop_legend or (suppress_legend and visible == "auto"):
        visible = False
    if (
        legend.position.edge == "top"
        and legend.direction == "horizontal"
        and title_visible in (None, "auto")
    ):
        title_visible = False
    decided_visibility = LegendStylePatch.model_validate(
        {
            "visible": visible is not False,
            "title": {"visible": title_visible is not False},
        }
    )
    legend = merge_unset(legend, decided_visibility)
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
            single_series_palette=chart_style_context.single_series_palette,
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
        "link_open": normalized.link_open,
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


def _resolve_text(
    value: str,
    display_text: ChartDisplayText,
    formats: FormatTable | None,
    chart_id: str,
    field: str,
) -> str:
    """``"raw"`` keeps authored text; ``None`` allows only text without Jinja."""
    if display_text == "raw":
        return value
    if display_text is None:
        if "{{" in value or "{%" in value:
            raise ValueError(
                f"chart '{chart_id}' {field} contains Jinja but resolve() "
                "got no display_text"
            )
        return value
    return resolve_display_text(
        value, display_text.variables, display_text.query_results, formats
    )


def _color_column_format(
    normalized: _BaseChartFields,
    color_field: str | None,
    formats: FormatTable | None,
    data: list[dict[str, Any]],  # type-state: explicit_any — query-row boundary values
) -> ResolvedFormat | None:
    """How the query says a numeric color column prints, for its legend/tooltip.

    None when the query declares no number format for the column or the column
    paints no numbers, so the emitter keeps the engine default.
    """
    values = _column_numeric_values(data, color_field)
    declared = declared_column_format(normalized, color_field, formats, time=False)
    if declared is None or not values:
        return None
    return column_format("number", formats, column=declared, values=values)


def _shared_kwargs(
    normalized: _SharedChartFields,
    display_text: ChartDisplayText,
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
        "title": _resolve_text(
            normalized.title,
            display_text,
            chart_style_context.formats,
            normalized.id,
            "title",
        ),
        "subtitle": _resolve_text(
            normalized.subtitle,
            display_text,
            chart_style_context.formats,
            normalized.id,
            "subtitle",
        ),
        "background": chart_style_context.background,
        "canvas": chart_style_context.ink_canvas,
        "title_style": concrete_title_style(chart_style_context),
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


def donut_over_pie(chart_style_context: ChartStyleContext) -> PieChartStyle:
    """The board-wide style of a donut: the donut slot over the pie slot."""
    pie = chart_style_context.pie
    donut = chart_style_context.donut
    return pie if donut is None else merge_unset(pie, donut)


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
    if normalized.type == "custom":  # no theme family slot: board-wide width
        return float(chart_style_context.preferred_width)
    family_base = getattr(chart_style_context, _FAMILY_SLOT[normalized.type])
    if isinstance(normalized, PieChart) and normalized.is_donut:
        family_base = donut_over_pie(chart_style_context)
    family = merge_unset(family_base, normalized.style)
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
    if chart_type == "custom":  # no theme family slot: board-wide width
        return float(chart_defaults.preferred_width)
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
    formats: FormatTable | None,
) -> ResolvedSupportTable | None:
    """Bake the final support_table, entry formats included.

    A string y column stays unformatted (its raw label renders rather than a
    formatted "-"), so the numeric-y gate for the default ``number`` stamp is
    decided here from the same rows render sees.
    """
    if support_table is None:
        return None
    # Deferred like the one in _support_table_geometry below: support_table.py imports
    # resolve.style.axis_cascade, so a module-level import here closes a
    # support_table ↔ resolve cycle that only stays quiet while some other module
    # happens to import resolve first.
    from dbt_charts.core.compile.support_table import (  # noqa: PLC0415
        resolve_support_table,
    )

    return resolve_support_table(
        support_table,
        formats,
        data,
        y,
        stamp_number=isinstance(y, str) and _column_is_numeric(data, y),
    )


def _support_table_geometry(
    support_table: ResolvedSupportTable | None,
    chart_style_context: ChartStyleContext,
    chart_type: str,
    x_labeled: bool,
    chart_id: str,
    category_axis_vertical: bool,
    axis_y_orient: Literal["left", "right"],
    stacked: bool,
) -> tuple[SupportTableStyle | None, float | None, bool | None]:
    """Bake the support_table strip's compile-owned geometry: the chart's
    effective SupportTableStyle (with ``position`` resolved to a concrete
    side) and the axis-offset pixel gap the strip reserves below the plot.

    Both are pure functions of the per-chart-cascaded ``chart_style_context``
    plus ``x_labeled`` (the caller's own ``bool(normalized.x_label)``,
    truthy not ``is not None``, resolving the axis cascade's
    ``auto`` title) —
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
        return None, None, None
    from dbt_charts.core.compile.support_table import (  # noqa: PLC0415
        axis_offset,
        bake_neutral_ink,
        reject_ignored_per_series_label,
        resolve_effective_support_table_style,
        resolve_support_table_position,
    )

    reject_ignored_per_series_label(
        support_table, chart_id, category_axis_vertical, stacked
    )

    effective_style = resolve_effective_support_table_style(
        chart_style_context, chart_type
    )
    resolved_position = resolve_support_table_position(
        effective_style.position, category_axis_vertical, axis_y_orient, chart_id
    )
    effective_style, series_ink = bake_neutral_ink(
        effective_style.model_copy(update={"position": resolved_position})
    )
    return (
        effective_style,
        axis_offset(
            chart_style_context,
            effective_style,
            x_labeled=x_labeled,
            chart_type=chart_type,
        ),
        series_ink,
    )


def _cartesian_kwargs(
    normalized: _CartesianChartFields,
    chart_style_context: ChartStyleContext,
    display_text: ChartDisplayText,
    data: list[dict[str, Any]],
    chart_type: str,
    panel_axes: tuple[PartitionAxis, ...],
    stacked: bool,
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
    resolution; ``stacked`` is bar's own resolved stacking, which decides whether
    a ``per_series`` label names a column. Every family but bar has a horizontal category axis
    unconditionally, so they take the defaults (``False``/unused); bar passes
    its own already-resolved ``orientation == "horizontal"`` and baked
    ``axis_y.position``.
    """
    resolved_support_table = _resolved_support_table(
        normalized.support_table, normalized.y, data, chart_style_context.formats
    )
    (
        effective_support_table_style,
        support_table_axis_offset,
        support_table_series_ink,
    ) = _support_table_geometry(
        resolved_support_table,
        chart_style_context,
        chart_type,
        bool(normalized.x_label),
        normalized.id,
        category_axis_vertical,
        axis_y_orient,
        stacked,
    )
    return {
        **_shared_kwargs(normalized, display_text, chart_style_context),
        "x": normalized.x,
        "y": normalized.y,
        "color": normalized.color,
        "x_label": normalized.x_label,
        "y_label": normalized.y_label,
        "field_labels": normalized.field_labels,
        "sort": normalized.sort,
        "multiples": normalized.multiples,
        "panel_axes": panel_axes,
        "support_table": resolved_support_table,
        "effective_support_table_style": effective_support_table_style,
        "support_table_axis_offset": support_table_axis_offset,
        "support_table_series_ink": support_table_series_ink,
        "aspect_ratio": normalized.aspect_ratio,
        "min_height": normalized.min_height,
        "max_height": normalized.max_height,
        "color_format": _color_column_format(
            normalized, normalized.color, chart_style_context.formats, data
        ),
    }


def _geo_kwargs(
    normalized: _GeoChartFields,
    chart_style_context: ChartStyleContext,
    display_text: ChartDisplayText,
) -> dict[str, Any]:
    """Extra kwargs for _GeoResolvedChartFields subclasses (projection only)."""
    return {
        **_shared_kwargs(normalized, display_text, chart_style_context),
        "projection": normalized.projection,
        "field_labels": normalized.field_labels,
    }
