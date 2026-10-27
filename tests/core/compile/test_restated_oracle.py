"""Every key WARN-RESTATED-* flags must be one whose removal changes nothing.

The oracle: delete the flagged key,
re-render, compare the SVG bytes. A flagged key that changes the render is a
false positive, the serious failure.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from dbt_charts.agent_api.cache import open_cache
from dbt_charts.agent_api.project_session import ProjectSession
from dbt_charts.core.compile import compile_file
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.project import InMemoryBoard

_STAMP = re.compile(r'data-rendered-at="[^"]*"|(?<=clip)\d+(?=-)')

_BOARD = """
extends: clarity
style:
  frame:
    card_gap: 8
    max_width: 1000
  charts:
    default_chart_height: 300
    table:
      pagination:
        page_rows: 5
queries:
  sales:
    type: values
    rows:
      - {model_year: 1970, avg_mpg: 3.5, units: 12}
      - {model_year: 1971, avg_mpg: 4.5, units: 20}
    formats:
      avg_mpg: integer
charts:
  total:
    type: kpi
    query: sales
    value: avg_mpg
    style:
      value:
        format: integer
  detail:
    type: table
    query: sales
    style:
      pagination:
        page_rows: 5
      columns:
        model_year:
          label: Model year
        units:
          label: Units sold
rows:
  - total
  - detail
"""


def _render(session: ProjectSession, path: Any, text: str) -> str:
    result = session.render_board(
        board=InMemoryBoard(text, path=path),
        format="svg",
        scale=2.0,
        standalone=True,
    )
    assert isinstance(result.data, str)
    return _STAMP.sub("", result.data)


def _without(doc: dict[str, Any], dotted: str) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    *parents, leaf = dotted.split(".")
    node = out
    for key in parents:
        node = node[key]
    del node[leaf]
    return out


@pytest.mark.parametrize("board", [_BOARD])
def test_every_flagged_key_is_removable_without_changing_the_render(
    board: str, tmp_path: Path
) -> None:
    reset_config()
    (tmp_path / "dbt_charts.yml").write_text("", encoding="utf-8")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/board.yml").write_text(board, encoding="utf-8")
    cache = open_cache(None)
    with ProjectSession.open(str(tmp_path), cache=cache) as session:
        path = session.project.path("charts/board.yml")
        result = compile_file(path.read_board(), apply_meta=True)
        flagged = [
            w.path for w in result.warnings if w.code.startswith("WARN-RESTATED-")
        ]
        assert len(flagged) >= 6, flagged
        doc = yaml.safe_load(board)
        baseline = _render(session, path, yaml.safe_dump(doc, sort_keys=False))
        for dotted in flagged:
            assert dotted is not None
            trimmed = yaml.safe_dump(_without(doc, dotted), sort_keys=False)
            assert _render(session, path, trimmed) == baseline, dotted
    cache.close()


_RENDER_TIME_BOARD = """
source: cars
links:
  auto: true
variables:
  first_year:
    input: select
    options:
      static: [1972, 1971, 1970]
queries:
  q:
    sql: |
      SELECT model_year, COUNT(*) AS n FROM cars
      WHERE {{ filter('model_year', first_year, '>=') }}
      GROUP BY 1
charts:
  detail:
    type: table
    query: q
    notes: Written for the next author, never drawn.
    link: false
rows:
  - detail
"""


def test_render_time_flags_are_removable_and_skip_undrawn_or_context_keys(
    tmp_path: Path,
) -> None:
    import duckdb

    reset_config()
    with duckdb.connect(str(tmp_path / "cars.duckdb")) as con:
        con.execute(
            "CREATE TABLE cars AS SELECT * FROM (VALUES (1970), (1971), (1972)) t(model_year)"
        )
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  cars:\n    type: duckdb\n    path: cars.duckdb\n", encoding="utf-8"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/board.yml").write_text(_RENDER_TIME_BOARD, encoding="utf-8")
    cache = open_cache(None)
    with ProjectSession.open(str(tmp_path), cache=cache) as session:
        path = session.project.path("charts/board.yml")
        doc = yaml.safe_load(_RENDER_TIME_BOARD)
        baseline_text = yaml.safe_dump(doc, sort_keys=False)
        result = session.render_board(
            InMemoryBoard(baseline_text, path=path),
            format="svg",
            use_cache=False,
            restated_check=True,
        )
        flagged = [
            w.path for w in result.warnings if w.code.startswith("WARN-RESTATED-")
        ]
        assert "charts.detail.notes" not in flagged
        assert "charts.detail.link" not in flagged
        assert "variables.first_year.options.static" not in flagged
        baseline = _render(session, path, baseline_text)
        for dotted in flagged:
            assert dotted is not None
            trimmed = yaml.safe_dump(_without(doc, dotted), sort_keys=False)
            assert _render(session, path, trimmed) == baseline, dotted
    cache.close()
