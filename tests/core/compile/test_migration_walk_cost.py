"""Migration walks cost what the document holds, not what the registry declares.

Assertions count calls or cache entries; only the ``slow`` test reads the clock.
"""

from __future__ import annotations

import statistics
import time
import warnings
from collections.abc import Callable

import pytest
import yaml

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import migrations, prepare_board_mapping
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import JsonObject


def _board(charts: int) -> JsonObject:
    return {
        "title": "Old",
        "source": "db",
        "style": {"charts": {"animation_duration": 100}},
        "queries": {f"q{i}": {"sql": "select 1 as a, 2 as b"} for i in range(charts)},
        "charts": {
            f"c{i}": {"type": "bar", "query": f"q{i}", "x": "a", "y": "b"}
            for i in range(charts)
        },
        "rows": [f"c{i}" for i in range(charts)],
    }


def _calls(name: str, run: Callable[[], object]) -> int:
    calls = 0
    original = getattr(migrations, name)

    def counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    with pytest.MonkeyPatch.context() as patch, warnings.catch_warnings():
        patch.setattr(migrations, name, counting)
        warnings.simplefilter("ignore")
        run()
    return calls


def test_deletion_walks_validate_only_nodes_carrying_a_retired_tail() -> None:
    def validations(charts: int) -> int:
        return _calls(
            "_matching_positions", lambda: prepare_board_mapping(_board(charts))
        )

    small = validations(2)
    assert small > 0
    assert validations(40) == small


def _nested(depth: int, style: JsonObject | None, level: int = 1) -> JsonObject:
    """A board of *depth* levels, three boards to a level, a chart at the leaves,
    every board authoring *style* when one is given."""
    board: JsonObject = {"title": f"Board {level}"}
    if style is not None:
        board["style"] = style
    if level == depth:
        board["rows"] = [{"type": "bar", "query": "q", "x": "a", "y": "b"}]
    else:
        layout = "cols" if level % 2 else "rows"
        board[layout] = [_nested(depth, style, level + 1) for _ in range(3)]
    return board


def _yaml(depth: int, title: str, extra: JsonObject, style: JsonObject | None) -> str:
    board: JsonObject = {
        **extra,
        "title": title,
        "queries": {"q": {"sql": "select 1 as a, 2 as b", "source": "db"}},
        "rows": [_nested(depth, style)],
    }
    return yaml.safe_dump(board, sort_keys=False)


def _editing(depth: int, edit: int) -> str:
    """A board mid-edit: a half-typed root key."""
    return _yaml(depth, "Sales", {f"titl{edit}": "x"}, None)


def _old(depth: int, edit: int) -> str:
    """A 0.9 board whose every nested board authors a key 0.10 renamed."""
    rule: JsonObject = {"text": {"column": {"rule": {"color": "#f00"}}}}
    return _yaml(depth, f"Sales {edit}", {}, rule)


def test_an_old_nested_board_migrates() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = compile(_old(3, 0))
    assert result.success, result.errors


def test_a_board_mid_edit_costs_the_same_however_many_moves_are_declared() -> None:
    """A Move whose keys the board never uses must add no schema-walk cost."""
    _, registry = migrations._board_migration_context()
    declared = registry.transition_from

    def steps() -> int:
        compile(_editing(3, 0))
        return _calls("_child_positions", lambda: compile(_editing(3, 1)))

    once = steps()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            registry, "transition_from", lambda version: declared(version) * 2
        )
        twice = steps()
    assert once > 0
    assert twice == once


@pytest.mark.parametrize("source", [_editing, _old])
def test_compiling_nested_boards_retains_no_walk_state(
    source: Callable[[int, int], str],
) -> None:
    for edit in range(1):
        compile(source(3, edit))
    settled = migrations._schema_branches_cached.cache_info().currsize
    for edit in range(1, 6):
        compile(source(3, edit))
    assert migrations._schema_branches_cached.cache_info().currsize == settled


@pytest.mark.slow
@pytest.mark.parametrize("source", [_editing, _old])
def test_a_nested_board_compiles_within_a_preview_keystroke(
    source: Callable[[int, int], str],
) -> None:
    compile(source(3, 0))
    seconds = []
    for edit in range(1, 6):
        start = time.perf_counter()
        compile(source(3, edit))
        seconds.append(time.perf_counter() - start)
    assert statistics.median(seconds) < 1.0
