"""How a board's column reference picks a query result column.

A warehouse spells a result column its own way: an unquoted Snowflake alias
comes back upper-cased, a BigQuery alias as written. A board spells it however
its author did. The column spelled exactly as written wins; else the one column
equal to it ignoring case; several such columns raise, never a guess.
"""

from __future__ import annotations

from collections.abc import Collection

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.diagnostics.codes_compile import ERR_COLUMN_REF_AMBIGUOUS


def match_column(ref: str, columns: Collection[str], where: str) -> str | None:
    """The result column ``ref`` names; None when it names none.

    ``where`` says which reference this is, for the ambiguity error.
    """
    if ref in columns:
        return ref
    folded = ref.casefold()
    matches = [column for column in columns if column.casefold() == folded]
    if len(matches) > 1:
        raise CompilationError.from_code(
            ERR_COLUMN_REF_AMBIGUOUS,
            where=where,
            ref=ref,
            matches=", ".join(repr(m) for m in matches),
        )
    return matches[0] if matches else None
