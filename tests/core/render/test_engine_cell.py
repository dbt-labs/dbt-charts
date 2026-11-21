"""A KPI, a callout or a spark_bar is drawn by the engine in the cell the layout sized:
each placement under its own key, a chart whose query failed as its error card, and a
chart the engine refuses as that chart's error."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from dbt_charts import _engine
from dbt_charts.agent_api.project_session import ProjectSession
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render
from dbt_charts.core.render.board_links import LinkContext
from dbt_charts.core.render.chart import session
from dbt_charts.core.render.engine_cell import failed_query
from dbt_charts.core.render.render_result import RenderResult

from ..._run import Run

_QUERIES = """\
queries:
  q:
    type: values
    rows:
      - {revenue: 4820}
  broken:
    source: wh
    sql: select * from no_such_table
"""


def _render(
    yaml_text: str, local_project: Callable[..., FilesystemProject]
) -> RenderResult:
    result = compile_board(yaml_text)
    assert result.success, result.errors
    assert result.board is not None
    run = Run(result.board, env=build_exec_env(local_project(Path.cwd())))
    return render(result.board, run.ctx, run.ledger, format="svg")


@dataclass
class Drawn:
    """What a render asked the engine for: the boards it prepared and the placement of every
    chart drawn in a cell, in draw order."""

    prepared: int = 0
    placements: list[str] = field(default_factory=list[str])


@pytest.fixture
def drawn(monkeypatch: pytest.MonkeyPatch) -> Drawn:
    seen = Drawn()
    real = _engine.PreparedBoard

    class Spy:
        def __init__(
            self, *args: Any
        ) -> None:  # type-state: explicit_any — the binding's own arguments
            seen.prepared += 1
            self._board = real(*args)

        def draw(
            self, *args: Any
        ) -> (
            _engine.RenderedCell
        ):  # type-state: explicit_any — the binding's own arguments
            seen.placements.append(args[0])
            return self._board.draw(*args)

    monkeypatch.setattr(_engine, "PreparedBoard", Spy)
    return seen


@pytest.fixture
def cells(drawn: Drawn) -> list[str]:
    """The placement of every chart the engine draws in a cell, in draw order."""
    return drawn.placements


def test_a_render_prepares_the_engine_board_once_for_all_its_kpi_cells(
    local_project: Callable[..., FilesystemProject], drawn: Drawn
) -> None:
    rendered = _render(
        _QUERIES
        + """\
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
  orders:
    type: kpi
    query: q
    value: revenue
    label: Orders
rows:
  - cols: [revenue, orders]
  - revenue
""",
        local_project,
    )
    assert rendered.chart_errors == []
    assert drawn == Drawn(1, ["revenue", "orders", "revenue#2"])


def test_each_placement_of_a_kpi_is_drawn_under_its_own_key(
    local_project: Callable[..., FilesystemProject], cells: list[str]
) -> None:
    rendered = _render(
        _QUERIES
        + """\
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
    label: Revenue
rows:
  - cols: [revenue, revenue]
  - revenue
""",
        local_project,
    )
    assert rendered.chart_errors == []
    assert cells == ["revenue", "revenue#2", "revenue#3"]
    assert isinstance(rendered.output, str)
    assert rendered.output.count("4.8") >= 3


def test_a_kpi_whose_query_failed_is_its_error_card_not_an_engine_cell(
    local_project: Callable[..., FilesystemProject], cells: list[str]
) -> None:
    rendered = _render(
        _QUERIES
        + """\
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
  lost:
    type: kpi
    query: broken
    value: revenue
rows:
  - cols: [lost, revenue]
