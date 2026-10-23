"""Tests for dbt_charts.agent_api.diagnostics: list_diagnostic_codes / get_diagnostic_code.

Ports and generalizes test_docs_warnings.py — the surface now spans both
ERR-* and WARN-* codes instead of WARN-* only.
"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from dbt_charts.agent_api.diagnostics import (
    REGISTRY,
    DiagnosticCodeDetail,
    DiagnosticCodeSummary,
    GetDiagnosticCodeArgs,
    ListDiagnosticCodesArgs,
    display_map,
    get_diagnostic_code,
    list_diagnostic_codes,
)
from dbt_charts.agent_api.paging import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT


def _all_codes(
    level: Literal["error", "warning"] | None = None,
) -> list[DiagnosticCodeSummary]:
    codes: list[DiagnosticCodeSummary] = []
    cursor: str | None = None
    while True:
        result = list_diagnostic_codes(level=level, cursor=cursor)
        codes.extend(result.codes)
        if result.next is None:
            return codes
        cursor = result.next


class TestListDiagnosticCodes:
    def test_sorted_alphabetically(self) -> None:
        codes = [r.code for r in _all_codes()]
        assert codes == sorted(codes)

    def test_no_filter_includes_both_levels(self) -> None:
        codes = [r.code for r in _all_codes()]
        assert "WARN-REDUNDANT-ENCODING" in codes
        assert "ERR-NO-LAYOUT" in codes

    def test_level_error_excludes_warnings(self) -> None:
        codes = [r.code for r in _all_codes(level="error")]
        assert all(c.startswith("ERR-") for c in codes)
        assert "ERR-NO-LAYOUT" in codes

    def test_level_warning_excludes_errors(self) -> None:
        codes = [r.code for r in _all_codes(level="warning")]
        assert all(c.startswith("WARN-") for c in codes)
        assert "WARN-REDUNDANT-ENCODING" in codes

    def test_default_page_is_capped(self) -> None:
        result = list_diagnostic_codes()
        assert len(result.codes) == DEFAULT_PAGE_LIMIT
        assert result.next is not None

    def test_pages_cover_the_registry_once(self) -> None:
        codes = [r.code for r in _all_codes()]
        assert codes == sorted(dc.code for dc in REGISTRY.all())

    def test_limit_is_clamped_to_the_max(self) -> None:
        result = list_diagnostic_codes(limit=MAX_PAGE_LIMIT + 1)
        assert len(result.codes) == MAX_PAGE_LIMIT

    def test_cursor_resumes_after_the_previous_page(self) -> None:
        first = list_diagnostic_codes(limit=2)
        second = list_diagnostic_codes(limit=2, cursor=first.next)
        assert second.codes[0].code > first.codes[-1].code

    def test_args_reject_a_cursor_that_is_not_a_next_value(self) -> None:
        with pytest.raises(ValidationError):
            ListDiagnosticCodesArgs.model_validate({"cursor": "ERR-NO-LAYOUT"})

    def test_model_dump_shape(self) -> None:
        result = list_diagnostic_codes(level="warning")
        d = result.model_dump(mode="json", exclude_none=True)
        assert d["success"] is True
        assert d["mode"] == "diagnostic_list"
        assert "codes" in d
        assert set(d["codes"][0].keys()) == {"code", "summary"}

    def test_summary_is_user_facing_not_return_jargon(self) -> None:
        """Summary must come from the doc field, not implementation jargon."""
        for entry in _all_codes():
            assert not entry.summary.startswith("Return "), (
                f"Summary for {entry.code!r} looks like implementation jargon: "
                f"{entry.summary!r}"
            )

    def test_no_summary_ends_in_a_dangling_abbreviation(self) -> None:
        """Regression: naive '.'-splitting used to cut summaries mid 'e.g.'."""
        for entry in _all_codes():
            assert entry.summary.count("(") == entry.summary.count(")"), (
                f"Summary for {entry.code!r} is truncated mid-parenthetical: "
                f"{entry.summary!r}"
            )
            assert entry.summary.count("`") % 2 == 0, (
                f"Summary for {entry.code!r} is truncated mid-inline-code: "
                f"{entry.summary!r}"
            )

    def test_every_registered_code_has_a_nonempty_summary(self) -> None:
        codes = _all_codes()
        assert len(codes) > 0
        for entry in codes:
            assert entry.summary.strip(), f"Empty summary for {entry.code!r}"

    def test_every_summary_ends_in_terminal_punctuation(self) -> None:
        """Regression: `_first_sentence` used to stop at a `.` inside an inline
        code span (e.g. `scale.type: log`) or a bare identifier (`meta.yml`),
        shipping summaries like 'Fired when a meta.' The guard now requires the
        terminating `.`/`!`/`?` to be followed by whitespace-or-end-of-string,
        so an accepted summary can no longer end mid-identifier — this asserts
        the visible half of that guarantee across every registered code.
        """
        for entry in _all_codes():
            assert entry.summary.rstrip()[-1:] in (
                ".",
                "!",
                "?",
            ), (
                f"Summary for {entry.code!r} doesn't end in a sentence: {entry.summary!r}"
            )


class TestGetDiagnosticCode:
    def test_known_warning_code_returns_detail(self) -> None:
        result = get_diagnostic_code("WARN-REDUNDANT-ENCODING")
        assert isinstance(result.detail, DiagnosticCodeDetail)
        assert result.detail.code == "WARN-REDUNDANT-ENCODING"
        assert result.detail.level == "warning"

    def test_known_error_code_returns_detail(self) -> None:
        result = get_diagnostic_code("ERR-NO-LAYOUT")
        assert isinstance(result.detail, DiagnosticCodeDetail)
        assert result.detail.code == "ERR-NO-LAYOUT"
        assert result.detail.level == "error"

    def test_case_insensitive(self) -> None:
        upper = get_diagnostic_code("WARN-REDUNDANT-ENCODING")
        lower = get_diagnostic_code("warn-redundant-encoding")
        assert upper.detail is not None and lower.detail is not None
        assert upper.detail.code == lower.detail.code
        assert upper.detail.doc == lower.detail.doc

    def test_doc_contains_expected_content(self) -> None:
        result = get_diagnostic_code("WARN-REDUNDANT-ENCODING")
        assert result.detail is not None
        assert "redundant" in result.detail.doc

    def test_summary_is_user_facing(self) -> None:
        result = get_diagnostic_code("WARN-REDUNDANT-ENCODING")
        assert result.detail is not None
        assert result.detail.summary
        assert not result.detail.summary.startswith("Return ")

    def test_unknown_code_returns_failure(self) -> None:
        result = get_diagnostic_code("NONEXISTENT_CODE_XYZ")
        assert result.success is False
        assert result.detail is None
        assert result.errors is not None
        assert any("NONEXISTENT_CODE_XYZ" in e for e in result.errors)

    def test_unknown_code_includes_valid_codes_in_error(self) -> None:
        result = get_diagnostic_code("NONEXISTENT_CODE_XYZ")
        assert result.errors is not None
        assert any("WARN-REDUNDANT-ENCODING" in e for e in result.errors)

    def test_error_code_resolves_with_its_actual_level(self) -> None:
        """A code registered under the other level still resolves — the caller
        (CLI verb) decides whether/how to flag the mismatch, using the
        returned `level` field. Never 'unknown code' for a code we ship.
        """
        result = get_diagnostic_code("ERR-NO-LAYOUT")
        assert result.success is True
        assert result.detail is not None
        assert result.detail.level == "error"

    def test_model_dump_shape_success(self) -> None:
        result = get_diagnostic_code("WARN-REDUNDANT-ENCODING")
        d = result.model_dump(mode="json", exclude_none=True)
        assert d["success"] is True
        assert d["mode"] == "diagnostic_detail"
        assert set(d["detail"].keys()) == {"code", "level", "summary", "doc"}

    def test_model_dump_shape_failure(self) -> None:
        result = get_diagnostic_code("NONEXISTENT_CODE_XYZ")
        d = result.model_dump(mode="json", exclude_none=True)
        assert d["success"] is False
        assert d["mode"] == "diagnostic_detail"
        assert "detail" not in d
        assert "errors" in d


class TestGetDiagnosticCodeArgs:
    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GetDiagnosticCodeArgs.model_validate(
                {"code": "FOO", "extra_field": "should_fail"}
            )

    def test_missing_code_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GetDiagnosticCodeArgs.model_validate({})


class TestFirstSentence:
    """Unit tests for the derivation helper itself, not just its callers.

    _first_sentence is the derived-by-default path: it must fail loudly
    (raise) rather than silently emit a sentence truncated mid-abbreviation.
    Codes whose doc trips this author an explicit `summary` on the
    DiagnosticCode instead of relying on derivation.
    """

    def test_clean_sentence_is_returned_unchanged(self) -> None:
        from dbt_charts.agent_api.diagnostics import _first_sentence

        assert _first_sentence("Fires on bar charts. More detail here.") == (
            "Fires on bar charts."
        )

    def test_raises_on_unclosed_parenthetical_abbreviation(self) -> None:
        from dbt_charts.agent_api.diagnostics import _first_sentence

        with pytest.raises(ValueError, match="parenthetical"):
            _first_sentence(
                "Fires when a chart uses a bounded projection (e.g. albersUsa) "
                "and points fall outside it."
            )

    def test_raises_on_period_inside_inline_code_span(self) -> None:
        """`scale.type` inside a backtick span is not a sentence boundary."""
        from dbt_charts.agent_api.diagnostics import _first_sentence

        with pytest.raises(ValueError, match="sentence-boundary"):
            _first_sentence(
                "Fired when a bar chart's y axis is set to `scale.type: log`. "
                "A bar's length encodes magnitude from zero."
            )

    def test_raises_on_period_immediately_followed_by_non_space(self) -> None:
        """A bare identifier like `meta.yml` is not a sentence boundary."""
        from dbt_charts.agent_api.diagnostics import _first_sentence

        with pytest.raises(ValueError, match="sentence-boundary"):
            _first_sentence(
                "Fired when a meta.yml file contains a field that is not "
                "recognized by the board schema."
            )

    def test_accepts_period_followed_by_end_of_string(self) -> None:
        from dbt_charts.agent_api.diagnostics import _first_sentence

        assert _first_sentence("Fires on bar charts.") == "Fires on bar charts."


class TestDisplayMap:
    """The re-export boundary Cloud and Playground embed per page."""

    def test_covers_every_registered_code(self) -> None:
        assert set(display_map()) == REGISTRY.codes()

    def test_values_match_registry_doc_url(self) -> None:
        assert (
            display_map()["ERR-EXTRA-FIELD"].doc_url
            == REGISTRY.get("ERR-EXTRA-FIELD").doc_url
        )

    def test_values_match_registry_title(self) -> None:
        assert (
            display_map()["ERR-EXTRA-FIELD"].title
            == REGISTRY.get("ERR-EXTRA-FIELD").title
        )
