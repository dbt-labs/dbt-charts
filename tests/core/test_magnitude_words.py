"""A locale names the abbreviations its magnitudes print."""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.primitives import NumberLocale, Regional
from dbt_charts.core.text.format_d3 import (
    LocaleName,
    Notation,
    format_d3,
    number_locale_scope,
)
from dbt_charts.core.text.magnitude_words import (
    _BY_LANGUAGE,
    _BY_REGION,
    magnitude_words,
)

_FR = {
    "decimal": ",",
    "thousands": ".",
    "grouping": [3],
    "currency": ["", " €"],
}


def _formatted(regional: Regional, value: float, notation: Notation) -> str:
    with number_locale_scope(regional.format_locale):
        return format_d3(value, ".2~s", notation=notation)


@pytest.mark.parametrize("name", get_args(LocaleName))
def test_every_named_locale_has_its_words(name: LocaleName) -> None:
    analytic, narrative = magnitude_words(name)
    assert all(w.strip() for w in (*analytic, *narrative))


@pytest.mark.parametrize("region", _BY_REGION)
def test_a_region_row_differs_from_its_language(region: str) -> None:
    assert _BY_REGION[region] != _BY_LANGUAGE[region.split("-")[0]]


@pytest.mark.parametrize(
    ("notation", "expected"),
    [("analytic", "1,5\xa0M"), ("narrative", "1,5\xa0Mio.")],
)
def test_a_named_locale_prints_its_own_words(notation: Notation, expected: str) -> None:
    assert _formatted(Regional(locale="de-DE"), 1_500_000, notation) == expected


@pytest.mark.parametrize(
    ("notation", "expected"), [("analytic", "1.5\xa0M"), ("narrative", "1.5mn")]
)
def test_en_us_keeps_the_house_words(notation: Notation, expected: str) -> None:
    assert _formatted(Regional(), 1_500_000, notation) == expected


def test_a_definition_without_words_prints_en_us_words() -> None:
    regional = Regional(locale=NumberLocale.model_validate(_FR))
    assert _formatted(regional, 1_500_000, "narrative") == "1,5mn"


def test_a_definition_names_its_own_words() -> None:
    locale = NumberLocale.model_validate(
        {
            **_FR,
            "analytic": [" k", " M", " Md", " Bn"],
            "narrative": ["k", "\xa0Mio.", "\xa0Mrd.", "\xa0Bio."],
        }
    )
    regional = Regional(locale=locale)
    assert _formatted(regional, 2_500_000_000, "analytic") == "2,5 Md"
    assert _formatted(regional, 2_500_000_000, "narrative") == "2,5\xa0Mrd."


@pytest.mark.parametrize(
    "words",
    [
        ["k", "M", "B"],
        ["k", "M", "B", "T", "P"],
        ["k", "", "B", "T"],
        ["k", "\xa0", "B", "T"],
    ],
)
def test_words_name_exactly_four_visible_tiers(words: list[str]) -> None:
    with pytest.raises(ValidationError):
        NumberLocale.model_validate({**_FR, "analytic": words})
