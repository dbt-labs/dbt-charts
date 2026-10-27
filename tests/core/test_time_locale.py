"""``regional.locale.time`` names a board's months, days and periods."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest
import vl_convert as vlc
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.primitives import (
    Regional,
    RegionalLocale,
    TimeLocale,
)
from dbt_charts.core.render.variables_resolve import (
    format_date_label,
    format_daterange_label,
)
from dbt_charts.core.text.format_d3 import (
    TimeLocaleName,
    portable_strftime,
    regional_scope,
    time_locale_definition,
)

from .._paths import DBT_CHARTS_DIR
from ._svg_render import render_board_to_svg

_INSTANTS = [
    datetime(2023, 5, 17, 13, 4, 5, tzinfo=UTC),
    datetime(2023, 1, 5, 1, 2, 3, tzinfo=UTC),
    datetime(2023, 12, 31, 0, 0, 0, tzinfo=UTC),
]
_ARIA_LABEL_RE = re.compile(r'aria-label="label: (.*?)"')


def _vega_utc_format(instant: datetime, fmt: str, locale: TimeLocaleName) -> str:
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"value": int(instant.timestamp() * 1000)}]},
        "transform": [
            {
                "calculate": f"utcFormat(datum.value, {json.dumps(fmt)})",
                "as": "label",
            }
        ],
        "mark": "text",
        "encoding": {"text": {"field": "label", "type": "nominal"}},
    }
    match = _ARIA_LABEL_RE.search(vlc.vegalite_to_svg(spec, time_format_locale=locale))
    assert match
    return match.group(1)


def test_a_bare_locale_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Regional.model_validate({"locale": "de-DE"})


def test_the_time_locale_must_have_a_d3_time_set() -> None:
    with pytest.raises(ValidationError, match="de-DE"):
        RegionalLocale.model_validate({"time": "pt-PT"})
    assert RegionalLocale.model_validate({"number": "pt-PT"}).number == "pt-PT"


@pytest.mark.parametrize("name", get_args(TimeLocaleName))
def test_every_named_time_locale_loads(name: TimeLocaleName) -> None:
    assert time_locale_definition(name) == vlc.get_time_format_locale(name)


def _stubbed_time_locale_names() -> set[str]:
    stub = Path(vlc.__file__).with_name("__init__.pyi")
    for node in ast.walk(ast.parse(stub.read_text())):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "TimeFormatLocaleName"
            and node.value is not None
        ):
            return {
                const.value
                for const in ast.walk(node.value)
                if isinstance(const, ast.Constant)
            }
    raise AssertionError("vl_convert's stub no longer names TimeFormatLocaleName")


def test_the_time_locale_names_are_exactly_vl_converts() -> None:
    assert _stubbed_time_locale_names() == set(get_args(TimeLocaleName))


def test_an_authored_time_locale_needs_seven_days_and_twelve_months() -> None:
    definition = vlc.get_time_format_locale("de-DE")
    authored = {
        "date_time": definition["dateTime"],
        "date": definition["date"],
        "time": definition["time"],
        "periods": definition["periods"],
        "days": definition["days"],
        "short_days": definition["shortDays"],
        "months": definition["months"],
        "short_months": definition["shortMonths"],
        "week_start": "monday",
    }
    assert TimeLocale.model_validate(authored)
    with pytest.raises(ValidationError):
        TimeLocale.model_validate({**authored, "months": definition["months"][:11]})


def test_an_authored_time_locale_reaches_vega_as_a_d3_definition() -> None:
    definition = vlc.get_time_format_locale("fr-FR")
    authored = TimeLocale.model_validate(
        {
            "date_time": definition["dateTime"],
            "date": definition["date"],
            "time": definition["time"],
            "periods": definition["periods"],
            "days": definition["days"],
            "short_days": definition["shortDays"],
            "months": definition["months"],
            "short_months": definition["shortMonths"],
            "week_start": "sunday",
        }
    )
    regional = Regional(locale=RegionalLocale(time=authored))
    assert regional.time_locale == definition


def test_the_default_time_locale_is_en_us() -> None:
    assert Regional().time_locale == vlc.get_time_format_locale("en-US")


@pytest.mark.parametrize("locale", ["de-DE", "fr-FR", "ja-JP", "ar-EG", "en-US"])
@pytest.mark.parametrize("directive", ["a", "A", "b", "B", "p", "c", "x", "X"])
@pytest.mark.parametrize("instant", _INSTANTS)
def test_portable_strftime_names_match_vega(
    locale: TimeLocaleName, directive: str, instant: datetime
) -> None:
    regional = Regional(locale=RegionalLocale(time=locale))
    with regional_scope(
        regional.format_locale,
        regional.time_locale,
        regional.week_start,
    ):
        actual = portable_strftime(instant, f"%{directive}")
    assert actual == _vega_utc_format(instant, f"%{directive}", locale)


def test_a_percent_in_a_locale_word_prints_literally() -> None:
    definition = vlc.get_time_format_locale("en-US")
    months = ["100%"] + definition["shortMonths"][1:]
    custom = TimeLocale.model_validate(
        {
            "date_time": definition["dateTime"],
            "date": definition["date"],
            "time": definition["time"],
            "periods": definition["periods"],
            "days": definition["days"],
            "short_days": definition["shortDays"],
            "months": definition["months"],
            "short_months": months,
            "week_start": "sunday",
        }
    )
    regional = Regional(locale=RegionalLocale(time=custom))
    with regional_scope(
        regional.format_locale,
        regional.time_locale,
        regional.week_start,
    ):
        assert portable_strftime(datetime(2023, 1, 5), "%b") == "100%"


def test_a_padding_modifier_on_a_name_directive_still_names_it() -> None:
    regional = Regional(locale=RegionalLocale(time="de-DE"))
    with regional_scope(
        regional.format_locale,
        regional.time_locale,
        regional.week_start,
    ):
        assert portable_strftime(datetime(2023, 3, 5), "%-b") == "Mrz"


_BOARD = """
title: Locale
regional:
  locale:
    number: de-DE
    time: de-DE
