"""``regional`` sets the board's d3 locale; ``regional.currency`` swaps the symbol
its currency template prints, at the locale's placement."""

from __future__ import annotations

import re
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import pytest
import yaml

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.format_value import format_kpi_parts, format_value
from dbt_charts.core.compile.merge import merge_patches
from dbt_charts.core.compile.models.board.patch import BoardPatch
from dbt_charts.core.compile.models.primitives import (
    FormatConfig,
    FormatTable,
    NumberLocale,
    Regional,
    RegionalLocale,
)
from dbt_charts.core.text.currencies import CURRENCIES, CurrencyCode
from dbt_charts.core.text.format_d3 import NumberLocaleName, regional_scope

from .._svg_render import render_board_to_svg

_NBSP = "\u00a0"


@contextmanager
def _regional(
    locale: NumberLocaleName | NumberLocale = "en-US",
    currency: CurrencyCode | None = None,
) -> Generator[FormatTable]:
    """The board's locale in scope, and its format table."""
    regional = Regional(locale=RegionalLocale(number=locale), currency=currency)
    with regional_scope(
        regional.format_locale,
        regional.time_locale,
        regional.week_start,
        regional.zone,
    ):
        yield FormatTable(aliases={}, currency=currency)


def _money(
    value: float,
    name: str = "currency_full",
    locale: NumberLocaleName = "en-US",
    currency: CurrencyCode | None = None,
) -> str:
    with _regional(locale, currency) as formats:
        return format_value(value, name, formats)


def _board(
    chart: dict[str, Any],
    regional: dict[str, Any] | None,
    rows: list[list[Any]] | None = None,
) -> str:
    values = rows or [["A", 1234567, 9], ["B", 2345678, 8]]
    board: dict[str, Any] = {
        "title": "t",
        "style": {},
        "queries": {
            "q": {"columns": ["channel", "v", "w"], "values": values},
            "one": {"columns": ["channel", "v", "w"], "values": values[:1]},
        },
        "charts": {"c": chart},
        "rows": ["c"],
    }
    if regional is not None:
        board["regional"] = regional
    return yaml.safe_dump(board, sort_keys=False, allow_unicode=True)


def test_an_unset_regional_is_en_us_with_its_own_currency() -> None:
    result = compile_board(_board({"type": "table", "query": "q"}, None))
    assert result.success and result.board is not None
    assert result.board.regional == Regional(currency=None)


def test_meta_and_board_regional_merge_field_by_field() -> None:
    meta = BoardPatch.model_validate({"regional": {"currency": "EUR"}})
    board = BoardPatch.model_validate({"regional": {"locale": {"number": "fr-FR"}}})
    merged = merge_patches(meta, board)
    assert merged.regional.model_dump() == {
        "locale": {"number": "fr-FR", "time": None},
        "currency": "EUR",
        "language": None,
        "time_zone": None,
    }


@pytest.mark.parametrize(
    ("locale", "currency", "expected"),
    [
        ("en-US", None, "$1,234.50"),
        ("de-DE", None, f"1.234,50{_NBSP}€"),
        ("en-US", "EUR", "€1,234.50"),
        ("de-DE", "USD", f"1.234,50{_NBSP}$"),
        ("en-US", "CHF", f"CHF{_NBSP}1,234.50"),
        ("de-CH", "USD", f"1'234,50{_NBSP}$"),
    ],
)
def test_the_locale_places_any_currency(
    locale: NumberLocaleName, currency: CurrencyCode | None, expected: str
) -> None:
    assert _money(1234.5, "currency_full", locale, currency) == expected


def test_every_money_name_and_an_authored_dollar_print_the_currency() -> None:
    with _regional("de-DE", "USD") as formats:
        assert format_value(1234.5, "currency_whole", formats) == f"1.235{_NBSP}$"
        assert format_value(1234567, "currency", formats) == f"1,23{_NBSP}M{_NBSP}$"
        assert format_value(1234.5, "$,.0f", formats) == f"1.235{_NBSP}$"


def test_under_one_swaps_to_plain_digits() -> None:
    assert _money(0.67, "currency", "en-US", "EUR") == "€0.67"
    assert _money(-0.67, "currency", "de-DE") == f"−0,67{_NBSP}€"


def test_jpy_has_no_minor_unit() -> None:
    assert _money(1234.5, "currency_full", "en-US", "JPY") == "¥1,235"
    assert _money(1234.5, "currency_whole", "en-US", "JPY") == "¥1,235"


