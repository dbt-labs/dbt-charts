"""The core seam to the Rust render engine: payload, errors and warning sinks.

Every test is a real `dbt_charts._engine` call over a chart resolved through
the real pipeline.
"""

from __future__ import annotations

import dataclasses
import datetime
import html
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from dbt_charts import _engine
from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.image_assets import image_scope
from dbt_charts.core.compile.models.board.normalized import (
    NO_TITLE_SHIFT,
    TitleShift,
)
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.primitives import Regional, RegionalLocale
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.table_overflow import (
    TableCramping,
    TableOverflow,
    collect_table_crampings,
    collect_table_overflows,
)
from dbt_charts.core.render.chart.table_page_squeeze import (
    TablePageSqueeze,
    collect_table_page_squeezes,
)
from dbt_charts.core.render.chart.table_static_pagination import (
    StaticPagination,
    collect_static_paginations,
)
from dbt_charts.core.render.chart.text_truncation import (
    TextTruncation,
    collect_text_truncations,
)
from dbt_charts.core.render.engine import (
    build_chart_payload,
    dumps_payload,
    render_native,
)
from dbt_charts.core.text.format_d3 import regional_scope

from ...conftest import InMemoryProject
from .._svg_render import render_board_to_svg

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(
    get_theme_style(get_default_theme_name())
)
_BOARD_WIDTH = 900.0


def _resolved(chart_def: dict[str, Any], data: list[dict[str, Any]]) -> ResolvedChart:
    registry = {"q": ValuesQuery(rows=data)}
    flat = normalize_chart("c1", chart_def, registry, sources={})
    return resolve(flat, data, chart_style_context=_BOARD_CTX)


@dataclass
class Sinks:
    truncations: dict[str, list[TextTruncation]]
    overflows: dict[str, TableOverflow]
    crampings: dict[str, TableCramping]
    squeezes: dict[str, TablePageSqueeze]
    paginations: dict[str, StaticPagination]


@contextmanager
def _sinks() -> Generator[Sinks]:
    with ExitStack() as stack:
        yield Sinks(
            stack.enter_context(collect_text_truncations()),
            stack.enter_context(collect_table_overflows()),
            stack.enter_context(collect_table_crampings()),
            stack.enter_context(collect_table_page_squeezes()),
            stack.enter_context(collect_static_paginations()),
        )


def _native(
    chart_def: dict[str, Any],
    data: list[dict[str, Any]],
    *,
    width: float = 300.0,
    height: float = 150.0,
    render_data: list[dict[str, Any]] | None = None,
    variables: dict[str, Any] | None = None,
) -> tuple[str, float | None]:
    chart = _resolved(chart_def, data)
    return render_native(
        chart,
        data if render_data is None else render_data,
        width=width,
        height=height,
        padding=None,
        board_style=_BOARD_RS,
        board_width=_BOARD_WIDTH,
        variables=variables,
    )


_KPI = {"type": "kpi", "query": "q", "value": "revenue"}
_REVENUE = [{"revenue": 128_000}]


def test_payload_carries_board_width_variables_and_datasets() -> None:
    chart = _resolved(_KPI, _REVENUE)
    payload = build_chart_payload(
        chart,
        {chart.query_name: _REVENUE},
        width=300.0,
        height=150.0,
        padding=None,
        board_style=_BOARD_RS,
        board_width=_BOARD_WIDTH,
        variables={"c1_page": 2},
        title_shift=NO_TITLE_SHIFT,
        is_placeholder=False,
    )
    assert payload["title_shift"] == {"title_dy": 0.0, "body_dy": 0.0}
    assert payload["board_style"]["board_width"] == _BOARD_WIDTH
    assert payload["variables"] == {"c1_page": 2}
    assert payload["rows"] == _REVENUE
    assert payload["datasets"] == {chart.query_name: _REVENUE}
    assert payload["background"] == _BOARD_RS.background
    json.dumps(payload)


