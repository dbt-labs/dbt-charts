"""V2 render path: ``multiples:`` compiles to a Vega-Lite ``facet`` spec.

Small multiples partition a single cartesian grammar by a ``rows`` field
(vertical stack), a ``columns`` field (side by side), or both (grid). Panels
share one measure scale by default; the facet value reads as a header label
(left for rows, top for columns), not a per-panel title. The both-edge y-axis
(mirror) auto-enables when there is more than one column, by defaulting the ONE
shipped ``resolved_axis_y.mirror`` flag — no second mirror mechanism.
"""

from __future__ import annotations

from typing import Any, Literal

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _board() -> Any:
    return resolve_style_and_context(get_theme_style("clarity"))


def _v2_vl(
    norm: Chart, data: list[dict[str, Any]], box: RenderBox = _DEFAULT_BOX
) -> dict[str, Any]:
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.session import BoardRenderSession

    board_rs, board_ctx = _board()
    resolved = resolve(norm, data, chart_style_context=board_ctx)
    session = BoardRenderSession.create(board_rs)
    return session.finalize_vl(
        session.emit_chart(resolved, box, {resolved.query_name: data})
    )


def _grid_data() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for region in ("West", "East", "North"):
        for product in ("Widgets", "Gadgets"):
            for month in range(6):
                rows.append(
                    {
                        "month": month,
                        "region": region,
                        "product": product,
                        "revenue": 100 + month * 5,
                        "cost": 40 + month * 2,
                    }
                )
    return rows


def _row_data() -> list[dict[str, Any]]:
    return [
        {"month": m, "region": r, "revenue": 100 + m * 5}
        for r in ("West", "East", "North")
        for m in range(6)
    ]


def _bar(
    multiples: dict[str, Any],
    orientation: Literal["vertical", "horizontal"],
    **extra: Any,
) -> BarChart:
    return BarChart.model_validate(
        {
            "id": "t",
            "type": "bar",
            "query_name": "q",
            "x": "product",
            "y": "revenue",
            "multiples": multiples,
            "style": {"orientation": orientation},
            **extra,
        }
    )


def _area(multiples: dict[str, Any], **extra: Any) -> AreaChart:
    return AreaChart.model_validate(
        {
            "id": "t",
            "type": "area",
            "query_name": "q",
            "x": "month",
            "y": "revenue",
            "multiples": multiples,
            **extra,
        }
    )


class TestFacetStructure:
    def test_row_facet_renders_panels_in_query_order(self):
        """The spec-level `sort` assertion above only proves the VL key is
        set correctly. VL's own default sort for a facet field is
        alphabetical regardless of `sort: null`, unlike a position channel,
        so only an end-to-end render through vl_convert proves panel order."""
        import re

        from dbt_charts.core.render.chart.vega_lite import render_chart

        board_rs, board_ctx = _board()
        svg = render_chart(
            _area({"rows": "region"}),
            board_rs,
            board_ctx,
            _row_data(),
            format="svg",
        )
        first_seen = []
        for match in re.finditer(r"West|East|North", svg):
            if match.group() not in first_seen:
                first_seen.append(match.group())
        assert first_seen == ["West", "East", "North"]


class TestFacetValidation:
    def test_scale_independent_plus_mirror_raises(self):
        """Fires the typed ERR-MULTIPLES-INDEPENDENT-SCALE-MIRROR code, never
        the ERR-INTERNAL fallback."""
        from dbt_charts.core.diagnostics.codes_render import (
            ERR_MULTIPLES_INDEPENDENT_SCALE_MIRROR,
        )

        chart = _area(
            {"rows": "region", "columns": "product", "scale": "independent"},
            style={"axis_y": {"mirror": True}},
        )
        with pytest.raises(
            ChartDataError, match="each panel has its own scale"
        ) as exc_info:
            _v2_vl(chart, _grid_data())
        assert exc_info.value.code is ERR_MULTIPLES_INDEPENDENT_SCALE_MIRROR

    def test_bar_scale_independent_plus_mirror_raises(self):
        """Bar resolves the mirror default after its orientation is known, so the
        independent-scale contradiction must still fire for a vertical bar."""
        from dbt_charts.core.diagnostics.codes_render import (
            ERR_MULTIPLES_INDEPENDENT_SCALE_MIRROR,
        )

        chart = _bar(
            {"columns": "region", "scale": "independent"},
            "vertical",
            style={"orientation": "vertical", "axis_y": {"mirror": True}},
        )
        data = [
            {"product": p, "region": r, "revenue": 100 + i}
            for i, (r, p) in enumerate(
                (r, p) for r in ("West", "East") for p in ("A", "B", "C")
            )
        ]
        with pytest.raises(ChartDataError) as exc_info:
            _v2_vl(chart, data)
        assert exc_info.value.code is ERR_MULTIPLES_INDEPENDENT_SCALE_MIRROR

    def test_scale_independent_plus_mirror_format_override_raises(self):
        """The scale-independent/mirror contradiction must also fire for the
        object form (mirror: {format: ...}) — an AxisMirrorStyle override still
        turns mirroring on, it just also relabels the mirrored edge."""
        chart = _area(
            {"rows": "region", "columns": "product", "scale": "independent"},
            style={"axis_y": {"mirror": {"format": ".0%"}}},
        )
        with pytest.raises(ChartDataError, match="each panel has its own scale"):
            _v2_vl(chart, _grid_data())


