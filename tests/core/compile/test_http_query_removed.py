"""`type: http` queries and `http` sources were removed; old content fails loudly.

A board that still names one must say what happened and what to use instead,
not surface a generic union-tag error, and never lose the query silently (its
charts would then point at nothing).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import load_project_sources
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.source import parse_source_config

_REPLACEMENT = "Load the data into your warehouse"


def _board_yaml(query_body: str) -> str:
    return f"""\
title: test
source: _test
queries:
  q:
{query_body}
rows: []
"""


@pytest.mark.parametrize(
    "query_body",
    [
        "    type: http\n    url: https://api.example.com/data\n",
        "    type: http\n    method: GET\n",
        # The retired grammar inferred `http` from a bare `url:`.
        "    url: https://api.example.com/data\n",
    ],
    ids=["typed", "typed_without_url", "inferred_from_url"],
)
def test_an_http_query_fails_compile_naming_the_replacement(query_body: str) -> None:
    result = compile(_board_yaml(query_body))

    assert not result.success
    (error,) = result.errors
    assert error.code == "ERR-HTTP-QUERY-REMOVED"
    assert "`type: http` queries were removed" in error.message
    assert _REPLACEMENT in error.message
    assert "CSV, JSON or Parquet file source" in error.message
    assert "union" not in error.message.lower()


_IMPORTED_HTTP_QUERIES = (
    pytest.param(
        "  api:\n    type: http\n    url: https://api.example.com/data\n",
        id="typed",
    ),
    pytest.param(
        "  api:\n    url: https://api.example.com/data\n", id="inferred_from_url"
    ),
)


@pytest.mark.parametrize("queries_body", _IMPORTED_HTTP_QUERIES)
def test_an_http_query_behind_a_cross_board_chart_import_fails_compile(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    queries_body: str,
) -> None:
    (tmp_path / "b.yml").write_text(
        f"title: b\nsource: _test\nqueries:\n{queries_body}"
        "charts:\n  c:\n    query: api\n    type: table\nrows: []\n"
    )
    board = (
        "title: a\nsource: _test\ncharts:\n  c:\n    ref: b.yml.charts.c\n"
        "rows:\n  - c\n"
    )

    result = compile(board, base_dir=local_project(root=tmp_path).directory())

    assert [e.code for e in result.errors] == ["ERR-HTTP-QUERY-REMOVED"]
    assert _REPLACEMENT in result.errors[0].message


@pytest.mark.parametrize("queries_body", _IMPORTED_HTTP_QUERIES)
def test_an_http_query_behind_a_query_ref_fails_compile(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    queries_body: str,
) -> None:
    (tmp_path / "b.yml").write_text(
        f"title: b\nsource: _test\nqueries:\n{queries_body}rows: []\n"
    )
    board = "title: a\nsource: _test\nqueries:\n  q: b.yml.queries.api\nrows: []\n"

    result = compile(board, base_dir=local_project(root=tmp_path).directory())

    assert [e.code for e in result.errors] == ["ERR-HTTP-QUERY-REMOVED"]
    assert _REPLACEMENT in result.errors[0].message


def test_an_http_source_names_the_replacement() -> None:
    with pytest.raises(ValueError, match="`type: http` sources were removed") as exc:
        parse_source_config({"type": "http", "url": "https://example.com"})

    assert _REPLACEMENT in str(exc.value)
    assert "CSV, JSON or Parquet file source" in str(exc.value)


def test_an_http_source_in_the_project_config_is_a_source_config_error(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  api:\n    type: http\n    url: https://example.com\n"
    )

    with pytest.raises(CompilationError) as exc:
        load_project_sources(local_project(tmp_path))

    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-SOURCE-CONFIG-INVALID"
    assert "`type: http` sources were removed" in str(exc.value)
    assert "api" in str(exc.value)
