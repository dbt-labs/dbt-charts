"""Ratchet: raw raise ValueError/TypeError/KeyError/RuntimeError in execute/ must be
stamped with an ERR-* code (e.g. QueryError.from_code(...)) or explicitly allowlisted.

Unstamped raises surface to users as ERR-INTERNAL, losing the structured code/fields
the diagnostic registry exists to provide. This test doesn't require every raise to
be migrated today — it prevents NEW ones from landing unstamped, and forces the
allowlist to shrink as raises are migrated (see test_allowlist_has_no_stale_entries).

Seeded at the 26 raw raises found in core/execute/ as of the initial ratchet
(complete-the-diagnostic-channel-and-ratchet-core-execute). Walker implementation
lives in tests/core/_raise_ratchet.py, shared with
tests/core/render/test_error_stamping.py — one AST walker, two roots.
"""

from __future__ import annotations

from ..._paths import DBT_CHARTS_PKG_DIR
from .._raise_ratchet import (
    assert_allowlist_has_no_stale_entries,
    assert_no_new_unstamped_raises,
)

_EXECUTE_ROOT = DBT_CHARTS_PKG_DIR / "core" / "execute"

# Keyed by (path relative to execute/, dotted qualname of enclosing function/method,
# or "<module>" for module-level raises). One entry covers every raise in that
# function — lineno-independent, so refactoring the function body doesn't require
# touching this list. Shrinks only.
ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # _duckdb_cache_base.py
        ("_duckdb_cache_base.py", "_DuckDBResultCacheBase.execute_file_source_sql"),
        # adapters/databricks_connection_manager.py
        (
            "adapters/databricks_connection_manager.py",
            "_assert_vendored_methods_unchanged",
        ),
        # adapters/dbt_adapter.py
        ("adapters/dbt_adapter.py", "DbtAdapter._resolve_target_dict"),
        ("adapters/dbt_adapter.py", "_read_profiles_yml"),
        ("adapters/dbt_adapter.py", "_read_target_dict"),
        # adapters/dbt_adapter_factory.py
        ("adapters/dbt_adapter_factory.py", "build_adapter"),
        # adapters/duckdb_adapter.py
        ("adapters/duckdb_adapter.py", "DuckDBAdapter._connect"),
        # adapters/http_adapter.py
        ("adapters/http_adapter.py", "_resolve_json_path"),
        # cache_backend.py — a naive timestamp from a backend is an implementer
        # contract breach, not an authoring mistake: no board YAML, CLI flag, or
        # variable value can produce it, so there is no author-facing diagnostic
        # to stamp it with. The message names the field and the contract.
        ("cache_backend.py", "_require_aware"),
        # cache_composition.py
        ("cache_composition.py", "compose_over_named_rows"),
        # duckdb_config.py
        ("duckdb_config.py", "normalize_duckdb_config"),
        # executor.py — an adapter/test-double's truncated_reason failing
        # Literal validation is an implementer contract breach (the value
        # crosses an adapter boundary with no runtime type enforcement), not
        # an authoring mistake: no board YAML, CLI flag, or variable value can
        # produce it, so there is no author-facing diagnostic to stamp it with.
        ("executor.py", "Executor._enforce_result_limits"),
        # file_source_materializer.py
        ("file_source_materializer.py", "FileSourceMaterializer.materialize_and_run"),
        ("file_source_materializer.py", "_parse_json"),
        ("file_source_materializer.py", "_parse_ndjson"),
        # sql_literals.py
        ("sql_literals.py", "_to_sql_literal"),
        ("sql_literals.py", "inline_percent_params"),
        ("sql_literals.py", "inline_qmark_params"),
        # The NUL guard is a backstop behind the render-time JinjaError on
        # string filters — the common author-reachable spellings already fail
        # there with a variable-naming message; a survivor here is a transform
        # the render guard missed, and the message says what to do.
        ("sql_literals.py", "inline_params_for_dialect"),
        # trivial_local_cache.py — passing an Arrow table on a non-file cache key
        # is an implementer contract breach, same category as _require_aware
        # above: only this package's own materializer calls put(arrow=...), so no
        # board YAML, CLI flag, or variable value can reach it.
        ("trivial_local_cache.py", "TrivialDuckDBCache.put"),
    }
)


def test_no_new_unstamped_raises_in_execute() -> None:
    """Every raw raise ValueError/TypeError/KeyError/RuntimeError in execute/ must be
    either migrated to a coded error or explicitly allowlisted."""
    assert_no_new_unstamped_raises(_EXECUTE_ROOT, ALLOWLIST, "execute")


def test_allowlist_has_no_stale_entries() -> None:
    """Allowlist entries must correspond to a real raise — forces cleanup on migration."""
    assert_allowlist_has_no_stale_entries(_EXECUTE_ROOT, ALLOWLIST)
