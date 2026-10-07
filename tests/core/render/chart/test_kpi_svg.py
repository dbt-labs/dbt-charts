"""KPI SVG regression guard: render path must stay byte-identical across calls.

Both sides resolve from the SAME authored chart dict via the real pipeline
(normalize -> resolve). Never hand-construct ResolvedKpiChart directly —
that would hide gaps in the KPI resolver.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.resolve import resolve


def _styles() -> tuple[Any, Any]:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    return resolve_style_and_context(get_theme_style(get_default_theme_name()))


def _oracle_svg(
    chart_def: dict[str, Any],
    query_registry: dict[str, Any],
    data: list[dict[str, Any]],
    board_rs: Any,
    board_ctx: Any,
) -> str:
    """Reference path: authored dict -> normalize_chart -> resolve -> render_kpi_svg."""
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.render.chart.kpi import render_kpi_svg

    flat = normalize_chart("chart1", chart_def, query_registry, sources={})
    resolved = resolve(flat, data, chart_style_context=board_ctx)
    return render_kpi_svg(resolved, data, board_style=board_rs)


def _v2_svg(
    chart_def: dict[str, Any],
    query_registry: dict[str, Any],
    data: list[dict[str, Any]],
    board_rs: Any,
    board_ctx: Any,
) -> str:
    """render path: authored dict -> normalize_chart -> resolve -> render_kpi_svg."""
    from dbt_charts.core.compile.models.chart.resolved.kpi import ResolvedKpiChart
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.kpi import render_kpi_svg

    compiled = normalize_chart("chart1", chart_def, query_registry, sources={})
    resolved = resolve(compiled, data, chart_style_context=board_ctx)
    assert isinstance(resolved, ResolvedKpiChart)
    return render_kpi_svg(resolved, data, board_style=board_rs)


_QUERY_REGISTRY = {"q": ValuesQuery(rows=[{"revenue": 128_000, "delta": 0.12}])}
_DATA = _QUERY_REGISTRY["q"].rows

_FIXTURES: dict[str, dict[str, Any]] = {
    "plain_value": {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
    },
    "value_label_support": {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "Revenue",
        "support": {
            "value": "delta",
            "label": "vs last month",
            "glyph": "▲",
        },
    },
    "tone_glyph": {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "Revenue",
        "glyph": "▲",
        "style": {"support": {"tone": "positive"}},
        "support": {
            "label": "vs last month",
        },
    },
    "conditional_formatting": {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "Revenue",
        "conditional_formatting": {
            "revenue": {
                "when": [
                    {"gt": 100_000, "font": {"color": "#00aa00"}},
                    {"lte": 100_000, "font": {"color": "#ff0000"}},
                ]
            }
        },
    },
}


def test_kpi_card_border_dash_array_emits_svg_dasharray() -> None:
    """style.border.dash_array on a KPI card reaches the card chrome's stroke."""
    chart_def: dict[str, Any] = {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "Revenue",
        "style": {
            "border": {
                "width": 1,
                "color": "#333333",
                "radius": 4,
                "dash_array": [4, 4],
                "line_cap": "round",
            },
        },
    }
    board_rs, board_ctx = _styles()
    svg = _v2_svg(chart_def, _QUERY_REGISTRY, _DATA, board_rs, board_ctx)
    assert 'stroke-dasharray="4,4"' in svg
    assert 'stroke-linecap="round"' in svg


@pytest.mark.parametrize("fixture_name", sorted(_FIXTURES))
def test_v2_kpi_svg_byte_identical(fixture_name: str) -> None:
    chart_def = _FIXTURES[fixture_name]
    board_rs, board_ctx = _styles()
    oracle = _oracle_svg(chart_def, _QUERY_REGISTRY, _DATA, board_rs, board_ctx)
    result = _v2_svg(chart_def, _QUERY_REGISTRY, _DATA, board_rs, board_ctx)
    assert oracle == result, (
        f"KPI SVG parity FAILED for fixture={fixture_name!r}\nORACLE=\n{oracle}\n\nV2=\n{result}"
    )


