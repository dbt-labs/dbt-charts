"""A chart's own ``legend:`` keys outrank the automatic legend policies.

Three ways an authored legend key used to be discarded in silence:

- ``legend.visible: true`` lost to the endpoint-label rail, which retires the
  color legend whenever it fires (the theme switches the rail on, so this hit
  every multi-series line and area chart).
- ``legend.position`` lost to the automatic top-legend policy on bar, and on
  line resolved to a legend that was never shown, because the position key
  alone left the theme's ``visible: false`` standing.
- ``legend.position: none`` was Vega-Lite's ``orient: "none"`` — "place me by
  hand", which with no coordinates floats the legend inside the plot — not
  "no legend".
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart


@pytest.fixture(autouse=True)
def _reset():
    """A neighbor on the same xdist worker can leave a config cached, and every
    resolution here reads the theme."""
    reset_config()
    yield
    reset_config()


_DATA = [
    {"date": "2024-01-01", "value": 100, "series": "Core"},
    {"date": "2024-02-01", "value": 120, "series": "Core"},
    {"date": "2024-03-01", "value": 140, "series": "Core"},
    {"date": "2024-01-01", "value": 200, "series": "Growth"},
    {"date": "2024-02-01", "value": 220, "series": "Growth"},
    {"date": "2024-03-01", "value": 180, "series": "Growth"},
]


def _render(make_chart, chart_type: str, style: dict[str, Any]):
    chart = make_chart(chart_type, x="date", y="value", color="series", style=style)
    theme = get_theme_style()
    context = resolve_chart_style_context(theme)
    resolved = resolve(chart, _DATA, chart_style_context=context)
    artifact = render_resolved_chart(resolved, _DATA, resolve_style(theme))
    assert artifact.kind == "vega_spec"
    return resolved, artifact.payload


def _color_legend(spec: dict[str, Any]) -> Any:
    """The color legend config of the spec's series color encoding.

    Walks concat/layer wrappers because an endpoint-label rail wraps the plot
    in an hconcat and the value-label feature adds sublayers; the series
    color channel is on the first node that carries one.
    """
    encoding = spec.get("encoding")
    if isinstance(encoding, dict) and "color" in encoding:
        return encoding["color"].get("legend", "<absent>")
    for key in ("hconcat", "vconcat", "concat", "layer"):
        for child in spec.get(key, []):
            found = _color_legend(child)
            if found not in ("<no-color-encoding>", "<absent>"):
                return found
    return "<no-color-encoding>"


class TestAuthoredVisibleTrueBeatsTheEndpointRail:
    def test_line_legend_visible_true_survives_the_rail(self, make_chart):
        """The rail replaces a legend nobody asked for, never one that was."""
        resolved, spec = _render(make_chart, "line", {"legend": {"visible": True}})

        assert resolved.legend.visible is True
        assert isinstance(_color_legend(spec), dict)

    def test_the_rail_still_renders_alongside_it(self, make_chart):
        """Asking for the legend back does not turn the rail off."""
        resolved, spec = _render(make_chart, "line", {"legend": {"visible": True}})

        assert resolved.style.endpoint_labels.visible is True
        assert "hconcat" in spec

    def test_line_legend_visible_false_still_hides(self, make_chart):
        resolved, spec = _render(make_chart, "line", {"legend": {"visible": False}})

        assert resolved.legend.visible is False
        assert _color_legend(spec) is None

    def test_an_unauthored_legend_is_still_retired_by_the_rail(self, make_chart):
        resolved, spec = _render(make_chart, "line", {})

        assert resolved.legend.visible is False
        assert _color_legend(spec) is None


class TestAuthoredPositionIsHonored:
    def test_line_position_bottom_renders_a_bottom_legend(self, make_chart):
        """Naming a position asks for a legend — it cannot resolve to nothing."""
        resolved, spec = _render(
            make_chart, "line", {"legend": {"position": {"edge": "bottom"}}}
        )

        assert resolved.legend.visible is True
        assert resolved.legend.position.edge == "bottom"
        assert _color_legend(spec)["orient"] == "bottom"

    def test_bar_position_bottom_beats_the_automatic_top_legend(self, make_chart):
        """A grouped bar routes its legend to the top strip unless told otherwise."""
        resolved, spec = _render(
            make_chart, "bar", {"legend": {"position": {"edge": "bottom"}}}
        )

        assert resolved.legend.position.edge == "bottom"
        assert _color_legend(spec)["orient"] == "bottom"

    def test_bar_keeps_its_top_legend_when_no_position_is_authored(self, make_chart):
        resolved, spec = _render(make_chart, "bar", {})

        assert resolved.legend.position.edge == "top"
        assert _color_legend(spec)["orient"] == "top"

    def test_bar_position_top_is_the_unauthored_top_strip(self, make_chart):
        """Authoring the position the engine already picked must not make it worse.

        The automatic top strip is not just an orient — it is a horizontal,
        uncolumned, titleless band sized to sit above the plot. Treating any
        authored position as a conflict to resolve short-circuits that branch,
        so ``position: top`` used to buy a vertical stack with a title above the
        plot: strictly worse than authoring nothing. Agreement is not a conflict.
        """
        authored, _ = _render(
            make_chart, "bar", {"legend": {"position": {"edge": "top"}}}
        )
        unauthored, _ = _render(make_chart, "bar", {})

        assert authored.legend.position.edge == "top"
        assert authored.legend.direction == unauthored.legend.direction == "horizontal"
        assert authored.legend.columns == unauthored.legend.columns == 0
        assert authored.legend.title.visible is unauthored.legend.title.visible is False

    def test_position_with_visible_false_stays_hidden(self, make_chart):
        """``visible: false`` is the author's own say-so too, and it is nearer."""
        resolved, spec = _render(
            make_chart,
            "line",
            {"legend": {"position": {"edge": "bottom"}, "visible": False}},
        )

        assert resolved.legend.visible is False
        assert _color_legend(spec) is None


def test_legend_position_none_is_rejected(make_chart):
    """``none`` was VL's "position me by hand", never "hide me"."""
    with pytest.raises(ValidationError, match="position"):
        make_chart(
            "line",
            x="date",
            y="value",
            color="series",
            style={"legend": {"position": "none"}},
        )
