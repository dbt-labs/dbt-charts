"""support_table/donut-total affix edge shapes that a Vega hand-composed non-native
affix (support_table_attachment.py/emitters/pie.py) must not crash or misrender.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from dbt_charts.cli.main import app

from .._paths import DBT_CHARTS_DIR

runner = CliRunner()

_FIXTURE_DIR = (
    DBT_CHARTS_DIR / "tests" / "cli" / "fixtures" / "support-table-affix-edge-shapes"
)


def _render_svg(tmp_path: Path) -> str:
    out = tmp_path / "out.svg"
    result = runner.invoke(
        app,
        [
            "render",
            "board.yml",
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


def test_spec_less_support_table_affix_does_not_crash_and_paints_prefix(
    tmp_path: Path,
) -> None:
    svg = _render_svg(tmp_path)
    assert "EUR" in svg


def test_alias_spelling_of_spec_less_support_table_affix_renders_same_digits(
    tmp_path: Path,
) -> None:
    """``format: eur`` (a ``style.formats`` alias whose target is a spec-less
    ``FormatConfig``) must resolve exactly like the inline spelling ``format.
    """
    svg = _render_svg(tmp_path)
    assert "EUR 168k" in svg, svg
    assert "EUR −41.5k" in svg, svg


def test_accounting_sign_flag_survives_support_table_affix_composition(
    tmp_path: Path,
) -> None:
    svg = _render_svg(tmp_path)
    assert "(41,500) EUR" in svg
    assert "−41,500 EUR" not in svg
    assert "-41,500 EUR" not in svg


def test_spec_less_donut_total_affix_does_not_crash(tmp_path: Path) -> None:
    svg = _render_svg(tmp_path)
    # positive query sums to 196000; a spec-less affix now resolves to the same "no
    # format authored" default the total prints without the prefix (plain, comma-grouped
    # digits), not an SI-shaped default ("196k").
    assert "EUR 196,000" in svg
    assert "196k" not in svg