def test_payload_carries_the_active_time_locale() -> None:
    chart = _resolved(_KPI, _REVENUE)
    regional = Regional(locale=RegionalLocale(number="de-DE", time="de-DE"))
    with regional_scope(
        regional.format_locale, regional.time_locale, regional.week_start
    ):
        payload = build_chart_payload(
            chart,
            {chart.query_name: _REVENUE},
            width=300.0,
            height=150.0,
            padding=None,
            board_style=_BOARD_RS,
            board_width=_BOARD_WIDTH,
            variables=None,
            title_shift=NO_TITLE_SHIFT,
            is_placeholder=False,
        )
    assert payload["board_style"]["time_locale"] == regional.time_locale
    assert payload["board_style"]["time_locale"]["shortMonths"][2] == "Mrz"


def test_render_native_returns_svg_and_natural_height() -> None:
    svg, natural_height = _native(_KPI, _REVENUE)
    assert svg.startswith("<svg")
    assert natural_height is None


def test_title_shift_grows_the_card_by_body_dy() -> None:
    spark = {
        "type": "spark_bar",
        "title": "Spark",
        "query": "q",
        "x": "revenue",
        "y": "category",
    }
    rows = [{"revenue": 5, "category": "a"}]
    chart = _resolved(spark, rows)

    def height(shift: TitleShift) -> float:
        svg, _ = render_native(
            chart,
            rows,
            width=300.0,
            height=90.0,
            padding=None,
            board_style=_BOARD_RS,
            board_width=None,
            title_shift=shift,
        )
        match = re.search(r'<svg[^>]* height="([\d.]+)"', svg)
        assert match is not None
        return float(match[1])

    assert height(TitleShift(title_dy=3.0, body_dy=7.0)) == height(NO_TITLE_SHIFT) + 7


def test_kpi_and_spark_bar_numbers_print_under_the_active_number_locale() -> None:
    rows = [{"revenue": 4_820_000.5, "category": "a", "count": 1_234_567}]
    kpi = {"type": "kpi", "query": "q", "value": "revenue", "label": "Revenue"}
    spark = {"type": "spark_bar", "query": "q", "x": "count", "y": "category"}

    def texts(chart_def: dict[str, Any]) -> list[str]:
        svg, _ = render_native(
            _resolved(chart_def, rows),
            rows,
            width=300.0,
            height=90.0,
            padding=None,
            board_style=_BOARD_RS,
            board_width=None,
        )
        return re.findall(r">([^<>]+)</(?:tspan|text)>", svg)

    assert texts(kpi)[:2] == ["4.8", "mn"]
    assert "1,234,567" in texts(spark)
    german = Regional(locale=RegionalLocale(number="de-DE"))
    with regional_scope(german.format_locale, german.time_locale, german.week_start):
        assert texts(kpi)[:2] == ["4,8", "Mio."]
        assert "1.234.567" in texts(spark)


@pytest.mark.parametrize(
    ("cell", "text"),
    [
        (float("inf"), "inf"),
        (float("-inf"), "-inf"),
        (float("nan"), "nan"),
        (Decimal("NaN"), "NaN"),
        (Decimal("-Infinity"), "-Infinity"),
    ],
)
def test_kpi_prints_a_non_finite_cell_as_pythons_str_of_it(
    cell: float | Decimal, text: str
) -> None:
    rows = [{"revenue": cell}]
    kpi = {"type": "kpi", "query": "q", "value": "revenue", "label": "Revenue"}
    svg, _ = render_native(
        _resolved(kpi, rows),
        rows,
        width=300.0,
        height=90.0,
        padding=None,
        board_style=_BOARD_RS,
        board_width=None,
    )
    assert f">{text}</tspan>" in svg


def test_a_non_finite_value_outside_the_query_cells_fails_in_python() -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        dumps_payload({"rows": [], "width": float("nan")})


_SPARK = {
    "type": "spark_bar",
    "title": "Spark",
    "subtitle": "sub",
    "query": "q",
    "x": "revenue",
    "y": "category",
}
_SPARK_ROWS = [{"revenue": 5, "category": "a"}, {"revenue": 9, "category": "b"}]


def _spark_svg(**kwargs: Any) -> str:
    svg, _ = render_native(
        _resolved(_SPARK, _SPARK_ROWS),
        _SPARK_ROWS,
        width=300.0,
        height=0.0,
        padding=None,
        board_style=_BOARD_RS,
        board_width=None,
        **kwargs,
    )
    return svg


