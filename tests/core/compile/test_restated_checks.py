"""Table-driven cases for the three WARN-RESTATED-* checks."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from dbt_charts.agent_api.cache import open_cache
from dbt_charts.agent_api.project_session import ProjectSession
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.diagnostics import Diagnostic

_DATA = """
queries:
  q:
    type: values
    rows:
      - {{genre: Action, year: 1990, units: 10, avg_mpg: 3.5}}
      - {{genre: Comedy, year: 1991, units: 7, avg_mpg: 4.5}}
    formats:
      units: integer
"""


def _restated(
    tmp_path: Path,
    board: str,
    meta: str | None = None,
    *,
    query_formats: bool = False,
) -> list[Diagnostic]:
    reset_config()
    (tmp_path / "dbt_charts.yml").write_text("", encoding="utf-8")
    (tmp_path / "charts").mkdir(exist_ok=True)
    if meta is not None:
        (tmp_path / "charts/meta.yml").write_text(dedent(meta), encoding="utf-8")
    (tmp_path / "charts/board.yml").write_text(dedent(board), encoding="utf-8")
    cache = open_cache(None)
    with ProjectSession.open(str(tmp_path), cache=cache) as session:
        result = session.render_board(
            session.project.path("charts/board.yml").read_board(),
            format="svg",
            use_cache=False,
            restated_check=True,
        )
    cache.close()
    assert result.status != "error", (result.validation_errors, result.board_error)
    return [
        w
        for w in result.warnings
        if w.code.startswith("WARN-RESTATED-")
        and (query_formats or not w.path.startswith("queries."))
    ]


def _chart_board(chart: str, style: str = "") -> str:
    return _DATA.format() + f"charts:\n  c:\n{chart}\nrows:\n  - c\n" + style


def _summary(warnings: list[Diagnostic]) -> list[tuple[str, str | None]]:
    return sorted((w.code, w.path) for w in warnings)


_STYLE_CASES = [
    # (id, board style block, meta, expected (code, path) pairs)
    (
        "theme leaf equal",
        "style:\n  frame:\n    card_gap: 8\n",
        None,
        [("WARN-RESTATED-STYLE", "style.frame.card_gap")],
    ),
    ("theme leaf differs", "style:\n  frame:\n    card_gap: 12\n", None, []),
    ("near-equal number", "style:\n  frame:\n    card_gap: 8.01\n", None, []),
    (
        "int vs float spelling is equal",
        "style:\n  frame:\n    max_width: 1200\n",
        None,
        [("WARN-RESTATED-STYLE", "style.frame.max_width")],
    ),
    (
        "charts tier leaf equal",
        "style:\n  charts:\n    default_chart_height: 300\n",
        None,
        [("WARN-RESTATED-STYLE", "style.charts.default_chart_height")],
    ),
    (
        "chart-type tier leaf equal",
        "style:\n  charts:\n    table:\n      pagination:\n        page_rows: 20\n",
        None,
        [("WARN-RESTATED-STYLE", "style.charts.table.pagination.page_rows")],
    ),
    (
        "meta equal",
        "style:\n  frame:\n    card_gap: 12\n",
        "style:\n  frame:\n    card_gap: 12\n",
        [("WARN-RESTATED-STYLE", "style.frame.card_gap")],
    ),
    (
        "override of meta back to the theme value",
        "style:\n  frame:\n    card_gap: 8\n",
        "style:\n  frame:\n    card_gap: 12\n",
        [],
    ),
    ("default theme", "extends: clarity\n", None, [("WARN-RESTATED-STYLE", "extends")]),
    ("other theme", "extends: paper\n", None, []),
    (
        "theme equal to the meta theme",
        "extends: paper\n",
        "extends: paper\n",
        [("WARN-RESTATED-STYLE", "extends")],
    ),
    (
        "clarity under a paper meta is a switch",
        "extends: clarity\n",
        "extends: paper\n",
        [],
    ),
    (
        "theme switch changes what is inherited",
        "extends: paper\nstyle:\n  background: dbt-grays.white\n",
        None,
        [],
    ),
    (
        "token equal to the theme token",
        "style:\n  background: dbt-grays.white\n",
        None,
        [("WARN-RESTATED-STYLE", "style.background")],
    ),
    ("hex is not its own token", "style:\n  background: '#ffffff'\n", None, []),
    (
        "list equal as a whole",
        "style:\n  charts:\n    color:\n      categorical:\n"
        "        single_series_palette: [category.blue.dark]\n",
        None,
        [
            (
                "WARN-RESTATED-STYLE",
                "style.charts.color.categorical.single_series_palette",
            )
        ],
    ),
    (
        "list that differs",
        "style:\n  charts:\n    color:\n      categorical:\n"
        "        single_series_palette: [category.blue.dark, category.red.dark]\n",
        None,
        [],
    ),
    (
        "partial map: only the equal key warns",
        "style:\n  frame:\n    card_gap: 8\n    margin: 20\n",
        None,
        [("WARN-RESTATED-STYLE", "style.frame.card_gap")],
    ),
]


@pytest.mark.parametrize(
    ("board_style", "meta", "expected"),
    [pytest.param(b, m, e, id=i) for i, b, m, e in _STYLE_CASES],
)
def test_style_cascade(
    tmp_path: Path, board_style: str, meta: str | None, expected: list[tuple[str, str]]
) -> None:
    board = board_style + "rows:\n  - text: hi\n"
    assert _summary(_restated(tmp_path, board, meta)) == sorted(expected)


def test_style_message_names_where_the_value_comes_from(tmp_path: Path) -> None:
    (warning,) = _restated(
        tmp_path,
        "style:\n  frame:\n    card_gap: 12\nrows:\n  - text: hi\n",
        "style:\n  frame:\n    card_gap: 12\n",
    )
    assert warning.message == (
        "`style.frame.card_gap: 12` is already set in `charts/meta.yml`; remove it."
    )


_NESTED = """\
style:
  font:
    color: {parent}
