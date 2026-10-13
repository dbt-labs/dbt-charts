"""Renamed spark variants (M2): bar / bar-normalize / columns.

Locks the rename table from the M2 in-cell visual encodings task:

| New             | Replaces           | Semantics                                                   |
|---|---|---|
| `bar`           | `progress` (no max) | single horizontal bar, scaled to column-max, no track       |
| `bar-normalize` | `progress` (w/ max) | single horizontal bar, scaled to explicit max, track on     |
| `columns`       | `bars` + `histogram`| multi-value vertical bars                                   |

The old names (`progress`, `bars`, `histogram`) must raise a clear
"renamed to X" error — pre-launch, no aliases (project AGENTS.md).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.authored import (
    SparkConfig,
    TableColumnConfig,
)
from dbt_charts.core.compile.models.style.authored import (
    TableChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

from ._engine_render import render_table_svg

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

_EFF = resolve_style(get_theme_style()).chart_defaults


# ---------------------------------------------------------------------------
# Direct renderer tests — new variant names
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Dispatcher tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Compile-time rejection of renamed-away names
# ---------------------------------------------------------------------------


class TestRenamedAwayNamesRejected:
    """Old variant names must raise with a clear "renamed to X" message.

    Pre-launch — no aliases, no silent fallback (project AGENTS.md).
    """

    @pytest.mark.parametrize(
        ("old_name", "new_name"),
        [
            ("progress", "bar"),
            ("bars", "columns"),
            ("histogram", "columns"),
        ],
    )
    def test_sparkconfig_rejects_old_type(self, old_name: str, new_name: str) -> None:
        """SparkConfig(type=<old>) raises with a renamed-to message."""
        with pytest.raises(ValidationError) as excinfo:
            SparkConfig(type=old_name)  # type: ignore[arg-type]
        msg = str(excinfo.value)
        assert old_name in msg
        assert new_name in msg
        assert "renamed" in msg.lower()

    @pytest.mark.parametrize(
        ("old_name", "new_name"),
        [
            ("progress", "bar"),
            ("bars", "columns"),
            ("histogram", "columns"),
        ],
    )
    def test_sparkconfig_dict_rejects_old_type(
        self, old_name: str, new_name: str
    ) -> None:
        """SparkConfig(**{"type": <old>, ...}) raises with a renamed-to message."""
        with pytest.raises(ValidationError) as excinfo:
            SparkConfig(**{"type": old_name})  # type: ignore[arg-type]
        msg = str(excinfo.value)
        assert old_name in msg
        assert new_name in msg
        assert "renamed" in msg.lower()

    @pytest.mark.parametrize(
        ("old_name", "new_name"),
        [
            ("progress", "bar"),
            ("bars", "columns"),
            ("histogram", "columns"),
        ],
    )
    def test_table_column_config_shorthand_rejects_old_type(
        self, old_name: str, new_name: str
    ) -> None:
        """`spark: <old-shorthand>` on a column raises with a renamed-to message."""
        with pytest.raises(ValidationError) as excinfo:
            TableColumnConfig(spark=old_name)  # type: ignore[arg-type]
        msg = str(excinfo.value)
        assert old_name in msg
        assert new_name in msg
        assert "renamed" in msg.lower()


# ---------------------------------------------------------------------------
# Table-rendering integration: new variants flow through the table pipeline
# ---------------------------------------------------------------------------


def _mock_chart(style=None):
    """Minimal table chart for render_table_svg."""
    from dbt_charts.core.compile.models.chart.normalized import TableChart

    if isinstance(style, dict):
        style = TableChartStylePatch.model_validate(style)
    return TableChart(id="test_table", type="table", style=style)


class TestSparkBarVariantInTable:
    """`spark.type: bar` — auto-scale to column max, no background track."""

    def test_no_background_track_rendered(self) -> None:
        chart = _mock_chart(
            style={
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "bar"}},
                }
            }
        )
        data = [{"name": "a", "val": 10}, {"name": "b", "val": 20}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )
        # Two spark cells, each with a single fill rect (no background).
        # Count <rect> elements inside spark <g> translate groups.
        import re

        groups = re.findall(r'<g transform="translate[^"]+">.*?</g>', svg, re.DOTALL)
        spark_groups = [g for g in groups if "<rect" in g]
        for g in spark_groups:
            assert g.count("<rect") == 1, (
                f"bar variant must render a single rect (no track), got: {g[:200]}"
            )


class TestSparkBarNormalizeVariantInTable:
    """`spark.type: bar-normalize` — scale to explicit max, background track on."""

    def test_background_track_rendered(self) -> None:
        chart = _mock_chart(
            style=TableChartStylePatch.model_validate(
                {
                    "columns": {
                        "name": {},
                        "pct": {"spark": {"type": "bar-normalize", "max": 100}},
                    }
                }
            )
        )
        data = [{"name": "a", "pct": 25}, {"name": "b", "pct": 75}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )

        import re

        groups = re.findall(r'<g transform="translate[^"]+">.*?</g>', svg, re.DOTALL)
        spark_groups = [g for g in groups if "<rect" in g]
        assert len(spark_groups) == 2
        for g in spark_groups:
            # background + fill = 2 rects
            assert g.count("<rect") == 2

    def test_widths_scale_to_explicit_max(self) -> None:
        chart = _mock_chart(
            style=TableChartStylePatch.model_validate(
                {
                    "columns": {
                        "name": {},
                        "pct": {"spark": {"type": "bar-normalize", "max": 100}},
                    }
                }
            )
        )
        data = [
            {"name": "a", "pct": 10},
            {"name": "b", "pct": 50},
            {"name": "c", "pct": 100},
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )

        import re

        fracs: list[float] = []
        groups = re.findall(r'<g transform="translate[^"]+">.*?</g>', svg, re.DOTALL)
        for g in groups:
            widths = [float(w) for w in re.findall(r'width="([\d.]+)"', g)]
            if len(widths) >= 2:
                fracs.append(round(widths[1] / widths[0], 2))
        assert fracs == [pytest.approx(0.1), pytest.approx(0.5), pytest.approx(1.0)]


class TestSparkColumnsVariantInTable:
    """`spark.type: columns` — multi-value vertical bars, one per array element."""

    def test_renders_rect_per_value(self) -> None:
        chart = _mock_chart(
            style=TableChartStylePatch.model_validate(
                {
                    "columns": {
                        "name": {},
                        "trend": {"spark": {"type": "columns"}},
                    }
                }
            )
        )
        data = [{"name": "a", "trend": [1, 2, 3, 4, 5]}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )
        # spark group should have 5 rects (one per array element)
        import re

        groups = re.findall(r'<g transform="translate[^"]+">.*?</g>', svg, re.DOTALL)
        spark_groups = [g for g in groups if g.count("<rect") >= 5]
        assert spark_groups, "no spark columns group rendered"
        assert spark_groups[0].count("<rect") == 5
