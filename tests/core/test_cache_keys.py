"""Tests for source-identity cache keying.

Security regression: cache keys must NOT include passwords or other secrets,
and MUST distinguish two users connecting to the same source name with
different credentials.
"""

from __future__ import annotations

import json

import pytest

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.cache_keys import SECRET_FIELDS, source_identity
from dbt_charts.core.execute.duckdb_cache import (
    compute_cache_key,
    compute_query_hash,
    compute_relevant_variables,
    compute_source_hash,
    compute_variables_hash,
)


class TestSourceIdentityStripsSecrets:
    """source_identity must drop every secret-shaped field from dict configs."""

    def test_strips_password(self):
        cfg = {"type": "postgres", "user": "alice", "password": "hunter2"}
        result = source_identity(cfg)
        assert "password" not in result
        assert result == {"type": "postgres", "user": "alice"}

    @pytest.mark.parametrize("field", sorted(SECRET_FIELDS))
    def test_strips_every_known_secret_field(self, field):
        cfg = {"type": "postgres", "user": "alice", field: "REDACTED"}
        assert field not in source_identity(cfg)

    def test_secret_filter_is_case_insensitive(self):
        cfg = {"type": "postgres", "user": "alice", "PASSWORD": "x", "Token": "y"}
        result = source_identity(cfg)
        assert "PASSWORD" not in result
        assert "Token" not in result

    def test_string_passes_through(self):
        assert source_identity("warehouse_prod") == "warehouse_prod"

    def test_none_passes_through(self):
        assert source_identity(None) is None


class TestSourceIdentityPreservesIdentity:
    """source_identity must keep host/user/project/keyfile etc."""

    def test_postgres_keeps_user_host_dbname(self):
        cfg = {
            "type": "postgres",
            "host": "db.example",
            "port": 5432,
            "dbname": "app",
            "user": "alice",
            "password": "x",
        }
        result = source_identity(cfg)
        assert result["user"] == "alice"
        assert result["host"] == "db.example"
        assert result["dbname"] == "app"
        assert result["port"] == 5432

    def test_bigquery_keeps_project_dataset_keyfile(self):
        cfg = {
            "type": "bigquery",
            "project": "p1",
            "dataset": "d1",
            "keyfile": "/path/to/sa.json",
        }
        assert source_identity(cfg) == cfg


class TestComputeSourceHashUserAware:
    """compute_source_hash must distinguish identities, ignore secrets."""

    def test_password_does_not_change_hash(self):
        cfg_a = {"type": "postgres", "user": "alice", "password": "p1"}
        cfg_b = {"type": "postgres", "user": "alice", "password": "p2"}
        assert compute_source_hash(cfg_a) == compute_source_hash(cfg_b)

    def test_different_user_different_hash(self):
        cfg_a = {"type": "postgres", "host": "db", "user": "alice"}
        cfg_b = {"type": "postgres", "host": "db", "user": "bob"}
        assert compute_source_hash(cfg_a) != compute_source_hash(cfg_b)

    def test_different_keyfile_different_hash(self):
        cfg_a = {"type": "bigquery", "project": "p", "keyfile": "/a.json"}
        cfg_b = {"type": "bigquery", "project": "p", "keyfile": "/b.json"}
        assert compute_source_hash(cfg_a) != compute_source_hash(cfg_b)


class TestComputeSourceHashResolvesNamedSource:
    """When board_sources is provided, named string references resolve to configs."""

    def test_resolved_name_matches_direct_dict(self):
        cfg = {"type": "postgres", "user": "alice", "host": "db"}
        board_sources = {"warehouse": cfg}
        assert compute_source_hash(
            "warehouse", board_sources=board_sources
        ) == compute_source_hash(cfg)

    def test_two_users_same_name_different_creds_get_different_hashes(self):
        # Same source NAME, two different resolved configs (the cross-user
        # leak this task fixes). Without resolution, both would hash to the
        # name "warehouse" and share a cache entry.
        board_sources_a = {"warehouse": {"type": "postgres", "user": "alice"}}
        board_sources_b = {"warehouse": {"type": "postgres", "user": "bob"}}
        h_a = compute_source_hash("warehouse", board_sources=board_sources_a)
        h_b = compute_source_hash("warehouse", board_sources=board_sources_b)
        assert h_a != h_b

    def test_password_difference_alone_does_not_split_hash(self):
        # Two users with the SAME identity but different passwords (e.g.,
        # rotated credential) must hit the same cache entry.
        board_sources_a = {
            "warehouse": {"type": "postgres", "user": "alice", "password": "p1"}
        }
        board_sources_b = {
            "warehouse": {"type": "postgres", "user": "alice", "password": "p2"}
        }
        assert compute_source_hash(
            "warehouse", board_sources=board_sources_a
        ) == compute_source_hash("warehouse", board_sources=board_sources_b)

    def test_unresolvable_name_falls_back_to_name_string(self):
        # No board_sources provided, or name absent from map: existing
        # behavior preserved (hash by name string).
        h_no_map = compute_source_hash("warehouse")
        h_empty_map = compute_source_hash("warehouse", board_sources={})
        h_other_map = compute_source_hash("warehouse", board_sources={"other": {}})
        assert h_no_map == h_empty_map == h_other_map