def test_spark_bar_placeholder_fades_the_chart_and_paints_the_overlay() -> None:
    from dbt_charts.core.render.placeholder import (
        add_placeholder_overlay,
        apply_placeholder_opacity,
    )

    chart = _resolved(_SPARK, _SPARK_ROWS)
    plain = _spark_svg()
    match = re.search(r'<svg[^>]* height="([\d.]+)"', plain)
    assert match is not None
    height = float(match[1])
    expected = add_placeholder_overlay(
        apply_placeholder_opacity(plain, _BOARD_RS),
        300.0,
        height,
        font=chart.style.spark_bar.font,
        resolved_style=_BOARD_RS,
    )
    # Python's overlay and the engine's writer spell an apostrophe differently.
    assert ET.canonicalize(_spark_svg(is_placeholder=True)) == ET.canonicalize(expected)


def test_a_kpi_link_resolves_against_the_link_context() -> None:
    from dbt_charts.core.render.board_links import LinkContext, set_link_context

    rows = [{"revenue": 5}]
    kpi = {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "R",
        "link": "../team/other.yml",
    }

    def href() -> str:
        svg, _ = render_native(
            _resolved(kpi, rows),
            rows,
            width=300.0,
            height=90.0,
            padding=None,
            board_style=_BOARD_RS,
            board_width=None,
        )
        match = re.search(r'href="([^"]*)"', svg)
        assert match is not None
        return match[1]

    assert href() == "../team/other.yml"
    set_link_context(
        LinkContext(
            root="/d", current_board_slug="zendesk/overview", origin="https://x.test"
        )
    )
    try:
        assert href() == "https://x.test/d/team/other"
    finally:
        set_link_context(None)


@pytest.mark.parametrize("how", ["authored", "synthesized"])
def test_a_spark_bar_link_is_ignored_as_it_always_was(how: str) -> None:
    chart_def = dict(_SPARK, link="../other") if how == "authored" else _SPARK
    chart = _resolved(chart_def, _SPARK_ROWS)
    if how == "synthesized":
        # `auto_link` resolves onto the same field an authored `link:` does.
        chart = chart.model_copy(update={"link": "/orders?region=a"})
    assert chart.link
    svg, _ = render_native(
        chart,
        _SPARK_ROWS,
        width=300.0,
        height=0.0,
        padding=None,
        board_style=_BOARD_RS,
        board_width=None,
    )
    assert "href" not in svg


def test_spark_bar_labels_print_str_of_the_cell() -> None:
    rows = [
        {"revenue": 5, "category": datetime.datetime(2024, 1, 1, 12, 0, 0)},
        {"revenue": 9, "category": datetime.date(2024, 1, 2)},
        {"revenue": 3, "category": float("nan")},
    ]
    chart = _resolved(_SPARK, rows)
    spark = chart.style.spark_bar
    wide = spark.model_copy(
        update={"label": spark.label.model_copy(update={"width": 200})}
    )
    chart = chart.model_copy(
        update={"style": chart.style.model_copy(update={"spark_bar": wide})}
    )
    svg, _ = render_native(
        chart,
        rows,
        width=400.0,
        height=0.0,
        padding=None,
        board_style=_BOARD_RS,
        board_width=None,
    )
    labels = re.findall(r'fill="#[0-9a-f]+" font-family="[^"]*">([^<]+)</text>', svg)
    assert labels == ["2024-01-01 12:00:00", "2024-01-02", "nan"]


@pytest.mark.parametrize(
    ("fmt", "cell"),
    [
        ("currency", 4_820_000.0),
        ("currency", 0.45),
        ("number", 0.5),
        ("number", 0.0023),
        ("number", -1.2e-9),
        ("number", 1234.5),
        ("integer", 1234567),
        ("percent", 0.184),
        ("percent_delta", -0.034),
        ("delta", -12),
        ("$,.2f", -1234.5),
        (None, 1234.5),
        (None, 12.0),
        (None, 4_820_000),
        (None, "At risk"),
        ("date_short", "2026-03-04"),
    ],
)
def test_the_engine_kpi_prints_the_text_board_to_dict_and_the_terminal_print(
    fmt: str | None, cell: float | str
) -> None:
    """`kpi_value_text` is the second formatter for a KPI value; this pins it to the card."""
    from dbt_charts.core.render.chart.kpi_value import kpi_value_text

    style = {} if fmt is None else {"style": {"value": {"format": fmt}}}
    chart_def = {"type": "kpi", "query": "q", "value": "v", "label": "L", **style}
    rows = [{"v": cell}]
    chart = _resolved(chart_def, rows)
    svg, _ = render_native(
        chart,
        rows,
        width=400.0,
        height=90.0,
        padding=None,
        board_style=_BOARD_RS,
        board_width=None,
    )
    value_run = re.search(r"<text[^>]*>(.*?)</text>", svg, re.S)
    assert value_run is not None
    painted = html.unescape(re.sub(r"<[^>]+>", "", value_run[1]))
    expected = kpi_value_text(
        cell,
        chart.format,
        chart.id,
        _BOARD_RS.chart_defaults.formats,
        native=chart.format_native,
        format_may_be_cascaded=True,
    )
    # The card kerns an affix against the number; the flat text spells the gap.
    assert "".join(painted.split()) == "".join(expected.split())


