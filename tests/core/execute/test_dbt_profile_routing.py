"""Regression tests for dbt_profile source routing through the adapter registry.

The bug: dbt_profile sources expand LATE (inside SqlAdapter._execute) after
routing already committed. A dbt_profile→duckdb query stays on SqlAdapter and
falls through to _execute_via_dbt_adapter (requires dbt.adapters.duckdb, opens
file writable). The fix expands dbt_profile EARLY in the source resolver so the
registry sees the concrete "duckdb" type and routes to DuckDBAdapter (read-only).

Tests:
- test_dbt_profile_duckdb_resolver_resolves_concrete_duckdb_type:
  routing unit - resolved dbt_profile→duckdb yields type=="duckdb", not "dbt_profile"
- test_allowlisted_resolver_rejects_dbt_profile:
  trust boundary - deployed-mode resolver refuses to expand a dbt_profile (no off-disk read)
- test_dbt_profile_duckdb_select_returns_correct_rows:
  integration - SELECT through build_adapter_registry returns seeded data; write rejected (read-only)
- test_dbt_profile_missing_raises_query_error:
  error path - missing profile name surfaces as QueryResult error
- TestDbtProfileDuckdbRefResolution:
  the follow-on gap - routing to DuckDBAdapter must not lose ref() resolution
- TestDbtProfileBoardRenderRefResolution:
  the render-path gap - AdapterRegistry._compose_query_refs must resolve ref()
  before its own strict-Jinja variable render, on the dct render/dct serve path
- TestDbtOwnsTheProfileSchema:
  profiles.yml is dbt's file - the installed dbt adapter validates the target, so
  every field dbt accepts survives expansion (threads, canonical BigQuery
  database/schema) and dbt's own required-field errors stay loud
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_profiles_yml(project_dir: Path, db_path: Path) -> None:
    """Write a profiles.yml declaring a single duckdb target.

    ``threads`` is here because dbt puts it in every target it generates — a
    fixture without it is not shaped like a profile any user actually has.
    """
    content = yaml.dump(
        {
            "test_project": {
                "target": "dev",
                "outputs": {
                    "dev": {
                        "type": "duckdb",
                        "path": str(db_path),
                        "threads": 4,
                    }
                },
            }
        }
    )
    (project_dir / "profiles.yml").write_text(content)


def _make_duckdb_file(db_path: Path) -> None:
    """Create a seeded DuckDB file with one row."""
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE numbers (n INTEGER)")
    conn.execute("INSERT INTO numbers VALUES (42)")
    conn.close()


def _make_project(project_dir: Path, db_path: Path) -> None:
    """Write dbt_charts.yml + dbt_project.yml + profiles.yml for a dbt+duckdb project.

    dbt_charts.yml registers a named 'prod' dbt_profile source — queries reference
    it by name (D-09/D-10: a query's source is a validated name, never an inline
    dict), and the registry expands it to a concrete DuckDBSourceConfig at
    execute time via the resolver.
    """
    (project_dir / "dbt_charts.yml").write_text(
        "name: test_project\n"
        "sources:\n"
        "  prod:\n"
        "    type: dbt_profile\n"
        "    profile: test_project\n"
        "    target: dev\n"
        "  missing_prod:\n"
        "    type: dbt_profile\n"
        "    profile: nonexistent_profile\n"
        "    target: dev\n"
    )
    (project_dir / "dbt_project.yml").write_text(
        "name: test_project\nprofile: test_project\n"
    )
    _write_profiles_yml(project_dir, db_path)


# ---------------------------------------------------------------------------
# Routing unit test: resolver must resolve the concrete type for dbt_profile→duckdb
# ---------------------------------------------------------------------------


class TestDbtProfileDuckdbRouting:
    """A dbt_profile source that resolves to DuckDB must expand inside the resolver
    so the registry re-routes to DuckDBAdapter (read-only)."""

    def test_dbt_profile_duckdb_resolver_resolves_concrete_duckdb_type(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DefaultSourceResolver.resolve() must expand a dbt_profile→duckdb dict
        so the registry re-route sees type=="duckdb".

        FAILS without early expansion: resolver returns DbtProfileSourceConfig
        (type="dbt_profile"), causing the re-route check
        (source_config.type == "duckdb") to be False, leaving the query on
        SqlAdapter → _execute_via_dbt_adapter (writable).

        The contract is the resolved `type`, not the concrete class: a dbt target
        expands to DbtTargetSourceConfig (dbt owns the profiles.yml schema), so
        asserting DuckDBSourceConfig here would pin the mirror, not the routing.
        """
        from dbt_charts.core.compile.config import ProjectSourcesConfig
        from dbt_charts.core.execute.source_resolver import (
            DbtContext,
            DefaultSourceResolver,
        )

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

        db_path = tmp_path / "warehouse.duckdb"
        _write_profiles_yml(tmp_path, db_path)

        resolver = DefaultSourceResolver()
        dbt_context = DbtContext(dbt_project_path=tmp_path)
        result = resolver.resolve(
            authored={
                "type": "dbt_profile",
                "profile": "test_project",
                "target": "dev",
            },
            board_sources={},
            project_sources=ProjectSourcesConfig(),
            dbt_context=dbt_context,
        )
        assert result is not None
        assert result.type == "duckdb", (
            f"Expected the concrete warehouse type after dbt_profile expansion, got {result.type!r}. "
            "Regression: resolver is not expanding dbt_profile→duckdb early enough for re-routing."
        )
        assert str(result.model_dump(by_alias=True)["path"]) == str(db_path)

    def test_allowlisted_resolver_rejects_dbt_profile(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """In deployed/multi-tenant mode, a dbt_profile named source must NOT read
        profiles.yml off disk — AllowlistedSourceResolver forces dbt_context=None,
        so expansion raises rather than resolving. This pins the trust boundary.
        """
        from dbt_charts.core.compile.config import ProjectSourcesConfig
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.source_resolver import (
            AllowlistedSourceResolver,
            DbtContext,
        )

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        _write_profiles_yml(tmp_path, tmp_path / "warehouse.duckdb")

        resolver = AllowlistedSourceResolver()
        project_sources = ProjectSourcesConfig(
            sources={
                "prod": {
                    "type": "dbt_profile",
                    "profile": "test_project",
                    "target": "dev",
                }
            },
        )
        # Even with a real dbt project path available, the allowlist resolver
        # discards it (passes dbt_context=None), so the dbt_profile cannot expand.
        with pytest.raises(DbtChartsError):
            resolver.resolve(
                authored="prod",
                board_sources={},
                project_sources=project_sources,
                dbt_context=DbtContext(dbt_project_path=tmp_path),
            )

    def test_dbt_profile_duckdb_select_returns_correct_rows(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """End-to-end: dbt_profile→duckdb SELECT returns the seeded row."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

        db_path = tmp_path / "warehouse.duckdb"
        _make_duckdb_file(db_path)
        _make_project(tmp_path, db_path)

        registry = build_adapter_registry(local_project(tmp_path), read_only=True)

        # 'prod' names the dbt_profile source registered in dbt_charts.yml
        # (_make_project) — a query's source is a validated name, never an
        # inline dict (D-09/D-10); the resolver expands it at execute time.
        query = SqlQuery(
            sql="SELECT n FROM numbers",
            source="prod",
        )
        result = registry.execute(query)
        assert result.error is None, f"SELECT failed: {result.error}"
        assert result.data == [{"n": 42}]

        # The whole point of routing dbt_profile→duckdb to DuckDBAdapter (not the
        # dbt-adapters pool) is that the file is opened READ-ONLY. A write must be
        # rejected — the old _execute_via_dbt_adapter path opened it writable.
        write = SqlQuery(
            sql="INSERT INTO numbers VALUES (99)",
            source="prod",
        )
        write_result = registry.execute(write)
        assert write_result.error is not None, (
            "Expected read-only DuckDB to reject the write"
        )

    def test_dbt_profile_missing_raises_query_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """A dbt_profile with a missing profile name surfaces as a QueryResult error,
        not an uncaught exception.
        """
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

        db_path = tmp_path / "warehouse.duckdb"
        _make_duckdb_file(db_path)
        _make_project(tmp_path, db_path)

        registry = build_adapter_registry(local_project(tmp_path), read_only=True)

        # 'missing_prod' names the dbt_profile source registered in dbt_charts.yml
        # (_make_project) whose profile name doesn't exist in profiles.yml.
        query = SqlQuery(
            sql="SELECT 1",
            source="missing_prod",
        )
        result = registry.execute(query)
        assert result.error is not None
        assert "nonexistent_profile" in str(result.error)


# ---------------------------------------------------------------------------
# ref() resolution: expanding dbt_profile→duckdb must not lose the manifest
# ---------------------------------------------------------------------------


def _write_manifest(project_dir: Path, relpath: str = "target/manifest.json") -> None:
    """Write a manifest declaring one model, `orders`, in schema `main`."""
    manifest_path = project_dir / relpath
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.test_project.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "schema": "main",
                        "alias": "orders",
                    }
                },
                "sources": {},
            }
        )
    )


class TestDbtProfileDuckdbRefResolution:
    """`{{ ref() }}` in a board query must resolve for a dbt_profile→duckdb source.

    Expanding dbt_profile early routes the query to DuckDBAdapter, which never
    consulted the dbt manifest — so the documented `sql: SELECT ... FROM
    {{ ref('model') }}` pattern (apps/docs/docs/queries.md) died at the Jinja
    variable renderer with "Undefined variable: 'ref' is undefined".
    """

    def test_ref_resolves_against_manifest(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """A ref() query against a dbt_profile→duckdb source returns the model's rows."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

        db_path = tmp_path / "warehouse.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, revenue INTEGER)")
        conn.execute("INSERT INTO orders VALUES (1, 100)")
        conn.close()
        _make_project(tmp_path, db_path)
        _write_manifest(tmp_path)

        registry = build_adapter_registry(local_project(tmp_path), read_only=True)

        result = registry.execute(
            SqlQuery(sql="SELECT revenue FROM {{ ref('orders') }}", source="prod")
        )
        assert result.error is None, f"ref() query failed: {result.error}"
        assert result.data == [{"revenue": 100}]

    def test_ref_without_manifest_names_the_missing_manifest(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """With no manifest, the author is told to build one — not that `ref` is
        an undefined Jinja variable."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

        db_path = tmp_path / "warehouse.duckdb"
        _make_duckdb_file(db_path)
        _make_project(tmp_path, db_path)

        registry = build_adapter_registry(local_project(tmp_path), read_only=True)

        result = registry.execute(
            SqlQuery(sql="SELECT * FROM {{ ref('orders') }}", source="prod")
        )
        assert result.error is not None
        assert "manifest" in str(result.error).lower()
        assert "undefined" not in str(result.error).lower()


class TestDbtProfileBoardRenderRefResolution:
    """`{{ ref() }}` in a board query must resolve on the `dct render` path too.

    `TestDbtProfileDuckdbRefResolution` above only exercises the ad-hoc `dct
    query` path (``registry.execute(query)``, no ``board=``). Passing a compiled
    board with populated ``board.queries`` takes a different branch inside
    ``AdapterRegistry._compose_query_refs``: with ``source_config`` resolved
    (non-None, since 'prod' is a named dbt_profile source) that guard used to
    render the raw, unresolved SQL through `render_parameterized_with_queries`
    (StrictUndefined) before any `ref()`/`source()` resolution — so `ref()`
    died as an undefined Jinja global on this path alone.
    """

    def test_ref_resolves_on_board_render_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """A board query using ref() against a dbt_profile source resolves when
        executed with board= set — the same path `dct render`/`dct serve` use."""
        from dbt_charts.core.compile.compiler import compile
        from dbt_charts.core.execute.adapters import build_adapter_registry

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

        db_path = tmp_path / "warehouse.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, revenue INTEGER)")
        conn.execute("INSERT INTO orders VALUES (1, 100)")
        conn.close()
        _make_project(tmp_path, db_path)
        _write_manifest(tmp_path)

        board_yaml = (
            "title: T\n"
            "queries:\n"
            "  q:\n"
            "    sql: SELECT revenue FROM {{ ref('orders') }}\n"
            "    source: prod\n"
            "charts:\n"
            "  c:\n"
            "    query: q\n"
            "    type: table\n"
            "rows:\n"
            "  - c\n"
        )
        result = compile(board_yaml)
        assert result.success, result.diagnostics
        board = result.board

        registry = build_adapter_registry(local_project(tmp_path), read_only=True)

        query_result = registry.execute(board.queries["q"], board=board)
        assert query_result.error is None, f"ref() query failed: {query_result.error}"
        assert query_result.data == [{"revenue": 100}]

    def test_ref_without_manifest_names_the_missing_manifest_on_board_render_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """With no manifest, the board render path also names the missing
        manifest rather than an undefined-Jinja-variable error — mirrors
        ``TestDbtProfileDuckdbRefResolution.test_ref_without_manifest_names_the_missing_manifest``
        for the ``board=`` render path. (This doesn't distinguish a resolve-only
        catch from a resolve-and-render catch — the resolver itself raises
        before the render step runs either way; see
        ``test_unbound_strict_variable_is_not_swallowed_as_a_ref_resolution_error``
        below for that.)"""
        from dbt_charts.core.compile.compiler import compile
        from dbt_charts.core.execute.adapters import build_adapter_registry

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

        db_path = tmp_path / "warehouse.duckdb"
        _make_duckdb_file(db_path)
        _make_project(tmp_path, db_path)

        board_yaml = (
            "title: T\n"
            "queries:\n"
            "  q:\n"
            "    sql: SELECT * FROM {{ ref('orders') }}\n"
            "    source: prod\n"
            "charts:\n"
            "  c:\n"
            "    query: q\n"
            "    type: table\n"
            "rows:\n"
            "  - c\n"
        )
        result = compile(board_yaml)
        assert result.success, result.diagnostics
        board = result.board

        registry = build_adapter_registry(local_project(tmp_path), read_only=True)

        query_result = registry.execute(board.queries["q"], board=board)
        assert query_result.error is not None
        assert "manifest" in str(query_result.error).lower()
        assert "undefined" not in str(query_result.error).lower()

    def test_unbound_strict_variable_is_not_swallowed_as_a_ref_resolution_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """A board query with no ref()/source() call but an unbound strict
        variable must fail as the variable-Jinja render error it is — not get
        relabeled 'dbt ref resolution failed' by a catch that's too wide.

        This SQL has no dbt Jinja at all, so ``DbtRefResolver.resolve()``
        short-circuits cleanly (``has_dbt_jinja`` is False) and cannot itself
        raise; only ``render_parameterized_with_queries`` can fail here. If
        ``_compose_query_refs`` ever again wraps that render call inside the
        same ``except DbtChartsError`` as the resolve step, this
        ``JinjaError`` (a ``DbtChartsError``) would be caught and returned as
        a mislabeled ``QueryResult`` instead of propagating — this test pins
        that it must propagate, matching every sibling adapter's separate
        resolve-then-render error handling.
        """
        from dbt_charts.core.compile.compiler import compile
        from dbt_charts.core.compile.errors import JinjaError
        from dbt_charts.core.execute.adapters import build_adapter_registry

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

        db_path = tmp_path / "warehouse.duckdb"
        _make_duckdb_file(db_path)
        _make_project(tmp_path, db_path)

        board_yaml = (
            "title: T\n"
            "variables:\n"
            "  region:\n"
            "    input: text\n"
            "queries:\n"
            "  q:\n"
            "    sql: \"SELECT * FROM numbers WHERE n = '{{ region }}'\"\n"
            "    source: prod\n"
            "charts:\n"
            "  c:\n"
            "    query: q\n"
            "    type: table\n"
            "rows:\n"
            "  - c\n"
        )
        result = compile(board_yaml)
        assert result.success, result.diagnostics
        board = result.board

        registry = build_adapter_registry(local_project(tmp_path), read_only=True)

        # No `variables=` passed — the registry composition path receives an
        # empty variables dict, so `region` (declared but unbound) is
        # StrictUndefined in the render.
        with pytest.raises(JinjaError, match="region"):
            registry.execute(board.queries["q"], board=board)


# ---------------------------------------------------------------------------
# profiles.yml is dbt's file: dbt validates the target, dbt charts asserts no schema
# ---------------------------------------------------------------------------


class TestDbtOwnsTheProfileSchema:
    """dbt charts must not type-check profiles.yml against its own source models.

    Every key dbt accepts has to survive expansion — dbt charts declares no schema
    for a file it does not own. dbt still validates (required fields, adapter
    type, target name), so bad profiles fail loudly with dbt's own message.
    """

    def _resolve(
        self, tmp_path: Path, target: dict[str, object], profile: str = "analytics"
    ):
        from dbt_charts.core.compile.config import ProjectSourcesConfig
        from dbt_charts.core.execute.source_resolver import (
            DbtContext,
            DefaultSourceResolver,
        )

        (tmp_path / "profiles.yml").write_text(
            yaml.dump({profile: {"target": "dev", "outputs": {"dev": target}}})
        )
        return DefaultSourceResolver().resolve(
            authored={"type": "dbt_profile", "profile": profile, "target": "dev"},
            board_sources={},
            project_sources=ProjectSourcesConfig(),
            dbt_context=DbtContext(dbt_project_path=tmp_path),
        )

    def test_stock_dbt_init_profile_with_threads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`threads` is in every target dbt generates; it is not a credential."""
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        result = self._resolve(
            tmp_path,
            {
                "type": "postgres",
                "host": "localhost",
                "port": 5432,
                "user": "analyst",
                "password": "secret",
                "dbname": "analytics",
                "schema": "public",
                "threads": 4,
            },
        )
        assert result is not None
        assert result.type == "postgres"

    def test_dbt_canonical_bigquery_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dbt's canonical spelling is database/schema; project/dataset are aliases."""
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        result = self._resolve(
            tmp_path,
            {
                "type": "bigquery",
                "method": "oauth",
                "database": "my-proj",
                "schema": "my_ds",
                "threads": 4,
            },
        )
        assert result is not None
        assert result.type == "bigquery"

    def test_adapter_specific_key_survives_expansion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A connection-affecting key must reach the adapter, not be dropped.

        Silently discarding sslmode would connect with different semantics than
        dbt does — worse than erroring.
        """
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        result = self._resolve(
            tmp_path,
            {
                "type": "postgres",
                "host": "localhost",
                "port": 5432,
                "user": "analyst",
                "password": "secret",
                "dbname": "analytics",
                "schema": "public",
                "sslmode": "require",
                "threads": 4,
            },
        )
        assert result is not None
        assert result.model_dump(by_alias=True)["sslmode"] == "require"

    def test_missing_required_field_names_profile_and_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dbt's validation stays loud — and its message beats a raw pydantic error."""
        from dbt_charts.core.diagnostics.execution import ExecutionError

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        with pytest.raises(ExecutionError) as exc:
            self._resolve(
                tmp_path,
                {
                    "type": "postgres",
                    "user": "u",
                    "password": "p",
                    "dbname": "d",
                    "schema": "public",
                    "port": 5432,
                },
            )
        message = str(exc.value)
        assert "host" in message
        assert "analytics" in message and "dev" in message

    def test_unknown_adapter_type_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.diagnostics.execution import ExecutionError

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        with pytest.raises(ExecutionError, match="notawarehouse"):
            self._resolve(tmp_path, {"type": "notawarehouse", "threads": 1})

    def test_relative_duckdb_path_resolves_against_project_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dbt resolves a relative path against CWD; dct can run from anywhere,
        so the dbt project dir stays the anchor."""
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        monkeypatch.chdir(tmp_path.parent)
        result = self._resolve(
            tmp_path, {"type": "duckdb", "path": "wh.duckdb", "threads": 4}
        )
        assert result is not None
        assert result.model_dump(by_alias=True)["path"] == str(tmp_path / "wh.duckdb")


class TestDbtTargetReachesDbtChartsReaders:
    """dbt's spelling of a field is not always the spelling dbt charts reads.

    Handing dbt's target dict through untouched is right for fields nobody but the
    adapter reads, but a few keys are consumed by dbt charts' own code under its own
    name (the BigQuery default-dataset build, normalize_duckdb_config). Those must
    be translated at the boundary or the setting is silently lost.
    """

    def _resolve(self, tmp_path: Path, target: dict[str, object]):
        from dbt_charts.core.compile.config import ProjectSourcesConfig
        from dbt_charts.core.execute.source_resolver import (
            DbtContext,
            DefaultSourceResolver,
        )

        (tmp_path / "profiles.yml").write_text(
            yaml.dump({"analytics": {"target": "dev", "outputs": {"dev": target}}})
        )
        return DefaultSourceResolver().resolve(
            authored={"type": "dbt_profile", "profile": "analytics", "target": "dev"},
            board_sources={},
            project_sources=ProjectSourcesConfig(),
            dbt_context=DbtContext(dbt_project_path=tmp_path),
        )

    @pytest.mark.parametrize(
        "spelling",
        [
            {"project": "my-proj", "dataset": "my_ds"},
            {"database": "my-proj", "schema": "my_ds"},
        ],
        ids=["dbt-alias", "dbt-canonical"],
    )
    def test_bigquery_target_keeps_project_and_dataset(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        spelling: dict[str, str],
    ) -> None:
        """SqlAdapter's pool builds BigQuery's default_dataset from
        source_config['project'] / ['dataset']. Both dbt spellings must land there,
        or a resolvable profile dies with KeyError: 'project' at connect time.
        """
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        result = self._resolve(
            tmp_path, {"type": "bigquery", "method": "oauth", "threads": 4, **spelling}
        )
        assert result is not None
        dumped = result.model_dump(by_alias=True)
        assert dumped["project"] == "my-proj"
        assert dumped["dataset"] == "my_ds"

    def test_duckdb_config_options_reaches_normalize_duckdb_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dbt-duckdb spells it config_options:; normalize_duckdb_config reads
        duckdb_config. Untranslated, the setting is silently dropped."""
        from dbt_charts.core.execute.duckdb_config import normalize_duckdb_config

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        result = self._resolve(
            tmp_path,
            {
                "type": "duckdb",
                "path": ":memory:",
                "threads": 4,
                "config_options": {"enable_external_access": False},
            },
        )
        assert result is not None
        assert normalize_duckdb_config(result.model_dump(by_alias=True)) == {
            "enable_external_access": False
        }

    def test_env_var_in_a_numeric_field_is_rendered_before_validation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dbt renders Jinja, then validates. Validating first rejects any
        env_var() in a field dbt types as non-string (port, threads, …)."""
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        monkeypatch.setenv("DCT_TEST_PGPORT", "6543")
        result = self._resolve(
            tmp_path,
            {
                "type": "postgres",
                "host": "localhost",
                "port": "{{ env_var('DCT_TEST_PGPORT') | int }}",
                "user": "analyst",
                "password": "secret",
                "dbname": "analytics",
                "schema": "public",
                "threads": 4,
            },
        )
        assert result is not None
        assert result.model_dump(by_alias=True)["port"] == 6543

    def test_target_without_type_raises_a_caught_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo'd `typ: duckdb` must not escape as KeyError — every caller guards
        (FileNotFoundError, ValueError) only, so a bare KeyError crashes the executor.
        """
        from dbt_charts.core.diagnostics.execution import ExecutionError

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        with pytest.raises(ExecutionError, match="type"):
            self._resolve(tmp_path, {"typ": "duckdb", "path": ":memory:"})

    def test_bigquery_target_without_project_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dbt lets BigQuery infer the project from application-default credentials
        (`database`/`project` are Optional; `__pre_deserialize__` injects it), so a
        target with only a dataset is config `dbt run` accepts. It must not become
        a KeyError deep in the connection pool.
        """
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        result = self._resolve(
            tmp_path,
            {"type": "bigquery", "method": "oauth", "dataset": "my_ds", "threads": 4},
        )
        assert result is not None
        assert "project" not in result.model_dump(by_alias=True)

    def test_duplicate_alias_in_target_raises_a_caught_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A target carrying both an alias and its canonical name (project AND
        database) makes dbt raise DuplicateAliasError. Callers guard
        (FileNotFoundError, ValueError) only, so it has to arrive as one.
        """
        from dbt_charts.core.diagnostics.execution import ExecutionError

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        with pytest.raises(ExecutionError):
            self._resolve(
                tmp_path,
                {
                    "type": "bigquery",
                    "method": "oauth",
                    "project": "p",
                    "database": "p",
                    "dataset": "d",
                },
            )

    def test_env_var_value_containing_jinja_is_not_re_evaluated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rendering happens once, at the dbt boundary. A second pass would treat
        the first pass's output as a template, so an env_var whose value carries
        Jinja delimiters would resolve to something dbt never saw — connecting
        somewhere other than dbt would.
        """
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        monkeypatch.setenv("DCT_TEST_HOST", "{{ 1 + 1 }}")
        result = self._resolve(
            tmp_path,
            {
                "type": "postgres",
                "host": "{{ env_var('DCT_TEST_HOST') }}",
                "port": 5432,
                "user": "analyst",
                "password": "secret",
                "dbname": "analytics",
                "schema": "public",
                "threads": 4,
            },
        )
        assert result is not None
        assert result.model_dump(by_alias=True)["host"] == "{{ 1 + 1 }}"


class TestAttributionSurvivesTheProfileExpansion:
    """`attribution:` rides the dbt charts source entry, not the dbt target — dbt owns
    profiles.yml and rejects keys it doesn't know. The expansion builds a fresh
    config from dbt's target dict, so the authored value has to be carried across it
    or every dbt_profile source (the most common production shape) loses attribution.
    """

    def _resolve(self, tmp_path: Path, authored: dict[str, object]):
        from dbt_charts.core.compile.config import ProjectSourcesConfig
        from dbt_charts.core.execute.source_resolver import (
            DbtContext,
            DefaultSourceResolver,
        )

        (tmp_path / "profiles.yml").write_text(
            yaml.dump(
                {
                    "analytics": {
                        "target": "dev",
                        "outputs": {"dev": {"type": "duckdb", "path": ":memory:"}},
                    }
                }
            )
        )
        return DefaultSourceResolver().resolve(
            authored=authored,
            board_sources={},
            project_sources=ProjectSourcesConfig(),
            dbt_context=DbtContext(dbt_project_path=tmp_path),
        )

    def test_authored_attribution_reaches_the_resolved_config(
        self, tmp_path: Path
    ) -> None:
        resolved = self._resolve(
            tmp_path,
            {
                "type": "dbt_profile",
                "profile": "analytics",
                "target": "dev",
                "attribution": {"team": "finance"},
            },
        )
        assert resolved.attribution == {"team": "finance"}

    def test_absent_attribution_expands_to_empty(self, tmp_path: Path) -> None:
        resolved = self._resolve(
            tmp_path,
            {"type": "dbt_profile", "profile": "analytics", "target": "dev"},
        )
        assert resolved.attribution == {}