class TestSecretsNeverInHashInput:
    """Belt-and-suspenders: passwords must not appear in the canonical input.

    This guards against the table-name leaking-into-logs failure mode.
    """

    def test_password_not_in_canonical_input(self):
        cfg = {"type": "postgres", "user": "alice", "password": "SUPER_SECRET_VALUE"}
        identity = source_identity(cfg)
        canonical = json.dumps(sorted(identity.items()), default=str, sort_keys=True)
        assert "SUPER_SECRET_VALUE" not in canonical


class TestExecutorCrossUserIsolation:
    """Two Executors with the same source NAME but different resolved
    configs (different DB users) must NOT share cache entries.

    Regression for the cross-user cache-leak vulnerability: before this
    fix, both runs hashed the literal string "warehouse" and shared a
    cache entry; user A could read user B's cached results when the two
    had different DB-side row-level access.
    """

    def test_two_users_same_source_name_distinct_cache_entries(self):
        from unittest.mock import Mock

        from dbt_charts.core.compile import compile
        from dbt_charts.core.compile.config import ProjectSourcesConfig
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

        board_yaml = """
title: Sales
source: warehouse
queries:
  revenue:
    sql: SELECT amount FROM orders
charts:
  c:
    query: revenue
    type: kpi
    value: amount
rows:
  - c
"""

        rows_alice = [{"amount": 100}]
        rows_bob = [{"amount": 999}]

        def _executor(
            user: str, rows: list[dict[str, object]], cache: TrivialDuckDBCache
        ):
            # Each user's 'warehouse' is registered project-side (D-09: boards
            # can't define sources inline) with a distinct user/password — the
            # board itself only names it, matching production shape.
            project_sources = ProjectSourcesConfig(
                sources={
                    "warehouse": {
                        "type": "postgres",
                        "host": "db.example",
                        "dbname": "app",
                        "user": user,
                        "password": f"{user}-secret",
                    }
                }
            )
            result = compile(board_yaml, project_sources=project_sources)
            ok = Mock()
            ok.is_success = True
            ok.data = rows
            ok.resolved_relations = None
            ok.truncated_reason = None
            registry = Mock()
            registry.execute.return_value = ok
            return (
                Executor(
                    result.board,
                    adapter_registry=registry,
                    query_registry=result.query_registry,
                    result_cache=cache,
                ),
                registry,
            )

        cache = TrivialDuckDBCache()
        try:
            exec_alice, reg_alice = _executor("alice", rows_alice, cache)
            exec_bob, reg_bob = _executor("bob", rows_bob, cache)

            # Alice runs first — seeds the cache under her identity.
            assert exec_alice.execute_query("revenue") == rows_alice
            assert reg_alice.execute.call_count == 1

            # Bob runs the SAME named source ("warehouse") but with a
            # different resolved user. He must NOT hit Alice's cache —
            # his adapter must be called and return his own data.
            assert exec_bob.execute_query("revenue") == rows_bob
            assert reg_bob.execute.call_count == 1
        finally:
            cache.close()


class TestSourceIdentityExcludesIncidentalFields:
    """Editing a source's authored cache: block or cost attribution must not
    change compute_source_hash — those fields describe caching policy and cost
    attribution, not connection identity, so touching them should never
    cold-restart the whole source's cache."""

    def test_cache_block_does_not_change_hash(self):
        base = {"type": "postgres", "user": "alice", "host": "db"}
        with_cache = {**base, "cache": {"ttl": "1h"}}
        assert compute_source_hash(base) == compute_source_hash(with_cache)

    def test_cache_disabled_does_not_change_hash(self):
        base = {"type": "postgres", "user": "alice", "host": "db"}
        with_cache = {**base, "cache": {"enabled": False}}
        assert compute_source_hash(base) == compute_source_hash(with_cache)

    def test_attribution_does_not_change_hash(self):
        base = {"type": "postgres", "user": "alice", "host": "db"}
        with_attribution = {**base, "attribution": {"team": "analytics"}}
        assert compute_source_hash(base) == compute_source_hash(with_attribution)

    def test_editing_attribution_does_not_change_hash(self):
        cfg_a = {"type": "postgres", "user": "alice", "attribution": {"team": "a"}}
        cfg_b = {"type": "postgres", "user": "alice", "attribution": {"team": "b"}}
        assert compute_source_hash(cfg_a) == compute_source_hash(cfg_b)


