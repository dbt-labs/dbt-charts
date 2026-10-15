"""BoardRenderSession — board-level coordinator for a render-v2 pass.

Holds the shared board style and drives the per-chart emit → features
pipeline for a single board/board render.  Call ``emit_chart`` per chart,
then ``finalize_vl`` to produce Vega-Lite JSON — that path is VL-only.

Non-VL families (``kpi``, ``table``, ``spark_bar``, ``callout``, ``custom``,
``pie``) are hand-drawn SVG, not Vega-Lite: ``render_svg_family`` dispatches them
straight to the Rust engine and returns the SVG string, bypassing
``emit_chart``/``finalize_vl`` entirely. It returns ``None`` for VL families
so the caller falls through to the ``emit_chart``/``finalize_vl`` path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbt_charts.core.compile.models.board.normalized import (
    NO_TITLE_SHIFT,
    TitleShift,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedCalloutChart,
    ResolvedChart,
    ResolvedCustomChart,
    ResolvedKpiChart,
    ResolvedPieChart,
    ResolvedSparkBarChart,
    ResolvedTableChart,
)
from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
    _SharedResolvedChartFields,
)
from dbt_charts.core.compile.models.chart.resolved._layer import LayeredResolvedChart
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_MULTIPLES_RESOLVED_WITHOUT_DATA
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.emitters import get_emitter
from dbt_charts.core.render.chart.feature import FeaturePipeline, chart_rows
from dbt_charts.core.render.chart.features import DEFAULT_FEATURES
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.chart.translate import assemble_final_vl
from dbt_charts.core.render.engine import render_native
from dbt_charts.core.render.sizing import custom_chart_height
from dbt_charts.core.render.utils import normalize_data_types


@dataclass
class BoardRenderSession:
    """Per-board context for a render-v2 pass.

    Args:
        board_style: Fully resolved board-level style shared across all charts.
            ``board_style.vega_config`` carries the pre-baked theme VL config.
        features: Feature pipeline applied in list order after each emit.
    """

    board_style: ResolvedStyle
    features: FeaturePipeline

    @classmethod
    def create(cls, board_style: ResolvedStyle) -> BoardRenderSession:
        """Create a session with the default feature pipeline."""
        return cls(
            board_style=board_style,
            features=FeaturePipeline(list(DEFAULT_FEATURES)),
        )

    def render_svg_family(
        self,
        chart: ResolvedChart,
        data: list[dict[str, Any]],
        *,
        width: float,
        height: float | None = None,
        is_placeholder: bool = False,
        inset: dict[str, int | float] | None = None,
        padding: dict[str, int | float] | None = None,
        title_shift: TitleShift = NO_TITLE_SHIFT,
    ) -> str | None:
        """Non-VL families → SVG string; VL families → None (caller runs emit path).

        ``inset`` is the padding the caller shrank ``width``/``height`` by; a
        family that paints a card rect grows it back out over that padding.
        ``padding`` is the padding a custom chart frames itself inside at the
        full ``width``/``height``, as a Vega-Lite chart does.
        """
        match chart:
            case ResolvedKpiChart() | ResolvedSparkBarChart():
                # No slot height is the card's own: a KPI's `default_height`, a
                # spark_bar's natural one (the engine's `0.0`).
                if height:
                    slot_height = height
                elif isinstance(chart, ResolvedKpiChart):
                    kpi_style = chart.style.kpi
                    assert kpi_style is not None, "resolve() must populate style.kpi"
                    slot_height = kpi_style.default_height
                else:
                    slot_height = 0.0
                svg, _ = render_native(
                    chart,
                    data,
                    width=width,
                    height=slot_height,
                    padding=inset,
                    board_style=self.board_style,
                    board_width=None,
                    title_shift=title_shift,
                    is_placeholder=is_placeholder,
                )
                return svg
            case ResolvedTableChart():
                return render_native(
                    chart,
                    data,
                    width=width,
                    height=height
                    or 0.0,  # type-state: silent_fallback — 0 asks the engine for natural height
                    padding=inset,
                    board_style=self.board_style,
                    title_shift=title_shift,
                )[0]
            case ResolvedCustomChart():
                # A missing slot height means "no slot": the chart's own
                # aspect-ratio box answers.
                svg, _ = render_native(
                    chart,
                    data,
                    width=width,
                    height=custom_chart_height(
                        chart.aspect_ratio,
                        chart.min_height,
                        chart.max_height,
                        width,
                        self.board_style.chart_defaults,
                    )
                    if height is None
                    else height,
                    padding=padding,
                    board_style=self.board_style,
                    board_width=width,
                    title_shift=title_shift,
                )
                return svg
            case ResolvedPieChart():
                svg, _ = render_native(
                    chart,
                    data,
                    width=width,
                    height=self.board_style.chart_defaults.view.continuous_height
                    if height is None
                    else height,  # type-state: silent_fallback — no slot is the theme's view height
                    padding=padding,
                    board_style=self.board_style,
                    title_shift=title_shift,
                    is_placeholder=is_placeholder,
                )
                return svg
            case ResolvedCalloutChart():
                svg, _ = render_native(
                    chart,
                    data,
                    width=width,
                    height=0.0 if height is None else height,
                    padding=None,
                    board_style=self.board_style,
                    board_width=width,
                )
                return svg
            case _:
                return None

    def emit_chart(
        self,
        chart: ResolvedChart,
        box: RenderBox,
        datasets: dict[str | None, list[dict[str, Any]]],
    ) -> ChartSpec:
        """Run emit → features for one chart.

        Returns the ``ChartSpec`` before assembly; callers drive
        ``finalize_vl(spec)`` for VL families.

        Args:
            chart: Resolved chart for this family.
            box: Render-time slot geometry passed through to emitters and features.
            datasets: Every query this chart references, keyed by query_name —
                the base chart's own rows (``chart.query_name``) plus, for a
                cartesian chart with typed overlay layers, each layer's own
                query override. Built by ``layout_sizing.build_chart_datasets``.

        Returns:
            Populated ``ChartSpec`` after all applicable features applied.
        """
        data = chart_rows(chart, datasets)
        # A faceted chart resolved against zero rows bakes panel_axes == ()
        # (partition()'s empty-data rule) even though multiples is set. Check
        # this before the emitter runs — an emitter that regroups by
        # panel_axes (gap-fill, per-panel validation) sees axes=() as the
        # ordinary N=1 case and validates real data as one giant panel,
        # raising a misleading duplicate-rows error before this chart ever
        # reaches FacetFeature. Loud and accurate rather than silent or wrong.
        if (
            isinstance(chart, _CartesianResolvedChartFields)
            and chart.multiples is not None
            and not chart.panel_axes
            and data.all_rows()
        ):
            raise ChartDataError.from_code(
                ERR_MULTIPLES_RESOLVED_WITHOUT_DATA, chart_id=chart.id
            )
        emitter = get_emitter(chart)
        if isinstance(chart, LayeredResolvedChart):
            spec = emitter.emit(chart, box, data, datasets=datasets)  # type: ignore[call-arg]
        else:
            spec = emitter.emit(chart, box, data)
        spec = self.features.apply(spec, chart, box, datasets)
        if (
            spec.data is None
        ):  # emitters that pre-populate data (e.g. point_map) take priority
            spec.data = normalize_data_types(data.all_rows())
        # Bake title/subtitle/title_font from the resolved chart so finalize_vl
        # can emit the chart title block with correct typography.
        # Every ResolvedChart union member carries title/subtitle
        # (KPI and Callout's are always None) so this is plain attribute access.
        spec.title = chart.title
        spec.subtitle = chart.subtitle
        spec.title_font = getattr(chart.style, "title_font", None)
        # get_emitter above only returns for the VL families, all of which are
        # _SharedResolvedChartFields subclasses (KPI/callout/table/spark_bar
        # are dispatched to render_svg_family before this point) — background
        # and title_style are guaranteed present, so read them directly.
        assert isinstance(chart, _SharedResolvedChartFields)
        spec.background = chart.background
        spec.title_style = chart.title_style
        return spec

    def finalize_vl(self, spec: ChartSpec) -> VLDict:
        """Assemble a ChartSpec to a final Vega-Lite spec dict.

        Delegates to ``assemble_final_vl`` with the session's ``board_style``.

        Args:
            spec: ``ChartSpec`` returned by ``emit_chart``.

        Returns:
            Vega-Lite JSON-serializable spec dict with config and background applied.
        """
        return assemble_final_vl(spec, self.board_style)
