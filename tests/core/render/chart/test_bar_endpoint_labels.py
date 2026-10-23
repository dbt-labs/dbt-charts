"""TDD tests for the endpoint-labels primitive on the bar family.

Mirrors the line/area implementation: a multi-series column chart with
``style.bar.endpoint_labels.visible: true`` emits an ``hconcat`` with the
chart pane on the left and a series-label pane on the right. Anchor
computation is per-family:

- Stacked column (stack mode None / "zero"): segment midpoint of each
  series in the rightmost x column.
- 100% stacked (stack mode "normalize"): segment midpoint expressed on
  the 0..1 normalized scale.

Tests use synthetic data so the expected anchor y values are computable
by hand without pinning any theme literals.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


@pytest.fixture
def seed_with_bar_endpoint_labels(model_copy_at):
    """Resolved style with bar.endpoint_labels override applied.

    Seeds from `stark` rather than the configured default so the legend tests
    in this file observe the cascade-default legend dict (the shipped editorial
    `default` theme sets legend.disable=true, which would mask the legend the
    no-wrap regression test is checking for).
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    def _build(enabled: bool, label_offset: float = 5.0):
        seed = model_copy_at(
            get_theme_style("stark"),
            "charts.bar.endpoint_labels",
            EndpointLabelsConfig(
                visible="auto" if enabled else False,
                label_offset=label_offset,
                height=20.0,
            ),
        )
        return resolve_chart_style_context(seed)

    return _build


@pytest.fixture
def resolve_bar_chart(make_chart, seed_with_bar_endpoint_labels):
    """Resolved bar chart with endpoint_labels enabled and optional stack."""

    def _build(
        data: list[dict[str, Any]],
        enabled: bool,
        color: str | None = "series",
        stack: Any = "zero",
        author_asked: bool = False,
        orientation: str | None = None,
    ):
        kwargs: dict[str, Any] = {}
        if color is not None:
            kwargs["color"] = color
        if stack is not None:
            kwargs["stack"] = stack
        style: dict[str, Any] = {}
        if orientation is not None:
            style["orientation"] = orientation
        if author_asked:
            # The chart's own patch, not the board seed: only this outranks the
            # shape-based disqualifiers in _bar_endpoint_labels_for_stack.
            style["endpoint_labels"] = {"visible": True}
        if style:
            kwargs["style"] = style
        chart = make_chart("bar", x="date", y="value", **kwargs)
        board_style = seed_with_bar_endpoint_labels(enabled)
        rc = resolve(chart, data, chart_style_context=board_style)
        return rc

    return _build


def _render(rc, data: list[dict[str, Any]]) -> dict[str, Any]:
    artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
    assert artifact.kind == "vega_spec"
    return artifact.payload


# ---------------------------------------------------------------------------
# Schema cascade
# ---------------------------------------------------------------------------


def test_all_themes_compile_with_bar_endpoint_labels_field(compiled_themes):
    """Every production theme must compile and resolve with bar.endpoint_labels populated."""
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    for _, style in compiled_themes.items():
        ctx = resolve_chart_style_context(style)
        cfg = ctx.bar.endpoint_labels
        assert cfg is not None
        # Field is present and typed; we don't pin theme literal values.
        assert cfg.visible in (True, False, "auto")
        assert isinstance(cfg.label_offset, float)


# ---------------------------------------------------------------------------
# Wrap gate
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Anchor computation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Label-pane structure (mirrors the line/area shape)
# ---------------------------------------------------------------------------


def test_negative_values_raise_registered_code_not_err_internal(resolve_bar_chart):
    """The defense-in-depth raise for a direct, non-default construction
    (endpoint_labels forced back on after resolve's auto-disable) must carry
    a registered code, not the ERR-INTERNAL fallback, and its fix must name
    the field's full authored path (`style.endpoint_labels.visible`), not
    the bare `endpoint_labels.visible` that doesn't exist at the chart root.
    """
    from dbt_charts.core.diagnostics.codes_render import (
        ERR_ENDPOINT_LABELS_NEGATIVE_STACK,
    )

    data = [
        {"date": "2024-01-01", "value": 10, "series": "A"},
        {"date": "2024-01-01", "value": -20, "series": "B"},
    ]
    rc = resolve_bar_chart(data=data, enabled=True, stack="zero")
    rc = rc.model_copy(
        update={
            "style": rc.style.model_copy(
                update={
                    "endpoint_labels": rc.style.endpoint_labels.model_copy(
                        update={"visible": True}
                    )
                }
            )
        }
    )
    with pytest.raises(ChartDataError) as exc_info:
        _render(rc, data)
    err = exc_info.value
    assert err.code is ERR_ENDPOINT_LABELS_NEGATIVE_STACK
    assert "style.endpoint_labels.visible" in str(err)


# ---------------------------------------------------------------------------
# Wrap-time legend suppression and normalize y-domain pin
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Trailing nulls anchor at each series' own last real value
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# normalize/center use each series' own anchor column's total, not the
# trailing column's
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Wide-measure stack order must match the emitter's own baseline_order
# ---------------------------------------------------------------------------
