"""Unit tests for core/execute/warehouse_check.py — per-adapter dispatch.

Every case drives a *compiled* board through the check, not a hand-built SQL
string: the whole point of the design is that the check sees what execution
sees (variables merged, setup_sql attached, refs resolved), and a bare string
fixture is exactly what hid the earlier variable- and semicolon-handling bugs.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import duckdb
import pytest
from google.api_core.exceptions import BadRequest, Forbidden, NotFound

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.compile.models.source import BigQuerySourceConfig
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_WAREHOUSE_CONNECTION,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry
from dbt_charts.core.execute.adapters.base import (
    QueryResult,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.adapters.sql_adapter import PreparedSql
from dbt_charts.core.execute.warehouse_check import (
    WarehouseCheckColumn,
    check_ad_hoc_query,
    warehouse_check,
)

_CHART = """\
charts:
  c:
    query: my_query
    type: table
"""

_BOARD = f"""\
title: Test Board
source: testdb
queries:
  my_query: SELECT a, b FROM t
{_CHART}"""


def _duckdb_project(
    tmp_path: Path,
    *,
    board_yml: str = _BOARD,
    sql_setup: str = "CREATE TABLE t (a INTEGER, b VARCHAR)",
):
    """A real project on a real DuckDB file, compiled — no test doubles anywhere."""
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(sql_setup)
    conn.close()

    (tmp_path / "dbt_charts.yml").write_text(
        f"sources:\n  testdb:\n    type: duckdb\n    path: {db_path}\n"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "board.yml").write_text(board_yml)

    project = FilesystemProject(tmp_path)
    result = compile_file(project.path("charts/board.yml").read_board())
    assert result.board is not None, result.errors
    return result, build_adapter_registry(project, profile_type="duckdb")


def _check(compiled, registry, query_name: str = "my_query"):
    return warehouse_check(
        compiled.query_registry[query_name],
        board=compiled.board,
        adapter_registry=registry,
        query_name=query_name,
        query_registry=compiled.query_registry,
    )


def _check_sql(sql: str, registry):
    """Check one SQL string against a stubbed registry, via a real compiled board.

    The board is real even where the registry is a double: the check reads
    variables and query metadata off it, and a hand-built stand-in would not
    have them.
    """
    import yaml

    from dbt_charts.core.compile import compile as compile_board

    compiled = compile_board(
        yaml.dump(
            {
                "source": "s",
                "queries": {"q": sql},
                "charts": {"c": {"query": "q", "type": "table"}},
            }
        )
    )
    assert compiled.board is not None, compiled.errors
    return _check(compiled, registry, query_name="q")


class TestWarehouseCheckDuckDB:
    """DuckDB path: DESCRIBE returns schema; invalid SQL fails cleanly."""

    def test_valid_query_returns_columns(self, tmp_path):
        result = _check(*_duckdb_project(tmp_path))
        assert result.status == "valid"
        assert result.mechanism == "DESCRIBE"
        assert result.columns_checked is True
        assert {c.name for c in result.columns} == {"a", "b"}
        assert result.error == ""

    def test_invalid_query_returns_error(self, tmp_path):
        compiled, registry = _duckdb_project(
            tmp_path,
            board_yml=_BOARD.replace("SELECT a, b FROM t", "SELECT nope FROM t"),
        )
        result = _check(compiled, registry)
        assert result.status == "invalid"
        assert result.mechanism == "DESCRIBE"
        assert result.columns_checked is False
        assert result.columns == []
        assert "nope" in result.error

    def test_connect_failure_surfaces_connection_failures_classified_message(
        self, tmp_path
    ):
        """A DuckDB file that can't be opened reaches the wrap path's real
        ``connection_failure()`` classifier, not a mocked QueryResult — and
        the caller sees the registry-template text verbatim (no adapter
        prefix), the one canonical wording decided for this error class.
        """
        missing_db = tmp_path / "missing.duckdb"
        (tmp_path / "dbt_charts.yml").write_text(
            f"sources:\n  testdb:\n    type: duckdb\n    path: {missing_db}\n"
        )
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "board.yml").write_text(_BOARD)
        project = FilesystemProject(tmp_path)
        compiled = compile_file(project.path("charts/board.yml").read_board())
        assert compiled.board is not None, compiled.errors
        registry = build_adapter_registry(project, profile_type="duckdb")

        result = _check(compiled, registry)

        assert result.status == "unchecked"
        # DuckDB's "does not exist" text has no finer classifier rule, so this
        # hits the generic bucket; the path moves to .detail.
        assert result.error == (
            "Could not open the warehouse: Could not connect to the "
            "warehouse. Check your connection details and try again."
        )

        query_result = registry.execute(
            compiled.query_registry["my_query"], variables={}, board=compiled.board
        )
        assert query_result.error is not None
        assert query_result.error.detail is not None
        assert str(missing_db) in query_result.error.detail

    def test_describe_returns_schema_not_rows(self, tmp_path):
        """DESCRIBE never triggers a full query execution.

        The table holds three rows; a real execution of `SELECT id FROM t` would
        report three single-column rows. DESCRIBE reports one column named `id`,
        so the returned columns are the assertion that separates the two.
        """
        compiled, registry = _duckdb_project(
            tmp_path,
            board_yml=_BOARD.replace("SELECT a, b FROM t", "SELECT id FROM t"),
            sql_setup="CREATE TABLE t (id INTEGER); INSERT INTO t VALUES (1), (2), (3)",
        )
        result = _check(compiled, registry)
        assert result.status == "valid"
        assert [c.name for c in result.columns] == ["id"]

    @pytest.mark.parametrize(
        "trailing",
        [
            ";",
            " -- keep an eye on this one",
            "; -- note",
            ";\n-- note",
            "; /* note */",
            ";;",
        ],
        ids=[
            "semicolon",
            "line_comment",
            "semicolon_and_line_comment",
            "semicolon_then_newline_comment",
            "semicolon_and_block_comment",
            "double_semicolon",
        ],
    )
    def test_trailing_artifacts_do_not_break_describe(self, tmp_path, trailing):
        """Pasted-from-an-IDE trailing punctuation parses fine on its own and
        must keep parsing fine once ``DESCRIBE`` is prepended.

        A previous ``DESCRIBE (\\n{sql}\\n)`` paren wrap needed a
        semicolon-strip helper to survive these — and each new trailing shape
        (a comment after the semicolon, a second semicolon) broke the helper
        again. ``DESCRIBE {sql}`` with no parens has nothing for a trailing
        artifact to land between, closing the whole defect class instead of
        chasing it with another regex branch.
        """
        # Set the trailing artifact on the compiled query directly rather than
        # round-tripping through board YAML text: some of these (a literal
        # newline before the comment) are not valid inside a plain YAML
        # scalar, and the YAML shape is not what this test is about.
        compiled, registry = _duckdb_project(tmp_path)
        result = warehouse_check(
            compiled.query_registry["my_query"].model_copy(
                update={"sql": f"SELECT a, b FROM t{trailing}"}
            ),
            board=compiled.board,
            adapter_registry=registry,
            query_name="my_query",
            query_registry=compiled.query_registry,
        )
        assert result.status == "valid", result.error

    @pytest.mark.parametrize(
        "sql",
        [
            "(SELECT a FROM t) UNION ALL (SELECT a FROM t)",
            "DESCRIBE t",
            "SHOW TABLES",
            "SELECT a FROM t; SELECT b FROM t",
        ],
        ids=[
            "leading_paren_compound",
            "authored_describe",
            "authored_show",
            "multi_statement",
        ],
    )
    def test_a_statement_the_wrap_cannot_lead_is_unchecked_before_anything_is_sent(
        self, tmp_path, sql
    ):
        """``DESCRIBE`` can only lead a single SELECT-family statement.

        A query that is itself a DESCRIBE/SHOW, one whose own leading
        parenthesized arm the prefix would bind to instead of the whole
        compound, or a multi-statement script the prefix reaches only the head
        of — for each, prepending the keyword yields a statement that is not
        "check this query", so the tier has nothing to report but its own
        limitation. Decided from the author's parsed statement *before* the
        wrap is built: nothing is sent, so no warehouse error has to be
        argued about afterwards.
        """
        compiled, registry = _duckdb_project(tmp_path)
        registry.execute = MagicMock(wraps=registry.execute)
        result = warehouse_check(
            compiled.query_registry["my_query"].model_copy(update={"sql": sql}),
            board=compiled.board,
            adapter_registry=registry,
            query_name="my_query",
            query_registry=compiled.query_registry,
        )
        assert result.status == "unchecked", result.error
        assert result.reason
        registry.execute.assert_not_called()

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT a, b FROM t ORDER BY a ASC DESC",
            "SELECT a, b FROM t WHERE a IN ()",
            "SELECT a, b FROM t LIMIT 1, 2",
            "SELECT a, b FROM t WHERE a = 1 AND",
        ],
        ids=[
            "doubled_order_direction",
            "empty_in_list",
            "mysql_style_limit_offset",
            "truncated_predicate",
        ],
    )
    def test_sql_the_warehouse_refuses_is_invalid(self, tmp_path, sql):
        """Genuinely broken SQL, refused by DuckDB's own parser.

        sqlglot (the guard) and DuckDB's parser disagree in both directions, so
        these land on whatever code the classifier has left — for three of them
        ``_classify_duckdb_error``'s ERR-WAREHOUSE-RUNTIME catch-all, for the
        fourth the guard's own ERR-UNPARSEABLE-SQL. Reading "whose fault" back
        out of that code is what reported all four as ``unchecked`` with exit 0;
        the wrappability gate above is what makes a rejection of the statement
        we *did* send attributable to the author without asking the code.
        """
        compiled, registry = _duckdb_project(tmp_path)
        result = warehouse_check(
            compiled.query_registry["my_query"].model_copy(update={"sql": sql}),
            board=compiled.board,
            adapter_registry=registry,
            query_name="my_query",
            query_registry=compiled.query_registry,
        )
        assert result.status == "invalid", result.reason
        assert result.error

    def test_a_query_limit_does_not_truncate_the_columns(self, tmp_path):
        """`limit: 2` bounds rows of the real query — DESCRIBE's rows are columns.

        Carrying the limit into the wrap makes a 3-column query report 2 columns,
        and the sweep then reports the third as missing from the result. The
        limit is set on the compiled query because that is where it lands: a
        `limit:` on an authored http query normalizes onto the
        compiled ``SqlQuery``, which is what the check receives.
        """
        compiled, registry = _duckdb_project(
            tmp_path,
            board_yml=_BOARD.replace("SELECT a, b FROM t", "SELECT a, b, c FROM t"),
            sql_setup="CREATE TABLE t (a INTEGER, b VARCHAR, c INTEGER)",
        )
        result = warehouse_check(
            compiled.query_registry["my_query"].model_copy(update={"limit": 2}),
            board=compiled.board,
            adapter_registry=registry,
            query_name="my_query",
            query_registry=compiled.query_registry,
        )
        assert result.status == "valid", result.error
        assert {c.name for c in result.columns} == {"a", "b", "c"}

    def test_a_dbt_profile_source_resolves_to_its_real_adapter(self, tmp_path):
        """A source reachable only through a dbt profile still gets DESCRIBEd.

        This is why the check resolves through ``resolve_query_source``: the
        project-sources-only resolver hands back the raw ``dbt_profile`` entry,
        which dispatches to "no-validity-primitive" and silently checks nothing.
        """
        db_path = tmp_path / "test.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE t (a INTEGER, b VARCHAR)")
        conn.close()

        (tmp_path / "profiles.yml").write_text(
            "p:\n"
            "  target: dev\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: duckdb\n"
            f"      path: {db_path}\n"
        )
        (tmp_path / "dbt_charts.yml").write_text(
            "name: p\n"
            "sources:\n"
            "  warehouse:\n"
            "    type: dbt_profile\n"
            "    profile: p\n"
            "    target: dev\n"
        )
        (tmp_path / "dbt_project.yml").write_text("name: p\nprofile: p\n")
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "board.yml").write_text(
            _BOARD.replace("source: testdb", "source: warehouse")
        )
        project = FilesystemProject(tmp_path)
        compiled = compile_file(project.path("charts/board.yml").read_board())
        assert compiled.board is not None, compiled.errors

        result = _check(
            compiled, build_adapter_registry(project, profile_type="duckdb")
        )
        assert result.adapter_type == "duckdb"
        assert result.status == "valid", result.error or result.reason
        assert {c.name for c in result.columns} == {"a", "b"}

    def test_a_marker_word_in_a_real_query_error_stays_invalid(self, tmp_path):
        """`credentials` as a column name must not read as a connection failure.

        The free-text scan for auth/connection wording downgrades `invalid` to
        `unchecked` — a warning, exit 0 — for a query that is genuinely broken.
        """
        compiled, registry = _duckdb_project(
            tmp_path,
            board_yml=_BOARD.replace("SELECT a, b FROM t", "SELECT credentials FROM t"),
        )
        result = _check(compiled, registry)
        assert result.status == "invalid", result.reason

    def test_board_variable_default_is_applied(self, tmp_path):
        """An undeclared variable would render empty and produce invalid SQL."""
        compiled, registry = _duckdb_project(
            tmp_path,
            board_yml=(
                "title: Test Board\n"
                "source: testdb\n"
                "variables:\n"
                "  min_a:\n"
                "    default: 1\n"
                "queries:\n"
                "  my_query: SELECT a, b FROM t WHERE a > {{ min_a }}\n" + _CHART
            ),
        )
        result = _check(compiled, registry)
        assert result.status == "valid", result.error
        assert {c.name for c in result.columns} == {"a", "b"}

    def test_setup_sql_runs_on_the_same_connection(self, tmp_path):
        """setup_sql creates the view the query selects from — drop it and it fails."""
        compiled, registry = _duckdb_project(
            tmp_path,
            board_yml=(
                "title: Test Board\n"
                "source: testdb\n"
                "queries:\n"
                "  my_query:\n"
                "    setup_sql: CREATE TEMP VIEW v AS SELECT a, b FROM t\n"
                "    sql: SELECT a, b FROM v\n" + _CHART
            ),
        )
        result = _check(compiled, registry)
        assert result.status == "valid", result.error
        assert {c.name for c in result.columns} == {"a", "b"}

    def test_a_rejected_setup_sql_is_invalid_not_unchecked(self, tmp_path):
        """The warehouse refused the setup_sql — that is a verdict, not silence.

        setup_sql runs through DuckDBAdapter._execute_setup_sql_duckdb, which
        catches the raw duckdb exception and (before this fix) routed it through
        handle_adapter_error — leaving error_code=None for anything that isn't
        already a DbtChartsError. _is_query_defect(None) is False, so a query the
        warehouse just rejected reported "unchecked" (exit 0 without --strict).
        """
        compiled, registry = _duckdb_project(
            tmp_path,
            board_yml=(
                "title: Test Board\n"
                "source: testdb\n"
                "queries:\n"
                "  my_query:\n"
                "    setup_sql: CREATE TEMP VIEW v AS SELECT missing_col FROM t\n"
                "    sql: SELECT * FROM v\n" + _CHART
            ),
        )
        result = _check(compiled, registry)
        assert result.status == "invalid", result.reason
        assert "missing_col" in result.error


@pytest.fixture
def fake_bigquery_client(monkeypatch):
    """Stand in for build_bigquery_client so the dry-run path opens no connection."""
    client = MagicMock()
    monkeypatch.setattr(
        "dbt_charts.core.connections.build_bigquery_client",
        lambda **kwargs: client,
    )
    return client


def _bigquery_registry(prepared_sql: str = "SELECT revenue FROM orders"):
    """Registry that resolves a BigQuery source and prepares SQL without I/O."""
    registry = MagicMock()
    registry.resolve_query_source.return_value = BigQuerySourceConfig(
        type="bigquery", project="my-proj", dataset="my-ds"
    )
    registry.prepare_sql.return_value = PreparedSql(
        sql=prepared_sql,
        setup_sql=None,
        dialect_name="bigquery",
        row_fetch_limit=resolve_effective_row_limit(None),
    )
    return registry


class TestWarehouseCheckBigQuery:
    """BigQuery path: native dry-run via google-cloud-bigquery, never executes."""

    def test_dry_run_flag_is_set(self, fake_bigquery_client):
        """dry_run=True must be set on the job config — asserts no real execution."""
        field = MagicMock()
        field.name = "revenue"
        field.field_type = "FLOAT"
        fake_bigquery_client.query.return_value = SimpleNamespace(schema=[field])

        result = _check_sql("SELECT revenue FROM orders", _bigquery_registry())

        assert result.status == "valid"
        assert result.mechanism == "bigquery-dry-run"
        assert result.columns_checked is True
        assert result.columns == [WarehouseCheckColumn(name="revenue", type="FLOAT")]
        assert fake_bigquery_client.query.call_args.kwargs["job_config"].dry_run is True

    def test_dry_run_sends_the_sql_execution_would_send(self, fake_bigquery_client):
        """Not the authored SQL — the prepared SQL, refs and variables resolved."""
        fake_bigquery_client.query.return_value = SimpleNamespace(schema=[])
        registry = _bigquery_registry(prepared_sql="SELECT 1 FROM `p.d.orders`")

        _check_sql("SELECT 1 FROM {{ ref('orders') }}", registry)

        assert registry.prepare_sql.called
        assert fake_bigquery_client.query.call_args.args[0] == (
            "SELECT 1 FROM `p.d.orders`"
        )

    def test_bigquery_error_returned_as_invalid(self, fake_bigquery_client):
        # BadRequest is what the SDK raises when the dry run rejects the SQL.
        fake_bigquery_client.query.side_effect = BadRequest(
            "Table not found: `proj.dataset.table`"
        )

        result = _check_sql("SELECT * FROM nonexistent_table", _bigquery_registry())

        assert result.status == "invalid"
        assert result.mechanism == "bigquery-dry-run"
        assert result.columns_checked is False
        assert "not found" in result.error.lower()

    def test_credentials_failure_is_unchecked_not_invalid(self, fake_bigquery_client):
        """A bad key says nothing about the SQL — calling it invalid asserts a lie."""
        fake_bigquery_client.query.side_effect = Exception(
            "Invalid JWT Signature: credentials could not be refreshed"
        )

        result = _check_sql("SELECT 1", _bigquery_registry())

        assert result.status == "unchecked"
        assert result.reason

    def test_client_construction_failure_is_unchecked_not_invalid(self, monkeypatch):
        """No ADC on this machine — the client never opens, before any query is sent.

        google.auth.exceptions.DefaultCredentialsError raises out of
        bigquery.Client(...) itself, not out of client.query(...); the
        fake_bigquery_client fixture stands in for an already-built client and
        cannot exercise this. If build_bigquery_client sits outside the check's
        try/except, this escapes uncaught and aborts every remaining board in the
        sweep instead of reporting this one query unchecked.
        """
        from google.auth.exceptions import DefaultCredentialsError

        def _raise_no_adc(**kwargs: object) -> None:
            raise DefaultCredentialsError(
                "Could not automatically determine credentials"
            )

        monkeypatch.setattr(
            "dbt_charts.core.connections.build_bigquery_client", _raise_no_adc
        )

        result = _check_sql("SELECT 1", _bigquery_registry())

        assert result.status == "unchecked"
        assert result.reason

    def test_per_table_access_denied_is_unchecked_not_invalid(
        self, fake_bigquery_client
    ):
        """BigQuery's per-table denial reads as a query error to any text scan."""
        fake_bigquery_client.query.side_effect = Forbidden(
            "Access Denied: Table p:d.t: User does not have permission to query table"
        )

        result = _check_sql("SELECT 1 FROM t", _bigquery_registry())

        assert result.status == "unchecked"
        assert result.reason

    def test_the_dry_run_resolves_unqualified_names_the_way_execution_does(
        self, fake_bigquery_client
    ):
        """The default dataset is what makes `FROM orders` mean anything.

        Execution sets it on every BigQuery connection
        (``sql_adapter.py`` ``_open``); a dry run without it answers
        "Table name 'orders' missing dataset" — reporting a healthy board
        invalid, since naming the dataset once on the source and then writing
        bare table names is the normal spelling.
        """
        fake_bigquery_client.query.return_value = SimpleNamespace(schema=[])

        _check_sql("SELECT 1 FROM orders", _bigquery_registry())

        job_config = fake_bigquery_client.query.call_args.kwargs["job_config"]
        # The SDK normalizes the "project.dataset" string into a DatasetReference.
        assert (
            job_config.default_dataset.project,
            job_config.default_dataset.dataset_id,
        ) == ("my-proj", "my-ds")

    def test_the_dry_run_is_sent_to_the_source_location(self, fake_bigquery_client):
        """A dataset outside the US is not visible from the default region."""
        fake_bigquery_client.query.return_value = SimpleNamespace(schema=[])
        registry = _bigquery_registry()
        registry.resolve_query_source.return_value = BigQuerySourceConfig(
            type="bigquery", project="my-proj", dataset="my-ds", location="EU"
        )

        _check_sql("SELECT 1", registry)

        assert fake_bigquery_client.query.call_args.kwargs["location"] == "EU"

    def test_columns_are_lowercased_to_match_what_execution_returns(
        self, fake_bigquery_client
    ):
        """`SqlAdapter` lowercases every result column, so lowercase is the only
        spelling that renders. Reporting the dry run's verbatim casing would
        reject `y: Revenue`'s working sibling `y: revenue` and accept the one
        that fails at render.
        """
        field = MagicMock()
        field.name = "Revenue"
        field.field_type = "FLOAT"
        fake_bigquery_client.query.return_value = SimpleNamespace(schema=[field])

        result = _check_sql("SELECT SUM(x) AS Revenue FROM t", _bigquery_registry())

        assert result.columns == [WarehouseCheckColumn(name="revenue", type="FLOAT")]

    def test_a_dropped_table_is_invalid_not_unchecked(self, fake_bigquery_client):
        """BigQuery raises NotFound — a sibling of BadRequest, not a subclass.

        Executing this SQL is classified ERR-BINDER-UNKNOWN-COLUMN, so checking
        it must not come back "the warehouse gave no verdict": a board pointing
        at a dropped table passing --warehouse is the exact silent pass this
        tier exists to prevent.
        """
        fake_bigquery_client.query.side_effect = NotFound(
            "404 Not found: Table my-proj:my-ds.gone was not found in location US"
        )

        result = _check_sql("SELECT * FROM gone", _bigquery_registry())

        assert result.status == "invalid"
        assert result.columns_checked is False

    @pytest.mark.parametrize(
        "trailing",
        ["", ";", "; -- note", ";\n", ";;"],
        ids=[
            "bare",
            "semicolon",
            "semicolon_and_comment",
            "semicolon_and_newline",
            "double_semicolon",
        ],
    )
    def test_setup_sql_is_dry_run_with_the_query(self, fake_bigquery_client, trailing):
        """A TEMP FUNCTION the query calls has to be in the same dry run.

        BigQuery gives a dry run no session, so sending only the query reports
        "Function not found" for SQL that renders fine. A script's dry run
        returns no schema, so validity is all this mechanism can claim there.

        The join is an explicit ``;\\n``, so a ``setup_sql`` that already ends
        in a semicolon — or in a comment that would swallow the appended one —
        is what makes ``_strip_trailing_semicolon_for_script_join`` load
        bearing: without it BigQuery gets ``;;`` and rejects a healthy board
        with ``ERR-WAREHOUSE-QUERY-INVALID``. Asserting the exact wire string
        rather than two substring probes is what lets that regress visibly.
        """
        fake_bigquery_client.query.return_value = SimpleNamespace(schema=None)
        registry = _bigquery_registry(prepared_sql="SELECT f(1)")
        registry.prepare_sql.return_value = PreparedSql(
            sql="SELECT f(1)",
            setup_sql=f"CREATE TEMP FUNCTION f(x INT64) AS (x + 1){trailing}",
            dialect_name="bigquery",
            row_fetch_limit=resolve_effective_row_limit(None),
        )

        result = _check_sql("SELECT f(1)", registry)

        assert fake_bigquery_client.query.call_args.args[0] == (
            "CREATE TEMP FUNCTION f(x INT64) AS (x + 1);\nSELECT f(1)"
        )
        assert result.status == "valid"
        assert result.columns_checked is False

    def test_a_dbt_profile_source_resolves_to_its_real_adapter(
        self, tmp_path, fake_bigquery_client
    ):
        """A BigQuery target reachable only through a dbt profile still dry-runs.

        Sibling of ``TestWarehouseCheckDuckDB.test_a_dbt_profile_source_resolves_to_its_real_adapter``:
        the resolver hands back a ``DbtTargetSourceConfig`` (not ``BigQuerySourceConfig``)
        for a dbt_profile source, and the DuckDB dispatch already routes on the type
        string. If BigQuery dispatch ever routed on ``isinstance(..., BigQuerySourceConfig)``
        instead, this would silently fall through to "no-validity-primitive" and dry-run
        nothing.
        """
        fake_bigquery_client.query.return_value = SimpleNamespace(schema=[])

        (tmp_path / "profiles.yml").write_text(
            "p:\n"
            "  target: dev\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: bigquery\n"
            "      method: oauth\n"
            "      project: my-proj\n"
            "      dataset: my-ds\n"
            "      threads: 4\n"
        )
        (tmp_path / "dbt_charts.yml").write_text(
            "name: p\n"
            "sources:\n"
            "  warehouse:\n"
            "    type: dbt_profile\n"
            "    profile: p\n"
            "    target: dev\n"
        )
        (tmp_path / "dbt_project.yml").write_text("name: p\nprofile: p\n")
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "board.yml").write_text(
            _BOARD.replace("source: testdb", "source: warehouse")
        )
        project = FilesystemProject(tmp_path)
        compiled = compile_file(project.path("charts/board.yml").read_board())
        assert compiled.board is not None, compiled.errors

        result = _check(
            compiled, build_adapter_registry(project, profile_type="bigquery")
        )
        assert result.adapter_type == "bigquery"
        assert result.mechanism == "bigquery-dry-run"
        assert result.status == "valid", result.error or result.reason

    def test_a_source_without_a_project_names_the_missing_setting(
        self, tmp_path, fake_bigquery_client
    ):
        """A dbt_profile BigQuery target may legitimately carry no project.

        dbt's own credentials accept ``method: oauth`` with a dataset alone and
        let ADC supply the project at connect time, so ``config["project"]``
        raised KeyError inside the SDK's blind ``except`` — the whole
        author-facing explanation became the string ``'project'``, on every
        query of the board.
        """
        (tmp_path / "profiles.yml").write_text(
            "p:\n"
            "  target: dev\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: bigquery\n"
            "      method: oauth\n"
            "      dataset: my-ds\n"
            "      threads: 4\n"
        )
        (tmp_path / "dbt_charts.yml").write_text(
            "name: p\n"
            "sources:\n"
            "  warehouse:\n"
            "    type: dbt_profile\n"
            "    profile: p\n"
            "    target: dev\n"
        )
        (tmp_path / "dbt_project.yml").write_text("name: p\nprofile: p\n")
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "board.yml").write_text(
            _BOARD.replace("source: testdb", "source: warehouse")
        )
        project = FilesystemProject(tmp_path)
        compiled = compile_file(project.path("charts/board.yml").read_board())
        assert compiled.board is not None, compiled.errors

        result = _check(
            compiled, build_adapter_registry(project, profile_type="bigquery")
        )

        assert result.status == "unchecked"
        assert "project" in result.reason
        assert result.reason != "the warehouse gave no verdict on it ('project')"
        fake_bigquery_client.query.assert_not_called()

    def test_a_prepare_failure_with_a_defect_code_is_invalid(
        self, fake_bigquery_client
    ):
        """prepare_sql can fail before any SQL exists to dry-run — e.g. an
        unresolved dbt ref. AdapterRegistry.prepare_sql then hands back a
        QueryResult carrying that failure instead of a PreparedSql, and this
        branch was previously untested: every other BigQuery test hard-sets
        ``prepare_sql.return_value = PreparedSql(...)``, so
        ``isinstance(prepared, QueryResult)`` was never True in any test.
        """
        registry = MagicMock()
        registry.resolve_query_source.return_value = BigQuerySourceConfig(
            type="bigquery", project="my-proj", dataset="my-ds"
        )
        registry.prepare_sql.return_value = QueryResult(
            data=[], error=QueryError("unresolved dbt ref", code=ERR_WAREHOUSE_RUNTIME)
        )

        result = _check_sql("SELECT * FROM {{ ref('gone') }}", registry)

        assert result.status == "invalid"
        assert result.error
        fake_bigquery_client.query.assert_not_called()

    def test_a_prepare_failure_with_no_error_is_unchecked(self, fake_bigquery_client):
        """A QueryResult with neither wire SQL nor an error leaves nothing to
        send — reported unchecked with a reason, never silently passed
        through as valid."""
        registry = MagicMock()
        registry.resolve_query_source.return_value = BigQuerySourceConfig(
            type="bigquery", project="my-proj", dataset="my-ds"
        )
        registry.prepare_sql.return_value = QueryResult(data=[], error=None)

        result = _check_sql("SELECT 1", registry)

        assert result.status == "unchecked"
        assert result.reason
        fake_bigquery_client.query.assert_not_called()


