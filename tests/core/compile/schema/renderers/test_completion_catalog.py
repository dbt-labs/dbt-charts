"""Tests for the shared editor completion-catalog builder.

Both the Cloud dashboard editor and the Playground fetch this catalog to drive
schema-aware YAML completions, so its assembly lives once in dbt_charts.core.

A checked-in fixture that consumes this catalog is kept in sync by a
guard test outside dbt-charts/, not covered here.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import user_facing_theme_names
from dbt_charts.core.compile.schema.renderers.completion_catalog import (
    build_completion_catalog,
)

from ...._dangling_refs import find_dangling_refs


def test_theme_names_exclude_synthetic_default_and_private_and_diagnostics() -> None:
    """ "default" is not a real theme name — get_theme_style("default") raises.

    Offering it in completion used to be an authoring trap: autocomplete would
    suggest a spelling that breaks compilation. user_facing_theme_names() is
    the one canonical listing completion_catalog now reads (no more
    synthetic-"default" prepending of its own).
    """
    names = user_facing_theme_names()

    assert "default" not in names
    assert all(not name.startswith("_") for name in names)
    assert all(not name.startswith("diagnostics-") for name in names)
    assert "clarity" in names


def test_build_catalog_returns_the_board_json_schema() -> None:
    catalog = build_completion_catalog()

    # render_yaml_schema output: a JSON Schema with a $defs table, the
    # top-level board properties, and the theme enum sourced from the names above.
    assert "$defs" in catalog
    assert "charts" in catalog["properties"]
    assert "theme" in catalog["properties"]


def test_catalog_has_no_dangling_refs() -> None:
    """The completion catalog shares render_yaml_schema with the IDE board schema,
    so it inherits the same dangling-$ref risk (see test_ide_schema_generation.py's
    test_schema_has_no_dangling_refs for the underlying bug)."""
    dangling = find_dangling_refs(build_completion_catalog())
    assert not dangling, f"Dangling $ref targets: {dangling}"