rows:
  - style:
      font:
        color: {child}
    rows:
      - text: hi
"""


@pytest.mark.parametrize(
    ("parent", "child", "warns"),
    [
        pytest.param("'#fafafa'", "'#fafafa'", True, id="nested restates its parent"),
        pytest.param(
            "'#fafafa'", "'#111111'", False, id="nested resets light under dark"
        ),
        pytest.param(
            "'#fafafa'",
            "dbt-grays.ink",
            False,
            id="reset to the theme token under a changed parent",
        ),
    ],
)
def test_nested_board_is_judged_against_its_parent(
    tmp_path: Path, parent: str, child: str, warns: bool
) -> None:
    warnings = _restated(tmp_path, _NESTED.format(parent=parent, child=child))
    nested = [w for w in warnings if w.path == "rows.0.style.font.color"]
    assert bool(nested) is warns
    if warns:
        assert nested[0].code == "WARN-RESTATED-STYLE"
        assert "parent" in nested[0].message


def test_nested_background_is_never_inherited(tmp_path: Path) -> None:
    board = """\
style:
  background: '#101010'
rows:
  - style:
      background: '#101010'
    rows:
      - text: hi
"""
    assert _restated(tmp_path, board) == []


_CHART_CASES = [
    (
        "table tier equal",
        "style:\n  charts:\n    table:\n      pagination:\n        page_rows: 5\n",
        "    type: table\n    query: q\n    style:\n      pagination:\n        page_rows: 5\n",
        [("WARN-RESTATED-STYLE", "charts.c.style.pagination.page_rows")],
    ),
    (
        "table tier overridden",
        "style:\n  charts:\n    table:\n      pagination:\n        page_rows: 5\n",
        "    type: table\n    query: q\n    style:\n      pagination:\n        page_rows: 9\n",
        [],
    ),
    (
        "kpi value format equals the query format",
        "",
        "    type: kpi\n    query: q\n    value: units\n    style:\n      value:\n        format: integer\n",
        [("WARN-RESTATED-FORMAT", "charts.c.style.value.format")],
    ),
    (
        "kpi value format overrides the query format",
        "",
        "    type: kpi\n    query: q\n    value: units\n    style:\n      value:\n        format: percent\n",
        [],
    ),
    (
        "column without a query format is not restated by one",
        "",
        "    type: table\n    query: q\n    style:\n      columns:\n        avg_mpg:\n          format: integer\n",
        [],
    ),
    (
        "table column format equals the query format",
        "",
        "    type: table\n    query: q\n    style:\n      columns:\n        units:\n          format: integer\n",
        [("WARN-RESTATED-FORMAT", "charts.c.style.columns.units.format")],
    ),
    (
        "header equal to the derived text",
        "",
        "    type: table\n    query: q\n    style:\n      columns:\n        avg_mpg:\n          label: Avg MPG\n",
        [("WARN-RESTATED-INFERRED", "charts.c.style.columns.avg_mpg.label")],
    ),
    (
        "header that only re-cases the derived text is kept",
        "",
        "    type: table\n    query: q\n    style:\n      columns:\n        avg_mpg:\n          label: Avg mpg\n",
        [],
    ),
    (
        "header with different words",
        "",
        "    type: table\n    query: q\n    style:\n      columns:\n        avg_mpg:\n          label: Miles per gallon\n",
        [],
    ),
]


@pytest.mark.parametrize(
    ("board_style", "chart", "expected"),
    [pytest.param(s, c, e, id=i) for i, s, c, e in _CHART_CASES],
)
def test_chart_level_keys(
    tmp_path: Path, board_style: str, chart: str, expected: list[tuple[str, str]]
) -> None:
    board = _chart_board(chart, "").replace(
        _DATA.format(), _DATA.format() + board_style, 1
    )
    assert _summary(_restated(tmp_path, board)) == sorted(expected)


_VARIABLE = """\
variables:
  region:
{fields}
rows:
  - text: hi
