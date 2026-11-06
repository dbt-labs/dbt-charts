"""d3's number and time locales come from the engine's vendored folder."""

from __future__ import annotations

from typing import get_args

import pytest
import vl_convert as vlc

from dbt_charts import _engine
from dbt_charts.core.text.format_d3 import (
    NumberLocaleName,
    TimeLocaleName,
    number_locale_definition,
    time_locale_definition,
)


def test_the_locale_names_are_the_engines_folder() -> None:
    assert list(get_args(NumberLocaleName)) == _engine.number_locales()
    assert list(get_args(TimeLocaleName)) == _engine.time_locales()


@pytest.mark.parametrize("name", get_args(NumberLocaleName))
def test_every_number_locale_is_vegas(name: NumberLocaleName) -> None:
    assert number_locale_definition(name) == vlc.get_format_locale(name)


@pytest.mark.parametrize("name", get_args(TimeLocaleName))
def test_every_time_locale_is_vegas(name: TimeLocaleName) -> None:
    assert time_locale_definition(name) == vlc.get_time_format_locale(name)


def test_an_unknown_locale_raises() -> None:
    with pytest.raises(ValueError, match="unknown time locale 'pt-PT'"):
        _engine.time_locale("pt-PT")
