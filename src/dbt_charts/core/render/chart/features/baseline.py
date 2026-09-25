"""Baseline rule features: zero, top (normalize), unity (ratio-percent).

Rule layers are modeled as real ``ChartSpec(mark="rule", ...)`` overlays — not
sentinel strings.  The translator handles ``"rule"`` as a normal VL mark and folds
it into a ``layer[]`` spec alongside the main encoding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from dbt_charts.core.compile.models.chart.resolved import FormatState, ResolvedChart
from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
)
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.heatmap import ResolvedHeatmapChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.chart.resolved.scatter import ResolvedScatterChart
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.style.resolved import ResolvedAxisStyle
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartRows
from dbt_charts.core.compile.resolve.chart._wide_fields import wide_measure_fields
from dbt_charts.core.render.chart.emitters._cartesian import (
    authored_measure_domain,
    build_zero_rule_if_applicable,
    effective_measure_domain,
    full_rule_at,
    multiples_scale_independent,
    non_bar_zero_rule_should_fire,
)
from dbt_charts.core.render.chart.feature import chart_rows
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.layout_sizing import rows_for_query
from dbt_charts.core.utils import numeric_column_values


def _insert_rule(
    spec: ChartSpec,
    rule: ChartSpec,
    chart: ResolvedChart,
) -> None:
    """Insert ``rule`` into ``spec.layers`` at the V1-matching z-position.

    - bar/other: append last — rule renders above all fills.
    - line: prepend first — rule renders below the strokes, so a line crosses
      the threshold without being interrupted by it.
    - scatter: not a ``spec.layers`` position at all — see the branch below.
    - area: insert after the last ``area`` sub-layer — above fills, below fg stroke.

    A chart with ``chart.layers`` (an authored overlay — bar/line/area/scatter
    on top of the base) is a DIFFERENT shape: ``render_cartesian_overlay``
    wraps the base's own composite sub-layers into ONE outer entry, so
    ``spec.layers`` here is ``[base_composite, overlay1, overlay2, ...]`` —
    not the flat per-mark sub-layer list the per-chart-type branches below
    assume. Scanning that outer list for a top-level ``area`` mark never
    matches (it's nested one level down inside ``base_composite``), so the
    rule must simply append last: on top of the base AND every overlay
    layer, matching the "readability reference line" contract regardless of
    chart type. The area-specific under-the-fg-stroke placement only applies
    to a bare area's own internal composition, with no overlay on top of it.
    """
    if isinstance(chart, ResolvedScatterChart):
        # Beneath the points — see ChartSpec.underlays. Unconditional: a scatter
        # with authored `layers:` wants the reference line under the overlay too.
        spec.underlays.append(rule)
        return
    if (
        isinstance(chart, (ResolvedBarChart, ResolvedLineChart, ResolvedAreaChart))
        and chart.layers
    ):
        spec.layers.append(rule)
        return
    if isinstance(chart, ResolvedLineChart):
        spec.layers.insert(0, rule)
    elif isinstance(chart, ResolvedAreaChart):
        last_area_idx = -1
        for i, layer in enumerate(spec.layers):
            if layer.mark == "area":
                last_area_idx = i
        if last_area_idx >= 0:
            spec.layers.insert(last_area_idx + 1, rule)
        elif len(spec.layers) >= 2:
            # No area layers found (unusual): insert before the last layer (hover).
            spec.layers.insert(len(spec.layers) - 1, rule)
        else:
            spec.layers.append(rule)
    else:
        # bar / layered / other: append last.
        spec.layers.append(rule)


def _is_percent_format(fmt: FormatState) -> bool:
    """True when a format string or FormatConfig spec contains a ``%`` token."""
    if isinstance(fmt, str):
        return "%" in fmt
    if isinstance(fmt, FormatConfig) and fmt.spec is not None:
        return "%" in fmt.spec
    return False


def _measure_field(
    chart: _CartesianResolvedChartFields,
    spec: ChartSpec,
    axis: str,
) -> str | Literal[False]:
    if isinstance(chart.y, str):
        return chart.y
    encoding = spec.encoding.get(axis)
    if isinstance(encoding, dict) and isinstance(encoding.get("field"), str):
        return encoding["field"]
    return False


def _is_log_scale(axis: ResolvedAxisStyle) -> bool:
    """True when *axis*'s continuous scale is log-typed.

    A log domain cannot represent a literal 0, so any rule whose datum is a
    threshold value (0 or 1) must never fire against a log-typed axis —
    checked identically in ``_insert_zero_rule``, ``_insert_top_rules``, and
    ``_apply_x_threshold``. ``_apply_unity`` does not call this guard.
    """
    cont = axis.scale.continuous if axis.scale is not None else None
    return cont is not None and cont.type == "log"


def _resolved_percent_format(
    axis: ResolvedAxisStyle, chart_format: FormatState
) -> bool:
    """True when the resolved axis label format or the chart-level format is
    percent-shaped.

    Both spellings are equivalent authoring surfaces, so every caller must check
    both: consulting ``chart_format`` alone disagrees with an axis-only percent
    format and yields a duplicate rule at datum 1.
    """
    ax_fmt = axis.labels.format
    return bool((ax_fmt and "%" in ax_fmt) or _is_percent_format(chart_format))


def _zero_in_shared_domain(
    chart: ResolvedBarChart
    | ResolvedLineChart
    | ResolvedAreaChart
    | ResolvedScatterChart,
    measure_field: str,
    datasets: dict[str | None, ChartRows],
) -> bool:
    """Whether 0 lies inside the y domain every mark on this scale shares.

    Mirrors ``_shared_y_values``, the resolve-time function that actually
    decides the domain: the union of the base measure (or every wide
    measure) and every layer not pinned to an independent right axis.
    ``apply()`` has already bailed out for an independent dual axis, so
    every layer reaching here shares the base's scale.

    The union is the question, not "does any one series straddle 0". Two
    series that each stay on one side of zero still bracket it once they
    share a scale — an all-negative base under an all-positive overlay
    renders a domain spanning 0, and the rule belongs on it.
    """
    base_rows = chart_rows(chart, datasets).all_rows()
    # Wide charts carry the authored measures in wide_measures; query rows
    # have the real columns, not the synthetic WIDE_VALUE_FIELD. `chart` is
    # never actually a ResolvedBarChart here (see the caller's own
    # isinstance branch), so wide_measure_fields matching bar too is inert.
    wide_fields = wide_measure_fields(chart)
    fields = (
        wide_fields if wide_fields else (measure_field,)
    )  # type-state: silent_fallback — empty tuple is the documented "not wide" return, not a hidden default
    values = [v for field in fields for v in numeric_column_values(base_rows, field)]
    for layer in chart.layers:
        if layer.type == "bar":
            # VL bars extend to/from 0 regardless of their own data range —
            # but only while VL still auto-fits the domain. A scale that
            # resolve has already pinned clips the bars short instead, which
            # is why build_zero_rule_if_applicable overrules this verdict
            # rather than deferring to it.
            return True
        if layer.y is None:
            continue
        values.extend(
            numeric_column_values(
                rows_for_query(layer.query_name, datasets, base_rows), layer.y
            )
        )
    if not values:
        return False
    return min(values) <= 0.0 <= max(values)


def _y_carries_the_measure(chart: ResolvedChart) -> bool:
    """Whether the y channel holds the measure a ``datum`` rule references.

    Line and area always put the measure on y. Scatter has no orientation
    field — x and y are both free-form data columns — so the dot-plot recipe
    rotates a scatter by moving the value onto x and the category onto y. A
    ``datum: 0`` or ``datum: 1`` rule has no position on a categorical axis,
    so it must not fire there.

    Read off the RESOLVED chart, never ``spec.encoding``: a scatter with
    authored ``layers`` has its encoding hoisted into the sub-layers by
    ``render_cartesian_overlay``, leaving ``x`` alone on the outer spec — a
    spec-side read answers False there and silently drops the rule from
    exactly the charts that layer a target line onto a scatter.
    ``ResolvedAxisStyle.is_quantitative`` is baked once at resolve time from
    the same channel classification that already decides the axis type, so
    this reads a fact fixed before the spec exists rather than
    reverse-engineering it from how the emitter composed the layers.
    """
    if not isinstance(chart, ResolvedScatterChart):
        return True
    return chart.style.axis_y.is_quantitative


def _domain_reaches(
    axis: ResolvedAxisStyle,
    data: list[dict[str, Any]],
    field: str,
    value: float,
) -> bool:
    """True when ``value`` lies within the bounds *axis* pins.

    Parameterized by axis (not by chart) so the same gate serves the unity
    rule's measure axis (``axis_y``, or ``axis_x`` for a horizontal bar) and
    the independent quantitative-x zero rule's ``axis_x``.

    Reads ``effective_measure_domain`` — authored domain, else the
    headroom-expanded ``domain_min`` / ``domain_max``, else a zero-anchored
    axis's floor, else the data extent. See that function for why it reads only
    values the emitter really pins and never predicts Vega-Lite's own ``nice``.
    On an x-axis, ``domain_min``/``domain_max`` and the zero-anchor floor are
    never baked (resolve only bakes them for y — see
    ``effective_measure_domain``'s own docstring), so this reduces there to
    "authored domain, else the data extent" — exactly what Vega-Lite auto-fits
    an unpinned x-axis to.

    Reading the raw data range alone made the gate disagree with the render in
    two ways, each leaving a percent chart with a 100% tick painted and no rule
    on it: headroom pulling the domain across ``value`` with no data point
    there, and a zero-anchored axis starting at 0. Both are fixed.

    Deliberately conservative where nothing is pinned: an axis whose tick
    ladder rounds out past ``value`` still reports False, because a rung is a
    tick position and not a domain edge. Reading it once made this gate
    non-monotonic in headroom and let a rule paint outside the domain it then
    stretched to fit itself.

    Not a promise that True means painted, or that False means absent. Under
    ``multiples: {scale: independent}`` resolve bakes neither ladder nor
    bounds, so this reads the whole-dataset extent while each panel auto-fits
    its own narrower domain — a pre-existing disagreement this gate inherits.
    """
    values = numeric_column_values(data, field)
    bounds = effective_measure_domain(
        axis,
        (min(values), max(values)) if values else None,
    )
    if bounds is None:
        return False
    lo, hi = bounds
    return lo <= value <= hi


@dataclass
class BaselineFeature:
    """Zero, top (normalize-stack), and unity (percent-format) baseline rules.

    Bar: zero rule fires regardless of the data.
    Line / area / scatter: zero rule fires when data straddles 0 (or
    scale.zero isn't explicitly False).
    Every family, bar included, is then overruled by a domain pinned away
    from 0 — see build_zero_rule_if_applicable.
    Normalize-stacked bar/area: top rules fire at datum 0 and 1 instead of a
    zero rule — except normalize-stacked, percent-format area, which gets
    only the single unity rule at datum 1 (no duplicate y=1 reference line).
    Line / area / scatter with percent format: unity rule fires at datum 1,
    independent of the zero/top rule above.
    """

    def applies_to(self, chart: ResolvedChart) -> bool:
        """A cartesian chart with a quantitative position axis.

        Heatmap is the one cartesian family with no quantitative axis at all
        — both its channels render as bands regardless of the underlying
        data's type (see ``heatmap.py``).
        """
        return isinstance(chart, _CartesianResolvedChartFields) and not isinstance(
            chart, ResolvedHeatmapChart
        )

    def apply(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        box: RenderBox,
        datasets: dict[str | None, list[dict[str, Any]]],
    ) -> ChartSpec:
        # Independent dual-axis: a `datum: 0` rule added here would get its own
        # degenerate y scale (VL can't bind it to the base measure scale under
        # independent resolve). `render_cartesian_overlay` (emitters/_overlay.py)
        # owns zero-rule insertion for this case instead, nesting each rule
        # inside the specific layer entry that owns the scale it binds to;
        # skip here so this feature never also adds one.
        resolve_scale = spec.resolve.get("scale")
        if resolve_scale is not None and resolve_scale.get("y") == "independent":
            return spec
        # Independent small-multiples scale: FacetFeature (which records
        # `spec.facet_scale`, later realized as the facet-root `resolve.scale`
        # in translate.py) runs after this feature, so `spec.resolve` never
        # carries it here — check the chart's own `multiples.scale` instead.
        # The rule/unity decision below is made once from the pooled union of
        # every panel's rows; under independent scale each panel gets its own
        # y-domain, so a single chart-wide verdict can be wrong for any one
        # panel — skip it, same as the dual-axis case above.
        if isinstance(
            chart, _CartesianResolvedChartFields
        ) and multiples_scale_independent(chart):
            return spec
        self._apply_zero_or_top(spec, chart, datasets)
        data = chart_rows(chart, datasets).all_rows()
        self._apply_unity(spec, chart, data)
        self._apply_x_threshold(spec, chart, data, datum=0, require_percent=False)
        self._apply_x_threshold(spec, chart, data, datum=1, require_percent=True)
        return spec

    def _apply_zero_or_top(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        datasets: dict[str | None, list[dict[str, Any]]],
    ) -> None:
        if isinstance(chart, ResolvedAreaChart) and chart.stack == "center":
            # Streamgraph: y=0 is the visual centerline of the silhouette, not
            # a meaningful baseline — an explicit rule reads as chart noise.
            return
        if (
            isinstance(chart, (ResolvedBarChart, ResolvedAreaChart))
            and chart.stack == "normalize"
        ):
            # rule_axis vs axis_style cascade-slot distinction: see
            # _insert_zero_rule.
            axis_style = chart.style.axis_y
            # Percent format: `_apply_unity` owns the single datum-1 rule, so
            # this only emits the datum-0 baseline (never both — that was the
            # duplicate-unity bug this carve-out exists to dedupe).
            datums = (
                (0,) if _resolved_percent_format(axis_style, chart.format) else (0, 1)
            )
            self._insert_top_rules(spec, chart, datums=datums)
            return
        if not chart_rows(chart, datasets).all_rows():
            return
        self._insert_zero_rule(spec, chart, datasets)

    def _insert_zero_rule(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        datasets: dict[str | None, list[dict[str, Any]]],
    ) -> None:
        assert isinstance(
            chart,
            (
                ResolvedBarChart,
                ResolvedLineChart,
                ResolvedAreaChart,
                ResolvedScatterChart,
            ),
        )
        # rule_axis is the VL CHANNEL the datum paints on — x for a horizontal
        # bar, y otherwise. axis_style is the cascade SLOT the measure's style
        # lives in, which is axis_y regardless of orientation — a horizontal
        # bar's rule paints on x but its measure style still lives here.
        # Conflating the two makes a horizontal bar read its categorical axis
        # for the measure's guards.
        rule_axis = (
            "x"
            if isinstance(chart, ResolvedBarChart) and chart.orientation == "horizontal"
            else "y"
        )
        axis_style = chart.style.axis_y
        authored = authored_measure_domain(axis_style)

        # Determine whether the rule should fire. The log-scale, authored-domain,
        # and grid.visible guards live once in build_zero_rule_if_applicable below
        # — this branch only decides the family-specific straddle/always-fire
        # verdict feeding its `should_fire`.
        if isinstance(chart, ResolvedBarChart):
            # A bar with y_start has no zero baseline unless its axis reaches
            # zero anyway (a waterfall's totals); the rule's datum would
            # otherwise pull zero into every floating bar's domain.
            should_fire = chart.y_start is None or axis_style.zero_anchored
        else:
            # Line / area / scatter: mirror V1 _domain_includes_zero. The
            # rule's datum:0 pulls 0 into the unified domain, so it fires
            # whenever the measure axis isn't explicitly scale.zero=False.
            # Only an explicit scale.zero=False requires a straddle check.
            non_bar_measure_field = _measure_field(chart, spec, "y")
            if non_bar_measure_field is False:
                return
            if not _y_carries_the_measure(chart):
                return
            scale = axis_style.scale
            _bsl_cont3 = scale.continuous if scale is not None else None
            zero_setting = _bsl_cont3.zero if _bsl_cont3 is not None else None
            should_fire = non_bar_zero_rule_should_fire(
                isinstance(chart, ResolvedScatterChart),
                zero_setting,
                zero_anchored=chart.style.axis_y.zero_anchored,
                authored_domain=authored,
                zero_in_domain=lambda: _zero_in_shared_domain(
                    chart, non_bar_measure_field, datasets
                ),
            )

        measure_field = _measure_field(chart, spec, rule_axis)
        if measure_field is False:
            return
        # Read the threshold style from the chart's own baked axis cascade
        # (style.axis_y), not board-level style — a chart-local axis patch has
        # to win.
        threshold_style = chart.style.axis_y.grid.threshold
        # grid.threshold.visible is the targeted off switch: it silences this
        # rule without taking the axis's other gridlines with it, which is all
        # the blanket grid.visible could do before.
        if not threshold_style.visible:
            return
        axis_y_scale = chart.style.axis_y.scale
        continuous = axis_y_scale.continuous if axis_y_scale is not None else None
        rule = build_zero_rule_if_applicable(
            measure_field,
            rule_axis,
            log_scale=continuous is not None and continuous.type == "log",
            authored_domain=authored,
            domain_min=axis_style.domain_min,
            domain_max=axis_style.domain_max,
            grid_visible=chart.style.axis_y.grid.visible,
            zero_color=threshold_style.color,
            zero_width=threshold_style.width,
            should_fire=should_fire,
        )
        if rule is None:
            return
        _insert_rule(spec, rule, chart)

    def _insert_top_rules(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        datums: tuple[int, ...],
    ) -> None:
        # Normalize stacks get the 0% baseline and, unless the caller already
        # routes datum 1 through `_apply_unity` (the percent-format carve-out
        # in `_apply_zero_or_top`), the 100% top reference line too — fully
        # styled (color/width from the baked zero grid style) and drawn on
        # top of the bars — matches V1's two datum rules.
        assert isinstance(chart, (ResolvedBarChart, ResolvedAreaChart))
        rule_axis = (
            "x"
            if isinstance(chart, ResolvedBarChart) and chart.orientation == "horizontal"
            else "y"
        )
        axis_style = chart.style.axis_y
        # Same log-domain incompatibility as _insert_zero_rule's datum:0 guard.
        if _is_log_scale(axis_style):
            return
        # rule_axis vs axis_style cascade-slot distinction: see
        # _insert_zero_rule.
        if not chart.style.axis_y.grid.visible or not axis_style.grid.threshold.visible:
            return
        measure_field = _measure_field(chart, spec, rule_axis)
        if measure_field is False:
            return
        threshold_style = axis_style.grid.threshold
        # Horizontal bars put the measure on x, so the 0%/100% datum rules anchor
        # on x — not the categorical y axis (mirrors the zero rule).
        for datum in datums:
            _insert_rule(
                spec,
                full_rule_at(
                    datum,
                    axis=rule_axis,
                    measure_field=measure_field,
                    color=threshold_style.color,
                    width=threshold_style.width,
                ),
                chart,
            )

    def _apply_unity(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        data: list[dict[str, Any]],
    ) -> None:
        if not isinstance(
            chart,
            (
                ResolvedBarChart,
                ResolvedLineChart,
                ResolvedAreaChart,
                ResolvedScatterChart,
            ),
        ):
            return
        # The axis carrying the measure: x for a horizontal bar, y otherwise.
        # rule_axis vs axis_style cascade-slot distinction: see
        # _insert_zero_rule.
        rule_axis = (
            "x"
            if isinstance(chart, ResolvedBarChart) and chart.orientation == "horizontal"
            else "y"
        )
        axis_style = chart.style.axis_y
        if not _resolved_percent_format(axis_style, chart.format):
            return
        # grid.visible=False suppresses the unity rule (mirrors zero-rule gate).
        # rule_axis vs axis_style cascade-slot distinction: see
        # _insert_zero_rule.
        if not chart.style.axis_y.grid.visible or not axis_style.grid.threshold.visible:
            return
        measure_field = _measure_field(chart, spec, rule_axis)
        if measure_field is False:
            return
        if not _y_carries_the_measure(chart):
            return
        # A normalize-stacked bar/area reroutes its definitional 1.0 ceiling
        # through this rule (see _apply_zero_or_top); its unity rule always
        # fires. Every other percent chart fires only when the rendered
        # domain reaches 1.0 — see _domain_reaches.
        is_normalize_stack = (
            isinstance(chart, (ResolvedBarChart, ResolvedAreaChart))
            and chart.stack == "normalize"
        )
        if not is_normalize_stack and not _domain_reaches(
            axis_style, data, measure_field, 1.0
        ):
            return
        threshold_style = axis_style.grid.threshold
        _insert_rule(
            spec,
            full_rule_at(
                1,
                axis=rule_axis,
                measure_field=measure_field,
                color=threshold_style.color,
                width=threshold_style.width,
            ),
            chart,
        )

    def _apply_x_threshold(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        data: list[dict[str, Any]],  # type-state: explicit_any — query row dict
        datum: int,
        require_percent: bool,
    ) -> None:
        """Independent x=0 / x=1 threshold for a quantitative x axis.

        Distinct from the measure-axis zero/unity rules in
        ``_insert_zero_rule`` / ``_apply_unity``: a chart can have a
        quantitative x AND a quantitative y — an ordinary x-y scatter
        straddling zero (or each reaching 1.0 on its own ratio) on both — and
        each axis earns its own threshold. ``datum=0`` fires unconditionally
        once the other guards pass (mirrors the y-side zero rule); ``datum=1``
        additionally requires ``require_percent``, gated on ``axis_x``'s own
        resolved label format — deliberately NOT ``chart.format``, which is
        the MEASURE's format (it feeds axis_y) and says nothing about an
        unrelated quantitative x. Horizontal bar's x is already the measure
        axis handled above by the y-slot rules (``rule_axis`` is "x" there,
        but the cascade slot they read is still ``axis_y`` — see
        ``_insert_zero_rule``'s comment on that distinction); skip it here to
        avoid a duplicate rule.

        ``data`` is only the base query's own rows (``apply()`` builds it via
        ``chart_rows(chart, datasets).all_rows()``), unlike the y-side zero
        rule, which unions every layer's rows through
        ``_zero_in_shared_domain`` before checking the domain. Nothing here
        unions an overlay layer's own x values in. The asymmetry is
        one-directional: this rule can go conservatively missing when only a
        layer's data would justify it, never misplaced.
        """
        if not isinstance(
            chart,
            (
                ResolvedBarChart,
                ResolvedLineChart,
                ResolvedAreaChart,
                ResolvedScatterChart,
            ),
        ):
            return
        if isinstance(chart, ResolvedBarChart) and chart.orientation == "horizontal":
            return
        # Read off ResolvedAxisStyle.is_quantitative on the RESOLVED chart, never
        # spec.encoding: a chart with authored layers has its encoding hoisted
        # into the sub-layers, leaving the outer spec's x channel unreliable.
        # is_quantitative is baked once at resolve time from the same channel
        # classification that already decides the axis type, so this reads a
        # fact fixed before the spec exists rather than reverse-engineering it
        # from how the emitter composed the layers.
        if not chart.style.axis_x.is_quantitative:
            return
        if not data:
            return
        axis_x = chart.style.axis_x
        if _is_log_scale(axis_x):
            return
        if require_percent and not _resolved_percent_format(axis_x, None):
            return
        # Both switches, and the blanket one is deliberate: a theme that hides
        # axis_x's gridlines entirely (bar and histogram do, by convention)
        # leaves the plot with no vertical structure at all, so the rule stays
        # suppressed there too — a lone heavy rule in that emptiness would read
        # as a stray mark rather than a threshold, and matching the rest of
        # the axis wins over drawing it anyway.
        if not axis_x.grid.visible or not axis_x.grid.threshold.visible:
            return
        x_field = chart.x
        if x_field is None:
            return
        if not _domain_reaches(axis_x, data, x_field, float(datum)):
            return
        threshold_style = axis_x.grid.threshold
        _insert_rule(
            spec,
            full_rule_at(
                datum,
                axis="x",
                measure_field=x_field,
                color=threshold_style.color,
                width=threshold_style.width,
            ),
            chart,
        )