"""


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        pytest.param(
            "    label: Region\n",
            ["variables.region.label"],
            id="label equals inferred",
        ),
        pytest.param("    label: Sales region\n", [], id="label differs"),
        pytest.param("    label: region\n", [], id="label re-cased"),
        pytest.param(
            "    input: select\n    options:\n      static: [a, b]\n",
            ["variables.region.input"],
            id="select inferred from options",
        ),
        pytest.param(
            "    input: radio\n    options:\n      static: [a, b]\n",
            [],
            id="radio is a choice",
        ),
        pytest.param(
            "    input: select\n    options:\n      static: [yes, no]\n",
            [],
            id="refined to a checkbox by data",
        ),
        pytest.param(
            "    input: select\n    options:\n      static: [1, 2, 3]\n",
            [],
            id="refined to a slider by data",
        ),
        pytest.param(
            "    input: select\n    options:\n      query: q\n",
            ["variables.region.input"],
            id="options query: judged at render time, after the options load",
        ),
        pytest.param(
            "    input: text\n",
            ["variables.region.input"],
            id="text inferred without signals",
        ),
        pytest.param("    input: auto\n", [], id="auto is a request, not a value"),
        pytest.param(
            "    input: checkbox\n    default: true\n",
            ["variables.region.input"],
            id="checkbox inferred from bool",
        ),
        pytest.param(
            "    required: false\n", ["variables.region.required"], id="schema default"
        ),
        pytest.param("    required: true\n", [], id="schema default overridden"),
    ],
)
def test_variable_inference(tmp_path: Path, fields: str, expected: list[str]) -> None:
    board = _DATA.format() + _VARIABLE.format(fields=fields)
    warnings = _restated(tmp_path, board)
    assert sorted(w.path for w in warnings) == sorted(expected)
    assert {w.code for w in warnings} <= {"WARN-RESTATED-INFERRED"}


_SOURCE = """\
source: warehouse
queries:
  q:
{query_source}    sql: select 1 as n
rows:
  - {nested}
"""


@pytest.mark.parametrize(
    ("query_source", "expected"),
    [
        pytest.param(
            "    source: warehouse\n",
            ["queries.q.source"],
            id="query repeats the board source",
        ),
        pytest.param("    source: other\n", [], id="query names another source"),
        pytest.param("", [], id="query inherits"),
    ],
)
def test_query_source(tmp_path: Path, query_source: str, expected: list[str]) -> None:
    board = _SOURCE.format(query_source=query_source, nested="text: hi")
    warnings = _restated_compile_only(tmp_path, board)
    assert sorted(w.path for w in warnings) == sorted(expected)
    assert {w.code for w in warnings} <= {"WARN-RESTATED-INFERRED"}


def _restated_compile_only(tmp_path: Path, board: str) -> list[Diagnostic]:
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.compile import compile_file

    reset_config()
    (tmp_path / "charts").mkdir(exist_ok=True)
    (tmp_path / "charts/board.yml").write_text(dedent(board), encoding="utf-8")
    project = FilesystemProject(tmp_path)
    result = compile_file(
        project.path("charts/board.yml").read_board(), apply_meta=True
    )
    return [w for w in result.warnings if w.code.startswith("WARN-RESTATED-")]


def test_nested_board_source(tmp_path: Path) -> None:
    board = """\
