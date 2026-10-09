"""One rule per field: authorable (in the schema) or internal (rejected on input)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.board.authored import AUTHORED_BOARD_ADAPTER
from dbt_charts.core.compile.models.chart.authored import AUTHORED_CHART_VARIANTS
from dbt_charts.core.compile.models.chart.authored._conditional_formatting import (
    ConditionalRule,
)
from dbt_charts.core.compile.schema.introspection import introspect


def _board(**extra: Any) -> dict[str, Any]:
    return {"title": "t", "rows": [], **extra}


def test_exclude_only_field_is_in_the_schema() -> None:
    models = introspect().models
    for model_name in (AUTHORED_CHART_VARIANTS[t] for t in ("bar", "line")):
        names = {f.name for f in models[model_name].fields}
        assert "visible" in names, model_name


@pytest.mark.parametrize(
    "key", ["defined_in_other_file", "input_auto_detected", "variable_dependencies"]
)
def test_authored_variable_rejects_internal_field(key: str) -> None:
    value: Any = [] if key == "variable_dependencies" else True
    board = _board(variables={"v": {"input": "select", key: value}})
    with pytest.raises(ValidationError):
        AUTHORED_BOARD_ADAPTER.validate_python(board)


def test_cache_enabled_round_trip_form_is_not_authorable() -> None:
    board = _board(cache={"enabled": False, "ttl": "1h"})
    with pytest.raises(ValidationError):
        AUTHORED_BOARD_ADAPTER.validate_python(board)


def test_rule_rejects_attribute_name_in_() -> None:
    with pytest.raises(ValidationError):
        ConditionalRule.model_validate({"in_": [1], "background": "red"})
    rule = ConditionalRule.model_validate({"in": [1], "background": "red"})
    assert rule.in_ == [1]


def test_schema_query_rejects_attribute_name_schema_name() -> None:
    query = {"type": "schema", "fields": ["a"]}
    with pytest.raises(ValidationError):
        AUTHORED_BOARD_ADAPTER.validate_python(
            _board(queries={"q": {**query, "schema_name": "s"}})
        )
    AUTHORED_BOARD_ADAPTER.validate_python(
        _board(queries={"q": {**query, "schema": "s"}})
    )
