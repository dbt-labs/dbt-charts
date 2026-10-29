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
from zoneinfo import ZoneInfo

import pytest

from dbt_charts import _engine
from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
)
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
from dbt_charts.core.constants.layout import SUPPORT_TABLE_COLUMN_BLOCK_SHARE_WARN_RATIO
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.annotation_nothing_drawn import (
    collect_annotation_nothing_drawn,
)
from dbt_charts.core.render.chart.axis_label_collision import (
    AxisLabelCollision,
    collect_axis_label_collisions,
)
from dbt_charts.core.render.chart.endpoint_label_overflow import (
    EndpointLabelGapOverflow,
    collect_endpoint_label_gap_overflows,
)
from dbt_charts.core.render.chart.plot_width_floor_record import (
    PlotWidthShareWarning,
    collect_plot_width_share_warnings,
)
from dbt_charts.core.render.chart.series_label_truncation import (
    SeriesLabelTruncation,
    collect_series_label_truncations,
)
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
from dbt_charts.core.render.chart.x_domain_paint_order import (
    XDomainPaintOrder,
    collect_x_domain_paint_orders,
)
from dbt_charts.core.render.engine import (
    build_chart_payload,
    column_types,
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
    assert (
        payload["board_style"]["column_block_share_warn_ratio"]
        == SUPPORT_TABLE_COLUMN_BLOCK_SHARE_WARN_RATIO
    )
    json.dumps(payload)


def _engine_decided_paths(node: object, path: tuple[str, ...] = ()) -> Generator[str]:
    """Paths of `auto` leaves the theme spells for the engine to decide."""
    if node == "auto" and (
        path[-3:-1] == ("title", "font")
        or path[-2:] == ("font", "color")
        or (path[-2:] == ("title", "visible") and "legend" not in path)
    ):
        yield ".".join(path)
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _engine_decided_paths(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _engine_decided_paths(value, (*path, str(index)))


@pytest.mark.parametrize("theme", ["clarity", "paper", "stark", "neon", "vivid"])
@pytest.mark.parametrize(
    "chart_def",
    [
        _KPI,
        {"type": "table", "query": "q"},
    ],
    ids=["kpi", "table"],
)
def test_payload_carries_no_engine_decided_literal(
    theme: str, chart_def: dict[str, Any]
) -> None:
    board_rs, board_ctx = resolve_style_and_context(get_theme_style(theme))
    flat = normalize_chart(
        "c1", chart_def, {"q": ValuesQuery(rows=_REVENUE)}, sources={}
    )
    chart = resolve(flat, _REVENUE, chart_style_context=board_ctx)
    payload = build_chart_payload(
        chart,
        {chart.query_name: _REVENUE},
        width=300.0,
        height=150.0,
        padding=None,
        board_style=board_rs,
        board_width=_BOARD_WIDTH,
        variables=None,
        title_shift=NO_TITLE_SHIFT,
        is_placeholder=False,
    )
    leaked = list(_engine_decided_paths(json.loads(json.dumps(payload))))
    assert not leaked, f"{theme}: payload carries engine-decided literals {leaked}"


def test_payload_types_every_column_of_rows_and_each_dataset() -> None:
    chart = _resolved(_KPI, _REVENUE)
    other = [{"day": datetime.date(2024, 1, 2), "label": "a"}]
    payload = build_chart_payload(
        chart,
        {chart.query_name: _REVENUE, "other": other},
        width=300.0,
        height=150.0,
        padding=None,
        board_style=_BOARD_RS,
        board_width=_BOARD_WIDTH,
        variables={},
        title_shift=NO_TITLE_SHIFT,
        is_placeholder=False,
    )
    assert payload["column_types"] == {
        "rows": {"revenue": "integer"},
        "datasets": {
            chart.query_name: {"revenue": "integer"},
            "other": {"day": "date", "label": "string"},
        },
    }


def test_column_types_name_each_cell_kind() -> None:
    rows = [
        {
            "s": "a",
            "i": 1,
            "f": 1.5,
            "d": Decimal("1.50"),
            "b": True,
            "date": datetime.date(2024, 1, 2),
            "ts": datetime.datetime(2024, 1, 2, 3, 4),
            "n": None,
            "nested": [{"key": "k", "value": 1}],
        },
        {"s": None, "i": None, "f": float("nan"), "n": None},
    ]
    assert column_types(rows) == {
        "s": "string",
        "i": "integer",
        "f": "float",
        "d": "decimal",
        "b": "boolean",
        "date": "date",
        "ts": "datetime",
        "n": "null",
        "nested": "nested",
    }


def test_column_types_widen_integers_into_floats() -> None:
    assert column_types([{"v": 1}, {"v": 2.5}]) == {"v": "float"}


@pytest.mark.parametrize(
    "mixed",
    [
        ["a", 1],
        [datetime.date(2024, 1, 2), "2024-01-02"],
        [datetime.date(2024, 1, 2), datetime.datetime(2024, 1, 2)],
        [True, 1],
        [Decimal("1"), 1.5],
    ],
)
def test_column_types_report_a_column_mixing_cell_kinds_as_mixed(
    mixed: list[Any],
) -> None:
    assert column_types([{"v": cell} for cell in mixed]) == {"v": "mixed"}


def test_payload_carries_the_active_time_locale() -> None:
    chart = _resolved(_KPI, _REVENUE)
    regional = Regional(
        locale=RegionalLocale(number="de-DE", time="de-DE"),
        time_zone="Europe/Berlin",
    )
    with regional_scope(
        regional.format_locale,
        regional.time_locale,
        regional.week_start,
        regional.zone,
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
    assert payload["board_style"]["time_zone"] == "Europe/Berlin"
    assert payload["board_style"]["week_start"] == 1, "de-DE weeks start on Monday"


def test_a_table_prints_an_aware_cell_in_the_board_zone_and_a_naive_one_as_written() -> (
    None
):
    instant = datetime.datetime(2024, 3, 10, 7, 30, tzinfo=datetime.UTC)
    rows = [{"at": instant}, {"at": instant.replace(tzinfo=None)}]
    table = {
        "type": "table",
        "query": "q",
        "style": {"columns": {"at": {"format": "%H:%M %Z"}}},
    }
    utc = Regional()
    with regional_scope(
        utc.format_locale,
        utc.time_locale,
        utc.week_start,
        ZoneInfo("America/New_York"),
    ):
        svg, _ = _native(table, rows, width=400.0, height=0.0)
    texts = re.findall(r">([^<>]+)</(?:tspan|text)>", svg)
    assert "03:30 EDT" in texts
    assert "07:30" in [text.strip() for text in texts]


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
    with regional_scope(
        german.format_locale,
        german.time_locale,
        german.week_start,
        german.zone,
    ):
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
        # `links.auto` resolves onto the same field an authored `link:` does.
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


def test_the_spark_bar_title_estimate_constants_reach_the_painted_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_svg = _spark_svg()
    monkeypatch.setattr("dbt_charts.core.render.engine.SPARK_BAR_TITLE_HEIGHT_PX", 40.0)
    monkeypatch.setattr(
        "dbt_charts.core.render.engine.SPARK_BAR_TITLE_BASELINE_Y_PX", 30.0
    )
    svg = _spark_svg()
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
        "color": "kind",
        "projection": "mercator",
    }
    with pytest.raises(ChartDataError) as caught:
        _native(point_map, [{"lat": 40.7, "lon": -74.0, "kind": ["a"]}])
    assert caught.value.code.code == "ERR-ENGINE-REFUSED"
    assert caught.value.fields == {
        "message": "a point map color value that is a list is not supported"
    }


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


@pytest.mark.parametrize("height", [190.0, 0.0])
def test_static_pagination_is_recorded_with_no_cap_note_in_any_slot(
    height: float,
) -> None:
    with _sinks() as sinks:
        svg, _ = _native(_CAPPED_TABLE, _CAPPED_ROWS, width=400.0, height=height)
    assert "Showing pages" not in svg
    assert sinks.paginations == {
        "c1": StaticPagination(
            page_rows=5,
            total_rows=125,
            rendered_pages=20,
            total_pages=25,
            cap_note_dropped=False,
        )
    }


_MONTHS = [{"month": m, "revenue": i * 10} for i, m in enumerate(["Jan", "Feb"], 1)]


@pytest.mark.parametrize("chart_type", ["bar", "line", "area", "scatter", "heatmap"])
def test_cartesian_chart_title_truncation_lands_in_the_text_sink(
    chart_type: str,
) -> None:
    title = "Quarterly Revenue Growth " * 12
    chart = {"type": chart_type, "query": "q", "x": "month", "y": "revenue"}
    if chart_type == "heatmap":
        chart["color"] = "revenue"
    with _sinks() as sinks:
        _native({**chart, "title": title}, _MONTHS, width=600.0, height=320.0)
    assert [(t.surface, t.authored_field) for t in sinks.truncations["c1"]] == [
        ("chart_title", "title")
    ]


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


_CARTESIAN_WARNINGS: list[tuple[str, dict[str, Any]]] = [
    (
        "WARN-CHART-TITLE-TRUNCATED",
        {"chart_id": "c1", "authored_field": "title", "authored_text": "T"},
    ),
    (
        "WARN-AXIS-TITLE-TRUNCATED",
        {"chart_id": "c1", "authored_field": "x_label", "authored_text": "X"},
    ),
    (
        "WARN-SUPPORT-TABLE-CELL-OVERFLOW",
        {"chart_id": "c1", "detail": "82px needed, 47px per band"},
    ),
    (
        "WARN-SERIES-LABEL-TRUNCATED",
        {"chart_id": "c1", "authored_field": "color", "series_names": ["A"]},
    ),
    (
        "WARN-ENDPOINT-LABEL-GAP-OVERFLOW",
        {"chart_id": "c1", "series_count": 3, "gap_px": 18.2},
    ),
    (
        "WARN-ENDPOINT-LABEL-RAIL-TIED",
        {"chart_id": "c1", "series_count": 3, "gap_px": 18.2},
    ),
    (
        "WARN-ENDPOINT-LABEL-RAIL-OVERFLOW",
        {
            "chart_id": "c1",
            "series_count": 3,
            "gap_px": 18.2,
            "dropped_series": ["B"],
        },
    ),
    (
        "WARN-AXIS-LABEL-COLLISION",
        {"chart_id": "c1", "field": "x", "label_count": 80},
    ),
    (
        "WARN-LAYER-X-DOMAIN-PAINT-ORDER",
        {"chart_id": "c1", "x_field": "m", "layer_only": ["Feb", "Apr"]},
    ),
    (
        "WARN-PLOT-WIDTH-BELOW-MINIMUM",
        {
            "chart_id": "c1",
            "block_width": 300.0,
            "plot_width": 200.0,
            "card_width": 500.0,
        },
    ),
]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (
            "WARN-ENDPOINT-LABEL-GAP-OVERFLOW",
            EndpointLabelGapOverflow(
                series_count=3, gap_px=18.2, cause="gap_did_not_fit"
            ),
        ),
        (
            "WARN-ENDPOINT-LABEL-RAIL-TIED",
            EndpointLabelGapOverflow(series_count=3, gap_px=18.2, cause="no_slope"),
        ),
        (
            "WARN-ENDPOINT-LABEL-RAIL-OVERFLOW",
            EndpointLabelGapOverflow(
                series_count=3,
                gap_px=18.2,
                cause="rail_overflow",
                dropped_series=("B",),
            ),
        ),
    ],
)
def test_each_endpoint_rail_warning_records_its_own_cause(
    monkeypatch: pytest.MonkeyPatch, code: str, expected: EndpointLabelGapOverflow
) -> None:
    params = dict(dict(_CARTESIAN_WARNINGS)[code])

    def emit(_payload: str) -> tuple[str, list[dict[str, Any]], float | None]:
        return "<svg/>", [{"code": code, "params": params}], None

    monkeypatch.setattr(_engine, "render_chart", emit)
    with collect_endpoint_label_gap_overflows() as rails:
        _native(_KPI, _REVENUE)
    assert rails == {"c1": expected}


