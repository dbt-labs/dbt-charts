"""`is_d3_number_spec` answers the same as the Rust port on the same literal table.

The Rust twin is `is_d3_number_spec_matches_the_shared_parity_table` in
`libs/chart-svg/resolve/src/format.rs`; keep the two tables identical.
"""

import pytest

from dbt_charts.core.text.format_d3 import is_d3_number_spec

_TABLE: list[tuple[str, bool]] = [
    ("$.2s", True),
    (".2s", True),
    (",.0f", True),
    (".0%", True),
    ("+,.2f", True),
    ("~s", True),
    ("", True),
    ("x", True),
    ("h", True),
    ("#,.0f", True),
    ("#.2s", True),
    ("#.2f", True),
    ("*>10", True),
    (",5f", False),
    (",0", False),
    ("1,2f", False),
    ("%Y", False),
    ("date_short", False),
    ("number", False),
    ("abc", False),
    (".", False),
    ("%%Y", False),
]


@pytest.mark.parametrize(("spec", "expected"), _TABLE)
def test_is_d3_number_spec_matches_rust_parity_table(spec: str, expected: bool) -> None:
    assert is_d3_number_spec(spec) is expected
