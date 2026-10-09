"""Tests for plain vs rich Diagnostic rendering.

Rendered through a real Rich ``Console`` to ``StringIO`` so the assertions
catch both the Panel-wrapper decision and Rich's own ANSI/markup
suppression — not just the conditional branch taken inside
``print_diagnostics``.
"""

from __future__ import annotations

import io
import json
import re
from unittest.mock import patch

import pytest
import typer
from rich.console import Console

from dbt_charts.cli._error_format import emit_diagnostics_jsonl, print_diagnostics
from dbt_charts.core.diagnostics import ERR_INTERNAL, Diagnostic
from dbt_charts.core.diagnostics.codes_query import (
    WARN_MISSING_JOIN_PREDICATE,
    WARN_REAGGREGATION,
)
from dbt_charts.core.diagnostics.codes_render import WARN_TOO_MANY_COLOR_CATEGORIES
from dbt_charts.core.diagnostics.diagnostic import SourceRange

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BOX_CHARS = re.compile(r"[╭╮╰╯│─]")


def _make_error() -> Diagnostic:
    return Diagnostic.from_code(
        ERR_INTERNAL,
        message="Something went wrong",
        hint="Try this fix",
        range=SourceRange(file="charts/dashboard.yml", start_line=42, end_line=42),
    )


def _render(err: Diagnostic, *, plain: bool) -> str:
    """Run print_diagnostics through a real Console captured to StringIO."""
    buf = io.StringIO()
    fake = Console(file=buf, force_terminal=not plain, no_color=plain, width=100)
    with patch("dbt_charts.cli._console.Console", return_value=fake):
        print_diagnostics([err])
    return buf.getvalue()


@pytest.fixture
def err() -> Diagnostic:
    return _make_error()


def test_plain_mode_has_no_chrome(err: Diagnostic) -> None:
    """In plain mode: no Panel borders, no ANSI, and inline markup is stripped."""
    output = _render(err, plain=True)
    assert not _BOX_CHARS.search(output), f"Box chars in plain output:\n{output!r}"
    assert not _ANSI.search(output), f"ANSI codes in plain output:\n{output!r}"
    # Body content (with markup tags consumed by Rich) is still readable.
    assert "ERR-INTERNAL" in output
    assert "Something went wrong" in output
    assert "Hint: Try this fix" in output
    assert "At: charts/dashboard.yml:42" in output
    assert "Docs: dct docs errors" in output


def test_rich_mode_has_panel_chrome(err: Diagnostic) -> None:
    """In rich mode the surrounding Panel renders box chars."""
    output = _render(err, plain=False)
    assert _BOX_CHARS.search(output), f"Expected box chars in rich output:\n{output!r}"
    assert "ERR-INTERNAL" in output


def test_detail_and_evidence_reach_rendered_output() -> None:
    """A query diagnostic's detail/evidence (e.g. the offending table names
    for a missing-join-predicate warning) must reach the printed body, not just
    `fields`.

    Regression: from_query_diagnostic.py deliberately stops concatenating
    detail/evidence into `message` (they're typed `fields` values instead),
    but print_diagnostics only read code/message/hint/fix/field/range —
    nothing rendered `fields`, so a join warning's table names were silently
    dropped from CLI output.
    """
    diag = Diagnostic.from_code(
        WARN_MISSING_JOIN_PREDICATE,
        message="CROSS JOIN with no join predicate",
        fields={
            "severity": "error",
            "confidence": 0.9,
            "detail": "CROSS JOIN produces a cartesian product of orders x line_items",
            "evidence": ["Sources: orders, order_items"],
        },
    )
    output = _render(diag, plain=True)
    assert "line_items" in output
    assert "order_items" in output


def test_query_name_is_rendered_in_message() -> None:
    """A DbtChartsError.from_code-constructed Diagnostic carries the query name
    as provenance only (from_code bypasses _format_message's "(query: name)"
    suffix). print_diagnostics must still surface it — otherwise the migrated
    (from_code) adapter path silently drops query attribution that the legacy
    path still prints."""
    diag = Diagnostic.from_code(
        ERR_INTERNAL,
        message="Warehouse could not resolve a column or table reference: boom.",
        query="broken_query",
    )
    output = _render(diag, plain=True)
    assert "(query: broken_query)" in output


