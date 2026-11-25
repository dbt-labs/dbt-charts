"""Legend placement models: the cartesian position object, the pie key's
edge-only position, and the legend direction values Vega-Lite supports.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.style.authored import (
    LegendStylePatch,
    PieChartStylePatch,
    PieLegendStylePatch,
)
from dbt_charts.core.compile.models.style.theme.legend import (
    LegendPositionStyle,
    LegendStyle,
    PieLegendStyle,
)


def _legend(position: object = None, direction: str = "vertical") -> LegendStyle:
    return LegendStyle.model_validate(
        {
            **({} if position is None else {"position": position}),
            "direction": direction,
            "columns": 0,
            "compact_columns": 2,
            "entry_gap": 10.0,
            "symbol": {"size": 10.0},
            "background": "#ffffff",
            "opacity": 1.0,
            "border": {"width": 0.0, "color": "#cccccc", "radius": 3.0},
            "label": {"padding": 8.0, "halo": 0},
            "title": {"padding": 0.0},
        }
    )


def test_unauthored_position_leaves_every_leaf_unset() -> None:
    assert _legend().position == LegendPositionStyle(
        edge=None, align=None, overlay=None
    )


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
@pytest.mark.parametrize("align", ["start", "center", "end"])
@pytest.mark.parametrize("overlay", [True, False])
def test_position_accepts_every_leaf_combination(
    edge: str, align: str, overlay: bool
) -> None:
    position = _legend({"edge": edge, "align": align, "overlay": overlay}).position
    assert (position.edge, position.align, position.overlay) == (edge, align, overlay)


@pytest.mark.parametrize(
    "position",
    [
        "top",
        "top-left",
        {"edge": "center"},
        {"edge": "none"},
        {"align": "left"},
        {"overlay": "maybe"},
        {"placement": "top"},
    ],
)
def test_position_rejects_the_retired_scalar_and_unknown_values(
    position: object,
) -> None:
    with pytest.raises(ValidationError):
        _legend(position)


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_pie_position_accepts_an_edge(edge: str) -> None:
    legend = PieLegendStyle.model_validate({"position": {"edge": edge}})
    assert legend.position.edge == edge


@pytest.mark.parametrize("leaf", ["columns", "limit", "title", "direction"])
def test_a_pie_legend_takes_only_its_position(leaf: str) -> None:
    """The attached table is the pie's legend: no other leaf styles it."""
    with pytest.raises(ValidationError):
        PieLegendStyle.model_validate({"position": {"edge": "top"}, leaf: 2})
    with pytest.raises(ValidationError):
        PieLegendStylePatch.model_validate({leaf: 2})


@pytest.mark.parametrize("align", ["start", "center", "end"])
def test_pie_position_accepts_an_align(align: str) -> None:
    patch = PieLegendStylePatch.model_validate(
        {"position": {"edge": "top", "align": align}}
    )
    assert patch.position.align == align


def test_pie_legend_rejects_overlay() -> None:
    with pytest.raises(ValidationError):
        PieLegendStylePatch.model_validate(
            {"position": {"edge": "right", "overlay": False}}
        )
    with pytest.raises(ValidationError):
        PieChartStylePatch.model_validate(
            {"key": {"legend": {"position": {"overlay": True}}}}
        )


def test_cartesian_legend_patch_accepts_align_and_overlay() -> None:
    patch = LegendStylePatch.model_validate(
        {"position": {"edge": "top", "align": "end", "overlay": True}}
    )
    assert patch.position.overlay is True


@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
def test_legend_style_accepts_valid_directions(direction: str) -> None:
    assert _legend(direction=direction).direction == direction


def test_legend_style_rejects_invalid_direction() -> None:
    with pytest.raises(ValidationError):
        _legend(direction="diagonal")