class TestWarehouseCheckUnchecked:
    """An adapter with no cheap validity primitive reports unchecked — never valid."""

    def _mysql_registry(self):
        from dbt_charts.core.compile.models.source import MySQLSourceConfig

        registry = MagicMock()
        registry.resolve_query_source.return_value = MySQLSourceConfig(
            type="mysql", host="db", database="d", user="u", password="p"
        )
        return registry

    def test_unallowlisted_adapter_is_unchecked_not_valid(self):
        """mysql has an EXPLAIN too, but no branch here yet (see the module
        docstring)."""
        result = _check_sql("SELECT 1", self._mysql_registry())
        assert result.status == "unchecked"
        assert result.mechanism == "no-validity-primitive"
        assert result.columns_checked is False
        assert "mysql" in result.reason

    def test_unallowlisted_adapter_never_calls_execute(self):
        registry = self._mysql_registry()
        _check_sql("SELECT 1", registry)
        registry.execute.assert_not_called()

    def test_databricks_is_unchecked_with_the_embedded_error_reason(self):
        """Spark's EXPLAIN returns planner errors as plan *text* instead of
        failing the statement, so an EXPLAIN branch would report a broken query
        valid — the one outcome this module forbids. Held out deliberately."""
        from dbt_charts.core.compile.models.source import DbtTargetSourceConfig

        registry = MagicMock()
        registry.resolve_query_source.return_value = DbtTargetSourceConfig(
            type="databricks"
        )
        result = _check_sql("SELECT 1", registry)
        assert result.status == "unchecked"
        assert result.columns_checked is False
        assert "plan text" in result.reason
        registry.execute.assert_not_called()