def test_cartesian_engine_warnings_land_in_their_sinks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = [
        {"code": code, "params": params} for code, params in _CARTESIAN_WARNINGS
    ]

    def emit(_payload: str) -> tuple[str, list[dict[str, Any]], float | None]:
        return "<svg/>", warnings, None

    monkeypatch.setattr(_engine, "render_chart", emit)
    with ExitStack() as stack:
        truncations = stack.enter_context(collect_text_truncations())
        series = stack.enter_context(collect_series_label_truncations())
        rails = stack.enter_context(collect_endpoint_label_gap_overflows())
        collisions = stack.enter_context(collect_axis_label_collisions())
        paint_orders = stack.enter_context(collect_x_domain_paint_orders())
        plot_widths = stack.enter_context(collect_plot_width_share_warnings())
        _native(_KPI, _REVENUE)
    assert [
        (t.surface, t.authored_field, t.authored_text) for t in truncations["c1"]
    ] == [
        ("chart_title", "title", "T"),
        ("axis_title", "x_label", "X"),
        ("support_table_cell_overflow", "support_table", "82px needed, 47px per band"),
    ]
    assert series["c1"] == [
        SeriesLabelTruncation(authored_field="color", series_name="A", fate="truncated")
    ]
    assert rails["c1"] == EndpointLabelGapOverflow(
        series_count=3, gap_px=18.2, cause="rail_overflow", dropped_series=("B",)
    )
    assert collisions["c1"] == AxisLabelCollision(field="x", label_count=80)
    assert paint_orders["c1"] == XDomainPaintOrder(
        x_field="m", layer_only=("Feb", "Apr")
    )
    assert plot_widths["c1"] == PlotWidthShareWarning(
        column_block_width_px=300.0, plot_width_px=200.0, card_width_px=500.0
    )


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
        metrics = chart_title_metrics(
            titled, card_padding=0.0, slot_width=float(width), pie_mode=None
        )
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
        style, None, active_format_locale(), active_time_locale(), "UTC", 0
    )
    assert envelope["pagination_control_height"] == TABLE_PAGINATION_CONTROL_HEIGHT