@pytest.mark.parametrize("code", sorted(CURRENCIES))
def test_every_supported_code_formats_every_money_name(code: CurrencyCode) -> None:
    for name in ("currency", "currency_whole", "currency_full"):
        text = _money(1234.5, name, "en-US", code)
        assert CURRENCIES[code].symbol in text
        assert ("$" in text) == ("$" in CURRENCIES[code].symbol)


def test_an_affix_composes_around_the_currency() -> None:
    with _regional("en-US", "EUR"):
        inline = FormatConfig(spec="currency", prefix="US ")
        assert format_value(12400, inline) == "US €12.4K"
    with _regional("de-DE"):
        net = FormatConfig(spec="currency_whole", suffix=" net")
        assert format_value(1234.5, net) == f"1.235{_NBSP}€ net"


def test_a_board_redefinition_prints_what_it_wrote() -> None:
    with _regional("en-US", "EUR"):
        dollar = FormatTable(aliases={"currency": "$,.0f"}, currency="EUR")
        assert format_value(1234.5, "currency", dollar) == "€1,235"
        own = {"currency": FormatConfig(spec=".3~s", prefix="£")}
        pound = FormatTable(aliases=own, currency="EUR")
        assert format_value(0.67, "currency", pound) == "£670m"


def test_kpi_parts_paint_the_currency_in_its_lanes() -> None:
    with _regional("de-DE") as formats:
        prefix, _, suffix = format_kpi_parts(1234567, "currency", formats)
        assert prefix == "" and suffix.endswith("€")
        prefix, _, suffix = format_kpi_parts(
            1234567, "currency", formats, anchored=True
        )
        assert prefix == "€" and "€" not in suffix


_CUSTOM: dict[str, Any] = {
    "decimal": ",",
    "thousands": ".",
    "grouping": [3],
    "currency": ["", " EUR"],
    "percent": " %",
}


def test_a_custom_locale_formats_like_d3() -> None:
    locale = NumberLocale.model_validate(_CUSTOM)
    assert locale.minus == "\u2212"
    with _regional(locale) as formats:
        assert format_value(-1234.5, "currency_full", formats) == "−1.234,50 EUR"
        assert format_value(0.25, ".0%", formats) == "25 %"
    with _regional(locale, "GBP") as formats:
        assert format_value(1234.5, "currency_full", formats) == "1.234,50 £"


@pytest.mark.parametrize(
    "regional",
    [
        {"currency": "eur"},
        {"currency": "€"},
        {"locale": {"number": "xx-YY"}},
        {"locale": "de-DE"},
        {"locale": {"time": "pt-PT"}},
        {"locale": {"number": {"decimal": ","}}},
        {"locale": {"number": _CUSTOM | {"grouping": []}}},
        {"locale": {"number": _CUSTOM | {"currency": ["", ""]}}, "currency": "USD"},
    ],
)
def test_an_invalid_regional_is_an_error(regional: dict[str, Any]) -> None:
    result = compile_board(_board({"type": "table", "query": "q"}, regional))
    assert not result.success
    assert "regional" in str(result.errors[0])


@pytest.mark.parametrize(
    "formats",
    [
        {"currency": "currency_whole", "currency_whole": "currency"},
        # A ring the first name only leads into, never back through.
        {
            "currency": "currency_whole",
            "currency_whole": "currency_full",
            "currency_full": "currency_whole",
        },
        # Aliases whose names read as specs, used by a slot.
        {",.0f": ",.2f", ",.2f": ",.0f", "revenue": ",.0f"},
    ],
)
def test_alias_cycles_are_a_compile_error(formats: dict[str, str]) -> None:
    board = yaml.safe_load(_board({"type": "table", "query": "q"}, None))
    board["style"] = {"formats": formats}
    result = compile_board(yaml.safe_dump(board))
    assert not result.success
    assert result.errors[0].code == "ERR-FORMAT-ALIAS-CYCLE"


