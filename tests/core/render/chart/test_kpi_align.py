"""KPI `style.align` — right/center shift the card's text runs via a computed
`x`, never `text-anchor="end"`/`"middle"` or a `dy` chain (see
`tasks/workstreams/dft-core/tasks/kpi-cannot-align-its-text-text-anchor-is-hardcoded-start-at-six-sites.md`
for why: cairosvg mis-renders `dy` chains, and every value/label/support
`<tspan>` in this file carries its own absolute `y`, which SVG 1.1 section
10.5 treats as opening a new text chunk — an `end`/`middle` anchor would
re-anchor each chunk independently and overlap the run).

`kpi_align_baseline.json` pins the pre-`align` byte-for-byte output (captured
from `origin/main` before this field existed) so omitting `align` must never
change a single byte.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.resolve import resolve

_BASELINE = json.loads(
    (Path(__file__).parent / "fixtures" / "kpi_align_baseline.json").read_text(
        encoding="utf-8"
    )
)

_QUERY_REGISTRY = {
    "q": ValuesQuery(rows=[{"revenue": 128_000, "delta": 0.12}]),
}
_DATA = _QUERY_REGISTRY["q"].rows


def _styles() -> tuple[Any, Any]:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    return resolve_style_and_context(get_theme_style(get_default_theme_name()))


def _render(chart_def: dict[str, Any]) -> str:
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.render.chart.kpi import render_kpi_svg

    board_rs, board_ctx = _styles()
    flat = normalize_chart("chart1", chart_def, _QUERY_REGISTRY, sources={})
    resolved = resolve(flat, _DATA, chart_style_context=board_ctx)
    return render_kpi_svg(resolved, _DATA, board_style=board_rs)


def _chart_def(variant: str, align: str | None = None) -> dict[str, Any]:
    chart_def: dict[str, Any] = {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "Revenue",
        "variant": variant,
        "support": {"value": "delta", "label": "vs last month", "glyph": "▲"},
    }
    if align is not None:
        chart_def["style"] = {"align": align}
    return chart_def


def _text_x_values(svg: str) -> list[float]:
    """`x` of every positioned `<text>`/`<tspan>`, in document order.

    Includes `tspan` because multi-line labels carry their own absolute `x`
    per line (`kpi.py` label-line emission) — a `text`-only pattern is blind
    to them, so a mis-placed second label line would assert nothing.
    """
    return [float(m) for m in re.findall(r'<(?:text|tspan) x="([^"]+)"', svg)]


@pytest.mark.parametrize("variant", ["stacked", "inline", "compact"])
def test_kpi_align_unauthored_is_byte_identical_to_baseline(variant: str) -> None:
    assert _render(_chart_def(variant)) == _BASELINE[variant]


@pytest.mark.parametrize("variant", ["stacked", "inline", "compact"])
def test_kpi_align_right_shifts_every_text_run_rightward(variant: str) -> None:
    default_svg = _render(_chart_def(variant))
    right_svg = _render(_chart_def(variant, align="right"))
    assert right_svg != default_svg

    default_xs = _text_x_values(default_svg)
    right_xs = _text_x_values(right_svg)
    assert len(default_xs) == len(right_xs)
    # value, label, and support (compact: value + right column) all move —
    # a single chart-level field, not an independently-authorable per-slot one.
    for default_x, right_x in zip(default_xs, right_xs, strict=True):
        assert right_x > default_x, (
            f"expected every <text> x to shift right under align=right "
            f"(variant={variant!r}); default={default_xs} right={right_xs}"
        )
    # anchor stays "start" — the run is repositioned by x, never re-anchored.
    assert 'text-anchor="end"' not in right_svg
    assert 'text-anchor="middle"' not in right_svg
    assert "dy=" not in right_svg


@pytest.mark.parametrize("variant", ["stacked", "inline", "compact"])
def test_kpi_align_center_shifts_less_than_right(variant: str) -> None:
    default_svg = _render(_chart_def(variant))
    center_svg = _render(_chart_def(variant, align="center"))
    right_svg = _render(_chart_def(variant, align="right"))
    assert center_svg != default_svg
    assert center_svg != right_svg

    default_xs = _text_x_values(default_svg)
    center_xs = _text_x_values(center_svg)
    right_xs = _text_x_values(right_svg)
    for default_x, center_x, right_x in zip(
        default_xs, center_xs, right_xs, strict=True
    ):
        assert default_x < center_x < right_x, (
            f"expected default < center < right (variant={variant!r}): "
            f"{default_x} < {center_x} < {right_x}"
        )
    assert "dy=" not in center_svg


def test_kpi_align_compact_right_column_moves_with_the_value_column() -> None:
    """Compact's value + right column is one rigid block under `align`.

    The right column's `x` is derived from the value run's own measured
    width (`right_column_x = content_x + value_run_width + gap`) — if
    `align` moved the value column without moving the right column by the
    same delta, the two columns would separate.
    """
    default_svg = _render(_chart_def("compact"))
    right_svg = _render(_chart_def("compact", align="right"))
    default_xs = _text_x_values(default_svg)
    right_xs = _text_x_values(right_svg)
    assert len(default_xs) == 3  # value text + top-payload text + bottom-payload text
    default_gap = default_xs[1] - default_xs[0]
    right_gap = right_xs[1] - right_xs[0]
    assert default_gap == pytest.approx(right_gap)


@pytest.mark.parametrize("variant", ["stacked", "inline", "compact"])
@pytest.mark.parametrize("align", ["right", "center"])
@pytest.mark.parametrize("card_width", [250.0, 120.0])
def test_align_never_places_a_run_left_of_the_content_box(
    variant: str, align: str, card_width: float
) -> None:
    """An overflowing run must not be pushed to a negative x.

    The emitted ``<svg viewBox="0 0 W H">`` is a nested viewport that clips at
    ``x = 0``, so a negative ``x`` destroys the run's LEADING characters — a
    right-aligned ``1,234,567,890`` renders as a well-formed, wrong
    ``234,567,890``. Ordering-only assertions (``right_x > default_x``) cannot
    catch that: they hold for any monotonic mis-measurement, including one that
    goes negative. This pins the edge instead.
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.render.chart.kpi import render_kpi_svg

    rows = [{"revenue": 1_234_567_890, "delta": 0.12}]
    registry = {"q": ValuesQuery(rows=rows)}
    chart_def = _chart_def(variant, align=align)
    chart_def["style"]["value"] = {"format": ",.0f"}

    board_rs, board_ctx = _styles()
    flat = normalize_chart("chart1", chart_def, registry, sources={})
    resolved = resolve(flat, rows, chart_style_context=board_ctx)
    svg = render_kpi_svg(resolved, rows, card_width, board_style=board_rs)

    xs = _text_x_values(svg)
    assert xs, "expected at least one positioned text run"
    assert min(xs) >= 0.0, (
        f"{variant}/{align} @{card_width}px: run placed at x={min(xs)} — the "
        "viewBox clips at "
        "x=0, so the headline value's leading digits would be destroyed"
    )