def test_jsonl_keeps_nullable_range_fields_explicit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A rangeless diagnostic goes out as `"range": null`, not as a missing key.

    The JSONL stream is read by machines that branch on the field. Dropping the
    key leaves `undefined` on the other side — a value JSON cannot express, and
    one a reader has no way to tell from a shape it does not understand.
    """
    emit_diagnostics_jsonl([Diagnostic.from_code(ERR_INTERNAL, message="no position")])

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["range"] is None


def test_jsonl_keeps_columnless_range_explicit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    diag = Diagnostic.from_code(
        ERR_INTERNAL,
        message="line-level only",
        range=SourceRange(file="charts/b.yml", start_line=13, end_line=13),
    )

    emit_diagnostics_jsonl([diag])

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["range"] == {
        "file": "charts/b.yml",
        "start_line": 13,
        "end_line": 13,
        "columns": None,
    }


def _render_many(diags: list[Diagnostic]) -> str:
    buf = io.StringIO()
    fake = Console(file=buf, force_terminal=False, no_color=True, width=100)
    with patch("dbt_charts.cli._console.Console", return_value=fake):
        print_diagnostics(diags)
    return buf.getvalue()


def _categories(chart: str, line: int, *, message: str | None = None) -> Diagnostic:
    return Diagnostic.from_code(
        WARN_TOO_MANY_COLOR_CATEGORIES,
        message=message or f"Chart {chart!r}: 13 color categories exceed 8",
        fix="Drop the color channel.",
        chart=chart,
        range=SourceRange(file="boards/b.yml", start_line=line, end_line=line),
    )


def _internal(chart: str, line: int, *, message: str = "Boom") -> Diagnostic:
    return Diagnostic.from_code(
        ERR_INTERNAL,
        message=message,
        fix="Fix it.",
        chart=chart,
        range=SourceRange(file="boards/b.yml", start_line=line, end_line=line),
    )


def test_repeated_warning_code_prints_once_with_count_and_every_name() -> None:
    output = _render_many([_categories("revenue", 3), _categories("orders", 9)])

    assert output.count("WARN-TOO-MANY-COLOR-CATEGORIES") == 1
    assert "WARN-TOO-MANY-COLOR-CATEGORIES ×2" in output
    assert "Applies to: revenue, orders" in output
    assert output.count("Fix: Drop the color channel.") == 1
    assert "At: boards/b.yml:3 (+1 more)" in output


def test_single_instance_prints_without_count() -> None:
    output = _render_many([_categories("revenue", 3)])

    assert "×" not in output
    assert (
        "WARN-TOO-MANY-COLOR-CATEGORIES  revenue: Chart 'revenue': 13 color categories exceed 8"
        in output
    )
    assert "Applies to" not in output
    assert "more" not in output
    assert "At: boards/b.yml:3" in output


def test_warnings_with_different_text_say_how_many_are_not_shown() -> None:
    output = _render_many(
        [
            _categories("revenue", 3),
            _categories("orders", 9, message="a different problem"),
            _categories("users", 12, message="another different problem"),
        ]
    )

    assert "WARN-TOO-MANY-COLOR-CATEGORIES ×3" in output
    assert "13 color categories exceed 8" in output
    assert "a different problem" not in output
    assert "and 2 more" in output


def test_different_codes_stay_separate_entries() -> None:
    output = _render_many([_categories("a", 1), _internal("b", 2), _categories("c", 3)])

    assert output.count("WARN-TOO-MANY-COLOR-CATEGORIES") == 1
    assert output.count("ERR-INTERNAL") == 1


def test_errors_with_one_message_keep_every_location() -> None:
    output = _render_many([_internal("a", 4), _internal("b", 8), _internal("c", 15)])

    assert output.count("ERR-INTERNAL") == 1
    assert "ERR-INTERNAL ×3" in output
    assert "At: boards/b.yml:4, boards/b.yml:8, boards/b.yml:15" in output
    assert output.count("Fix: Fix it.") == 1


def test_errors_with_different_messages_are_never_collapsed() -> None:
    output = _render_many(
        [
            _internal("a", 4, message="column x is missing"),
            _internal("b", 8, message="column y is missing"),
        ]
    )

    assert output.count("ERR-INTERNAL") == 2
    assert "column x is missing" in output
    assert "column y is missing" in output


def test_validate_json_keeps_every_instance(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from dbt_charts.agent_api.validate import ValidateResult
    from dbt_charts.cli.commands.validate import _emit

    result = ValidateResult(
        success=True,
        path="boards/b.yml",
        warnings=[_categories("revenue", 3), _categories("orders", 9)],
    )
    with pytest.raises(typer.Exit):
        _emit([result], json_output=True, strict=False)

    payload = json.loads(capsys.readouterr().out)
    assert [w["chart"] for w in payload["warnings"]] == ["revenue", "orders"]


@pytest.mark.parametrize(
    ("warnings", "header"),
    [
        ([_categories("a", 1)], "⚠ 1 warning:"),
        ([_categories("a", 1), _categories("b", 2)], "⚠ 2 warnings, 1 code:"),
        (
            [
                _categories("a", 1),
                _categories("b", 2),
                Diagnostic.from_code(WARN_REAGGREGATION, message="again"),
            ],
            "⚠ 3 warnings, 2 codes:",
        ),
    ],
)
def test_render_warning_header_counts_instances_and_codes(
    warnings: list[Diagnostic],
    header: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from dbt_charts.cli.commands.render import _print_warnings

    _print_warnings(warnings)

    assert capsys.readouterr().err.startswith(header)


def test_diagnostics_with_no_location_are_not_merged() -> None:
    from pathlib import Path

    from dbt_charts.agent_api._paths import defaults_file_skip_diagnostics

    output = _render_many(
        defaults_file_skip_diagnostics([Path("a/meta.yml"), Path("b/meta.yml")])
    )

    assert output.count("WARN-DEFAULTS-FILE-GIVEN-AS-BOARD") == 2
    assert "a/meta.yml" in output
    assert "b/meta.yml" in output
    assert "×" not in output


def test_same_text_charts_do_not_claim_different_text() -> None:
    output = _render_many([_categories("revenue", 3), _categories("orders", 9)])

    assert "different" not in output


def test_errors_naming_their_chart_in_the_message_still_group() -> None:
    output = _render_many(
        [
            _internal("revenue", 4, message="Query 'revenue' failed"),
            _internal("orders", 8, message="Query 'orders' failed"),
        ]
    )

    assert output.count("ERR-INTERNAL") == 1
    assert "ERR-INTERNAL ×2" in output


def test_a_short_chart_name_does_not_blank_unrelated_words() -> None:
    output = _render_many(
        [
            _internal("int", 4, message="expected int, got str"),
            _internal("float", 8, message="expected float, got str"),
        ]
    )

    assert output.count("ERR-INTERNAL") == 2


def test_warnings_differing_only_in_fix_say_so() -> None:
    other = _categories("orders", 9).model_copy(update={"fix": "Something else."})

    output = _render_many([_categories("revenue", 3), other])

    assert "and 1 more with different details" in output