@pytest.mark.parametrize("variant", ["stacked", "inline", "compact"])
def test_v2_kpi_svg_byte_identical_across_variants(variant: str) -> None:
    chart_def = {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "Revenue",
        "style": {"variant": variant},
        "support": {
            "value": "delta",
            "label": "vs last month",
        },
    }
    board_rs, board_ctx = _styles()
    oracle = _oracle_svg(chart_def, _QUERY_REGISTRY, _DATA, board_rs, board_ctx)
    result = _v2_svg(chart_def, _QUERY_REGISTRY, _DATA, board_rs, board_ctx)
    assert oracle == result, (
        f"KPI SVG parity FAILED for variant={variant!r}\nORACLE=\n{oracle}\n\nV2=\n{result}"
    )


# Matches `23-kpi-variant-x-slots.yml`'s `one` query (series_ix=1, point_ix=0)
# exactly, so the reproduction below is the corpus specimen's own content.
_OVERFLOW_QUERY_REGISTRY = {"q": ValuesQuery(rows=[{"value": 84.33, "delta": -10.23}])}
_OVERFLOW_DATA = _OVERFLOW_QUERY_REGISTRY["q"].rows


def _render_inline_at_width(chart_def: dict[str, Any], width: float) -> str:
    """Render a KPI chart at an explicit width through the real pipeline."""
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.render.chart.kpi import render_kpi_svg

    board_rs, board_ctx = _styles()
    flat = normalize_chart("chart1", chart_def, _OVERFLOW_QUERY_REGISTRY, sources={})
    resolved = resolve(flat, _OVERFLOW_DATA, chart_style_context=board_ctx)
    return render_kpi_svg(resolved, _OVERFLOW_DATA, width=width, board_style=board_rs)


# All-three-slots content at a card width narrow enough that the assembled
# inline run (value + gap + label + gap + support) cannot fit — the shape of
# `23-kpi-variant-x-slots.yml#in_full`. Measured against the default theme's
# zero horizontal `content_padding`: the run is ~334px wide, so 315px overflows.
_OVERFLOW_CHART_DEF: dict[str, Any] = {
    "type": "kpi",
    "query": "q",
    "value": "value",
    "label": "Label, Value and Support",
    "style": {"variant": "inline"},
    "support": {
        "value": "delta",
        "label": "vs. prior period",
    },
}
_OVERFLOW_WIDTH = 315.0


def test_kpi_inline_all_three_slots_falls_back_to_stacked_when_run_overflows_card() -> (
    None
):
    """The inline run must not paint past the card when it does not fit.

    Recommended fix (option A): fall back to the stacked arrangement for this
    card rather than let the run paint out of bounds. The fallback must
    produce exactly what authoring `variant: stacked` with the same content
    and width would — same layout primitive, not a second one.
    """
    inline_svg = _render_inline_at_width(_OVERFLOW_CHART_DEF, _OVERFLOW_WIDTH)
    stacked_def = {**_OVERFLOW_CHART_DEF, "style": {"variant": "stacked"}}
    stacked_svg = _render_inline_at_width(stacked_def, _OVERFLOW_WIDTH)
    assert inline_svg == stacked_svg, (
        f"expected the overflowing inline run to fall back to stacked geometry\n"
        f"INLINE=\n{inline_svg}\n\nSTACKED=\n{stacked_svg}"
    )
    # The stacked fallback never emits a `dx`-advanced run on one baseline.
    assert "dx=" not in inline_svg


