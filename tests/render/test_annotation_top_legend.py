"""A top legend sits above annotation text that reaches past the plot, in every family that draws annotations."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

RULES = """    annotations:
      - type: rule
        x: {a}
        text: Removed registration gating
      - type: rule
        x: {b}
        text: Binary auto-install
      - type: rule
        x: {c}
        text: v1 Compatibility Mode
"""

LINE_ROWS = "".join(
    f'      - ["2026-0{m}-0{d}", {m + d}, {m * d}]\n'
    for m in (1, 2)
    for d in (1, 2, 3, 4)
)

BOARDS = {
    "line": (
        "queries:\n  q:\n    columns: [day, a, b]\n    values:\n"
        + LINE_ROWS
        + "charts:\n  c:\n    query: q\n    type: line\n    x: day\n    y: [a, b]\n"
        "    style:\n      legend:\n        position: top\n"
        + RULES.format(a='"2026-01-03"', b='"2026-01-04"', c='"2026-02-01"')
        + "rows:\n  - c\n"
    ),
    "bar": (
        "queries:\n  q:\n    columns: [m, a, b]\n    values:\n      - [Jan, 3, 4]\n      - [Feb, 5, 2]\n"
        "      - [Mar, 4, 4]\n      - [Apr, 6, 3]\n      - [May, 5, 5]\n      - [Jun, 7, 4]\n      - [Jul, 6, 6]\n"
        "charts:\n  c:\n    query: q\n    type: bar\n    x: m\n    y: [a, b]\n"
        "    style:\n      orientation: vertical\n      legend:\n        position: top\n"
        + RULES.format(a="Mar", b="Apr", c="May")
        + "rows:\n  - c\n"
    ),
    "scatter": (
        "queries:\n  q:\n    columns: [x, y, g]\n    values:\n      - [1, 3, a]\n      - [4, 5, b]\n"
        "      - [7, 6, a]\n      - [10, 5, b]\n"
        "charts:\n  c:\n    query: q\n    type: scatter\n    x: x\n    y: y\n    color: g\n"
        "    style:\n      legend:\n        position: top\n"
        + RULES.format(a="4", b="4.5", c="5")
        + "rows:\n  - c\n"
    ),
}


def _texts(svg: str) -> dict[str, float]:
    """Each text's absolute baseline y, by its string, through the ancestors' translates."""
    found: dict[str, float] = {}

    def walk(node: ET.Element, dy: float) -> None:
        match = re.search(
            r"translate\(\s*[-\d.]+[ ,]+([-\d.]+)\s*\)", node.get("transform", "")
        )
        dy += float(match.group(1)) if match else 0.0
        if node.tag.endswith("text") and node.get("y") is not None:
            found["".join(node.itertext()).strip()] = dy + float(node.get("y", "0"))
        for child in node:
            walk(child, dy)

    walk(ET.fromstring(svg), 0.0)
    return found


@pytest.mark.parametrize("family", sorted(BOARDS))
def test_the_top_legend_clears_crowded_rule_labels(
    family: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "dbt_charts.yml").write_text("{}\n")
    monkeypatch.chdir(tmp_path)
    board, out = tmp_path / "b.yml", tmp_path / "b.svg"
    board.write_text(BOARDS[family])
    result = CliRunner().invoke(
        app, ["render", str(board), "--format", "svg", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    texts = _texts(out.read_text())
    labels = [
        texts[t]
        for t in (
            "Removed registration gating",
            "Binary auto-install",
            "v1 Compatibility Mode",
        )
    ]
    legend = [y for t, y in texts.items() if t in {"A", "B", "a", "b"}]
    assert legend, texts
    assert max(legend) < min(labels) - 8, (legend, labels)
