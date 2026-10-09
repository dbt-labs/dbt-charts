"""The default slice label writes the tooltip format into a Jinja template.

Anything it cannot write safely is a chart-scoped error, not a template syntax
error blamed on a template the author never wrote.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.resolve.chart.label_data import _jinja_string_literal
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

_BOARD = """\
style:
  formats:
    x:
      spec: ",.0f"
      {affix}: "{{"
  charts:
    tooltip:
      format: x
queries:
  q: {{type: values, rows: [{{cat: A, v: 1}}, {{cat: B, v: 2}}]}}
charts:
  p: {{query: q, type: pie, theta: v, color: cat}}
rows: [p]
"""


@pytest.mark.parametrize("affix", ["prefix", "suffix"])
def test_a_brace_in_a_slice_format_affix_is_a_chart_error(
    affix: str, local_project: Callable[..., FilesystemProject]
) -> None:
    result = compile_board(_BOARD.format(affix=affix))
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )

    rendered = render(result.board, executor, format="svg")

    assert any(
        "Pie chart 'p'" in str(error) and "contains '{'" in str(error)
        for error in rendered.chart_errors
    ), rendered.chart_errors


def test_a_spec_with_both_quote_characters_cannot_be_written() -> None:
    with pytest.raises(ChartDataError, match="both quote characters"):
        _jinja_string_literal("a'b\"c", "p")


@pytest.mark.parametrize(
    ("text", "literal"),
    [(",.0f", "',.0f'"), ("'>8,.0f", '"\'>8,.0f"'), ("\\>8,.0f", "'\\\\>8,.0f'")],
)
def test_the_spec_literal_uses_the_quote_it_does_not_contain(
    text: str, literal: str
) -> None:
    assert _jinja_string_literal(text, "p") == literal