def _with_overlay_weight(weight: str | float) -> ResolvedStyle:
    overlay = _BOARD_RS.placeholder.overlay
    font = overlay.font.model_copy(update={"weight": weight})
    placeholder = _BOARD_RS.placeholder.model_copy(
        update={"overlay": overlay.model_copy(update={"font": font})}
    )
    return dataclasses.replace(_BOARD_RS, placeholder=placeholder)


@pytest.mark.parametrize(
    ("weight", "attr"), [("bold", "bold"), (700, "700"), (500.0, "500.0")]
)
def test_a_placeholder_font_weight_of_any_authored_kind_leaves_every_engine_chart_rendering(
    weight: str | float, attr: str
) -> None:
    board_style = _with_overlay_weight(weight)
    charts = {
        "kpi": {"type": "kpi", "query": "q", "value": "revenue", "label": "R"},
        "spark_bar": _SPARK,
        "callout": {"type": "callout", "message": "hi"},
    }
    for name, chart_def in charts.items():
        rows = _SPARK_ROWS if name == "spark_bar" else [{"revenue": 5, "category": "a"}]
        svg, _ = render_native(
            _resolved(chart_def, rows),
            rows,
            width=300.0,
            height=90.0,
            padding=None,
            board_style=board_style,
            board_width=None,
            is_placeholder=name == "spark_bar",
        )
        if name == "spark_bar":
            assert f'font-weight="{attr}" fill=' in svg.split("placeholder-overlay")[1]


def test_a_config_override_of_the_spark_bar_title_block_reaches_the_painted_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from dbt_charts.core.compile.config import get_chart_rendering

    real = get_chart_rendering()
    fake = SimpleNamespace(
        spark_bar=SimpleNamespace(title_height=40.0, title_baseline_y=30.0),
    )
    default_svg = _spark_svg()
    monkeypatch.setattr(
        "dbt_charts.core.render.engine.get_chart_rendering", lambda: fake
    )
    svg = _spark_svg()
    assert real.spark_bar.title_baseline_y != 30.0
    assert 'y="30.0"' in svg and 'y="30.0"' not in default_svg


def test_table_pagination_reads_the_variables_the_seam_forwards() -> None:
    rows = [{"name": f"row_{i}"} for i in range(30)]
    table = {
        "type": "table",
        "query": "q",
        "style": {"pagination": {"enabled": True, "page_rows": 5}},
    }
    page_1, _ = _native(table, rows, width=400.0, height=0.0)
    page_2, _ = _native(table, rows, width=400.0, height=0.0, variables={"c1_page": 2})
    assert page_1 != page_2


def test_kpi_multirow_raises_the_registry_error() -> None:
    with pytest.raises(ChartDataError) as caught:
        _native(_KPI, _REVENUE, render_data=_REVENUE * 2)
    assert caught.value.code.code == "ERR-KPI-MULTIROW"
    assert caught.value.fields == {"chart_id": "c1", "row_count": 2}
    assert "2" in str(caught.value)


def test_table_error_raises_the_registry_error() -> None:
    rows = [{"s": "#3164a3"}, {"s": "Mid"}]
    table = {
        "type": "table",
        "query": "q",
        "style": {"columns": {"s": {"swatch": True}}},
    }
    with pytest.raises(ChartDataError) as caught:
        _native(table, rows)
    assert caught.value.code.code == "ERR-TABLE-SWATCH-NOT-COLOR"
    assert caught.value.fields["value"] == "Mid"


