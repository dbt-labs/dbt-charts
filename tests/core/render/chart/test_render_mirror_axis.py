"""``style.axis_y.mirror`` draws the y-scale on both edges without repeating the axis title."""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.compile.models.chart.normalized import AreaChart

from ..._engine_render import render_chart_svg

_SVG_NS = "http://www.w3.org/2000/svg"


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


class TestV2MirrorAxis:
    def test_mirror_is_boolean_only(self):
        """style.axis_y.mirror is a boolean flag — non-bool values are rejected."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AreaChart.model_validate(
                {
                    "id": "t",
                    "type": "area",
                    "x": "month",
                    "y": "value",
                    "style": {"axis_y": {"mirror": "left"}},
                }
            )


def _svg_text_count(svg: str, label: str) -> int:
    """Count SVG <text> elements whose text content equals label exactly."""
    root = ET.fromstring(svg)
    return sum(1 for t in root.iter(f"{{{_SVG_NS}}}text") if t.text == label)


class TestMirrorAxisTitleSuppression:
    """Regression: mirrored y-axis must not leak the measure's display name
    as a stray axis-title text node in the rendered SVG.

    Root cause: ``MirrorAxisFeature.apply`` built the ghost axis with
    ``ghost_axis.pop("title", None)``, which removes the key entirely rather
    than nulling it. With no ``title`` key, Vega-Lite falls back to
    ``encoding.y.title`` on the ghost layer and paints it as a visible rotated
    label. Setting ``ghost_axis["title"] = None`` keeps an explicit null.

    Both entry points reach the same code path:
    - Authored ``style.axis_y.mirror: true`` via ``MirrorAxisFeature``
    - ``multiples: {columns: ...}`` via ``_apply_multiples_mirror`` at resolve time
    """

    def test_authored_mirror_renders_zero_axis_title_text_nodes(self):
        """style.axis_y.mirror: true — no stray measure-axis title text in the SVG."""
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "signups",
                "style": {"axis_y": {"mirror": True}},
            }
        )
        data = [{"month": i, "signups": 100 + i * 5} for i in range(6)]
        svg = render_chart_svg(chart, data)
        assert _svg_text_count(svg, "Signups") == 0

    def test_multiples_columns_auto_mirror_renders_zero_axis_title_text_nodes(self):
        """multiples: {columns: ...} auto-mirror — same zero-title assertion on SVG."""
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "signups",
                "multiples": {"columns": "region"},
            }
        )
        data = [
            {"month": i, "region": r, "signups": 100 + i * 5 + j * 50}
            for j, r in enumerate(["East", "West"])
            for i in range(4)
        ]
        svg = render_chart_svg(chart, data)
        assert _svg_text_count(svg, "Signups") == 0

    def test_authored_mirror_aria_labels_still_carry_field_name(self):
        """After fixing the axis title leak, data marks still carry 'Signups'
        in their aria-labels — the tooltip/accessibility field label is intact.

        The fix sets encoding.y.title: null on the BASE (shared) encoding only.
        The LAYER encodings retain title: 'Signups', so VL's aria-label on each
        mark still reads 'Signups: <value>'.
        """
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "signups",
                "style": {"axis_y": {"mirror": True}},
            }
        )
        data = [{"month": i, "signups": 100 + i * 5} for i in range(6)]
        svg = render_chart_svg(chart, data)
        # Aria-label attributes (not text content) still carry "Signups: <value>"
        assert "Signups: 100" in svg