_FMT = {"format": "currency"}
_BAR = {"type": "bar", "query": "q", "x": "channel", "y": "v"}
_DESTINATIONS: dict[str, dict[str, Any]] = {
    "table": {"type": "table", "query": "q", "style": {"columns": {"v": _FMT}}},
    "kpi_headline": {
        "type": "kpi",
        "query": "one",
        "value": "v",
        "style": {"value": _FMT},
    },
    "kpi_support": {
        "type": "kpi",
        "query": "one",
        "value": "w",
        "support": {"value": "v", "label": "vs", "format": "currency"},
    },
    "bar_axis": _BAR | {"style": {"number_format": "currency"}},
    "line_axis": {"type": "line", "query": "q", "x": "channel", "y": "v"}
    | {"style": {"number_format": "currency"}},
    "mirror_axis": {"type": "line", "query": "q", "x": "channel", "y": "v"}
    | {"style": {"number_format": "currency", "axis_y": {"mirror": True}}},
    "tooltip": _BAR | {"style": {"number_format": "currency"}},
    "mark_label": _BAR
    | {
        "style": {"marks": {"bar": {"labels": {"visible": True, "format": "currency"}}}}
    },
    "support_table": _BAR
    | {"support_table": {"entries": [{"source": "v", "label": "V", **_FMT}]}},
}
_PAINTED = re.compile(r"<text[^>]*>(.*?)</text>|aria-label=\"([^\"]*)\"", re.S)


def _painted(svg: str) -> str:
    # Vega's per-axis summary quotes the raw scale domain, never a formatted value.
    svg = re.sub(r'aria-label="[XY]-axis[^"]*"', "", svg)
    return " ".join(re.sub(r"<[^>]+>", "", a or b) for a, b in _PAINTED.findall(svg))


_REGIONALS: dict[str, tuple[dict[str, Any] | None, str]] = {
    "en-US": (None, "$"),
    "en-US-EUR": ({"currency": "EUR"}, "€"),
    "de-DE": ({"locale": {"number": "de-DE"}}, "€"),
    "sv-SE-USD": ({"locale": {"number": "sv-SE"}, "currency": "USD"}, "$"),
}


@pytest.mark.parametrize("regional", sorted(_REGIONALS))
@pytest.mark.parametrize("name", sorted(_DESTINATIONS))
def test_the_currency_reaches_every_destination(name: str, regional: str) -> None:
    authored, symbol = _REGIONALS[regional]
    text = _painted(render_board_to_svg(_board(_DESTINATIONS[name], authored)))
    assert symbol in text
    assert all(other not in text for other in {"$", "€", "kr"} - {symbol}), text


@pytest.mark.parametrize("name", sorted(_DESTINATIONS))
def test_under_one_swaps_to_plain_digits_in_every_destination(name: str) -> None:
    rows = [["A", 0.67, 1], ["B", 0.42, 2]]
    text = _painted(
        render_board_to_svg(
            _board(_DESTINATIONS[name], {"locale": {"number": "de-DE"}}, rows)
        )
    )
    assert "€" in text
    assert not re.search(r"\d+m\b", text), text
    assert re.search(r"0,[0-9]+", text)


def test_a_board_alias_to_a_money_name_prints_the_currency() -> None:
    board = yaml.safe_load(_board(_DESTINATIONS["table"], {"currency": "EUR"}))
    board["style"]["formats"] = {"money": "currency"}
    board["charts"]["c"]["style"]["columns"]["v"]["format"] = "money"
    text = _painted(render_board_to_svg(yaml.safe_dump(board)))
    assert "€" in text and "$" not in text


def test_a_custom_locale_reaches_vega() -> None:
    chart = _BAR | {"style": {"number_format": "currency_whole"}}
    text = _painted(render_board_to_svg(_board(chart, {"locale": {"number": _CUSTOM}})))
    assert " EUR" in text and "$" not in text, text
    assert re.search(r"\d\.\d{3}", text), text


_PLACEMENT_BOARD = """
title: P
regional:
  locale:
    number: de-DE
queries:
  q:
    type: values
    rows:
      - {{m: 2024-01-01, v: 441200000}}
      - {{m: 2024-02-01, v: 448900000}}
      - {{m: 2024-03-01, v: 456100000}}
  one:
    type: values
    rows:
      - {{v: 1234567}}
charts:
  c:
{chart}
rows:
  - c
"""


def _placement_cells(chart: str) -> list[str]:
    svg = render_board_to_svg(_PLACEMENT_BOARD.format(chart=chart))
    cells = re.findall(r"<text[^>]*>(.*?)</text>", svg, re.S)
    return [re.sub(r"<[^>]+>", "", c) for c in cells if "€" in c]


def test_a_table_column_in_anchors_mode_leads_with_the_currency() -> None:
    cells = _placement_cells(
        "    query: q\n    type: table\n    style:\n      columns:\n"
        "        v:\n          format: currency\n"
    )
    assert cells == ["€441M"]