def test_spark_bar_error_raises_the_registry_error() -> None:
    rows = [{"category": "A", "revenue": 100}]
    with pytest.raises(ChartDataError) as caught:
        _native(
            {"type": "spark_bar", "query": "q", "x": "revenu", "y": "category"}, rows
        )
    assert caught.value.code.code == "ERR-SPARK-BAR-VALUE-FIELD-NOT-FOUND"


def test_engine_refusal_raises_instead_of_falling_back() -> None:
    point_map = {
        "type": "point_map",
        "query": "q",
        "latitude": "lat",
        "longitude": "lon",
    }
    with pytest.raises(ChartDataError) as caught:
        _native(point_map, [{"lat": 1.0, "lon": 2.0}])
    assert caught.value.code.code == "ERR-ENGINE-REFUSED"
    assert caught.value.fields == {"message": "chart family not implemented: point_map"}


def test_kpi_label_truncation_lands_in_the_text_sink() -> None:
    label = "A Very Long KPI Label That Cannot Possibly Fit"
    with _sinks() as sinks:
        _native({**_KPI, "label": label}, _REVENUE, width=120.0)
    assert sinks.truncations["c1"] == [
        TextTruncation(surface="kpi_label", authored_field="label", authored_text=label)
    ]


def test_kpi_inline_fallback_lands_in_the_text_sink() -> None:
    chart = {
        **_KPI,
        "label": "Revenue this quarter",
        "style": {"variant": "inline"},
    }
    with _sinks() as sinks:
        _native(chart, _REVENUE, width=120.0)
    assert (
        TextTruncation(
            surface="kpi_inline_fallback",
            authored_field="style.variant",
            authored_text="inline",
        )
        in sinks.truncations["c1"]
    )


def test_kpi_align_overflow_lands_in_the_text_sink() -> None:
    chart = {**_KPI, "style": {"align": "right"}}
    rows = [{"revenue": 123_456_789_012_345}]
    with _sinks() as sinks:
        _native(chart, rows, width=40.0)
    assert [(t.surface, t.authored_field) for t in sinks.truncations["c1"]] == [
        ("kpi_align_overflow", "style.align")
    ]


def test_kpi_support_truncation_lands_in_the_text_sink() -> None:
    chart = {
        **_KPI,
        "support": {"value": "revenue", "label": "an explainer far too long to fit"},
    }
    with _sinks() as sinks:
        _native(chart, _REVENUE, width=120.0)
    assert (
        TextTruncation(
            surface="kpi_support",
            authored_field="support.label",
            authored_text="an explainer far too long to fit",
        )
        in sinks.truncations["c1"]
    )


def test_spark_label_truncation_records_one_entry_per_label() -> None:
    rows = [
        {"category": "A label long enough to be cut off", "revenue": 100},
        {"category": "Another label long enough to be cut", "revenue": 50},
    ]
    with _sinks() as sinks:
        _native(
            {"type": "spark_bar", "query": "q", "x": "revenue", "y": "category"},
            rows,
            width=160.0,
        )
    assert [(t.surface, t.authored_field) for t in sinks.truncations["c1"]] == [
        ("spark_label", "y")
    ] * 2


def test_table_text_truncation_records_the_engine_count_per_column() -> None:
    chart = _resolved({"type": "table", "query": "q"}, [_WIDE_ROW])
    payload = build_chart_payload(
        chart,
        {chart.query_name: [_WIDE_ROW]},
        width=200.0,
        height=0.0,
        padding=None,
        board_style=_BOARD_RS,
        board_width=_BOARD_WIDTH,
        variables={},
        title_shift=NO_TITLE_SHIFT,
        is_placeholder=False,
    )
    _, raw, _ = _engine.render_chart(json.dumps(payload))
    counts = {
        w["params"]["authored_field"]: w["params"]["truncation_count"]
        for w in raw
        if w["code"] == "WARN-TABLE-TEXT-TRUNCATED"
    }
    assert counts
    with _sinks() as sinks:
        _native({"type": "table", "query": "q"}, [_WIDE_ROW], width=200.0, height=0.0)
    assert Counter(
        t.authored_field for t in sinks.truncations["c1"] if t.surface == "table_cell"
    ) == Counter(counts)


