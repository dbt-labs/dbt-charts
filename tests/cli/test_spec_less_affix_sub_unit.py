"""Regression gate: a spec-less currency affix (``{prefix."""

from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from dbt_charts.cli.main import app

from .._paths import DBT_CHARTS_DIR

runner = CliRunner()

_FIXTURE_DIR = (
    DBT_CHARTS_DIR / "tests" / "cli" / "fixtures" / "spec-less-affix-sub-unit"
)

_TEXT_NODE = re.compile(r"<text[^>]*>(.*?)</text>", re.S)
# A digit immediately followed by "m" -- d3's SI milli prefix.
_MILLI_PATTERN = re.compile(r"\d+m\b")


def _text_node_content(svg: str) -> str:
    """Every ``<text>`` node's inner content, joined."""
    return "\n".join(_TEXT_NODE.findall(svg))


def _render(board: str, tmp_path: Path, *, project_dir: Path | None = None) -> str:
    out = tmp_path / "out.svg"
    args = [
        "render",
        board,
        "--format",
        "svg",
        "--output",
        str(out),
        "--project-dir",
        str(project_dir if project_dir is not None else _FIXTURE_DIR),
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output + (result.stderr or "")
    return out.read_text()


def _render_inline_board(yaml_text: str, tmp_path: Path) -> str:
    """Render a board authored inline (not one of the fixture's own boards),
    in its own scratch project directory."""
    (tmp_path / "dbt_charts.yml").write_text("")
    board_path = tmp_path / "board.yml"
    board_path.write_text(yaml_text)
    return _render(str(board_path), tmp_path, project_dir=tmp_path)


def test_sweep_board_paints_no_milli_anywhere(tmp_path: Path) -> None:
    svg = _render("sweep.yml", tmp_path)
    text = _text_node_content(svg)
    milli_hits = _MILLI_PATTERN.findall(text)
    assert not milli_hits, (
        f"milli-prefixed digits found in sweep board text: {milli_hits}"
    )
    # Every slot paints plain digits with the currency prefix, not SI.
    assert "£0.67" in text, text
    assert "£0.18" in text, text
    assert "£0.42" in text, text


def test_sweep_board_alias_variant_paints_no_milli_anywhere(tmp_path: Path) -> None:
    """Same sweep, with the affix supplied through a ``style.formats`` alias (``formats."""
    svg = _render("sweep-alias.yml", tmp_path)
    text = _text_node_content(svg)
    milli_hits = _MILLI_PATTERN.findall(text)
    assert not milli_hits, (
        f"milli-prefixed digits found in alias sweep text: {milli_hits}"
    )
    assert "£0.67" in text, text
    assert "£0.18" in text, text
    assert "£0.42" in text, text


_ABOVE_ONE_BOARD = """
title: compacts above one
queries:
  q:
    columns: [channel, v]
    values: [[A, 12400000], [B, 8600000]]
charts:
  lbl:
    type: bar
    query: q
    x: channel
    y: v
    style:
      number_format:
        prefix: "£"
      marks:
        bar:
          labels:
            visible: true
rows:
  - cols: [lbl]
"""


def test_values_at_or_above_one_still_compact(tmp_path: Path) -> None:
    """A spec-less affix on
    values >= $1 still compacts through the engine's predefined SI spec."""
    text = _text_node_content(_render_inline_board(_ABOVE_ONE_BOARD, tmp_path))
    milli_hits = _MILLI_PATTERN.findall(text)
    assert not milli_hits, (
        f"milli-prefixed digits found for a >= $1 value: {milli_hits}"
    )
    assert "£12.4mn" in text, text


_NO_AFFIX_BOARD = """
title: no affix at all
queries:
  q:
    columns: [channel, v]
    values: [[A, 0.67], [B, 0.18]]
charts:
  lbl:
    type: bar
    query: q
    x: channel
    y: v
    style:
      marks:
        bar:
          labels:
            visible: true
rows:
  - cols: [lbl]
"""


def test_no_affix_authored_paints_unchanged(tmp_path: Path) -> None:
    """A slot with no affix at all paints like every unaffixed board."""
    text = _text_node_content(_render_inline_board(_NO_AFFIX_BOARD, tmp_path))
    assert "0.67" in text, text
    assert "0.18" in text, text
    assert "670m" not in text, text
