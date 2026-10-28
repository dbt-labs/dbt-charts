"""Detector: WARN_VALUE_LABELS_CROWD_WIDTH — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved import (
    LayeredResolvedChart,
    ResolvedAreaChart,
    ResolvedAreaLayer,
    ResolvedBarChart,
    ResolvedBarLayer,
    ResolvedChart,
    ResolvedLayer,
    ResolvedLineChart,
    ResolvedLineLayer,
    ResolvedScatterLayer,
)
from dbt_charts.core.compile.models.style.resolved import ResolvedMarkLabels
from dbt_charts.core.diagnostics import WARN_VALUE_LABELS_CROWD_WIDTH, Diagnostic
from dbt_charts.core.render.chart.geometry_warnings import EngineParams
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def _painted_labels(
    chart: ResolvedChart, layer: int | None
) -> tuple[str, ResolvedMarkLabels, str | None]:
    """The `marks.<key>` the judged labels were switched on under, their style, and the
    measure column. Area labels ride the area's *line* mark, and a line's labels fall
    back to its point mark when the line's are off. A wide chart's `y` is the
    synthetic fold column, not one the author wrote, so it names no measure."""
    if layer is not None:
        assert isinstance(chart, LayeredResolvedChart), type(chart).__name__
        return _layer_labels(chart.layers[layer])
    match chart:
        case ResolvedBarChart():
            return "bar", chart.style.mark.labels, _measure(chart)
        case ResolvedAreaChart():
            return "line", chart.style.line_mark.labels, _measure(chart)
        case ResolvedLineChart():
            if chart.style.line_mark.labels.visible:
                return "line", chart.style.line_mark.labels, _measure(chart)
            return "point", chart.style.point_mark.labels, _measure(chart)
        case _:
            raise AssertionError(
                f"the engine judges no value labels of its own on a {type(chart).__name__}"
            )


def _measure(
    chart: ResolvedBarChart | ResolvedAreaChart | ResolvedLineChart,
) -> str | None:
    return None if chart.wide_measures else chart.y


def _layer_labels(
    layer: ResolvedLayer,
) -> tuple[str, ResolvedMarkLabels, str | None]:
    match layer:
        case ResolvedBarLayer():
            return "bar", layer.bar_mark.labels, layer.y
        case ResolvedAreaLayer():
            return "line", layer.line_mark.labels, layer.y
        case ResolvedLineLayer():
            if layer.line_mark.labels.visible:
                return "line", layer.line_mark.labels, layer.y
            return "point", layer.point_mark.labels, layer.y
        case ResolvedScatterLayer():
            return "point", layer.point_mark.labels, layer.y


def _overshoot(params: EngineParams) -> float:
    return params["label_width"] - params["slot_width"]


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """One warning per label set (a chart's own, or one layer's); a set the engine raised
    for more than once (facet panels, color groups) keeps its worst overshoot."""
    worst: dict[tuple[str, int | None], EngineParams] = {}
    for chart_id, params in engine_warnings(ctx, WARN_VALUE_LABELS_CROWD_WIDTH):
        key = (chart_id, params["layer"])
        if key not in worst or _overshoot(params) > _overshoot(worst[key]):
            worst[key] = params
    warnings: list[Diagnostic] = []
    for (chart_id, layer), params in worst.items():
        chart = ctx.board_spec.charts[chart_id]
        mark_key, labels, measure = _painted_labels(chart, layer)
        # Chart-local style takes no family segment — `style.bar.…` is the
        # board-level spelling and does not compile on a chart.
        owner = f"charts.{chart_id}" + ("" if layer is None else f".layers.{layer}")
        warnings.append(
            Diagnostic.from_code(
                WARN_VALUE_LABELS_CROWD_WIDTH,
                chart=chart_id,
                path=f"{owner}.style.marks.{mark_key}.labels",
                field=labels.field if labels.field is not None else measure,
                message=WARN_VALUE_LABELS_CROWD_WIDTH.message_template.format(**params),
                fix=WARN_VALUE_LABELS_CROWD_WIDTH.fix_template,
            )
        )
    return warnings
