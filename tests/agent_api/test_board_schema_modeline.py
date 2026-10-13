"""Every board we ship names its hosted schema exactly when that schema accepts it."""

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from dbt_charts.agent_api.init import init_project
from dbt_charts.core.compile.schema.introspection import introspect
from dbt_charts.core.compile.schema.renderers.json_schema import render_yaml_schema
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    BOARD_SCHEMA_URL,
)

from .._paths import DBT_CHARTS_DIR

MODELINE = f"# yaml-language-server: $schema={BOARD_SCHEMA_URL}"
SHIPPED = DBT_CHARTS_DIR / "src" / "dbt_charts"
BOARD_FILES = sorted(
    path
    for root in (
        SHIPPED / "agent_api" / "_init_templates" / "charts",
        SHIPPED / "ai" / "examples",
        *(SHIPPED / "ai" / "skills").glob("*/examples"),
        DBT_CHARTS_DIR / "examples" / "playground" / "charts",
        DBT_CHARTS_DIR / "examples" / "tutorial_dbt" / "charts",
    )
    for path in root.rglob("*.yml")
)


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    return Draft202012Validator(render_yaml_schema(introspect()))


@pytest.mark.parametrize(
    "path", BOARD_FILES, ids=lambda p: str(p.relative_to(DBT_CHARTS_DIR))
)
def test_modeline_is_on_line_one_exactly_when_the_schema_accepts_the_board(
    path: Path, validator: Draft202012Validator
) -> None:
    text = path.read_text(encoding="utf-8")
    accepted = validator.is_valid(yaml.safe_load(text))
    assert (text.splitlines()[0] == MODELINE) == accepted
    assert (MODELINE in text) == accepted


def test_the_file_set_is_not_empty() -> None:
    assert len(BOARD_FILES) > 50


def test_project_config_template_has_no_board_modeline() -> None:
    template = SHIPPED / "agent_api" / "_init_templates" / "dbt_charts.yml"
    assert "yaml-language-server" not in template.read_text(encoding="utf-8")


def test_init_scaffolds_the_guide_board_with_the_modeline(tmp_path: Path) -> None:
    init_project(tmp_path)
    guide = (tmp_path / "charts" / "guide.yml").read_text(encoding="utf-8")
    assert guide.splitlines()[0] == MODELINE
