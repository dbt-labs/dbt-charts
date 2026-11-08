"""``endpoint_labels.visible`` becomes ``legend.position.edge`` at the 0.12.0 boundary."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.compiler import CompileResult, compile_file
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    collect_migration_notices,
    migrate_board_yaml_text,
    migrate_yaml_text,
    prepare_board_mapping,
)
from dbt_charts.core.compile.migrations.migrations import (
    MigrationError,
    package_schemas,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedLineChart,
)
from dbt_charts.core.compile.parse.parser import load_yaml_mapping
from dbt_charts.core.compile.resolve import resolve

_DEV = package_schemas().dev.version

_ROWS: list[dict[str, Any]] = [
    {"m": "2024-01-01", "s": "a", "v": 1},
    {"m": "2024-02-01", "s": "a", "v": 2},
    {"m": "2024-01-01", "s": "b", "v": 3},
    {"m": "2024-02-01", "s": "b", "v": 4},
]

_BOARD = """
queries:
  q:
    type: values
    rows:
      - {m: "2024-01-01", s: a, v: 1}
      - {m: "2024-02-01", s: a, v: 2}
      - {m: "2024-01-01", s: b, v: 3}
      - {m: "2024-02-01", s: b, v: 4}
%s
rows:
  - c
"""

_CHART = (
    "charts:\n  c:\n    type: line\n    query: q\n    x: m\n    y: v\n    color: s\n"
)


def _quiet_prepare(raw: dict[str, Any]) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return prepare_board_mapping(raw)


def _refusal(raw: dict[str, Any]) -> str:
    """The warning a migration that cannot finish reports; the board is left as authored."""
    with pytest.warns(SchemaMigrationWarning, match="could not finish") as caught:
        prepare_board_mapping(raw)
    return str(caught[0].message)


def _chart(style: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "line",
        "query": "q",
        "x": "m",
        "y": "v",
        "color": "s",
        "style": style,
    }


def _chart_style(style: dict[str, Any]) -> dict[str, Any]:
    migrated = _quiet_prepare(
        {
            "charts": {
                "c": {
                    "type": "line",
                    "query": "q",
                    "x": "m",
                    "y": "v",
                    "style": style,
                }
            },
            "rows": ["c"],
        }
    )
    AuthoredBoard.model_validate(migrated)
    return migrated["charts"]["c"]["style"]


@pytest.mark.parametrize(("visible", "edge"), [(True, "endpoints"), (False, "top")])
def test_visible_becomes_the_legend_edge(visible: bool, edge: str) -> None:
    style = _chart_style({"endpoint_labels": {"visible": visible}})

    assert style["legend"] == {"position": {"edge": edge}}
    assert "visible" not in style.get("endpoint_labels", {})


def test_auto_alone_becomes_edge_auto() -> None:
    style = _chart_style({"endpoint_labels": {"visible": "auto", "label_offset": 9}})

    assert style == {
        "endpoint_labels": {"label_offset": 9},
        "legend": {"position": {"edge": "auto"}},
    }


def test_the_other_endpoint_label_keys_stay() -> None:
    style = _chart_style({"endpoint_labels": {"visible": True, "label_offset": 9}})

    assert style["endpoint_labels"] == {"label_offset": 9}
    assert style["legend"] == {"position": {"edge": "endpoints"}}


@pytest.mark.parametrize("visible", [False, "auto"])
def test_an_authored_side_beside_a_false_or_auto_wins(visible: bool | str) -> None:
    style = _chart_style(
        {
            "endpoint_labels": {"visible": visible},
            "legend": {"position": {"edge": "bottom"}, "columns": 3},
        }
    )

    assert style["legend"] == {"position": {"edge": "bottom"}, "columns": 3}
    assert "endpoint_labels" not in style


def test_true_beside_an_authored_side_becomes_endpoints() -> None:
    style = _chart_style(
        {
            "endpoint_labels": {"visible": True},
            "legend": {"position": {"edge": "bottom"}},
        }
    )

    assert style == {"legend": {"position": {"edge": "endpoints"}}}


def test_the_edge_joins_an_existing_legend_block() -> None:
    style = _chart_style(
        {"endpoint_labels": {"visible": False}, "legend": {"columns": 4}}
    )

    assert style["legend"] == {"columns": 4, "position": {"edge": "top"}}


@pytest.mark.parametrize("family", ["line", "area", "bar"])
def test_the_family_tier_moves_too(family: str) -> None:
    migrated = _quiet_prepare(
        {
            "style": {
                "charts": {family: {"endpoint_labels": {"visible": True}}},
            },
            "text": "x",
        }
    )

    assert migrated["style"]["charts"][family]["legend"] == {
        "position": {"edge": "endpoints"}
    }
    AuthoredBoard.model_validate(migrated)


def test_a_nested_board_moves() -> None:
    migrated = _quiet_prepare(
        {
            "rows": [
                {
                    "style": {
                        "charts": {"line": {"endpoint_labels": {"visible": False}}}
                    },
                    "text": "x",
                }
            ]
        }
    )

    assert migrated["rows"][0]["style"]["charts"]["line"]["legend"] == {
        "position": {"edge": "top"}
    }


_OLD_VS_NEW = [
    (
        "endpoint_labels:\n        visible: true",
        "legend:\n        position:\n          edge: endpoints",
    ),
    (
        "endpoint_labels:\n        visible: false",
        "legend:\n        position:\n          edge: top",
    ),
    ("endpoint_labels:\n        visible: auto", "{}"),
    (
        "endpoint_labels:\n        visible: false\n      legend:\n        visible: false",
        "legend:\n        visible: false\n        position:\n          edge: top",
    ),
]


@pytest.mark.parametrize(("old", "new"), _OLD_VS_NEW)
def test_old_spelling_compiles_like_the_new_one(old: str, new: str) -> None:
    def resolved(style: str) -> dict[str, Any]:
        result = compile_board(_BOARD % (_CHART + f"    style:\n      {style}"))
        assert result.success, result.errors
        assert result.board is not None
        return resolve(
            result.board.charts["c"],
            _ROWS,
            chart_style_context=result.board.chart_style_context,
            width=900.0,
        ).model_dump(mode="json")

    assert resolved(old) == resolved(new)


def test_hidden_legend_beside_hidden_labels_names_nothing() -> None:
    result = compile_board(
        _BOARD
        % (
            _CHART + "    style:\n      endpoint_labels:\n        visible: false\n"
            "      legend:\n        visible: false\n"
        )
    )

    assert result.board is not None
    chart = resolve(
        result.board.charts["c"],
        _ROWS,
        chart_style_context=result.board.chart_style_context,
        width=900.0,
    )
    assert isinstance(chart, ResolvedLineChart)
    assert chart.style.endpoint_labels.visible is False
    assert chart.legend.visible is False


def test_text_rewrite_moves_the_key() -> None:
    text = _BOARD % (
        _CHART + "    style:\n      endpoint_labels:\n        visible: false\n"
        "        label_offset: 9\n"
    )
    migrated = migrate_yaml_text(text, stop_target=_DEV)

    style = load_yaml_mapping(migrated)["charts"]["c"]["style"]
    assert style["legend"] == {"position": {"edge": "top"}}
    assert style["endpoint_labels"] == {"label_offset": 9}


def test_text_rewrite_lets_an_authored_side_win_over_false() -> None:
    text = _BOARD % (
        _CHART + "    style:\n      endpoint_labels:\n        visible: false\n"
        "      legend:\n        position:\n          edge: left\n"
    )
    migrated = migrate_yaml_text(text, stop_target=_DEV)

    style = load_yaml_mapping(migrated)["charts"]["c"]["style"]
    assert style == {"legend": {"position": {"edge": "left"}}}


def test_text_rewrite_writes_auto() -> None:
    text = _BOARD % (
        _CHART + "    style:\n      endpoint_labels:\n        visible: auto\n"
        "        label_offset: 9\n"
    )
    migrated = migrate_yaml_text(text, stop_target=_DEV)

    style = load_yaml_mapping(migrated)["charts"]["c"]["style"]
    assert style == {
        "endpoint_labels": {"label_offset": 9},
        "legend": {"position": {"edge": "auto"}},
    }


@pytest.mark.parametrize("visible", ["true", "false", "auto"])
def test_text_rewrite_leaves_no_empty_endpoint_labels_block(visible: str) -> None:
    text = _BOARD % (
        _CHART + f"    style:\n      endpoint_labels:\n        visible: {visible}\n"
    )
    migrated = migrate_yaml_text(text, stop_target=_DEV)

    assert "endpoint_labels" not in migrated
    AuthoredBoard.model_validate(load_yaml_mapping(migrated))


@pytest.mark.parametrize("framing", ["", "# kept\n", "---\n"])
@pytest.mark.parametrize(("visible", "edge"), [("false", "top"), ("auto", "auto")])
def test_text_rewrite_of_a_file_holding_only_the_retired_key(
    visible: str, edge: str, framing: str
) -> None:
    text = (
        f"{framing}style:\n  charts:\n    line:\n"
        f"      endpoint_labels:\n        visible: {visible}\n"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_yaml_text(text, stop_target=_DEV)

    assert load_yaml_mapping(migrated)["style"] == {"charts": {"line": _edge(edge)}}


def test_text_rewrite_reads_text_only_the_board_loader_accepts() -> None:
    text = (
        _BOARD % (_CHART + "    style:\n      endpoint_labels:\n        visible: false")
        + "title: 'x'\t\n"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_yaml_text(text, stop_target=_DEV)

    assert load_yaml_mapping(migrated)["charts"]["c"]["style"] == _edge("top")


@pytest.mark.parametrize("chart_type", ["scatter", "heatmap"])
@pytest.mark.parametrize("visible", [False, "banana"])
@pytest.mark.parametrize(
    "beside",
    [{}, {"legend": {"position": {"edge": "left"}}}],
    ids=["alone", "beside edge"],
)
def test_visible_off_a_rail_chart_is_refused(
    chart_type: str, visible: bool | str, beside: dict[str, Any]
) -> None:
    style = {"endpoint_labels": {"visible": visible}, **beside}
    chart = {"type": chart_type, "query": "q", "x": "m", "y": "v", "style": style}

    refusal = _refusal({"charts": {"c": chart}, "rows": ["c"]})

    assert "not on a line, area or bar chart" in refusal


@pytest.mark.parametrize(
    ("style", "migrated"),
    [
        ({"endpoint_labels": {"visible": True}}, {}),
        ({"endpoint_labels": {"visible": False}}, {}),
        (
            {
                "endpoint_labels": {"visible": False},
                "legend": {"position": {"edge": "left"}},
            },
            {"legend": {"position": {"edge": "left"}}},
        ),
    ],
    ids=["true", "false", "false beside edge"],
)
def test_visible_on_a_histogram_is_struck(
    in_memory_project: Callable, style: dict[str, Any], migrated: dict[str, Any]
) -> None:
    chart = {"type": "histogram", "query": "q", "x": "v", "style": style}
    raw = {"charts": {"c": chart}, "rows": ["c"]}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SchemaMigrationWarning)
        prepared = prepare_board_mapping(raw)

    assert prepared["charts"]["c"].get("style", {}) == migrated
    about = [
        str(w.message)
        for w in caught
        if str(w.message).startswith("`charts.c.style.endpoint_labels.visible`")
    ]
    assert about == [
        "`charts.c.style.endpoint_labels.visible` was struck: it had no effect on "
        "a histogram."
    ]
    project = in_memory_project(
        Path("/tmp/test-endpoint-labels-histogram"),
        {"charts/board.yml": _BOARD % yaml.safe_dump({"charts": {"c": chart}})},
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile_file(project.path("charts/board.yml").read_board())
    assert result.success, result.errors


def test_an_unrecognized_visible_on_a_histogram_is_refused() -> None:
    chart = {
        "type": "histogram",
        "query": "q",
        "x": "v",
        "style": {"endpoint_labels": {"visible": "banana"}},
    }

    refusal = _refusal({"charts": {"c": chart}, "rows": ["c"]})

    assert "endpoint_labels.visible: 'banana'" in refusal


@pytest.mark.parametrize(
    ("visible", "edge"),
    [
        ('"false"', "top"),
        ("off", "top"),
        ("0", "top"),
        ("yes", "endpoints"),
        ("1", "endpoints"),
    ],
)
def test_a_coerced_visible_migrates_as_it_resolved(visible: str, edge: str) -> None:
    text = _BOARD % (
        _CHART + f"    style:\n      endpoint_labels:\n        visible: {visible}\n"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_yaml_text(text, stop_target=_DEV)

    style = load_yaml_mapping(migrated)["charts"]["c"]["style"]
    assert style == {"legend": {"position": {"edge": edge}}}


@pytest.mark.parametrize("visible", ["banana", "2"])
def test_an_unrecognized_visible_raises(visible: str) -> None:
    text = _BOARD % (
        _CHART + f"    style:\n      endpoint_labels:\n        visible: {visible}\n"
    )
    with pytest.raises(MigrationError, match="endpoint_labels.visible"):
        migrate_yaml_text(text, stop_target=_DEV)


def test_false_alone_writes_top() -> None:
    raw = {
        "charts": {"c": _chart({"endpoint_labels": {"visible": False}})},
        "rows": ["c"],
    }

    assert _quiet_prepare(raw)["charts"]["c"]["style"]["legend"] == {
        "position": {"edge": "top"}
    }


_EDGE_ENDPOINTS = {"legend": {"position": {"edge": "endpoints"}}}


@pytest.mark.parametrize("null", ["null", "~"])
def test_null_visible_is_struck(null: str) -> None:
    text = _BOARD % (
        _CHART + f"    style:\n      endpoint_labels:\n        visible: {null}\n"
        "        label_offset: 9\n"
    )
    migrated = migrate_yaml_text(text, stop_target=_DEV)

    style = load_yaml_mapping(migrated)["charts"]["c"]["style"]
    assert style == {"endpoint_labels": {"label_offset": 9}}
    AuthoredBoard.model_validate(load_yaml_mapping(migrated))


@pytest.mark.parametrize(("visible", "style"), [(None, {}), (True, _EDGE_ENDPOINTS)])
@pytest.mark.parametrize(
    "broader",
    [
        {"legend": {"position": {"edge": "bottom"}}},
        {"line": {"endpoint_labels": {"visible": False}}},
    ],
)
def test_null_and_true_migrate_locally_amid_other_settings(
    visible: bool | None, style: dict[str, Any], broader: dict[str, Any]
) -> None:
    raw = {
        "style": {"charts": broader},
        "charts": {"c": _chart({"endpoint_labels": {"visible": visible}})},
        "rows": ["c"],
    }

    assert _quiet_prepare(raw)["charts"]["c"].get("style", {}) == style


_SIDE = {"legend": {"position": {"edge": "bottom"}}}
_FALSE = {"endpoint_labels": {"visible": False}}
_AUTO = {"endpoint_labels": {"visible": "auto"}}


def _inline(style: dict[str, Any]) -> dict[str, Any]:
    return {"type": "line", "query": "q", "x": "m", "y": "v", "style": style}


def _series_chart(
    style: dict[str, Any] | None = None, chart_type: str = "line"
) -> dict[str, Any]:
    chart: dict[str, Any] = {
        "type": chart_type,
        "query": "q",
        "x": "m",
        "y": "v",
        "color": "s",
    }
    if style is not None:
        chart["style"] = style
    return chart


def _visible(value: bool | str) -> dict[str, Any]:
    return {"endpoint_labels": {"visible": value}}


def _edge(edge: str) -> dict[str, Any]:
    return {"legend": {"position": {"edge": edge}}}


def _charts_style(**scopes: dict[str, Any]) -> dict[str, Any]:
    """A `style.charts` block: `block=` holds its own keys, the rest are families."""
    block = scopes.pop("block", {})
    return {"style": {"charts": {**block, **scopes}}}


def _board_text(raw: dict[str, Any]) -> str:
    return yaml.safe_dump(
        {"queries": {"q": {"type": "values", "rows": _ROWS}}, **raw}, sort_keys=False
    )


_Placed = tuple[bool, bool, str]
_RAIL: _Placed = (True, False, "right")


def _legend(edge: str) -> _Placed:
    return (False, True, edge)


def _placed(board: Board) -> list[_Placed]:
    placed: list[_Placed] = []
    for item in board.layout.items:
        if item.chart is not None:
            chart = resolve(
                item.chart,
                _ROWS,
                chart_style_context=board.chart_style_context,
                width=900.0,
            )
            assert isinstance(
                chart, ResolvedLineChart | ResolvedAreaChart | ResolvedBarChart
            )
            placed.append(
                (
                    chart.style.endpoint_labels.visible,
                    chart.legend.visible,
                    chart.legend.position.edge,
                )
            )
        if item.board is not None:
            placed.extend(_placed(item.board))
    return placed


_C = {"charts": {"c": _series_chart()}, "rows": ["c"]}


def _tabs_in_tabs(inner: dict[str, Any]) -> dict[str, Any]:
    return {"items": [{"title": "a", "tabs": {"items": [{"title": "b", **inner}]}}]}


_ONE_FAMILY_TWO_BOARDS = {
    **_charts_style(line=_visible(False)),
    "charts": {"c": _series_chart(), "d": _series_chart()},
    "rows": ["c", {**_charts_style(block=_edge("bottom")), "rows": ["d"]}],
}
_FALSE_C = {"charts": {"c": _series_chart(_visible(False))}}

# Each board as the previous grammar authored it, and what every placed chart
# resolved to before the migration: (endpoint labels, legend visible, edge).
# Where the old board left the legend at the family's default position, the
# migrated `false` names `top`.
_RESOLVED: list[tuple[str, dict[str, Any], list[_Placed]]] = [
    (
        "board top, family false beside edge auto",
        {
            **_charts_style(
                block=_edge("top"), line={**_visible(False), **_edge("auto")}
            ),
            **_C,
        },
        [_legend("top")],
    ),
    (
        "board top, family false, chart edge auto",
        {
            **_charts_style(block=_edge("top"), line=_visible(False)),
            "charts": {"c": _series_chart(_edge("auto"))},
            "rows": ["c"],
        },
        [_legend("top")],
    ),
    (
        "family false, chart edge auto",
        {
            **_charts_style(line=_visible(False)),
            "charts": {"c": _series_chart(_edge("auto"))},
            "rows": ["c"],
        },
        [_legend("top")],
    ),
    (
        "family false, chart edge top",
        {
            **_charts_style(line=_visible(False)),
            "charts": {"c": _series_chart(_edge("top"))},
            "rows": ["c"],
        },
        [_legend("top")],
    ),
    (
        "root family true, nested board top with family false",
        {
            **_charts_style(line=_visible(True)),
            "charts": {"c": _series_chart()},
            "rows": [
                {
                    **_charts_style(block=_edge("top"), line=_visible(False)),
                    "rows": ["c"],
                }
            ],
        },
        [_legend("top")],
    ),
    (
        "sibling families both false",
        {
            **_charts_style(line=_visible(False), bar=_visible(False)),
            "charts": {"c": _series_chart(), "b": _series_chart(chart_type="bar")},
            "rows": ["c", "b"],
        },
        [_legend("top"), _legend("top")],
    ),
    (
        "family false beside a sibling family's edge",
        {
            **_charts_style(line=_visible(False), bar=_edge("top")),
            "charts": {"c": _series_chart(), "b": _series_chart(chart_type="bar")},
            "rows": ["c", "b"],
        },
        [_legend("top"), _legend("top")],
    ),
    (
        "board top, chart false",
        {**_charts_style(block=_edge("top")), **_FALSE_C, "rows": ["c"]},
        [_legend("top")],
    ),
    (
        "family false, chart false",
        {**_charts_style(line=_visible(False)), **_FALSE_C, "rows": ["c"]},
        [_legend("top")],
    ),
    (
        "family false, chart auto",
        {
            **_charts_style(line=_visible(False)),
            "charts": {"c": _series_chart(_visible("auto"))},
            "rows": ["c"],
        },
        [_RAIL],
    ),
    (
        "root family false, nested family auto",
        {
            **_charts_style(line=_visible(False)),
            "rows": [
                {**_charts_style(line=_visible("auto")), "rows": [_series_chart()]}
            ],
        },
        [_RAIL],
    ),
    (
        "board top, family false, nested board edge auto",
        {
            **_charts_style(block=_edge("top"), line=_visible(False)),
            "charts": {"c": _series_chart()},
            "rows": [{**_charts_style(block=_edge("auto")), "rows": ["c"]}],
        },
        [_legend("top")],
    ),
    (
        "board bottom, chart false",
        {**_charts_style(block=_edge("bottom")), **_FALSE_C, "rows": ["c"]},
        [_legend("bottom")],
    ),
    (
        "nested board bottom, chart false",
        {
            **_FALSE_C,
            "rows": [{**_charts_style(block=_edge("bottom")), "rows": ["c"]}],
        },
        [_legend("bottom")],
    ),
    (
        "tab bottom, chart false",
        {
            **_FALSE_C,
            "tabs": {
                "items": [
                    {
                        "title": "t",
                        **_charts_style(block=_edge("bottom")),
                        "rows": ["c"],
                    }
                ]
            },
        },
        [_legend("bottom")],
    ),
    (
        "grid item in a nested board bottom, chart false",
        {
            **_FALSE_C,
            "rows": [
                {
                    **_charts_style(block=_edge("bottom")),
                    "grid": {"items": [{"item": "c", "col": 0, "row": 0}]},
                }
            ],
        },
        [_legend("bottom")],
    ),
    (
        "named inline chart false under board bottom",
        {
            **_charts_style(block=_edge("bottom")),
            "rows": [{"c": _series_chart(_visible(False))}],
        },
        [_legend("bottom")],
    ),
    (
        "nested family false under a root family bottom",
        {
            **_charts_style(line=_edge("bottom")),
            "charts": {"c": _series_chart()},
            "rows": [{**_charts_style(line=_visible(False)), "rows": ["c"]}],
        },
        [_legend("bottom")],
    ),
    (
        "root family false and bottom outranks a nested board top",
        {
            **_charts_style(line={**_visible(False), **_edge("bottom")}),
            "charts": {"c": _series_chart()},
            "rows": [{**_charts_style(block=_edge("top")), "rows": ["c"]}],
        },
        [_legend("bottom")],
    ),
    (
        "root family false takes a nested board's bottom",
        {
            **_charts_style(line=_visible(False)),
            "charts": {"c": _series_chart()},
            "rows": [{**_charts_style(block=_edge("bottom")), "rows": ["c"]}],
        },
        [_legend("bottom")],
    ),
    (
        "one chart placed twice under the same edge",
        {
            **_FALSE_C,
            "rows": [
                {**_charts_style(block=_edge("bottom")), "rows": ["c"]},
                {**_charts_style(block=_edge("bottom")), "rows": ["c"]},
            ],
        },
        [_legend("bottom"), _legend("bottom")],
    ),
    (
        "the analytics meta.yml shape",
        {
            **_charts_style(
                block=_edge("top"),
                line=_visible(False),
                area=_visible(False),
                bar=_visible(False),
            ),
            "charts": {"c": _series_chart(), "b": _series_chart(chart_type="bar")},
            "rows": ["c", "b"],
        },
        [_legend("top"), _legend("top")],
    ),
    (
        "tabs in tabs, chart false",
        {"tabs": _tabs_in_tabs({"rows": [_series_chart(_visible(False))]})},
        [_legend("top")],
    ),
    (
        "tabs in tabs, family false",
        {
            "tabs": _tabs_in_tabs(
                {**_charts_style(line=_visible(False)), "rows": [_series_chart()]}
            )
        },
        [_legend("top")],
    ),
    (
        "root family false, chart edge auto in tabs in tabs",
        {
            **_charts_style(line=_visible(False)),
            "tabs": _tabs_in_tabs({"rows": [_series_chart(_edge("auto"))]}),
        },
        [_legend("top")],
    ),
    (
        "chart defined in a sibling board",
        {
            "rows": [
                {
                    "charts": {"c": _series_chart(_edge("auto"))},
                    "rows": [_series_chart()],
                },
                {**_charts_style(line=_visible(False)), "rows": ["c"]},
            ]
        },
        [_RAIL, _legend("top")],
    ),
    (
        "one family, two boards",
        _ONE_FAMILY_TWO_BOARDS,
        [_legend("top"), _legend("bottom")],
    ),
    (
        "root family false, nested block left",
        {
            **_charts_style(line=_visible(False)),
            "charts": {"c": _series_chart(), "d": _series_chart()},
            "rows": ["c", {**_charts_style(block=_edge("left")), "rows": ["d"]}],
        },
        [_legend("top"), _legend("left")],
    ),
    (
        "family true",
        {
            **_charts_style(area=_visible(True)),
            "charts": {"a": _series_chart(chart_type="area")},
            "rows": ["a"],
        },
        [_RAIL],
    ),
    (
        "chart true",
        {"charts": {"c": _series_chart(_visible(True))}, "rows": ["c"]},
        [_RAIL],
    ),
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(raw, expected) for _, raw, expected in _RESOLVED],
    ids=[name for name, _, _ in _RESOLVED],
)
def test_old_boards_resolve_as_they_did(
    raw: dict[str, Any], expected: list[_Placed]
) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile_board(_board_text(raw))

    assert result.success, result.errors
    assert result.board is not None
    assert _placed(result.board) == expected


_AGREEMENT = [
    "board top, family false beside edge auto",
    "family false, chart edge auto",
    "family false, chart auto",
    "root family true, nested board top with family false",
    "root family false, nested family auto",
    "the analytics meta.yml shape",
    "tabs in tabs, family false",
    "one family, two boards",
]


@pytest.mark.parametrize(
    "raw",
    [raw for name, raw, _ in _RESOLVED if name in _AGREEMENT]
    + [
        {
            "charts": {
                "c": _series_chart(
                    {"endpoint_labels": {"visible": False, "label_offset": None}}
                )
            },
            "rows": ["c"],
        }
    ],
    ids=[*_AGREEMENT, "label_offset null beside visible"],
)
def test_the_text_and_mapping_paths_agree(raw: dict[str, Any]) -> None:
    text = _board_text(raw)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_yaml_text(text, stop_target=_DEV)

    from_text = load_yaml_mapping(migrated)
    del from_text["_schema_version"]
    assert from_text == _quiet_prepare(load_yaml_mapping(text))
    assert "visible" not in migrated


@pytest.mark.parametrize(
    ("raw", "names"),
    [
        (
            {
                **_FALSE_C,
                "rows": [
                    {**_charts_style(block=_edge("bottom")), "rows": ["c"]},
                    {"rows": ["c"]},
                ],
            },
            ["charts.c.style", "rows.0.rows.0", "rows.1.rows.0"],
        ),
    ],
    ids=["one chart, two placements"],
)
def test_a_chart_placed_where_it_resolved_apart_is_refused(
    raw: dict[str, Any], names: list[str]
) -> None:
    message = _refusal(raw)

    for name in names:
        assert f"`{name}`" in message


def test_a_side_in_the_same_mapping_migrates_amid_other_settings() -> None:
    raw = {
        "style": {"charts": _SIDE},
        "charts": {
            "c": _chart({**_FALSE, **{"legend": {"position": {"edge": "left"}}}})
        },
        "rows": ["c"],
    }

    migrated = _quiet_prepare(raw)
    assert migrated["charts"]["c"]["style"] == {
        "legend": {"position": {"edge": "left"}}
    }


def test_the_text_and_mapping_paths_agree_on_a_side_amid_other_settings() -> None:
    text = (
        "style:\n  charts:\n    legend:\n      position:\n        edge: right\n"
        + _BOARD
        % (
            _CHART + "    style:\n      endpoint_labels:\n        visible: false\n"
            "      legend:\n        position:\n          edge: left\n"
        )
    )
    migrated = migrate_yaml_text(text, stop_target=_DEV)

    from_text = load_yaml_mapping(migrated)
    del from_text["_schema_version"]
    assert from_text == _quiet_prepare(load_yaml_mapping(text))
    assert "endpoint_labels" not in migrated


@pytest.mark.parametrize(
    "style",
    [
        "      endpoint_labels:\n        visible: null\n",
        "      endpoint_labels:\n        visible: false\n      legend: null\n",
        "      endpoint_labels:\n        visible: false\n"
        "      legend:\n        position: null\n",
    ],
)
def test_text_and_mapping_paths_agree_on_null_siblings(style: str) -> None:
    text = _BOARD % (_CHART + "    style:\n" + style)
    migrated = migrate_yaml_text(text, stop_target=_DEV)

    AuthoredBoard.model_validate(load_yaml_mapping(migrated))
    AuthoredBoard.model_validate(_quiet_prepare(load_yaml_mapping(text)))


def test_a_null_visible_is_dropped_by_the_style_null_rule() -> None:
    """`visible: null` was never a value past 0.10.0: the style-null rule of that
    boundary drops it, and its notice is the one owed."""
    text = _BOARD % (
        _CHART + "    style:\n      endpoint_labels:\n        visible: null\n"
    )

    result = compile_board(text)

    assert result.success, result.errors
    notices = [w.message for w in result.warnings if w.code == "WARN-SCHEMA-MIGRATED"]
    assert any(n.startswith("style null is accepted only where") for n in notices)
    assert not any("endpoint_labels.visible" in n for n in notices), notices


def _migrated_charts(*charts: dict[str, Any]) -> dict[str, Any]:
    return _quiet_prepare(
        {
            "charts": {f"c{i}": chart for i, chart in enumerate(charts)},
            "rows": [f"c{i}" for i in range(len(charts))],
        }
    )["charts"]


_TOP = {"legend": {"position": {"edge": "top"}}}


def test_sibling_charts_each_false_migrate_independently() -> None:
    charts = _migrated_charts(_inline(_FALSE), _inline(_FALSE))

    assert [charts[c]["style"] for c in charts] == [_TOP, _TOP]


def test_false_beside_a_sibling_chart_label_offset_migrates() -> None:
    charts = _migrated_charts(
        _inline(_FALSE), _inline({"endpoint_labels": {"label_offset": 9}})
    )

    assert charts["c0"]["style"] == _TOP
    assert charts["c1"]["style"] == {"endpoint_labels": {"label_offset": 9}}


def test_false_beside_a_sibling_chart_with_its_own_edge_migrates() -> None:
    charts = _migrated_charts(_inline(_FALSE), _inline(_SIDE))

    assert charts["c0"]["style"] == _TOP
    assert charts["c1"]["style"] == _SIDE


def test_sibling_true_and_false_migrate_independently() -> None:
    charts = _migrated_charts(
        _inline({"endpoint_labels": {"visible": True}}), _inline(_FALSE)
    )

    assert charts["c0"]["style"] == _EDGE_ENDPOINTS
    assert charts["c1"]["style"] == _TOP


def test_auto_beside_sibling_settings_becomes_edge_auto() -> None:
    charts = _migrated_charts(_inline(_AUTO), _inline(_SIDE))

    assert charts["c0"]["style"] == {"legend": {"position": {"edge": "auto"}}}


def test_true_beside_edge_auto_becomes_endpoints() -> None:
    charts = _migrated_charts(
        _inline(
            {
                "endpoint_labels": {"visible": True},
                "legend": {"position": {"edge": "auto"}},
            }
        )
    )

    assert charts["c0"]["style"] == _EDGE_ENDPOINTS


def test_family_false_beside_the_blocks_concrete_edge_writes_it() -> None:
    """Another file's family outranks this block, so the family keeps its own edge."""
    migrated = _quiet_prepare(
        {
            "style": {
                "charts": {
                    **_SIDE,
                    "line": _FALSE,
                    "area": _FALSE,
                    "bar": _FALSE,
                }
            },
            "text": "x",
        }
    )

    assert migrated["style"]["charts"] == {
        **_SIDE,
        "line": _SIDE,
        "area": _SIDE,
        "bar": _SIDE,
    }


