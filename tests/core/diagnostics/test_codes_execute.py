"""Tests for execute-domain ERR-* error codes.

ERR_FILE_NOT_FOUND is not covered here: the execute-domain variant of this
constant (zero raise sites, pre-rename name dropped along with the domain
segment) was deleted outright in the domain-drop cleanup. The surviving
``ERR_FILE_NOT_FOUND`` is a
compile-domain code (``codes_compile.py``) with an unrelated
``message_template`` shape (``path``/``hint``, not ``file_path``).

ERR_SOURCE_NOT_FOUND's dbt_charts.yml/sources: wording is not asserted here
either: the merge folded the execute-domain wording into compile's, and the
dbt-project-name guidance that used to carry that phrasing is deferred to
``fix_template`` rather than kept in ``message_template``.
"""

from __future__ import annotations


class TestCodesExecute:
    def test_codes_registered(self) -> None:
        from dbt_charts.core.diagnostics import REGISTRY
        from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND
        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_SOURCE_NOT_FOUND_EMPTY,
            ERR_SOURCE_NOT_NAMED,
        )

        assert REGISTRY.get("ERR-SOURCE-NOT-FOUND") is ERR_SOURCE_NOT_FOUND
        assert REGISTRY.get("ERR-SOURCE-NOT-FOUND-EMPTY") is ERR_SOURCE_NOT_FOUND_EMPTY
        assert REGISTRY.get("ERR-SOURCE-NOT-NAMED") is ERR_SOURCE_NOT_NAMED

    def test_doc_url_and_topic_present(self) -> None:
        from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND
        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_SOURCE_NOT_FOUND_EMPTY,
            ERR_SOURCE_NOT_NAMED,
        )

        for code in (
            ERR_SOURCE_NOT_FOUND,
            ERR_SOURCE_NOT_FOUND_EMPTY,
            ERR_SOURCE_NOT_NAMED,
        ):
            assert code.doc_url, f"{code.code} missing doc_url"
            assert code.docs_topic, f"{code.code} missing docs_topic"

    def test_source_not_found_message_mentions_query_and_source(self) -> None:
        from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND

        msg = ERR_SOURCE_NOT_FOUND.message_template.format(
            query_name="revenue_by_month",
            source="prj_production",
            available="fivetran",
            advice="",
        )
        assert "revenue_by_month" in msg
        assert "prj_production" in msg
        assert "sources:" in msg

    def test_source_not_found_empty_message_mentions_dbt_charts_yml(self) -> None:
        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_SOURCE_NOT_FOUND_EMPTY,
        )

        msg = ERR_SOURCE_NOT_FOUND_EMPTY.message_template.format(
            source="prj_production", advice=""
        )
        assert "dbt_charts.yml" in msg
        assert "sources:" in msg

    def test_source_not_named_message_is_surface_neutral(self) -> None:
        """Raised on both local and Cloud sourceless queries, so the message must
        not assume a local ``dbt_charts.yml`` / ``sources:`` block or a DuckDB adapter.
        """
        from dbt_charts.core.diagnostics.codes_execute import ERR_SOURCE_NOT_NAMED

        msg = ERR_SOURCE_NOT_NAMED.message_template.format(available="none configured")
        assert "names no source" in msg
        assert "none configured" in msg
        assert "`source:`" in msg
        assert "default" not in msg
        assert "dbt_charts.yml" not in msg
        assert "DuckDB" not in msg

    def test_source_not_found_hint_fires_on_close_match(self) -> None:
        from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND

        assert ERR_SOURCE_NOT_FOUND.hint_generator is not None
        hint = ERR_SOURCE_NOT_FOUND.hint_generator(
            source="fivtran", available=["fivetran"], advice=""
        )
        assert hint is not None
        assert "fivetran" in hint

    def test_source_not_found_hint_absent_on_no_match(self) -> None:
        from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND

        assert ERR_SOURCE_NOT_FOUND.hint_generator is not None
        hint = ERR_SOURCE_NOT_FOUND.hint_generator(
            source="prj_production", available=["fivetran"], advice=""
        )
        assert hint is None