class TestWarehouseCheckExplain:
    """EXPLAIN tier: postgres/redshift/snowflake get a validity-only check.

    A first EXPLAIN tier was cut from PR #7266 because its wrap guard inferred
    fault from warehouse errors and kept producing false-invalids. The shipped
    guard now rules on the author's own parsed statement before anything is
    sent — these tests pin the tier to that gate, not a new one.
    """

    def _registry(self, adapter_type: str, result: QueryResult) -> MagicMock:
        registry = MagicMock()
        registry.resolve_query_source.return_value = SimpleNamespace(type=adapter_type)
        registry.execute.return_value = result
        return registry

    @pytest.mark.parametrize("adapter_type", ["postgres", "redshift", "snowflake"])
    def test_valid_query_is_validity_only(self, adapter_type):
        """A clean EXPLAIN proves the query binds but yields a plan, not a
        result schema — columns stay unchecked and unread."""
        registry = self._registry(
            adapter_type, QueryResult(data=[{"QUERY PLAN": "Seq Scan on t"}])
        )
        result = _check_sql("SELECT a FROM t", registry)
        assert result.status == "valid"
        assert result.mechanism == "EXPLAIN"
        assert result.adapter_type == adapter_type
        assert result.columns_checked is False
        assert result.columns == []
        # The reason is the whole detail describe_query's refusal can show —
        # an empty one renders "Cannot list columns without running the query: ."
        assert "no result schema" in result.reason

    def test_sends_the_explain_prefixed_sql_with_no_limit(self):
        registry = self._registry("postgres", QueryResult(data=[]))
        _check_sql("SELECT a FROM t", registry)
        sent = registry.execute.call_args.args[0]
        assert sent.sql.startswith("EXPLAIN ")
        assert sent.sql.endswith("SELECT a FROM t")
        assert sent.limit is None

    def test_warehouse_rejection_is_invalid(self):
        registry = self._registry(
            "postgres",
            QueryResult(
                data=[],
                error=QueryError(
                    'column "customer_id" does not exist', code=ERR_WAREHOUSE_RUNTIME
                ),
            ),
        )
        result = _check_sql("SELECT customer_id FROM t", registry)
        assert result.status == "invalid"
        assert "customer_id" in result.error

    def test_connection_fault_is_unchecked_not_invalid(self):
        registry = self._registry(
            "snowflake",
            QueryResult(
                data=[],
                error=QueryError("could not connect", code=ERR_WAREHOUSE_CONNECTION),
            ),
        )
        result = _check_sql("SELECT 1", registry)
        assert result.status == "unchecked"
        assert result.reason

    def test_unwrappable_statement_names_explain(self):
        """The gate's refusal must name the keyword actually in play."""
        registry = self._registry("postgres", QueryResult(data=[]))
        result = _check_sql("SELECT 1; SELECT 2", registry)
        assert result.status == "unchecked"
        assert "EXPLAIN" in result.reason
        registry.execute.assert_not_called()

    def test_leading_parenthesized_arm_is_refused(self):
        """Postgres parses `EXPLAIN (SELECT …)` as EXPLAIN's *options list*,
        rejecting SQL the author wrote correctly — the gate refuses the shape
        before anything is sent."""
        registry = self._registry("postgres", QueryResult(data=[]))
        result = _check_sql("(SELECT 1) UNION ALL (SELECT 2)", registry)
        assert result.status == "unchecked"
        registry.execute.assert_not_called()


