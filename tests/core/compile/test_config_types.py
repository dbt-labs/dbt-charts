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
)
from dbt_charts.core.compile.models.config import (
    PUBLISHED_TO_FORM,
    ChartRenderingConfig,
    Config,
    ConfigNode,
    InspectorConfig,
    ServerConfig,
    as_plain_mapping,
    is_mapping_like,
)

ColorVariantsConfig = ChartRenderingConfig.ColorVariantsConfig


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

    # Verify the mapping helper works for non-empty nodes (palettes as example)
    assert is_mapping_like(config.palettes) is True
    palettes = as_plain_mapping(config.palettes)
    assert len(palettes) > 0


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

    assert round_tripped.palettes["vivid-10"] == compiled.palettes["vivid-10"]


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


def test_default_config_yaml_is_valid_settings():
    """default_config.yml must parse cleanly as Settings via model_validate."""
    import yaml

    data = yaml.safe_load(_default_config_path.read_text())
    assert data is not None
    assert "style" not in data, "style: must not appear in default_config.yml"
    assert "theme" not in data, "theme: must not appear in default_config.yml"
    assert data.get("vega", {}).get("default_theme") is None, (
        "vega.default_theme must not appear in default_config.yml"
    )
    assert data.get("vega", {}).get("default_palette") is None, (
        "vega.default_palette must not appear in default_config.yml"
    )
    # Verify Settings.model_validate accepts the merged defaults stack
    settings = get_config()
    Config.model_validate(settings.to_plain_dict(exclude_none=False))


def test_chart_rendering_is_typed_config() -> None:
    """chart_rendering must be a typed ChartRenderingConfig, not a bare ConfigNode."""
    config = get_config()
    assert isinstance(config.chart_rendering, ChartRenderingConfig)


def test_chart_rendering_pie_defaults() -> None:
    cr = get_config().chart_rendering
    assert cr.pie.wedge_label_min_share == 0.08
    assert cr.pie.invisible_slice_share == 0.02
    assert cr.pie.wheel_dominance_min_ratio == 0.6
    assert cr.pie.right_placement_min_width_fraction == 0.5
    assert cr.pie.right_placement_min_width_px == 280.0
    assert cr.pie.right_placement_max_wheel_px == 480.0


def test_chart_rendering_bar_defaults() -> None:
    cr = get_config().chart_rendering
    assert cr.bar.grouped_bar_padding_inner == 0.2
    assert cr.bar.grouped_bar_padding_outer == 0.2


def test_chart_rendering_type_inference_defaults() -> None:
    cr = get_config().chart_rendering
    assert cr.type_inference.max_ordinal_buckets == 60


def test_chart_rendering_frame_defaults() -> None:
    cr = get_config().chart_rendering
    assert cr.frame.footer_rule_gap_px == 2
    assert cr.frame.footer_timestamp_gap_px == 12


def test_chart_rendering_support_table_defaults() -> None:
    cr = get_config().chart_rendering
    assert cr.support_table.divider_gap == 4.0
    assert cr.support_table.chart_support_table_max_x_ticks == 40


def test_chart_rendering_color_variants_defaults() -> None:
    """The seed block beside hover_emphasis, exactly the fitted constants."""
    cv = get_config().chart_rendering.color_variants
    assert isinstance(cv, ColorVariantsConfig)
    assert cv.light_k == 0.30
    assert cv.light_chroma == 0.60
    assert cv.light_min_gap == 0.03
    assert cv.light_pale_gap == 0.03
    assert cv.dark_pole == 0.20
    assert cv.dark_k == 0.30
    assert cv.pale_l == 0.86
    assert cv.pale_chroma == 0.32
    assert cv.deep_pole == 0.28
    assert cv.deep_k == 0.85
    assert cv.label_ink_min_contrast == 4.5


def test_chart_rendering_color_variants_is_frozen_and_hashable() -> None:
    """frozen=True makes it hashable, so a cache keyed on it needs no invalidation."""
    cv = get_config().chart_rendering.color_variants
    assert hash(cv) is not None
    with pytest.raises(ValidationError):
        cv.dark_k = 0.9


def test_chart_rendering_color_variants_override_is_readable(
    tmp_path: object, local_project: Callable[..., FilesystemProject]
) -> None:
    """Overriding one constant in dbt_charts.yml must take effect and leave the rest default."""
    import pathlib

    path = pathlib.Path(str(tmp_path))
    (path / "dbt_charts.yml").write_text(
        "chart_rendering:\n  color_variants:\n    dark_k: 0.6\n"
    )
    load_config(local_project(path))
    cv = get_config().chart_rendering.color_variants
    assert cv.dark_k == 0.6
    assert cv.light_k == 0.30


def test_chart_rendering_color_variants_rejects_an_out_of_range_value(
    tmp_path: object, local_project: Callable[..., FilesystemProject]
) -> None:
    """dark_k is a move fraction, bounded to (0, 1] -- a project value outside
    that range must raise at load, not silently clamp or pass through."""
    import pathlib

    path = pathlib.Path(str(tmp_path))
    (path / "dbt_charts.yml").write_text(
        "chart_rendering:\n  color_variants:\n    dark_k: 1.5\n"
    )
    with pytest.raises(ValidationError, match="dark_k"):
        load_config(local_project(path))


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


def test_chart_rendering_frame_override_is_readable(
    tmp_path: object, local_project: Callable[..., FilesystemProject]
) -> None:
    """A partial frame override must merge with defaults; only the overridden key changes."""
    import pathlib

    path = pathlib.Path(str(tmp_path))
    (path / "dbt_charts.yml").write_text(
        "chart_rendering:\n  frame:\n    footer_timestamp_gap_px: 99\n"
    )
    load_config(local_project(path))
    assert get_config().chart_rendering.frame.footer_timestamp_gap_px == 99