def test_board_style_envelope_carries_the_resolved_text_mark_font() -> None:
    from dbt_charts.core.render.engine import board_style_envelope
    from dbt_charts.core.text.format_d3 import active_format_locale, active_time_locale

    style, _ = resolve_style_and_context(get_theme_style())
    envelope = board_style_envelope(
        style, None, active_format_locale(), active_time_locale(), "UTC", 0
    )
    font = style.chart_defaults.marks.text.font
    assert envelope["text_mark_font"] == {"family": font.family, "size": font.size}


def test_board_style_envelope_carries_the_authored_numeral_family() -> None:
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.render.engine import board_style_envelope
    from dbt_charts.core.text.format_d3 import active_format_locale, active_time_locale

    def envelope_for(patch: StylePatch | None) -> dict:
        style, _ = resolve_style_and_context(get_theme_style(), patch)
        return board_style_envelope(
            style, None, active_format_locale(), active_time_locale(), "UTC", 0
        )

    assert envelope_for(None)["numeral_family"] == (
        "'dbt Sans Tabular', 'Noto Emoji', Inter, system-ui, sans-serif"
    )
    patch = StylePatch.model_validate(
        {"fonts": {"numeral": {"family": "'Source Serif 4', Georgia, serif"}}}
    )
    assert envelope_for(patch)["numeral_family"] == (
        "'Source Serif 4', 'Noto Emoji', Georgia, serif"
    )