class TestWarehouseCheckFileSource:
    """A csv/json/parquet source resolves to the duckdb DESCRIBE mechanism: it
    executes on DuckDB (via the file-source materializer), so DuckDB's own
    DESCRIBE answers it too — see the dispatch branch in warehouse_check.py."""

    def _csv_registry(self, result: QueryResult) -> MagicMock:
        from dbt_charts.core.compile.models.source import CsvSourceConfig

        registry = MagicMock()
        registry.resolve_query_source.return_value = CsvSourceConfig(
            type="csv", files={"orders": "data/orders.csv"}
        )
        registry.execute.return_value = result
        return registry

    def test_valid_query_reads_columns_from_the_materialized_table(self):
        registry = self._csv_registry(
            QueryResult(data=[{"column_name": "region", "column_type": "VARCHAR"}])
        )
        result = _check_sql("SELECT region FROM orders", registry)
        assert result.status == "valid"
        assert result.mechanism == "DESCRIBE"
        assert result.adapter_type == "csv"
        assert result.columns_checked is True
        assert result.columns == [WarehouseCheckColumn(name="region", type="VARCHAR")]

    def test_sends_the_describe_prefixed_sql_with_no_limit(self):
        registry = self._csv_registry(QueryResult(data=[]))
        _check_sql("SELECT region FROM orders", registry)
        sent = registry.execute.call_args.args[0]
        assert sent.sql.startswith("DESCRIBE ")
        assert sent.sql.endswith("SELECT region FROM orders")
        assert sent.limit is None

    def test_unwrappable_statement_stays_unchecked_without_reaching_the_materializer(
        self,
    ):
        """An authored EXPLAIN can't be led by DESCRIBE — the gate refuses the
        shape from the author's own parsed statement before anything reaches
        the materializer, same as every other DESCRIBE adapter."""
        registry = self._csv_registry(QueryResult(data=[]))
        result = _check_sql("EXPLAIN SELECT 1", registry)
        assert result.status == "unchecked"
        assert "DESCRIBE" in result.reason
        registry.execute.assert_not_called()


