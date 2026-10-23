"""The words a board generates print from its language's catalog, in every
group the catalog covers: controls, tooltips and spans, time axes, units,
humanized names, letter case, theme text and placeholder data."""

from __future__ import annotations

import json
import re

import pytest
import vl_convert

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.controls import interactive_controls
from dbt_charts.core.render.placeholder import generate_placeholder_data
from dbt_charts.core.render.svg_cache import svg_cache_key
from dbt_charts.core.render.variables_resolve import (
    format_daterange_label,
    format_variable_display_value,
)
from dbt_charts.core.text.case import apply_case
from dbt_charts.core.text.format_d3 import board_word, language_scope
from dbt_charts.core.text.humanize import slug_to_text
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_NATIVE,
    PredefinedNumberFormat,
)


def _vega_text(
    expr: str, *, format_locale: dict[str, object] | None = None, **datum: object
) -> str:
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [datum]},
        "transform": [{"calculate": expr, "as": "t"}],
        "mark": {"type": "text"},
        "encoding": {"text": {"field": "t"}},
    }
    svg = vl_convert.vegalite_to_svg(spec, format_locale=format_locale)
    return "".join(re.findall(r">([^<]*)</text>", svg))


def test_humanized_names_speak_the_board_language() -> None:
    assert slug_to_text("avg_orders_cnt") == "Avg orders (Count)"
    with language_scope("de"):
        assert slug_to_text("avg_orders_cnt") == "Ø orders (Anzahl)"
        assert slug_to_text("revenue_usd") == "Revenue ($)"


@pytest.mark.parametrize(
    ("language", "text", "title"),
    [
        ("en", "revenue per region", "Revenue per Region"),
        ("de", "umsatz pro Region", "Umsatz pro Region"),
        ("ja", "売上 by region", "売上 by region"),
    ],
)
def test_title_case_is_the_languages_own_rule(
    language: str, text: str, title: str
) -> None:
    with language_scope(language):
        assert apply_case(text, "title") == title
        assert apply_case(text, "upper") == text.upper()


def test_control_values_read_in_the_board_language() -> None:
    select = Variable(input="select")
    with language_scope("de"):
        assert format_variable_display_value(select, None) == "Alle"
        assert (
            format_variable_display_value(Variable(input="checkbox"), False) == "Nein"
        )
        assert format_variable_display_value(Variable(input="checkbox"), "yes") == "Ja"
        assert format_variable_display_value(
            Variable(input="daterange"), ["2026-03-05", None]
        ).startswith("Ab 5.")
        assert format_variable_display_value(Variable(input="date"), "") == (
            "Beliebiges Datum"
        )
        assert format_daterange_label("2026-03-05", "2026-03-09").startswith("5.–9.")
    assert format_variable_display_value(select, None) == "All"


def test_the_controls_strip_carries_its_words_for_the_page() -> None:
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.variables_resolve import resolve_controls
    from dbt_charts.core.render.variables_strip import render_variables_strip_svg

    style = resolve_style(get_theme_style(get_default_theme_name()))
    controls = resolve_controls(
        {"span": Variable(input="daterange")}, {}, None, style.variables
    )
    with language_scope("de"), interactive_controls(True):
        svg, _ = render_variables_strip_svg(
            controls, 600.0, style, "start", variables_path="variables"
        )
    words = json.loads(
        re.search(r'data-dbt-language="([^"]*)"', svg).group(1).replace("&quot;", '"')
    )
    assert words["today"] == "Heute" and words["apply"] == "Anwenden"
    assert words["calendar_day"] == "{weekday}, {day}. {month} {year}"
    with interactive_controls(False):
        static, _ = render_variables_strip_svg(
            controls, 600.0, style, "start", variables_path="variables"
        )
    assert "data-dbt-language" not in static
    with language_scope("ja"), interactive_controls(False):
        japanese, _ = render_variables_strip_svg(
            controls, 600.0, style, "start", variables_path="variables"
        )
    assert "Span\uff1a" in japanese


# 0.95 months to the bit (2498310 s / 86400 / 30.4375): it prints "0.9", where
# rounding the value itself would read 1.0 and pick the singular.
_MONTH_095 = 2498310 / 86400


def test_unit_letters_and_words_come_from_the_catalog() -> None:
    native = PREDEFINED_NATIVE
    assert native[PredefinedNumberFormat.duration].python(3725) == ("1h 2m", "")
    assert native[PredefinedNumberFormat.bytes].python(1_530_000) == ("1.53", " MB")
    with language_scope("fr"):
        assert native[PredefinedNumberFormat.duration].python(3725) == ("1h 2min", "")
        assert native[PredefinedNumberFormat.bytes].python(1_530_000) == ("1.53", " Mo")
        vega = native[PredefinedNumberFormat.bytes].vega("1530000")
        assert _vega_text(vega) == "1.53 Mo"


