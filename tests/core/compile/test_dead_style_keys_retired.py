"""``style.variables.visible`` and ``style.charts.table.spark.bar.font`` set
nothing any renderer read, so they left the grammar and migrate away."""

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


_SPARK_FONT: dict[str, Any] = {
    "charts": {"table": {"spark": {"bar": {"font": {"size": 9}, "width": 40}}}}
}


@pytest.mark.parametrize(
    ("style", "match"),
    [
        ({"variables": {"visible": False}}, r"variables\.visible"),
        (_SPARK_FONT, r"spark\.bar\.font"),
    ],
)
def test_the_keys_are_rejected_at_latest(style: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        AuthoredBoard.model_validate({"title": "T", "style": style})


def test_variables_visible_migrates_away_on_every_board() -> None:
    raw: dict[str, Any] = {
        "style": {"variables": {"visible": False, "gap": 4}},
        "rows": [
            {
                "style": {"variables": {"visible": True, "gap": 6}},
                "rows": [{"title": "Leaf", "text": "hello"}],
            }
        ],
    }

    migrated = _migrate(raw)

    assert migrated["style"]["variables"] == {"gap": 4}
    assert migrated["rows"][0]["style"]["variables"] == {"gap": 6}
    AuthoredBoard.model_validate(migrated)


def test_spark_bar_font_migrates_away() -> None:
    migrated = _migrate({"style": _SPARK_FONT, "rows": [{"text": "hello"}]})

    assert migrated["style"]["charts"]["table"]["spark"]["bar"] == {"width": 40}
    AuthoredBoard.model_validate(migrated)


def test_a_variable_keeps_its_own_visible() -> None:
    raw: dict[str, Any] = {
        "variables": {
            "region": {
                "input": "select",
                "options": {"static": ["a"]},
                "visible": False,
            }
        },
        "rows": [{"text": "hello"}],
    }

    assert _migrate(raw)["variables"]["region"]["visible"] is False


@pytest.mark.parametrize(
    ("style_yaml", "reason"),
    [
        ("  variables:\n    visible: false\n", "own `visible: false`"),
        (
            "  charts:\n    table:\n      spark:\n        bar:\n"
            "          font:\n            size: 9\n",
            "spark bar",
        ),
    ],
)
def test_a_board_authoring_the_key_still_compiles(style_yaml: str, reason: str) -> None:
    result = compile(f"title: T\nstyle:\n{style_yaml}rows:\n  - text: hello\n")
    assert result.success, result.errors
    assert any(
        w.code == "WARN-SCHEMA-MIGRATED" and reason in w.message
        for w in result.warnings
    ), [w.message for w in result.warnings]


def test_a_table_charts_own_spark_bar_font_still_compiles() -> None:
    text = (
        "queries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
        "charts:\n  t:\n    type: table\n    query: q\n    style:\n"
        "      spark:\n        bar:\n          font:\n            size: 9\n"
        "rows:\n  - t\n"
    )
    result = compile(text)
    assert result.success, result.errors
    assert any(
        w.code == "WARN-SCHEMA-MIGRATED" and "spark bar" in w.message
        for w in result.warnings
    )
