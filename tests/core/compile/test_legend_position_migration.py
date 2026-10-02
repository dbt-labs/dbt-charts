"""0.9.0 -> 0.10.0: ``legend.position`` scalar becomes ``{edge, align, overlay}``
(dbt_charts.core.compile.migrations.versions.v0_10_0).
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    collect_migration_notices,
    migrate_mapping,
    migrate_yaml_text,
    prepare_board_mapping,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.migrations.versions.v0_10_0 import (
    LEGEND_POSITION_EXPANSION,
)
from dbt_charts.core.compile.parse.parser import load_yaml_mapping
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore::dbt_charts.core.compile.migrations.SchemaMigrationWarning"
)


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: JsonObject, catalog: YamlSchemaCatalog) -> JsonObject:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


def _bar(position: Any) -> dict[str, Any]:
    return {
        "title": "t",
        "charts": {
            "c": {
                "type": "bar",
                "query": "q",
                "x": "a",
                "y": "b",
                "style": {"legend": {"position": position}},
            }
        },
        "rows": ["c"],
    }


def _pie(position: Any) -> dict[str, Any]:
    return {
        "title": "t",
        "charts": {
            "p": {
                "type": "pie",
                "query": "q",
                "theta": "b",
                "style": {"legend": {"position": position}},
            }
        },
        "rows": ["p"],
    }


@pytest.mark.parametrize("old", sorted(LEGEND_POSITION_EXPANSION))
def test_cartesian_scalar_expands_to_edge_align_overlay(
    old: str, catalog: YamlSchemaCatalog
) -> None:
    migrated = _migrate(_bar(old), catalog)
    assert migrated["charts"]["c"]["style"]["legend"]["position"] == dict(
        LEGEND_POSITION_EXPANSION[old]
    )


@pytest.mark.parametrize("old", sorted(LEGEND_POSITION_EXPANSION))
def test_pie_scalar_keeps_edge_and_align_and_drops_overlay(
    old: str, catalog: YamlSchemaCatalog
) -> None:
    expected = {
        leaf: value
        for leaf, value in LEGEND_POSITION_EXPANSION[old].items()
        if leaf != "overlay"
    }
    migrated = _migrate(_pie(old), catalog)
    assert migrated["charts"]["p"]["style"]["legend"]["position"] == expected


def test_family_theme_slot_migrates_per_family(catalog: YamlSchemaCatalog) -> None:
    raw: dict[str, Any] = {
        "title": "t",
        "style": {
            "charts": {
                "legend": {"position": "bottom"},
                "bar": {"legend": {"position": "top-right"}},
                "pie": {"legend": {"position": "top-right"}},
            }
        },
        "rows": ["c"],
    }
    legends = _migrate(raw, catalog)["style"]["charts"]
    assert legends["legend"]["position"] == {"edge": "bottom", "overlay": False}
    assert legends["bar"]["legend"]["position"] == {
        "edge": "top",
        "align": "end",
        "overlay": True,
    }
    assert legends["pie"]["legend"]["position"] == {"edge": "top", "align": "end"}


def test_dropping_a_pie_corner_overlay_is_reported(
    catalog: YamlSchemaCatalog,
) -> None:
    _, registry = _board_migration_context()
    with collect_migration_notices() as notices:
        migrate_mapping(_pie("top-left"), catalog=catalog, registry=registry)
    assert any("overlay was dropped" in notice.message for notice in notices)


def test_a_pie_cardinal_drops_its_pinned_defaults_silently(
    catalog: YamlSchemaCatalog,
) -> None:
    _, registry = _board_migration_context()
    with collect_migration_notices() as notices:
        migrate_mapping(_pie("bottom"), catalog=catalog, registry=registry)
    assert not any("overlay was dropped" in notice.message for notice in notices)


def test_a_current_mapping_is_left_alone() -> None:
    raw = _bar({"edge": "top", "align": "end", "overlay": True})
    assert prepare_board_mapping(raw) == raw


def test_prepare_board_mapping_migrates_an_old_board_without_dct_migrate() -> None:
    migrated = prepare_board_mapping(_bar("top-left"))
    assert migrated["charts"]["c"]["style"]["legend"]["position"] == {
        "edge": "top",
        "align": "start",
        "overlay": True,
    }


def test_text_rewrite_expands_the_scalar_in_place(
    catalog: YamlSchemaCatalog,
) -> None:
    _, registry = _board_migration_context()
    text = (
        "title: t\n"
        "charts:\n"
        "  c:\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: a\n"
        "    y: b\n"
        "    style:\n"
        "      legend:\n"
        "        position: bottom-right\n"
        "        direction: horizontal\n"
        "rows:\n"
        "  - c\n"
    )
    rewritten = migrate_yaml_text(text, catalog=catalog, registry=registry)
    legend = load_yaml_mapping(rewritten)["charts"]["c"]["style"]["legend"]
    assert legend["position"] == {"edge": "bottom", "align": "end", "overlay": True}
    assert legend["direction"] == "horizontal"
    assert "rows:\n  - c\n" in rewritten


def test_text_rewrite_of_a_pie_corner_writes_edge_and_align(
    catalog: YamlSchemaCatalog,
) -> None:
    _, registry = _board_migration_context()
    text = (
        "title: t\n"
        "charts:\n"
        "  p:\n"
        "    type: pie\n"
        "    query: q\n"
        "    theta: b\n"
        "    style:\n"
        "      legend:\n"
        "        position: top-left\n"
        "rows:\n"
        "  - p\n"
    )
    rewritten = migrate_yaml_text(text, catalog=catalog, registry=registry)
    assert load_yaml_mapping(rewritten)["charts"]["p"]["style"]["legend"] == {
        "position": {"edge": "top", "align": "start"}
    }


def _chart(position: Any, kind: str = "bar") -> dict[str, Any]:
    chart: dict[str, Any] = {
        "type": kind,
        "query": "q",
        "style": {"legend": {"position": position}},
    }
    chart.update({"theta": "b"} if kind == "pie" else {"x": "a", "y": "b"})
    return chart


@pytest.mark.parametrize("nesting", ["rows", "cols"])
def test_a_chart_inside_a_sub_board_migrates(
    nesting: str, catalog: YamlSchemaCatalog
) -> None:
    raw: dict[str, Any] = {
        "title": "t",
        nesting: [{"title": "sub", nesting: [_chart("left")]}],
    }
    migrated = _migrate(raw, catalog)
    legend = migrated[nesting][0][nesting][0]["style"]["legend"]
    assert legend["position"] == {"edge": "left", "overlay": False}


def test_a_sub_boards_own_charts_map_migrates(catalog: YamlSchemaCatalog) -> None:
    raw: dict[str, Any] = {
        "title": "t",
        "rows": [
            {
                "title": "sub",
                "charts": {"c": _chart("bottom-right")},
                "rows": ["c"],
            }
        ],
    }
    migrated = _migrate(raw, catalog)
    position = migrated["rows"][0]["charts"]["c"]["style"]["legend"]["position"]
    assert position == {"edge": "bottom", "align": "end", "overlay": True}


def test_a_sub_boards_theme_slot_migrates_once_per_family(
    catalog: YamlSchemaCatalog,
) -> None:
    raw: dict[str, Any] = {
        "title": "t",
        "rows": [
            {
                "title": "sub",
                "style": {"charts": {"legend": {"position": "top"}}},
                "rows": [_chart("right", "pie")],
            }
        ],
    }
    migrated = _migrate(raw, catalog)
    sub = migrated["rows"][0]
    assert sub["style"]["charts"]["legend"]["position"]["edge"] == "top"
    assert sub["rows"][0]["style"]["legend"]["position"] == {"edge": "right"}


def test_text_rewrite_reaches_a_sub_board(catalog: YamlSchemaCatalog) -> None:
    _, registry = _board_migration_context()
    text = (
        "title: t\n"
        "rows:\n"
        "  - title: sub\n"
        "    rows:\n"
        "      - type: bar\n"
        "        query: q\n"
        "        x: a\n"
        "        y: b\n"
        "        style:\n"
        "          legend:\n"
        "            position: top-left\n"
    )
    rewritten = migrate_yaml_text(text, catalog=catalog, registry=registry)
    legend = load_yaml_mapping(rewritten)["rows"][0]["rows"][0]["style"]["legend"]
    assert legend["position"] == {"edge": "top", "align": "start", "overlay": True}


def test_a_migrated_cardinal_pie_table_keeps_its_centered_default() -> None:
    """A legacy `position: bottom` pins no `align`, so the attached table stays
    centered under the card instead of moving to the left edge."""
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.models.style.authored import PieChartStylePatch

    from ...core._board_utils import make_test_resolved_chart

    migrated = prepare_board_mapping(_pie("bottom"))
    chart = PieChart(
        id="p",
        type="pie",
        query_name="q",
        theta="v",
        color="n",
        style=PieChartStylePatch.model_validate(migrated["charts"]["p"]["style"]),
    )
    rows = [
        {"n": name, "v": value}
        for name, value in [
            ("A", 40.0),
            ("B", 30.0),
            ("C", 20.0),
            ("D", 8.0),
            ("E", 1.5),
        ]
    ]
    resolved = make_test_resolved_chart(chart, rows, width=1100.0)
    assert (resolved.attached_table_placement, resolved.attached_table_align) == (
        "bottom",
        "center",
    )
