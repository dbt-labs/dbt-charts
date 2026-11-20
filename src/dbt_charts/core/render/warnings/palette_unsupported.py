"""Detector: WARN_PALETTE_UNSUPPORTED — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule: the chart authored a palette name on the known anti-pattern
list (e.g. "RdYlGn", "parula"), either as its categorical palette or as a
continuous color scale's palette. The palette still draws as authored; this
detector is the only place the recommendation surfaces.

A categorical palette resolves to stops at compile time, so its name survives
only as ``chart.warned_palette`` (with ``chart.warned_palette_recommendation``
baked beside it, see compile/resolve/chart/_kwargs.py::_base_kwargs). A
continuous scale keeps its palette name, so the scales are read directly, the
same ones WARN-GRADIENT-HINGE-MISMATCH checks.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.palette import PALETTE_WARN_RECOMMENDATIONS
from dbt_charts.core.compile.models.primitives import (
    ResolvedNamedPaletteScaleTargetConfig,
)
from dbt_charts.core.diagnostics import WARN_PALETTE_UNSUPPORTED, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext
from dbt_charts.core.render.warnings.gradient_hinge_mismatch import scale_bindings


def _diagnostic(
    chart_id: str, path: str | None, requested: str, recommended: str
) -> Diagnostic:
    return Diagnostic.from_code(
        WARN_PALETTE_UNSUPPORTED,
        chart=chart_id,
        path=path,
        field="palette",
        message=WARN_PALETTE_UNSUPPORTED.message_template.format(
            chart_id=chart_id, requested=requested, recommended=recommended
        ),
        fix=WARN_PALETTE_UNSUPPORTED.fix_template.format(
            requested=requested, recommended=recommended
        ),
    )


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per anti-pattern palette name a chart authors."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        requested = chart.warned_palette
        if requested is not None:
            assert chart.warned_palette_recommendation is not None, (
                "warned_palette_recommendation must be baked whenever warned_palette is set"
            )
            # Set on CategoricalColorStyle, so the authored key is
            # `palette` or `single_series_palette` under
            # `style.color.categorical`; naming the common one still lands
            # in the right block.
            warnings.append(
                _diagnostic(
                    chart_id,
                    f"charts.{chart_id}.style.color.categorical.palette",
                    requested,
                    chart.warned_palette_recommendation,
                )
            )
        for path, _, scale, _ in scale_bindings(chart_id, chart):
            if not isinstance(scale, ResolvedNamedPaletteScaleTargetConfig):
                continue
            name = scale.warned_palette
            if name is None:
                continue
            warnings.append(
                _diagnostic(
                    chart_id,
                    path,
                    name,
                    PALETTE_WARN_RECOMMENDATIONS[name],
                )
            )

    return warnings
