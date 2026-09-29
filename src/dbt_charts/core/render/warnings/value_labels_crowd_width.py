"""Detector: WARN_VALUE_LABELS_CROWD_WIDTH — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The check is general, not bar-specific: for N labels spread across a categorical
x-axis, each gets a slot of ``plot_width / N``. If the widest rendered label is
wider than its slot, labels overflow their mark and collide with their
neighbors. Same computation for bar, line, and area value labels.

Both sides are exact: the slot uses the panel's real rendered width from the
Vega-Lite spec (``vega_specs[chart_id].width``, unwrapping the small-multiples
unit), and the label width is measured from font metrics. No estimation — the
check fires exactly when the widest label is wider than its slot.

Scope: bar (vertical), line, area — cartesian families with value labels over a
categorical x. Horizontal bars run the label along the bar's length (a different
axis) and are out of scope; x-axis labels are handled by the engine's own
skip-then-tilt strategy, including calendar-aware temporal thinning, not here.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.style.theme import MarkLabelsStyle
from dbt_charts.core.diagnostics import WARN_VALUE_LABELS_CROWD_WIDTH, Diagnostic
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.render.chart.emitters._cartesian import widest_panel_distinct_count
from dbt_charts.core.render.chart.features.value_labels import (
    painted_label_text,
    painted_span_label_text,
)
from dbt_charts.core.render.warnings.base import (
    WarningContext,
    encoding_channel_type,
    facet_channel_is_independent,
)

_CATEGORICAL_TYPES = frozenset({"nominal", "ordinal"})


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one warning per chart whose widest value label overflows its slot."""
    warnings: list[Diagnostic] = []
    text_font = ctx.board_spec.style.chart_defaults.marks.text.font

    for chart_id, chart in ctx.board_spec.charts.items():
        # Family + value-label slot. Horizontal bars run the label along the bar
        # length (a different axis) and are out of scope; area labels ride the
        # overlaid line mark.
        labels: MarkLabelsStyle
        # `mark_key` is the authored `marks.<key>` the labels came from, so the
        # warning marks where they were switched on. Area labels ride the
        # overlaid *line* mark, which is why it is not always the family name.
        mark_key: str
        # The paint path's own register flag (`_label_format_fallback`,
        # compile/resolve/chart/_marks.py).
        is_house: bool
        # Same gate as `_build_bar_span_text_layer` (features/value_labels.py).
        span_start_field: str | None = None
        if isinstance(chart, ResolvedBarChart):
            if chart.orientation == "horizontal":
                continue
            labels = chart.style.mark.labels
            mark_key = "bar"
            is_house = chart.style.label_is_house
            if labels.field is None:
                span_start_field = chart.y_start
        elif isinstance(chart, ResolvedLineChart):
            labels = chart.style.line_mark.labels
            mark_key = "line"
            is_house = chart.style.line_label_is_house
        elif isinstance(chart, ResolvedAreaChart):
            labels = chart.style.line_mark.labels
            mark_key = "line"
            is_house = chart.style.label_is_house
        else:
            continue

        # Visible-first: value labels ship default-off, so this cuts almost every
        # chart before any measurement.
        if not labels.visible:
            continue
        if chart.x is None or not isinstance(chart.y, str):
            continue
        if chart_id not in ctx.chart_results or chart_id not in ctx.vega_specs:
            continue

        # Small multiples wrap the per-panel unit spec (with its own width) under
        # "spec"; the facet root carries only facet/config/data. Read the panel the
        # labels actually render into.
        spec = ctx.vega_specs[chart_id]
        unit = spec["spec"] if "facet" in spec else spec
        if encoding_channel_type(unit, "x") not in _CATEGORICAL_TYPES:
            continue
        render_width = unit.get("width")
        if not isinstance(render_width, int | float) or render_width <= 0:
            continue

        rows = ctx.chart_results[chart_id]
        x_field = chart.x
        label_field = labels.field if labels.field is not None else chart.y
        whole_dataset_distinct = len({row[x_field] for row in rows if x_field in row})
        # facet_bound_position_channels (emitters/_cartesian.py) can resolve
        # x independently for a panel holding any proper subset of the x
        # domain, not only the single-value case a name-matched facet field
        # used to guarantee by construction — read the WIDEST panel's own
        # count, not the whole-dataset union `rows` would give (and not a
        # flat 1, which only ever held for that one degenerate shape).
        if facet_channel_is_independent(spec, "x"):
            distinct = (
                widest_panel_distinct_count(x_field, chart.panel_axes, rows)
                or whole_dataset_distinct
            )
        else:
            distinct = whole_dataset_distinct
        if distinct == 0:
            continue

        # Font the value label renders in: its own, else the theme text mark's.
        lbl_font = labels.font
        family = (
            lbl_font.family
            if lbl_font is not None and lbl_font.family is not None
            else text_font.family
        )
        size = (
            lbl_font.size
            if lbl_font is not None and lbl_font.size is not None
            else text_font.size
        )
        if size is None:
            continue
        slot = render_width / distinct
        measurer = get_font_measurer(family)

        widest = 0.0
        for row in rows:
            value = row.get(label_field)
            if not isinstance(value, (int, float)):
                continue
            if span_start_field is not None:
                start_value = row.get(span_start_field)
                if not isinstance(start_value, (int, float)):
                    continue
                text = painted_span_label_text(
                    value, start_value, labels.format, is_house
                )
            else:
                text = painted_label_text(value, labels.format, is_house)
            width = measurer.measure(text, size)
            widest = max(widest, width)
        if widest <= slot:
            continue

        warnings.append(
            Diagnostic.from_code(
                WARN_VALUE_LABELS_CROWD_WIDTH,
                chart=chart_id,
                # Chart-local style takes no family segment — `style.bar.…` is
                # the board-level spelling and does not compile on a chart.
                path=f"charts.{chart_id}.style.marks.{mark_key}.labels",
                field=label_field,
                message=WARN_VALUE_LABELS_CROWD_WIDTH.message_template.format(
                    chart_id=chart_id, label_width=widest, slot_width=slot
                ),
                fix=WARN_VALUE_LABELS_CROWD_WIDTH.fix_template,
            )
        )

    return warnings
