"""The text output prints each restated key once per file, with its own path."""

from __future__ import annotations

from dbt_charts.cli._error_format import render_diagnostics
from dbt_charts.core.compile.restated import restated_diagnostic
from dbt_charts.core.diagnostics import WARN_RESTATED_INFERRED, WARN_RESTATED_STYLE
from dbt_charts.core.diagnostics.diagnostic import SourceRange


def _at(file: str, code=WARN_RESTATED_STYLE, path: str = "extends"):  # noqa: ANN001, ANN202
    diag = restated_diagnostic(code, path, "clarity", "the theme", "default theme")
    return diag.model_copy(
        update={"range": SourceRange(file=file, start_line=2, end_line=2)}
    )


def test_two_boards_restating_one_path_each_keep_their_warning() -> None:
    text = render_diagnostics([_at("/p/a.yml"), _at("/p/b.yml")])

    assert text.startswith("2 keys restate a default; delete them:")
    assert text.count("extends: clarity") == 2


def test_one_authored_key_reported_twice_prints_once() -> None:
    text = render_diagnostics([_at("/p/a.yml"), _at("/p/a.yml")])

    assert text.startswith("1 key restates a default; delete it:")


def test_the_most_specific_cause_wins() -> None:
    text = render_diagnostics(
        [_at("/p/a.yml", WARN_RESTATED_INFERRED), _at("/p/a.yml", WARN_RESTATED_STYLE)]
    )

    assert "Docs: WARN-RESTATED-STYLE" in text
    assert "WARN-RESTATED-INFERRED" not in text


def test_the_render_count_header_counts_deduped_keys(capsys) -> None:  # noqa: ANN001
    from dbt_charts.cli.commands.render import _print_warnings

    _print_warnings([_at("/p/a.yml"), _at("/p/a.yml")])

    assert capsys.readouterr().err.startswith("⚠ 1 warning:")
