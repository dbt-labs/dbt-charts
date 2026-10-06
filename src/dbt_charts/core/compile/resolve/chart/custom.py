"""Custom chart resolver: bakes the style tree, pins, and color options a template reads."""

from __future__ import annotations

from typing import Any

from pydantic import JsonValue

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import CustomChart
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedCustomChart,
    ResolvedCustomStyle,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve.chart._kwargs import (
    AutomaticLinkCandidate,
    ChartTextVariables,
    _base_kwargs,
    _shared_kwargs,
    _title_font,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.compile.resolve.style.palette import (
    ToneAsPaletteError,
    UnknownColorError,
    palette,
    tone_colors,
)
from dbt_charts.core.compile.resolve.style.tokens import _resolve_color_tokens
from dbt_charts.core.diagnostics.codes_compile import ERR_CUSTOM_FIELDS

__all__ = ["_resolve_custom"]


def _palette_value(name: str) -> JsonValue:
    """A palette role's name → its hex list, or its alias dict for a tone role."""
    try:
        return list(palette(name))
    except ToneAsPaletteError:
        return dict(tone_colors(name))


def _style_tree(context: ChartStyleContext) -> dict[str, JsonValue]:
    tree = context.cascaded.model_dump(mode="json")
    tree["palettes"] = {
        role: _palette_value(name) for role, name in context.palettes.items()
    }
    return tree


def _resolve_token(context: ChartStyleContext, token: str) -> str:
    resolved = _resolve_color_tokens(
        token,
        context.palettes,
        context.roles,
        single_series_palette=context.single_series_palette,
    )
    assert isinstance(resolved, str)
    return resolved


def _resolve_custom(
    normalized: CustomChart,
    _data: list[dict[str, Any]],  # type-state: explicit_any — DB rows
    chart_style_context: ChartStyleContext,
    width: float,
    automatic_link_candidate: AutomaticLinkCandidate,
    variables: ChartTextVariables,
) -> ResolvedCustomChart:
    local_context = build_chart_style_context(chart_style_context, normalized)
    declared = normalized.template_header.options
    color_options = {name for name, spec in declared.items() if spec.type == "color"}
    options: dict[str, JsonValue] = {}
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
        **_shared_kwargs(normalized, variables, local_context),
        chart_type="custom",
        template_name=normalized.template_name,
        template_source=normalized.template_source,
        channels=normalized.channels,
        options=options,
        aspect_ratio=normalized.aspect_ratio,
        min_height=normalized.min_height,
        max_height=normalized.max_height,
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
            title_font=_title_font(normalized, local_context, width),
            tree=_style_tree(chart_style_context),
        ),
    )
