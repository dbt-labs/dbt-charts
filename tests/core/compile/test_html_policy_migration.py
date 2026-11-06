"""Migration coverage for the 0.4.0 -> 0.5.0 allow_html: bool -> html_policy:
str cut (dbt_charts.core.compile.migrations.versions.v0_5_0), the lossless
value-mapped rename that accompanies the html-policy type change.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_yaml_text,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.parse.parser import load_yaml_mapping
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


def _minimal_board(extra: dict[str, Any]) -> dict[str, Any]:
    """Return a minimal schema-shaped board dict with extra top-level keys."""
    return {
        "title": "Revenue",
        "rows": ["revenue"],
        "charts": {
            "revenue": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
            }
        },
        **extra,
    }


def test_allow_html_true_migrates_to_trusted_raw_in_memory(
    catalog: YamlSchemaCatalog,
) -> None:
    raw = _minimal_board({"allow_html": True})

    migrated = _migrate(raw, catalog)

    assert migrated["html_policy"] == "trusted-raw"
    assert "allow_html" not in migrated
    AuthoredBoard.model_validate(migrated)


def test_allow_html_false_migrates_to_none_in_memory(
    catalog: YamlSchemaCatalog,
) -> None:
    raw = _minimal_board({"allow_html": False})

    migrated = _migrate(raw, catalog)

    assert migrated["html_policy"] == "none"
    assert "allow_html" not in migrated
    AuthoredBoard.model_validate(migrated)


@pytest.mark.parametrize(
    ("allow_html_literal", "expected_html_policy"),
    [("true", "trusted-raw"), ("false", "none")],
)
def test_allow_html_yaml_text_round_trip_preserves_comments(
    catalog: YamlSchemaCatalog, allow_html_literal: str, expected_html_policy: str
) -> None:
    """The file-preserving writer used by `dct migrate` rewrites allow_html:
    to html_policy: <mapped value>, leaving surrounding comments in the file
    intact (a scalar Move deletes the old key and inserts the new one as a
    top-level key -- the comment's line survives, but is not necessarily
    still adjacent to the moved value)."""
    yaml_text = (
        "title: Revenue\n"
        "# this comment must survive\n"
        f"allow_html: {allow_html_literal}\n"
        "rows: [revenue]\n"
        "charts:\n"
        "  revenue:\n"
        "    type: bar\n"
        "    query: revenue\n"
        "    x: month\n"
        "    y: amount\n"
    )

    migrated = migrate_yaml_text(yaml_text)

    assert "# this comment must survive" in migrated
    assert "allow_html" not in migrated
    doc = load_yaml_mapping(migrated)
    assert doc["html_policy"] == expected_html_policy
    AuthoredBoard.model_validate(doc)


def test_allow_html_rejected_on_a_current_board() -> None:
    """Un-migrated allow_html on a current-schema board raises ValidationError via
    extra='forbid' -- no alias, per the no-back-compat rule."""
    from pydantic import ValidationError

    raw: dict[str, Any] = {
        "title": "Revenue",
        "allow_html": True,
        "rows": ["revenue"],
    }

    with pytest.raises(ValidationError, match="allow_html"):
        AuthoredBoard.model_validate(raw)
