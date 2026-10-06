"""`style.text.divider` owns markdown gridline/rule styling; the table's border
override is gone.

``TableChartStyle`` declared its own ``border: BorderStyle`` ("Table outer
border style"), *shadowing* the chart-card border slot every other family
inherits from ``_ChartStyleBase``. The override was required-but-inert: the
table renderer read it into a ``colors["border"]`` key that was written twice
and never read, and the table's visible lines come from ``header.divider`` /
``row.divider`` instead. Its ``color`` escaped only into mdsvg as
``table_border_color`` / ``hr_color`` — the gridlines of *markdown* tables and
``<hr>``, never the table chart.

The override is removed (table now inherits the shared card slot like every
other family) and the markdown coupling is filed under the surface it paints.

TDD tests — written BEFORE the change.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_theme_style, list_built_in_themes
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)


def _board(style: dict[str, Any]) -> dict[str, Any]:
    return {
        "charts": {"c1": {"type": "bar", "query": "q1", "x": "x", "y": "x"}},
        "queries": {"q1": {"sql": "SELECT 1 AS x", "source": "test"}},
        "style": style,
    }


class TestTextRuleIsAuthorable:
    def test_text_divider_color_is_authorable(self) -> None:
        AuthoredBoard.model_validate(
            _board({"text": {"divider": {"color": "#ff0000"}}})
        )

    def test_every_theme_supplies_a_concrete_rule_color(self) -> None:
        """mdsvg needs a real color, so no theme may leave the slot unfilled.

        Enumerated from the theme directory rather than hand-listed, so a new
        theme is covered the day it lands.
        """
        # `_base` is the base layer, not a standalone loadable theme.
        for theme in (t for t in list_built_in_themes() if t != "_base"):
            color = resolve_style(get_theme_style(theme)).text.divider.color
            assert color and color.startswith("#"), (theme, color)


class TestTableBorderNoLongerDiverges:
    """The table family inherits the shared card-border slot, like its peers.

    Pins the *shape* of the fix rather than the absence of a symbol: before,
    `charts.table.border` was a required table-only override that resolved to a
    theme-supplied value while its siblings resolved to the inherited card
    border. Now table and geoshape agree.
    """

    def test_table_border_no_longer_diverges_from_a_sibling_family(self) -> None:
        """Table and geoshape agree on the card slot: both unauthored, both
        `None`.

        The sibling is geoshape rather than bar because bar no longer carries
        the slot at all — the Vega-Lite families have no per-chart card, so
        `style.border` is structurally absent on them. geoshape is the nearest
        family that still inherits the shared card border unauthored.

        Deliberately narrow. It bites the regression it names — re-adding a
        required, theme-filled `border` to `TableChartStyle` makes table
        non-`None` while geoshape stays `None` — but it does NOT prove the
        cascade wires `charts.border` through to either family. It cannot: that
        slot is inert for every family today, which is the separate defect this
        task's Plan defers. Do not read this as a card-border behavior test.
        """
        csc = resolve_chart_style_context(get_theme_style())
        assert csc.table.border == csc.geoshape.border
