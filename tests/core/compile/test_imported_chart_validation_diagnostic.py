"""Charts imported via `<file>.charts.<name>`: validation, chained refs, cycles."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import CompileResult, compile

_SOURCE = """\
title: Other
queries:
  revenue:
    sql: "SELECT 1 AS month, 2 AS revenue"
    source: db
charts:
  rev_chart:
    type: bar
    query: revenue
    x: month
    y: revenue
    style:
      legend:
        position: 42
rows:
  - rev_chart
"""


def test_invalid_field_in_imported_chart_is_a_coded_diagnostic(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "other.yaml").write_text(_SOURCE)
    main_yaml = """\
title: Importer
charts:
  rev_chart: other.yaml.charts.rev_chart
rows:
  - rev_chart
"""
    result = compile(
        main_yaml,
        base_dir=local_project(root=tmp_path).directory(),
        file="main.yaml",
    )
    [inline] = compile(_SOURCE, file="other.yaml").errors

    assert not result.success
    [err] = result.errors
    assert err.code == inline.code == "ERR-WRONG-SHAPE"
    assert err.path == inline.path
    assert err.message == inline.message
    assert err.hint == inline.hint
    assert err.range == inline.range
    assert err.range is not None
    assert err.range.file == "other.yaml"
    assert err.range.start_line == 14


_CHART = """\
  c:
    type: bar
    query: revenue
    x: month
    y: revenue
"""
_QUERIES = """\
queries:
  revenue:
    sql: "SELECT 1 AS month, 2 AS revenue"
    source: db
"""
_FINAL = f"title: T\n{_QUERIES}charts:\n{_CHART}rows:\n  - c\n"


def _board_with_chart(ref: str) -> str:
    return f"title: X\ncharts:\n  c: {ref}\nrows:\n  - c\n"


def _compile_importer(
    root: Path, local_project: Callable[..., FilesystemProject], ref: str
) -> CompileResult:
    return compile(
        _board_with_chart(ref),
        base_dir=local_project(root=root).directory(),
        file="main.yaml",
    )


@pytest.mark.parametrize("hop", ["third.yaml.charts.c", "{ref: third.yaml.charts.c}"])
def test_chained_import_resolves_through_second_file(
    tmp_path: Path, local_project: Callable[..., FilesystemProject], hop: str
) -> None:
    (tmp_path / "other.yaml").write_text(_board_with_chart(hop))
    (tmp_path / "third.yaml").write_text(_FINAL)
    result = _compile_importer(tmp_path, local_project, "other.yaml.charts.c")

    assert result.success, [str(e) for e in result.errors]
    assert result.board is not None
    assert result.board.charts["c"].query_name in result.query_registry


def test_three_hop_chain_scopes_queries_to_the_final_file(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "a.yaml").write_text(_board_with_chart("b.yaml.charts.c"))
    (tmp_path / "b.yaml").write_text(
        "title: B\nqueries:\n  revenue:\n"
        "    sql: \"SELECT 'wrong' AS month, 0 AS revenue\"\n    source: db\n"
        "charts:\n  c: {ref: c.yaml.charts.c}\nrows:\n  - c\n"
    )
    (tmp_path / "c.yaml").write_text(_FINAL)
    result = _compile_importer(tmp_path, local_project, "a.yaml.charts.c")

    assert result.success, [str(e) for e in result.errors]
    assert result.board is not None
    query = result.query_registry[result.board.charts["c"].query_name]
    assert "SELECT 1 AS month" in str(query.sql)


def test_chained_ref_resolves_relative_to_its_own_file(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "shared").mkdir()
    (tmp_path / "sub" / "other.yaml").write_text(
        _board_with_chart("../shared/third.yaml.charts.c")
    )
    (tmp_path / "shared" / "third.yaml").write_text(_FINAL)
    result = _compile_importer(tmp_path, local_project, "sub/other.yaml.charts.c")

    assert result.success, [str(e) for e in result.errors]


@pytest.mark.parametrize(
    "files",
    [
        {"a.yaml": "a.yaml.charts.c"},
        {"a.yaml": "b.yaml.charts.c", "b.yaml": "a.yaml.charts.c"},
    ],
    ids=["self", "two-file"],
)
def test_circular_chart_import_is_a_coded_diagnostic_with_a_range(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    files: dict[str, str],
) -> None:
    for name, ref in files.items():
        (tmp_path / name).write_text(_board_with_chart(ref))
    result = _compile_importer(tmp_path, local_project, "a.yaml.charts.c")

    [err] = result.errors
    assert err.code == "ERR-UNRESOLVED-REFERENCE"
    assert "circular" in err.message.lower()
    assert err.range is not None
    assert err.range.file == "main.yaml"


@pytest.mark.parametrize(
    ("middle", "code"),
    [
        ("{ref: c.yaml.charts.c, x: month}", "ERR-EXTRA-FIELD"),
        ("{ref: not-a-chart-ref}", "ERR-VALIDATION-FIELD"),
    ],
    ids=["ref-with-sibling-key", "malformed-ref"],
)
def test_malformed_middle_hop_is_a_coded_diagnostic_in_the_middle_file(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    middle: str,
    code: str,
) -> None:
    (tmp_path / "a.yaml").write_text(_board_with_chart("b.yaml.charts.c"))
    (tmp_path / "b.yaml").write_text(_board_with_chart(middle))
    (tmp_path / "c.yaml").write_text(_FINAL)
    result = _compile_importer(tmp_path, local_project, "a.yaml.charts.c")

    assert not result.success
    [err] = result.errors
    assert err.code == code
    assert err.range is not None
    assert err.range.file == "b.yaml"


def test_non_mapping_imported_chart_is_a_coded_diagnostic_in_its_file(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "other.yaml").write_text(_board_with_chart("3"))
    result = _compile_importer(tmp_path, local_project, "other.yaml.charts.c")

    assert not result.success
    [err] = result.errors
    assert err.code == "ERR-VALIDATION-FIELD"
    assert err.range is not None
    assert err.range.file == "other.yaml"