""",
        local_project,
    )
    assert cells == ["revenue"]
    assert [e.code for e in rendered.chart_errors] != []
    assert all(e.code != "ERR-ENGINE-REFUSED" for e in rendered.chart_errors), (
        rendered.chart_errors
    )


def _refusal() -> _engine.RenderDiagnosticError:
    refusal = _engine.RenderDiagnosticError("refused")
    refusal.code = "ERR-ENGINE-REFUSED"
    refusal.params = {"message": "a KPI shape is not supported"}
    return refusal


@pytest.mark.parametrize("refused_in", ["prepare", "draw"])
def test_a_kpi_the_engine_refuses_is_that_charts_error_not_a_python_draw(
    local_project: Callable[..., FilesystemProject],
    monkeypatch: pytest.MonkeyPatch,
    refused_in: str,
) -> None:
    """No authored KPI reaches a refusal today (compile and resolve refuse the shapes
    the engine does), so the engine's answer is stood in for: refusing the board while
    preparing it, which every KPI cell then reports as its own error, or refusing the
    one chart it draws."""

    class Refusing:
        def __init__(
            self, *args: Any
        ) -> None:  # type-state: explicit_any — the binding's own arguments
            if refused_in == "prepare":
                raise _refusal()

        def draw(
            self, *args: Any
        ) -> (
            _engine.RenderedCell
        ):  # type-state: explicit_any — the binding's own arguments
            raise _refusal()

    def no_python(
        *args: Any, **kwargs: Any
    ) -> None:  # type-state: explicit_any — render_native's own arguments
        raise AssertionError("a KPI the engine refuses fell back to render_native")

    monkeypatch.setattr(_engine, "PreparedBoard", Refusing)
    monkeypatch.setattr(session, "render_native", no_python)
    rendered = _render(
        _QUERIES
        + """\
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
  orders:
    type: kpi
    query: q
    value: revenue
rows:
  - cols: [revenue, orders]
""",
        local_project,
    )
    assert [e.code for e in rendered.chart_errors] == ["ERR-ENGINE-REFUSED"] * 2


def test_a_kpi_painted_by_a_color_scale_is_drawn_in_the_engines_cell(
    local_project: Callable[..., FilesystemProject], cells: list[str]
) -> None:
    rendered = _render(
        _QUERIES
        + """\
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
    style:
      background:
        column: revenue
        scale:
          palette: ["#ffffff", "#3366cc"]
          min: 0
          max: 10000
rows:
  - revenue
""",
        local_project,
    )
    assert rendered.chart_errors == []
    assert cells == ["revenue"]
    assert isinstance(rendered.output, str)
    assert "4.8" in rendered.output


_SCALED = (
    _QUERIES
    + """\
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
    style:
      background:
        column: revenue
        scale:
@SCALE@
rows:
  - revenue
"""
)


@pytest.mark.parametrize(
    "scale",
    [
        "          palette: blues\n          min: 0\n          max: 10000",
        '          palette: ["#000000", "#808080", "#ffffff", "#ff0000"]\n'
        "          min: 0\n          max: 10000\n          hinge: 50",
        "          palette: editorial-10\n          min: 0\n          max: 10000\n          hinge: 50",
    ],
    ids=["vega-scheme-name", "even-diverging-list", "even-named-palette-with-a-hinge"],
)
def test_a_kpi_whose_scale_palette_the_engine_errors_on_is_that_charts_error_card(
    local_project: Callable[..., FilesystemProject],
    cells: list[str],
    scale: str,
) -> None:
    """The engine answers these as production does (`Q-kpi-scale-error-card`): a refusal
    that is the chart's error card, never a fall back to `render_native`."""
    rendered = _render(_SCALED.replace("@SCALE@", scale), local_project)
    assert cells == ["revenue"]
    assert [e.code for e in rendered.chart_errors] == ["ERR-ENGINE-REFUSED"]


@pytest.mark.parametrize(
    "scale",
    [
        '          palette: ["#000000", "#ffffff"]\n          min: 0\n          max: 10000',
        '          palette: ["#000000", "#808080", "#ffffff"]\n'
        "          min: 0\n          max: 10000\n          hinge: 50",
        '          palette: ["#000000", "#808080", "#ffffff", "#ff0000"]\n'
        "          min: 0\n          max: 10000",
    ],
    ids=["two-stop-sequential", "odd-diverging", "even-list-without-a-hinge"],
)
def test_a_kpi_whose_scale_palette_the_engine_draws_is_drawn_in_the_cell(
    local_project: Callable[..., FilesystemProject],
    cells: list[str],
    scale: str,
) -> None:
    _render(_SCALED.replace("@SCALE@", scale), local_project)
    assert cells == ["revenue"]


