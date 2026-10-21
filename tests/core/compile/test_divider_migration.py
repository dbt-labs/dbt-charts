"""0.9.0 -> 0.10.0: separator-line ``rule`` keys become ``divider``
(dbt_charts.core.compile.migrations.versions.v0_10_0).
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
    prepare_board_mapping,
)
from dbt_charts.core.compile.migrations.migrations import (
    _board_migration_context,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.parse.parser import load_yaml_mapping
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    load_yaml_schema_catalog,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore::dbt_charts.core.compile.migrations.SchemaMigrationWarning"
)


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(
            raw, catalog=load_yaml_schema_catalog(), registry=registry
        )


def _board(style: dict[str, Any]) -> dict[str, Any]:
    return {"title": "t", "style": style}


def _table(block: dict[str, Any]) -> dict[str, Any]:
    return {"charts": {"table": block}}


_ALL_RED = {
    "header": {"divider": {"color": "#f00"}},
    "row": {
        "divider": {"color": "#f00"},
        "roles": {
            "summary": {"divider": {"color": "#f00"}},
            "total": {"divider": {"color": "#f00"}},
        },
    },
}

# (id, old style, new style). Every old style fails the current grammar, so the
# document is recognized as 0.9.0 and migrated.
STYLE_CASES = [
    (
        "column rule, presence meant on",
        {
            "text": {
                "column": {"rule": {"color": "#f00", "width": 2, "style": "dashed"}}
            }
        },
        {
            "text": {
                "column": {
                    "divider": {
                        "visible": True,
                        "color": "#f00",
                        "width": 2,
                        "dasharray": "6 3",
                    }
                }
            }
        },
    ),
    (
        "column rule style solid and dotted become a dasharray",
        {"text": {"column": {"rule": {"style": "dotted"}}}},
        {"text": {"column": {"divider": {"visible": True, "dasharray": "2 2"}}}},
    ),
    (
        "column rule style solid is the absence of a dasharray",
        {"text": {"column": {"rule": {"style": "solid", "color": "#f00"}}}},
        {"text": {"column": {"divider": {"visible": True, "color": "#f00"}}}},
    ),
    (
        "text rule keeps its color",
        {"text": {"rule": {"color": "#f00"}}},
        {"text": {"divider": {"color": "#f00"}}},
    ),
    (
        "footer rule renames stroke_width and turns on",
        {"footer": {"rule": {"color": "#f00", "stroke_width": 2}}},
        {"footer": {"divider": {"visible": True, "color": "#f00", "width": 2}}},
    ),
    (
        "footer rule null was a no-op in a board patch",
        {"footer": {"rule": None}},
        {"footer": {}},
    ),
    (
        "header rule width turns it on",
        _table({"header": {"rule": {"width": 2}}}),
        _table({"header": {"divider": {"visible": True, "width": 2}}}),
    ),
    (
        "header rule width 0 turns it off and drops the width",
        _table({"header": {"rule": {"width": 0, "color": "#f00"}}}),
        _table({"header": {"divider": {"visible": False}}}),
    ),
    (
        "header rule without width leaves visibility to the theme",
        _table({"header": {"rule": {"color": "#f00", "continuous": False}}}),
        _table({"header": {"divider": {"continuous": False}}}),
    ),
    (
        "row rule width turns it on",
        _table({"row": {"rule": {"width": 1, "continuous": True}}}),
        _table({"row": {"divider": {"visible": True, "width": 1, "continuous": True}}}),
    ),
    (
        "summary rule_width drove the total's double line too",
        _table({"row": {"roles": {"summary": {"rule_width": 2}}}}),
        _table(
            {
                "row": {
                    "roles": {
                        "summary": {"divider": {"visible": True, "width": 2}},
                        "total": {"divider": {"visible": True, "width": 2}},
                    }
                }
            }
        ),
    ),
    (
        "summary rule_width 0 fell back to the row width",
        _table(
            {
                "row": {
                    "rule": {"width": 1},
                    "roles": {"summary": {"rule_width": 0}},
                }
            }
        ),
        _table(
            {
                "row": {
                    "divider": {"visible": True, "width": 1},
                    "roles": {
                        "summary": {"divider": {"visible": True, "width": 1}},
                        "total": {"divider": {"visible": True, "width": 1}},
                    },
                }
            }
        ),
    ),
    (
        "summary rule_width 0 with no row rule hides both",
        _table(
            {
                "row": {
                    "roles": {"summary": {"rule_width": 0}, "total": {"rule_width": 3}}
                }
            }
        ),
        _table(
            {
                "row": {
                    "roles": {
                        "summary": {"divider": {"visible": False}},
                        "total": {"divider": {"visible": False}},
                    }
                }
            }
        ),
    ),
    (
        "total rule_width was never read and is dropped",
        _table({"row": {"roles": {"total": {"rule_width": 3}}}}),
        _table({"row": {"roles": {"total": {}}}}),
    ),
    (
        "support table row rule drops continuous, which was never read",
        {
            "charts": {
                "support_table": {"row": {"rule": {"width": 1, "continuous": True}}}
            }
        },
        {
            "charts": {
                "support_table": {"row": {"divider": {"visible": True, "width": 1}}}
            }
        },
    ),
    (
        "support table divider drops continuous and keeps its color",
        {
            "charts": {
                "support_table": {
                    "divider": {"width": 1, "color": "#f00", "continuous": True}
                }
            }
        },
        {
            "charts": {
                "support_table": {
                    "divider": {"visible": True, "width": 1, "color": "#f00"}
                }
            }
        },
    ),
    (
        "table rule color colored every table line",
        _table({"rule": {"color": "#f00"}}),
        _table(_ALL_RED),
    ),
    (
        "row rule color colored every table line and beat the table rule",
        _table({"rule": {"color": "#0f0"}, "row": {"rule": {"color": "#f00"}}}),
        _table(_ALL_RED),
    ),
    (
        "header rule color was never read",
        _table({"header": {"rule": {"color": "#f00", "continuous": False}}}),
        _table({"header": {"divider": {"continuous": False}}}),
    ),
]


@pytest.mark.parametrize(
    ("old", "new"),
    [pytest.param(old, new, id=name) for name, old, new in STYLE_CASES],
)
def test_style_migrates_to_the_divider_grammar(
    old: dict[str, Any], new: dict[str, Any]
) -> None:
    migrated = _migrate(_board(old))
    assert migrated["style"] == new
    assert AuthoredBoard.model_validate(migrated) == AuthoredBoard.model_validate(
        _board(new)
    )


def test_prepare_board_mapping_migrates_without_dct_migrate() -> None:
    migrated = prepare_board_mapping(
        _board({"footer": {"rule": {"color": "#f00", "stroke_width": 2}}})
    )
    assert migrated["style"]["footer"] == {
        "divider": {"visible": True, "color": "#f00", "width": 2}
    }


def test_chart_level_table_style_migrates() -> None:
    def board(style: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": "t",
            "charts": {"c": {"type": "table", "query": "q", "style": style}},
            "rows": ["c"],
        }

    migrated = _migrate(
        board(
            {
                "header": {"rule": {"width": 2}},
                "row": {"rule": {"width": 1}, "roles": {"summary": {"rule_width": 2}}},
                "rule": {"color": "#f00"},
            }
        )
    )
    assert migrated["charts"]["c"]["style"] == {
        "header": {"divider": {"visible": True, "width": 2, "color": "#f00"}},
        "row": {
            "divider": {"visible": True, "width": 1, "color": "#f00"},
            "roles": {
                "summary": {"divider": {"visible": True, "width": 2, "color": "#f00"}},
                "total": {"divider": {"visible": True, "width": 2, "color": "#f00"}},
            },
        },
    }


def test_a_board_nested_in_a_board_migrates() -> None:
    raw = {
        "title": "t",
        "rows": [
            {
                "text": "x",
                "style": {"text": {"column": {"rule": {"color": "#f00"}}}},
            }
        ],
    }
    migrated = _migrate(raw)
    assert migrated["rows"][0]["style"] == {
        "text": {"column": {"divider": {"visible": True, "color": "#f00"}}}
    }
    AuthoredBoard.model_validate(migrated)


def test_a_board_nested_two_deep_compiles() -> None:
    result = compile(
        "title: t\n"
        "rows:\n"
        "  - cols:\n"
        "      - text: x\n"
        "        style:\n"
        "          text:\n"
        "            column:\n"
        "              rule:\n"
        "                color: '#f00'\n"
    )
    assert result.success, result.errors


def test_values_authored_at_two_levels_resolve_per_block() -> None:
    """The old renderer merged the board and chart table styles before choosing
    the line color, so the board's `row.rule.color` beat the chart's
    `rule.color`. Migration sees one block at a time and cannot."""
    raw = {
        "title": "t",
        "style": {"charts": {"table": {"row": {"rule": {"color": "#f00"}}}}},
        "charts": {
            "c": {
                "type": "table",
                "query": "q",
                "style": {"rule": {"color": "#00f"}},
            }
        },
        "rows": ["c"],
    }
    migrated = _migrate(raw)
    assert migrated["charts"]["c"]["style"]["header"] == {"divider": {"color": "#00f"}}


@pytest.mark.parametrize(
    "current",
    [
        {"text": {"column": {"divider": {"color": "#f00"}}}},
        {"footer": {"divider": {"visible": False}}},
        _table({"row": {"divider": {"visible": True, "width": 1}}}),
        {"charts": {"support_table": {"divider": {"color": "#f00"}}}},
    ],
)
def test_a_current_divider_is_left_alone(current: dict[str, Any]) -> None:
    raw = _board(current)
    assert prepare_board_mapping(raw) == raw


def test_a_support_divider_without_continuous_cannot_be_migrated() -> None:
    """``{width: 1}`` is valid in both grammars with different meaning (on, then
    a hidden divider resized), so recognition cannot see it."""
    raw = _board({"charts": {"support_table": {"divider": {"width": 1}}}})
    assert prepare_board_mapping(raw) == raw


def test_text_rewrite_renames_and_adds_visible_in_place() -> None:
    _, registry = _board_migration_context()
    text = (
        "title: t\n"
        "style:\n"
        "  footer:\n"
        "    rule:\n"
        "      color: '#f00'\n"
        "      stroke_width: 2\n"
        "  charts:\n"
        "    table:\n"
        "      row:\n"
        "        roles:\n"
        "          summary:\n"
        "            rule_width: 2\n"
    )
    rewritten = migrate_yaml_text(
        text, catalog=load_yaml_schema_catalog(), registry=registry
    )
    style: Any = load_yaml_mapping(rewritten)["style"]
    assert style["footer"] == {
        "divider": {"visible": True, "color": "#f00", "width": 2}
    }
    roles = style["charts"]["table"]["row"]["roles"]
    assert roles["summary"] == {"divider": {"visible": True, "width": 2}}
    assert roles["total"] == {"divider": {"visible": True, "width": 2}}
