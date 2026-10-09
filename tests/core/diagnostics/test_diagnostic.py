"""Tests for the unified Diagnostic wire model.

Diagnostic is the one shape for both error- and warning-level diagnostics,
with `level` resolved from the registered code rather than stored on the
instance (D-10).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.diagnostics import (
    ERR_NO_LAYOUT,
    WARN_BAR_BAND_WIDTH_TOO_NARROW,
    Diagnostic,
)
from dbt_charts.core.diagnostics.diagnostic import ColumnSpan, SourceRange


class TestLevel:
    def test_level_comes_from_the_registry(self) -> None:
        d = Diagnostic.from_code(ERR_NO_LAYOUT, message="no layout")
        assert d.level == "error"

        w = Diagnostic.from_code(WARN_BAR_BAND_WIDTH_TOO_NARROW, message="too narrow")
        assert w.level == "warning"

    def test_disagreeing_literal_level_raises(self) -> None:
        with pytest.raises(ValidationError, match="disagrees with registry"):
            Diagnostic.model_validate(
                {"code": ERR_NO_LAYOUT.code, "message": "no layout", "level": "warning"}
            )

    def test_agreeing_literal_level_is_accepted(self) -> None:
        # Not a rule we enforce at runtime (no call site is supposed to pass a
        # literal level=, but the mechanism pops it before validation either
        # way) — see Context in the task worksheet. Documented, not asserted
        # as a red state.
        d = Diagnostic.model_validate(
            {"code": ERR_NO_LAYOUT.code, "message": "no layout", "level": "error"}
        )
        assert d.level == "error"

    def test_assigning_level_raises(self) -> None:
        d = Diagnostic.from_code(ERR_NO_LAYOUT, message="no layout")
        with pytest.raises(AttributeError):
            d.level = "warning"  # type: ignore[misc]

    def test_unregistered_code_raises(self) -> None:
        with pytest.raises(ValidationError, match="unregistered diagnostic code"):
            Diagnostic.model_validate({"code": "ERR-GONE", "message": "x"})


class TestRoundTrip:
    """Diagnostic.model_validate(d.model_dump()) == d must hold — the one
    property that catches the computed-field/extra=forbid interaction.
    """

    def test_round_trips_an_error(self) -> None:
        d = Diagnostic.from_code(ERR_NO_LAYOUT, message="no layout", chart="rev")
        assert Diagnostic.model_validate(d.model_dump()) == d

    def test_round_trips_a_warning(self) -> None:
        w = Diagnostic.from_code(
            WARN_BAR_BAND_WIDTH_TOO_NARROW,
            message="too narrow",
            fix=WARN_BAR_BAND_WIDTH_TOO_NARROW.fix_template,
            chart="rev",
            field="month",
        )
        assert Diagnostic.model_validate(w.model_dump()) == w

    def test_round_trips_with_cause_range_and_fields(self) -> None:
        inner = Diagnostic.from_code(ERR_NO_LAYOUT, message="inner failure")
        outer = Diagnostic.from_code(
            ERR_NO_LAYOUT,
            message="outer failure",
            range=SourceRange(
                file="charts/x.yml",
                start_line=3,
                end_line=3,
                columns=ColumnSpan(start_col=1, end_col=5),
            ),
            fields={"chart_id": "rev", "row_count": 5},
            cause=inner,
        )
        assert Diagnostic.model_validate(outer.model_dump()) == outer

    def test_does_not_serialize_registry_derived_fields(self) -> None:
        d = Diagnostic.from_code(ERR_NO_LAYOUT, message="no layout")
        dumped = d.model_dump()
        for forbidden in ("title", "doc_url", "docs_topic", "domain"):
            assert forbidden not in dumped


class TestSqlLintAdapterUnconcatenated:
    """from_query_diagnostic must stop flattening detail/evidence into message
    and dropping severity/confidence — they become typed fields.
    """

    def test_message_carries_only_the_templated_head(self) -> None:
        from dbt_charts.core.diagnostics.codes_query import (
            WARN_MISSING_JOIN_PREDICATE,
        )
        from dbt_charts.core.diagnostics.from_query_diagnostic import (
            from_query_diagnostic,
        )
        from dbt_charts.core.inspect.query_validator import QueryDiagnostic

        diag = QueryDiagnostic(
            code=WARN_MISSING_JOIN_PREDICATE.code,
            severity="warning",
            message="orders has no join predicate",
            detail="orders CROSS JOIN line_items has no ON clause",
            recommendation=None,
            confidence=0.87,
            evidence=("sources: orders, line_items",),
        )
        d = from_query_diagnostic("revenue", diag)

        expected_head = WARN_MISSING_JOIN_PREDICATE.message_template.format(
            query_name="revenue", message="orders has no join predicate"
        )
        assert d.message == expected_head
        assert "CROSS JOIN line_items" not in d.message
        assert "sources: orders" not in d.message

        assert d.fields["severity"] == "warning"
        assert d.fields["confidence"] == 0.87
        assert d.fields["detail"] == "orders CROSS JOIN line_items has no ON clause"
        assert d.fields["evidence"] == ["sources: orders, line_items"]
