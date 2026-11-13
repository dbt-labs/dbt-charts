"""Board column references match the result's columns, ignoring letter case.

A warehouse spells a result column its own way (BigQuery keeps the alias,
Snowflake upper-cases an unquoted one); a board may spell it another. Exact
spelling wins, else the one column equal ignoring case; two such columns raise.
"""

from __future__ import annotations

import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.column_match import match_column
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart import authored, normalized
from dbt_charts.core.compile.models.markers import Channel, ResultColumn
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve.chart.positional import bind_channels
from dbt_charts.core.diagnostics.codes_compile import ERR_COLUMN_REF_AMBIGUOUS
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from ....._run import Run
from ...._svg_render import render_board_svg_result

# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_exact_spelling_wins_over_a_case_only_match() -> None:
    assert match_column("revenue", ["Revenue", "revenue"], where="x") == "revenue"


def test_the_one_case_only_match_is_the_column() -> None:
    assert match_column("revenue", ["Month", "REVENUE"], where="x") == "REVENUE"
    assert match_column("cost", ["Month", "REVENUE"], where="x") is None


def test_two_case_only_matches_raise_naming_both() -> None:
    with pytest.raises(CompilationError) as exc:
        match_column("revenue", ["Revenue", "REVENUE"], where="chart 'c' y")
    assert exc.value.code is ERR_COLUMN_REF_AMBIGUOUS
    assert "'Revenue'" in str(exc.value)
    assert "'REVENUE'" in str(exc.value)
    assert "chart 'c' y" in str(exc.value)


# ---------------------------------------------------------------------------
# Every reference on a chart
# ---------------------------------------------------------------------------


def test_channels_and_side_fields_take_the_result_spelling(
    make_chart: Callable[..., normalized.Chart],
) -> None:
    chart = make_chart(
        "bar",
        x="month",
        y=["revenue"],
        color="region",
        sort={"by": "revenue", "order": "desc"},
        tooltip=[{"column": "units"}],
        field_labels={"revenue": "Revenue ($)"},
    )
    bound = bind_channels(chart, ["Month", "Region", "Revenue", "UNITS"])
    assert isinstance(bound, normalized.BarChart)
    assert (bound.x, bound.y, bound.color) == ("Month", ["Revenue"], "Region")
    assert bound.sort is not None and bound.sort.by == "Revenue"
    assert [t.column for t in bound.tooltip] == ["UNITS"]
    assert bound.field_labels == {"Revenue": "Revenue ($)"}


def test_a_layer_with_its_own_query_matches_that_querys_columns(
    make_chart: Callable[..., normalized.Chart],
) -> None:
    chart = make_chart(
        "bar",
        x="month",
        y="revenue",
        layers=[{"type": "line", "query": "targets", "x": "month", "y": "target"}],
        layer_query_formats={"targets": {"target": "currency"}},
    )
    bound = bind_channels(
        chart,
        ["month", "revenue"],
        layer_columns={"targets": ["MONTH", "Target"]},
    )
    assert isinstance(bound, normalized.BarChart)
    assert (bound.layers[0].x, bound.layers[0].y) == ("MONTH", "Target")
    assert bound.layer_query_formats == {"targets": {"Target": "currency"}}


def test_query_formats_keys_take_the_result_spelling(
    make_chart: Callable[..., normalized.Chart],
) -> None:
    chart = make_chart(
        "kpi",
        value="mau seat util %",
        query=SqlQuery(
            sql="SELECT 1",
            source="test_profile",
            formats={"mau seat util %": "percent"},
        ),
    )
    bound = bind_channels(chart, ["MAU Seat Util %"])
    assert isinstance(bound, normalized.KpiChart)
    assert bound.value == "MAU Seat Util %"
    assert bound.query is not None
    assert bound.query.formats == {"MAU Seat Util %": "percent"}


def test_table_column_keys_and_link_templates_take_the_result_spelling(
    make_chart: Callable[..., normalized.Chart],
) -> None:
    chart = make_chart(
        "table",
        style={
            "columns": {
                "q3-25": {"label": "Q3", "link": "/orders?id={{ order_id }}"},
            }
        },
    )
    bound = bind_channels(chart, ["ORDER_ID", "Q3-25"])
    assert isinstance(bound, normalized.TableChart)
    assert bound.style is not None and bound.style.columns is not None
    assert list(bound.style.columns) == ["Q3-25"]
    assert bound.style.columns["Q3-25"].link == "/orders?id={{ ORDER_ID }}"


def test_an_unknown_column_is_left_for_its_own_error(
    make_chart: Callable[..., normalized.Chart],
) -> None:
    chart = make_chart("bar", x="month", y="nope")
    bound = bind_channels(chart, ["Month", "Revenue"])
    assert isinstance(bound, normalized.BarChart)
    assert (bound.x, bound.y) == ("Month", "nope")


