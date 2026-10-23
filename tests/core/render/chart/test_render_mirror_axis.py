"""V2 render path: ``style.axis_y.mirror`` draws the y-scale on both edges.

The feature is a ``MirrorAxisFeature`` in the V2 FeaturePipeline that appends a
transparent ghost overlay layer carrying the opposite-edge y-axis and sets
``resolve.axis.y = "independent"`` off the single shared scale.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    Chart,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)
_SVG_NS = "http://www.w3.org/2000/svg"


_MIRROR: dict[str, Any] = {"axis_y": {"mirror": True}}


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _board() -> Any:
    return resolve_style_and_context(get_theme_style("clarity"))


def _v2_vl(
    norm: Chart, data: list[dict[str, Any]], board: Any | None = None
) -> dict[str, Any]:
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.session import BoardRenderSession

    board_rs, board_ctx = board if board is not None else _board()
    resolved = resolve(norm, data, chart_style_context=board_ctx)
    session = BoardRenderSession.create(board_rs)
    return session.finalize_vl(
        session.emit_chart(resolved, _DEFAULT_BOX, {resolved.query_name: data})
    )


def _y_axis_key_by_orient(spec: dict[str, Any], key: str) -> dict[str, Any]:
    """Map each y-axis orient ('left'/'right') to its VL axis value for `key`."""
    found: dict[str, Any] = {}

    def walk(s: dict[str, Any]) -> None:
        enc = s.get("encoding")
        if isinstance(enc, dict) and isinstance(enc.get("y"), dict):
            axis = enc["y"].get("axis")
            if isinstance(axis, dict) and axis.get("orient"):
                found[axis["orient"]] = axis.get(key)
        for lyr in s.get("layer", []):
            if isinstance(lyr, dict):
                walk(lyr)

    walk(spec)
    return found


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


def _compacting_data() -> list[dict[str, Any]]:
    """A 0-450k-shaped ladder — big enough to compact (six written-out
    integer digits at the extreme) and small enough to land in ANCHOR mode,
    matching the worked example the ruler tests already pin.
    """
    return [{"month": i, "value": i * 100_000} for i in range(6)]


class TestV2MirrorAxisRulerMechanism:
    """Each edge of a mirrored axis takes its ruler from its own resolved
    format and anchoring, never the primary's labelExpr verbatim. A literal d3
    spec on a right-edge axis is only right-aligned (per-tick literal text, no
    ruler); a house alias bakes the end-anchored ruler, and the ghost bakes one
    only when its own format needs it.
    """

    @staticmethod
    def _labelexprs(position: str, labels: dict[str, Any], mirror: Any) -> Any:
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {"position": position, "labels": labels, "mirror": mirror}
                },
            }
        )
        return _y_axis_key_by_orient(_v2_vl(chart, _compacting_data()), "labelExpr")


def _v2_svg(norm: Chart, data: list[dict[str, Any]], board: Any | None = None) -> str:
    """Render a chart through the full V2 pipeline to SVG via vl-convert."""
    from dbt_charts.core.render.chart.vega_lite import render_chart

    board_rs, board_ctx = board if board is not None else _board()
    return render_chart(
        norm, board_rs, board_ctx, data, format="svg", width=600, height=300
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

    @pytest.fixture(autouse=True)
    def reset(self):
        reset_config()
        yield
        reset_config()

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
        svg = _v2_svg(chart, data)
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
        svg = _v2_svg(chart, data)
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
        svg = _v2_svg(chart, data)
        # Aria-label attributes (not text content) still carry "Signups: <value>"
        assert "Signups: 100" in svg