def test_the_theme_text_is_baked_into_the_resolved_style_in_the_boards_language() -> (
    None
):
    theme = get_theme_style(get_default_theme_name())
    assert theme.footer.text == theme.timestamp.format == "auto"
    assert theme.placeholder.overlay.text == "auto"

    english = resolve_style(theme)
    assert english.footer.text == "made with dbt Charts"
    assert english.timestamp.format == "Data as of %H:%M %Z on %-d %b %Y"
    assert english.placeholder.overlay.text == "add data"

    german = resolve_style(theme, language="de")
    assert german.footer.text == "erstellt mit dbt Charts"
    assert german.timestamp.format == "Daten vom %-d %b %Y, %H:%M %Z"
    assert german.placeholder.overlay.text == "Daten hinzufügen"


def test_authored_theme_text_wins_verbatim_in_any_language() -> None:
    patch = StylePatch.model_validate(
        {
            "footer": {"text": "my footer"},
            "timestamp": {"format": "%H:%M %Z"},
            "placeholder": {"overlay": {"text": "nothing yet"}},
        }
    )
    style = resolve_style(
        get_theme_style(get_default_theme_name()), patch, language="de"
    )
    assert style.footer.text == "my footer"
    assert style.timestamp.format == "%H:%M %Z"
    assert style.placeholder.overlay.text == "nothing yet"


def test_a_german_board_resolves_german_theme_text_from_its_regional() -> None:
    result = compile("title: t\nregional:\n  locale:\n    time: de-DE\n")
    assert result.success
    assert result.board.resolved_style.footer.text == "erstellt mit dbt Charts"


def test_the_svg_cache_key_tells_two_board_languages_apart() -> None:
    theme = get_theme_style(get_default_theme_name())

    def key(language: str) -> str:
        return svg_cache_key(
            {"mark": "point"},
            output_format="svg",
            width=100.0,
            height=100.0,
            is_placeholder=True,
            resolved_style=resolve_style(theme, language=language),
            link_context=None,
        )

    assert key("de") != key("en")


def test_placeholder_data_is_translated() -> None:
    with language_scope("de"):
        bar = generate_placeholder_data("bar")
        table = generate_placeholder_data("table")
    assert next(iter(bar[0].values())) == "Kategorie A"
    assert table[0]["name"] == "Beispielelement 1"
    assert table[1]["status"] == "Ausstehend"


def test_the_footer_brand_has_no_gap_where_the_text_has_no_space() -> None:
    from dbt_charts.core.compile.sizing import footer_text_width
    from dbt_charts.core.font_measure import get_font_measurer

    ja = resolve_style(get_theme_style(get_default_theme_name()), language="ja").footer
    family = "Inter"
    glued = footer_text_width(ja, family)
    spaced = footer_text_width(
        ja.model_copy(update={"text": "dbt Charts で作成"}), family
    )
    gap = get_font_measurer(family).measure(" ", float(ja.font.size))
    assert ja.text == "dbt Chartsで作成"
    assert spaced - glued == pytest.approx(gap)


_DONUT = "type: pie\n{pad}query: q\n{pad}theta: val\n{pad}color: cat\n{pad}style:\n{pad}  inner_radius: 0.6\n"


@pytest.mark.parametrize(
    "layout",
    [
        "charts:\n  donut:\n    " + _DONUT.format(pad="    ") + "rows: [donut]\n",
        "rows:\n  - donut:\n      " + _DONUT.format(pad="      "),
        "rows:\n  - " + _DONUT.format(pad="    "),
    ],
    ids=["registry", "named_inline", "bare_inline"],
)
def test_a_donut_total_label_reads_in_the_boards_language(layout: str) -> None:
    svg = _rendered_svg(
        "regional:\n  language: de\nqueries:\n"
        "  q:\n    type: values\n    rows:\n      - {val: 1, cat: A}\n" + layout
    )
    assert "Gesamt Val" in svg


@pytest.mark.parametrize(
    ("language", "word", "count", "text"),
    [
        ("de", "series.count", 3, "3 Serien"),
        ("de", "series.count", 1, "1 Serie"),
        ("de", "tooltip.series", None, "Serie"),
        ("de", "tooltip.latitude", None, "Breitengrad"),
        ("ja", "tooltip.longitude", None, "経度"),
    ],
)
def test_generated_words_speak_the_board_language(
    language: str, word: str, count: int | None, text: str
) -> None:
    with language_scope(language):
        assert board_word(word, count) == text