@pytest.mark.parametrize(
    "chart_def",
    [
        pytest.param(
            {**_OVERFLOW_CHART_DEF, "support": None}, id="label_and_value_only"
        ),
        pytest.param(
            {**_OVERFLOW_CHART_DEF, "label": None}, id="support_and_value_only"
        ),
        pytest.param(
            {**_OVERFLOW_CHART_DEF, "label": None, "support": None}, id="bare_value"
        ),
    ],
)
def test_kpi_inline_narrower_slot_combinations_keep_todays_geometry(
    chart_def: dict[str, Any],
) -> None:
    """Only the run that genuinely overflows may change.

    Two-slot and one-slot inline content at the same card width that
    overflows with all three slots must stay unaffected — they fit today and
    must keep rendering inline, not fall back.

    Discriminates on identity against the same content authored as
    `variant: stacked`, not on `<text>` count or the input width: for the
    bare-value case, stacked also emits exactly one `<text>` (no label, no
    support block), so a `<text>` count can't tell "stayed inline" from
    "fell back" — and the fallback IS defined as producing byte-identical
    output to an equivalent stacked-authored chart (pinned by the sibling
    overflow test above), so equality is the direct way to ask the question.
    """
    inline_svg = _render_inline_at_width(chart_def, _OVERFLOW_WIDTH)
    stacked_def = {**chart_def, "style": {"variant": "stacked"}}
    stacked_svg = _render_inline_at_width(stacked_def, _OVERFLOW_WIDTH)
    assert inline_svg != stacked_svg, (
        f"expected this content to still fit inline at {_OVERFLOW_WIDTH}px, not "
        f"fall back to stacked geometry\nINLINE=\n{inline_svg}\n\nSTACKED=\n{stacked_svg}"
    )


def test_kpi_inline_all_three_slots_keeps_todays_geometry_when_it_fits() -> None:
    """A wide-enough card keeps the inline run on one baseline, unfallback."""
    svg = _render_inline_at_width(_OVERFLOW_CHART_DEF, 600.0)
    assert svg.count("<text") == 1
    assert "dx=" in svg


# A bare (no label, no support) inline KPI whose value alone is long enough
# to overflow a narrow card. Stacking has nothing to move to another line —
# `_emit_kpi_stacked` paints the identical untruncated `number_str` at the
# same x and font size — so falling back would only grow the card and shift
# the baseline off the row's shared line while leaving the same overflow in
# place. The predicate must exclude this case rather than "fix" it that way.
_BARE_VALUE_OVERFLOW_QUERY_REGISTRY = {
    "q": ValuesQuery(rows=[{"headline": "This overflows a narrow card by itself"}])
}
_BARE_VALUE_OVERFLOW_DATA = _BARE_VALUE_OVERFLOW_QUERY_REGISTRY["q"].rows
_BARE_VALUE_OVERFLOW_CHART_DEF: dict[str, Any] = {
    "type": "kpi",
    "query": "q",
    "value": "headline",
    "style": {"variant": "inline"},
}
_BARE_VALUE_OVERFLOW_WIDTH = 200.0


def _render_bare_value_at_width(chart_def: dict[str, Any], width: float) -> str:
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.render.chart.kpi import render_kpi_svg

    board_rs, board_ctx = _styles()
    flat = normalize_chart(
        "chart1", chart_def, _BARE_VALUE_OVERFLOW_QUERY_REGISTRY, sources={}
    )
    resolved = resolve(flat, _BARE_VALUE_OVERFLOW_DATA, chart_style_context=board_ctx)
    return render_kpi_svg(
        resolved, _BARE_VALUE_OVERFLOW_DATA, width=width, board_style=board_rs
    )


def test_kpi_inline_bare_value_overflow_does_not_fall_back() -> None:
    """Stacking can't help a bare-value overflow, so the predicate skips it.

    Proven the same way as the "stayed inline" tests above: a real fallback
    is byte-identical to the equivalent stacked-authored chart, so comparing
    against one shows whether the predicate (wrongly) took it.
    """
    inline_svg = _render_bare_value_at_width(
        _BARE_VALUE_OVERFLOW_CHART_DEF, _BARE_VALUE_OVERFLOW_WIDTH
    )
    stacked_def = {**_BARE_VALUE_OVERFLOW_CHART_DEF, "style": {"variant": "stacked"}}
    stacked_svg = _render_bare_value_at_width(stacked_def, _BARE_VALUE_OVERFLOW_WIDTH)
    assert inline_svg != stacked_svg, (
        "a bare-value overflow must not fall back to stacked — stacking cannot "
        f"help it\nINLINE=\n{inline_svg}\n\nSTACKED=\n{stacked_svg}"
    )
    # Still the inline shape: exactly one <text>, height untouched by a
    # fallback that would otherwise grow the card to fit a taller layout.
    assert inline_svg.count("<text") == 1