class TestWarehouseCheckClickHouse:
    """ClickHouse takes a bare DESCRIBE prefix like DuckDB, but names the
    schema columns ``name``/``type`` rather than ``column_name``/``column_type``."""

    def _registry(self, result: QueryResult) -> MagicMock:
        from dbt_charts.core.compile.models.source import DbtTargetSourceConfig

        registry = MagicMock()
        registry.resolve_query_source.return_value = DbtTargetSourceConfig(
            type="clickhouse", host="h", schema="analytics"
        )
        registry.execute.return_value = result
        return registry

    def test_valid_query_reads_columns_from_describe(self):
        registry = self._registry(
            QueryResult(
                data=[
                    {"name": "month", "type": "Date", "default_type": ""},
                    {"name": "revenue", "type": "Float64", "default_type": ""},
                ]
            )
        )
        result = _check_sql("SELECT month, revenue FROM orders", registry)
        assert result.status == "valid"
        assert result.mechanism == "DESCRIBE"
        assert result.adapter_type == "clickhouse"
        assert result.columns_checked is True
        assert result.columns == [
            WarehouseCheckColumn(name="month", type="Date"),
            WarehouseCheckColumn(name="revenue", type="Float64"),
        ]

    def test_sends_the_describe_prefixed_sql_with_no_limit(self):
        registry = self._registry(QueryResult(data=[]))
        _check_sql("SELECT month FROM orders", registry)
        sent = registry.execute.call_args.args[0]
        assert sent.sql.startswith("DESCRIBE ")
        assert sent.sql.endswith("SELECT month FROM orders")
        assert sent.limit is None

    def test_warehouse_rejection_is_invalid(self):
        registry = self._registry(
            QueryResult(
                data=[],
                error=QueryError(
                    "Code: 47. DB::Exception: Unknown expression identifier `nope`",
                    code=ERR_WAREHOUSE_RUNTIME,
                ),
            )
        )
        result = _check_sql("SELECT nope FROM orders", registry)
        assert result.status == "invalid"
        assert "nope" in result.error


