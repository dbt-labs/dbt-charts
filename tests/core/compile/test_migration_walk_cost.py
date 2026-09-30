"""Deletion walks validate a node only where a retired tail is present.

Assertions count calls rather than time, so counts must not move as the board grows.
"""

from __future__ import annotations

import warnings

import pytest

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


def _validations(charts: int) -> int:
    calls = 0
    original = migrations._matching_positions

    def counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    with pytest.MonkeyPatch.context() as patch, warnings.catch_warnings():
        patch.setattr(migrations, "_matching_positions", counting)
        warnings.simplefilter("ignore")
        prepare_board_mapping(_board(charts))
    return calls


def test_deletion_walks_validate_only_nodes_carrying_a_retired_tail() -> None:
    small = _validations(2)
    assert small > 0
    assert _validations(40) == small
