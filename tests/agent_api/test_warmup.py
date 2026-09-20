from __future__ import annotations

from dbt_charts.agent_api.design import build_design_target
from dbt_charts.agent_api.warmup import warm_process
from dbt_charts.core.compile import config
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.schema.introspection import introspect

# `description:` was renamed to `notes:`, so this board fails the current-schema
# check and takes the path that needs the migration registry.
OLD_GRAMMAR_BOARD = "title: Old\ndescription: authored before the rename\nrows: []\n"


def test_warm_process_builds_the_registry_the_schema_and_the_theme() -> None:
    _board_migration_context.cache_clear()
    introspect.cache_clear()
    config._compiled_theme_cache.clear()

    warm_process()

    assert _board_migration_context.cache_info().currsize == 1
    assert introspect.cache_info().currsize == 1
    # Read off the module: load_config()/reset_config() rebind the global.
    assert config.get_default_theme_name() in config._compiled_theme_cache


def test_an_old_grammar_board_after_warm_process_builds_nothing() -> None:
    _board_migration_context.cache_clear()
    introspect.cache_clear()
    warm_process()
    registry_misses = _board_migration_context.cache_info().misses
    schema_misses = introspect.cache_info().misses

    build_design_target(OLD_GRAMMAR_BOARD, "")

    assert _board_migration_context.cache_info().misses == registry_misses
    assert introspect.cache_info().misses == schema_misses
