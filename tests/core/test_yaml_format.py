"""Tests for YAML render output format."""

import datetime
import uuid
from unittest.mock import MagicMock, patch

import pytest
import yaml

from dbt_charts.core.compile.models.board.normalized import Board, Layout, LayoutItem
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.renderer import render
from dbt_charts.core.render.yaml_format import render_board_yaml

from ._board_utils import _default_chart_style_context, _default_resolved_style


def _make_executor(data: list[dict]) -> MagicMock:
    """Create a mock executor that returns the given data for any chart.

    ``cache_hit_ats`` must be a real (empty) list, not the default MagicMock
    attribute: render() now draws the board for every format (not just svg),
    and the svg footer/timestamp code iterates this attribute directly.
    """
    executor = MagicMock(spec=Executor)
    executor.execute_chart.return_value = data
    executor.cache_hit_ats = []
    return executor


def _make_board(charts: list[Chart], title: str = "Test Board") -> Board:
    """Create a Board with chart items in a rows layout."""
    items = [
        LayoutItem(type="chart", chart=chart, width=600, height=300) for chart in charts
    ]
    return Board(
        id="test-board",
        title=title,
        layout=Layout(type="rows", items=items, width=600, height=600),
        resolved_style=_default_resolved_style(),
        chart_style_context=_default_chart_style_context(),
        level=1,
    )


