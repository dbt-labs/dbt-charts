"""Tests for the compiled global config contract (Settings)."""

import re
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import (
    _default_config_path,
    get_config,
    load_config,
    reset_config,
    validate_project_config_data,
)
from dbt_charts.core.compile.models.config import (
    PUBLISHED_TO_FORM,
    CloudConfig,
    Config,
    ConfigNode,
    InspectorConfig,
    ServerConfig,
    as_plain_mapping,
    is_mapping_like,
)


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


def test_server_config_defaults_are_present():
    config = get_config()

    assert config.server.debug is True
    assert config.server.nav is True
    assert config.server.port is None
    assert config.server.markdown_metadata_table is False
    assert config.public_url == ""


def test_server_config_requires_shipped_markdown_metadata_table() -> None:
    with pytest.raises(ValidationError, match="markdown_metadata_table"):
        ServerConfig.model_validate({"debug": True, "nav": True})


def test_config_requires_shipped_public_url() -> None:
    compiled = get_config().to_plain_dict(exclude_none=False)
    del compiled["public_url"]

    with pytest.raises(ValidationError, match="public_url"):
        Config.model_validate(compiled)


def test_published_to_defaults_to_none() -> None:
    assert get_config().published_to is None


def test_published_to_accepts_a_well_formed_url() -> None:
    compiled = get_config().to_plain_dict(exclude_none=False)
    compiled["published_to"] = "https://dbtcharts.com/acme-data/analytics/"

    config = Config.model_validate(compiled)

    assert config.published_to == "https://dbtcharts.com/acme-data/analytics/"


@pytest.mark.parametrize(
    "value",
    [
        "acme-data/analytics",
        "https://dbtcharts.com/acme-data/",
        "https://dbtcharts.com/acme-data/analytics/extra/",
        "ftp://dbtcharts.com/acme-data/analytics/",
        "https://dbtcharts.com/",
    ],
)
def test_published_to_rejects_anything_but_an_absolute_org_project_url(
    value: str,
) -> None:
    compiled = get_config().to_plain_dict(exclude_none=False)
    compiled["published_to"] = value

    with pytest.raises(ValidationError, match=re.escape(PUBLISHED_TO_FORM)):
        Config.model_validate(compiled)


def test_cloud_block_defaults_to_absent() -> None:
    assert get_config().cloud is None


@pytest.mark.parametrize("value", [True, False, None])
def test_cloud_block_accepts_a_boolean_or_unset_switch(value: bool | None) -> None:
    compiled = get_config().to_plain_dict(exclude_none=False)
    compiled["cloud"] = {"allow_public": value, "honor_agent_instructions": value}

    config = Config.model_validate(compiled)

    assert config.cloud == CloudConfig(
        allow_public=value, honor_agent_instructions=value
    )


def test_cloud_block_rejects_an_unknown_key() -> None:
    compiled = get_config().to_plain_dict(exclude_none=False)
    compiled["cloud"] = {"allow_publik": False}

    with pytest.raises(ValidationError, match="cloud.allow_publik"):
        Config.model_validate(compiled)


def test_cloud_block_rejects_a_non_boolean_switch() -> None:
    compiled = get_config().to_plain_dict(exclude_none=False)
    compiled["cloud"] = {"allow_public": "nope"}

    with pytest.raises(ValidationError, match="cloud.allow_public"):
        Config.model_validate(compiled)


def test_settings_requires_yaml_owned_fields():
    # rendering is a required, typed section — removing it must fail validation
    compiled = get_config().to_plain_dict(exclude_none=False)
    del compiled["rendering"]

    with pytest.raises(ValidationError):
        Config.model_validate(compiled)


def test_mapping_interface_preserves_explicit_none_values():
    config = get_config()

    assert "strict" in config
    assert config["strict"] is None

    node = ConfigNode.model_validate({"background": None})
    assert "background" in node
    assert node["background"] is None


def test_mapping_interface_reflects_mutation():
    node = ConfigNode.model_validate({"foo": 1})
    assert node["foo"] == 1

    node.foo = 2
    assert node["foo"] == 2


def test_parent_mapping_reflects_nested_mutation():
    node = ConfigNode.model_validate({"child": {"value": 1}})
    assert node["child"].value == 1

    node.child.value = 2
    assert node["child"].value == 2


def test_mapping_helpers_project_config_nodes_to_plain_dicts():
    config = get_config()

    # Verify the mapping helper works for non-empty nodes (dbt_grays as
    # example -- palettes is unpopulated by default since the categorical
    # scan that used to fill it is gone, see models/config.py's comment).
    assert is_mapping_like(config.dbt_grays) is True
    dbt_grays = as_plain_mapping(config.dbt_grays)
    assert len(dbt_grays) > 0


