"""Custom chart resolver: bakes the style tree, pins, and color options a template reads."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, JsonValue

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import column_format
from dbt_charts.core.compile.models.chart.normalized import CustomChart
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedCustomChart,
    ResolvedCustomStyle,
)
from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.compile.models.style.authored import (
    LegendStylePatch,
    PieLegendStylePatch,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.theme import (
    LegendPositionStyle,
    LegendStyle,
    PieLegendStyle,
)
from dbt_charts.core.compile.resolve.chart._kwargs import (
    AutomaticLinkCandidate,
    _base_kwargs,
    _shared_kwargs,
)
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AUTO_AXIS_FORMAT,
    target_tick_count,
)
from dbt_charts.core.compile.resolve.style.board import concrete_family_defaults
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.compile.resolve.style.legend_position import (
    decide_legend_position,
    merge_authored_position,
)
from dbt_charts.core.compile.resolve.style.palette import (
    ToneAsPaletteError,
    UnknownColorError,
    palette,
    tone_colors,
)
from dbt_charts.core.compile.resolve.style.tokens import _resolve_color_tokens
from dbt_charts.core.compile.resolve.style.typography import (
    concrete_title_style,
    resolve_title_font,
)
from dbt_charts.core.compile.template.custom_template import OptionValue
from dbt_charts.core.compile.template.display_text import ChartDisplayText
from dbt_charts.core.diagnostics.codes_compile import ERR_CUSTOM_FIELDS

__all__ = ["_resolve_custom"]


def _palette_value(name: str) -> JsonValue:
    """A palette role's name → its hex list, or its alias dict for a tone role."""
    try:
        return list(palette(name))
    except ToneAsPaletteError:
        return dict(tone_colors(name))


_ENGINE_DECIDED_PLACEMENT = ("edge", "align", "overlay")


def _concrete(node: JsonValue, path: tuple[str, ...] = ()) -> JsonValue:
    """*node* with each remaining engine-decided leaf resolved the way the
    resolvers do: ``auto`` ticks to the engine target, ``auto`` legend values to
    the decided placement, and an axis title that shows when labeled to hidden.
    Whole-number font weights are ints: SVG ignores ``font-weight="600.0"``."""
    if isinstance(node, list):
        return [_concrete(v, path) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: _concrete(v, (*path, k)) for k, v in node.items()}
    for key in ("weight", "compact_weight"):
        weight = out.get(key)
        if isinstance(weight, float) and weight.is_integer():
            out[key] = int(weight)
    if path[-1:] == ("ticks",) and out.get("count") == "auto":
        out["count"] = target_tick_count("auto")
    if path[-1:] == ("title",) and out.get("visible") == "auto":
        out["visible"] = False
    if path[-1:] == ("labels",) and out.get("format") == AUTO_AXIS_FORMAT:
        out["format"] = None
    if path[-1:] == ("legend",) and out.get("visible") == "auto":
        out["visible"] = True
    if path[-2:] == ("legend", "title") and out.get("visible") == "auto":
        out["visible"] = None
    return out


def _decided_position(
    board: LegendPositionStyle,
    family_legend: LegendStyle | PieLegendStyle | None,
    *,
    radial: bool = False,
) -> dict[str, JsonValue]:
    """The placement a chart of this family with no chart-local style gets,
    decided by the chart resolver's own functions."""
    decided = decide_legend_position(
        merge_authored_position(board, family_legend), top_strip=False, radial=radial
    )
    return {k: getattr(decided, k) for k in _ENGINE_DECIDED_PLACEMENT}