class TestYamlFormat:
    def test_basic_yaml_output(self, make_chart):
        """YAML format returns valid YAML with board title and charts."""
        data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
        chart = make_chart("bar", x="month", y="revenue", title="Revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="yaml").output

        parsed = yaml.safe_load(result)
        assert parsed["title"] == "Test Board"
        assert "charts" in parsed
        assert "rows" in parsed

    def test_charts_have_resolved_type_and_fields(self, make_chart):
        """Charts in YAML output have concrete type and field mappings."""
        data = [{"month": "Jan", "revenue": 100}]
        chart = make_chart("bar", x="month", y="revenue", id="rev_chart")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="yaml").output

        parsed = yaml.safe_load(result)
        chart_def = parsed["charts"]["rev_chart"]
        assert chart_def["type"] == "bar"
        assert chart_def["x"] == "month"
        assert chart_def["y"] == "revenue"

    def test_queries_use_values_not_sql(self, make_chart):
        """Queries in YAML output use values: with inline data, not sql:."""
        data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
        chart = make_chart("bar", x="month", y="revenue", query_name="sales")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="yaml").output

        parsed = yaml.safe_load(result)
        assert "queries" in parsed
        query_def = parsed["queries"]["sales"]
        assert "rows" in query_def
        assert query_def["rows"] == data
        assert "sql" not in query_def

    def test_layout_preserved_as_rows(self, make_chart):
        """Layout is preserved as rows referencing chart names."""
        data = [{"x": 1, "y": 2}]
        chart1 = make_chart("bar", id="chart_a", title="Chart A")
        chart2 = make_chart("line", id="chart_b", title="Chart B")
        board = _make_board([chart1, chart2])
        executor = _make_executor(data)

        result = render(board, executor, format="yaml").output

        parsed = yaml.safe_load(result)
        assert "rows" in parsed
        assert "chart_a" in parsed["rows"]
        assert "chart_b" in parsed["rows"]

    def test_kpi_chart(self, make_chart):
        """KPI charts include `value` field in YAML output."""
        data = [{"revenue": 42000}]
        chart = make_chart("kpi", value="revenue", x=None, y=None, id="kpi_rev")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="yaml").output

        parsed = yaml.safe_load(result)
        chart_def = parsed["charts"]["kpi_rev"]
        assert chart_def["type"] == "kpi"
        assert chart_def["value"] == "revenue"

    def test_unauthored_collapse_is_not_serialized(self, make_chart):
        """`collapse` defaults to False on every point_map — an author who
        never wrote it must not see it echoed back in `dct render` output
        (they'd copy the unauthored default straight back into the board)."""
        data = [{"lat": 1.0, "lng": 2.0}]
        chart = make_chart("point_map", latitude="lat", longitude="lng", id="pads")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="yaml").output

        parsed = yaml.safe_load(result)
        assert "collapse" not in parsed["charts"]["pads"]

    def test_authored_collapse_true_is_serialized(self, make_chart):
        """`collapse: true` is a real authored decision — it must round-trip."""
        data = [{"lat": 1.0, "lng": 2.0}]
        chart = make_chart(
            "point_map", latitude="lat", longitude="lng", id="pads", collapse=True
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="yaml").output

        parsed = yaml.safe_load(result)
        assert parsed["charts"]["pads"]["collapse"] is True

    def test_round_trip_compiles(self, make_chart):
        """YAML output is valid board input — it re-compiles without errors."""
        from dbt_charts.core.compile import compile

        data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
        chart = make_chart("bar", x="month", y="revenue", id="rev", title="Revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="yaml").output

        compile_result = compile(result)
        assert not compile_result.errors, (
            f"Round-trip compile failed: {compile_result.errors}"
        )
        assert compile_result.board is not None

    def test_round_trip_compiles_when_a_chart_failed_to_resolve(self, make_chart):
        """A failed chart is emitted as a callout, and the document still
        re-compiles.

        This is what makes the callout the right shape for the error case: the
        authored callout surface forbids `query`, and a failed chart no longer
        has one. Emitting anything that still carried a `query` — or pointing at
        a query with no rows behind it — would produce a document that no longer
        compiles, silently.
        """
        from dbt_charts.core.compile import compile
        from dbt_charts.core.diagnostics import ERR_INTERNAL, Diagnostic

        chart = make_chart("bar", x="month", y="revenue", id="rev")
        board = _make_board([chart])
        executor = _make_executor([{"month": "Jan", "revenue": 100}])

        # The shape board_to_dict produces for a chart whose data failed: an
        # item carrying its id and a diagnostic, and no "chart" key at all.
        failed_item = {
            "type": "chart",
            "id": "rev",
            "_error": Diagnostic(code=ERR_INTERNAL.code, message="boom").model_dump(
                exclude_none=True
            ),
        }
        with patch(
            "dbt_charts.core.render.yaml_format.board_to_dict",
            return_value={"title": "Broken", "items": [failed_item]},
        ):
            result = render(board, executor, format="yaml").output

        parsed = yaml.safe_load(result)
        assert parsed["charts"]["rev"]["type"] == "callout"
        assert "query" not in parsed["charts"]["rev"]

        compile_result = compile(result)
        assert not compile_result.errors, (
            f"Round-trip compile failed: {compile_result.errors}"
        )

    @pytest.mark.parametrize(
        "source_yaml",
        [
            pytest.param(
                """
source: test
charts:
  combo:
    query: {sql: "SELECT 1 AS m, 2 AS v"}
    type: bar
    x: m
    y: v
    layers:
      - type: line
        x: m
        y: v
rows: [combo]
""",
                id="layered-cartesian",
            ),
            pytest.param(
                """
source: test
charts:
  note:
    type: callout
    message: hello
rows: [note]
""",
                id="callout",
            ),
            pytest.param(
                """
source: test
charts:
  card:
    query: {sql: "SELECT 1 AS v"}
    type: kpi
    value: v
    variant: inline
rows: [card]
""",
                id="kpi-variant",
            ),
            pytest.param(
                """
source: test
charts:
  grid:
    query: {sql: "SELECT 1 AS m, 'a' AS lbl, 2 AS v"}
    type: table
    rows: [m]
    columns: [lbl]
    values: [v]
rows: [grid]
""",
                id="pivot-table",
            ),
            pytest.param(
                """
source: test
charts:
  rev:
    query: {sql: "SELECT 1 AS m, 2 AS v"}
    type: bar
    x: m
    y: v
    support_table:
      - source: v
        format: ",.0f"
      - source: v
        aggregate: sum
        format:
          spec: ",.0f"
          prefix: "EUR "
rows: [rev]
""",
                id="support-table-formats",
            ),
            pytest.param(
                """
source: test
charts:
  card:
    query: {sql: "SELECT 1 AS v, 2 AS s"}
    type: kpi
    value: v
    support:
      value: s
      format:
        prefix: "EUR "
rows: [card]
""",
                id="kpi-formats",
            ),
        ],
    )
    def test_round_trip_compiles_across_families(self, source_yaml):
        """Every projected field must survive re-compile, on every family.

        The plain-bar case above cannot catch a field that only exists on
        another family — `layers` shipped resolved mark styles for exactly that
        reason. These compile real YAML first so the resolved models are the
        real ones, not hand-built fixtures.
        """
        from dbt_charts.core.compile import compile

        compiled = compile(source_yaml)
        assert compiled.board is not None, [e.message for e in compiled.errors]

        executor = _make_executor([{"m": 1, "v": 2}])
        executor.execute_query.return_value = [{"m": 1, "v": 2}]
        result = render(compiled.board, executor, format="yaml").output

        recompiled = compile(result)
        assert not recompiled.errors, (
            f"Round-trip compile failed: {[e.message for e in recompiled.errors]}"
        )

    def test_nested_board(self, make_chart):
        """Nested boards are represented in YAML output."""
        data = [{"x": 1, "y": 2}]
        chart = make_chart("bar", id="inner_chart")
        inner_board = Board(
            id="inner",
            title="Inner Board",
            layout=Layout(
                type="rows",
                items=[LayoutItem(type="chart", chart=chart, width=600, height=300)],
                width=600,
                height=300,
            ),
            resolved_style=_default_resolved_style(),
            chart_style_context=_default_chart_style_context(),
            level=2,
        )
        outer_board = Board(
            id="outer",
            title="Outer Board",
            layout=Layout(
                type="rows",
                items=[
                    LayoutItem(type="board", board=inner_board, width=600, height=300)
                ],
                width=600,
                height=600,
            ),
            resolved_style=_default_resolved_style(),
            chart_style_context=_default_chart_style_context(),
            level=1,
        )
        executor = _make_executor(data)

        result = render(outer_board, executor, format="yaml").output

        parsed = yaml.safe_load(result)
        assert parsed["title"] == "Outer Board"

    def test_multiple_charts_unique_queries(self, make_chart):
        """Multiple charts with same query_name get distinct resolved queries."""
        data1 = [{"a": 1}]
        data2 = [{"b": 2}]
        chart1 = make_chart("bar", id="c1", query_name="q1")
        chart2 = make_chart("line", id="c2", query_name="q2")
        board = _make_board([chart1, chart2])

        # A callable side_effect (keyed by query_name, not call order) survives
        # the extra execute_chart calls the svg draw pass now makes before the
        # yaml data walk runs — a fixed-length side_effect list would exhaust
        # on the third call and silently fail the whole render.
        def _pick_data(chart: Chart, *_args: object, **_kwargs: object) -> list[dict]:
            return data1 if chart.query_name == "q1" else data2

        executor = MagicMock(spec=Executor)
        executor.execute_chart.side_effect = _pick_data
        executor.cache_hit_ats = []

        result = render(board, executor, format="yaml").output

        parsed = yaml.safe_load(result)
        assert "q1" in parsed["queries"]
        assert "q2" in parsed["queries"]
        assert parsed["queries"]["q1"]["rows"] == data1
        assert parsed["queries"]["q2"]["rows"] == data2

    def test_max_rows_per_query_truncates_with_note(self, make_chart):
        """A row cap truncates values rows and marks the document as partial."""
        data = [{"month": f"m{i}", "revenue": i} for i in range(5)]
        chart = make_chart("bar", x="month", y="revenue", id="rev", query_name="q")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="yaml", max_rows_per_query=2).output

        parsed = yaml.safe_load(result)
        assert parsed["queries"]["q"]["rows"] == [data[0], data[4]]
        # Truncation is explicit: a comment header names the query and counts,
        # and the document stays valid re-compilable YAML.
        first_line = result.splitlines()[0]
        assert first_line.startswith("#")
        assert "q" in first_line
        assert "first 1 and last 1 of 5 rows" in first_line

    def test_no_cap_emits_no_truncation_note(self, make_chart):
        data = [{"month": "m1", "revenue": 1}]
        board = _make_board([make_chart("bar", x="month", y="revenue")])
        result = render(board, _make_executor(data), format="yaml").output
        assert "rows" in result
        assert not result.startswith("#")