def test_a_table_column_that_repeats_follows_the_locale() -> None:
    cells = _placement_cells(
        "    query: q\n    type: table\n    style:\n      columns:\n        v:\n"
        "          format:\n            spec: currency\n            repeat: every\n"
    )
    assert cells == [f"441M{_NBSP}€", f"449M{_NBSP}€", f"456M{_NBSP}€"]


def test_an_anchored_support_table_strip_leads_with_the_currency() -> None:
    cells = _placement_cells(
        "    query: q\n    type: bar\n    x: m\n    y: v\n    support_table:\n"
        "      - source: v\n        format: currency\n        label: V\n"
    )
    assert cells == [f"€441{_NBSP}Mio."]


def test_a_kpi_follows_the_locale() -> None:
    cells = _placement_cells(
        "    query: one\n    type: kpi\n    value: v\n    style:\n      value:\n"
        "        format: currency\n"
    )
    assert cells == [f"1,23Mio.{_NBSP}€"]


def test_an_axis_that_anchors_its_unit_leads_with_the_currency() -> None:
    cells = _placement_cells(
        "    query: q\n    type: line\n    x: m\n    y: v\n    style:\n"
        "      number_format: currency\n"
    )
    assert cells and all(c.startswith("€") for c in cells), cells


def test_an_axis_that_repeats_its_unit_follows_the_locale() -> None:
    board = yaml.safe_load(_PLACEMENT_BOARD.format(chart="    query: q"))
    board["queries"]["q"]["rows"] = [
        {"c": "A", "v": 1234.5},
        {"c": "B", "v": 2345.5},
    ]
    board["charts"]["c"] = {
        "query": "q",
        "type": "bar",
        "x": "c",
        "y": "v",
        "style": {"number_format": "currency_whole"},
    }
    svg = render_board_to_svg(yaml.safe_dump(board))
    cells = [c for c in re.findall(r"<text[^>]*>([^<]*)</text>", svg) if "€" in c]
    assert len(cells) > 1 and all(c.endswith(f"{_NBSP}€") for c in cells), cells


def test_a_ruler_axis_that_repeats_its_unit_trails_the_magnitude() -> None:
    board = yaml.safe_load(_PLACEMENT_BOARD.format(chart="    query: q"))
    board["queries"]["q"]["rows"] = [
        {"c": "A", "v": 1234567},
        {"c": "B", "v": 987654},
    ]
    board["charts"]["c"] = {
        "query": "q",
        "type": "bar",
        "x": "c",
        "y": "v",
        "style": {"number_format": "currency"},
    }
    svg = render_board_to_svg(yaml.safe_dump(board))
    cells = [c for c in re.findall(r"<text[^>]*>([^<]*)</text>", svg) if "€" in c]
    assert len(cells) > 1, cells
    assert all(re.fullmatch(rf"[\d.]+{_NBSP}Tsd.{_NBSP}€", c) for c in cells), cells


@pytest.mark.parametrize(("locale", "symbol"), [("en-US", "$"), ("de-DE", "€")])
def test_a_left_axis_support_table_repeats_the_currency(
    locale: str, symbol: str
) -> None:
    chart = (
        "    query: q\n    type: bar\n    x: m\n    y: v\n    style:\n"
        "      axis_y:\n        position: left\n    support_table:\n"
        "      - source: v\n        format: currency_whole\n        label: V\n"
    )
    board = _PLACEMENT_BOARD.format(chart=chart).replace("de-DE", locale)
    cells = [
        re.sub(r"<[^>]+>", "", c)
        for c in re.findall(r"<text[^>]*>(.*?)</text>", render_board_to_svg(board))
        if re.search(r"\d{3}[,.]\d{3}", c)
    ]
    assert len(cells) == 3 and all(symbol in c for c in cells), cells


def _nested(inner_regional: str) -> str:
    return f"""
title: Root
regional:
  currency: EUR
queries:
  q:
    type: values
    rows:
      - {{v: 1234.5}}
charts:
  c:
    query: q
    type: kpi
    value: v
    style:
      value:
        format: currency_full
rows:
  - title: Inner
{inner_regional}    rows:
      - c
"""


def test_a_nested_board_takes_its_roots_regional() -> None:
    result = compile_board(_nested(""))
    assert result.success and result.board is not None
    inner = result.board.layout.items[0].board
    assert inner is not None and inner.regional.currency == "EUR"
    assert "€" in render_board_to_svg(_nested(""))


