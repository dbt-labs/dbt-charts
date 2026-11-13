"""`dct validate --json` findings the cutover onto the engine's diagnostics must keep."""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_charts.agent_api import ProjectSession
from dbt_charts.agent_api.validate import ValidateResult
from dbt_charts.cli.filesystem_project import FilesystemProject

_RESTATED_BOARD = """
queries:
  q:
    sql: select 1 as a, 2 as b
    source: s
style:
  charts:
    table:
      pagination:
        page_rows: 5
charts:
  c:
    type: table
    query: q
    style:
      pagination:
        page_rows: 5
rows:
  - c
  - c
"""


def test_a_chart_level_restated_key_warns_once_per_placement(tmp_path: Path) -> None:
    """No example board restates a key, so this pins the warning the examples cannot."""
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  s:\n    type: duckdb\n    path: x.duckdb\n", encoding="utf-8"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/board.yml").write_text(_RESTATED_BOARD, encoding="utf-8")
    with ProjectSession.from_project(FilesystemProject(tmp_path)) as session:
        [result] = session.validate_paths(None, warehouse=False)
    assert result.success
    assert [(w.code, w.path) for w in result.warnings] == [
        ("WARN-RESTATED-STYLE", "charts.c.style.pagination.page_rows")
    ] * 2


_ERROR_BOARD = """
queries:
  q:
    sql: select 'a' as a, 2 as b
    source: s
charts:
  c:
    type: bar
    query: q
{chart}
rows:
  - c
"""


@pytest.mark.parametrize(
    ("chart", "code"),
    [
        pytest.param("    y: b\n", "ERR-CHART-CHANNELS-INCOMPLETE", id="channel-set"),
        pytest.param(
            "    x: a\n    y: b\n    style:\n      color:\n        static: accent[99]\n",
            "ERR-COLOR-TOKEN",
            id="color-token",
        ),
    ],
)
def test_validate_reports_the_errors_only_the_python_compile_found(
    tmp_path: Path, chart: str, code: str
) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  s:\n    type: duckdb\n    path: x.duckdb\n", encoding="utf-8"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/board.yml").write_text(
        _ERROR_BOARD.format(chart=chart), encoding="utf-8"
    )
    with ProjectSession.from_project(FilesystemProject(tmp_path)) as session:
        [result] = session.validate_paths(None, warehouse=False)
    assert not result.success
    assert [e.code for e in result.errors] == [code]


_MARKDOWN_BOARD = """\
---
board:
  queries:
    q:
      sql: select 'a' as a, 2 as b
      source: s
  charts:
    c:
      type: bar
      query: q
      x: a
      y: b
---

# Sales overview

Some prose about sales.

```charts
c
```
"""


def test_a_markdown_board_opening_with_a_heading_and_no_title_warns(
    tmp_path: Path,
) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  s:\n    type: duckdb\n    path: x.duckdb\n", encoding="utf-8"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/board.md").write_text(_MARKDOWN_BOARD, encoding="utf-8")
    with ProjectSession.from_project(FilesystemProject(tmp_path)) as session:
        [result] = session.validate_paths(None, warehouse=False)
    assert result.success
    assert [(w.code, w.path) for w in result.warnings] == [
        ("WARN-BODY-HEADING-NO-TITLE", "rows.0.text")
    ]
    # The lines index the .md file: the text block runs from the heading on line 15
    # through the prose on line 17.
    [warning] = result.warnings
    assert warning.range is not None
    assert (warning.range.file, warning.range.start_line, warning.range.end_line) == (
        "charts/board.md",
        15,
        17,
    )


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("x.db", id="literal"),
        pytest.param("{{ env_var('DCT_TEST_UNSET_DB_PATH') }}", id="templated"),
    ],
)
def test_validate_warns_where_a_source_cannot_honor_the_board_time_zone(
    tmp_path: Path, path: str
) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        f'sources:\n  s:\n    type: sqlite\n    path: "{path}"\n', encoding="utf-8"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/board.yml").write_text(
        "regional:\n  time_zone: America/New_York\n"
        + _ERROR_BOARD.format(chart="    x: a\n    y: b\n"),
        encoding="utf-8",
    )
    with ProjectSession.from_project(FilesystemProject(tmp_path)) as session:
        [result] = session.validate_paths(None, warehouse=False)
    assert result.success
    assert [w.code for w in result.warnings] == ["WARN-SESSION-TIME-ZONE-UNSUPPORTED"]