# --- warehouse scalar types must round-trip through safe YAML --------------
#
# `render_board_yaml` documents are handed straight to `yaml.safe_load` by
# consumers (e.g. Cloud's registered-view materialize path) and must also
# stay re-compilable board YAML. `yaml.dump`'s default (unsafe) Dumper emits
# `!!python/object` tags for types it doesn't know — TIME, INTERVAL, and UUID
# columns from a warehouse result all hit that path unless normalized first.
# BYTES is different: both `dump` and `safe_dump` already represent `bytes`
# natively as `!!binary`, so it never hit the python-tag path — it needed
# normalizing so the round-trip re-compile sees a plain string, not a `bytes`
# object a `values:` query can't author.


def test_exotic_warehouse_scalars_are_safe_loadable_and_recompile(make_chart):
    """TIME, INTERVAL, UUID, and BYTES columns must not emit python tags."""
    from dbt_charts.core.compile import compile

    data = [
        {
            "started_at": datetime.time(9, 30, 0),
            "duration": datetime.timedelta(hours=1, minutes=15),
            "row_id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
            "payload": b"\x00\x01binary",
            "label": "a",
            "amount": 1,
        }
    ]
    chart = make_chart("table", query_name="q", id="tbl")
    board = _make_board([chart])
    executor = _make_executor(data)

    result = render(board, executor, format="yaml").output

    parsed = yaml.safe_load(result)  # raises ConstructorError on !!python/object tags
    row = parsed["queries"]["q"]["rows"][0]
    assert row["started_at"] == "09:30:00"
    assert row["duration"] == "PT1H15M"
    assert row["row_id"] == "12345678-1234-5678-1234-567812345678"
    assert row["payload"] == "000162696e617279"

    compile_result = compile(result)
    assert not compile_result.errors, (
        f"Round-trip compile failed: {compile_result.errors}"
    )