def test_a_nested_board_matching_its_root_is_accepted() -> None:
    assert compile_board(_nested("    regional:\n      currency: EUR\n")).success


def test_a_nested_board_with_another_regional_is_an_error() -> None:
    result = compile_board(_nested("    regional:\n      currency: USD\n"))
    assert not result.success
    assert result.errors[0].code == "ERR-REGIONAL-NESTED-MISMATCH"


def test_a_comma_decimal_locale_pads_trimmed_digits_on_their_decimal_mark() -> None:
    board = yaml.safe_load(_PLACEMENT_BOARD.format(chart="    query: q"))
    board["queries"]["q"]["rows"] = [
        {"m": "2024-01-01", "v": 1234.5},
        {"m": "2024-02-01", "v": 2.75},
    ]
    trimmed = {"format": ",.2~f"}
    board["charts"]["c"] = {
        "query": "q",
        "type": "table",
        "style": {"columns": {"v": trimmed}},
    }
    table = _painted(render_board_to_svg(yaml.safe_dump(board)))
    assert "1.234,5" in table and "2,75" in table, table
    board["charts"]["c"] = {
        "query": "q",
        "type": "bar",
        "x": "m",
        "y": "v",
        "support_table": [{"source": "v", "label": "V", **trimmed}],
    }
    strip = _painted(render_board_to_svg(yaml.safe_dump(board)))
    assert "1.234,5" in strip and "2,75" in strip, strip


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("currency_whole", rf"[\d.]+{_NBSP}€"),
        ("currency", rf"[\d,]+{_NBSP}Mio\.{_NBSP}€"),
    ],
)
def test_a_support_strip_that_repeats_follows_the_locale(
    name: str, expected: str
) -> None:
    cells = _placement_cells(
        "    query: q\n    type: bar\n    x: m\n    y: v\n    support_table:\n"
        f"      - source: v\n        format:\n          spec: {name}\n"
        "          repeat: every\n        label: V\n"
    )
    assert len(cells) == 3 and all(re.fullmatch(expected, c) for c in cells), cells


@pytest.mark.parametrize("locale", ["en-US", "de-DE"])
def test_an_si_column_without_a_shared_tier_pads_in_any_locale(
    locale: NumberLocaleName,
) -> None:
    from dbt_charts.core.compile.resolve.chart._table import (
        _unscaled_decimal_pad_table,
    )

    with _regional(locale) as formats:
        table = _unscaled_decimal_pad_table(
            [2.5, 12.75, 3.0], ",.3~s", "Inter", None, formats
        )
    assert len(table) == 3


def test_a_negative_anchored_tick_leads_with_the_currency_then_the_sign() -> None:
    board = yaml.safe_load(_PLACEMENT_BOARD.format(chart="    query: q"))
    board["queries"]["q"]["rows"] = [
        {"m": "2024-01-01", "v": -441200000},
        {"m": "2024-02-01", "v": -248900000},
        {"m": "2024-03-01", "v": -156100000},
    ]
    board["charts"]["c"] = {
        "query": "q",
        "type": "line",
        "x": "m",
        "y": "v",
        "style": {"number_format": "currency"},
    }
    svg = render_board_to_svg(yaml.safe_dump(board))
    cells = [c for c in re.findall(r"<text[^>]*>([^<]*)</text>", svg) if "€" in c]
    assert cells == [c for c in cells if c.startswith("€−")], cells
    assert cells, svg


@pytest.mark.parametrize("locale", ["en-US", "de-DE"])
def test_a_currency_column_mixing_si_tiers_is_not_padded(
    locale: NumberLocaleName,
) -> None:
    from dbt_charts.core.compile.resolve.chart._table import (
        _unscaled_decimal_pad_table,
    )

    with _regional(locale) as formats:
        table = _unscaled_decimal_pad_table(
            [12100, 900, 1.5], "$,.3~s", "Inter", None, formats
        )
    assert table == ()


def test_a_table_lane_keeps_the_locales_currency_gap() -> None:
    with _regional("de-DE") as formats:
        every = FormatConfig(spec="currency_whole", repeat="every")
        parts = format_kpi_parts(1500000, every, formats, default_number=True)
    assert parts == ("", "1.500.000", f"{_NBSP}€")
    custom = NumberLocale.model_validate(_CUSTOM)
    with _regional(custom) as formats:
        money = format_kpi_parts(1500000, every, formats, default_number=True)
        share = format_kpi_parts(0.184, ".1%", formats, default_number=True)
    assert money[2] == " EUR" and share[2] == " %"
