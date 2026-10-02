"""Acceptance gate for the boards in ``fixtures/currency-affixes/``."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from dbt_charts.cli.main import app

from .._paths import DBT_CHARTS_DIR

runner = CliRunner()

_FIXTURE_DIR = DBT_CHARTS_DIR / "tests" / "cli" / "fixtures" / "currency-affixes"


def _render_svg(board: str, tmp_path: Path) -> str:
    out = tmp_path / "out.svg"
    result = runner.invoke(
        app,
        [
            "render",
            board,
            "--format",
            "svg",
            "--output",
            str(out),
            "--project-dir",
            str(_FIXTURE_DIR),
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    return out.read_text()


def test_affix_painted_by_renderers_board_paints_the_currency_on_every_slot(
    tmp_path: Path,
) -> None:
    """support_table, donut total, and KPI all carry the authored EUR prefix."""
    svg = _render_svg("affix-painted-by-renderers.yml", tmp_path)
    assert "EUR 168,000" in svg, svg
    assert "EUR 28,000" in svg, svg
    assert "EUR −41,500" in svg, svg
    assert "EUR 196,000" in svg, svg


def test_affix_painted_by_renderers_board_terminal_kpi_paints_the_currency(
    tmp_path: Path,
) -> None:
    """The terminal KPI paints both the prefix and the ',.0f' spec."""
    result = runner.invoke(
        app,
        [
            "render",
            "affix-painted-by-renderers.yml",
            "--format",
            "terminal",
            "--project-dir",
            str(_FIXTURE_DIR),
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert "EUR 154,500" in result.stdout, result.stdout


def test_format_config_every_slot_board_compiles_and_paints(
    tmp_path: Path,
) -> None:
    """A prefix renders sign-first (-EUR ...), a suffix renders digits-first
    (... EUR)."""
    svg = _render_svg("format-config-every-slot.yml", tmp_path)
    # Prefix axis: sign-first ordering; the zero tick stays bare.
    assert "−€50,000" in svg, svg
    assert "€100,000" in svg, svg
    assert ">€0<" not in svg, svg
    # Suffix axis: trailing form, bare zero.
    assert "100,000 €" in svg, svg
    assert ">0 €<" not in svg, svg
    # The suffix form paints on the bar's aria-label (the exact datum value, not a
    # rounded axis tick).
    assert "−41,500 €" in svg, svg
    # Value labels: sign-first prefix ordering.
    assert "−€41,500" in svg, svg
    # Stack totals (Jan 130,000; Feb 165,000).
    assert "€165,000" in svg, svg


def test_sign_placement_and_repeat_board_paints_each_surface(tmp_path: Path) -> None:
    svg = _render_svg("sign-placement-and-repeat.yml", tmp_path)
    # Axis and mirror ghost: repeat: anchor affixes the top tick only.
    assert ">EUR 100,000<" in svg, svg
    assert ">50,000<" in svg, svg
    assert ">100,000 net<" in svg, svg
    # support_table: the house format's suffix follows the strip anchor;
    # repeat: every composes a spaced prefix, sign after it, on every cell.
    assert ">120,000 net<" in svg, svg
    assert ">−40,000<" in svg, svg
    assert ">EUR −10,000<" in svg, svg
    assert ">EUR 95,000<" in svg, svg
    # Left-axis strip: an explicit repeat: anchor puts the prefix on the leftmost cell.
    assert ">EUR 120,000<" in svg, svg
    assert ">EUR −40,000<" not in svg, svg
    # An anchored prefix leads the sign on its one value: axis, mirror, strip.
    assert ">€−100,000<" in svg, svg
    assert ">EUR −100,000<" in svg, svg
    assert ">€−120,000<" in svg, svg
    assert ">−€" not in svg, svg
    # Table: repeat: every keeps the prefix on every row under symbol_mode anchors.
    assert svg.count(">€<") == 3, svg
