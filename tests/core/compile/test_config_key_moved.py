"""Retired project config keys raise ERR-CONFIG-KEY-MOVED naming the successor."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import (
    _default_config_path,
    load_config,
    reset_config,
    validate_project_config_data,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.diagnostics.hints import CHART_RENDERING_SUCCESSORS


@pytest.fixture(autouse=True)
def _reset_config() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


_PROBE_KEY = "probe.knob"


def test_defaults_ship_no_chart_rendering_section() -> None:
    assert "chart_rendering" not in yaml.safe_load(_default_config_path.read_text())


def test_retired_chart_rendering_key_raises_naming_its_successor(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        CHART_RENDERING_SUCCESSORS, _PROBE_KEY, "style.charts.some.successor"
    )
    (tmp_path / "dbt_charts.yml").write_text(
        "chart_rendering:\n  probe:\n    knob: 0.9\n"
    )

    with pytest.raises(CompilationError) as exc:
        load_config(local_project(tmp_path))

    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-CONFIG-KEY-MOVED"
    message = str(exc.value)
    assert "chart_rendering.probe.knob" in message
    assert "style.charts.some.successor" in message
    assert "successor: 0.9" in message
    assert "charts/meta.yml" in message


def test_retired_estimate_key_says_it_is_an_engine_constant(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(CHART_RENDERING_SUCCESSORS, _PROBE_KEY, None)
    (tmp_path / "dbt_charts.yml").write_text(
        "chart_rendering:\n  probe:\n    knob: 0.9\n"
    )

    with pytest.raises(CompilationError, match="engine constant, not configurable"):
        load_config(local_project(tmp_path))


@pytest.mark.parametrize(
    ("section", "leaf", "successor"),
    [
        ("bar", "grouped_bar_padding_inner", "style.charts.bar.grouped.padding_inner"),
        ("bar", "grouped_bar_padding_outer", "style.charts.bar.grouped.padding_outer"),
        (
            "step_band",
            "disconnected_width",
            "style.charts.marks.line.disconnected_width",
        ),
        (
            "support_table",
            "divider_gap",
            "style.charts.support_table.divider.gap",
        ),
        (
            "stroke",
            "stacked_fallback_width",
            "style.charts.marks.area.stacked.stroke.width",
        ),
        ("kpi", "compact_spec", "style.charts.kpi.value.compact.spec"),
        ("kpi", "compact_threshold", "style.charts.kpi.value.compact.threshold"),
        ("thumbnail", "placeholder_ink", "rendering.thumbnail.placeholder_ink"),
        ("thumbnail", "same_line_share", "rendering.thumbnail.same_line_share"),
    ],
)
def test_retired_style_key_names_its_successor(
    section: str,
    leaf: str,
    successor: str,
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        f"chart_rendering:\n  {section}:\n    {leaf}: 0.5\n"
    )

    with pytest.raises(CompilationError, match=successor):
        load_config(local_project(tmp_path))


def test_project_vega_block_raises_naming_the_theme_style(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        "vega:\n  config:\n    numberFormat: '.4f'\n"
    )

    with pytest.raises(CompilationError) as exc:
        load_config(local_project(tmp_path))

    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-CONFIG-KEY-MOVED"
    assert "charts/meta.yml" in str(exc.value)
    assert "style:" in str(exc.value)


def test_validate_project_config_data_rejects_a_retired_key() -> None:
    with pytest.raises(CompilationError, match="ERR-CONFIG-KEY-MOVED|vega"):
        validate_project_config_data(
            {"vega": {"config": {}}}, filename="dbt_charts.yml"
        )


_ENGINE_CONSTANT_KEYS = sorted(
    [k for k in CHART_RENDERING_SUCCESSORS if k.startswith("pie.")]
    + [
        "axis.label_gap_spaces",
        "axis.label_gap_spaces_numeric",
        "axis.sparse_ceiling_px",
        "type_inference.max_ordinal_buckets",
        "gradient.nice_tick_count",
        "bar.hover_band_trigger_fraction",
        "bar.hover_band_extend_fraction",
        "bar.label_fit_line_height_multiplier",
        "bar.top_rail_label_gap_spaces",
        "endpoint_labels.max_width_fraction",
        "endpoint_labels.line_height_multiplier",
        "facet.min_panel_px",
        "plot_height_floor.ratio",
        "support_table.chart_support_table_max_x_ticks",
        "support_table.plot_width_floor_ratio",
        "support_table.column_block_share_warn_ratio",
        "spark_bar.title_height",
        "spark_bar.title_baseline_y",
        "facet.chrome_px",
        "facet.mirror_axis_px",
        "legend.min_reserve_px",
        "legend.dimension_axis_reserve_chrome_px",
        "legend.row_height_px",
        "legend.chrome_height_px",
        "plot_height_floor.irreducible_height_px",
        "plot_height_floor.axis_titles_height_px",
        "plot_height_floor.subtitle_height_px",
        "plot_height_floor.compact_legend_row_px",
        "bar.plot_height_floor_row_legend_total_px",
        "color_variants.light_k",
        "color_variants.light_chroma",
        "color_variants.light_min_gap",
        "color_variants.light_pale_gap",
        "color_variants.dark_pole",
        "color_variants.dark_k",
        "color_variants.pale_l",
        "color_variants.pale_chroma",
        "color_variants.deep_pole",
        "color_variants.deep_k",
        "color_variants.label_ink_min_contrast",
        "text_contrast.min_ratio",
        "text_contrast.large_text_min_ratio",
        "stroke.min_width",
        "stroke.max_width",
        "stroke.fallback_width",
        "point.diameter_ratio",
        "point.min_px_per_point",
    ]
)


@pytest.mark.parametrize("key", _ENGINE_CONSTANT_KEYS)
def test_retired_engine_constant_key_says_it_is_not_configurable(
    key: str, tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    section, leaf = key.split(".")
    (tmp_path / "dbt_charts.yml").write_text(
        f"chart_rendering:\n  {section}:\n    {leaf}: 1\n"
    )

    with pytest.raises(CompilationError, match="engine constant, not configurable"):
        load_config(local_project(tmp_path))
