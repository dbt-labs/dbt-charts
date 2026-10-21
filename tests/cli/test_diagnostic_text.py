"""Snapshots of the human text `print_diagnostics` renders.

Text only: the structured diagnostics (JSON, LSP, MCP) keep every instance with
its own code, path, range, fix and docs link.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import duckdb

from dbt_charts._docs_site import docs_site_url
from dbt_charts.cli._error_format import render_diagnostics
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.compile.restated import restated_diagnostic
from dbt_charts.core.diagnostics import (
    WARN_RESTATED_INFERRED,
    WARN_RESTATED_STYLE,
    Diagnostic,
)
from dbt_charts.core.diagnostics.diagnostic import SourceRange
from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

_DOCS = docs_site_url()


def _restated(  # noqa: ANN202
    code,  # noqa: ANN001
    path: str,
    value: object,
    source: str,
    why: str,
    line: int,
) -> Diagnostic:
    return restated_diagnostic(code, path, value, source, why).model_copy(
        update={
            "range": SourceRange(
                file=str(Path.cwd() / "charts/airports.yml"),
                start_line=line,
                end_line=line,
            )
        }
    )


def _union_order_by_error() -> Diagnostic:
    db = Path(tempfile.mkdtemp()) / "t.duckdb"
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE TABLE t AS SELECT 'a' AS sector, 1 AS n")
    adapter = DuckDBAdapter(
        source_config=DuckDBSourceConfig(type="duckdb", path=str(db))
    )
    result = adapter._execute(
        SqlQuery(
            sql="SELECT sector, n FROM t UNION SELECT sector, 2 FROM t "
            "ORDER BY CASE WHEN sector = 'x' THEN 1 ELSE 2 END",
            source="s",
        )
    )
    adapter.close()
    assert result.error is not None
    return result.error.to_diagnostic().model_copy(
        update={"query": "directional_extremes"}
    )


def test_sql_error_names_the_query_and_gives_the_union_advice() -> None:
    text = render_diagnostics([_union_order_by_error()])

    head, fix, _, docs = text.split("\n")
    assert head.startswith(
        "error[ERR-BINDER-QUERY-SHAPE]: Warehouse rejected the shape"
    )
    assert head.endswith("(query: directional_extremes)")
    assert ".." not in head
    assert "columns exist" not in text
    assert fix.startswith("  Fix: ") and "wrap the UNION in a subquery" in fix
    assert docs == (
        f"Docs: ERR-BINDER-QUERY-SHAPE {_DOCS}/reference/errors/#err-binder-query-shape"
    )


def test_validation_error_snapshot() -> None:
    result = compile(
        "queries:\n  q: SELECT 1 AS x\ncharts:\n  k:\n    type: kpi\n    query: q\n"
        "    value: x\n    title: Hello\nrows:\n  - k\n"
    )

    assert render_diagnostics(result.errors) == (
        "error[ERR-VALIDATION-FIELD]: Field 'charts.k.kpi': Value error, "
        "`title:` is not used on `type: kpi`. Use `label:` instead.\n\n"
        f"Docs: ERR-VALIDATION-FIELD {_DOCS}/reference/errors/#err-validation-field"
    )


def test_yaml_syntax_error_snapshot() -> None:
    result = compile("queries:\n  q: [unclosed\ncharts: {\n")

    text = render_diagnostics(result.errors)

    assert text.startswith("error[ERR-YAML-SYNTAX]: Invalid YAML syntax")
    assert "\n      charts: {\n  Fix: Fix the line quoted above." in text
    assert text.endswith(
        f"Docs: ERR-YAML-SYNTAX {_DOCS}/reference/errors/#err-yaml-syntax"
    )


def test_restated_keys_collapse_into_one_block_without_fix_or_docs_noise() -> None:
    warnings = [
        _restated(
            WARN_RESTATED_INFERRED,
            "charts.a.sort.order",
            "asc",
            "the default",
            "default",
            203,
        ),
        _restated(
            WARN_RESTATED_INFERRED,
            "charts.t.style.columns.name.label",
            "Name",
            "the header derived from the column name",
            "derived header",
            258,
        ),
        _restated(
            WARN_RESTATED_STYLE,
            "extends",
            "clarity",
            "the default theme `clarity`",
            "default theme",
            2,
        ),
    ]

    assert render_diagnostics(warnings) == (
        "3 keys restate a default; delete them:\n"
        "  charts/airports.yml:203  charts.a.sort.order: asc  (default)\n"
        "  charts/airports.yml:258  charts.t.style.columns.name.label: Name  (derived header)\n"
        "  charts/airports.yml:2    extends: clarity  (default theme)\n\n"
        f"Docs: WARN-RESTATED-INFERRED {_DOCS}/reference/warnings/#warn-restated-inferred  "
        f"WARN-RESTATED-STYLE {_DOCS}/reference/warnings/#warn-restated-style"
    )


def test_one_warning_per_authored_path_across_restated_codes() -> None:
    path = "charts.t.style.pagination.enabled"
    warnings = [
        _restated(
            WARN_RESTATED_INFERRED,
            path,
            True,
            "what the renderer picks for this chart's data",
            "renderer picks it",
            254,
        ),
        _restated(
            WARN_RESTATED_STYLE,
            path,
            True,
            "the board's style for this chart type",
            "board style",
            254,
        ),
    ]

    text = render_diagnostics(warnings)

    assert text.count(path) == 1
    assert "(board style)" in text
    assert text.startswith("1 key restates a default; delete it:")


def test_mixed_errors_and_warnings_print_errors_first() -> None:
    warning = _restated(
        WARN_RESTATED_INFERRED,
        "charts.a.sort.order",
        "asc",
        "the default",
        "default",
        9,
    )

    text = render_diagnostics([warning, _union_order_by_error()])

    assert text.index("error[") < text.index("1 key restates")
    assert text.count("\nDocs: ") == 1
    assert re.search(
        r"Docs: ERR-BINDER-QUERY-SHAPE \S+  WARN-RESTATED-INFERRED \S+$", text
    )
    assert "Field:" not in text


def test_a_detail_that_repeats_the_message_is_not_printed_again() -> None:
    error = _union_order_by_error()
    assert error.detail is not None and error.detail.rstrip(".") in error.message

    assert "Detail:" not in render_diagnostics([error])


def test_repeated_identical_errors_print_once_with_every_chart() -> None:
    base = _union_order_by_error()
    errors = [base.model_copy(update={"chart": name}) for name in ("a", "b")]

    text = render_diagnostics(errors)

    assert text.count("error[ERR-BINDER-QUERY-SHAPE] x2:") == 1
    assert "  Applies to: a, b" in text