def test_board_style_envelope_carries_the_themes_hover_emphasis() -> None:
    from dbt_charts.core.render.engine import board_style_envelope
    from dbt_charts.core.text.format_d3 import active_format_locale, active_time_locale

    style, _ = resolve_style_and_context(get_theme_style())
    envelope = board_style_envelope(
        style, None, active_format_locale(), active_time_locale(), "UTC", 0
    )
    hover = style.chart_defaults.hover_emphasis
    assert envelope["hover_emphasis"] == {
        "visible": hover.visible,
        "recede_contrast": hover.recede_contrast,
    }


def test_board_style_envelope_carries_the_themes_default_view_widths() -> None:
    from dbt_charts.core.render.engine import board_style_envelope
    from dbt_charts.core.text.format_d3 import active_format_locale, active_time_locale

    style, _ = resolve_style_and_context(get_theme_style())
    envelope = board_style_envelope(
        style, None, active_format_locale(), active_time_locale(), "UTC", 0
    )
    view = style.chart_defaults.view
    assert envelope["continuous_width"] == view.continuous_width
    assert envelope["discrete_width"] == view.discrete_width


def test_board_style_envelope_carries_the_board_palette_a_chart_with_none_falls_back_to() -> (
    None
):
    from dbt_charts.core.render.engine import board_style_envelope
    from dbt_charts.core.text.format_d3 import active_format_locale, active_time_locale

    style, context = resolve_style_and_context(get_theme_style())
    envelope = board_style_envelope(
        style, None, active_format_locale(), active_time_locale(), "UTC", 0
    )
    assert envelope["palette"] == context.palette


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


def test_the_width_measure_sizes_an_aware_cell_as_the_board_zone_prints_it() -> None:
    from dbt_charts.core.render import engine

    # 03:00Z on March 1 is February 29 in New York.
    rows = [{"at": datetime.datetime(2024, 3, 1, 3, tzinfo=datetime.UTC)}]
    table = {
        "type": "table",
        "query": "q",
        "style": {"columns": {"at": {"format": "%B"}}},
    }

    def width(zone: str) -> float | None:
        utc = Regional()
        with regional_scope(
            utc.format_locale, utc.time_locale, utc.week_start, ZoneInfo(zone)
        ):
            return engine.measure_table_natural_width(
                _resolved(table, rows), rows, _BOARD_RS
            )

    assert width("America/New_York") != width("UTC")


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
    german = Regional(locale=RegionalLocale(time="de-DE"), time_zone="Europe/Berlin")
    with regional_scope(
        german.format_locale,
        german.time_locale,
        german.week_start,
        german.zone,
    ):
        engine.measure_table_natural_width(_resolved(table, rows), rows, _BOARD_RS)
    assert sent[0]["time_locale"] == german.time_locale
    assert sent[0]["time_zone"] == "Europe/Berlin"


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


