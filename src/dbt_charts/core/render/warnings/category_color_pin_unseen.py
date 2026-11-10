"""Detector: WARN_CATEGORY_COLOR_PIN_UNSEEN — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  any CategoryColorScale bound to a chart on the board carries a non-empty
  `unseen_pins` mapping — a pin `_seatable_pins`
  (compile/resolve/style/category_colors.py) dropped because this render's
  rows never draw the pinned value — and the absence is definite:
    (a) a chart drawing the field has a query that reads no board variable
        (`Query.variable_dependencies` is empty), so no filter can have hidden
        the value; or
    (b) an unseen pin is a near miss of a drawn value, a likely typo.
  A pin absent only from variable-filtered rows is a pin for another
  selection, which is what pins are for.

One diagnostic per FIELD, not per chart and not per value: the identical
bound scale is attached to every chart that draws the field
(`_bound_scales` in compile/resolve/chart/_kwargs.py), so a naive per-chart
walk would fire once per chart for the same dropped pin.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved.callout import ResolvedCalloutChart
from dbt_charts.core.compile.parse.yaml_error_formatter import nearest_names
from dbt_charts.core.diagnostics import WARN_CATEGORY_COLOR_PIN_UNSEEN, Diagnostic
from dbt_charts.core.diagnostics.hints import backticked_list
from dbt_charts.core.render.warnings.base import WarningContext


def _pin_phrase(pin: str, drawn: list[str]) -> str:
    """The unseen pin, with the drawn value it is closest to when one is."""
    close = nearest_names(pin, drawn, limit=1)
    return f"`{pin}` (did you mean `{close[0]}`?)" if close else f"`{pin}`"


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per field carrying a dropped category-color pin."""
    warnings: list[Diagnostic] = []
    seen_fields: set[str] = set()

    for chart in ctx.board_spec.charts.values():
        for scale in chart.category_colors:
            if not scale.unseen_pins or scale.field in seen_fields:
                continue
            drawn = list(scale.slots)
            definite = any(
                c.query is not None and not c.query.variable_dependencies
                for c in ctx.board_spec.charts.values()
                if not isinstance(c, ResolvedCalloutChart)
                and scale in c.category_colors
            )
            if not definite and not any(
                nearest_names(pin, drawn, limit=1) for pin in scale.unseen_pins
            ):
                continue
            seen_fields.add(scale.field)
            values = ", ".join(_pin_phrase(pin, drawn) for pin in scale.unseen_pins)
            warnings.append(
                Diagnostic.from_code(
                    WARN_CATEGORY_COLOR_PIN_UNSEEN,
                    chart=None,
                    path=f"style.charts.category_colors.{scale.field}",
                    field=scale.field,
                    message=WARN_CATEGORY_COLOR_PIN_UNSEEN.message_template.format(
                        field=scale.field, values=values, drawn=backticked_list(drawn)
                    ),
                    fix=WARN_CATEGORY_COLOR_PIN_UNSEEN.fix_template,
                )
            )

    return warnings