def test_callout_truncation_lands_in_the_text_sink() -> None:
    callout = {"type": "callout", "message": "word " * 200}
    with _sinks() as sinks:
        _native(callout, [], width=120.0, height=40.0)
    assert [(t.surface, t.authored_field) for t in sinks.truncations["c1"]] == [
        ("callout_text", "message")
    ]


def test_callout_truncation_reports_the_text_as_authored_not_as_resolved() -> None:
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="400"></svg>'
    callout = _resolved(
        {"type": "callout", "message": "![m](shot.svg) " + "word " * 200}, []
    ).model_copy(update={"base_dir": "charts"})
    project = InMemoryProject(Path("/proj"), {"charts/shot.svg": svg})
    with image_scope(project), _sinks() as sinks:
        render_native(
            callout,
            [],
            width=120.0,
            height=40.0,
            padding=None,
            board_style=_BOARD_RS,
            board_width=_BOARD_WIDTH,
        )
    [truncation] = sinks.truncations["c1"]
    assert truncation.authored_text == callout.message


_WIDE_ROW = {f"column_number_{i}": "A fairly long cell value" for i in range(10)}
_SQUEEZE_ROWS = [{"service": f"svc_{i}", "cost": i * 100} for i in range(1, 5)]


def test_table_page_squeeze_lands_in_its_sink() -> None:
    table = {"type": "table", "query": "q", "style": {"pagination": {"enabled": True}}}
    with _sinks() as sinks:
        _native(table, _SQUEEZE_ROWS, width=600.0, height=100.0)
    assert sinks.squeezes == {
        "c1": TablePageSqueeze(drawn_rows=1, page_rows=4, total_rows=4)
    }


def test_table_columns_overflow_lands_in_its_sink() -> None:
    table = {
        "type": "table",
        "query": "q",
        "style": {"columns": {"org_id": {"width": 300}, "table_name": {"width": 300}}},
    }
    row = {"org_id": "act_2zGAXPCi8w3MckVTMpVT3OSQ3Ni", "table_name": "weekly"}
    with _sinks() as sinks:
        _native(table, [row], width=400.0, height=0.0)
    overflow = sinks.overflows["c1"]
    assert overflow.available_width == 400.0
    assert overflow.required_width > 400.0


def test_table_cramping_lands_in_its_sink_with_the_engine_suggested_width() -> None:
    table = {"type": "table", "query": "q", "style": {"preferred_width": 800}}
    rows = [{f"a_long_header_name_{i}": "1,234" for i in range(6)}]
    with _sinks() as sinks:
        _native(table, rows, width=420.0, height=0.0)
    cramping = sinks.crampings["c1"]
    assert cramping.wrapped_headers > 0
    assert cramping.column_count == 6
    assert cramping.required_width > cramping.available_width
    assert cramping.suggested_width is not None
    assert cramping.suggested_width % 50 == 0


def test_a_cramped_table_outside_a_board_renders_with_no_suggested_fix() -> None:
    table = {"type": "table", "query": "q", "style": {"preferred_width": 800}}
    rows = [{f"a_long_header_name_{i}": "1,234" for i in range(6)}]
    chart = _resolved(table, rows)
    with _sinks() as sinks:
        svg, _ = render_native(
            chart,
            rows,
            width=420.0,
            height=0.0,
            padding=None,
            board_style=_BOARD_RS,
            board_width=None,
        )
    assert svg.startswith("<svg")
    assert sinks.crampings == {}


_CAPPED_TABLE = {
    "type": "table",
    "query": "q",
    "style": {"pagination": {"enabled": True, "page_rows": 5}},
}
_CAPPED_ROWS = [{"name": f"row_{i}"} for i in range(125)]


def test_static_pagination_whose_cap_note_was_dropped_lands_in_its_sink() -> None:
    with _sinks() as sinks:
        _native(_CAPPED_TABLE, _CAPPED_ROWS, width=400.0, height=190.0)
    assert sinks.paginations == {
        "c1": StaticPagination(
            page_rows=5,
            total_rows=125,
            rendered_pages=20,
            total_pages=25,
            cap_note_dropped=True,
        )
    }