class TestWarehouseCheckCacheRef:
    """A cache ref composes a render-time result no warehouse has heard of."""

    def test_jinja_cache_ref_is_unchecked_with_a_reason(self):
        registry = MagicMock()
        result = _check_sql("SELECT {{ queries.myquery.cache }} AS x", registry)
        assert result.status == "unchecked"
        assert result.mechanism == "cache-ref"
        assert "cached result" in result.reason
        registry.resolve_query_source.assert_not_called()
        registry.execute.assert_not_called()


class TestWarehouseCheckConnectionFailures:
    """A DuckDB warehouse that was never opened has not ruled on anybody's SQL.

    DuckDB opens its connection inside the block that classifies query errors,
    so an unopenable file used to arrive carrying ERR-WAREHOUSE-RUNTIME — a
    query-defect code — and condemned every query in the board.
    """

    def test_an_unopenable_duckdb_file_is_unchecked_not_invalid(self, tmp_path):
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  testdb:\n"
            "    type: duckdb\n"
            f"    path: {tmp_path / 'no-such-dir' / 'test.duckdb'}\n"
        )
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "board.yml").write_text(_BOARD)
        project = FilesystemProject(tmp_path)
        compiled = compile_file(project.path("charts/board.yml").read_board())
        assert compiled.board is not None, compiled.errors

        result = _check(
            compiled, build_adapter_registry(project, profile_type="duckdb")
        )

        assert result.status == "unchecked"
        assert result.reason


