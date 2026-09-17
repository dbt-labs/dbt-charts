"""FacetFeature — record small-multiples intent from ``chart.multiples``.

Records the facet row/column fields and scale on the ``ChartSpec``; the actual
structural wrap into a Vega-Lite ``facet`` operator happens in ``translate_to_vl``
(mirroring how ``EndpointLabelFeature`` records composition intent that
``translate.py`` later realizes as hconcat/vconcat).

The both-edge y-axis for a wide grid is NOT decided here — it is already baked
into the resolved ``axis_y.mirror`` flag at resolve time, so ``MirrorAxisFeature``
(which runs before this) has already appended the ghost overlay to the unit spec.
"""

from __future__ import annotations

from typing import Any, Literal

from dbt_charts.core.compile.models.chart.resolved import (
    LayeredResolvedChart,
    ResolvedChart,
)
from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
)
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_MULTIPLES_ENDPOINT_LABELS,
    ERR_MULTIPLES_LAYER_PARTITION,
    ERR_MULTIPLES_SUPPORT_TABLE,
)
from dbt_charts.core.render.chart.emitters._cartesian import (
    facet_bound_position_channels,
)
from dbt_charts.core.render.chart.feature import chart_rows
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox

# VL types that back a discrete (band/point-per-value) scale. Narrowing a
# channel's scale to one panel's single value is only correct for these —
# for "quantitative"/"temporal" the domain IS the information the chart
# carries (a point's position within the shared range), so narrowing it to a
# degenerate single-value domain would silently strip that meaning rather
# than trim unused axis slots. See `_discrete_facet_channels`.
_DISCRETE_VL_TYPES = frozenset({"nominal", "ordinal"})


def _discrete_facet_channels(
    spec: ChartSpec, candidates: frozenset[Literal["x", "y"]]
) -> frozenset[Literal["x", "y"]]:
    """Filter `candidates` down to channels the emitter already baked as a
    discrete VL type on `spec.encoding`.

    Reads the type the emitter already decided (`build_x_enc` et al.), rather
    than re-deriving it from data — `FacetFeature` runs last in the pipeline
    (`DEFAULT_FEATURES`), after every emitter has finished, so `spec.encoding`
    already carries the real, data-classified type. Not
    `channel != spec.measure_channel`: a heatmap's `measure_channel` is "y"
    (the vocabulary's stand-in for "the family's one quantitative axis", which
    for heatmap doesn't exist positionally), and "y" is precisely the nominal
    band this fix exists to narrow — measure_channel answers a different
    question than "is this scale discrete".

    A channel absent from `spec.encoding` (e.g. `y` on a layered chart, where
    the shared y encoding moves onto each layer) has no type to confirm — not
    included, the same conservative default as every other guard here.
    """
    channels: set[Literal["x", "y"]] = set()
    for channel in candidates:
        # A channel absent from spec.encoding (e.g. y on a layered chart) has
        # no baked type to confirm; empty-dict read is the documented
        # conservative default (excluded below), not a masked bug.
        encoding = spec.encoding.get(
            channel, {}
        )  # type-state: silent_fallback — see above
        if encoding.get("type") in _DISCRETE_VL_TYPES:
            channels.add(channel)
    return frozenset(channels)