source: warehouse
rows:
  - source: warehouse
    rows:
      - text: hi
  - source: elsewhere
    rows:
      - text: hi
"""
    paths = [w.path for w in _restated_compile_only(tmp_path, board)]
    assert paths == ["rows.0.source"]


_BAR = (
    "    type: bar\n    query: q\n    x: {x}\n    y: units\n"
    "    style:\n      orientation: {orientation}\n"
)


@pytest.mark.parametrize(
    ("x", "orientation", "warns"),
    [
        pytest.param(
            "genre", "horizontal", True, id="category x: horizontal is what auto picks"
        ),
        pytest.param(
            "genre", "vertical", False, id="category x: vertical overrides auto"
        ),
        pytest.param(
            "year", "vertical", True, id="numeric x: vertical is what auto picks"
        ),
        pytest.param(
            "year", "horizontal", False, id="numeric x: horizontal overrides auto"
        ),
    ],
)
def test_bar_orientation_is_compared_with_this_charts_data(
    tmp_path: Path, x: str, orientation: str, warns: bool
) -> None:
    warnings = _restated(
        tmp_path, _chart_board(_BAR.format(x=x, orientation=orientation))
    )
    hits = [w for w in warnings if w.path == "charts.c.style.orientation"]
    assert bool(hits) is warns
    if warns:
        assert hits[0].code == "WARN-RESTATED-INFERRED"
        assert "what the renderer picks" in hits[0].message


@pytest.mark.parametrize("stack", ["none", "stacked"])
def test_stack_is_never_judged(tmp_path: Path, stack: str) -> None:
    """The normalizer promotes `stack` onto the chart (and auto may stack a
    colored bar), so dropping it from `style` proves nothing."""
    chart = (
        "    type: bar\n    query: q\n    x: genre\n    y: units\n    color: genre\n"
        f"    style:\n      stack: {stack}\n"
    )
    paths = [w.path for w in _restated(tmp_path, _chart_board(chart))]
    assert "charts.c.style.stack" not in paths


def test_render_time_check_skips_keys_the_compile_check_reported(
    tmp_path: Path,
) -> None:
    chart = "    type: kpi\n    query: q\n    value: units\n    style:\n      value:\n        format: integer\n"
    warnings = _restated(tmp_path, _chart_board(chart))
    assert [w.code for w in warnings] == ["WARN-RESTATED-FORMAT"]


def test_removal_that_makes_the_chart_invalid_means_the_key_matters(
    tmp_path: Path,
) -> None:
    """Dropping `align` leaves an unsupported legend placement; it is not restated."""
    chart = (
        "    type: bar\n    query: q\n    x: genre\n    y: units\n    color: genre\n"
        "    style:\n      legend:\n        position:\n          edge: left\n"
        "          align: start\n          overlay: true\n"
    )
    assert _restated(tmp_path, _chart_board(chart)) == []


_QUERY_VARIABLE = """\
queries:
  opts:
    type: values
    rows:
      - {{v: a}}
      - {{v: b}}
  q:
    type: values
    rows:
      - {{n: 1}}
variables:
  region:
{fields}
charts:
  c:
    type: kpi
    query: q
    value: n
rows:
  - c