def test_mapping_helpers_support_open_ended_nodes():
    node = ConfigNode.model_validate({"alpha": 1, "child": {"beta": 2}})

    assert is_mapping_like(node) is True
    assert as_plain_mapping(node) == {"alpha": 1, "child": {"beta": 2}}


@pytest.mark.parametrize("value", [None, 1, "x", [1, 2]])
def test_mapping_helpers_reject_non_mapping_values(value):
    assert is_mapping_like(value) is False


def test_yaml_defaults_round_trip_into_settings():
    compiled = get_config()
    round_tripped = Config.model_validate(compiled.to_plain_dict(exclude_none=False))

    assert round_tripped.dbt_grays["ink"] == compiled.dbt_grays["ink"]


def test_load_settings_rejects_unknown_top_level_keys(
    tmp_path, local_project: Callable[..., FilesystemProject]
):
    (tmp_path / "dbt_charts.yml").write_text("unknown_config_key: true\n")
    project = local_project(tmp_path)

    with pytest.raises(ValidationError):
        load_config(project)


def test_dbt_project_dir_key_is_accepted(
    tmp_path, local_project: Callable[..., FilesystemProject]
):
    """dbt_project_dir: must not trip extra="forbid" -- the shipped docs tell
    users to write this key, and `dct serve` calls load_config at startup."""
    (tmp_path / "dbt_charts.yml").write_text("dbt_project_dir: ../ext\n")
    project = local_project(tmp_path)

    config = load_config(project)

    assert config.dbt_project_dir == "../ext"


def test_dbt_project_dir_defaults_to_none() -> None:
    assert get_config().dbt_project_dir is None


def test_warnings_ignore_key_is_accepted_and_takes_effect(
    tmp_path, local_project: Callable[..., FilesystemProject]
):
    """load_config and Project.warnings_ignore must both accept the same file."""
    (tmp_path / "dbt_charts.yml").write_text(
        "warnings:\n  ignore:\n    - WARN-REAGGREGATION\n"
    )
    project = local_project(tmp_path)

    config = load_config(project)

    assert config.warnings is not None
    assert config.warnings.ignore == ["WARN-REAGGREGATION"]
    assert project.warnings_ignore == frozenset({"WARN-REAGGREGATION"})


def test_warnings_defaults_to_none() -> None:
    assert get_config().warnings is None


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("warnings:\n  ignored:\n    - WARN-REAGGREGATION\n", id="typo"),
        pytest.param("warnings:\n  ignore: WARN-REAGGREGATION\n", id="not-a-list"),
        pytest.param("warnings:\n  ignore:\n    - 42\n", id="non-string-entry"),
    ],
)
def test_malformed_warnings_block_is_rejected(
    body: str, tmp_path, local_project: Callable[..., FilesystemProject]
):
    (tmp_path / "dbt_charts.yml").write_text(body)

    with pytest.raises(ValidationError):
        load_config(local_project(tmp_path))


def test_project_config_validation_rejects_an_unregistered_warning_code() -> None:
    """A typo'd code would otherwise validate clean and fail every render."""
    with pytest.raises(ValueError, match="WARN-TYPO"):
        validate_project_config_data(
            {"warnings": {"ignore": ["WARN-TYPO"]}}, filename="dbt_charts.yml"
        )


def test_default_config_yaml_is_valid_settings():
    """default_config.yml must parse cleanly as Settings via model_validate."""
    import yaml

    data = yaml.safe_load(_default_config_path.read_text())
    assert data is not None
    assert "style" not in data, "style: must not appear in default_config.yml"
    assert "theme" not in data, "theme: must not appear in default_config.yml"
    assert "vega" not in data, "vega: must not appear in default_config.yml"
    # Verify Settings.model_validate accepts the merged defaults stack
    settings = get_config()
    Config.model_validate(settings.to_plain_dict(exclude_none=False))


def test_inspector_is_typed_config() -> None:
    """inspector must be a typed InspectorConfig exposing tree_max_depth."""
    config = get_config()
    assert isinstance(config.inspector, InspectorConfig)
    assert config.inspector.tree_max_depth == 4


def test_inspector_tree_max_depth_override(
    tmp_path: object, local_project: Callable[..., FilesystemProject]
) -> None:
    """Overriding inspector.tree_max_depth in dbt_charts.yml must take effect."""
    import pathlib

    path = pathlib.Path(str(tmp_path))
    (path / "dbt_charts.yml").write_text("inspector:\n  tree_max_depth: 2\n")
    load_config(local_project(path))
    assert get_config().inspector.tree_max_depth == 2