def test_a_chart_failed_with_its_first_failed_query_its_own_then_its_layers() -> None:
    queries: Any = {
        "query": "a",
        "layers": [None, "b", "c"],
    }  # type-state: explicit_any — a ChartQueries literal
    assert failed_query(queries, {"c", "b"}) == "b"
    assert failed_query(queries, {"a", "c"}) == "a"
    assert failed_query(queries, set()) is None
    assert failed_query({"query": None, "layers": []}, {"a"}) is None


def test_a_kpi_with_notes_is_drawn_and_its_wrapper_carries_them(
    local_project: Callable[..., FilesystemProject], cells: list[str]
) -> None:
    rendered = _render(
        _QUERIES
        + """\
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
    notes: Booked revenue only
rows:
  - revenue
""",
        local_project,
    )
    assert rendered.chart_errors == []
    assert cells == ["revenue"]
    assert isinstance(rendered.output, str)
    assert 'data-chart-notes="Booked revenue only"' in rendered.output


def test_a_kpi_draws_on_a_stored_board_whose_host_passes_builtin_values(
    tmp_path: Path, cells: list[str]
) -> None:
    """The plan echoes the host's directory values untagged beside the board's
    variables; the engine takes the board's alone."""
    (tmp_path / "dbt_charts.yml").write_text("{}\n", encoding="utf-8")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "k.yml").write_text(
        _QUERIES.replace(
            "  broken:\n    source: wh\n    sql: select * from no_such_table\n", ""
        )
        + """\
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
rows:
  - revenue
""",
        encoding="utf-8",
    )
    session = ProjectSession.open(tmp_path)
    rendered = session.render_board(
        session.project.path("charts/k.yml").read_board(), format="svg"
    )
    assert rendered.status == "ok", (rendered.board_error, rendered.validation_errors)
    assert rendered.chart_errors == []
    assert cells == ["revenue"]


def test_a_bordered_kpi_cards_fill_is_rounded_to_its_border_radius(
    local_project: Callable[..., FilesystemProject], cells: list[str]
) -> None:
    rendered = _render(
        _QUERIES
        + """\
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
    style:
      background: "#F6F0E6"
      border:
        width: 3
        color: "#7a3fa0"
        radius: 6
rows:
  - revenue
""",
        local_project,
    )
    assert cells == ["revenue"]
    assert isinstance(rendered.output, str)
    assert re.search(r'<rect [^>]*rx="6"[^>]*fill="#F6F0E6"', rendered.output)


def test_a_kpi_whose_rows_it_cannot_take_is_that_charts_own_error(
    local_project: Callable[..., FilesystemProject], cells: list[str]
) -> None:
    rendered = _render(
        """\
queries:
  q:
    type: values
    rows:
      - {revenue: 1}
      - {revenue: 2}
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
rows:
  - revenue
""",
        local_project,
    )
    assert cells == ["revenue"]
    assert [e.code for e in rendered.chart_errors] == ["ERR-KPI-MULTIROW"]


def test_a_kpi_written_inline_in_the_layout_is_drawn_under_its_public_id(
    local_project: Callable[..., FilesystemProject], cells: list[str]
) -> None:
    rendered = _render(
        _QUERIES
        + """\
rows:
  - type: kpi
    query: q
    value: revenue
    label: Revenue
""",
        local_project,
    )
    assert rendered.chart_errors == []
    assert len(cells) == 1
    assert isinstance(rendered.output, str)
    assert "4.8" in rendered.output


def test_a_kpi_after_a_hidden_placement_of_itself_keys_the_drawn_one_first(
    local_project: Callable[..., FilesystemProject], cells: list[str]
) -> None:
    """The engine counts only the placements a board draws, as the layout walk does."""
    rendered = _render(
        _QUERIES
        + """\
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
rows:
  - rows: [revenue]
    visible: false
  - revenue
""",
        local_project,
    )
    assert rendered.chart_errors == []
    assert cells == ["revenue"]


