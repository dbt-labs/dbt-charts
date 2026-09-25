"""Tests for build_diagnostic / DbtChartsError.to_diagnostic and from_code round-trip."""

from __future__ import annotations


class TestToDiagnostic:
    def test_from_code_round_trip_with_hint(self) -> None:
        from dbt_charts.core.diagnostics import ERR_INVALID_DEFAULT_THEME, Diagnostic
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.diagnostics.registry import REGISTRY

        exc = DbtChartsError.from_code(
            ERR_INVALID_DEFAULT_THEME,
            source="config.yml",
            theme="nite",
            available=["light", "dark", "night"],
        )
        d = exc.to_diagnostic(file="charts/x.yml")

        assert isinstance(d, Diagnostic)
        assert d.code == "ERR-INVALID-DEFAULT-THEME"
        assert "nite" in d.message
        assert "light, dark, night" in d.message
        assert "['light'" not in d.message
        assert REGISTRY.get(d.code).domain == "serve"
        assert d.hint is not None and "night" in d.hint
        assert REGISTRY.get(d.code).doc_url.startswith("https://")

    def test_diagnostic_no_file(self) -> None:
        from dbt_charts.core.diagnostics import ERR_NO_LAYOUT, Diagnostic
        from dbt_charts.core.diagnostics.registry import REGISTRY
        from dbt_charts.core.render.errors import RenderError

        exc = RenderError.from_code(ERR_NO_LAYOUT, charts="rev, orders")
        d = exc.to_diagnostic()

        assert isinstance(d, Diagnostic)
        assert d.code == "ERR-NO-LAYOUT"
        assert REGISTRY.get(d.code).domain == "render"
        assert d.range is None

    def test_unknown_internal_stamp_on_legacy_constructor(self) -> None:
        """Legacy string-constructor stamps ERR-INTERNAL."""
        from dbt_charts.core.render.errors import RenderError

        exc = RenderError("Something went wrong")
        assert exc.code is not None
        assert exc.code.code == "ERR-INTERNAL"

    def test_build_diagnostic_raises_on_none_code(self) -> None:
        """build_diagnostic raises RuntimeError if code is None (impossible path guard)."""
        import pytest

        from dbt_charts.core.diagnostics.diagnostic import build_diagnostic
        from dbt_charts.core.render.errors import RenderError

        exc = RenderError("direct DbtChartsError")
        exc.code = None  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="not an ErrorCode"):
            build_diagnostic(exc)

    def test_from_code_mirrors_fields_to_typed_attrs(self) -> None:
        """from_code should populate typed subclass attrs from fields so
        consumers see the same value whether the error was raised via legacy
        __init__ or from_code.
        """
        from dbt_charts.core.diagnostics import (
            ERR_FORMAT_UNSUPPORTED,
            ERR_KPI_MULTIROW,
        )
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.render.errors import FormatError

        cd_inst = ChartDataError.from_code(
            ERR_KPI_MULTIROW, chart_id="rev", row_count=5
        )
        assert cd_inst.chart_id == "rev"
        assert cd_inst.fields == {"chart_id": "rev", "row_count": 5}

        fe_inst = FormatError.from_code(ERR_FORMAT_UNSUPPORTED, format="xyz")
        assert fe_inst.format == "xyz"
        assert fe_inst.fields == {"format": "xyz"}

    def test_cause_chains_dbt_charts_errors_into_nested_diagnostic(self) -> None:
        """When __cause__ is itself a DbtChartsError, build_diagnostic recurses
        so consumers keep code/level through the chain.
        """
        from dbt_charts.core.diagnostics import (
            ERR_INTERNAL,
            ERR_NO_LAYOUT,
            Diagnostic,
        )
        from dbt_charts.core.diagnostics.registry import REGISTRY
        from dbt_charts.core.render.errors import RenderError

        inner = RenderError.from_code(ERR_NO_LAYOUT, charts="rev")
        try:
            try:
                raise inner
            except RenderError as e:
                raise RenderError.from_code(
                    ERR_INTERNAL, message="wrapping the inner error"
                ) from e
        except RenderError as outer:
            d = outer.to_diagnostic()

        assert d.code == "ERR-INTERNAL"
        assert isinstance(d.cause, Diagnostic)
        assert d.cause.code == "ERR-NO-LAYOUT"
        assert REGISTRY.get(d.cause.code).domain == "render"
        assert REGISTRY.get(d.cause.code).doc_url.endswith("#err-no-layout")

    def test_cause_flattens_foreign_exception_with_non_errorcode_code_attr(
        self,
    ) -> None:
        """Foreign exceptions can carry a `.code` attribute (e.g.
        urllib.error.HTTPError sets `.code` to an HTTP status int). Duck-typing
        on `.code is not None` would falsely recurse and crash; the
        isinstance(ErrorCode) check must flatten these to a string.
        """
        from dbt_charts.core.diagnostics import ERR_INTERNAL
        from dbt_charts.core.render.errors import RenderError

        class FakeHTTPError(Exception):
            """Mimics urllib.error.HTTPError shape: foreign exception with a
            non-ErrorCode `.code` attribute (an int)."""

            code = 503

        try:
            try:
                raise FakeHTTPError("upstream failed")
            except FakeHTTPError as e:
                raise RenderError.from_code(
                    ERR_INTERNAL, message="wrapping http error"
                ) from e
        except RenderError as outer:
            d = outer.to_diagnostic()

        assert isinstance(d.cause, str)
        assert "upstream failed" in d.cause

    def test_line_and_column_populate_a_column_span(self) -> None:
        """ParseError carries both `.line` and `.column` from a PyYAML mark;
        build_diagnostic must thread `.column` into `range.columns`, not just
        `.line` into `start_line`/`end_line`.
        """
        from dbt_charts.core.compile.errors import ParseError
        from dbt_charts.core.diagnostics.diagnostic import ColumnSpan

        exc = ParseError("bad syntax", line=5, column=3)
        d = exc.to_diagnostic(file="f.yaml")

        assert d.range is not None
        assert d.range.start_line == 5
        assert d.range.end_line == 5
        assert d.range.columns == ColumnSpan(start_col=3, end_col=3)

    def test_line_without_column_leaves_columns_none(self) -> None:
        """No column on the exception means no ColumnSpan — never a fabricated 0/1."""
        from dbt_charts.core.compile.errors import ParseError

        exc = ParseError("bad syntax", line=5)
        d = exc.to_diagnostic(file="f.yaml")

        assert d.range is not None
        assert d.range.columns is None

    def test_cause_flattens_foreign_exceptions_to_string(self) -> None:
        """Non-DbtChartsError causes flatten to a string."""
        from dbt_charts.core.diagnostics import ERR_INTERNAL
        from dbt_charts.core.render.errors import RenderError

        try:
            try:
                raise RuntimeError("foreign exception")
            except RuntimeError as e:
                raise RenderError.from_code(
                    ERR_INTERNAL, message="wrapping foreign"
                ) from e
        except RenderError as outer:
            d = outer.to_diagnostic()

        assert isinstance(d.cause, str)
        assert "foreign exception" in d.cause


class TestConnectionFailureDetailBoundary:
    def test_connection_failure_message_carries_no_driver_text_only_detail_does(
        self,
    ) -> None:
        from dbt_charts.core.execute.adapters.base import connection_failure

        raw_driver_text = "FATAL: password authentication failed for user 'brian'"
        result = connection_failure("postgres", RuntimeError(raw_driver_text))

        assert result.error is not None
        d = result.error.to_diagnostic()

        assert d.code == "ERR-WAREHOUSE-CONNECTION"
        assert raw_driver_text not in d.message
        assert "brian" not in d.message
        assert d.detail == raw_driver_text
        assert "detail" not in d.fields