def test_static_pagination_that_states_its_cap_in_the_file_is_recorded_as_stated() -> (
    None
):
    with _sinks() as sinks:
        svg, _ = _native(_CAPPED_TABLE, _CAPPED_ROWS, width=400.0, height=0.0)
    assert "in this static export" in svg
    assert sinks.paginations == {
        "c1": StaticPagination(
            page_rows=5,
            total_rows=125,
            rendered_pages=20,
            total_pages=25,
            cap_note_dropped=False,
        )
    }


def test_no_open_sink_is_a_noop() -> None:
    _native(
        {**_KPI, "label": "A Very Long KPI Label That Cannot Possibly Fit"},
        _REVENUE,
        width=120.0,
    )


def test_an_unmapped_engine_warning_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def emit(_payload: str) -> tuple[str, list[dict[str, Any]], float | None]:
        return "<svg/>", [{"code": "WARN-NOT-MAPPED", "params": {}}], None

    monkeypatch.setattr(_engine, "render_chart", emit)
    with pytest.raises(ValueError, match="WARN-NOT-MAPPED"):
        _native(_KPI, _REVENUE)


@pytest.mark.parametrize("cell", [Decimal("NaN"), Decimal("-Infinity"), float("nan")])
def test_a_non_finite_numeric_table_cell_paints_the_null_dash(
    cell: float | Decimal,
) -> None:
    rows = [{"n": "a", "v": 1.5}, {"n": "b", "v": cell}]
    svg, _ = _native({"type": "table", "query": "q"}, rows, width=400.0, height=0.0)
    assert "—" in svg


def test_a_decimal_nan_pivot_dimension_keeps_first_seen_order_without_crashing() -> (
    None
):
    rows = [
        {"r": "x", "c": Decimal("NaN"), "v": 1},
        {"r": "x", "c": Decimal(2), "v": 2},
        {"r": "x", "c": Decimal(1), "v": 3},
    ]
    table = {
        "type": "table",
        "query": "q",
        "rows": ["r"],
        "columns": ["c"],
        "values": ["v"],
    }
    svg, _ = _native(table, rows, width=500.0, height=0.0)
    # The dimension is labeled with str() of it; the unsortable mix
    # keeps first-seen order.
    assert re.search(r">NaN</\w+>.*?>2</\w+>.*?>1</\w+>", svg, re.S)


def test_a_nan_element_in_a_spark_columns_cell_does_not_erase_the_real_bars() -> None:
    rows = [{"n": "a", "trend": [float("nan"), -5, 5]}]
    table = {
        "type": "table",
        "query": "q",
        "style": {"columns": {"trend": {"spark": {"type": "columns"}}}},
    }
    svg, _ = _native(table, rows, width=400.0, height=0.0)
    assert svg.count('rx="1.0"') == 2


def test_a_tables_title_metrics_follow_the_engines_painted_title_block() -> None:
    """Across the width where a long title and subtitle wrap, the band's
    metrics track the height the engine paints for the title block."""
    from dbt_charts.core.render.title_band import chart_title_metrics

    def table(title: bool) -> dict[str, Any]:
        return {
            "type": "table",
            "query": "q",
            "title": "Quarterly revenue by region and product family for enterprise accounts"
            if title
            else None,
            "subtitle": "All figures in US dollars, before refunds and adjustments"
            if title
            else None,
        }

    rows = [{"a": 1}]
    tails: set[float] = set()
    for width in range(240, 700, 20):
        titled = _resolved(table(True), rows)
        metrics = chart_title_metrics(titled, card_padding=0.0, slot_width=float(width))
        assert metrics is not None
        _, with_title = _native(table(True), rows, width=float(width), height=0.0)
        _, without = _native(table(False), rows, width=float(width), height=0.0)
        assert with_title is not None and without is not None
        outer = float(titled.style.table.outer_padding)
        assert with_title - without == pytest.approx(
            metrics.ascent - outer + metrics.tail, abs=1.0
        )
        tails.add(metrics.tail)
    assert len(tails) > 1, "the sweep never crossed a wrap"


def test_board_style_envelope_carries_the_sizers_pagination_control_height() -> None:
    from dbt_charts.core.render.engine import board_style_envelope
    from dbt_charts.core.render.sizing import TABLE_PAGINATION_CONTROL_HEIGHT
    from dbt_charts.core.text.format_d3 import active_format_locale, active_time_locale

    style, _ = resolve_style_and_context(get_theme_style())
    envelope = board_style_envelope(
        style, None, active_format_locale(), active_time_locale()
    )
    assert envelope["pagination_control_height"] == TABLE_PAGINATION_CONTROL_HEIGHT


