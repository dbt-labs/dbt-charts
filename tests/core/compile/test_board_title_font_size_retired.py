"""``style.title.font.size`` is not board grammar: a board title sizes from the
heading ramp, so the key was dead, and it migrates away rather than lingering."""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import SchemaMigrationWarning
from dbt_charts.core.compile.models.board.authored import AuthoredBoard

from ._engine_migrations import migrate_mapping


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw)


def test_authored_board_title_font_size_is_rejected_at_latest() -> None:
    with pytest.raises(ValidationError, match=r"style\.title\.font\.size"):
        AuthoredBoard.model_validate(
            {"title": "T", "style": {"title": {"font": {"size": 40}}}}
        )


def test_board_title_font_size_migrates_away() -> None:
    raw: dict[str, Any] = {
        "style": {"title": {"font": {"size": 40, "weight": 600}}},
        "rows": [
            {
                "title": "Section",
                "style": {"title": {"font": {"size": 22, "weight": 500}}},
                "rows": [{"title": "Leaf", "text": "hello"}],
            }
        ],
    }

    migrated = _migrate(raw)

    assert migrated["style"]["title"]["font"] == {"weight": 600}
    assert migrated["rows"][0]["style"]["title"]["font"] == {"weight": 500}
    AuthoredBoard.model_validate(migrated)


def test_a_board_authoring_the_key_still_compiles() -> None:
    text = (
        "title: T\nstyle:\n  title:\n    font:\n      size: 40\n"
        "rows:\n  - title: Leaf\n    text: hello\n"
    )
    result = compile(text)
    assert result.success, result.errors
    assert any(
        w.code == "WARN-SCHEMA-MIGRATED" and "heading ramp" in w.message
        for w in result.warnings
    )


def test_chart_title_font_size_still_validates_and_survives_migration() -> None:
    raw: dict[str, Any] = {
        "charts": {
            "c": {
                "type": "bar",
                "query": "q",
                "x": "a",
                "y": "b",
                "style": {"title": {"font": {"size": 24}}},
            }
        },
        "rows": ["c"],
    }

    migrated = _migrate(raw)

    assert migrated["charts"]["c"]["style"]["title"]["font"] == {"size": 24}
    AuthoredBoard.model_validate(migrated)