class TestAuthoredSurface:
    """The authored surface is object-only: rows and/or columns (at least one)."""

    def test_rows_only(self):
        from dbt_charts.core.compile.models.chart.authored import (
            AreaChart as AuthoredArea,
            MultiplesConfig,
        )

        chart = AuthoredArea.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "revenue",
                "multiples": {"rows": "region"},
            }
        )
        assert isinstance(chart.multiples, MultiplesConfig)
        assert chart.multiples.rows == "region"
        assert chart.multiples.columns is None
        assert chart.multiples.scale == "shared"

    def test_columns_only(self):
        from dbt_charts.core.compile.models.chart.authored import (
            AreaChart as AuthoredArea,
        )

        chart = AuthoredArea.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "revenue",
                "multiples": {"columns": "product"},
            }
        )
        assert chart.multiples is not None
        assert chart.multiples.rows is None
        assert chart.multiples.columns == "product"

    def test_both_with_independent_scale(self):
        from dbt_charts.core.compile.models.chart.authored import (
            AreaChart as AuthoredArea,
        )

        chart = AuthoredArea.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "revenue",
                "multiples": {
                    "rows": "region",
                    "columns": "product",
                    "scale": "independent",
                },
            }
        )
        assert chart.multiples is not None
        assert chart.multiples.rows == "region"
        assert chart.multiples.columns == "product"
        assert chart.multiples.scale == "independent"

    def test_empty_multiples_raises(self):
        """Neither rows nor columns → error (at least one required)."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.chart.authored import (
            AreaChart as AuthoredArea,
        )

        with pytest.raises(
            ValidationError, match="at least one of `rows` or `columns`"
        ):
            AuthoredArea.model_validate(
                {
                    "id": "t",
                    "type": "area",
                    "x": "month",
                    "y": "revenue",
                    "multiples": {"scale": "shared"},
                }
            )

    def test_pie_rejects_multiples(self):
        """multiples is cartesian-only — a non-cartesian family rejects it via
        extra='forbid' (small multiples has no meaning on an arc chart)."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.chart.authored import PieChart

        with pytest.raises(ValidationError):
            PieChart.model_validate(
                {
                    "id": "t",
                    "type": "pie",
                    "theta": "revenue",
                    "multiples": {"rows": "region"},
                }
            )


# ---------------------------------------------------------------------------
# Facet panel sizing (pure bridge helpers) — the render bridge divides the slot
# into per-panel dimensions because vl-convert does not reflow facet panels.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# render_chart bridge — V1 refusal + V2 faceted integration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# FacetFeature refusals — incompatible compositions error, not silently render
# ---------------------------------------------------------------------------


class TestFacetRefusals:
    def _resolved_area(
        self, multiples: dict[str, Any], extra: dict[str, Any] | None = None
    ):
        from dbt_charts.core.compile.resolve import resolve

        spec = {
            "id": "t",
            "type": "area",
            "x": "month",
            "y": "revenue",
            "multiples": multiples,
        }
        if extra:
            spec.update(extra)
        return resolve(
            AreaChart.model_validate(spec), _row_data(), chart_style_context=_board()[1]
        )

    def test_unknown_partition_field_raises(self):
        """A mistyped multiples field fails loud with the available columns, like x/y."""
        chart = _area({"rows": "regionn"})  # typo
        with pytest.raises(ChartDataError, match="not found in the query result"):
            _v2_vl(chart, _row_data())