class TestComputeCacheKey:
    """compute_cache_key is the single source of truth for the duckdb cache
    key triple (source_hash, query_hash, variables_hash)."""

    def test_sql_query_matches_primitive_recipe(self):
        q = SqlQuery(sql="SELECT 1", source="dw")
        source_hash, query_hash, variables_hash = compute_cache_key(q)
        assert source_hash == compute_source_hash("dw", board_sources=None)
        assert query_hash == compute_query_hash("SELECT 1")
        assert variables_hash == compute_variables_hash({})

    def test_setup_sql_folded_into_query_hash(self):
        q = SqlQuery(sql="SELECT 1", setup_sql="SET x = 1", source="warehouse")
        assert compute_cache_key(q)[1] == compute_query_hash("SELECT 1\nSET x = 1")

    def test_incremental_inactive_matches_non_incremental_key(self):
        """A query most people don't use incremental on must hash exactly as
        before the feature existed, so upgrading never busts every cache entry."""
        inactive = SqlQuery(sql="SELECT 1", source="dw")
        assert inactive.incremental is None
        assert compute_cache_key(inactive)[1] == compute_query_hash("SELECT 1")

    def test_incremental_active_differs_from_inactive(self):
        active = SqlQuery(sql="SELECT 1", source="dw", incremental="updated_at")
        inactive = SqlQuery(sql="SELECT 1", source="dw")
        assert compute_cache_key(active)[1] != compute_cache_key(inactive)[1]

    def test_incremental_distinct_columns_distinct_keys(self):
        a = SqlQuery(sql="SELECT 1", source="dw", incremental="updated_at")
        b = SqlQuery(sql="SELECT 1", source="dw", incremental="created_at")
        assert compute_cache_key(a)[1] != compute_cache_key(b)[1]

    def test_incremental_column_named_false_differs_from_inactive(self):
        """A column literally named 'False' must not collide with the
        inactive (None) state — the fold format must be unambiguous."""
        named_false = SqlQuery(sql="SELECT 1", source="dw", incremental="False")
        inactive = SqlQuery(sql="SELECT 1", source="dw")
        assert compute_cache_key(named_false)[1] != compute_cache_key(inactive)[1]

    def test_dialect_changes_the_key_only_when_set(self):
        """The same text means different SQL in another dialect, but a query
        with no dialect hashes exactly as before."""
        plain = SqlQuery(sql="SELECT 1", source="dw")
        bigquery = SqlQuery(sql="SELECT 1", source="dw", dialect="bigquery")
        snowflake = SqlQuery(sql="SELECT 1", source="dw", dialect="snowflake")
        assert compute_cache_key(plain)[1] == compute_query_hash("SELECT 1")
        assert len({compute_cache_key(q)[1] for q in (plain, bigquery, snowflake)}) == 3

    def test_distinct_sql_distinct_key(self):
        assert compute_cache_key(
            SqlQuery(sql="SELECT 1", source="warehouse")
        ) != compute_cache_key(SqlQuery(sql="SELECT 2", source="warehouse"))

    def test_distinct_source_same_sql_differs_only_in_source_hash(self):
        a = compute_cache_key(SqlQuery(sql="SELECT 1", source="dw1"))
        b = compute_cache_key(SqlQuery(sql="SELECT 1", source="dw2"))
        assert a[0] != b[0]  # source_hash differs
        assert a[1] == b[1]  # query_hash identical

    def test_variables_filtered_to_declared_dependencies(self):
        q = SqlQuery(
            sql="SELECT {{ year }}", variable_dependencies={"year"}, source="warehouse"
        )
        # An unrelated variable must not change the key.
        assert compute_cache_key(q, {"year": 2024, "other": "a"}) == compute_cache_key(
            q, {"year": 2024, "other": "b"}
        )
        # A dependency change must.
        assert compute_cache_key(q, {"year": 2024}) != compute_cache_key(
            q, {"year": 2025}
        )

    def test_board_sources_resolves_named_source(self):
        # A named source must hash through board_sources resolution — the path that
        # keeps two users' same-named sources from colliding.
        cfg = {"type": "postgres", "user": "alice", "host": "db"}
        q = SqlQuery(sql="SELECT 1", source="warehouse")
        source_hash, _, _ = compute_cache_key(q, board_sources={"warehouse": cfg})
        assert source_hash == compute_source_hash(
            "warehouse", board_sources={"warehouse": cfg}
        )
        # Different resolved identity → different source_hash for the same SQL + name.
        other = {"type": "postgres", "user": "bob", "host": "db"}
        other_hash, _, _ = compute_cache_key(q, board_sources={"warehouse": other})
        assert source_hash != other_hash


class TestComputeRelevantVariables:
    """compute_relevant_variables is the single filter compute_cache_key hashes
    and that callers needing the filtered dict itself (e.g. cache-row audit
    metadata) reuse — never a second hand-rolled copy of the filter."""

    def test_filters_to_declared_dependencies(self):
        q = SqlQuery(
            sql="SELECT {{ year }}", variable_dependencies={"year"}, source="warehouse"
        )
        assert compute_relevant_variables(q, {"year": 2024, "other": "a"}) == {
            "year": 2024
        }

    def test_no_dependencies_or_no_variables_yields_empty_dict(self):
        q = SqlQuery(sql="SELECT 1", source="warehouse")
        assert compute_relevant_variables(q, {"year": 2024}) == {}
        q2 = SqlQuery(
            sql="SELECT {{ year }}", variable_dependencies={"year"}, source="warehouse"
        )
        assert compute_relevant_variables(q2, None) == {}