def _pivot_headers(cells: list[object]) -> list[str]:
    rows = [{"r": "x", "c": c, "v": i} for i, c in enumerate(cells)]
    table = {
        "type": "table",
        "query": "q",
        "rows": ["r"],
        "columns": ["c"],
        "values": ["v"],
        "style": {"header": {"font": {"case": "none"}}},
    }
    svg, _ = _native(table, rows, width=600.0, height=0.0)
    head = svg.split("data-col")[0]
    return re.findall(r">([^<>]+)</(?:text|tspan)>", head)[1:]


def test_finite_decimal_pivot_dimensions_label_by_str_in_first_seen_order() -> None:
    cells = [Decimal("2"), Decimal("NaN"), Decimal("1")]
    assert _pivot_headers(cells) == ["2", "NaN", "1"]


def test_finite_decimal_pivot_dimensions_sort_numerically() -> None:
    assert _pivot_headers([Decimal("2024"), Decimal("2023")]) == ["2023", "2024"]


def test_the_width_measure_labels_decimal_pivot_dimensions_as_the_render_does() -> None:
    """A Decimal dimension labels its column with `str()` ("2.50", not "2.5") in
    the measure as in the render, so a hugged pivot is sized for its headers."""
    from dbt_charts.core.render.engine import measure_table_natural_width

    table = {
        "type": "table",
        "query": "q",
        "rows": ["r"],
        "columns": ["c"],
        "values": ["v"],
    }

    def rows(labels: list[object]) -> list[dict[str, Any]]:
        return [{"r": "x", "c": c, "v": i} for i, c in enumerate(labels)]

    decimals = rows([Decimal("2024"), Decimal("2.50"), Decimal("1000000.00")])
    chart = _resolved(table, decimals)
    as_text = measure_table_natural_width(
        chart, rows(["2024", "2.50", "1000000.00"]), _BOARD_RS
    )
    as_floats = measure_table_natural_width(
        chart, rows([2024.0, 2.5, 1000000.0]), _BOARD_RS
    )
    assert as_text is not None and as_floats is not None
    assert measure_table_natural_width(chart, decimals, _BOARD_RS) == as_text
    assert as_text != as_floats


def test_the_width_measure_carries_the_active_time_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dbt_charts.core.render import engine

    sent: list[dict[str, Any]] = []

    def capture(payload_json: str) -> float | None:
        sent.append(json.loads(payload_json))
        return None

    monkeypatch.setattr(engine._engine, "measure_table_natural_width", capture)
    table = {"type": "table", "query": "q"}
    rows = [{"d": "2024-03-05"}]
    german = Regional(locale=RegionalLocale(time="de-DE"))
    with regional_scope(german.format_locale, german.time_locale, german.week_start):
        engine.measure_table_natural_width(_resolved(table, rows), rows, _BOARD_RS)
    assert sent[0]["time_locale"] == german.time_locale


@pytest.mark.parametrize(
    ("currency", "unit"),
    [("latitude", "°"), ("',.4f'", None)],
)
def test_a_table_column_follows_the_board_alias_chain(
    currency: str, unit: str | None
) -> None:
    """`number: currency` on a board that redefines `currency` paints the
    board's `currency` in a default-formatted table column, as a KPI does."""
    svg = render_board_to_svg(
        f"""
title: T
style:
  formats:
    number: currency
    currency: {currency}
queries:
  q:
    columns: [lat]
    values:
      - [37.7749]
charts:
  t:
    query: q
    type: table
rows:
  - t
"""
    )
    assert ">37.7749</tspan>" in svg
    assert (f'"start">{unit}</tspan>' in svg) == (unit is not None)


def test_a_chain_ending_at_an_engine_name_keeps_that_names_rules() -> None:
    """`currency: compact` keeps `compact`'s below-1 plain digits in a table
    column, as a KPI paints it."""
    svg = render_board_to_svg(
        """
title: T
style:
  formats:
    number: currency
    currency: compact
queries:
  q:
    columns: [rate]
    values:
      - [0.0042]
charts:
  t:
    query: q
    type: table
rows:
  - t
"""
    )
    assert ">0.0042</tspan>" in svg
