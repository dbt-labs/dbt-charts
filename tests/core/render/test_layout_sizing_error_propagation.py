"""Regression: chart-sizing error suppression is narrowed to migrate-or-fail signals.

PR #1753 narrowed three broad except clauses in ``layout_sizing.py``. Pre-fix
each site caught a bug-class exception tuple (some combination of ``KeyError``,
``ValueError``, ``OSError``, ``RuntimeError``, ``LookupError``) and silently
logged a warning, falling back to an aspect-ratio estimate. The chart would
then render at a wrong height OR invisibly empty — masking the underlying bug.

Post-fix, only migrate-or-fail signals are suppressed:

- ``_get_table_height_from_data``: ``ExecutionError`` + ``ChartDataError``
- ``_make_data_aware_height_provider``: ``ExecutionError`` + ``ChartDataError``
- ``_align_cols_heights``: ``ChartDataError`` only

(The table-height site gained ``ChartDataError`` once pivot totals routed its
reshape through ``pivot_table_data``, which raises ``ChartDataError`` on a bad
pivot/data contract; render draws the per-chart error card for that same
signal, so the sizer must not crash the whole board's layout ahead of it.)

Bug-class exceptions now propagate. The ``ChartDataError`` catch in
``dbt_charts.core.render.chart.rendering`` (the per-tile error-block branch)
remains the migrate-or-fail surface for vega-lite-rejected specs.

These tests document the post-fix invariant. Several subtests catch pre-fix
regressions (e.g. ``KeyError``, ``ValueError``, ``OSError``, ``LookupError``);
others (e.g. ``RuntimeError`` for the sizing-provider and align-cols sites)
were already uncaught pre-fix and serve as documenting / drift-guard tests
to keep the post-fix invariant pinned across all bug-class types.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.executor import Executor

_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)


def _make_chart(chart_type: str = "table") -> Chart:
    """Fresh Chart per call — `Chart` is mutable so don't
    share state across tests."""
    return TypeAdapter(Chart).validate_python(
        {
            "id": "t1",
            "query": SqlQuery(sql="SELECT 1", source="test"),
            "query_name": "q",
            "type": chart_type,
            "style": None,
        }
    )


def _executor_raising(exc: Exception) -> MagicMock:
    mock = MagicMock(spec=Executor)
    mock.execute_chart.side_effect = exc
    return mock


# ---------------------------------------------------------------------------
# _get_table_height_from_data: every failure sizes the error card
# ---------------------------------------------------------------------------


class TestTableHeightErrorPropagation:
    """Sizing runs the table renderer; the main pass draws any failure there as
    the chart's error card, so sizing reserves that card instead of aborting the
    board."""

    def _call(self, exc: Exception) -> float:
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.layout_sizing import _get_table_height_from_data

        chart = _make_chart("table")
        theme = get_theme_style()
        board_style = resolve_style(theme)
        board_ctx = resolve_chart_style_context(theme)
        resolved = resolve(chart, [], board_ctx)
        return _get_table_height_from_data(
            chart,
            resolved,
            _executor_raising(exc),
            variables={},
            card_padding=float(board_style.frame.card_padding),
            board_style=board_style,
        )

    @pytest.mark.parametrize(
        "exc",
        [
            ExecutionError("query failed"),
            ChartDataError("pivot.column 'x' not in data rows"),
            RuntimeError("bug"),
            ValueError("bug"),
            KeyError("bug"),
            OSError("bug"),
        ],
    )
    def test_failure_sizes_the_error_card(self, exc: Exception) -> None:
        assert self._call(exc) > 0


# ---------------------------------------------------------------------------
# _make_data_aware_height_provider: ExecutionError + ChartDataError suppressed
# ---------------------------------------------------------------------------


class TestSizingProviderErrorPropagation:
    """Render-first sizing path in ``_make_data_aware_height_provider`` must
    suppress only the two migrate-or-fail signals."""

    def _call_chart_provider(self, exc: Exception) -> float:
        """Build a non-table chart sizing provider and inject *exc* at the
        ``_render_chart_to_svg`` call. Returns height (if suppressed) or raises."""
        import importlib
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.compile.models.board.normalized import LayoutItem
        from dbt_charts.core.compile.models.chart.resolved import ResolvedChart

        chart = _make_chart("bar")
        item = LayoutItem(type="chart", width=400.0, height=0.0, chart=chart)
        executor = MagicMock(spec=Executor)

        mock_resolved = MagicMock(spec=ResolvedChart)
        mock_resolved.id = chart.id
        mock_resolved.layout_padding = _ZERO_PADDING

        theme = get_theme_style()
        resolved = resolve_style(theme)
        board_ctx = resolve_chart_style_context(theme)
        layout_sizing_mod = importlib.import_module(
            "dbt_charts.core.render.layout_sizing"
        )
        with patch.object(layout_sizing_mod, "_render_chart_to_svg", side_effect=exc):
            from dbt_charts.core.render.layout_sizing import (
                SizingRenderCtx,
                _make_data_aware_height_provider,
            )

            render_ctx = SizingRenderCtx(
                executor=executor,
                resolved_style=resolved,
                chart_style_context=board_ctx,
                pre_resolved={chart.id: mock_resolved},
            )
            provider = _make_data_aware_height_provider(
                render_ctx,
                executor,
                variables={},
                card_padding=16.0,
                style_contexts={id(resolved): board_ctx},
            )
            return provider(item, 0.0, 20.0, 400.0, None, resolved)

    def test_execution_error_is_suppressed(self) -> None:
        height = self._call_chart_provider(ExecutionError("query failed"))
        assert height > 0

    def test_chart_data_error_is_suppressed(self) -> None:
        height = self._call_chart_provider(ChartDataError("vega rejected spec"))
        assert height > 0

    def test_runtime_error_propagates(self) -> None:
        with pytest.raises(RuntimeError, match="bug"):
            self._call_chart_provider(RuntimeError("bug"))

    def test_value_error_propagates(self) -> None:
        with pytest.raises(ValueError, match="bug"):
            self._call_chart_provider(ValueError("bug"))

    def test_key_error_propagates(self) -> None:
        with pytest.raises(KeyError):
            self._call_chart_provider(KeyError("bug"))

    def test_os_error_propagates(self) -> None:
        with pytest.raises(OSError, match="bug"):
            self._call_chart_provider(OSError("bug"))

    def _call_chart_provider_resolve_raises(self, exc: Exception) -> float:
        """Like _call_chart_provider, but injects *exc* at resolve time itself
        (``resolve_chart_with_runtime_inputs``, inside ``_require_resolved``)
        rather than at the render call — no ``pre_resolved`` shortcut, so the
        provider must actually attempt resolution."""
        import importlib
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.compile.models.board.normalized import LayoutItem

        chart = _make_chart("bar")
        item = LayoutItem(type="chart", width=400.0, height=0.0, chart=chart)
        executor = MagicMock(spec=Executor)
        executor.execute_query.return_value = []

        theme = get_theme_style()
        resolved = resolve_style(theme)
        board_ctx = resolve_chart_style_context(theme)
        layout_sizing_mod = importlib.import_module(
            "dbt_charts.core.render.layout_sizing"
        )
        with patch.object(
            layout_sizing_mod, "resolve_chart_with_runtime_inputs", side_effect=exc
        ):
            from dbt_charts.core.render.layout_sizing import (
                SizingRenderCtx,
                _make_data_aware_height_provider,
            )

            render_ctx = SizingRenderCtx(
                executor=executor,
                resolved_style=resolved,
                chart_style_context=board_ctx,
            )
            provider = _make_data_aware_height_provider(
                render_ctx,
                executor,
                variables={},
                card_padding=16.0,
                style_contexts={id(resolved): board_ctx},
            )
            return provider(item, 0.0, 20.0, 400.0, None, resolved)

    def test_resolve_stage_chart_data_error_is_suppressed(self) -> None:
        """A ChartDataError raised during resolve() itself (not just render)
        must degrade to the aspect-ratio estimate, not crash the whole board.

        Regression: hoisting _require_resolved() out of the render call's
        try/except (to read layout_padding off the resolved chart before
        computing static_estimate) must not narrow the guarded region —
        resolve-stage failures need the same fallback as render-stage ones.
        """
        height = self._call_chart_provider_resolve_raises(
            ChartDataError("bad multiples/mirror combo")
        )
        assert height > 0

    def test_resolve_stage_runtime_error_propagates(self) -> None:
        with pytest.raises(RuntimeError, match="bug"):
            self._call_chart_provider_resolve_raises(RuntimeError("bug"))


# ---------------------------------------------------------------------------
# _align_cols_heights: only ChartDataError is suppressed
# ---------------------------------------------------------------------------


class TestAlignColsErrorPropagation:
    """``_align_cols_heights`` re-renders shorter Vega cols items at target
    height. The narrowed except now only suppresses ``ChartDataError``."""

    def _call_align(self, exc: Exception) -> None:
        """Build two cols items, mark one as the tallest in render_cache, and
        inject *exc* when the re-render fires for the shorter one. Returns
        normally if exc is suppressed; raises otherwise."""
        import importlib
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.compile.models.board.normalized import LayoutItem
        from dbt_charts.core.compile.models.chart.resolved import ResolvedChart

        chart_short = _make_chart("bar")
        chart_short.id = "short"
        chart_tall = _make_chart("bar")
        chart_tall.id = "tall"

        item_short = LayoutItem(
            type="chart", width=400.0, height=0.0, chart=chart_short
        )
        item_tall = LayoutItem(type="chart", width=400.0, height=0.0, chart=chart_tall)

        executor = MagicMock(spec=Executor)
        theme = get_theme_style()
        resolved = resolve_style(theme)
        board_ctx = resolve_chart_style_context(theme)

        mock_short = MagicMock(spec=ResolvedChart)
        mock_short.id = "short"
        mock_short.layout_padding = _ZERO_PADDING
        mock_tall = MagicMock(spec=ResolvedChart)
        mock_tall.id = "tall"
        mock_tall.layout_padding = _ZERO_PADDING

        layout_sizing_mod = importlib.import_module(
            "dbt_charts.core.render.layout_sizing"
        )
        with patch.object(layout_sizing_mod, "_render_chart_to_svg", side_effect=exc):
            from dbt_charts.core.render.layout_sizing import (
                SizingRenderCtx,
                _align_cols_heights,
            )

            render_ctx = SizingRenderCtx(
                executor=executor,
                resolved_style=resolved,
                chart_style_context=board_ctx,
                render_cache={
                    ("short", 400.0, 100.0): ("<svg/>", 100.0),
                    ("tall", 400.0, 300.0): ("<svg/>", 300.0),
                },
                natural_heights={
                    ("short", 400.0): 100.0,
                    ("tall", 400.0): 300.0,
                },
                pre_resolved={"short": mock_short, "tall": mock_tall},
            )
            _align_cols_heights([item_short, item_tall], 300.0, render_ctx)

    def test_chart_data_error_is_suppressed(self) -> None:
        """ChartDataError → cols re-render skipped, original height kept."""
        # Should return without raising.
        self._call_align(ChartDataError("vega rejected spec"))

    def test_runtime_error_propagates(self) -> None:
        with pytest.raises(RuntimeError, match="bug"):
            self._call_align(RuntimeError("bug"))

    def test_value_error_propagates(self) -> None:
        with pytest.raises(ValueError, match="bug"):
            self._call_align(ValueError("bug"))

    def test_key_error_propagates(self) -> None:
        with pytest.raises(KeyError):
            self._call_align(KeyError("bug"))

    def test_os_error_propagates(self) -> None:
        with pytest.raises(OSError, match="bug"):
            self._call_align(OSError("bug"))
