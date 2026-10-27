"""Security regression: a composed query's variable values are data, not template.

A query holding ``{{ queries.X }}`` used to be rendered twice — once to expand the
reference and substitute variables, once again on the way to the warehouse. The
second render read the first render's output, so a variable value sat in template
position and was evaluated: ``{{ 7*7 }}`` arrived as ``49``, and a value naming a
Jinja global reached the interpreter's globals and ran a shell command on the
render host.

The same double pass inlined values as bare ``str(value)`` with no escaping, so a
composed query also lost the parameter binding that the identical SQL gets without
the reference.

Both follow from rendering twice, and both are covered here at the boundary that
matters: what reaches the warehouse.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import PostgresSourceConfig
from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry
from dbt_charts.core.execute.adapters.base import QueryResult
from dbt_charts.core.execute.adapters.sql_adapter import PreparedSql, SqlAdapter
from dbt_charts.core.execute.executor import Executor

# Reaches the Jinja interpreter's globals from an ordinary expression. Inert as a
# value; remote code execution the moment something renders it as a template.
GLOBALS_PAYLOAD = "{{ cycler.__init__.__globals__.os.popen('id').read() }}"

# Evaluates to 49 if — and only if — the value is rendered.
ARITH_PAYLOAD = "{{ 7*7 }}"

# Closes the surrounding literal and appends a tautology. Neutralized by binding
# the value as a parameter, not by escaping it.
SQLI_PAYLOAD = "active' OR '1'='1"


def _board_yaml(composed_sql: str) -> str:
    """A two-query board: `base`, and a `composed` query that references it."""
    indented = "\n".join(f"      {line}" for line in composed_sql.strip().splitlines())
    return "\n".join(
        [
            "source: db",
            "variables:",
            "  s:",
            "    input: text",
            "    default: active",
            "queries:",
            "  base:",
            "    sql: |",
            "      SELECT 1 AS id, 'active' AS status",
            "      UNION ALL",
            "      SELECT 2 AS id, 'inactive' AS status",
            "    source: db",
            "  composed:",
            "    sql: |",
            indented,
            "    source: db",
            "charts:",
            "  dummy:",
            "    type: bar",
            "    x: id",
            "    y: id",
            "    query: composed",
        ]
    )


def _executor(
    yaml_body: str,
    local_project: Callable[..., FilesystemProject],
    source: dict[str, Any],
) -> Executor:
    result = compile(yaml_body)
    assert result.success, [str(e) for e in result.errors]
    assert result.board is not None
    project = local_project(Path.cwd())
    # sources is a cached_property; pre-populate its cache via the writable
    # instance dict (vars()), matching the sibling composed-query suite.
    vars(project)["sources"] = ProjectSourcesConfig(sources={"db": source})
    return Executor(result.board, build_adapter_registry(project), use_cache=False)


class TestComposedQueryValuesAreNeverRendered:
    """End-to-end through the executor: a variable value must reach the warehouse
    exactly as supplied, whatever it looks like."""

    @pytest.mark.parametrize(
        ("label", "payload"),
        [("arithmetic", ARITH_PAYLOAD), ("jinja_globals", GLOBALS_PAYLOAD)],
    )
    def test_jinja_in_a_value_arrives_verbatim(
        self,
        label: str,
        payload: str,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """A value that looks like a template is echoed back unchanged.

        Selecting the variable back out is the whole assertion: whatever the
        warehouse returns is what the warehouse was given.
        """
        executor = _executor(
            _board_yaml("SELECT '{{ s }}' AS echoed FROM {{ queries.base }} LIMIT 1"),
            local_project,
            {"type": "duckdb", "path": ":memory:"},
        )

        rows = executor.execute_query("composed", {"s": payload})

        assert rows == [{"echoed": payload}], f"{label} payload was not passed through"

    def test_jinja_globals_payload_does_not_execute(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The globals payload must not run a command.

        Asserted separately from verbatim arrival because the failure mode is
        specific and worth naming: `id` output in the result set means the render
        host executed it.
        """
        executor = _executor(
            _board_yaml("SELECT '{{ s }}' AS echoed FROM {{ queries.base }} LIMIT 1"),
            local_project,
            {"type": "duckdb", "path": ":memory:"},
        )

        echoed = executor.execute_query("composed", {"s": GLOBALS_PAYLOAD})[0]["echoed"]

        assert "uid=" not in echoed and "gid=" not in echoed, (
            "variable value was evaluated as a template and ran a shell command"
        )

    def test_value_is_bound_not_spliced_into_the_sql(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A quote-closing value must match no row rather than rewrite the predicate.

        The same SQL without the {{ queries.base }} reference has always bound this
        value as a parameter; composing must not quietly opt out of that.
        """
        executor = _executor(
            _board_yaml(
                "SELECT * FROM {{ queries.base }} WHERE base.status = '{{ s }}'"
            ),
            local_project,
            {"type": "duckdb", "path": ":memory:"},
        )

        rows = executor.execute_query("composed", {"s": SQLI_PAYLOAD})

        assert rows == [], "value was spliced into the SQL as a literal"

    def test_filter_helper_still_binds_inside_a_composed_query(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The filter helper keeps working, and keeps parameterizing.

        Composed + filter() is the combination the deferral mechanism existed to
        serve; it has to survive that mechanism's removal.
        """
        executor = _executor(
            _board_yaml(
                "SELECT * FROM {{ queries.base }} WHERE {{ filter('status', s) }}"
            ),
            local_project,
            {"type": "duckdb", "path": ":memory:"},
        )

        assert executor.execute_query("composed", {"s": "active"}) == [
            {"id": 1, "status": "active"}
        ]
        assert executor.execute_query("composed", {"s": SQLI_PAYLOAD}) == []


class TestSqlAdapterReceivesBoundValues:
    """The two ways SQL reaches SqlAdapter — with params pre-computed by the
    registry, and with the adapter rendering for itself — must both bind values."""

    @staticmethod
    def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        """Intercept at the warehouse boundary and record what would be sent."""
        seen: dict[str, Any] = {}

        def _fake(
            self: SqlAdapter,
            prepared: PreparedSql,
            query: SqlQuery,
            source_config: dict[str, Any] | None,
        ) -> QueryResult:
            seen["sql"] = prepared.sql
            return QueryResult(data=[], columns=[], schema=literal_schema([], []))

        monkeypatch.setattr(SqlAdapter, "_execute_via_dbt_adapter", _fake)
        return seen

    _PG_SOURCE = {
        "type": "postgres",
        "host": "h",
        "dbname": "db",
        "user": "u",
        "password": "p",
    }

    def test_registry_render_path_binds_the_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Executor → registry → SqlAdapter, the path a rendered board takes."""
        seen = self._capture(monkeypatch)
        executor = _executor(
            _board_yaml(
                "SELECT * FROM {{ queries.base }} WHERE base.status = '{{ s }}'"
            ),
            local_project,
            self._PG_SOURCE,
        )

        executor.execute_query("composed", {"s": ARITH_PAYLOAD})

        assert ARITH_PAYLOAD in seen["sql"], (
            "value was not inlined as a literal in the SQL"
        )
        assert "49" not in seen["sql"], "value was evaluated as a template"

    def test_adapter_render_path_binds_the_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """SqlAdapter rendering for itself, over already-expanded composed SQL.

        The SQL written here is what reference expansion now emits: the reference
        replaced by its subquery, every variable still author-written.
        """
        seen = self._capture(monkeypatch)
        adapter = SqlAdapter(
            project=local_project(Path.cwd()),
            dbt_project_path=None,
            profile_type="postgres",
        )
        query = SqlQuery(
            sql="SELECT * FROM (SELECT 1 AS id) AS base WHERE base.id = '{{ s }}'",
            source="db",
        )

        adapter._execute(
            query,
            {"s": ARITH_PAYLOAD},
            source_config=PostgresSourceConfig(**self._PG_SOURCE),
        )

        assert ARITH_PAYLOAD in seen["sql"], (
            "value was not inlined as a literal in the SQL"
        )
        assert "49" not in seen["sql"], "value was evaluated as a template"