def test_a_kpi_links_through_the_hosts_link_context(
    local_project: Callable[..., FilesystemProject],
) -> None:
    result = compile_board(
        _QUERIES
        + """\
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
    link: reports/revenue
rows:
  - revenue
"""
    )
    assert result.success, result.errors
    assert result.board is not None
    run = Run(result.board, env=build_exec_env(local_project(Path.cwd())))
    rendered = render(
        result.board,
        run.ctx,
        run.ledger,
        format="svg",
        link_context=LinkContext(
            root="/boards", current_board_slug="home", origin="https://charts.example"
        ),
    )
    assert rendered.chart_errors == []
    assert isinstance(rendered.output, str)
    assert 'href="https://charts.example/boards/reports/revenue"' in rendered.output


def test_a_kpi_shown_by_a_query_probe_is_drawn_in_its_cell(
    local_project: Callable[..., FilesystemProject], cells: list[str]
) -> None:
    rendered = _render(
        _QUERIES
        + """\
  flags:
    type: values
    rows:
      - {show: true}
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
rows:
  - visible:
      query: flags
      column: show
    rows:
      - revenue
""",
        local_project,
    )
    assert rendered.chart_errors == []
    assert cells == ["revenue"]
    assert isinstance(rendered.output, str)
    assert "4.8" in rendered.output


_CALLOUT = """\
charts:
  note:
    type: callout
    title: Heads up
    message: The numbers are in.
"""


@pytest.fixture
def python_callouts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each callout `render_native` draws, in sizing or in the board's render."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedCalloutChart
    from dbt_charts.core.render import engine

    seen: list[str] = []
    real = engine.render_native

    def spy(
        chart: Any, *args: Any, **kwargs: Any
    ) -> Any:  # type-state: explicit_any — render_native's own signature
        if isinstance(chart, ResolvedCalloutChart):
            seen.append(chart.id)
        return real(chart, *args, **kwargs)

    monkeypatch.setattr(engine, "render_native", spy)
    monkeypatch.setattr(session, "render_native", spy)
    return seen


def test_a_callout_is_measured_and_drawn_in_the_engines_cell(
    local_project: Callable[..., FilesystemProject],
    cells: list[str],
    python_callouts: list[str],
) -> None:
    rendered = _render(
        _CALLOUT + "rows:\n  - cols: [note, note]\n  - note\n", local_project
    )
    assert rendered.chart_errors == []
    assert python_callouts == []
    # Sizing measures each width by the chart's first placement; the board's render
    # then draws each placement under its own key.
    assert cells[-3:] == ["note", "note#2", "note#3"]
    assert set(cells[:-3]) == {"note"}
    assert isinstance(rendered.output, str)
    assert rendered.output.count("Heads up") >= 3


def test_a_callout_placed_in_a_restyling_scope_is_drawn_in_each_scope(
    local_project: Callable[..., FilesystemProject],
) -> None:
    red = "#ff0000"
    rendered = _render(
        _CALLOUT
        + f"""\
rows:
  - note
  - style:
      charts:
        callout:
          background: '{red}'
    rows:
      - note
""",
        local_project,
    )
    assert rendered.board_error is None
    assert isinstance(rendered.output, str)
    cards = re.findall(
        r'<g[^>]*data-chart-id="note"[^>]*>.*?</g>', rendered.output, re.S
    )
    assert len(cards) == 2
    assert red not in cards[0].lower()
    assert red in cards[1].lower()


def test_a_callout_whose_project_image_is_missing_is_its_own_error_card(
    local_project: Callable[..., FilesystemProject], cells: list[str]
) -> None:
    rendered = _render(
        _QUERIES
        + """\
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
  note:
    type: callout
    message: "![Logo](images/missing.png)"
rows:
  - cols: [note, revenue]
""",
        local_project,
    )
    assert [e.code for e in rendered.chart_errors] == ["ERR-IMAGE-REF-UNRESOLVED"]
    assert "note" not in cells