def test_keyed_words_fill_their_placeholders_in_the_board_language() -> None:
    with language_scope("ja"):
        assert board_word("controls.field_label", label="x") == "x："
    with language_scope("de"):
        assert board_word("placard.image_error", message="m") == "Bildfehler: m"


def _rendered_svg(board_yaml: str) -> str:
    from pathlib import Path

    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.adapters import build_adapter_registry
    from dbt_charts.core.project import InMemoryBoard

    from ..conftest import InMemoryProject

    project = InMemoryProject(Path("/proj"), {})
    result = render_dashboard(
        board=InMemoryBoard(board_yaml, path=None),
        variables=None,
        project=project,
        adapter_registry=build_adapter_registry(project, read_only=False),
        result_cache=None,
        format="svg",
    )
    assert isinstance(result.data, str), result.validation_errors
    return result.data


_WIDE_SCATTER = """regional:
  language: de
queries:
  q:
    type: values
    rows:
      - {team: A, a: 1, b: 2}
      - {team: B, a: 3, b: 4}
charts:
  c:
    type: scatter
    query: q
    x: team
    y: [a, b]
rows:
  - c
"""


def test_a_wide_scatter_names_its_series_in_the_board_language() -> None:
    svg = _rendered_svg(_WIDE_SCATTER)
    assert "Serie" in svg and "Series" not in svg


def test_an_image_error_reads_in_the_board_language() -> None:
    svg = _rendered_svg("regional:\n  language: de\ntext: '![l](missing.svg)'\n")
    assert "Bildfehler" in svg and "Image error" not in svg


def test_point_map_tooltip_titles_read_in_the_board_language() -> None:
    from dbt_charts.core.render.chart.geo_tooltip import build_point_tooltip_fields

    with language_scope("de"):
        fields = build_point_tooltip_fields(
            [], lat_field="lat", lng_field="lng", tooltip_format=",", field_labels={}
        )
    assert [field["title"] for field in fields] == ["Breitengrad", "Längengrad"]


def test_an_overlay_legend_with_comma_series_counts_them_in_the_board_language() -> (
    None
):
    svg = _rendered_svg(
        """regional:
  language: de
queries:
  q:
    type: values
    rows:
      - {status: x, revenue: 1, category: "Alpha, Inc"}
      - {status: y, revenue: 2, category: "Beta"}
charts:
  c:
    type: bar
    query: q
    x: status
    y: revenue
    color: category
    layers:
      - type: line
        query: q
        x: status
        y: revenue
        label: Trend
rows:
  - c
"""
    )
    assert "Serien" in svg and "series" not in svg


def test_placeholder_names_read_in_the_board_language() -> None:
    with language_scope("de"):
        pie = generate_placeholder_data("pie")
        boxplot = generate_placeholder_data("boxplot")
        heatmap = generate_placeholder_data("heatmap")
    assert pie[0]["category"] == "Segment A"
    assert boxplot[0]["category"] == "Gruppe A"
    assert (heatmap[0]["y"], heatmap[0]["x"]) == ("Zeile A", "Sp. 1")


def test_humanized_ratio_and_quantity_read_in_the_board_language() -> None:
    with language_scope("de"):
        assert slug_to_text("win_ratio") == "Win (Verhältnis)"
        assert slug_to_text("order_qty") == "Order (Menge)"


def test_a_date_range_variable_reads_its_open_ends_in_the_board_language() -> None:
    from dbt_charts.core.render.variables_resolve import format_variable_display_value

    daterange = Variable(input="daterange")
    with language_scope("de"):
        assert format_variable_display_value(daterange, None) == "Alle Daten"
        assert format_variable_display_value(daterange, [None, "2026-03-05"]) == (
            "Bis 5. Mar 2026"
        )


def test_percentage_point_deltas_and_pie_headings_read_in_the_board_language() -> None:
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )

    delta = PREDEFINED_NATIVE[PredefinedNumberFormat.percentage_points_delta]
    chart = PieChart(id="p", type="pie", theta="value", color="series")
    data = [
        {"series": "Big", "value": 63},
        {"series": "Rest", "value": 30},
        {"series": "Small", "value": 6},
        {"series": "Tiny", "value": 1},
    ]
    with language_scope("de"):
        assert delta.python(1.5) == ("+1.5", " Pkt.")
        resolved = resolve(
            chart, data, resolve_chart_style_context(get_theme_style()), width=600.0
        )
    assert resolved.attached_heading == "Zu klein für eine Beschriftung"


def test_a_chart_wrapper_announces_itself_in_the_board_language() -> None:
    svg = _rendered_svg(_WIDE_SCATTER)
    assert 'aria-roledescription="Diagramm"' in svg
    assert 'aria-roledescription="chart"' not in svg
