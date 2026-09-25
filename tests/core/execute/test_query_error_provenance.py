"""Query execution errors keep typed codes and query provenance."""

from __future__ import annotations

import json
from pathlib import Path

from dbt_charts.agent_api.project_session import ProjectSession
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import BoardRenderResult
from dbt_charts.core.diagnostics.registry import REGISTRY


def _render_broken_sql_dashboard(tmp_path: Path) -> BoardRenderResult:
    project = FilesystemProject(tmp_path)
    (tmp_path / "charts").mkdir()
    board_path = tmp_path / "charts" / "broken.yml"
    board_path.write_text(
        """\
title: Broken SQL
queries:
  broken_query:
    sql: |
      SELECT
        1 AS ok
      FROM missing syntax
    source: db
charts:
  broken_chart:
    type: table
    query: broken_query
rows:
  - broken_chart
""",
    )
    (tmp_path / "dbt_charts.yml").write_text(
        """\
sources:
  db:
    type: duckdb
""",
    )

    session = ProjectSession.from_project(project)
    try:
        return session.render_board(
            board=session.project.path("charts/broken.yml").read_board(), format="svg"
        )
    finally:
        session.close()


def test_named_query_sql_syntax_error_keeps_execute_code_and_query_name(
    tmp_path: Path,
) -> None:
    """Research finding 12: a syntax error in a named query gets a coded,
    correctly titled diagnostic — not an ERR-INTERNAL "Chart Error" placard,
    and not a wrap-and-stringify message chain."""
    rendered = _render_broken_sql_dashboard(tmp_path)

    assert rendered.status == "partial"
    assert len(rendered.chart_errors) == 1
    error = rendered.chart_errors[0]
    assert REGISTRY.get(error.code).domain == "execute"
    assert error.code == "ERR-BINDER-UNKNOWN-COLUMN"
    assert error.fields["query_name"] == "broken_query"
    assert error.detail
    # The message is exactly the registered template — no wrap-and-stringify
    # prefix chain ("Query execution failed: DuckDB SQL execution failed: ...").
    assert error.message.startswith(
        "Warehouse could not resolve a column or table reference: "
    )
    assert "Query execution failed" not in error.message
    assert "DuckDB SQL execution failed" not in error.message

    # The placard title is the registered code's title, not a hardcoded
    # "Query Error: {name}" / "Chart Error: {id}" string.
    data = rendered.data
    assert isinstance(data, str)
    assert "Warehouse rejected an unknown column or table reference" in data
    assert "Query Error:" not in data
    assert "Chart Error:" not in data
    # Query attribution must still reach the *rendered* placard body — not
    # just error.fields — so a multi-chart dashboard's errors are still
    # distinguishable by which query produced them.
    assert "(query: broken_query)" in data

    # ERR-BINDER-UNKNOWN-COLUMN carries no sqlglot position at all — it must
    # keep highlighting the whole query block (columns=None), never a
    # guessed column, so the SQL-position narrowing below can't regress this.
    assert error.range is not None
    assert error.range.start_line != error.range.end_line
    assert error.range.columns is None


def _render_unparseable_sql_dashboard(tmp_path: Path) -> BoardRenderResult:
    project = FilesystemProject(tmp_path)
    (tmp_path / "charts").mkdir()
    board_path = tmp_path / "charts" / "broken.yml"
    board_path.write_text(
        """\
title: Broken SQL
queries:
  broken_query:
    sql: |
      WITHasdf x AS (
        SELECT 1 AS ok
      )
      SELECT * FROM x
    source: db
charts:
  broken_chart:
    type: table
    query: broken_query
rows:
  - broken_chart
""",
    )
    (tmp_path / "dbt_charts.yml").write_text(
        """\
sources:
  db:
    type: duckdb
""",
    )

    session = ProjectSession.from_project(project)
    try:
        return session.render_board(
            board=session.project.path("charts/broken.yml").read_board(), format="svg"
        )
    finally:
        session.close()


def test_single_token_sql_typo_narrows_range_to_the_token(tmp_path: Path) -> None:
    """A one-token typo (`WITH` -> `WITHasdf`) must underline just that
    token, not the whole 4-line query block — the core deliverable of this
    task. Also confirms the DuckDB path surfaces ERR-UNPARSEABLE-SQL rather
    than misclassifying it as ERR-WAREHOUSE-RUNTIME."""
    rendered = _render_unparseable_sql_dashboard(tmp_path)

    assert rendered.status == "partial"
    assert len(rendered.chart_errors) == 1
    error = rendered.chart_errors[0]
    assert error.code == "ERR-UNPARSEABLE-SQL"

    assert error.range is not None
    # Board line 5 is "      WITHasdf x AS (" — a single physical line, not
    # the 4-line block (start_line=4..end_line=8 for the whole "sql:" node).
    assert error.range.start_line == error.range.end_line == 5
    assert error.range.columns is not None
    board_line = "      WITHasdf x AS ("
    token = board_line[
        error.range.columns.start_col - 1 : error.range.columns.end_col - 1
    ]
    assert token == "AS"


def test_unparseable_sql_diagnostic_serializes_to_json(tmp_path: Path) -> None:
    """Regression: the diagnostic must survive `model_dump(mode="json")`.

    Cloud's editor endpoint (`apps/cloud/apps/dashboards/api.py`) dumps every
    editor diagnostic to JSON before handing it to CodeMirror's lint pass. A
    non-JSON-able value on `.fields` — e.g. the raw `sqlglot.errors.ParseError`
    the cause is derived from — raises PydanticSerializationError there and
    500s the whole preview instead of drawing a squiggle.
    """
    rendered = _render_unparseable_sql_dashboard(tmp_path)
    error = rendered.chart_errors[0]
    assert error.code == "ERR-UNPARSEABLE-SQL"

    dumped = error.model_dump(mode="json")
    json.dumps(dumped)  # must not raise
    assert isinstance(dumped["fields"]["cause"], str)


def _render_templated_sql_dashboard(tmp_path: Path) -> BoardRenderResult:
    """A board whose broken SQL also interpolates a variable.

    `{{ region }}` is replaced by a `$1` bind placeholder before sqlglot ever
    sees the string, so sqlglot's columns are measured against text that no
    longer matches the authored line.
    """
    project = FilesystemProject(tmp_path)
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "broken.yml").write_text(
        """\
title: Broken SQL
variables:
  region:
    default: "north-america-west"
queries:
  broken_query:
    sql: |
      SELECT 1 AS ok
      WHERE '{{ region }}' = 'x' AND aa bb
    source: db
charts:
  broken_chart:
    type: table
    query: broken_query
rows:
  - broken_chart
""",
    )
    (tmp_path / "dbt_charts.yml").write_text(
        """\
sources:
  db:
    type: duckdb
""",
    )

    session = ProjectSession.from_project(project)
    try:
        return session.render_board(
            board=session.project.path("charts/broken.yml").read_board(), format="svg"
        )
    finally:
        session.close()


def test_templated_sql_keeps_the_whole_block_range(tmp_path: Path) -> None:
    """Variable interpolation shifts every column after it, so the sqlglot
    position no longer addresses the authored text. Fall back to the honest
    whole-block range rather than underlining an arbitrary substring — a
    wrong-but-confident mark is worse than a coarse one.
    """
    rendered = _render_templated_sql_dashboard(tmp_path)
    error = rendered.chart_errors[0]
    assert error.code == "ERR-UNPARSEABLE-SQL"

    assert error.range is not None
    assert error.range.columns is None