def test_safe_dump_rejects_a_type_clean_value_does_not_normalize(make_chart):
    """Pin the `safe_dump` swap itself, not just `clean_value`'s output.

    Every type `clean_value` normalizes (above) serializes identically
    whether the dumper is `yaml.dump` or `yaml.safe_dump` — by the time the
    dumper sees the row, it's already a plain string. So a test built only
    from those types goes on passing even if `safe_dump` were reverted to
    `dump`. `bytearray` is a type `clean_value` does not touch: `yaml.dump`
    would silently emit a `!!python/object/apply:` tag for it, while
    `yaml.safe_dump` raises `RepresenterError` — a real signal a revert would
    trip.
    """
    board = _make_board([make_chart("table", query_name="q", id="tbl")])
    executor = _make_executor([{"payload": bytearray(b"\x00\x01")}])

    with pytest.raises(yaml.representer.RepresenterError):
        render_board_yaml(board, executor, {})


def test_round_trip_renders_a_decided_sign_placement_as_unauthored():
    """A sign placement resolve decided is not written back as authored: the
    recompiled board renders the same anchored prefix, with no error."""
    from ._svg_render import render_board_to_svg

    source = """
title: T
queries:
  q:
    type: values
    rows:
      - {month: "2026-01-01", net: 120000}
      - {month: "2026-02-01", net: -40000}
charts:
  c:
    query: q
    type: line
    x: month
    y: net
    style:
      axis_y:
        ticks:
          count: 5
        labels:
          format:
            spec: number
            prefix: "€"
    support_table:
      entries:
        - source: net
          format:
            spec: integer
            prefix: "€"
rows:
  - c
"""
    from pathlib import Path

    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute.adapters import build_adapter_registry

    compiled = compile(source)
    assert compiled.board is not None
    executor = Executor(
        compiled.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=compiled.query_registry,
    )
    round_tripped = render(compiled.board, executor, format="yaml").output
    assert "sign_placement" not in round_tripped, round_tripped
    for svg in (render_board_to_svg(source), render_board_to_svg(round_tripped)):
        assert "ERR-" not in svg, svg
        assert ">€120,000<" in svg, svg
