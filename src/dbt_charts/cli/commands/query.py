"""Query command implementation — named board queries and raw SQL dispatch."""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import typer
from pydantic import TypeAdapter
from rich.markup import escape

if TYPE_CHECKING:
    from rich.console import Console

    from dbt_charts.agent_api.query import (
        BoardQueryLookupResult,
        ExecuteQueryResult,
        QueryBoardResult,
    )
    from dbt_charts.agent_api.validate_query import QueryDiagnostic

from dbt_charts.agent_api import Project
from dbt_charts.agent_api._paths import BOARD_CANDIDATE_SUFFIXES
from dbt_charts.agent_api.query_csv import BOARD_QUERY_HINT, SQL_QUERY_HINT, with_csv
from dbt_charts.cli._console import dct_console
from dbt_charts.cli._json_output import print_json_result
from dbt_charts.cli._project import with_project

console = dct_console()
err_console = dct_console(stderr=True)

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

_SEVERITY_ICON = {"error": "❌", "warning": "⚠️ ", "info": "ℹ️ "}

_diagnostic_adapter: TypeAdapter[QueryDiagnostic] | None = None


def _diagnostic_wire_dict(d: QueryDiagnostic) -> dict[str, Any]:
    """Serialize a QueryDiagnostic the way ExecuteQueryResult does via
    model_dump_json(exclude_none=True), so `--validate --json` matches
    execute_query's wire shape key-for-key instead of the null-filled shape
    `QueryDiagnostic.to_dict()` used to produce.

    Builds the adapter lazily: `dbt_charts.core.inspect.query_validator`
    (QueryDiagnostic's module) imports `core.compile.sql_guard`, and
    `dbt_charts.cli.main` must not eagerly load `dbt_charts.core.compile`
    (test_lazy_imports.py).
    """
    global _diagnostic_adapter
    if _diagnostic_adapter is None:
        from dbt_charts.agent_api.validate_query import QueryDiagnostic as _QD

        _diagnostic_adapter = TypeAdapter(_QD)
    return _diagnostic_adapter.dump_python(d, mode="json", exclude_none=True)


def _print_csv(result: QueryBoardResult | ExecuteQueryResult, hint: str) -> None:
    """Print the CSV the MCP tool gives the model, cut by the same budget."""
    cut = with_csv(result.model_dump(mode="json", exclude_none=True), hint=hint)
    typer.echo(cut["csv"], nl=False)
    if "notice" in cut:
        typer.echo(cut["notice"], err=True)
    elif cut["row_count"] == 0:
        typer.echo("0 rows", err=True)


def _print_diagnostic(d: QueryDiagnostic, target: Console = err_console) -> None:
    icon = _SEVERITY_ICON.get(d.severity, "⚠️ ")
    target.print(f"{icon} [{d.code}] {d.message}", markup=False)
    if d.detail:
        target.print(f"   {d.detail}", markup=False)
    if d.recommendation:
        target.print(f"   → {d.recommendation}", markup=False)
    if d.confidence is not None:
        target.print(f"   confidence: {d.confidence:.0%}")
    for ev in d.evidence:
        target.print(f"   evidence: {ev}", markup=False)


def _print_board_result(result: QueryBoardResult) -> None:
    if not result.success:
        for err in result.errors:
            err_console.print(f"[red]Error:[/red] {escape(err)}")
        if result.available_queries:
            matches = difflib.get_close_matches(
                result.name, result.available_queries, n=3
            )
            if matches:
                err_console.print(f"Did you mean: {escape(', '.join(matches))}?")
            elif _looks_like_sql(result.name):
                err_console.print(
                    "Did you mean to use a source name instead of a board path?"
                )
            err_console.print(
                f"Available queries: {escape(', '.join(result.available_queries))}"
            )
        raise typer.Exit(1)
    _print_csv(result, BOARD_QUERY_HINT)


def _print_sql_result(result: ExecuteQueryResult) -> None:
    if not result.success:
        for err in result.errors:
            err_console.print(f"[red]Error:[/red] {escape(err)}")
        raise typer.Exit(1)
    _print_csv(result, SQL_QUERY_HINT)


def _print_validate_result(
    active: list[QueryDiagnostic],
    suppressed: list[QueryDiagnostic] | None = None,
    json_output: bool = False,
) -> None:
    """Print validate result and exit 1 on errors."""
    has_errors = any(d.severity == "error" for d in active)
    if json_output:
        active_dicts = [_diagnostic_wire_dict(d) for d in active]
        payload: Any = (
            {
                "diagnostics": active_dicts,
                "suppressed": [_diagnostic_wire_dict(d) for d in suppressed or []],
            }
            if suppressed is not None
            else active_dicts
        )
        typer.echo(json.dumps(payload, indent=2))
    elif not active and not suppressed:
        console.print("No issues found.")
    else:
        for d in active:
            _print_diagnostic(d, target=console)
        if suppressed:
            console.print(f"\nSuppressed ({len(suppressed)}):")
            for d in suppressed:
                console.print(f"  ~ [{d.code}] {d.message}", markup=False)

    if has_errors:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# SQL-source resolution helpers
# ---------------------------------------------------------------------------


def _is_board_context(context: str) -> bool:
    """Return True when the first operand selects board-query mode.

    Must track every suffix the project calls a board: a board path that fails
    this test falls through to raw-SQL mode, where the query reference is
    linted as SQL text and a nonexistent one reports success.
    """
    return Path(context).suffix in BOARD_CANDIDATE_SUFFIXES


