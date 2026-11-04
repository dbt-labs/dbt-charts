"""Tests for required-variable enforcement at render time.

Task: enforce-variable-required-at-render-time-so-unscoped-looker-boards-fail-loudly
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project
from dbt_charts.core.render import MissingRequiredVariablesError, render

_BOARD_REQUIRED = """
variables:
  account_id:
    input: text
    label: Account ID
    notes: The account to scope results to
    required: true
queries:
  q:
    type: values
    rows:
      - {id: 1}
charts:
  c:
    query: q
    type: kpi
    value: id
rows:
  - c
"""


def _make_executor(board, query_registry, local_project: Callable[..., Project]):
    return Executor(
        board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=query_registry,
    )


def test_required_variable_missing_raises(local_project: Callable[..., Project]):
    """Required variable with no value → MissingRequiredVariablesError before any query runs."""
    result = compile(_BOARD_REQUIRED)
    assert result.success and result.board is not None
    executor = _make_executor(result.board, result.query_registry, local_project)

    with pytest.raises(MissingRequiredVariablesError) as exc_info:
        render(result.board, executor, format="text")

    err = exc_info.value
    assert len(err.missing) == 1
    mv = err.missing[0]
    assert mv.key == "account_id"
    assert mv.label == "Account ID"
    assert mv.notes == "The account to scope results to"
    assert mv.input_type == "text"
    assert err.code is not None
    assert err.code.code == "ERR-INPUT-INVALID"
    assert err.hint is not None
    assert "?account_id=" in err.hint
    assert "default:" in err.hint


def test_required_variable_provided_renders(local_project: Callable[..., Project]):
    """Required variable with explicit value → no error."""
    result = compile(_BOARD_REQUIRED)
    assert result.success and result.board is not None
    executor = _make_executor(result.board, result.query_registry, local_project)

    # Should not raise
    render(result.board, executor, format="text", variables={"account_id": "acct_123"})


def test_required_variable_with_default_renders(local_project: Callable[..., Project]):
    """Required variable with a non-None default → default satisfies the requirement."""
    yaml = """
variables:
  region:
    input: select
    label: Region
    required: true
    default: North
queries:
  q:
    type: values
    rows:
      - {x: 1}
charts:
  c:
    query: q
    type: kpi
    value: x
rows:
  - c
"""
    result = compile(yaml)
    assert result.success and result.board is not None
    executor = _make_executor(result.board, result.query_registry, local_project)

    # Default value satisfies required — must not raise
    render(result.board, executor, format="text")


def test_optional_variable_missing_no_error(local_project: Callable[..., Project]):
    """Optional variable with no value → no error (regression guard)."""
    yaml = """
variables:
  region:
    input: select
    options:
      static: [North, South]
queries:
  q:
    type: values
    rows:
      - {x: 1}
charts:
  c:
    query: q
    type: kpi
    value: x
rows:
  - c
"""
    result = compile(yaml)
    assert result.success and result.board is not None
    executor = _make_executor(result.board, result.query_registry, local_project)

    # Optional missing → no raise
    render(result.board, executor, format="text")


def test_multiple_required_variables_missing_all_listed(
    local_project: Callable[..., Project],
):
    """Multiple required variables all missing → all appear in error.missing (no fail-fast)."""
    yaml = """
variables:
  account_id:
    required: true
    label: Account
  date_range:
    required: true
    label: Date Range
queries:
  q:
    type: values
    rows:
      - {x: 1}
charts:
  c:
    query: q
    type: kpi
    value: x
rows:
  - c
"""
    result = compile(yaml)
    assert result.success and result.board is not None
    executor = _make_executor(result.board, result.query_registry, local_project)

    with pytest.raises(MissingRequiredVariablesError) as exc_info:
        render(result.board, executor, format="text")

    err = exc_info.value
    assert len(err.missing) == 2
    keys = {mv.key for mv in err.missing}
    assert "account_id" in keys
    assert "date_range" in keys


def test_required_variable_fires_before_any_query_executes(
    monkeypatch, local_project: Callable[..., Project]
):
    """Required variable check fires before any query is dispatched.

    Spies on ``Executor.run_misses`` and asserts it is not called when a
    required variable is unset — pinning the "validate then execute" ordering
    as a real invariant rather than coincidence.
    """
    result = compile(_BOARD_REQUIRED)
    assert result.success and result.board is not None
    executor = _make_executor(result.board, result.query_registry, local_project)

    calls: list[str] = []

    def _boom_parallel(*args, **kwargs):
        calls.append("parallel")

    monkeypatch.setattr(Executor, "run_misses", _boom_parallel)

    with pytest.raises(MissingRequiredVariablesError):
        render(result.board, executor, format="text")

    assert calls == [], f"Queries dispatched before validation: {calls}"


def test_required_variable_check_fires_for_all_formats(
    local_project: Callable[..., Project],
):
    """The check fires regardless of output format."""
    result = compile(_BOARD_REQUIRED)
    assert result.success and result.board is not None

    for fmt in ("text", "json", "yaml"):
        executor = _make_executor(result.board, result.query_registry, local_project)
        with pytest.raises(MissingRequiredVariablesError, match="account_id"):
            render(result.board, executor, format=fmt)


def test_error_message_includes_key(local_project: Callable[..., Project]):
    """Error str representation includes the missing variable key."""
    result = compile(_BOARD_REQUIRED)
    assert result.success and result.board is not None
    executor = _make_executor(result.board, result.query_registry, local_project)

    with pytest.raises(MissingRequiredVariablesError) as exc_info:
        render(result.board, executor, format="text")

    assert "account_id" in str(exc_info.value)


def test_required_variable_empty_string_raises(local_project: Callable[..., Project]):
    """Empty string from URL param (e.g. ?account_id=) is treated as missing."""
    result = compile(_BOARD_REQUIRED)
    assert result.success and result.board is not None
    executor = _make_executor(result.board, result.query_registry, local_project)

    with pytest.raises(MissingRequiredVariablesError):
        render(result.board, executor, format="text", variables={"account_id": ""})


def test_required_variable_empty_list_raises(local_project: Callable[..., Project]):
    """Empty list (e.g. multiselect with nothing chosen) is treated as missing."""
    yaml = """
variables:
  tags:
    input: multiselect
    required: true
    label: Tags
queries:
  q:
    type: values
    rows:
      - {x: 1}
charts:
  c:
    query: q
    type: kpi
    value: x
rows:
  - c
"""
    result = compile(yaml)
    assert result.success and result.board is not None
    executor = _make_executor(result.board, result.query_registry, local_project)

    with pytest.raises(MissingRequiredVariablesError):
        render(result.board, executor, format="text", variables={"tags": []})
