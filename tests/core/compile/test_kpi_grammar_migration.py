"""0.9.0 -> 0.10.0: KPI content at root, styling under ``style:``."""

from __future__ import annotations

import warnings
from typing import Any

import pytest
import yaml

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_yaml_text,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.normalized import KpiChart
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    load_yaml_schema_catalog,
)

from ._engine_migrations import migrate_mapping

_QUERIES = {"q1": {"sql": "select 1 as v, 0.1 as d", "source": "test"}}
_LINE_HEIGHT_WARNING = "line_height has no effect on KPI text slots"


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_mapping(raw)
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
    assert chart.style.label.max_lines == "all"


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
        "label": {"max_lines": 1},
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
    assert migrated["charts"]["c1"]["style"] == {"label": {"max_lines": 1}}


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
    with pytest.warns(SchemaMigrationWarning, match=_LINE_HEIGHT_WARNING):
        migrated = migrate_mapping(
            _board({"style": chart_style}),
        )
    assert "line_height" not in str(migrated)
    AuthoredBoard.model_validate(migrated)


def test_theme_kpi_line_height_is_dropped_with_a_warning() -> None:
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
        migrated = migrate_mapping(raw)
    assert "line_height" not in str(migrated)


def test_non_kpi_charts_are_untouched() -> None:
    bar = {
        "type": "bar",
        "query": "q1",
        "x": "v",
        "y": "d",
        "style": {"title": {"max_lines": "all"}},
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
    load_yaml_schema_catalog()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        rewritten = migrate_yaml_text(text)
        expected = migrate_mapping(yaml.safe_load(text))
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


def test_text_migration_renames_an_overflow_mode_to_its_max_lines() -> None:
    text = (
        "title: T\nstyle:\n  title:\n    overflow: wrap-two\n"
        "  charts:\n    table:\n      header:\n        overflow: wrap\n"
        "rows:\n  - text: hi\n"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        rewritten = migrate_yaml_text(text)
    assert yaml.safe_load(rewritten)["style"] == {
        "title": {"max_lines": 2},
        "charts": {"table": {"header": {"max_lines": "all"}}},
    }


def _text_migrated(text: str) -> Any:  # noqa: ANN401 — a raw board mapping
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return yaml.safe_load(migrate_yaml_text(text))


def test_text_migration_reaches_a_nested_board_under_rows() -> None:
    migrated = _text_migrated(
        "title: T\nrows:\n  - title: N\n    style:\n      title:\n        overflow: wrap-two\n"
        "    rows:\n      - text: hi\n"
    )
    assert migrated["rows"][0]["style"]["title"] == {"max_lines": 2}


def test_text_migration_reaches_a_tab_item() -> None:
    migrated = _text_migrated(
        "title: T\ntabs:\n  items:\n    - title: a\n      style:\n        title:\n"
        "          overflow: truncate\n      rows:\n        - text: hi\n"
    )
    assert migrated["tabs"]["a"]["style"]["title"] == {"max_lines": 1}


def test_text_migration_lands_a_table_wrap_inside_a_tab_pane() -> None:
    head = "title: T\nqueries:\n  q: {sql: select 1 as a, source: test}\ntabs:\n  items:\n    - title: a\n"
    theme = _text_migrated(
        head
        + "      style:\n        charts:\n          table:\n            wrap: true\n"
        "      rows:\n        - text: hi\n"
    )
    assert theme["tabs"]["a"]["style"]["charts"]["table"] == {"max_lines": "all"}
    inline = _text_migrated(
        head
        + "      rows:\n        - type: table\n          query: q\n          style:\n"
        "            wrap: false\n"
    )
    assert inline["tabs"]["a"]["rows"][0]["style"] == {"max_lines": 1}


def test_text_migration_strikes_a_tab_pane_rail_limit() -> None:
    migrated = _text_migrated(
        "title: T\ntabs:\n  items:\n    - title: a\n      style:\n        layout:\n"
        "          tabs:\n            rail_max_lines: 3\n            bar_height: 44\n"
        "      rows:\n        - text: hi\n"
    )
    assert migrated["tabs"]["a"]["style"]["layout"]["tabs"] == {"bar_height": 44}


def test_text_migration_reaches_a_tab_pane_outliers_reveal() -> None:
    migrated = _text_migrated(
        "title: T\ntabs:\n  items:\n    - title: a\n      style:\n        charts:\n"
        "          bar:\n            outliers:\n              reveal: none\n"
        "      rows:\n        - text: hi\n"
    )
    outliers = migrated["tabs"]["a"]["style"]["charts"]["bar"]["outliers"]
    assert outliers["reveal"] == "chart"


def test_axis_migration_reaches_a_tab_item() -> None:
    migrated = _text_migrated(
        "title: T\nqueries:\n  q: {sql: select 1 as a, source: test}\n"
        "tabs:\n  items:\n    - title: a\n      rows:\n        - type: bar\n"
        "          query: q\n          x: a\n          y: a\n          style:\n"
        "            orientation: horizontal\n            axis_y:\n              position: right\n"
    )
    style = migrated["tabs"]["a"]["rows"][0]["style"]
    assert style["axis_x"] == {"position": "right"}
    assert "axis_y" not in style


def test_text_migration_reaches_an_inline_kpi_under_rows() -> None:
    migrated = _text_migrated(
        "title: T\nqueries:\n  q: {sql: select 1 as v, source: test}\nrows:\n"
        "  - type: kpi\n    query: q\n    value: v\n    style:\n      label:\n"
        "        overflow: wrap\n"
    )
    assert migrated["rows"][0]["style"]["label"] == {"max_lines": "all"}