"""


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        pytest.param(
            "    input: select\n    options:\n      query: opts\n",
            ["variables.region.input"],
            id="select over an options query",
        ),
        pytest.param(
            "    input: select\n    options:\n      query: opts\n      column: v\n",
            ["variables.region.input"],
            id="select over a column",
        ),
        pytest.param(
            "    input: radio\n    options:\n      query: opts\n",
            [],
            id="radio differs",
        ),
        pytest.param(
            "    input: select\n    options:\n      query: opts\n    default: a\n",
            ["variables.region.input"],
            id="a default that changes what is drawn is kept",
        ),
        pytest.param(
            "    input: text\n    placeholder: Type here\n",
            ["variables.region.input"],
            id="text input; the placeholder is drawn, so it is kept",
        ),
    ],
)
def test_variable_settings_judged_after_the_options_load(
    tmp_path: Path, fields: str, expected: list[str]
) -> None:
    warnings = _restated(tmp_path, _QUERY_VARIABLE.format(fields=fields))
    assert sorted(w.path for w in warnings) == sorted(expected)


@pytest.mark.parametrize(
    ("data_type", "options", "warns"),
    [
        pytest.param("number", "[1, 2, 3]", True, id="numbers infer number"),
        pytest.param("string", "[a, b]", True, id="strings infer string"),
        pytest.param(
            "number", "[a, b]", False, id="number over strings changes typing"
        ),
        pytest.param(
            "string", "[1, 2, 3]", False, id="string over numbers changes typing"
        ),
    ],
)
def test_variable_data_type(
    tmp_path: Path, data_type: str, options: str, warns: bool
) -> None:
    fields = f"    input: select\n    data_type: {data_type}\n    options:\n      static: {options}\n"
    paths = [
        w.path
        for w in _restated_compile_only(
            tmp_path, _DATA.format() + _VARIABLE.format(fields=fields)
        )
    ]
    assert ("variables.region.data_type" in paths) is warns


_PLAIN_BAR = "    type: bar\n    query: q\n    x: genre\n    y: units\n{extra}"


@pytest.mark.parametrize(
    ("extra", "path", "warns"),
    [
        pytest.param(
            "    field_labels:\n      genre: Genre\n",
            "charts.c.field_labels.genre",
            True,
            id="field label equal to the derived text",
        ),
        pytest.param(
            "    field_labels:\n      genre: Kind of film\n",
            "charts.c.field_labels.genre",
            False,
            id="field label with other words",
        ),
        pytest.param(
            "    field_labels:\n      genre: genre\n",
            "charts.c.field_labels.genre",
            False,
            id="field label that only re-cases the derived text is kept",
        ),
        pytest.param(
            "    sort:\n      by: units\n      order: asc\n",
            "charts.c.sort.order",
            True,
            id="sort order equal to the default",
        ),
        pytest.param(
            "    sort:\n      by: units\n      order: desc\n",
            "charts.c.sort.order",
            False,
            id="sort order changed",
        ),
        pytest.param(
            "    x_label: Genre\n",
            "charts.c.x_label",
            False,
            id="an axis label turns the axis title on, so it is kept",
        ),
        pytest.param(
            "    height: 300\n",
            "charts.c.height",
            False,
            id="height is sized by the layout, never judged here",
        ),
    ],
)
def test_chart_fields_outside_style(
    tmp_path: Path, extra: str, path: str, warns: bool
) -> None:
    paths = [
        w.path
        for w in _restated(tmp_path, _chart_board(_PLAIN_BAR.format(extra=extra)))
    ]
    assert (path in paths) is warns


def test_schema_default_never_names_a_union_tag(tmp_path: Path) -> None:
    board = (
        _DATA.format().replace("type: values\n", "type: values\n")
        + "rows:\n  - text: hi\n"
    )
    assert not any(
        w.path and w.path.endswith(".type")
        for w in _restated_compile_only(tmp_path, board)
    )


def test_schema_default_walks_any_authored_model(tmp_path: Path) -> None:
    chart = "    type: bar\n    query: q\n    x: genre\n    y: units\n    sort:\n      by: units\n      order: asc\n"
    paths = [w.path for w in _restated_compile_only(tmp_path, _chart_board(chart))]
    assert "charts.c.sort.order" in paths


def test_slider_step_is_not_judged_by_the_control_alone(tmp_path: Path) -> None:
    """min/max/step also feed validation and sizing, so a drawn-the-same control
    does not prove the key redundant."""
    fields = "    input: select\n    options:\n      query: opts\n    step: 1\n"
    warnings = _restated(tmp_path, _QUERY_VARIABLE.format(fields=fields))
    assert "variables.region.step" not in [w.path for w in warnings]


def test_grid_columns_24_is_the_default(tmp_path: Path) -> None:
    board = _DATA.format() + (
        "charts:\n  c:\n    type: bar\n    query: q\n    x: genre\n    y: units\n"
        "grid:\n  columns: 24\n  items:\n    - item: c\n      col: 0\n      row: 0\n"
        "      col_span: 24\n"
    )
    assert "grid.columns" in [w.path for w in _restated(tmp_path, board)]


def test_callout_chart_records_the_fields_the_author_wrote() -> None:
    from dbt_charts.core.compile import compile

    result = compile(
        "charts:\n  c:\n    type: callout\n    message: hi\nrows:\n  - c\n"
    )

    assert result.success, result.errors
    assert result.board.charts["c"].authored_fields == {"type", "message"}


def test_integer_format_on_an_integer_table_column_is_restated(
    tmp_path: Path,
) -> None:
    chart = (
        "    type: table\n    query: q\n    style:\n      columns:\n"
        "        units:\n          format: integer\n"
    )
    board = _chart_board(chart).replace("    formats:\n      units: integer\n", "")
    hits = _restated(tmp_path, board)
    assert ("WARN-RESTATED-FORMAT", "charts.c.style.columns.units.format") in (
        _summary(hits)
    )


@pytest.mark.parametrize(
    ("declared", "warns"),
    [("integer", True), ("year", False), ("number_full", False)],
)
def test_query_format_equal_to_the_column_type_default_is_restated(
    tmp_path: Path, declared: str, warns: bool
) -> None:
    board = _chart_board("    type: table\n    query: q")
    board = board.replace("units: integer", f"units: {declared}")
    paths = [w.path for w in _restated(tmp_path, board, query_formats=True)]
    assert ("queries.q.formats.units" in paths) is warns


def test_query_format_read_by_a_kpi_is_not_restated(tmp_path: Path) -> None:
    """A KPI compacts its default, so `integer` is a real choice there."""
    board = _chart_board("    type: kpi\n    query: q\n    value: units")
    paths = [w.path for w in _restated(tmp_path, board, query_formats=True)]
    assert "queries.q.formats.units" not in paths


def test_a_valid_old_grammar_variable_does_not_crash_the_check() -> None:
    from dbt_charts.core.compile import compile

    result = compile(
        "variables:\n  region:\n    description: old prose\n    input: select\n"
        "    options:\n      static: [a, b]\nrows:\n  - text: hi\n"
    )

    assert result.errors == []
    assert [w.path for w in result.warnings if w.code.startswith("WARN-RESTATED-")] == [
        "variables.region.input"
    ]


def test_a_cascaded_value_no_layer_sets_is_not_restated() -> None:
    from dbt_charts.core.compile.merge import resolve_built_in_theme
    from dbt_charts.core.compile.models.board.patch import BOARD_PATCH_ADAPTER
    from dbt_charts.core.compile.restated import cascade_restated

    def table_spark(spark: dict[str, object]):  # noqa: ANN202
        return BOARD_PATCH_ADAPTER.validate_python(
            {"style": {"charts": {"table": {"columns": {"t": {"spark": spark}}}}}}
        )

    layers = [
        ("theme", "theme default", resolve_built_in_theme("clarity")),
        ("meta", "inherited", table_spark({"negative_color": True})),
    ]

    assert cascade_restated(table_spark({"type": "line"}), layers) == []


def test_a_style_leaf_equal_to_a_model_default_but_not_the_theme_is_kept(
    tmp_path: Path,
) -> None:
    board = _chart_board(
        "    type: kpi\n    query: q\n    value: units\n",
        "style:\n  charts:\n    kpi:\n      content_padding:\n        top: 0\n",
    )

    assert _restated(tmp_path, board) == []


@pytest.mark.parametrize(("label", "warns"), [("Region", False), ("REGION", True)])
def test_variable_label_is_compared_in_the_label_fonts_case(
    tmp_path: Path, label: str, warns: bool
) -> None:
    board = (
        "style:\n  variables:\n    label:\n      font:\n        case: upper\n"
        f"variables:\n  region:\n    label: {label}\n    options:\n"
        "      static: [a, b]\nrows:\n  - text: hi\n"
    )

    paths = [w.path for w in _restated(tmp_path, board)]

    assert ("variables.region.label" in paths) is warns


@pytest.mark.parametrize(
    ("chart", "expected"),
    [
        pytest.param(
            "    type: bar\n    query: q\n    x: genre\n    y: units\n"
            "    style:\n      axis_y:\n        labels:\n          format: integer\n",
            [("WARN-RESTATED-FORMAT", "charts.c.style.axis_y.labels.format")],
            id="y axis format equal to the query's number format",
        ),
        pytest.param(
            "    type: bar\n    query: q\n    x: genre\n    y: units\n"
            "    style:\n      axis_y:\n        labels:\n          format: percent\n",
            [],
            id="y axis format differs",
        ),
        pytest.param(
            "    type: bar\n    query: q\n    x: year\n    y: units\n"
            "    style:\n      axis_x:\n        labels:\n          format: integer\n",
            [],
            id="x axis is not a column the query formats",
        ),
    ],
)
def test_axis_label_formats_follow_the_query_format(
    tmp_path: Path, chart: str, expected: list[tuple[str, str]]
) -> None:
    assert _summary(_restated(tmp_path, _chart_board(chart))) == expected


def test_dropping_one_dict_entry_leaf_leaves_its_siblings_authored() -> None:
    from dbt_charts.core.compile.models.style.authored.table import TableColumnConfig
    from dbt_charts.core.compile.restated import _drop_leaf

    columns = {"a": TableColumnConfig(label="A"), "b": TableColumnConfig(label="B")}

    dropped = _drop_leaf(columns, ["a", "label"])

    assert "label" not in dropped["a"].model_fields_set
    assert dropped["b"].label == "B" and "label" in dropped["b"].model_fields_set


def test_normalizing_twice_does_not_read_an_inferred_input_as_authored() -> None:
    from dbt_charts.core.compile.normalize.dispatch import normalize_board
    from dbt_charts.core.compile.parse.parser import parse_yaml

    board = parse_yaml("variables:\n  region: {}\nrows:\n  - text: hi\n")
    first = normalize_board(board)
    assert first.variables["region"].authored_fields == frozenset()

    again = normalize_board(board)

    assert again.variables["region"].authored_fields == frozenset()


def test_the_render_time_check_runs_only_when_asked(tmp_path: Path) -> None:
    board = _chart_board(_BAR.format(x="genre", orientation="horizontal"))
    (tmp_path / "dbt_charts.yml").write_text("", encoding="utf-8")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/board.yml").write_text(board, encoding="utf-8")
    cache = open_cache(None)
    with ProjectSession.open(str(tmp_path), cache=cache) as session:
        result = session.render_board(
            session.project.path("charts/board.yml").read_board(),
            format="svg",
            use_cache=False,
        )
    cache.close()

    assert result.status != "error"
    assert not [w for w in result.warnings if w.code.startswith("WARN-RESTATED-")]


def test_render_time_variable_warnings_carry_the_nested_boards_path(
    tmp_path: Path,
) -> None:
    board = """\
