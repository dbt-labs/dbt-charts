"""Migration coverage for the 0.3.1 -> 0.4.0 axis/scale model reshape.

This is the first real structural transition in the repo and the first case
where a renamed field is a shared style class reused at hundreds of tree
positions (every chart family's axis_x/axis_y, every nested board, every
theme override). That shape uses ``suffix_rename_moves`` — tail substitution
that covers every occurrence of a shared style class without enumerating each
absolute path.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.migrations import (
    MigrationError,
    SchemaMigrationWarning,
    UnsupportedSchemaError,
    migrate_board_yaml_text,
    migrate_yaml_text,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.parse.parser import load_yaml_mapping, parse_yaml
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

from ._engine_migrations import migrate_mapping


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: JsonObject, catalog: YamlSchemaCatalog) -> JsonObject:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw)


def test_label_container_migrates_in_memory(catalog: YamlSchemaCatalog) -> None:
    """The label->labels container rename is a whole-value move: it works for
    migrate_mapping (any JSON value), and — being a pure same-position
    rename — also for the file-preserving writer (see
    test_yaml_text_renames_the_label_container_in_place below)."""
    raw: dict[str, Any] = {
        "charts": {
            "revenue": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
                "style": {
                    "axis_x": {
                        "label": {"angle": -35, "align": "right"},
                        "domain": {"visible": False},
                    }
                },
            }
        },
        "rows": ["revenue"],
    }

    migrated = _migrate(raw, catalog)

    axis_x = migrated["charts"]["revenue"]["style"]["axis_x"]
    assert axis_x == {
        "labels": {"angle": -35, "align": "right"},
        "line": {"visible": False},
    }
    AuthoredBoard.model_validate(migrated)


def test_scale_continuous_nesting_migrates_in_memory(
    catalog: YamlSchemaCatalog,
) -> None:
    raw: dict[str, Any] = {
        "charts": {
            "revenue": {
                "type": "line",
                "query": "revenue",
                "x": "month",
                "y": "amount",
                "style": {
                    "axis_y": {
                        "scale": {"type": "log", "zero": False, "domain": [1, 100]}
                    }
                },
            }
        },
        "rows": ["revenue"],
    }

    migrated = _migrate(raw, catalog)

    assert migrated["charts"]["revenue"]["style"]["axis_y"]["scale"] == {
        "continuous": {"type": "log", "zero": False, "domain": [1, 100]}
    }
    AuthoredBoard.model_validate(migrated)


def test_deleted_field_is_not_silently_migrated(catalog: YamlSchemaCatalog) -> None:
    """categorical_orient was deleted, not renamed — position now serves its
    role with a different default (see AxisYStyle's docstring). No Move can
    express that without changing behavior, so this must fail loudly rather
    than guess."""
    raw: dict[str, Any] = {
        "charts": {
            "revenue": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
                "style": {"axis_y": {"categorical_orient": "left"}},
            }
        },
        "rows": ["revenue"],
    }

    with pytest.raises(UnsupportedSchemaError):
        _migrate(raw, catalog)


def test_removed_domain_strategy_is_not_silently_migrated(
    catalog: YamlSchemaCatalog,
) -> None:
    """domain_strategy was removed outright — 0.3.x spelled it flat under
    `scale`, and there is no current key to move it to. Same shape as
    categorical_orient above: fail loudly instead of carrying a key that
    would parse and do nothing."""
    raw: dict[str, Any] = {
        "charts": {
            "revenue": {
                "type": "line",
                "query": "revenue",
                "x": "month",
                "y": "amount",
                "style": {"axis_y": {"scale": {"domain_strategy": "stable"}}},
            }
        },
        "rows": ["revenue"],
    }

    with pytest.raises(UnsupportedSchemaError):
        _migrate(raw, catalog)


def test_removed_domain_strategy_is_rejected_on_a_current_board() -> None:
    """The nested 0.4.0 spelling has no home either — extra="forbid" rejects it."""
    raw: dict[str, Any] = {
        "charts": {
            "revenue": {
                "type": "line",
                "query": "revenue",
                "x": "month",
                "y": "amount",
                "style": {
                    "axis_y": {"scale": {"continuous": {"domain_strategy": "stable"}}}
                },
            }
        },
        "rows": ["revenue"],
    }

    with pytest.raises(ValidationError, match="domain_strategy"):
        AuthoredBoard.model_validate(raw)


def test_yaml_text_migrates_scalar_leaves_preserving_comments(
    catalog: YamlSchemaCatalog,
) -> None:
    """The file-preserving writer only moves scalar block-mapping leaves
    (see migrate_yaml_text's docstring); every rule this test exercises
    relocates a single scalar rather than a whole mapping."""
    yaml_text = (
        "charts:\n"
        "  revenue:\n"
        "    type: bar\n"
        "    query: revenue\n"
        "    x: month\n"
        "    y: amount\n"
        "    style:\n"
        "      # keep this comment\n"
        "      axis_y:\n"
        "        format: currency_whole\n"
        "        scale:\n"
        "          band_padding_inner: 0.2\n"
        "          type: log\n"
        "      axis_x:\n"
        "        ticks:\n"
        "          size: 8\n"
        "rows: [revenue]\n"
    )

    migrated = migrate_yaml_text(yaml_text)

    assert "# keep this comment" in migrated
    doc = load_yaml_mapping(migrated)
    axis_y = doc["charts"]["revenue"]["style"]["axis_y"]
    assert axis_y["labels"]["format"] == "currency_whole"
    assert axis_y["scale"]["padding"] == 0.2
    assert axis_y["scale"]["continuous"]["type"] == "log"
    assert doc["charts"]["revenue"]["style"]["axis_x"]["ticks"]["length"] == 8
    parse_yaml(migrated)


def test_yaml_text_renames_the_label_container_in_place(
    catalog: YamlSchemaCatalog,
) -> None:
    """label -> labels is a pure same-position rename (old and new path
    share the same parent, axis_x) — renaming never relocates content, so
    the file-preserving writer handles it for any value shape, not just
    scalars: the nested angle stays untouched under the renamed key."""
    yaml_text = (
        "charts:\n"
        "  revenue:\n"
        "    type: bar\n"
        "    query: revenue\n"
        "    x: month\n"
        "    y: amount\n"
        "    style:\n"
        "      axis_x:\n"
        "        label:\n"
        "          angle: -35\n"
        "rows: [revenue]\n"
    )

    migrated = migrate_yaml_text(yaml_text)

    doc = load_yaml_mapping(migrated)
    assert doc["charts"]["revenue"]["style"]["axis_x"] == {"labels": {"angle": -35}}
    parse_yaml(migrated)


def test_yaml_text_rejects_a_move_to_a_genuinely_different_parent(
    catalog: YamlSchemaCatalog,
) -> None:
    """Confirms the file writer's non-scalar restriction is still real for
    an actual relocation (not a same-position rename): the scale reshape
    moves a value into a new nested container, so a non-scalar value there
    must still fail explicitly rather than silently reformatting."""
    yaml_text = (
        "charts:\n"
        "  revenue:\n"
        "    type: line\n"
        "    query: revenue\n"
        "    x: month\n"
        "    y: amount\n"
        "    style:\n"
        "      axis_y:\n"
        "        scale:\n"
        "          domain: [0, 100]\n"
        "rows: [revenue]\n"
    )

    with pytest.raises(MigrationError, match="only scalar"):
        migrate_yaml_text(yaml_text)


def test_yaml_text_renames_the_label_container_across_multiple_charts(
    catalog: YamlSchemaCatalog,
) -> None:
    """The wildcard move (charts.*.style.axis_x.label) resolves once per
    chart, and rename_key_at_path re-parses yaml_text fresh on every call in
    the same loop -- renaming never changes line count, so each chart's
    rename stays correctly targeted regardless of how many precede it."""
    yaml_text = (
        "charts:\n"
        "  revenue:\n"
        "    type: bar\n"
        "    query: revenue\n"
        "    x: month\n"
        "    y: amount\n"
        "    style:\n"
        "      axis_x:\n"
        "        label:\n"
        "          angle: -35\n"
        "  churn:\n"
        "    type: bar\n"
        "    query: churn\n"
        "    x: month\n"
        "    y: rate\n"
        "    style:\n"
        "      axis_x:\n"
        "        label:\n"
        "          angle: 0\n"
        "rows: [revenue, churn]\n"
    )

    migrated = migrate_yaml_text(yaml_text)

    doc = load_yaml_mapping(migrated)
    assert doc["charts"]["revenue"]["style"]["axis_x"] == {"labels": {"angle": -35}}
    assert doc["charts"]["churn"]["style"]["axis_x"] == {"labels": {"angle": 0}}
    parse_yaml(migrated)


def test_reverted_example_board_migrates_to_the_current_shape() -> None:
    """End-to-end proof against real pre-reshape content: this mirrors the
    0.3.1-shaped style block manually fixed in
    examples/dundersign_dbt/charts/executive/growth/bookings-arr.yml during
    the round-2 field reshape (extends: substituted for theme: — the
    theme-shorthand recognition gap is a separate, pre-existing limitation;
    see test_current_pydantic_shorthand_is_not_rejected_by_schema_recognition
    in test_migrations.py). A live CLI run against a scratch project produced
    the identical structure via `dct migrate` end to end."""
    old_yaml_text = (
        "title: Bookings\n"
        "extends: clarity\n"
        "queries:\n"
        "  revenue:\n"
        "    sql: SELECT month, cumulative_won FROM x\n"
        "charts:\n"
        "  arr_line:\n"
        "    type: line\n"
        "    query: revenue\n"
        "    x: month\n"
        "    y: cumulative_won\n"
        "    style:\n"
        "      axis_y:\n"
        "        format: currency_whole\n"
        "rows: [arr_line]\n"
    )

    migrated = migrate_board_yaml_text(old_yaml_text)

    doc = load_yaml_mapping(migrated)
    assert doc["charts"]["arr_line"]["style"]["axis_y"] == {
        "labels": {"format": "currency_whole"}
    }
    parse_yaml(migrated)