queries:
  q:
    type: values
    rows:
      - {month: 2023-03-01, revenue: 10}
      - {month: 2023-05-01, revenue: 20}
      - {month: 2023-10-01, revenue: 30}
charts:
  line:
    query: q
    type: line
    x: month
    y: revenue
rows:
  - line
"""


def test_a_de_de_board_names_its_axis_months_in_german() -> None:
    svg = render_board_to_svg(_BOARD)
    assert "Mrz" in svg
    assert "Okt" in svg
    assert "Mar<" not in svg
    assert "Oct<" not in svg


def _authored_time(**overrides: object) -> dict[str, object]:
    d = vlc.get_time_format_locale("en-US")
    return {
        "date_time": d["dateTime"],
        "date": d["date"],
        "time": d["time"],
        "periods": d["periods"],
        "days": d["days"],
        "short_days": d["shortDays"],
        "months": d["months"],
        "short_months": d["shortMonths"],
        "week_start": "sunday",
        **overrides,
    }


@pytest.mark.parametrize("field", ["date_time", "date", "time"])
def test_a_pattern_with_a_directive_vega_paints_differently_is_rejected(
    field: str,
) -> None:
    with pytest.raises(ValidationError, match=r"\['k'\]"):
        TimeLocale.model_validate(_authored_time(**{field: "%k"}))


def test_a_locale_pattern_may_hold_an_escaped_composite() -> None:
    assert TimeLocale.model_validate(_authored_time(date_time="%%c"))
    assert TimeLocale.model_validate(_authored_time(date="100%%x"))


@pytest.mark.parametrize(
    ("field", "pattern"),
    [
        ("date_time", "%c"),
        ("date", "%c"),
        ("date", "%x"),
        ("time", "%X"),
        ("time", "%-x"),
    ],
)
def test_a_composite_pattern_may_not_expand_into_itself(
    field: str, pattern: str
) -> None:
    with pytest.raises(ValidationError):
        TimeLocale.model_validate(_authored_time(**{field: pattern}))


def test_a_nested_composite_expands_through_the_locale() -> None:
    custom = TimeLocale.model_validate(
        _authored_time(date_time="%x %X", date="%d.%m.%Y", time="%H:%M")
    )
    regional = Regional(locale=RegionalLocale(time=custom))
    with regional_scope(
        regional.format_locale,
        regional.time_locale,
        regional.week_start,
    ):
        assert (
            portable_strftime(datetime(2023, 3, 5, 14, 7), "%c") == "05.03.2023 14:07"
        )


_NESTED = """
title: Root
regional:
  locale:
    number: de-DE
    time: de-DE
queries:
  q:
    type: values
    rows:
      - {v: 1}
charts:
  c:
    query: q
    type: kpi
    value: v
rows:
  - title: Inner
    regional:
      locale:
        number: LOCALE
    rows:
      - c
