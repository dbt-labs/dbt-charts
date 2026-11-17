"""A KPI's conditional rules live at ``style.conditional``.

Each rule is one predicate plus the card style it applies, restricted to the
fields marked ``Conditionable()``. Rules evaluate in order: every matching rule
applies its keys, the later match wins per key, and ``default`` applies only
when nothing else in its column matched. The body path names the row it styles.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.migrations import migrate_yaml_text
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.normalized import KpiChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.parse.parser import load_yaml_mapping
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style_and_context,
)

_CTX = resolve_style_and_context(get_theme_style())[1]
_DATA = [{"total": 100, "change": 12}]


def _chart(style: dict[str, Any], **kwargs: Any) -> KpiChart:
    return KpiChart(
        id="k",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="kpi",
        value="total",
        style=style,
        **kwargs,
    )


@pytest.mark.parametrize(
    "body",
    [
        {"value": {"glyph": {"up": "x"}}},
        {"value": {"format": "number"}},
        {"label": {"font": {"size": 20}}},
        {"border": {"width": 2}},
    ],
)
def test_fields_a_rule_cannot_vary_are_refused(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _chart({"conditional": {"total": [{"gt": 0, **body}]}})


def test_rule_glyph_is_refused() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _chart({"conditional": {"total": [{"gt": 0, "glyph": "x"}]}})


def test_unknown_rule_column_is_refused() -> None:
    with pytest.raises(ValueError, match="style.conditional targets column 'nope'"):
        resolve(
            _chart({"conditional": {"nope": [{"gt": 0, "background": "#fff"}]}}),
            _DATA,
            chart_style_context=_CTX,
        )


def test_theme_scope_conditional_is_refused() -> None:
    text = "style:\n  charts:\n    kpi:\n      conditional:\n        a:\n          - gt: 0\n            background: '#fff'\n"
    with pytest.raises(ValidationError, match="chart-local"):
        AuthoredBoard.model_validate(load_yaml_mapping(text))


_OLD = """\
queries:
  q:
    type: values
    columns: [total, change]
    values:
      - [100, 12]
charts:
  k:
    query: q
    type: kpi
    value: total
    support:
      value: change
    background:
      column: total
      scale:
        palette: ["#ffffff", "#3366cc"]
    conditional_formatting:
      total:
        when:
          - gt: 50
            background: "#166534"
            font:
              color: "#ffffff"
      change:
        when:
          - lt: 0
            font:
              color: "#aa0000"
rows:
  - k
"""


def _migrate(text: str) -> str:
    return migrate_yaml_text(text)


def _migrated(text: str) -> dict[str, Any]:
    return load_yaml_mapping(_migrate(text))["charts"]["k"]


def test_old_kpi_board_migrates() -> None:
    k = _migrated(_OLD)
    assert "conditional_formatting" not in k and "background" not in k
    assert k["style"]["background"]["column"] == "total"
    assert k["style"]["conditional"]["total"] == [
        {"gt": 50, "background": "#166534", "value": {"font": {"color": "#ffffff"}}}
    ]
    assert k["style"]["conditional"]["change"] == [
        {"lt": 0, "support": {"font": {"color": "#aa0000"}}}
    ]


def test_a_support_rule_glyph_cannot_migrate() -> None:
    text = _OLD.replace(
        '            font:\n              color: "#aa0000"',
        '            glyph: "v"',
    )
    with pytest.raises(Exception, match=r"support\.good"):
        _migrate(text)


def test_a_support_rule_tone_migrates_to_support_tone() -> None:
    text = _OLD.replace(
        '            font:\n              color: "#aa0000"',
        "            tone: negative",
    )
    assert _migrated(text)["style"]["conditional"]["change"] == [
        {"lt": 0, "support": {"tone": "negative"}}
    ]


def test_a_headline_rule_tone_cannot_migrate() -> None:
    text = _OLD.replace(
        '            background: "#166534"\n',
        '            background: "#166534"\n            tone: positive\n',
    )
    with pytest.raises(Exception, match=r"headline"):
        _migrate(text)


def test_both_background_spellings_cannot_migrate() -> None:
    text = (
        _OLD.replace("rows:\n  - k", "").replace(
            "    support:\n", "    style:\n      background: '#fff'\n    support:\n"
        )
        + "rows:\n  - k\n"
    )
    with pytest.raises(Exception, match=r"style\.background"):
        _migrate(text)


def test_the_kpi_rule_type_publishes_its_given_name() -> None:
    from dbt_charts.core.compile.models.factories import build_conditional_model
    from dbt_charts.core.compile.models.style.theme.kpi import _KpiChartStyleBase

    rule_cls = build_conditional_model(
        _KpiChartStyleBase, name="KpiChartStyleConditional"
    )
    assert rule_cls.__name__ == "KpiChartStyleConditional"