class TestWarehouseCheckDbtManifestFallback:
    """A board on an auto-detected dbt project, with no `sources:` registered.

    `resolve_query_source` legitimately returns None for a SqlQuery whose
    authored `source:` names nothing in board/project sources when a dbt
    project is in scope — DefaultSourceResolver's dbt fallback, the same path
    `dct render` uses.
    """

    def _dbt_project(self, tmp_path: Path, sql: str, monkeypatch: pytest.MonkeyPatch):
        import json

        # A machine exporting DBT_PROFILES_DIR outranks the project-local
        # profiles.yml this helper writes (_read_profiles_yml's resolution
        # order), which would make this fixture read the wrong file wherever
        # that env var happens to be set.
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

        db_path = tmp_path / "repro.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE makers (company_name VARCHAR, country VARCHAR)")
        conn.execute("INSERT INTO makers VALUES ('Instruo', 'United Kingdom')")
        conn.close()

        (tmp_path / "dbt_project.yml").write_text(
            "name: repro\nversion: '1.0.0'\nconfig-version: 2\nprofile: repro\n"
        )
        (tmp_path / "profiles.yml").write_text(
            "repro:\n"
            "  target: dev\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: duckdb\n"
            f"      path: {db_path}\n"
            "      schema: main\n"
            "      threads: 1\n"
        )
        (tmp_path / "target").mkdir()
        manifest = {
            "nodes": {
                "model.repro.dim_makers": {
                    "resource_type": "model",
                    "name": "dim_makers",
                    "schema": "main",
                    "alias": "makers",
                }
            },
            "sources": {},
        }
        (tmp_path / "target" / "manifest.json").write_text(json.dumps(manifest))

        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "count.yml").write_text(
            "title: Maker count\n"
            "source: repro\n"
            "queries:\n"
            "  q:\n"
            f'    sql: "{sql}"\n'
            "charts:\n"
            "  c:\n"
            "    query: q\n"
            "    type: table\n"
        )

        project = FilesystemProject(tmp_path)
        result = compile_file(project.path("charts/count.yml").read_board())
        assert result.board is not None, result.errors
        return result, build_adapter_registry(project, profile_type="duckdb")

    def test_dbt_jinja_query_is_checked_via_the_dbt_adapter(
        self, tmp_path, monkeypatch
    ):
        """`repro` is not a board or project source (no `dbt_charts.yml`)."""
        compiled, registry = self._dbt_project(
            tmp_path,
            "select company_name, country from {{ ref('dim_makers') }}",
            monkeypatch,
        )
        result = _check(compiled, registry, query_name="q")
        assert result.status == "valid", result.error
        assert result.mechanism == "DESCRIBE"
        assert result.adapter_type == "duckdb"
        assert result.columns_checked is True
        assert {c.name for c in result.columns} == {"company_name", "country"}

    def test_plain_sql_query_is_checked_via_the_duckdb_adapter(
        self, tmp_path, monkeypatch
    ):
        """No `ref()`, so `DbtAdapter` declines and `DuckDBAdapter` claims it
        instead, finding the project's own warehouse file by the dbt-project
        auto-discovery convention (`data/dev.duckdb`)."""
        (tmp_path / "data").mkdir()
        dev_db = tmp_path / "data" / "dev.duckdb"
        conn = duckdb.connect(str(dev_db))
        conn.execute("CREATE TABLE makers (company_name VARCHAR, country VARCHAR)")
        conn.close()

        compiled, registry = self._dbt_project(
            tmp_path, "select company_name, country from makers", monkeypatch
        )
        result = _check(compiled, registry, query_name="q")
        assert result.status == "valid", result.error
        assert result.mechanism == "DESCRIBE"
        assert result.adapter_type == "duckdb"
        assert {c.name for c in result.columns} == {"company_name", "country"}


class TestWarehouseCheckDbtFallbackTypeResolution:
    """`_resolve_dbt_fallback_type` in isolation: adapter routing and
    profile-failure reporting, with the registry mocked."""

    def _registry(self, claiming_adapter):
        registry = MagicMock()
        registry.resolve_query_source.return_value = None
        registry.get_adapter.return_value = claiming_adapter
        return registry

    def test_no_claiming_adapter_is_unchecked_with_no_source_mechanism(self):
        registry = self._registry(None)
        result = _check_sql("SELECT 1", registry)
        assert result.status == "unchecked"
        assert result.mechanism == "no-source"
        assert result.reason
        registry.execute.assert_not_called()

    def test_unresolvable_dbt_profile_reports_the_real_failure_in_the_reason(self):
        from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter

        adapter = MagicMock(spec=DbtAdapter)
        adapter.resolve_target_type.side_effect = ValueError("Profile 'x' not found")
        result = _check_sql("SELECT {{ ref('x') }}", self._registry(adapter))
        assert result.status == "unchecked"
        assert result.mechanism == "no-source"
        assert "Profile 'x' not found" in result.reason
        adapter.resolve_target_type.assert_called_once()

    def test_bigquery_fallback_type_without_a_resolved_config_is_unchecked(self):
        from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter
        from dbt_charts.core.execute.warehouse_check import _BIGQUERY_DRY_RUN

        adapter = MagicMock(spec=DbtAdapter)
        adapter.resolve_target_type.return_value = "bigquery"
        registry = self._registry(adapter)
        result = _check_sql("SELECT {{ ref('x') }}", registry)
        assert result.status == "unchecked"
        assert result.adapter_type == "bigquery"
        assert result.mechanism == _BIGQUERY_DRY_RUN
        registry.execute.assert_not_called()

    def test_databricks_fallback_type_is_unchecked_not_a_crash(self):
        """A dbt-fallback type outside `_PREFIX_CHECKS ∪ {bigquery}` still
        reaches the `is_file_source(source_config)` guard with
        `source_config=None` — the guard's `source_config is not None` clause
        is the only thing standing between this and an `AttributeError`."""
        from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter

        adapter = MagicMock(spec=DbtAdapter)
        adapter.resolve_target_type.return_value = "databricks"
        registry = self._registry(adapter)
        result = _check_sql("SELECT {{ ref('x') }}", registry)
        assert result.status == "unchecked"
        assert result.adapter_type == "databricks"
        assert result.mechanism == "no-validity-primitive"
        assert "plan text" in result.reason
        registry.execute.assert_not_called()

    def test_routes_on_the_composed_query_not_the_authored_one(self):
        """A `ref()` reachable only through `{{ queries.base }}` must still
        route to `DbtAdapter`: routing has to run after composition, or a
        query whose own SQL carries no jinja gets handed to `DuckDBAdapter`
        instead of the warehouse that will actually run it."""
        import yaml

        from dbt_charts.core.compile import compile as compile_board
        from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter

        compiled = compile_board(
            yaml.dump(
                {
                    "source": "repro",
                    "queries": {
                        "base": "select * from {{ ref('dim_makers') }}",
                        "agg": "select * from ({{ queries.base }}) t",
                    },
                    "charts": {"c": {"query": "agg", "type": "table"}},
                }
            )
        )
        assert compiled.board is not None, compiled.errors

        adapter = MagicMock(spec=DbtAdapter)
        adapter.resolve_target_type.return_value = "postgres"
        registry = self._registry(adapter)
        registry.execute.return_value = QueryResult(data=[])

        result = warehouse_check(
            compiled.query_registry["agg"],
            board=compiled.board,
            adapter_registry=registry,
            query_name="agg",
            query_registry=compiled.query_registry,
        )

        routed_sql = registry.get_adapter.call_args.args[0].sql
        assert "{{ queries" not in routed_sql
        assert "dim_makers" in routed_sql
        assert result.adapter_type == "postgres"
        assert result.mechanism == "EXPLAIN"