def test_a_redefined_number_beats_the_integer_default_in_a_table() -> None:
    """An integer column prints the board's redefined `number`, not the
    whole-number type default, so the board's affix survives."""
    svg = render_board_to_svg(
        """
title: T
style:
  formats:
    number: currency
    currency: latitude
queries:
  q:
    columns: [lat]
    values:
      - [37]
charts:
  t:
    query: q
    type: table
rows:
  - t
"""
    )
    assert '"start">\u00b0</tspan>' in svg


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


_MONTHLY = [
    {"month": f"2024-{m:02d}-01", "revenue": float(v)}
    for m, v in enumerate([10, 12, 11, 15, 14, 18, 17, 21], start=1)
]


@pytest.mark.parametrize("family", ["line", "area"])
def test_a_resolved_annotation_draws_in_the_engine(family: str) -> None:
    chart_def = {
        "type": family,
        "query": "q",
        "x": "month",
        "y": "revenue",
        "annotations": [
            {"type": "band", "x": {"from": "2024-03-01", "to": "2024-05-01"}},
            {"type": "rule", "y": "avg()", "text": "Average"},
            *(
                [{"type": "trend", "fit": "linear()", "interval": "confidence(95)"}]
                if family == "line"
                else []
            ),
        ],
    }
    registry = {"q": ValuesQuery(rows=_MONTHLY)}
    flat = normalize_chart("c1", chart_def, registry, sources={})
    chart = resolve(
        flat,
        _MONTHLY,
        chart_style_context=_BOARD_CTX,
        rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
    )
    svg, _ = render_native(
        chart,
        _MONTHLY,
        width=480.0,
        height=300.0,
        padding=None,
        board_style=_BOARD_RS,
    )
    background = svg.index("annotations-background")
    foreground = svg.index("annotations-foreground")
    # The data stroke is the last line mark; a halo under it paints before the band.
    assert background < svg.rindex('class="mark-line role-mark') < foreground
    assert ">Average</text>" in svg


_POINTS = [
    {"spend": float(s), "signups": float(v)}
    for s, v in zip(range(10, 70, 10), [12, 18, 33, 38, 52, 57], strict=True)
]


@pytest.mark.parametrize("multiples", [None, {"columns": "region"}])
def test_a_resolved_scatter_annotation_draws_in_the_engine(
    multiples: dict[str, str] | None,
) -> None:
    rows = [{**p, "region": g} for g in ("EMEA", "APAC") for p in _POINTS]
    chart_def: dict[str, Any] = {
        "type": "scatter",
        "query": "q",
        "x": "spend",
        "y": "signups",
        "annotations": [
            {"type": "band", "x": {"from": 20, "to": 40}},
            {"type": "rule", "y": "avg()", "text": "Average"},
            {"type": "rule", "x": 75, "text": "Budget"},
            {"type": "trend", "fit": "linear()", "interval": "confidence(95)"},
        ],
    }
    if multiples is not None:
        chart_def["multiples"] = multiples
        # A zero-anchored panel bakes no lower y bound, which small multiples need.
        chart_def["style"] = {"axis_y": {"scale": {"continuous": {"zero": False}}}}
    registry = {"q": ValuesQuery(rows=rows)}
    flat = normalize_chart("c1", chart_def, registry, sources={})
    chart = resolve(
        flat,
        rows,
        chart_style_context=_BOARD_CTX,
        rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
    )
    svg, _ = render_native(
        chart,
        rows,
        width=480.0,
        height=300.0,
        padding=None,
        board_style=_BOARD_RS,
    )
    panels = 1 if multiples is None else 2
    assert svg.count("annotations-background") == panels
    assert svg.count("annotations-foreground") == panels
    assert svg.count(">Average</text>") == panels
    assert svg.count(">Budget</text>") == panels