def test_lone_family_false_writes_top() -> None:
    raw = {"style": {"charts": {"line": _FALSE}}, "text": "x"}

    assert _quiet_prepare(raw)["style"]["charts"]["line"] == _TOP


_ANALYTICS_META = """\
style:
  charts:
    legend:
      position:
        edge: top
    line:
      endpoint_labels:
        visible: false
    area:
      endpoint_labels:
        visible: false
    bar:
      endpoint_labels:
        visible: false
"""


def test_a_project_meta_yml_with_family_false_and_a_top_legend_compiles(
    in_memory_project: Callable,
) -> None:
    board = _BOARD % (_CHART + "    title: t\n")
    project = in_memory_project(
        Path("/tmp/test-endpoint-labels-meta"),
        {"charts/board.yml": board, "charts/meta.yml": _ANALYTICS_META},
    )

    result = compile_file(project.path("charts/board.yml").read_board())

    assert result.success, result.errors
    assert result.board is not None
    chart = resolve(
        result.board.charts["c"],
        _ROWS,
        chart_style_context=result.board.chart_style_context,
        width=900.0,
    )
    assert isinstance(chart, ResolvedLineChart)
    assert chart.legend.position.edge == "top"
    assert chart.style.endpoint_labels.visible is False


def test_a_chart_that_resolved_apart_from_its_family_gets_its_own_edge() -> None:
    migrated = _quiet_prepare(_ONE_FAMILY_TWO_BOARDS)

    assert migrated["style"]["charts"]["line"] == _TOP
    assert migrated["charts"]["d"]["style"] == {
        "legend": {"position": {"edge": "bottom"}}
    }
    assert "style" not in migrated["charts"]["c"]


