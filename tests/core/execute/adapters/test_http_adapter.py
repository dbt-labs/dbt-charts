"""Tests for HttpAdapter json_path extraction and response normalization.

Covers:
- json_path dot-notation extraction from nested JSON responses
- list responses work without json_path
- dict responses without json_path produce a clear error (not a heuristic)
- json_path targeting a non-list value produces an error
- json_path targeting a missing key produces an error
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.core.compile.models.query.normalized import HttpQuery
from dbt_charts.core.execute.adapters.http_adapter import HttpAdapter


def _mock_response(json_data: object) -> MagicMock:
    """Return a mock httpx.Response that returns json_data from .json()."""
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def _execute(query: HttpQuery, response_data: object) -> object:
    """Run query through HttpAdapter against a mocked GET response."""
    adapter = HttpAdapter()
    with patch(
        "dbt_charts.core.execute.adapters.http_adapter.httpx.Client"
    ) as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = (
            _mock_response(response_data)
        )
        return adapter.execute(query)


class TestJsonPathExtraction:
    def test_json_path_extracts_top_level_key(self) -> None:
        """json_path=$.data pulls the list from a wrapping object."""
        rows = [{"id": 1}, {"id": 2}]
        result = _execute(
            HttpQuery(url="https://api.example.com/", json_path="$.data"),
            {"data": rows, "total": 2},
        )
        assert result.is_success, result.error
        assert result.data == rows

    def test_json_path_traverses_nested_keys(self) -> None:
        """json_path=$.results.items traverses two levels."""
        rows = [{"name": "a"}, {"name": "b"}]
        result = _execute(
            HttpQuery(url="https://api.example.com/", json_path="$.results.items"),
            {"results": {"items": rows, "count": 2}},
        )
        assert result.is_success, result.error
        assert result.data == rows

    def test_json_path_missing_key_returns_error(self) -> None:
        """json_path pointing to a missing key returns a clear error, not an exception."""
        result = _execute(
            HttpQuery(url="https://api.example.com/", json_path="$.missing_key"),
            {"data": [{"id": 1}]},
        )
        assert not result.is_success
        assert "missing_key" in str(result.error)

    def test_json_path_non_list_result_returns_error(self) -> None:
        """json_path pointing to a non-list value returns a clear error."""
        result = _execute(
            HttpQuery(url="https://api.example.com/", json_path="$.count"),
            {"data": [{"id": 1}], "count": 42},
        )
        assert not result.is_success
        assert "list" in str(result.error).lower()


class TestNoJsonPath:
    def test_list_response_works_without_json_path(self) -> None:
        """A response that is already a list works without json_path."""
        rows = [{"id": 1}, {"id": 2}]
        result = _execute(HttpQuery(url="https://api.example.com/"), rows)
        assert result.is_success, result.error
        assert result.data == rows

    @pytest.mark.parametrize(
        "response_data",
        [
            {"data": [{"id": 1}]},  # old heuristic key 1
            {"results": [{"id": 1}]},  # old heuristic key 2
            {"other": [{"id": 1}]},  # never matched by old heuristic either
        ],
    )
    def test_dict_response_without_json_path_errors(self, response_data: dict) -> None:
        """Dict responses without json_path always error — no heuristic guessing.

        Regression guard: the old adapter tried data["data"] then data["results"]
        silently. Now the user must specify json_path explicitly.
        """
        result = _execute(HttpQuery(url="https://api.example.com/"), response_data)
        assert not result.is_success
        assert "json_path" in str(result.error)
