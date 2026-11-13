"""A default-less multiselect must not take the board down at render time.

The regression this pins is `render()`'s own variable merge, not a helper: the
merge seeds every registered variable to `None`, `variable_defaults` only carries
the ones with a non-`None` default, and markdown/title Jinja reads that dict
directly. So a `multiselect` with no `default:` reached a template as `None`, and
`{{ v | join(', ') }}` raised `TypeError` → `ERR-INTERNAL` → the whole board, not
one tile.

Tests here go through `dbt_charts.core.render.render` deliberately. A test that
narrows the values itself before calling a renderer would pass while the shipped
path crashed — that is exactly how this defect survived its first fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from ..._run import Run

# A default-less multiselect interpolated with a bare join — exactly what the
# Looker migrator emits for a multi-value filter.
_BOARD = """
title: Regions
variables:
  regions:
    input: multiselect
    options:
      static: [North, South]
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
text: |
  ## {{ regions | join(', ') }}
rows:
  - c
"""


def _render(variables: dict[str, object] | None) -> str:
    """Compile and render through the path a host uses, returning the SVG."""
    result = compile(_BOARD)
    assert result.success, result.errors
    executor = Run(
        result.board,
        env=build_exec_env(FilesystemProject(Path.cwd())),
    )
    output = render(
        result.board, executor.ctx, executor.ledger, format="svg", variables=variables
    ).output
    assert isinstance(output, str)
    return output


def test_unset_multiselect_renders_rather_than_killing_the_board() -> None:
    """No value supplied at all — the first-load case that raised ERR-INTERNAL."""
    svg = _render(None)
    assert "<svg" in svg


def test_explicitly_null_multiselect_renders() -> None:
    """A caller passing `None` explicitly is the same unset state."""
    svg = _render({"regions": None})
    assert "<svg" in svg


def test_scalar_multiselect_renders_the_whole_value() -> None:
    """The post-interaction shape: the runtime writes a scalar back via the URL.

    Unnarrowed, `join` iterates the string and renders "N, o, r, t, h" — a wrong
    result that looks right, which is worse than the crash.
    """
    svg = _render({"regions": "North"})
    assert "North" in svg
    assert "N, o, r, t, h" not in svg


@pytest.mark.parametrize(
    "selected",
    [["North"], ["North", "South"]],
    ids=["one", "several"],
)
def test_selected_multiselect_values_reach_the_rendered_text(
    selected: list[str],
) -> None:
    """Every selected member reaches the rendered text.

    Asserted member by member rather than against the joined string: SVG may
    split text across `tspan`s, so a whole-phrase match would be brittle — but
    truncating the expectation to its first member would make the multi-value
    case assert nothing the single-value case does not.
    """
    svg = _render({"regions": selected})
    for member in selected:
        assert member in svg