@pytest.mark.parametrize("align", ["right", "center"])
def test_inline_bare_value_overflow_is_clamped(align: str) -> None:
    """The inline clamp is only reachable from a bare-value inline KPI.

    ``_emit_kpi_inline`` hands off to ``_emit_kpi_stacked`` whenever the run
    overflows AND ``can_fall_back`` (``label_text or support_row is not None``).
    Every other test here authors both, so their ``variant="inline"`` cases are
    redirected to the stacked emitter and are byte-duplicates of the stacked
    case — the inline floor is never executed. A KPI with neither label nor
    support cannot fall back, so it is the one shape that reaches it.
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.render.chart.kpi import render_kpi_svg

    rows = [{"revenue": 1_234_567_890, "delta": 0.12}]
    registry = {"q": ValuesQuery(rows=rows)}
    chart_def: dict[str, Any] = {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "variant": "inline",
        "style": {"align": align, "value": {"format": ",.0f"}},
    }

    board_rs, board_ctx = _styles()
    flat = normalize_chart("chart1", chart_def, registry, sources={})
    resolved = resolve(flat, rows, chart_style_context=board_ctx)
    svg = render_kpi_svg(resolved, rows, 120.0, board_style=board_rs)

    xs = _text_x_values(svg)
    assert xs, "expected at least one positioned text run"
    assert min(xs) >= 0.0, (
        f"inline/{align}: bare-value run placed at x={min(xs)} — the inline "
        "clamp did not fire, so the leading digits would be clipped"
    )


@pytest.mark.parametrize("align", ["right", "center"])
def test_align_is_all_or_nothing_across_the_card(align: str) -> None:
    """One overflowing run drops align for the whole card, not just that run.

    Clamping per-run right-aligns the runs that fit and snaps the overflowing
    one back to the content edge, so the card renders with two different
    alignments — neither what was authored nor the pre-``align`` geometry. The
    shape that exposes it is a SHORT value with a LONG support explainer: the
    value and label fit and shift, the support run does not.

    ``KpiChartStyle.align``'s own contract is that alignment is one chart-level
    choice, so the card must be internally consistent either way.
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.render.chart.kpi import render_kpi_svg

    rows = [{"revenue": 7, "delta": 0.12}]
    registry = {"q": ValuesQuery(rows=rows)}
    chart_def: dict[str, Any] = {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "Rev",
        "variant": "stacked",
        "style": {"align": align},
        "support": {
            "value": "delta",
            "label": "versus the same period a year earlier, adjusted",
            "format": "percent_delta",
        },
    }

    board_rs, board_ctx = _styles()
    flat = normalize_chart("chart1", chart_def, registry, sources={})
    resolved = resolve(flat, rows, chart_style_context=board_ctx)
    svg = render_kpi_svg(resolved, rows, 200.0, board_style=board_rs)

    xs = _text_x_values(svg)
    assert xs, "expected positioned text runs"
    assert len({round(x, 3) for x in xs}) == 1 or min(xs) > 0.0, (
        f"{align}: runs at {sorted({round(x, 1) for x in xs})} — the card "
        "mixes an aligned run with one snapped back to the content edge"
    )