def test_a_callout_cut_in_its_cell_warns_with_its_text_as_authored() -> None:
    """The engine cuts the text after its project image resolves to a `data:` URI;
    the warning names the text as the author wrote it, as the Python path did."""
    from dbt_charts.core.board import render_dashboard

    from ...conftest import InMemoryProject

    message = "![m](shot.svg) " + "word " * 400
    project = InMemoryProject(
        Path("/proj"),
        {
            "sales.yml": f"charts:\n  c:\n    type: callout\n    message: '{message}'\n"
            "rows:\n  - c\n",
            "shot.svg": '<svg xmlns="http://www.w3.org/2000/svg" width="10" '
            'height="40"></svg>',
        },
    )
    result = render_dashboard(
        board=project.path("sales.yml").read_board(),
        project=project,
        env=build_exec_env(project, read_only=False),
        result_cache=None,
        format="svg",
    )
    assert result.status == "ok", result.chart_errors
    [warning] = [w for w in result.warnings if w.code == "WARN-CALLOUT-TEXT-TRUNCATED"]
    assert "shot.svg" in warning.message
    assert "data:" not in warning.message


def _spark_bar_board(chart: str) -> str:
    """A one-spark_bar board."""
    return f"""\
queries:
  q:
    type: values
    columns: [label, count]
    values:
      - [alpha, 30]
      - [beta, 12]
      - [gamma, 5]
charts:
  s:
    query: q
    type: spark_bar
{chart}rows:
  - s
width: 400
theme: clarity
style:
  footer:
    visible: false
  timestamp:
    visible: false
"""


@pytest.mark.parametrize(
    ("chart", "height"),
    [("", 94.0), ("    title: Words\n", 118.0)],
    ids=["untitled", "titled"],
)
def test_a_spark_bar_is_measured_and_drawn_in_the_engines_cell_at_productions_height(
    local_project: Callable[..., FilesystemProject],
    cells: list[str],
    monkeypatch: pytest.MonkeyPatch,
    chart: str,
    height: float,
) -> None:
    """Production sized this card through `render_native` at these heights before the
    cell drew it (`spark_counts`, `spark_titled`)."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedSparkBarChart
    from dbt_charts.core.render import engine

    python: list[str] = []
    real = engine.render_native

    def spy(
        resolved: Any, *args: Any, **kwargs: Any
    ) -> Any:  # type-state: explicit_any — render_native's own signature
        if isinstance(resolved, ResolvedSparkBarChart):
            python.append(resolved.id)
        return real(resolved, *args, **kwargs)

    monkeypatch.setattr(engine, "render_native", spy)
    monkeypatch.setattr(session, "render_native", spy)
    rendered = _render(_spark_bar_board(chart), local_project)
    assert rendered.chart_errors == []
    assert python == []
    assert cells[-1] == "s"
    assert isinstance(rendered.output, str)
    assert 'data-chart-id="s" data-chart-type="spark_bar"' in rendered.output
    assert f'data-chart-height="{height}"' in rendered.output


def test_a_spark_bar_labels_a_datetime_as_python_prints_it(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Production labeled a row `str(cell)`, and a datetime's `str` is its
    `isoformat(" ")`, not the ISO text the engine's rows carry."""
    from datetime import datetime

    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.compile.models.source import DuckDBSourceConfig
    from dbt_charts.core.project import InMemoryBoard

    from ..._scripted_env import ScriptedBackend, scripted_env

    board = """\
source: wh
queries:
  q: SELECT at, n FROM t
charts:
  s:
    query: q
    type: spark_bar
    x: n
    y: at
    style:
      label:
        width: 300
rows:
  - s
"""
    project = local_project(tmp_path)
    result = render_dashboard(
        board=InMemoryBoard(board, path=project.path("charts/_t.yml")),
        env=scripted_env(
            ScriptedBackend([{"at": datetime(2024, 1, 5, 10), "n": 3}]),
            named_sources={"wh": DuckDBSourceConfig(type="duckdb", path=":memory:")},
        ),
        format="svg",
        project=project,
        result_cache=None,
    )
    assert result.status == "ok", result.chart_errors
    assert isinstance(result.data, str)
    assert ">2024-01-05 10:00:00<" in result.data
