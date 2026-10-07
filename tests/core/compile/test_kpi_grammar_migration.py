"""0.9.0 -> 0.10.0: KPI content at root, styling under ``style:``."""

from __future__ import annotations

import warnings
from typing import Any

import pytest
import yaml

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.normalized import KpiChart
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    load_yaml_schema_catalog,
)

_QUERIES = {"q1": {"sql": "select 1 as v, 0.1 as d", "source": "test"}}
_LINE_HEIGHT_WARNING = "line_height has no effect on KPI text slots"


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_mapping(
            raw, catalog=load_yaml_schema_catalog(), registry=registry
        )
    AuthoredBoard.model_validate(migrated)
    return migrated


def _board(chart: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "title": "T",
        "queries": _QUERIES,
        "charts": {"c1": {"type": "kpi", "query": "q1", "value": "v", **chart}},
        "rows": ["c1"],
        **extra,
    }


def _compiled_kpi(yaml_text: str) -> KpiChart:
    result = compile(yaml_text)
    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]
    assert result.board is not None
    chart = result.board.charts["c1"]
    assert isinstance(chart, KpiChart)
    return chart


_OLD_KPI = """title: T
queries:
  q1: {sql: "select 1 as v, 0.1 as d", source: test}
charts:
  c1:
    type: kpi
    query: q1
    value: v
    variant: compact
    support:
      value: d
      label: vs prior
      format: percent_delta
      tone: positive
      glyph: "▲"
    style:
      glyph:
        character: "●"
        font:
          size: 14
      affix:
        font:
          size: 12
      title:
        overflow: wrap
rows: [c1]
"""


def test_old_kpi_grammar_compiles_into_the_new_shape() -> None:
    chart = _compiled_kpi(_OLD_KPI)
    assert chart.glyph == "●"
    assert chart.support is not None
    assert chart.support.model_dump(exclude_none=True) == {
        "value": "d",
        "label": "vs prior",
        "glyph": "▲",
    }
    assert chart.style is not None
    assert chart.style.variant == "compact"
    assert chart.style.support is not None
    assert chart.style.support.format == "percent_delta"
    assert chart.style.support.tone == "positive"
    assert chart.style.value is not None
    assert chart.style.value.glyph is not None
    assert chart.style.value.glyph.font is not None
    assert chart.style.value.glyph.font.size == 14
    assert chart.style.value.affix is not None
    assert chart.style.value.affix.font is not None
    assert chart.style.value.affix.font.size == 12
    assert chart.style.label is not None
    assert chart.style.label.overflow == "wrap"


def test_variant_without_a_style_block_migrates() -> None:
    chart = _compiled_kpi(
        """title: T
queries:
  q1: {sql: "select 1 as v", source: test}
charts:
  c1:
    type: kpi
    query: q1
    value: v
    variant: inline
rows: [c1]
"""
    )
    assert chart.style is not None
    assert chart.style.variant == "inline"


def test_support_styling_without_style_block_migrates() -> None:
    migrated = _migrate(
        _board({"support": {"value": "d", "format": "percent", "tone": "negative"}})
    )
    chart = migrated["charts"]["c1"]
    assert chart["support"] == {"value": "d"}
    assert chart["style"]["support"] == {"format": "percent", "tone": "negative"}


def test_theme_paths_move() -> None:
    migrated = _migrate(
        _board(
            {},
            style={
                "charts": {
                    "kpi": {
                        "affix": {"font": "label"},
                        "glyph": {"font": {"size": 9}, "character": "●"},
                        "title": {"overflow": "clip", "font": {"size": 30}},
                    }
                }
            },
        )
    )
    kpi = migrated["style"]["charts"]["kpi"]
    assert kpi == {
        "value": {"affix": {"font": "label"}, "glyph": {"font": {"size": 9}}},
        "label": {"overflow": "clip"},
    }


def test_chart_local_title_leftovers_and_slot_characters_are_deleted() -> None:
    migrated = _migrate(
        _board(
            {
                "style": {
                    "title": {"overflow": "truncate", "font": {"size": 30}},
                    "label": {"character": "x"},
                    "affix": {"character": "y"},
                }
            }
        )
    )
    assert migrated["charts"]["c1"]["style"] == {"label": {"overflow": "truncate"}}


