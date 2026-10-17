"""A malformed tool call comes back as one plain line naming the argument, not a
Pydantic dump with a docs URL."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from dbt_charts.agent_api.argument_errors import format_argument_error
from dbt_charts.agent_api.query import ExecuteQueryArgs
from dbt_charts.agent_api.search import SearchBoardsArgs


def _error(tool: str, model: type[BaseModel], **args: object) -> str:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(args)
    return format_argument_error(tool, caught.value)


def test_an_unknown_argument_is_named_and_the_url_is_gone() -> None:
    message = _error("search_boards", SearchBoardsArgs, query="x", project="p1")

    assert message == "search_boards: project: is not an argument of this tool"


def test_a_missing_argument_says_it_is_required() -> None:
    assert _error("search_boards", SearchBoardsArgs) == (
        "search_boards: query: is required"
    )


def test_a_null_variable_value_is_one_clause_not_one_per_union_member() -> None:
    message = _error(
        "execute_query",
        ExecuteQueryArgs,
        sql="select 1",
        variables=[{"name": "region", "value": None}],
    )

    assert message == (
        "execute_query: variables.0.value: got null, expected one of "
        "str, int, float, bool, list[str]"
    )


def test_the_named_query_form_names_the_same_variables_field() -> None:
    message = _error(
        "execute_query",
        ExecuteQueryArgs,
        query="q",
        board="b.yml",
        variables=[{"name": "region", "value": None}],
    )

    assert message.startswith("execute_query: variables.0.value: got null")


def test_a_value_check_keeps_its_own_reason_without_the_pydantic_prefix() -> None:
    message = _error("search_boards", SearchBoardsArgs, query="", cursor="abc")

    assert message.startswith("search_boards: cursor: 'abc' is not a cursor")
    assert "Value error" not in message


def test_several_bad_arguments_stay_on_one_line() -> None:
    message = _error(
        "search_boards", SearchBoardsArgs, query="x", project="p1", cursor="abc"
    )

    assert "\n" not in message
    assert message.count("; ") == 1
    assert "project: is not an argument of this tool" in message
    assert "cursor: " in message


def test_a_model_level_check_has_no_field_to_name() -> None:
    message = _error(
        "execute_query",
        ExecuteQueryArgs,
        query="q",
        board="b.yml",
        variables=[{"name": "a", "value": 1}, {"name": "a", "value": 2}],
    )

    assert message == "execute_query: Duplicate variable name: 'a'"


@pytest.mark.parametrize("tool", ["execute_query", "search_boards"])
def test_no_pydantic_docs_url_survives(tool: str) -> None:
    model: type[BaseModel] = (
        ExecuteQueryArgs if tool == "execute_query" else SearchBoardsArgs
    )
    message = _error(tool, model, bogus=1)

    assert "pydantic.dev" not in message
    assert "validation error" not in message