def _resolve_query_input(
    context: str | None,
    query_text: str | None,
    file: Path | None,
) -> tuple[str, bool, str | None, Path | None, str | None]:
    """Resolve the input operands into dispatch mode.

    Returns (sql_text, mode_is_named, query_name, board_path, source_name).
    Raises typer.Exit(1) on any resolution error.
    """
    if context is None:
        typer.echo("Error: provide a source or board path.", err=True)
        raise typer.Exit(1)

    if _is_board_context(context):
        if file is not None:
            typer.echo(
                "Error: use a board query reference or --file, not both.", err=True
            )
            raise typer.Exit(1)
        if not query_text:
            typer.echo(
                "Error: provide a query reference for the board"
                " (e.g. dct query charts/sales.yml revenue).",
                err=True,
            )
            raise typer.Exit(1)
        return "", True, query_text, Path(context), None

    if file and query_text:
        typer.echo("Error: provide SQL text or --file, not both.", err=True)
        raise typer.Exit(1)

    if file is not None:
        if not file.exists():
            typer.echo(f"Error: file not found: {file}", err=True)
            raise typer.Exit(1)
        return file.read_text(encoding="utf-8"), False, None, None, context

    if not query_text:
        typer.echo(
            "Error: provide SQL or --file for source context"
            " (e.g. dct query warehouse 'SELECT 1').",
            err=True,
        )
        raise typer.Exit(1)

    return query_text, False, None, None, context


_SQL_SHAPE_CHARS = frozenset(" \t\n()")


def _looks_like_sql(name: str) -> bool:
    """Return True if name resembles a SQL expression rather than a query identifier."""
    upper = name.upper()
    return (
        upper.startswith("SELECT")
        or upper.startswith("WITH")
        or any(c in name for c in _SQL_SHAPE_CHARS)
    )


def _handle_lookup_failure(lr: BoardQueryLookupResult, query_name: str) -> NoReturn:
    """Emit errors from a failed BoardQueryLookupResult and raise typer.Exit(1)."""
    for err in lr.errors:
        typer.echo(f"Error: {err}", err=True)
    if lr.available_queries:
        matches = difflib.get_close_matches(query_name, lr.available_queries, n=3)
        if matches:
            typer.echo("Did you mean: " + ", ".join(matches) + "?", err=True)
        elif _looks_like_sql(query_name):
            typer.echo(
                "Did you mean to use a source name instead of a board path?",
                err=True,
            )
        typer.echo("Available queries: " + ", ".join(lr.available_queries), err=True)
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@with_project
def query_command(
    context: str | None,
    query_text: str | None = None,
    validate: bool = False,
    file: Path | None = None,
    dialect: str | None = None,
    vars: dict[str, Any] | None = None,
    show_suppressed: bool = False,
    json_output: bool = False,
    *,
    project: Project,
) -> None:
    """Run a named board query, raw SQL, or static SQL lint — selected from the input args."""
    from dbt_charts.agent_api import ProjectSession
    from dbt_charts.agent_api.query import MAX_QUERY_LIMIT

    # --- 1. Resolve input shape ---
    sql_text, mode_is_named, query_name, board, source = _resolve_query_input(
        context, query_text, file
    )

    if dialect is not None and validate:
        from dbt_charts.agent_api.validate_query import require_sqlglot_dialect

        try:
            require_sqlglot_dialect(dialect)
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from e

    # --- 2. Apply mode flags ---
    # All branches open a ProjectSession so relationship context is available for
    # validate paths (raw-SQL validate-only was previously a stateless short-
    # circuit; it now routes through project_session.validate_query for calibrated severity).
    with ProjectSession.from_project(
        project, allow_mutating_methods=True
    ) as project_session:
        if not validate:
            # Execute path — keep the named-query branch as a direct query_board call so
            # that file-not-found and compile errors are returned as structured JSON
            # (QueryBoardResult.success=False) rather than plain stderr messages.
            if mode_is_named:
                assert query_name is not None
                assert board is not None
                board_result = project_session.query_board(
                    name=query_name,
                    path=board,
                    vars=vars,
                    limit=MAX_QUERY_LIMIT,
                )
                if json_output:
                    print_json_result(board_result)
                    if not board_result.success:
                        raise SystemExit(1)
                else:
                    _print_board_result(board_result)
            else:
                assert source is not None
                exec_result = project_session.execute_query(
                    sql_text,
                    variables=vars,
                    source=source,
                    limit=MAX_QUERY_LIMIT,
                )
                if json_output:
                    print_json_result(exec_result)
                    if not exec_result.success:
                        raise SystemExit(1)
                else:
                    _print_sql_result(exec_result)
            return

        # raw-SQL validate-only — no named-query lookup needed.
        if not mode_is_named:
            if show_suppressed:
                active, suppressed = project_session.validate_query(
                    sql_text, dialect=dialect, source=source, return_suppressed=True
                )
            else:
                active = project_session.validate_query(
                    sql_text, dialect=dialect, source=source
                )
                suppressed = None
            _print_validate_result(active, suppressed, json_output=json_output)
            return

        # Named-query validate: extract SQL from the compiled board.
        assert query_name is not None
        assert board is not None
        lr = project_session.lookup_board_query_sql(query_name, board, vars=vars)
        if not lr.success:
            _handle_lookup_failure(lr, query_name)
        if show_suppressed:
            active, suppressed = project_session.validate_query(
                lr.sql,
                dialect=dialect,
                source=lr.source,
                return_suppressed=True,
            )
        else:
            active = project_session.validate_query(
                lr.sql, dialect=dialect, source=lr.source
            )
            suppressed = None
        _print_validate_result(active, suppressed, json_output=json_output)
