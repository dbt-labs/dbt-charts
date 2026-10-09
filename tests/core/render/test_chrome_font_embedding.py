"""Footer and timestamp families are embedded only while they are painted.

They paint their own `caption` family after the board's fonts are selected, so
the selection has to be told about them, and only when they are visible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dbt_charts.core.project import Project

_YAML = """\
theme: stark
style:
  fonts:
    caption:
      family: Source Serif 4
  footer:
    visible: {footer}
  timestamp:
    visible: {timestamp}
queries:
  q: {{type: values, rows: [{{n: 1}}]}}
charts:
  t: {{query: q, type: table}}
rows: [t]
"""
_SERIF_FONT_FACE_RULE = re.compile(r"@font-face \{[^}]*font-family: 'Source Serif 4'")


def _export(tmp_path: Path, local_project, *, footer: bool, timestamp: bool) -> str:
    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.execute.adapters import build_adapter_registry
    from dbt_charts.core.render import render

    result = compile(
        _YAML.format(footer=str(footer).lower(), timestamp=str(timestamp).lower())
    )
    assert result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(tmp_path)),
    )
    output = render(result.board, executor, format="svg", standalone=True).output
    assert isinstance(output, str)
    return output


@pytest.mark.parametrize(
    ("footer", "timestamp", "embedded"),
    [(True, False, True), (False, True, True), (False, False, False)],
    ids=["footer", "timestamp", "neither"],
)
def test_caption_family_is_embedded_only_for_visible_chrome(
    tmp_path: Path,
    local_project: type[Project],
    footer: bool,
    timestamp: bool,
    embedded: bool,
) -> None:
    svg = _export(tmp_path, local_project, footer=footer, timestamp=timestamp)
    assert (_SERIF_FONT_FACE_RULE.search(svg) is not None) is embedded