def test_false_beside_edge_endpoints_keeps_its_meaning() -> None:
    raw = {
        "charts": {"c": _series_chart({**_visible(False), **_edge("endpoints")})},
        "rows": ["c"],
    }

    assert _quiet_prepare(raw)["charts"]["c"]["style"] == _TOP


_DATED = """\
queries:
  q:
    type: values
    rows:
      - {m: "2024-01-01", s: a, v: 1}
      - {m: "2024-02-01", s: b, v: 2}
%s
charts:
  c:
    type: line
    query: q
    x: m
    y: v
    color: s
    annotations:
      - type: rule
        x: 2024-01-15
        text: launch
%s
rows:
  - c
"""
_FAMILY_FALSE_TEXT = (
    "style:\n  charts:\n    line:\n      endpoint_labels:\n        visible: false"
)
_CHART_FALSE_TEXT = "    style:\n      endpoint_labels:\n        visible: false"


@pytest.mark.parametrize(
    "text",
    [_DATED % (_FAMILY_FALSE_TEXT, ""), _DATED % ("", _CHART_FALSE_TEXT)],
    ids=["family false", "chart false"],
)
def test_a_yaml_date_does_not_strand_the_migration(text: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile_board(text)
        migrated = migrate_yaml_text(text, stop_target=_DEV)

    assert result.success, result.errors
    assert result.board is not None
    assert _placed(result.board) == [_legend("top")]
    assert "x: 2024-01-15\n" in migrated
    assert "visible" not in migrated
    assert migrate_board_yaml_text(text) == text


def test_a_current_board_with_an_edge_and_a_typo_is_not_read_as_old() -> None:
    result = compile_board(
        _BOARD
        % (
            _CHART + "    style:\n      nonsense_key: 1\n      legend:\n"
            "        position:\n          edge: top\n"
        )
    )

    assert [e.code for e in result.errors] == ["ERR-EXTRA-FIELD"]
    assert not [w for w in result.warnings if w.code == "WARN-SCHEMA-MIGRATED"]


_META_FAMILY_FALSE = """\
style:
  charts:
    line:
      endpoint_labels:
        visible: false
"""


def _compile_under_meta(
    in_memory_project: Callable, chart_style: dict[str, Any]
) -> list[_Placed]:
    raw = {"charts": {"c": _series_chart(chart_style)}, "rows": ["c"]}
    project = in_memory_project(
        Path("/tmp/test-endpoint-labels-cross-file"),
        {"charts/board.yml": _board_text(raw), "charts/meta.yml": _META_FAMILY_FALSE},
    )
    result = compile_file(project.path("charts/board.yml").read_board())
    assert result.success, result.errors
    assert result.board is not None
    return _placed(result.board)


def test_a_chart_auto_under_a_meta_false_keeps_its_rail(
    in_memory_project: Callable,
) -> None:
    assert _compile_under_meta(in_memory_project, _visible("auto")) == [_RAIL]


_ALIAS_QUERY = """\
queries:
  q:
    type: values
    rows:
      - {m: "2024-01-01", s: a, v: 1}
      - {m: "2024-02-01", s: a, v: 2}
      - {m: "2024-01-01", s: b, v: 3}
      - {m: "2024-02-01", s: b, v: 4}
"""
_ALIAS_CHARTS = """\
charts:
  c:
    type: line
    query: q
    x: m
    y: v
    color: s
  a:
    type: %s
    query: q
    x: m
    y: v
    color: s
rows:
  - c
  - a
"""


_ALIASED: list[tuple[str, str]] = list(
    zip(
        [
            "two families share endpoint_labels",
            "a family merges another",
            "a line and an area chart share endpoint_labels",
            "two line charts merge one style",
        ],
        [
            _ALIAS_QUERY
            + "style:\n  charts:\n    line:\n      endpoint_labels: &e\n"
            + "        visible: false\n    area:\n      endpoint_labels: *e\n"
            + _ALIAS_CHARTS % "area",
            _ALIAS_QUERY
            + "style:\n  charts:\n    line: &f\n      endpoint_labels:\n"
            + "        visible: false\n    area:\n      <<: *f\n"
            + _ALIAS_CHARTS % "area",
            _ALIAS_QUERY
            + """\
charts:
  c:
    type: line
    query: q
    x: m
    y: v
    color: s
    style:
      endpoint_labels: &e
        visible: false
  a:
    type: area
    query: q
    x: m
    y: v
    color: s
    style:
      endpoint_labels: *e
rows:
  - c
  - a
""",
            _ALIAS_QUERY
            + """\
charts:
  c:
    type: line
    query: q
    x: m
    y: v
    color: s
    style: &s
      endpoint_labels:
        visible: false
  d:
    type: line
    query: q
    x: m
    y: v
    color: s
    style:
      <<: *s
      font:
        size: 12
rows:
  - c
  - d
""",
        ],
        strict=True,
    )
)


@pytest.mark.parametrize(
    "text", [text for _, text in _ALIASED], ids=[name for name, _ in _ALIASED]
)
def test_every_aliased_location_keeps_its_false(text: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile_board(text)

    assert result.success, result.errors
    assert result.board is not None
    assert _placed(result.board) == [_legend("top"), _legend("top")]


def test_no_module_state_keeps_the_board_after_migration() -> None:
    from dbt_charts.core.compile.migrations import migrations

    secret = "select 'kept-past-its-migration' as m"
    _quiet_prepare(
        {
            "queries": {"q": {"sql": secret}},
            **_charts_style(line=_visible(False)),
            **_C,
        }
    )

    assert not any(secret in repr(value) for value in vars(migrations).values())


def test_a_chart_named_type_is_not_read_as_an_inline_chart() -> None:
    raw = {
        "rows": [{"charts": {"type": _series_chart(_visible(False))}}],
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile_board(_board_text(raw))

    assert result.success, result.errors
    assert _quiet_prepare(raw)["rows"][0]["charts"]["type"]["style"] == _TOP


@pytest.mark.parametrize(
    ("position", "struck"),
    [
        ({"edge": "top", "align": "end"}, "`align`"),
        ({"edge": "top", "overlay": True}, "`overlay`"),
        ({"align": "center"}, "`align`"),
    ],
)
def test_true_beside_a_placed_align_or_overlay_strikes_it(
    position: dict[str, Any], struck: str
) -> None:
    raw = {
        "charts": {
            "c": _chart(
                {
                    "endpoint_labels": {"visible": True},
                    "legend": {"position": position},
                }
            )
        }
    }
    with collect_migration_notices() as notices:
        style = prepare_board_mapping(raw)["charts"]["c"]["style"]

    assert style == {"legend": {"position": {"edge": "endpoints"}}}
    assert any(struck in n.message and "was struck" in n.message for n in notices)


def test_text_rewrite_strikes_align_beside_true() -> None:
    text = _BOARD % (
        _CHART + "    style:\n      endpoint_labels:\n        visible: true\n"
        "      legend:\n        position:\n          edge: top\n"
        "          align: end\n"
    )
    migrated = migrate_yaml_text(text, stop_target=_DEV)

    style = load_yaml_mapping(migrated)["charts"]["c"]["style"]
    assert style == {"legend": {"position": {"edge": "endpoints"}}}


_META_ALIGN_END = """\
style:
  charts:
    legend:
      position:
        edge: top
        align: end
        overlay: true
"""


def test_a_meta_yml_align_does_not_stop_a_board_chart_true(
    in_memory_project: Callable,
) -> None:
    board = _BOARD % (
        _CHART + "    style:\n      endpoint_labels:\n        visible: true\n"
    )
    project = in_memory_project(
        Path("/tmp/test-endpoint-labels-meta-align"),
        {"charts/board.yml": board, "charts/meta.yml": _META_ALIGN_END},
    )

    result = compile_file(project.path("charts/board.yml").read_board())

    assert result.success, result.errors
    assert result.board is not None
    style = result.board.charts["c"].style.model_dump(exclude_none=True)
    assert style["legend"]["position"] == {"edge": "endpoints"}
    assert _placed(result.board) == [(True, False, "top")]


_META_ALIGN_CENTER = """\
style:
  charts:
    legend:
      position:
        align: center
"""


def test_a_meta_yml_align_under_the_board_blocks_endpoints_compiles(
    in_memory_project: Callable,
) -> None:
    """Same tier, different files: the align is inherited, not written beside."""
    board = _BOARD % (
        "style:\n  charts:\n    legend:\n      position:\n"
        "        edge: endpoints\n" + _CHART
    )
    project = in_memory_project(
        Path("/tmp/test-endpoint-labels-meta-align-block"),
        {"charts/board.yml": board, "charts/meta.yml": _META_ALIGN_CENTER},
    )

    result = compile_file(project.path("charts/board.yml").read_board())

    assert result.success, result.errors
    assert result.board is not None
    assert _placed(result.board) == [_RAIL]


_BLOCK_ENDPOINTS_ALIGN = (
    "style:\n  charts:\n    legend:\n      position:\n"
    "        edge: endpoints\n        align: center\n"
)


@pytest.mark.parametrize("in_meta", [False, True], ids=["board", "meta.yml"])
def test_align_beside_endpoints_in_one_project_file_is_an_error(
    in_memory_project: Callable, in_meta: bool
) -> None:
    board = _BOARD % (_CHART if in_meta else _BLOCK_ENDPOINTS_ALIGN + _CHART)
    files = {"charts/board.yml": board}
    if in_meta:
        files["charts/meta.yml"] = _BLOCK_ENDPOINTS_ALIGN
    project = in_memory_project(Path("/tmp/test-endpoint-labels-align-error"), files)

    result = compile_file(project.path("charts/board.yml").read_board())

    assert [e.code for e in result.errors] == ["ERR-LEGEND-ALIGN-WITH-ENDPOINTS"]


_ALIGNED_TRUE = {
    "endpoint_labels": {"visible": True},
    "legend": {"position": {"align": "center"}},
}

# Boards whose migration writes through a list (`rows`, `tabs.items`), and what
# each placed chart resolves to.
_THROUGH_A_LIST: list[tuple[str, dict[str, Any], list[_Placed]]] = [
    (
        "pin write: root family false, nested board edge",
        {
            **_charts_style(line=_visible(False)),
            "rows": [
                _series_chart(),
                {**_charts_style(block=_edge("bottom")), "rows": [_series_chart()]},
            ],
        },
        [_legend("top"), _legend("bottom")],
    ),
    (
        "inline chart in rows: true beside align",
        {"rows": [_series_chart(_ALIGNED_TRUE)]},
        [_RAIL],
    ),
    (
        "inline chart in tabs: true beside align",
        {"tabs": {"items": [{"title": "a", "rows": [_series_chart(_ALIGNED_TRUE)]}]}},
        [_RAIL],
    ),
    (
        "nested family true under a root align",
        {
            "style": {"charts": {"legend": {"position": {"align": "center"}}}},
            "rows": [{**_charts_style(line=_visible(True)), "rows": [_series_chart()]}],
        },
        [_RAIL],
    ),
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(raw, expected) for _, raw, expected in _THROUGH_A_LIST],
    ids=[name for name, _, _ in _THROUGH_A_LIST],
)
def test_a_migration_writing_through_a_list_compiles(
    raw: dict[str, Any], expected: list[_Placed]
) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile_board(_board_text(raw))

    assert result.success, result.errors
    assert result.board is not None
    assert _placed(result.board) == expected


@pytest.mark.parametrize(
    "raw",
    [raw for _, raw, _ in _THROUGH_A_LIST],
    ids=[name for name, _, _ in _THROUGH_A_LIST],
)
def test_text_and_mapping_paths_agree_through_a_list(raw: dict[str, Any]) -> None:
    text = _board_text(raw)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_yaml_text(text, stop_target=_DEV)

    from_text = load_yaml_mapping(migrated)
    del from_text["_schema_version"]
    assert from_text == _quiet_prepare(load_yaml_mapping(text))


def test_true_beside_align_in_rows_strikes_it_and_says_so() -> None:
    with collect_migration_notices() as notices:
        migrated = prepare_board_mapping({"rows": [_series_chart(_ALIGNED_TRUE)]})

    assert migrated["rows"][0]["style"] == {
        "legend": {"position": {"edge": "endpoints"}}
    }
    assert any("`align`" in n.message and "was struck" in n.message for n in notices)


_FAMILY_ALIGN = {"legend": {"position": {"align": "center"}}}
_FAMILY_OVERLAY = {"legend": {"position": {"overlay": True}}}
_ENDPOINTS = _edge("endpoints")
_STACKED_BAR_ENDPOINTS = {**_ENDPOINTS, "stack": "zero"}


def _rows_of(*charts: dict[str, Any]) -> dict[str, Any]:
    named = {f"c{i}": chart for i, chart in enumerate(charts)}
    return {"charts": named, "rows": list(named)}


# Each board meets a family-tier `align` or `overlay` with `edge: endpoints`
# from another tier or file; every chart in it must name its series on the
# rail. File names map to their contents.
_CROSSINGS: list[tuple[str, dict[str, dict[str, Any]]]] = [
    (
        "family align, chart edge",
        {
            "board.yml": {
                **_charts_style(line=_FAMILY_ALIGN),
                **_rows_of(_series_chart(_ENDPOINTS)),
            }
        },
    ),
    (
        "family overlay, chart edge",
        {
            "board.yml": {
                **_charts_style(line=_FAMILY_OVERLAY),
                **_rows_of(_series_chart(_ENDPOINTS)),
            }
        },
    ),
    (
        "family edge, chart align",
        {
            "board.yml": {
                **_charts_style(line=_ENDPOINTS),
                **_rows_of(_series_chart(_FAMILY_ALIGN)),
            }
        },
    ),
    (
        "family edge, chart overlay",
        {
            "board.yml": {
                **_charts_style(line=_ENDPOINTS),
                **_rows_of(_series_chart(_FAMILY_OVERLAY)),
            }
        },
    ),
    (
        "bar family align, chart edge",
        {
            "board.yml": {
                **_charts_style(bar=_FAMILY_ALIGN),
                **_rows_of(_series_chart(_STACKED_BAR_ENDPOINTS, "bar")),
            }
        },
    ),
    (
        "area family align, chart edge",
        {
            "board.yml": {
                **_charts_style(area=_FAMILY_ALIGN),
                **_rows_of(_series_chart(_ENDPOINTS, "area")),
            }
        },
    ),
    (
        "meta.yml family align, chart edge",
        {
            "meta.yml": _charts_style(line=_FAMILY_ALIGN),
            "board.yml": _rows_of(_series_chart(_ENDPOINTS)),
        },
    ),
    (
        "meta.yml family align, board family edge",
        {
            "meta.yml": _charts_style(line=_FAMILY_ALIGN),
            "board.yml": {
                **_charts_style(line=_ENDPOINTS),
                **_rows_of(_series_chart()),
            },
        },
    ),
    (
        "meta.yml family edge, board family align",
        {
            "meta.yml": _charts_style(line=_ENDPOINTS),
            "board.yml": {
                **_charts_style(line=_FAMILY_ALIGN),
                **_rows_of(_series_chart()),
            },
        },
    ),
    (
        "extends family align, board family edge",
        {
            "_base.yml": _charts_style(line=_FAMILY_ALIGN),
            "board.yml": {
                "extends": "./_base.yml",
                **_charts_style(line=_ENDPOINTS),
                **_rows_of(_series_chart()),
            },
        },
    ),
    (
        "extends family edge, board family overlay",
        {
            "_base.yml": _charts_style(line=_ENDPOINTS),
            "board.yml": {
                "extends": "./_base.yml",
                **_charts_style(line=_FAMILY_OVERLAY),
                **_rows_of(_series_chart()),
            },
        },
    ),
    (
        "family align, imported file's chart edge",
        {
            "child.yml": _rows_of(_series_chart(_ENDPOINTS)),
            "board.yml": {**_charts_style(line=_FAMILY_ALIGN), "rows": ["./child.yml"]},
        },
    ),
    (
        "family align, imported file's family edge",
        {
            "child.yml": {
                **_charts_style(line=_ENDPOINTS),
                **_rows_of(_series_chart()),
            },
            "board.yml": {**_charts_style(line=_FAMILY_ALIGN), "rows": ["./child.yml"]},
        },
    ),
    (
        "family align, chart ref's edge",
        {
            "shared.yml": _rows_of(_series_chart(_ENDPOINTS)),
            "board.yml": {
                **_charts_style(line=_FAMILY_ALIGN),
                "charts": {"c": "shared.yml.charts.c0"},
                "rows": ["c"],
            },
        },
    ),
    (
        "root family align, nested family edge",
        {
            "board.yml": {
                **_charts_style(line=_FAMILY_ALIGN),
                "charts": {"c": _series_chart()},
                "rows": [{**_charts_style(line=_ENDPOINTS), "rows": ["c"]}],
            }
        },
    ),
    (
        "meta.yml family align, nested family edge",
        {
            "meta.yml": _charts_style(line=_FAMILY_ALIGN),
            "board.yml": {
                "charts": {"c": _series_chart()},
                "rows": [{**_charts_style(line=_ENDPOINTS), "rows": ["c"]}],
            },
        },
    ),
    (
        "nested family align, deeper nested family edge",
        {
            "board.yml": {
                "charts": {"c": _series_chart()},
                "rows": [
                    {
                        **_charts_style(line=_FAMILY_ALIGN),
                        "rows": [{**_charts_style(line=_ENDPOINTS), "rows": ["c"]}],
                    }
                ],
            }
        },
    ),
    (
        "previous grammar: family align, chart visible true",
        {
            "board.yml": {
                **_charts_style(line=_FAMILY_ALIGN),
                **_rows_of(_series_chart(_visible(True))),
            }
        },
    ),
    (
        "previous grammar: family overlay, chart visible true",
        {
            "board.yml": {
                **_charts_style(line=_FAMILY_OVERLAY),
                **_rows_of(_series_chart(_visible(True))),
            }
        },
    ),
]


def _file_text(raw: dict[str, Any] | str) -> str:
    if isinstance(raw, str):
        return raw
    # Only a file that defines charts declares their query.
    return _board_text(raw) if "charts" in raw else yaml.safe_dump(raw, sort_keys=False)


def _compile_project(
    in_memory_project: Callable, files: dict[str, dict[str, Any]] | dict[str, str]
) -> CompileResult:
    project = in_memory_project(
        Path("/tmp/test-endpoint-labels-crossings"),
        {f"charts/{name}": _file_text(raw) for name, raw in files.items()},
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return compile_file(project.path("charts/board.yml").read_board())


@pytest.mark.parametrize(
    "files",
    [files for _, files in _CROSSINGS],
    ids=[name for name, _ in _CROSSINGS],
)
def test_a_family_align_or_overlay_from_another_tier_leaves_endpoints_alone(
    in_memory_project: Callable, files: dict[str, dict[str, Any]]
) -> None:
    result = _compile_project(in_memory_project, files)

    assert result.success, result.errors
    assert result.board is not None
    placed = _placed(result.board)
    assert placed
    assert all(rail and not legend for rail, legend, _edge_ in placed), placed


@pytest.mark.parametrize("file", ["meta.yml", "child.yml"])
@pytest.mark.parametrize(
    ("leaf", "code"),
    [
        (_FAMILY_ALIGN, "ERR-LEGEND-ALIGN-WITH-ENDPOINTS"),
        (_FAMILY_OVERLAY, "ERR-LEGEND-OVERLAY-WITH-ENDPOINTS"),
    ],
    ids=["align", "overlay"],
)
def test_a_family_align_or_overlay_beside_endpoints_in_one_file_is_an_error(
    in_memory_project: Callable, file: str, leaf: dict[str, Any], code: str
) -> None:
    family = {
        "legend": {"position": {"edge": "endpoints", **leaf["legend"]["position"]}}
    }
    styled = _charts_style(line=family)
    files = {
        "meta.yml": {
            "meta.yml": styled,
            "board.yml": _rows_of(_series_chart()),
        },
        "child.yml": {
            "child.yml": {**styled, **_rows_of(_series_chart())},
            "board.yml": {"rows": ["./child.yml"]},
        },
    }[file]

    result = _compile_project(in_memory_project, files)

    assert [e.code for e in result.errors] == [code]


_BLOCK_TOP = _charts_style(block=_edge("top"))
_BLOCK_TOP_FAMILY_FALSE = _charts_style(block=_edge("top"), line=_visible(False))
# Each board turns its chart's labels off beside a block-tier edge, while another
# file's family tier, which outranks any block, would turn them on.
_OPT_OUTS_UNDER_ANOTHER_FILES_FAMILY = [
    (
        "meta.yml family true, chart false",
        {
            "meta.yml": _charts_style(line=_visible(True)),
            "board.yml": {**_BLOCK_TOP, **_rows_of(_series_chart(_visible(False)))},
        },
    ),
    (
        "meta.yml family true, board family false",
        {
            "meta.yml": _charts_style(line=_visible(True)),
            "board.yml": {**_BLOCK_TOP_FAMILY_FALSE, **_rows_of(_series_chart())},
        },
    ),
    (
        "meta.yml family true, nested board family false",
        {
            "meta.yml": _charts_style(line=_visible(True)),
            "board.yml": {
                **_BLOCK_TOP,
                "charts": {"c": _series_chart()},
                "rows": [{**_charts_style(line=_visible(False)), "rows": ["c"]}],
            },
        },
    ),
    (
        "extends family true, chart false",
        {
            "_base.yml": _charts_style(line=_visible(True)),
            "board.yml": {
                "extends": "./_base.yml",
                **_BLOCK_TOP,
                **_rows_of(_series_chart(_visible(False))),
            },
        },
    ),
    (
        "meta.yml family auto, chart false",
        {
            "meta.yml": _charts_style(line=_visible("auto")),
            "board.yml": {**_BLOCK_TOP, **_rows_of(_series_chart(_visible(False)))},
        },
    ),
]


@pytest.mark.parametrize(
    "files",
    [files for _, files in _OPT_OUTS_UNDER_ANOTHER_FILES_FAMILY],
    ids=[name for name, _ in _OPT_OUTS_UNDER_ANOTHER_FILES_FAMILY],
)
def test_a_false_beside_a_block_edge_outlasts_another_files_family(
    in_memory_project: Callable, files: dict[str, dict[str, Any]]
) -> None:
    result = _compile_project(in_memory_project, files)

    assert result.success, result.errors
    assert result.board is not None
    assert _placed(result.board) == [_legend("top")]


# One file migrates alone, so where another file's legend setting now ranks
# differently the result cannot match the previous grammar's. The previous
# result is in each id.
_CROSS_FILE_RANK_CHANGES = [
    (
        "meta family edge bottom, block top, chart false (was bottom)",
        {
            "meta.yml": _charts_style(line=_edge("bottom")),
            "board.yml": {**_BLOCK_TOP, **_rows_of(_series_chart(_visible(False)))},
        },
        [_legend("top")],
    ),
    (
        "meta family edge auto, block top, chart auto (was the rail)",
        {
            "meta.yml": _charts_style(line=_edge("auto")),
            "board.yml": {**_BLOCK_TOP, **_rows_of(_series_chart(_visible("auto")))},
        },
        [_legend("top")],
    ),
    (
        "meta family edge bottom, block top, family false (was bottom)",
        {
            "meta.yml": _charts_style(line=_edge("bottom")),
            "board.yml": {**_BLOCK_TOP_FAMILY_FALSE, **_rows_of(_series_chart())},
        },
        [_legend("top")],
    ),
    (
        "meta block top and family false, chart edge auto (was right)",
        {
            "meta.yml": _BLOCK_TOP_FAMILY_FALSE,
            "board.yml": _rows_of(_series_chart(_edge("auto"))),
        },
        [_RAIL],
    ),
    (
        "meta family false and edge top, board family edge auto (was right)",
        {
            "meta.yml": _charts_style(line={**_visible(False), **_edge("top")}),
            "board.yml": {
                **_charts_style(line=_edge("auto")),
                **_rows_of(_series_chart()),
            },
        },
        [_RAIL],
    ),
    (
        "meta family true and edge top, board family edge auto on a bar",
        {
            "meta.yml": _charts_style(bar={**_visible(True), **_edge("top")}),
            "board.yml": {
                **_charts_style(bar=_edge("auto")),
                **_rows_of(_series_chart(chart_type="bar")),
            },
        },
        None,
    ),
]


@pytest.mark.parametrize(
    ("files", "placed"),
    [(files, placed) for _, files, placed in _CROSS_FILE_RANK_CHANGES],
    ids=[name for name, *_ in _CROSS_FILE_RANK_CHANGES],
)
def test_a_result_another_files_legend_now_ranks_apart_from(
    in_memory_project: Callable,
    files: dict[str, dict[str, Any]],
    placed: list[_Placed] | None,
) -> None:
    result = _compile_project(in_memory_project, files)

    assert result.success, result.errors
    assert result.board is not None
    if placed is not None:
        assert _placed(result.board) == placed


def _visible_paths(node: object, at: tuple[str, ...] = ()) -> list[str]:
    """Every authored `endpoint_labels.visible` under *node*, dotted."""
    if at[-2:] == ("endpoint_labels", "visible"):
        return [".".join(at)]
    if isinstance(node, list):
        return [
            path
            for index, item in enumerate(node)
            for path in _visible_paths(item, (*at, str(index)))
        ]
    if isinstance(node, dict):
        return [
            path
            for key, value in node.items()
            for path in _visible_paths(value, (*at, str(key)))
        ]
    return []


_VISIBLE = ".endpoint_labels.visible"


def _subject(notice: str) -> str:
    """The dotted key a notice is about: its first backticked `path: value`."""
    _, tick, rest = notice.partition("`")
    assert tick, notice
    return rest.partition("`")[0].partition(": ")[0]


def _assert_one_notice_per_migrated_key(
    in_memory_project: Callable, files: dict[str, dict[str, Any]] | dict[str, str]
) -> None:
    result = _compile_project(in_memory_project, files)
    notices = [
        w.message
        for w in result.warnings
        if w.code == "WARN-SCHEMA-MIGRATED" and _subject(w.message).endswith(_VISIBLE)
    ]
    owed = [
        f"charts/{name}: `{path}: "
        for name, raw in files.items()
        for path in _visible_paths(yaml.safe_load(_file_text(raw)))
    ]
    for prefix in owed:
        assert sum(n.startswith(prefix) for n in notices) == 1, (prefix, notices)
    assert len(notices) == len(owed), notices


_LAYERED_TRUE_BESIDE_LEFT = _series_chart({**_visible(True), **_edge("left")})
_LAYERED_TRUE_BESIDE_LEFT["layers"] = [{"type": "line", "y": "v"}]
_META_FALSE = _charts_style(line=_visible(False))
_LAYERED_LEFT = _series_chart(_edge("left"))
_LAYERED_LEFT["layers"] = [{"type": "line", "y": "v"}]
_FAMILY_TRUE_OVER_A_CHART_LEFT = {
    **_charts_style(line=_visible(True)),
    **_rows_of(_LAYERED_LEFT),
}
_FAMILY_TRUE_OVER_A_NESTED_FAMILY_LEFT = {
    **_charts_style(line=_visible(True)),
    "charts": {"c": _series_chart()},
    "rows": [{**_charts_style(line=_edge("left")), "rows": ["c"]}],
}
# The root family pins `x` to the `bottom` it resolved to beside the block, so
# the nested family that also styles `x` has no chart of its own left.
_FAMILY_FALSE_UNDER_A_PINNED_CHART: dict[str, Any] = {
    **_charts_style(line=_FALSE),
    "charts": {"c": _series_chart(), "d": _series_chart(), "x": _series_chart()},
    "rows": [
        "c",
        "d",
        {**_charts_style(block=_SIDE), "rows": ["x"]},
        {**_charts_style(block=_SIDE, line=_FALSE), "rows": ["x"]},
    ],
}
_FAMILY_FALSE_AND_SIDE_UNDER_A_PINNED_CHART: dict[str, Any] = {
    **_FAMILY_FALSE_UNDER_A_PINNED_CHART,
    "rows": [
        *_FAMILY_FALSE_UNDER_A_PINNED_CHART["rows"][:3],
        {**_charts_style(line={**_FALSE, **_SIDE}), "rows": ["x"]},
    ],
}

# Inputs the tests above migrate one file at a time, and shapes where a file's
# migration combines with another file's legend settings.
_MIGRATED_INPUTS: list[tuple[str, dict[str, dict[str, Any]] | dict[str, str]]] = [
    *((f"resolved: {name}", {"board.yml": raw}) for name, raw, _ in _RESOLVED),
    *((f"crossing: {name}", files) for name, files in _CROSSINGS),
    *(
        (f"opt-out: {name}", files)
        for name, files in _OPT_OUTS_UNDER_ANOTHER_FILES_FAMILY
    ),
    *((f"rank: {name}", files) for name, files, _ in _CROSS_FILE_RANK_CHANGES),
    *((f"aliased: {name}", {"board.yml": text}) for name, text in _ALIASED),
    *(
        (
            f"old spelling {index}",
            {"board.yml": _BOARD % (_CHART + f"    style:\n      {old}")},
        )
        for index, (old, _new) in enumerate(_OLD_VS_NEW)
    ),
    *(
        (f"dated: {name}", {"board.yml": text})
        for name, text in [
            ("family false", _DATED % (_FAMILY_FALSE_TEXT, "")),
            ("chart false", _DATED % ("", _CHART_FALSE_TEXT)),
        ]
    ),
    *(
        (
            f"coerced {visible}",
            {
                "board.yml": _BOARD
                % (
                    _CHART
                    + f"    style:\n      endpoint_labels:\n        visible: {visible}\n"
                )
            },
        )
        for visible in ['"false"', "off", "0", "yes", "1"]
    ),
    (
        "false beside edge endpoints",
        {"board.yml": _rows_of(_series_chart({**_visible(False), **_ENDPOINTS}))},
    ),
    (
        "false beside a side, block side",
        {
            "board.yml": {
                "style": {"charts": _SIDE},
                **_rows_of(_series_chart({**_FALSE, **_edge("left")})),
            }
        },
    ),
    ("layered true beside left", {"board.yml": _rows_of(_LAYERED_TRUE_BESIDE_LEFT)}),
    ("true beside align", {"board.yml": {"rows": [_series_chart(_ALIGNED_TRUE)]}}),
    (
        "family false beside the block's side",
        {
            "board.yml": {
                "style": {
                    "charts": {**_SIDE, "line": _FALSE, "area": _FALSE, "bar": _FALSE}
                },
                "text": "x",
            }
        },
    ),
    (
        "lone family false",
        {"board.yml": {"style": {"charts": {"line": _FALSE}}, "text": "x"}},
    ),
    (
        "analytics meta.yml",
        {
            "meta.yml": yaml.safe_load(_ANALYTICS_META),
            "board.yml": _rows_of(_series_chart()),
        },
    ),
    (
        "meta false, chart auto",
        {
            "meta.yml": _META_FALSE,
            "board.yml": _rows_of(_series_chart(_visible("auto"))),
        },
    ),
    (
        "meta false, chart edge auto",
        {"meta.yml": _META_FALSE, "board.yml": _rows_of(_series_chart(_edge("auto")))},
    ),
    (
        "meta false, block edge left",
        {
            "meta.yml": _META_FALSE,
            "board.yml": {
                **_charts_style(block=_edge("left")),
                **_rows_of(_series_chart()),
            },
        },
    ),
    (
        "meta bar true, board chart edge auto",
        {
            "meta.yml": _charts_style(bar=_visible(True)),
            "board.yml": _rows_of(_series_chart(_edge("auto"), chart_type="bar")),
        },
    ),
    (
        "meta bar true, board bar edge auto",
        {
            "meta.yml": _charts_style(bar=_visible(True)),
            "board.yml": {
                **_charts_style(bar=_edge("auto")),
                **_rows_of(_series_chart(chart_type="bar")),
            },
        },
    ),
    (
        "chart false beside edge auto",
        {"board.yml": _rows_of(_series_chart({**_FALSE, **_edge("auto")}))},
    ),
    (
        "meta family bottom, block auto, chart false",
        {
            "meta.yml": _charts_style(line=_edge("bottom")),
            "board.yml": {
                **_charts_style(block=_edge("auto")),
                **_rows_of(_series_chart(_FALSE)),
            },
        },
    ),
    (
        "nested family false struck under a root family top",
        {
            "board.yml": {
                **_charts_style(line=_edge("top")),
                "rows": [{**_charts_style(line=_FALSE), "rows": ["./child.yml"]}],
            },
            "child.yml": _rows_of(_series_chart(_edge("auto"))),
        },
    ),
    (
        "mixed chains under a root family false",
        {
            "meta.yml": _charts_style(line=_edge("right")),
            "board.yml": {
                **_charts_style(line=_FALSE),
                "charts": {
                    "c": _series_chart(),
                    "d": _series_chart(),
                    "e": _series_chart(),
                },
                "rows": [
                    "c",
                    {**_charts_style(block=_edge("bottom")), "rows": ["d", "e"]},
                ],
            },
        },
    ),
    ("family true over a chart's side", {"board.yml": _FAMILY_TRUE_OVER_A_CHART_LEFT}),
    (
        "family true over a nested family's side",
        {"board.yml": _FAMILY_TRUE_OVER_A_NESTED_FAMILY_LEFT},
    ),
    (
        "family false under a pinned chart",
        {"board.yml": _FAMILY_FALSE_UNDER_A_PINNED_CHART},
    ),
    (
        "family false and side under a pinned chart",
        {"board.yml": _FAMILY_FALSE_AND_SIDE_UNDER_A_PINNED_CHART},
    ),
]


_LAYERED_LEAVES = (
    "a legend shown beside the labels (as on a layered chart) no longer stays at "
)

_BECAME: list[tuple[str, dict[str, Any], str, str]] = [
    (
        "struck, inherits",
        {
            **_charts_style(line=_edge("top")),
            "charts": {"c": _series_chart()},
            "rows": [{**_charts_style(line=_FALSE), "rows": ["c"]}],
        },
        "rows.0.style.charts.line.endpoint_labels.visible: false",
        "was struck; the charts it styles inherit `legend.position.edge: top`",
    ),
    (
        "is now",
        _rows_of(_series_chart(_FALSE)),
        "charts.c0.style.endpoint_labels.visible: false",
        "is now `legend.position.edge: top`",
    ),
    (
        "is now, beside a side",
        _rows_of(_LAYERED_TRUE_BESIDE_LEFT),
        "charts.c0.style.endpoint_labels.visible: true",
        f"is now `legend.position.edge: endpoints`; {_LAYERED_LEAVES}`left`",
    ),
    (
        "false beside endpoints",
        _rows_of(_series_chart({**_FALSE, **_ENDPOINTS})),
        "charts.c0.style.endpoint_labels.visible: false",
        "is now `legend.position.edge: top`",
    ),
    (
        "not live, struck",
        _FAMILY_FALSE_UNDER_A_PINNED_CHART,
        "rows.3.style.charts.line.endpoint_labels.visible: false",
        "was struck; the charts it styles hold their own `legend.position.edge`",
    ),
    (
        "not live, kept",
        _FAMILY_FALSE_AND_SIDE_UNDER_A_PINNED_CHART,
        "rows.3.style.charts.line.endpoint_labels.visible: false",
        "is now `legend.position.edge: bottom`",
    ),
    (
        "a chart's side under a family true",
        _FAMILY_TRUE_OVER_A_CHART_LEFT,
        "charts.c0.style.legend.position.edge: left",
        "is now `legend.position.edge: endpoints`, as "
        f"`style.charts.line.endpoint_labels.visible: true` applies; {_LAYERED_LEAVES}"
        "`left`",
    ),
    (
        "a nested family's side under a family true",
        _FAMILY_TRUE_OVER_A_NESTED_FAMILY_LEFT,
        "rows.0.style.charts.line.legend.position.edge: left",
        "is now `legend.position.edge: endpoints`, as "
        f"`style.charts.line.endpoint_labels.visible: true` applies; {_LAYERED_LEAVES}"
        "`left`",
    ),
    (
        "in tabs, beside a side",
        {"tabs": {"items": [{"title": "a", "rows": [_LAYERED_TRUE_BESIDE_LEFT]}]}},
        "tabs.items.0.rows.0.style.endpoint_labels.visible: true",
        f"is now `legend.position.edge: endpoints`; {_LAYERED_LEAVES}`left`",
    ),
    (
        "in tabs, a chart's side under a family true",
        {
            **_charts_style(line=_visible(True)),
            "tabs": {"items": [{"title": "a", "rows": [_LAYERED_LEFT]}]},
        },
        "tabs.items.0.rows.0.style.legend.position.edge: left",
        "is now `legend.position.edge: endpoints`, as "
        f"`style.charts.line.endpoint_labels.visible: true` applies; {_LAYERED_LEAVES}"
        "`left`",
    ),
]


@pytest.mark.parametrize(
    ("raw", "subject", "became"),
    [case[1:] for case in _BECAME],
    ids=[case[0] for case in _BECAME],
)
def test_a_notice_names_what_the_key_became(
    raw: dict[str, Any], subject: str, became: str
) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SchemaMigrationWarning)
        prepare_board_mapping(raw)

    about = [
        str(w.message)
        for w in caught
        if str(w.message).startswith(f"`{subject.partition(': ')[0]}: ")
    ]
    assert about == [
        f"`{subject}` {became}. Legend settings in other files (meta.yml, "
        "`extends:`, a board this file styles) can change how this combines, so "
        "check the charts it styles."
    ]


@pytest.mark.parametrize(
    "files",
    [files for _, files in _MIGRATED_INPUTS],
    ids=[name for name, _ in _MIGRATED_INPUTS],
)
def test_every_migrated_visible_gets_one_notice_naming_it(
    in_memory_project: Callable, files: dict[str, dict[str, Any]] | dict[str, str]
) -> None:
    _assert_one_notice_per_migrated_key(in_memory_project, files)