"""


def test_a_nested_board_may_author_only_part_of_the_locale() -> None:
    assert compile(_NESTED.replace("LOCALE", "de-DE")).success


def test_a_nested_mismatch_names_only_what_the_nested_board_authored() -> None:
    result = compile(_NESTED.replace("LOCALE", "fr-FR"))
    assert not result.success
    message = result.errors[0].message
    assert result.errors[0].code == "ERR-REGIONAL-NESTED-MISMATCH"
    assert "fr-FR" in message and "en-US" not in message


def test_date_variable_labels_use_the_time_locale() -> None:
    regional = Regional(locale=RegionalLocale(time="de-DE"))
    with regional_scope(
        regional.format_locale,
        regional.time_locale,
        regional.week_start,
        language=regional.resolved_language,
    ):
        assert format_date_label("2023-03-05") == "5. Mrz 2023"
        assert format_daterange_label("2023-03-05", "2023-10-09").startswith("5. Mrz")
    assert format_date_label("2023-03-05") == "5 Mar 2023"


def test_the_looping_locale_is_rejected_at_parse() -> None:
    with pytest.raises(ValidationError):
        TimeLocale.model_validate(_authored_time(date_time="%x%X", date="%", time="c"))


@pytest.mark.parametrize("field", ["date_time", "date", "time"])
@pytest.mark.parametrize("pattern", ["%", "100%", "%-", "a%"])
def test_a_pattern_ending_in_a_lone_percent_is_rejected(
    field: str, pattern: str
) -> None:
    with pytest.raises(ValidationError):
        TimeLocale.model_validate(_authored_time(**{field: pattern}))


@pytest.mark.parametrize("field", ["date_time", "date", "time"])
@pytest.mark.parametrize("pattern", ["100%%", "%%x", "%d %B"])
def test_a_pattern_of_whole_tokens_is_accepted(field: str, pattern: str) -> None:
    assert TimeLocale.model_validate(_authored_time(**{field: pattern}))


def test_the_board_schema_carries_the_pattern_of_every_pattern_field() -> None:
    from dbt_charts.core.compile.schema.introspection import introspect
    from dbt_charts.core.compile.schema.renderers.json_schema import (
        render_yaml_schema,
    )

    defs = render_yaml_schema(introspect())["$defs"]
    properties = defs["TimeLocale"]["properties"]
    for field in ("date_time", "date", "time"):
        assert "pattern" in json.dumps(properties[field]), field


@pytest.mark.parametrize("field", ["date_time", "date", "time"])
@pytest.mark.parametrize("value", [None, 5, ["%d"]])
def test_a_non_string_pattern_is_a_validation_error(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TimeLocale.model_validate(_authored_time(**{field: value}))


@pytest.mark.parametrize(
    ("name", "start"),
    [
        ("en-US", 0),
        ("de-DE", 1),
        ("pt-BR", 0),
        ("fa-IR", 6),
        ("ar-EG", 6),
        ("en-GB", 1),
    ],
)
def test_a_named_time_locale_starts_its_week_where_cldr_says(
    name: TimeLocaleName, start: int
) -> None:
    assert Regional(locale=RegionalLocale(time=name)).week_start == start


def test_every_time_locale_region_has_a_cldr_first_day() -> None:
    from dbt_charts.core.text.format_d3 import CLDR_FIRST_DAY

    regions = {name[3:] for name in get_args(TimeLocaleName)}
    assert regions <= set(CLDR_FIRST_DAY)


def test_an_authored_week_start_is_a_weekday_name() -> None:
    custom = TimeLocale.model_validate(_authored_time(week_start="saturday"))
    assert Regional(locale=RegionalLocale(time=custom)).week_start == 6
    with pytest.raises(ValidationError):
        TimeLocale.model_validate(_authored_time(week_start="mon"))
    missing = _authored_time()
    del missing["week_start"]
    with pytest.raises(ValidationError):
        TimeLocale.model_validate(missing)


def test_the_first_day_table_keeps_only_the_wanted_regions() -> None:
    spec = importlib.util.spec_from_file_location(
        "gen_cldr_first_day", DBT_CHARTS_DIR / "scripts" / "gen_cldr_first_day.py"
    )
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    first_day_table = script.first_day_table

    week_data = {"001": "mon", "DE": "mon", "US": "sun", "FR": "mon"}
    assert first_day_table(week_data, ["US", "DE"]) == {"DE": "mon", "US": "sun"}
    with pytest.raises(KeyError, match="IR"):
        first_day_table(week_data, ["IR"])
