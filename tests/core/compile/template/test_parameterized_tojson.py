"""`| tojson` on a parameterized variable names the variable, not a Python type."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.errors import JinjaError
from dbt_charts.core.compile.template.parameterized import render_parameterized


def test_tojson_on_a_variable_names_the_variable_and_the_alternative() -> None:
    with pytest.raises(JinjaError) as exc:
        render_parameterized("select {{ region | tojson }}", {"region": "x"})
    message = str(exc.value)
    assert "Variable 'region'" in message
    assert "tojson" in message
    assert "_ParameterizedValue" not in message