def test_a_refused_extra_field_is_located_and_hinted(tmp_path: Path) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  s:\n    type: duckdb\n    path: x.duckdb\n", encoding="utf-8"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/board.yml").write_text(
        _ERROR_BOARD.format(chart="    x: a\n    y: b\n    bogus: 1\n"),
        encoding="utf-8",
    )
    with ProjectSession.from_project(FilesystemProject(tmp_path)) as session:
        [result] = session.validate_paths(None, warehouse=False)
    [error] = result.errors
    assert error.path == "charts.c.bogus"
    assert error.range is not None
    assert (error.range.file, error.range.start_line) == ("charts/board.yml", 12)
    assert error.hint


def _validate_one(root: Path, board: str, config: str) -> ValidateResult:
    (root / "dbt_charts.yml").write_text(config, encoding="utf-8")
    (root / "charts").mkdir(exist_ok=True)
    (root / "charts/board.yml").write_text(board, encoding="utf-8")
    with ProjectSession.from_project(FilesystemProject(root)) as session:
        results = session.validate_paths(None, warehouse=False)
    return next(r for r in results if r.path == "charts/board.yml")


_DUCKDB = "sources:\n  s:\n    type: duckdb\n    path: x.duckdb\n"


def test_a_parse_warning_keeps_its_query_and_sql_position(tmp_path: Path) -> None:
    board = _ERROR_BOARD.format(chart="    x: a\n    y: b\n").replace(
        "select 'a' as a, 2 as b", "select ((( from"
    )
    [warning] = _validate_one(tmp_path, board, _DUCKDB).warnings
    assert warning.code == "WARN-PARSE-ERROR"
    assert warning.query == "q"
    assert warning.fields["sql_line_text"] == "select ((( from"
    assert {
        "sql_line",
        "sql_start_col",
        "sql_end_col",
        "sql_line_text",
    } <= set(warning.fields)


_RETIRED_KEY_BOARD = """
titel: Revenue
queries:
  q:
    sql: select 1 as a
    source: s
charts:
  c:
    type: bar
    query: q
    x: a
    y: a
rows:
  - c
"""


def test_a_refused_boards_warnings_honor_the_projects_ignore_list(
    tmp_path: Path,
) -> None:
    config = _DUCKDB + "warnings:\n  ignore:\n    - WARN-SCHEMA-MIGRATED\n"
    result = _validate_one(tmp_path, _RETIRED_KEY_BOARD, config)
    assert not result.success
    assert "WARN-SCHEMA-MIGRATED" not in [w.code for w in result.warnings]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("type: trino\n    timezone: 9\n", id="malformed"),
        pytest.param(
            "type: duckdb\n    path: x.duckdb\n    settings:\n"
            "      timezone: \"{{ env_var('DCT_TEST_ZONE', 'America/New_York') }}\"\n",
            id="templated-zone",
        ),
    ],
)
def test_a_source_the_host_cannot_read_a_zone_from_raises_no_zone_warning(
    tmp_path: Path, source: str
) -> None:
    config = f"sources:\n  s:\n    {source}"
    board = _ERROR_BOARD.format(chart="    x: a\n    y: b\n")
    board = "regional:\n  time_zone: America/New_York\n" + board
    codes = [w.code for w in _validate_one(tmp_path, board, config).warnings]
    assert "WARN-SOURCE-TIME-ZONE-OVERRIDDEN" not in codes


def test_a_restated_warning_keeps_the_fields_the_cli_prints(tmp_path: Path) -> None:
    result = _validate_one(tmp_path, _RESTATED_BOARD, _DUCKDB)
    restated = [w for w in result.warnings if w.code.startswith("WARN-RESTATED")]
    assert restated
    for warning in restated:
        assert {"value", "source", "why"} <= set(warning.fields)


def test_the_cli_prints_restated_warnings_from_the_engine(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from dbt_charts.cli.main import app

    (tmp_path / "dbt_charts.yml").write_text(_DUCKDB, encoding="utf-8")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/board.yml").write_text(_RESTATED_BOARD, encoding="utf-8")
    result = CliRunner().invoke(
        app, ["validate", "charts/board.yml", "--project-dir", str(tmp_path)]
    )
    assert result.exception is None, result.exception
    assert "restate" in result.output


def test_a_bar_charts_authored_hidden_value_labels_are_not_a_restated_default(
    tmp_path: Path,
) -> None:
    """Deleting `labels.visible: false` brings the zero label back, so it is not restated."""
    board = """
queries:
  q:
    sql: select 'Action' as genre, 10 as units
    source: s
charts:
  c:
    type: bar
    query: q
    x: genre
    y: units
    style:
      marks:
        bar:
          labels:
            visible: false
rows:
  - c
"""
    result = _validate_one(tmp_path, board, _DUCKDB)
    assert result.success
    assert [w for w in result.warnings if w.code.startswith("WARN-RESTATED")] == []