@pytest.mark.parametrize(
    "chart_style",
    [
        {"font": {"line_height": 1.4}},
        {"value": {"font": {"line_height": 1.4}}},
        {"label": {"font": {"line_height": 1.4}}},
        {"affix": {"font": {"line_height": 1.4}}},
        {"glyph": {"font": {"line_height": 1.4}}},
    ],
    ids=["parent", "value", "label", "affix", "glyph"],
)
def test_kpi_line_height_is_dropped_with_a_warning(
    chart_style: dict[str, Any],
) -> None:
    _, registry = _board_migration_context()
    with pytest.warns(SchemaMigrationWarning, match=_LINE_HEIGHT_WARNING):
        migrated = migrate_mapping(
            _board({"style": chart_style}),
            catalog=load_yaml_schema_catalog(),
            registry=registry,
        )
    assert "line_height" not in str(migrated)
    AuthoredBoard.model_validate(migrated)


def test_theme_kpi_line_height_is_dropped_with_a_warning() -> None:
    _, registry = _board_migration_context()
    raw = _board(
        {},
        style={
            "charts": {
                "kpi": {
                    "font": {"line_height": 1.2},
                    "value": {"font": {"line_height": 1.25}},
                }
            }
        },
    )
    with pytest.warns(SchemaMigrationWarning, match=_LINE_HEIGHT_WARNING):
        migrated = migrate_mapping(
            raw, catalog=load_yaml_schema_catalog(), registry=registry
        )
    assert "line_height" not in str(migrated)


def test_non_kpi_charts_are_untouched() -> None:
    bar = {
        "type": "bar",
        "query": "q1",
        "x": "v",
        "y": "d",
        "style": {"title": {"overflow": "wrap"}},
    }
    raw = {
        "title": "T",
        "queries": _QUERIES,
        "charts": {
            "b1": bar,
            "k1": {"type": "kpi", "query": "q1", "value": "v", "variant": "compact"},
        },
        "rows": ["b1", "k1"],
    }
    migrated = _migrate(raw)
    assert migrated["charts"]["b1"] == bar
    assert migrated["charts"]["k1"]["style"] == {"variant": "compact"}


def test_new_grammar_is_not_migrated() -> None:
    raw = _board({"glyph": "●", "style": {"variant": "inline"}})
    assert _migrate(raw) == raw


_OLD_THEME_KPI = """title: T
queries:
  q1: {sql: "select 1 as v", source: test}
charts:
  c1: {type: kpi, query: q1, value: v}
style:
  charts:
    kpi:
      affix:
        font: label
      glyph:
        font:
          size: 9
          line_height: 1.2
rows: [c1]
"""


@pytest.mark.parametrize("text", [_OLD_KPI, _OLD_THEME_KPI], ids=["chart", "theme"])
def test_text_migration_matches_the_mapping_migration(text: str) -> None:
    _, registry = _board_migration_context()
    catalog = load_yaml_schema_catalog()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        rewritten = migrate_yaml_text(text, catalog=catalog, registry=registry)
        expected = migrate_mapping(
            yaml.safe_load(text), catalog=catalog, registry=registry
        )
    assert yaml.safe_load(rewritten) == expected


def test_old_theme_paths_and_line_height_compile_clean() -> None:
    chart = _compiled_kpi(
        """title: T
queries:
  q1: {sql: "select 1 as v", source: test}
charts:
  c1:
    type: kpi
    query: q1
    value: v
    style:
      font:
        line_height: 1.4
      affix:
        font:
          size: 12
          line_height: 1.4
style:
  charts:
    kpi:
      glyph:
        character: "●"
        font: label
      title:
        overflow: clip
rows: [c1]
"""
    )
    assert chart.style is not None
    assert chart.style.value is not None
    assert chart.style.value.affix is not None
    assert chart.style.value.affix.font is not None
    assert chart.style.value.affix.font.size == 12