queries:
  opts:
    type: values
    rows:
      - {v: a}
      - {v: b}
rows:
  - cols:
      - variables:
          region:
            input: select
            options:
              query: opts
        rows:
          - text: hi
"""

    assert [w.path for w in _restated(tmp_path, board)] == [
        "rows.0.cols.0.variables.region.input"
    ]


@pytest.mark.parametrize(
    "nesting",
    [
        "rows:\n  - html_policy: {policy}\n    text: <b>nested</b>\n",
        "rows:\n  - rows:\n      - html_policy: {policy}\n        text: <b>deep</b>\n",
    ],
    ids=["nested", "grandchild"],
)
@pytest.mark.parametrize(("policy", "warns"), [("none", False), ("trusted-raw", True)])
def test_nested_html_policy_is_judged_against_the_inherited_one(
    tmp_path: Path, nesting: str, policy: str, warns: bool
) -> None:
    """An inline nested board inherits the parent's `html_policy` through every
    level that sets none of its own, so a nested `none` under a `trusted-raw`
    root is an override, not the default."""
    board = "html_policy: trusted-raw\n" + nesting.format(policy=policy)
    hits = [w.path for w in _restated_compile_only(tmp_path, board)]
    assert any(p.endswith("html_policy") for p in hits) is warns, hits