@pytest.mark.parametrize("date", ["2025-03-01", "2024-08-20", "2025-03-20"])
def test_a_scatter_date_annotation_past_the_data_widens_the_x_axis(date: str) -> None:
    rows = [{**r, "signups": r["revenue"] * 3} for r in _MONTHLY]
    chart_def = {
        "type": "scatter",
        "query": "q",
        "x": "month",
        "y": "signups",
        "annotations": [{"type": "rule", "x": date}],
    }
    flat = normalize_chart("c1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={})
    chart = resolve(
        flat,
        rows,
        chart_style_context=_BOARD_CTX,
        rendered_at=datetime.datetime(2025, 6, 14, tzinfo=datetime.UTC),
    )
    svg, _ = render_native(
        chart, rows, width=480.0, height=300.0, padding=None, board_style=_BOARD_RS
    )
    assert 'aria-roledescription="annotation"' in svg


@pytest.mark.parametrize(
    ("chart", "rule_y"),
    [
        ({"style": {"axis_y": {"scale": {"continuous": {"type": "log"}}}}}, 200),
        (
            {
                "multiples": {"columns": "region", "scale": "independent"},
                "style": {"axis_y": {"scale": {"continuous": {"zero": False}}}},
            },
            1000,
        ),
    ],
    ids=["log-y", "independent-panels"],
)
def test_a_scatter_y_annotation_past_the_data_widens_an_engine_computed_domain(
    chart: dict[str, Any], rule_y: int
) -> None:
    rows = [{**p, "region": g} for g in ("EMEA", "APAC") for p in _POINTS]
    chart_def: dict[str, Any] = {
        "type": "scatter",
        "query": "q",
        "x": "spend",
        "y": "signups",
        "annotations": [{"type": "rule", "y": rule_y}],
        **chart,
    }
    flat = normalize_chart("c1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={})
    resolved = resolve(
        flat,
        rows,
        chart_style_context=_BOARD_CTX,
        rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
    )
    svg, _ = render_native(
        resolved, rows, width=480.0, height=300.0, padding=None, board_style=_BOARD_RS
    )
    assert 'aria-roledescription="annotation"' in svg


@pytest.mark.parametrize(
    "continuous",
    [
        {"type": "sqrt"},
        {"type": "pow", "pow": {"exponent": 2}},
        {"type": "symlog"},
    ],
    ids=["sqrt", "pow", "symlog"],
)
def test_a_scatter_on_bent_axes_draws_annotations(continuous: dict[str, Any]) -> None:
    scale = {"scale": {"continuous": continuous}}
    chart_def: dict[str, Any] = {
        "type": "scatter",
        "query": "q",
        "x": "spend",
        "y": "signups",
        "style": {"axis_x": scale, "axis_y": scale},
        "annotations": [
            {"type": "band", "x": {"from": 20, "to": 40}},
            {"type": "rule", "y": 80, "text": "Target"},
            {"type": "trend", "fit": "linear()", "interval": "confidence(95)"},
        ],
    }
    flat = normalize_chart(
        "c1", chart_def, {"q": ValuesQuery(rows=_POINTS)}, sources={}
    )
    resolved = resolve(
        flat,
        _POINTS,
        chart_style_context=_BOARD_CTX,
        rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
    )
    svg, _ = render_native(
        resolved,
        _POINTS,
        width=480.0,
        height=300.0,
        padding=None,
        board_style=_BOARD_RS,
    )
    assert ">Target</text>" in svg


@pytest.mark.parametrize("multiples", [None, {"columns": "region"}])
@pytest.mark.parametrize(
    ("x", "y", "annotations"),
    [
        (
            "spend",
            "team",
            [
                {"type": "band", "y": "B", "text": "Focus"},
                {"type": "rule", "x": "avg()", "text": "Average"},
            ],
        ),
        (
            "team",
            "signups",
            [
                {"type": "band", "x": "B", "text": "Focus"},
                {"type": "rule", "y": "avg()", "text": "Average"},
            ],
        ),
    ],
    ids=["category-y", "category-x"],
)
def test_a_dot_plot_draws_category_annotations(
    x: str,
    y: str,
    annotations: list[dict[str, Any]],
    multiples: dict[str, str] | None,
) -> None:
    rows = [
        {**p, "team": t, "region": g}
        for g in ("EMEA", "APAC")
        for p, t in zip(_POINTS, "AABBCC", strict=True)
    ]
    chart_def: dict[str, Any] = {
        "type": "scatter",
        "query": "q",
        "x": x,
        "y": y,
        "annotations": annotations,
    }
    if multiples is not None:
        chart_def["multiples"] = multiples
        measure_axis = "axis_x" if y == "team" else "axis_y"
        chart_def["style"] = {measure_axis: {"scale": {"continuous": {"zero": False}}}}
    flat = normalize_chart("c1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={})
    resolved = resolve(
        flat,
        rows,
        chart_style_context=_BOARD_CTX,
        rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
    )
    svg, _ = render_native(
        resolved, rows, width=480.0, height=300.0, padding=None, board_style=_BOARD_RS
    )
    panels = 1 if multiples is None else 2
    assert svg.count(">Focus</text>") == panels
    assert svg.count(">Average</text>") == panels


def test_a_shared_log_y_scale_stays_shared_when_one_panel_has_a_trend() -> None:
    rows = [
        {"spend": float(x), "signups": float(y), "region": g}
        for g, ys in (
            ("EMEA", [20, 25, 30, 35, 40, 45]),
            ("APAC", [900, 925, 940, 965, 980, 999]),
        )
        for x, y in zip(range(10, 70, 10), ys, strict=True)
    ]
    chart_def: dict[str, Any] = {
        "type": "scatter",
        "query": "q",
        "x": "spend",
        "y": "signups",
        "multiples": {"columns": "region"},
        "style": {"axis_y": {"scale": {"continuous": {"type": "log"}}}},
        "annotations": [
            {"type": "trend", "fit": "linear()", "interval": "confidence(95)"}
        ],
    }
    flat = normalize_chart("c1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={})
    resolved = resolve(
        flat,
        rows,
        chart_style_context=_BOARD_CTX,
        rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
    )
    svg, _ = render_native(
        resolved, rows, width=480.0, height=300.0, padding=None, board_style=_BOARD_RS
    )
    labels = re.findall(r'text-anchor="end"[^>]*>([^<]+)</text>', svg)
    ladder = labels[: labels.index(labels[0], 1)]
    assert labels[len(ladder) : 2 * len(ladder)] == ladder


def test_rule_text_above_a_facet_panel_reserves_room_for_it() -> None:
    rows = [{**p, "region": g} for g in ("EMEA", "APAC") for p in _POINTS]

    def first_plot_top(rule: dict[str, Any]) -> float:
        chart_def: dict[str, Any] = {
            "type": "scatter",
            "query": "q",
            "x": "spend",
            "y": "signups",
            "multiples": {"columns": "region"},
            "style": {"axis_y": {"scale": {"continuous": {"zero": False}}}},
            "annotations": [rule],
        }
        flat = normalize_chart(
            "c1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={}
        )
        chart = resolve(
            flat,
            rows,
            chart_style_context=_BOARD_CTX,
            rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
        )
        svg, _ = render_native(
            chart, rows, width=480.0, height=300.0, padding=None, board_style=_BOARD_RS
        )
        return float(re.search(r'<g transform="translate\([^,]+,([^)]+)\)', svg)[1])

    bare = first_plot_top({"type": "rule", "x": 75})
    labeled = first_plot_top({"type": "rule", "x": 75, "text": "Budget"})
    assert labeled > bare


def test_a_scatter_on_a_date_x_draws_date_annotations() -> None:
    rows = [{**r, "signups": r["revenue"] * 3} for r in _MONTHLY]
    chart_def = {
        "type": "scatter",
        "query": "q",
        "x": "month",
        "y": "signups",
        "annotations": [
            {"type": "band", "x": {"from": "2024-03-01", "to": "2024-05-01"}},
            {"type": "rule", "x": "2024-06-01", "text": "Launch"},
        ],
    }
    flat = normalize_chart("c1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={})
    chart = resolve(
        flat,
        rows,
        chart_style_context=_BOARD_CTX,
        rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
    )
    svg, _ = render_native(
        chart, rows, width=480.0, height=300.0, padding=None, board_style=_BOARD_RS
    )
    assert svg.index("annotations-background") < svg.index("annotations-foreground")
    assert ">Launch</text>" in svg


def test_rule_text_above_a_rail_chart_reserves_top_margin() -> None:
    rows = [
        {"month": f"2024-{m:02d}-01", "region": region, "revenue": float(m * k)}
        for m in range(1, 7)
        for region, k in (("EMEA", 1), ("APAC", 2))
    ]
    chart_def = {
        "type": "line",
        "query": "q",
        "x": "month",
        "y": "revenue",
        "color": "region",
        "annotations": [{"type": "rule", "x": "2024-03-01", "text": "Launch"}],
    }
    flat = normalize_chart("c1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={})
    chart = resolve(
        flat,
        rows,
        chart_style_context=_BOARD_CTX,
        rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
    )
    svg, _ = render_native(
        chart, rows, width=480.0, height=300.0, padding=None, board_style=_BOARD_RS
    )
    plot_top = float(re.search(r'<g transform="translate\([^,]+,([^)]+)\)', svg)[1])
    text = re.search(
        r'<text[^>]* y="([^"]+)"[^>]* font-size="([^"]+)"[^>]*>Launch<', svg
    )
    assert text is not None
    assert plot_top + float(text[1]) - float(text[2]) >= 0


def test_an_annotation_the_engine_draws_nothing_for_is_recorded() -> None:
    rows = [{"month": f"2024-{m:02d}-01", "revenue": float(m)} for m in range(1, 7)]
    chart_def = {
        "type": "line",
        "query": "q",
        "x": "month",
        "y": "revenue",
        "style": {"axis_y": {"scale": {"continuous": {"type": "log"}}}},
        "annotations": [{"type": "rule", "y": 1000}],
    }
    flat = normalize_chart("c1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={})
    chart = resolve(
        flat,
        rows,
        chart_style_context=_BOARD_CTX,
        rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
    )
    with collect_annotation_nothing_drawn() as drawn_nothing:
        render_native(
            chart, rows, width=480.0, height=300.0, padding=None, board_style=_BOARD_RS
        )
    assert list(drawn_nothing) == ["c1"]
    assert list(drawn_nothing["c1"]) == [0]


_REGIONS: list[dict[str, Any]] = [
    {"region": r, "revenue": float(v)}
    for r, v in (("East", 10), ("West", 14), ("North", 8))
]

_SIZES: list[dict[str, Any]] = [
    {"size": float(s), "revenue": float(v)}
    for s, v in ((10, 5), (20, 9), (30, 7), (40, 12), (50, 8))
]


@pytest.mark.parametrize(
    ("rows", "x", "style", "annotations"),
    [
        (_MONTHLY, "month", {}, [{"type": "band", "x": "current(month)"}]),
        (
            _REGIONS,
            "region",
            {"orientation": "horizontal"},
            [{"type": "band", "x": "West"}],
        ),
        (_SIZES, "size", {}, [{"type": "band", "x": {"from": 15, "to": 35}}]),
    ],
    ids=["date", "horizontal", "numeric"],
)
def test_a_resolved_annotation_draws_on_a_bar(
    rows: list[dict[str, Any]],
    x: str,
    style: dict[str, Any],
    annotations: list[dict[str, Any]],
) -> None:
    chart_def = {
        "type": "bar",
        "query": "q",
        "x": x,
        "y": "revenue",
        "style": style,
        "annotations": [*annotations, {"type": "rule", "y": 12, "text": "Quota"}],
    }
    flat = normalize_chart("c1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={})
    chart = resolve(
        flat,
        rows,
        chart_style_context=_BOARD_CTX,
        rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
    )
    svg, _ = render_native(
        chart, rows, width=480.0, height=300.0, padding=None, board_style=_BOARD_RS
    )
    assert svg.index("annotations-background") < svg.index("annotations-foreground")
    assert ">Quota</text>" in svg


_US_STATES_URL = "bundled:us-10m.json"
_GEOSHAPE = {"type": "map", "query": "q", "geo": {"source": "us-states"}}
_POINT_MAP = {
    "type": "point_map",
    "query": "q",
    "latitude": "lat",
    "longitude": "lon",
    "projection": "albersUsa",
    "basemap": {"source": "us-states"},
}
_POINT_ROWS = [{"lat": 40.7, "lon": -74.0}]


def _payload_of(
    chart_def: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    chart = _resolved(chart_def, rows)
    return build_chart_payload(
        chart,
        {chart.query_name: rows},
        width=600.0,
        height=400.0,
        padding=None,
        board_style=_BOARD_RS,
        board_width=_BOARD_WIDTH,
        variables={},
        title_shift=NO_TITLE_SHIFT,
        is_placeholder=False,
    )


@pytest.mark.parametrize(
    ("chart_def", "rows"), [(_GEOSHAPE, []), (_POINT_MAP, _POINT_ROWS)]
)
def test_a_geo_chart_payload_carries_its_document_text_under_the_source_url(
    chart_def: dict[str, Any], rows: list[dict[str, Any]]
) -> None:
    geometries = _payload_of(chart_def, rows)["geometries"]
    assert set(geometries) == {_US_STATES_URL}
    assert json.loads(geometries[_US_STATES_URL])["type"] == "Topology"


def test_a_non_geo_chart_payload_carries_no_geometries() -> None:
    assert _payload_of(_KPI, _REVENUE)["geometries"] is None


def test_a_point_map_without_a_basemap_loads_no_geometry() -> None:
    chart_def = {k: v for k, v in _POINT_MAP.items() if k != "basemap"}
    assert _payload_of(chart_def, _POINT_ROWS)["geometries"] is None


@pytest.mark.parametrize(
    ("chart_def", "rows"), [(_GEOSHAPE, []), (_POINT_MAP, _POINT_ROWS)]
)
def test_the_engine_renders_a_bundled_geo_chart_through_the_python_payload(
    chart_def: dict[str, Any], rows: list[dict[str, Any]]
) -> None:
    svg, _ = _native(chart_def, rows, width=600.0, height=400.0)
    assert svg.lstrip().startswith("<svg")