def test_a_reference_matching_two_columns_by_case_raises_naming_the_field(
    make_chart: Callable[..., normalized.Chart],
) -> None:
    chart = make_chart("bar", x="month", y="revenue")
    with pytest.raises(CompilationError) as exc:
        bind_channels(chart, ["month", "Revenue", "REVENUE"])
    assert exc.value.code is ERR_COLUMN_REF_AMBIGUOUS
    assert "y" in str(exc.value)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_a_bigquery_alias_referenced_lowercase_in_table_columns_renders() -> None:
    svg = render_board_svg_result(
        """
queries:
  q:
    columns: [Region, Q3-25]
    values:
      - [EMEA, 10]
charts:
  c:
    type: table
    query: q
    style:
      columns:
        q3-25:
          label: Third quarter
rows:
  - c
"""
    )
    assert not svg.chart_errors
    assert isinstance(svg.output, str)
    assert "Third quarter" in svg.output


def _kpi_board(formats_key: str, value: str) -> str:
    return f"""
queries:
  q:
    columns: [MAU Seat Util %]
    values:
      - [0.5]
    formats:
      {formats_key}: percent
charts:
  c:
    type: kpi
    query: q
    value: {value}
rows:
  - c
"""


def test_a_snowflake_alias_referenced_lowercase_in_formats_renders() -> None:
    lowercase = render_board_svg_result(
        _kpi_board("mau seat util %", "mau seat util %")
    )
    exact = render_board_svg_result(_kpi_board("MAU Seat Util %", "MAU Seat Util %"))
    plain = render_board_svg_result(
        _kpi_board("other", "MAU Seat Util %").replace(
            "    formats:\n      other: percent\n", ""
        )
    )
    assert not lowercase.chart_errors
    assert lowercase.output == exact.output
    assert lowercase.output != plain.output


_OWN_QUERY_LAYER = """
queries:
  q:
    columns: [month, revenue]
    values:
      - [Jan, 100]
      - [Feb, 150]
  q2:
    columns: [month, TARGET]
    values:
      - [Jan, 120]
      - [Feb, 130]
charts:
  c:
    query: q
    type: line
    x: month
    y: revenue
    layers:
      - type: line
        query: q2
        y: target
rows:
  - c
"""


@pytest.mark.parametrize("fmt", ["svg", "json"])
def test_an_own_query_layer_matches_its_columns_in_every_export(fmt: str) -> None:
    result = compile(_OWN_QUERY_LAYER)
    assert result.success
    run = Run(
        result.board,
        env=build_exec_env(FilesystemProject(Path.cwd())),
    )
    rendered = render(result.board, run.ctx, run.ledger, format=fmt)
    assert not rendered.chart_errors


# ---------------------------------------------------------------------------
# The walk cannot miss a field
# ---------------------------------------------------------------------------


def _metadata(tp: Any) -> list[Any]:  # type-state: explicit_any — a type form
    out: list[Any] = []
    if typing.get_origin(tp) is typing.Annotated:
        out.extend(tp.__metadata__)
    for arg in typing.get_args(tp):
        out.extend(_metadata(arg))
    return out


def _models(tp: Any) -> list[type[BaseModel]]:  # type-state: explicit_any — a type form
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return [tp]
    return [m for arg in typing.get_args(tp) for m in _models(arg)]


def _reads_columns(model: type[BaseModel], seen: set[type[BaseModel]]) -> bool:
    if model in seen:
        return False
    seen.add(model)
    for field in model.model_fields.values():
        if any(isinstance(m, ResultColumn) for m in field.metadata) or any(
            isinstance(m, ResultColumn) for m in _metadata(field.annotation)
        ):
            return True
        if any(_reads_columns(m, seen) for m in _models(field.annotation)):
            return True
    return False


def _channel_fields() -> set[str]:
    names: set[str] = set()
    for name in authored.__all__:
        model = getattr(authored, name)
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        for field_name, field in model.model_fields.items():
            if any(isinstance(m, Channel) for m in field.metadata):
                names.add(field_name)
    return names


@pytest.mark.parametrize(
    "model",
    typing.get_args(typing.get_args(normalized.Chart)[0]),
    ids=lambda m: m.__name__,
)
def test_every_authored_channel_field_carries_a_column_ref(
    model: type[BaseModel],
) -> None:
    for name in _channel_fields() & model.model_fields.keys():
        field = model.model_fields[name]
        assert any(isinstance(m, ResultColumn) for m in field.metadata) or (
            _reads_columns_in(field.annotation)
        ), f"{model.__name__}.{name} names a column but carries no ColumnRef"


def _reads_columns_in(tp: Any) -> bool:  # type-state: explicit_any — a type form
    return any(isinstance(m, ResultColumn) for m in _metadata(tp)) or any(
        _reads_columns(m, set()) for m in _models(tp)
    )
