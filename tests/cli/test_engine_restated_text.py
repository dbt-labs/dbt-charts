"""Every engine `WARN-RESTATED-*` renders through `dct validate`'s text output."""

import pytest

from dbt_charts import _engine
from dbt_charts.cli._error_format import render_diagnostics
from dbt_charts.core.diagnostics import REGISTRY, Diagnostic

_HOST = {
    "boards_root": ".",
    "today": "2026-11-06",
    "default_theme": "clarity",
    "html_policy_ceiling": None,
    "sources": {"db": "{}"},
    "cache_root": (True, "24h", False),
    "source_caches": {"db": (None, None, None)},
    "implicit_source": None,
    "zones": {},
}

_TABLE = """source: db
queries:
  q:
    columns: [order_id]
    values:
      - [1]
    formats:
      order_id: percent
charts:
  c:
    type: table
    query: q
    style:
      pagination:
        page_rows: 7
      columns:
        order_id:
          label: Order ID
          format: percent
style:
  charts:
    table:
      pagination:
        page_rows: 7
rows:
  - c
"""

_VARIABLE = """source: db
variables:
  order_region:
    default: east
    label: Order region
text: hello
"""

_THEME = "source: db\nextends: clarity\ntext: hello\n"


class _Files:
    def __init__(self, text: str) -> None:
        self.text = text

    def read(self, path: str) -> str | None:
        return self.text if path == "a.yml" else None

    def list(self, dir: str) -> list[str]:
        return ["a.yml"] if dir == "" else []

    def exists(self, path: str) -> bool:
        return path == "a.yml"


def _restated(board: str) -> list[Diagnostic]:
    found = _engine.compile(_Files(board), "a.yml", **_HOST).diagnostics()
    return [
        Diagnostic.from_code(
            REGISTRY.get(d["code"]),
            message=REGISTRY.get(d["code"]).message_template.format(**d["params"]),
            path=d.get("path"),
            fields=d["params"],
        )
        for d in found
        if d["code"].startswith("WARN-RESTATED")
    ]


@pytest.mark.parametrize(
    ("board", "code", "why"),
    [
        (_TABLE, "WARN-RESTATED-STYLE", "board style"),
        (_TABLE, "WARN-RESTATED-INFERRED", "derived header"),
        (_TABLE, "WARN-RESTATED-FORMAT", "query format"),
        (_VARIABLE, "WARN-RESTATED-INFERRED", "inferred"),
        (_THEME, "WARN-RESTATED-STYLE", "default theme"),
    ],
)
def test_an_engine_restated_warning_renders_as_text(
    board: str, code: str, why: str
) -> None:
    mine = [d for d in _restated(board) if d.code == code and d.fields["why"] == why]
    assert mine, [(d.code, d.fields) for d in _restated(board)]
    text = render_diagnostics(mine)
    assert f"({why})" in text
    assert str(mine[0].path) in text