class TestPrepareSqlThroughARealRegistry:
    """The dry run's SQL comes from a real AdapterRegistry, not a canned string.

    Every other BigQuery test injects a MagicMock registry, which cannot catch a
    divergence between what `prepare_sql` composes and what execution sends —
    and that divergence is where the dry run's defects have actually lived.
    """

    def _bigquery_project(self, tmp_path: Path, board_yml: str):
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  warehouse:\n"
            "    type: bigquery\n"
            "    project: my-proj\n"
            "    dataset: my-ds\n"
        )
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "board.yml").write_text(board_yml)
        project = FilesystemProject(tmp_path)
        compiled = compile_file(project.path("charts/board.yml").read_board())
        assert compiled.board is not None, compiled.errors
        return compiled, build_adapter_registry(project, profile_type="bigquery")

    def test_a_query_chain_is_composed_into_the_dry_run(
        self, tmp_path, fake_bigquery_client
    ):
        fake_bigquery_client.query.return_value = SimpleNamespace(schema=[])
        compiled, registry = self._bigquery_project(
            tmp_path,
            "title: Real Registry\n"
            "source: warehouse\n"
            "queries:\n"
            "  base: SELECT id, revenue FROM orders\n"
            "  top: SELECT id FROM {{ queries.base }}\n"
            "charts:\n"
            "  c:\n"
            "    query: top\n"
            "    type: table\n",
        )

        result = _check(compiled, registry, query_name="top")

        sent = fake_bigquery_client.query.call_args.args[0]
        assert "SELECT id, revenue FROM orders" in sent
        assert "{{" not in sent
        assert result.status == "valid"

    def test_the_job_config_matches_what_execution_sets(
        self, tmp_path, fake_bigquery_client
    ):
        """Same default dataset execution binds to the connection, and no billing."""
        fake_bigquery_client.query.return_value = SimpleNamespace(schema=[])
        compiled, registry = self._bigquery_project(
            tmp_path,
            "title: Real Registry\n"
            "source: warehouse\n"
            "queries:\n"
            "  my_query: SELECT a FROM t\n" + _CHART,
        )

        _check(compiled, registry)

        job_config = fake_bigquery_client.query.call_args.kwargs["job_config"]
        assert (
            job_config.default_dataset.project,
            job_config.default_dataset.dataset_id,
        ) == ("my-proj", "my-ds")
        assert job_config.dry_run is True
        assert job_config.use_query_cache is False


class TestCheckAdHocQuery:
    """check_ad_hoc_query — the no-board entry point ``describe_query`` uses.

    Same dispatch as ``warehouse_check``, driven off a synthesized empty board
    instead of a compiled one — there is no board behind a hand-typed SQL
    string. Each case pins one adapter's row of the dispatch table so a
    regression that quietly widens back to full execution fails here first.
    """

    def test_bigquery_dry_run_never_executes(self, fake_bigquery_client):
        field = MagicMock()
        field.name = "revenue"
        field.field_type = "FLOAT"
        fake_bigquery_client.query.return_value = SimpleNamespace(schema=[field])

        result = check_ad_hoc_query(
            "SELECT revenue FROM orders",
            source="s",
            adapter_registry=_bigquery_registry(),
        )

        assert result.status == "valid"
        assert result.mechanism == "bigquery-dry-run"
        assert result.columns_checked is True
        assert result.columns == [WarehouseCheckColumn(name="revenue", type="FLOAT")]
        assert fake_bigquery_client.query.call_args.kwargs["job_config"].dry_run is True

    def test_bigquery_rejection_is_invalid(self, fake_bigquery_client):
        fake_bigquery_client.query.side_effect = BadRequest("Syntax error near FORM")

        result = check_ad_hoc_query(
            "SELECT 1 FORM orders",
            source="s",
            adapter_registry=_bigquery_registry(),
        )

        assert result.status == "invalid"
        assert "Syntax error" in result.error

    def test_postgres_explain_checks_validity_only(self):
        from dbt_charts.core.compile.models.source import parse_source_config

        registry = MagicMock()
        registry.resolve_query_source.return_value = parse_source_config(
            {
                "type": "postgres",
                "host": "h",
                "dbname": "d",
                "user": "u",
                "password": "p",
            }
        )
        registry.execute.return_value = QueryResult(data=[{"QUERY PLAN": "Result"}])

        result = check_ad_hoc_query("SELECT 1", source="s", adapter_registry=registry)

        assert result.status == "valid"
        assert result.mechanism == "EXPLAIN"
        assert result.columns_checked is False
        assert registry.execute.call_args.args[0].sql == "EXPLAIN SELECT 1"

    def test_duckdb_describe_via_synthesized_board(self, tmp_path):
        """DuckDB DESCRIBE still works with no real board behind the query."""
        compiled, registry = _duckdb_project(tmp_path)

        result = check_ad_hoc_query(
            "SELECT a, b FROM t", source="testdb", adapter_registry=registry
        )

        assert result.status == "valid"
        assert result.mechanism == "DESCRIBE"
        assert result.columns_checked is True
        assert {c.name for c in result.columns} == {"a", "b"}
