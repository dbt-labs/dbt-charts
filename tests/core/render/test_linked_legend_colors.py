"""A linked legend keeps every option and each series' color after a filter.

A chart whose ``link: "?metric={{ color }}"`` sets a choice variable lists every
option of that variable in its legend, and a filter must not recolor a series.
Colors come from the board's category-color plan when the field binds (two
charts encode it, or the author pins it), so the plan must hold a slot for every
option in option order, not only for the values the filtered query returned.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import RenderResult, render

from ..._run import Run

_SOURCES_DIR = Path(tempfile.mkdtemp(prefix="dct-linked-legend-"))
(_SOURCES_DIR / "dbt_charts.yml").write_text(
    'sources:\n  mem:\n    type: duckdb\n    path: ":memory:"\n'
)

# Not alphabetical, so a color by alphabetical rank cannot pass for option order.
_OPTIONS = ["strict", "loose", "lenient"]

_HEAD = """
variables:
  metric:
    input: select
    options:
      static: [strict, loose, lenient]
queries:
  rates:
    source: mem
    sql: >
      SELECT * FROM (VALUES
        ('ac', 'strict', 0.3), ('ac', 'loose', 0.5), ('ac', 'lenient', 0.7),
        ('bd', 'strict', 0.4), ('bd', 'loose', 0.6), ('bd', 'lenient', 0.8)
      ) AS t(arm, metric, rate)
      WHERE metric = '{{ metric }}'
"""

_TWO_CHARTS = """
charts:
  by_metric:
    query: rates
    type: bar
    x: arm
    y: rate
    color: metric
    link: "?metric={{ color }}"
  also_by_metric:
    query: rates
    type: bar
    x: arm
    y: rate
    color: metric
rows:
  - by_metric
  - also_by_metric
"""

_PINNED = """
style:
  charts:
    category_colors:
      metric:
        values:
          loose: "#123456"
"""

_ONE_CHART = """
charts:
  by_metric:
    query: rates
    type: bar
    x: arm
    y: rate
    color: metric
    link: "?metric={{ color }}"
rows:
  - by_metric
"""


def _render(
    yaml_content: str, metric: str, *, strict_variables: bool = True
) -> RenderResult:
    project = FilesystemProject(_SOURCES_DIR)
    result = compile(yaml_content, project_sources=project.sources)
    assert result.success and result.board is not None, result.errors
    executor = Run(
        result.board,
        env=build_exec_env(project),
        query_registry=result.query_registry,
    )
    return render(
        result.board,
        executor.ctx,
        executor.ledger,
        variables={"metric": metric},
        strict_variables=strict_variables,
    )


def _svg(yaml_content: str, metric: str) -> str:
    rendered = _render(yaml_content, metric)
    assert rendered.board_error is None, rendered.board_error
    assert isinstance(rendered.output, str)
    return rendered.output


def _legends(svg: str) -> list[tuple[str, str]]:
    """``(label, swatch paint at rest)`` of every legend entry, in order."""
    swatches = re.findall(
        r'<g class="mark-symbol role-legend-symbol"[^>]*><(?:rect|path)([^>]*)>', svg
    )
    labels = re.findall(
        r'<g class="mark-text role-legend-label"[^>]*><text[^>]*>([^<]*)<', svg
    )

    def at_rest(attrs: str) -> str:
        rest = re.search(r'data-dbt-rest-fill="([^"]*)"', attrs)
        fill = re.search(r' fill="([^"]*)"', attrs)
        assert fill is not None, attrs
        return (rest or fill).group(1)

    return list(zip(labels, (at_rest(attrs) for attrs in swatches), strict=True))


def _assert_filter_keeps_every_option_and_color(
    yaml_content: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Both filtered renders' legends; the linked chart's lead, in option order."""
    strict = _legends(_svg(yaml_content, "strict"))
    lenient = _legends(_svg(yaml_content, "lenient"))
    options = len(_OPTIONS)
    assert [label for label, _ in strict[:options]] == _OPTIONS
    assert lenient[:options] == strict[:options]
    assert len({fill for _, fill in strict[:options]}) == options
    return strict, lenient


def test_a_field_two_charts_encode_keeps_every_option_and_color_after_a_filter() -> (
    None
):
    strict, lenient = _assert_filter_keeps_every_option_and_color(_HEAD + _TWO_CHARTS)
    # The sibling chart lists only the series its rows hold, in the same colors.
    assert strict[3] == strict[0]
    assert lenient[3] == lenient[2]


def test_a_pinned_field_keeps_every_option_and_color_after_a_filter() -> None:
    legends, _ = _assert_filter_keeps_every_option_and_color(
        _HEAD + _PINNED + _ONE_CHART
    )
    assert ("loose", "#123456") in legends


def test_an_unbound_field_keeps_every_option_and_color_after_a_filter() -> None:
    _assert_filter_keeps_every_option_and_color(_HEAD + _ONE_CHART)


def test_a_failing_options_query_is_a_board_error_not_an_exception() -> None:
    yaml_content = """
variables:
  metric:
    input: select
    options:
      query: metric_options
queries:
  metric_options:
    source: mem
    sql: SELECT metric FROM no_such_table
  rates:
    source: mem
    sql: SELECT 'ac' AS arm, 'strict' AS metric, 0.3 AS rate
charts:
  by_metric:
    query: rates
    type: bar
    x: arm
    y: rate
    color: metric
    link: "?metric={{ color }}"
rows:
  - by_metric
"""
    rendered = _render(yaml_content, "strict", strict_variables=False)
    assert rendered.output is None
    assert rendered.board_error is not None
    assert rendered.board_error.code == "ERR-VARIABLE-OPTIONS-FAILED"


def _planned_slots(yaml_content: str, variables: dict[str, str]) -> dict[str, int]:
    """The board scale ``build_resolved_board`` plans for ``metric``, outside ``render()``.

    The artifact path builds its resolved board this way, with no render scope
    open, and replays it later; the plan it stores must already seat every option.
    """
    from dbt_charts.core.render.board_resolve import build_resolved_board

    result = compile(yaml_content)
    assert result.success and result.board is not None, result.errors
    executor = Run(
        result.board,
        env=build_exec_env(FilesystemProject(_SOURCES_DIR)),
        query_registry=result.query_registry,
    )
    resolved_board, _ = build_resolved_board(
        result.board, executor, variables, render_first=False
    )
    scales = {
        scale.field: scale
        for chart in resolved_board.charts.values()
        for scale in chart.category_colors
    }
    return dict(scales["metric"].slots)


def test_a_resolved_board_built_outside_render_seats_every_option() -> None:
    slots = _planned_slots(_HEAD + _TWO_CHARTS, {"metric": "loose"})
    assert slots == {"strict": 0, "loose": 1, "lenient": 2}


def test_integer_options_seat_the_text_rows_they_name() -> None:
    head = _HEAD.replace("static: [strict, loose, lenient]", "static: [3, 1, 2]")
    head = (
        head.replace("'strict'", "'3'")
        .replace("'loose'", "'1'")
        .replace("'lenient'", "'2'")
    )
    slots = _planned_slots(head + _TWO_CHARTS, {"metric": "1"})
    assert slots == {"3": 0, "1": 1, "2": 2}