def _style_tree(context: ChartStyleContext) -> dict[str, JsonValue]:
    cascaded = context.cascaded
    defaults = concrete_family_defaults(context)
    charts = cascaded.charts.model_copy(
        update={
            "title": concrete_title_style(context),
            "marks": defaults.marks,
            "pie": defaults.pie,
            "kpi": defaults.kpi,
            "support_table": defaults.support_table,
            "bar": defaults.bar,
            "line": defaults.line,
            "area": defaults.area,
            "heatmap": defaults.heatmap,
            "histogram": defaults.histogram,
            "scatter": defaults.scatter,
        }
    )
    dumped = cascaded.model_copy(update={"charts": charts}).model_dump(mode="json")
    concrete = _concrete(dumped)
    assert isinstance(concrete, dict)
    tree_charts = concrete["charts"]
    assert isinstance(tree_charts, dict)
    general = cascaded.charts.legend
    general_node = tree_charts["legend"]
    assert isinstance(general_node, dict)
    general_node["position"] = _decided_position(general.position, None)
    for name, family in charts:
        legend = dict(family).get("legend") if isinstance(family, BaseModel) else None
        node = tree_charts[name]
        legend_node = node.get("legend") if isinstance(node, dict) else None
        if isinstance(legend, (LegendStylePatch, PieLegendStylePatch)) and isinstance(
            legend_node, dict
        ):
            legend_node["position"] = _decided_position(
                general.position, legend, radial=name == "pie"
            )
    concrete["palettes"] = {
        role: _palette_value(name) for role, name in context.palettes.items()
    }
    return concrete


def _resolve_token(context: ChartStyleContext, token: str) -> str:
    resolved = _resolve_color_tokens(
        token,
        context.palettes,
        context.roles,
        single_series_palette=context.single_series_palette,
    )
    assert isinstance(resolved, str)
    return resolved


def _column_formats(
    normalized: CustomChart,
    data: list[dict[str, Any]],  # type-state: explicit_any — DB rows
    context: ChartStyleContext,
) -> dict[str, ResolvedFormat]:
    """The format each returned column prints with on a built-in chart.

    The columns are the first row's, the set the renderer checks channels
    against."""
    return {
        column: column_format(
            "number",
            context.formats,
            query_formats=normalized.query.formats if normalized.query else None,
            columns=column,
        )
        for column in (data[0] if data else ())
    }


def _resolve_custom(
    normalized: CustomChart,
    data: list[dict[str, Any]],  # type-state: explicit_any — DB rows
    chart_style_context: ChartStyleContext,
    width: float,
    automatic_link_candidate: AutomaticLinkCandidate,
    display_text: ChartDisplayText,
) -> ResolvedCustomChart:
    local_context = build_chart_style_context(chart_style_context, normalized)
    declared = normalized.template_header.options
    color_options = {name for name, spec in declared.items() if spec.type == "color"}
    options: dict[str, OptionValue] = {}
    for name, value in normalized.options.items():
        if name in color_options and isinstance(value, str):
            try:
                value = _resolve_token(chart_style_context, value)
            except UnknownColorError as exc:
                raise CompilationError.from_code(
                    ERR_CUSTOM_FIELDS,
                    chart_id=normalized.id,
                    reason=f"option {name!r} value {value!r}: {exc}",
                ) from exc
        options[name] = value
    return ResolvedCustomChart(
        **_base_kwargs(
            normalized,
            chart_style_context,
            {},
            requested_alias_palette=chart_style_context.requested_alias_palette,
            automatic_link_candidate=automatic_link_candidate,
            layout_padding=chart_style_context.padding,
            wrap_legend=False,  # a custom chart draws no color legend
        ),
        **_shared_kwargs(normalized, display_text, local_context),
        chart_type="custom",
        template_name=normalized.template_name,
        template_source=normalized.template_source,
        channels=normalized.channels,
        options=options,
        aspect_ratio=normalized.aspect_ratio,
        min_height=normalized.min_height,
        max_height=normalized.max_height,
        column_formats=_column_formats(normalized, data, chart_style_context),
        column_options=tuple(
            name
            for name, spec in declared.items()
            if spec.type in ("column", "columns")
        ),
        # Pins are already hex: the board cascade resolved their tokens.
        category_pins={
            column: dict(binding.values)
            for column, binding in chart_style_context.category_color_pins.items()
        },
        style=ResolvedCustomStyle(
            title_font=resolve_title_font(local_context, width),
            tree=_style_tree(chart_style_context),
        ),
    )
