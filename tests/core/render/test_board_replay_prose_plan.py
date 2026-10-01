"""A stored board artifact must carry the prose plan its text was laid out with."""

from __future__ import annotations

import json

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_render import ERR_BOARD_ARTIFACT_INVALID
from dbt_charts.core.render.board_replay import dump_board_artifact, load_board_artifact
from dbt_charts.core.render.board_resolve import build_resolved_board_static

# No root text: only the walk into the nested boards can find the plan-less block.
BOARD = "rows:\n  - text: A nested block.\n  - text: Another.\n"


def _artifact() -> dict:
    result = compile(BOARD)
    assert result.success
    return json.loads(dump_board_artifact(build_resolved_board_static(result.board)))


def _strip_plans(node: object) -> None:
    if isinstance(node, dict):
        node.pop("prose_plan", None)
        for value in node.values():
            _strip_plans(value)
    elif isinstance(node, list):
        for value in node:
            _strip_plans(value)


def test_the_plan_round_trips() -> None:
    resolved = load_board_artifact(json.dumps(_artifact()).encode())
    nested = resolved.layout.items[0].board
    assert nested.prose_plan is not None
    assert resolved.layout.items[1].board.prose_plan == nested.prose_plan


def test_an_artifact_from_before_the_plan_is_rejected_by_name() -> None:
    artifact = _artifact()
    _strip_plans(artifact)
    with pytest.raises(DbtChartsError) as excinfo:
        load_board_artifact(json.dumps(artifact).encode())
    assert excinfo.value.code is ERR_BOARD_ARTIFACT_INVALID
    assert "prose plan" in str(excinfo.value)
