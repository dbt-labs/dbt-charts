"""Genuine-zero bar label: emits a direct "0" text mark on the baseline for
a row whose measure is exactly ``0``.

A bar mark at value 0 already renders as invisible — a real path with zero
extent, stroked in a self-referencing knockout of the theme background — so
a genuine zero reads as missing data. This feature does not touch the bar
mark itself (suppressing it buys nothing visually; it is already invisible,
see the task's own measured evidence) — it only adds the "0" annotation.
Vega-Lite already drops a NULL row from the mark entirely (no mark, no
label) — that "gap" reading is correct and this feature leaves it alone;
only an exact ``0`` triggers a label.

Bar only: line/area/scatter vertices at value 0 already render visibly (a
point or a line segment touching the baseline is not ambiguous with "no
data" the way a 0-height rect is), so the mark-vs-missing confusion this
feature resolves doesn't exist for those families.

Implementation note: the label layer is built entirely from VL *encoding*
conditions (``text``), never a ``transform``. A ``transform`` array on any
VL layer — even one that changes nothing about which rows other layers
draw — makes vl-convert silently discard the shared categorical axis's
``sort`` and fall back to alphabetical order (documented in
``value_labels.py``'s own transform-hoisting comment, and reproduced against
an earlier revision of this feature that used per-layer ``filter``
transforms: any sorted bar chart lost its sort the moment one row was zero).
The label layer therefore carries the SAME full, untouched row set as the
main mark — no data split, no transform, nothing for VL to re-infer a
domain or sort from — and blanks itself for every row except the zero one
via a plain ``condition`` on the ``text`` channel.
"""

from __future__ import annotations

from dataclasses import dataclass

from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.feature import chart_rows
from dbt_charts.core.render.chart.features.value_labels import (
    BAR_POS_MAP_HORIZONTAL,
    BAR_POS_MAP_VERTICAL,
    apply_label_font,
    label_size_encoding,
)
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.format_utils import format_value
from dbt_charts.core.utils import coerce_numeric_cell


def _zero_label_scope(chart: ResolvedChart) -> bool:
    """True for the plain single-series bar shape this feature owns.

    Ownership is exclusive by construction, not by agreement: this feature
    only ever fires where ``ValueLabelFeature`` would not otherwise label the
    row (``labels.visible is not True`` — ``ValueLabelFeature``'s own bar
    path is gated on the exact same field, see
    ``_append_bar_segment_label_layer``). Neither feature needs to know
    whether the other ran, so disabling either one in ``DEFAULT_FEATURES``
    degrades gracefully instead of silently dropping a label.

    No color channel, no stacking, no multi-metric ``y: [...]``, no typed
    overlay layers:
    - Grouped/stacked zero segments need a position decision (mid-stack?
      mid-group?) this feature doesn't make.
    - A typed overlay layer may reference an unrelated field/query that a
      blind ``y``-field filter isn't safe to apply to.
    """
    if not isinstance(chart, ResolvedBarChart):
        return False
    if not isinstance(chart.y, str) or chart.x is None:
        return False
    if chart.stack not in (None, "none"):
        return False
    if chart.layers:
        return False
    if chart.style.mark.labels.visible is True:
        return False
    return chart.resolved_channels.get("color") is None


@dataclass
class ZeroValueLabelFeature:
    """Appends a direct "0" label for a genuine-zero bar row.

    Fires only for the plain single-series bar shape, and only where
    ``ValueLabelFeature`` isn't already labeling every row (see
    ``_zero_label_scope``). Grouped/stacked zero segments need a position
    decision (mid-stack? mid-group?) this feature doesn't make.
    """

    def applies_to(self, chart: ResolvedChart) -> bool:
        return _zero_label_scope(chart)

    def apply(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        box: RenderBox,
        datasets: dict[str | None, list[VLDict]],
    ) -> ChartSpec:
        assert isinstance(chart, ResolvedBarChart)
        y = chart.y
        assert isinstance(y, str)  # guaranteed by applies_to

        data = (
            spec.data
            if spec.data is not None
            else chart_rows(chart, datasets).all_rows()
        )
        if not any(coerce_numeric_cell(row.get(y)) == 0 for row in data):
            return spec

        is_horiz = chart.orientation == "horizontal"
        # "above" unconditionally: labels.position (top/middle/bottom) places
        # text relative to a bar's own extent, which doesn't exist for the
        # (invisible) zero row — there's nothing to sit inside, on top of, or
        # centered in.
        pos_map = BAR_POS_MAP_HORIZONTAL if is_horiz else BAR_POS_MAP_VERTICAL
        mark_props: VLDict = {k: v for k, v in pos_map["above"].items() if k != "type"}

        # Chrome comes from the bar's own value-label slot (marks.bar.labels)
        # — the same theme surface and helpers ValueLabelFeature uses, so the
        # "0" label reads as the same family of annotation, restylable via
        # style.marks.bar.labels.font, and never drifts from a hand-copy.
        labels = chart.style.mark.labels
        apply_label_font(mark_props, labels)

        text_enc: VLDict = {
            "condition": {"test": f"datum['{y}'] != 0", "value": ""},
            "field": y,
            "type": "quantitative",
        }
        if labels.format is not None:
            fmt = labels.format
            if fmt.prefix or fmt.suffix:
                # Only the value 0 paints, so the affix is composed once in
                # Python; a per-row transform would break a sorted domain.
                text_enc.pop("field")
                text_enc.pop("type")
                text_enc["value"] = format_value(0, labels.format)
            else:
                text_enc["format"] = fmt.spec
        layer_enc: VLDict = {"text": text_enc}
        size = label_size_encoding(labels)
        if size is not None:
            layer_enc["size"] = size

        spec.layers.append(
            ChartSpec(
                mark="text",
                mark_props=mark_props,
                encoding=layer_enc,
                # This "0" IS the row's own printed value -- _zero_label_scope
                # guarantees ValueLabelFeature's own bar path never fires on
                # the same row set, so the two are mutually exclusive, never
                # a double-label. Without the marker, hovering would recede
                # the (invisible) zero-height bar while its own "0" stayed
                # lit, the opposite of every other row.
                value_label=True,
            )
        )
        return spec
