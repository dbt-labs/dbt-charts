"""formatLocale parity against real d3 (Vega's bundled d3-format, via vl-convert)."""

import json
import math
import pathlib

import pytest

from d3_format import format_locale

FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "locale_parity.json").read_text()
)


@pytest.mark.parametrize(
    ("locale", "spec", "value_label", "expected"),
    FIXTURE["cases"],
    ids=[f"{c[0]}:{c[1]}({c[2]})" for c in FIXTURE["cases"]],
)
def test_locale_parity(locale: str, spec: str, value_label: str, expected: str) -> None:
    value = math.nan if value_label == "nan" else float(value_label)
    result = format_locale(FIXTURE["locales"][locale]).format(spec)(value)
    assert result == expected
