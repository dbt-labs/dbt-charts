"""`input: range` was a one-value slider (dbt-labs/dbt-charts#145); it migrates to
`input: slider` (the `v0_13_0` move), and is no longer an authorable input type.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.compile.models.variable.authored import Variable

BOARD = """\
title: t
queries:
  q:
    type: values
    columns: [a, v]
    values:
      - [x, 3]
variables:
  years:
    input: range
    min: 2
    max: 50
    step: 1
    default: 10
charts:
  t:
    type: table
    query: q
rows:
  - t
"""


def test_an_unmigrated_range_input_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Variable.model_validate({"input": "range"})


def test_a_range_variable_compiles_as_a_slider_with_a_notice() -> None:
    result = compile(BOARD)
    assert result.board is not None, result.errors
    assert not result.errors
    variable = result.board.variables["years"]
    assert (variable.input, variable.default) == ("slider", 10)
    assert any("one-value slider" in str(w) for w in result.warnings), result.warnings
