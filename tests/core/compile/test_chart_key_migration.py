"""The 0.11.0 `legend` / `endpoint_labels` / `series_label` grammar migrates to `style.key`."""

from __future__ import annotations

import warnings

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.migrations.migrations import migrate_yaml_text
from dbt_charts.core.compile.parse.parser import load_yaml_mapping

_BOARD = """
queries:
  q:
    type: values
    rows:
      - {{m: "2024-01-01", s: a, v: 1}}
      - {{m: "2024-02-01", s: a, v: 2}}
charts:
  c:
    type: {kind}
    query: q
    x: m
    y: v
    color: s
    style:
{style}
rows:
  - c
"""


def _compile(kind: str, style: str):
    return compile_board(_BOARD.format(kind=kind, style=style))


def test_a_legend_and_endpoint_labels_compile_as_a_key() -> None:
    result = _compile(
        "line",
        "      legend:\n        columns: 2\n        position:\n          edge: endpoints\n"
        "      endpoint_labels:\n        label_offset: 9\n",
    )

    assert result.success, result.errors
    (chart,) = result.board.charts.values()
    assert chart.style.key.type == "direct"
    assert chart.style.key.legend.columns == 2
    assert chart.style.key.direct.offset == 9


def test_a_shown_legend_is_the_legend_type_and_a_hidden_one_hides_a_scatter_key() -> (
    None
):
    shown = _compile("scatter", "      legend:\n        visible: true\n")
    hidden = _compile("scatter", "      legend:\n        visible: false\n")

    (shown_chart,) = shown.board.charts.values()
    (hidden_chart,) = hidden.board.charts.values()
    assert (shown_chart.style.key.visible, shown_chart.style.key.type) == (
        True,
        "legend",
    )
    assert hidden_chart.style.key.visible is False


def test_a_hidden_legend_on_a_line_is_dropped() -> None:
    migrated = migrate_yaml_text(
        _BOARD.format(kind="line", style="      legend:\n        visible: false\n")
    )

    assert "key:" not in migrated


def test_a_legend_beside_endpoint_labels_is_refused() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = _compile(
            "line",
            "      legend:\n        visible: true\n        position:\n"
            "          edge: endpoints\n",
        )

    assert not result.success
    assert any("names the series twice" in w.message for w in result.warnings)


def test_a_legend_holding_values_and_a_position_rewrites_as_text() -> None:
    """The entry list sits at the legend's own column, beside a `position` the
    `edge` rewrite splits across two parents."""
    text = _BOARD.format(
        kind="line",
        style=(
            "      legend:\n"
            "        position: top\n"
            "        direction: horizontal\n"
            "        values:\n"
            "        - Solar\n"
            "        - Natural gas"
        ),
    )

    migrated = load_yaml_mapping(migrate_yaml_text(text))

    assert migrated["charts"]["c"]["style"] == {
        "key": {
            "legend": {
                "position": {"edge": "top", "overlay": False},
                "direction": "horizontal",
                "values": ["Solar", "Natural gas"],
            }
        }
    }