class FacetFeature:
    """``chart.multiples`` → facet row/column/scale recorded on the spec."""

    def applies_to(self, chart: ResolvedChart) -> bool:
        return (
            isinstance(chart, _CartesianResolvedChartFields)
            and chart.multiples is not None
        )

    def apply(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        box: RenderBox,
        datasets: dict[str | None, list[dict[str, Any]]],
    ) -> ChartSpec:
        assert isinstance(chart, _CartesianResolvedChartFields)  # applies_to guard
        # Endpoint labels compose the chart into an hconcat/vconcat pane; faceting
        # a concat is nonsensical. Refuse rather than emit a broken spec. A
        # multi-column set usually raises this same code earlier, from
        # MirrorAxisFeature, because it auto-mirrors the y-axis — but only when
        # the auto-mirror actually applies (shared scale, unset axis_y.mirror,
        # quantitative y). A multi-column set missing any of those lands here.
        if spec.endpoint_label_layout is not None:
            raise ChartDataError.from_code(
                ERR_MULTIPLES_ENDPOINT_LABELS, chart_id=chart.id
            )
        if chart.support_table is not None:
            raise ChartDataError.from_code(
                ERR_MULTIPLES_SUPPORT_TABLE, chart_id=chart.id
            )
        multiples = chart.multiples
        assert multiples is not None  # applies_to guarantees this
        # The "resolved without data, rendered with real rows" check lives in
        # BoardRenderSession.emit_chart, before the emitter runs — an emitter
        # that regroups by panel_axes (gap-fill, per-panel validation) would
        # otherwise see axes=() as the ordinary N=1 case and raise a
        # misleading duplicate-rows error before this feature ever runs.
        # An own-query overlay layer whose query returns the partition
        # column(s) means the author wants per-panel layer data, which
        # Vega-Lite cannot express: the facet operator only ever partitions
        # the root dataset, never a layer's own inline dataset.
        # Keyed on column presence only — row content/cardinality never
        # matters, and an empty layer dataset has no key to test, so it is
        # allowed (nothing to repeat wrongly in every panel).
        if isinstance(chart, LayeredResolvedChart):
            partition_fields = [
                field
                for field in (multiples.rows, multiples.columns)
                if field is not None
            ]
            for layer in chart.layers:
                if layer.query_name is None or layer.query_name == chart.query_name:
                    continue
                # Unlike chart_rows() in feature.py, a missing key here is
                # NOT necessarily a caller bug: two real, non-production
                # callers pass an incomplete datasets map on purpose — the
                # board-level warning-detection pass (renderer.py, which
                # only threads the base chart's own rows) and
                # generate_vega_lite_spec() (a standalone dev/test entry
                # point with no per-query datasets concept at all). Neither
                # can supply this layer's own rows, so there is nothing to
                # validate for it here — the same tolerant lookup
                # render_cartesian_overlay uses (_overlay.py). A
                # present-but-empty layer dataset is the same "nothing to
                # repeat wrongly in any panel" story, so both share one
                # early-continue.
                layer_rows = datasets.get(layer.query_name)
                if not layer_rows:
                    continue
                layer_columns = set(layer_rows[0].keys())
                offending = [
                    field for field in partition_fields if field in layer_columns
                ]
                if offending:
                    raise ChartDataError.from_code(
                        ERR_MULTIPLES_LAYER_PARTITION,
                        chart_id=chart.id,
                        fields=offending,
                    )
        spec.facet_row = multiples.rows
        spec.facet_column = multiples.columns
        spec.facet_scale = multiples.scale
        dataset = chart_rows(chart, datasets)
        # ChartDataset.column_values() reads a partition field's order off its
        # own baked PartitionAxis (query first-encounter order), not off panel
        # traversal; see its docstring. Known gap: a chart whose x IS the
        # facet field can have its x column rewritten in place by the emitter
        # (e.g. year-shaped-to-temporal normalization) after this dataset was
        # built from the raw pre-emit rows. The sort array then names
        # pre-rewrite values Vega-Lite can't match in data.values, and that
        # one shape falls back to alphabetical order.
        if multiples.rows is not None:
            spec.facet_row_order = dataset.column_values(multiples.rows)
        if multiples.columns is not None:
            spec.facet_column_order = dataset.column_values(multiples.columns)
        # A horizontal bar flips its axes — the measure rides VL x, the category
        # y — so an independent facet scale has to free x there. Decided here
        # rather than in the emitter because an authored `layers:` overlay
        # returns a fresh ChartSpec, dropping anything the emitter stamped on
        # the one it was handed.
        spec.measure_channel = (
            "x"
            if isinstance(chart, ResolvedBarChart) and chart.orientation == "horizontal"
            else "y"
        )
        # box.facet_unnarrowed_panel_width is the same baseline
        # `_render_vl_artifact` (vega_lite.py) already checked the extra
        # axis's cost against, so this reaches the identical narrow/don't-
        # narrow verdict. This method only ever runs on a faceted chart
        # (applies_to's own guard), so real facet geometry always exists in
        # production — box is always _render_vl_artifact's own, carrying a
        # real value here, never None. A test caller that builds its own
        # box must supply a real one too (or accept that None deliberately
        # skips the affordability check, per facet_bound_position_channels's
        # own documented None semantics) — not lean on a stand-in value
        # this method invents on its behalf.
        spec.facet_independent_channels = _discrete_facet_channels(
            spec,
            facet_bound_position_channels(
                chart,
                multiples,
                dataset.all_rows(),
                box.facet_unnarrowed_panel_width,
            ),
        )
        return spec
