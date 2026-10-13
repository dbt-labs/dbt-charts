"""Column → boards reverse index: which boards break if this column changes.

A dbt author about to rename ``orders.customer_id`` asks one question — which
dashboards depend on it. The forward direction (validate every board, read the
errors) inverts that question and only answers it *after* the rename breaks
things; this index answers it before, from compiled board SQL alone, with no
warehouse connection.

Boards whose column set cannot be determined (a ``SELECT *``, unparseable
SQL, a board that fails compile) are returned as *indeterminate*, never
omitted — a board missing from an impact list reads as "safe to rename".
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from dbt_charts.core.column_refs import extract_base_column_refs
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.inspect.manifest_utils import (
    INSPECT_TEMPLATE_MANIFEST,
)
from dbt_charts.core.project import Project


class ColumnImpactHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    board: str
    query: str
    table: str
    column: str


class ColumnImpactIndeterminate(BaseModel):
    model_config = ConfigDict(frozen=True)

    board: str
    query: str
    reason: str


class ColumnImpactResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    table: str | None = None
    boards_scanned: int = 0
    """Boards analyzed under ``charts/`` — 0 means nothing was scanned, which
    is not the same answer as "scanned everything, no hits"."""
    hits: list[ColumnImpactHit] = []
    indeterminate: list[ColumnImpactIndeterminate] = []


def _matches_table(ref_table: str, wanted: str) -> bool:
    """Match the full dotted name or any dotted suffix, case-insensitively.

    ``--table orders`` finds ``orders``, ``analytics.orders`` and
    ``my_project.analytics.orders``; ``--table analytics.orders`` narrows to
    that schema whichever database qualifies it. A fully qualified dbt source name
    (``raw.orders``) does not match a bare ``orders`` reached via ``ref()`` —
    the two are different relations until dbt resolves them.
    """
    ref = ref_table.lower()
    return ref == wanted or ref.endswith(f".{wanted}")


def column_impact(
    project: Project,
    *,
    column: str,
    table: str | None = None,
) -> ColumnImpactResult:
    """Every board query referencing *column* (optionally narrowed to *table*).

    Matching is case-insensitive, mirroring SQL identifier semantics on the
    warehouses the boards run against.
    """
    column_l = column.lower()
    table_l = table.lower() if table is not None else None

    hits: list[ColumnImpactHit] = []
    indeterminate: list[ColumnImpactIndeterminate] = []
    boards_scanned = 0

    for board_path in project.iter_boards():
        # Meta files and ejected inspect-template dirs are not user boards
        # (iter_boards already excludes private names). Markdown boards ARE —
        # their frontmatter is full board config, and a .md that isn't one
        # compiles to board=None and lands on the indeterminate arm below
        # rather than being silently skipped.
        if (
            board_path.is_meta
            or (board_path.parent / INSPECT_TEMPLATE_MANIFEST).exists()
        ):
            continue
        boards_scanned += 1
        relpath = board_path.relpath
        try:
            result = compile_file(board_path.read_board())
        except (OSError, UnicodeDecodeError, CompilationError) as exc:
            indeterminate.append(
                ColumnImpactIndeterminate(
                    board=relpath,
                    query="*",
                    reason=f"the board could not be read or compiled: {exc}",
                )
            )
            continue
        if not result.success:
            indeterminate.append(
                ColumnImpactIndeterminate(
                    board=relpath,
                    query="*",
                    reason="the board does not compile, so its queries cannot be analyzed",
                )
            )
            continue
        for query_name, refs in extract_base_column_refs(result).items():
            if refs.indeterminate is not None:
                indeterminate.append(
                    ColumnImpactIndeterminate(
                        board=relpath, query=query_name, reason=refs.indeterminate
                    )
                )
                continue
            for ref_table, ref_column in sorted(refs.columns):
                if ref_column.lower() != column_l:
                    continue
                if table_l is not None and not _matches_table(ref_table, table_l):
                    continue
                hits.append(
                    ColumnImpactHit(
                        board=relpath,
                        query=query_name,
                        table=ref_table,
                        column=ref_column,
                    )
                )

    hits.sort(key=lambda h: (h.board, h.query, h.table))
    indeterminate.sort(key=lambda i: (i.board, i.query))
    return ColumnImpactResult(
        column=column,
        table=table,
        boards_scanned=boards_scanned,
        hits=hits,
        indeterminate=indeterminate,
    )
