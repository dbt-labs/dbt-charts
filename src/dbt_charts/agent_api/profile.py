"""One-screen data profile of a file source: what an author asks before writing a board.

Per table: row count; per column: type, null share, and the one summary that
fits it (value counts, distinct count with examples, numeric range, date range
and grain); then sample rows. Every number comes from a few full-table
aggregates through ``execute_query``, so it runs only on local file sources.
"""

from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any

from dbt_charts.agent_api.query import MAX_QUERY_LIMIT, execute_query
from dbt_charts.core.compile.normalize.queries import is_file_source_ref
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.inspect.db_types import (
    is_complex,
    is_numeric,
    is_temporal,
)
from dbt_charts.core.text.row_table import rows_csv_lines

# Sources a profile may scan: all are local files, so a full pass is cheap.
FILE_KINDS = frozenset({"csv", "parquet", "json"})
PROFILE_SOURCE_TYPES = FILE_KINDS | {"duckdb", "sqlite"}

LOW_CARDINALITY = 12
NUMBER_VALUES = 6  # a number with more distinct values reads better as a range
EXAMPLES = 3
SAMPLE_ROWS = 3
SAMPLE_COLUMNS = 16
MAX_TABLES = 5
LINE_BUDGET = 66
LINE_WIDTH = 100
OVERVIEW_WIDTH = 80
OVERVIEW_TABLES = 56
CELL_WIDTH = 24

_TABLE_LINES = 1 + 1 + SAMPLE_ROWS  # header + sample-row header + sample rows

_DUCKDB_COLUMNS_SQL = (
    "SELECT table_schema AS s, table_name AS t, column_name AS c, data_type AS ty "
    "FROM information_schema.columns "
    "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
    "ORDER BY table_schema, table_name, ordinal_position"
)
_SQLITE_COLUMNS_SQL = (
    "SELECT 'main' AS s, m.name AS t, p.name AS c, p.type AS ty "
    "FROM sqlite_master m, pragma_table_info(m.name) p "
    "WHERE m.type IN ('table', 'view') AND m.name NOT LIKE 'sqlite_%' "
    "ORDER BY m.name, p.cid"
)

# (days between consecutive distinct dates) -> grain, when every gap fits.
_GRAINS = (
    ("daily", 1, 1),
    ("weekly", 7, 7),
    ("monthly", 28, 31),
    ("quarterly", 89, 92),
    ("yearly", 365, 366),
)


def profile_source(
    source: str,
    *,
    adapter_registry: AdapterRegistry,
    wanted: Sequence[str] = (),
) -> str:
    """Profile *source* (a named file source or a CSV/JSON/Parquet path).

    A source with more than ``MAX_TABLES`` tables prints a one-line overview
    per table instead, unless *wanted* names the tables to profile.

    Raises:
        ValueError: the source is unknown, not a local file source, or has no table.
    """
    inline = is_file_source_ref(source)
    try:
        config = {} if inline else adapter_registry.resolve_source_config(source)
    except DbtChartsError as exc:
        raise ValueError(str(exc)) from exc
    kind = (
        PurePosixPath(source).suffix.lstrip(".").lower()
        if inline
        else str(config["type"])
    )
    if kind not in PROFILE_SOURCE_TYPES:
        raise ValueError(
            f"source {source!r} is a {kind} source; a profile scans every table, "
            f"so it runs only on file sources ({', '.join(sorted(PROFILE_SOURCE_TYPES))}). "
            f"Look at one table with: dct query {source} 'SELECT * FROM <table> LIMIT 5'"
        )

    def run(sql: str) -> list[dict[str, Any]]:
        result = execute_query(
            sql,
            source=source,
            limit=MAX_QUERY_LIMIT,
            adapter_registry=adapter_registry,
        )
        if not result.success:
            raise ValueError("; ".join(result.errors))
        if result.truncated:
            raise ValueError(f"profile query returned more than {MAX_QUERY_LIMIT} rows")
        return result.data

    sqlite = kind == "sqlite"
    if kind in FILE_KINDS:
        # A file source's tables are its declared names; the connection's own
        # schema listing holds internal relation names instead.
        names = [PurePosixPath(source).stem] if inline else list(config["files"])
        tables = {
            name: [
                (r["column_name"], r["column_type"])
                for r in run(f"DESCRIBE {_quote(name)}")
            ]
            for name in names
        }
        refs = {name: _quote(name) for name in names}
    else:
        tables, refs = _tables(
            run(_SQLITE_COLUMNS_SQL if sqlite else _DUCKDB_COLUMNS_SQL)
        )
    if not tables:
        raise ValueError(f"source {source!r} has no tables")
    head = f"source {source} ({kind}): {len(tables)} table(s)"
    if wanted:
        missing = [name for name in wanted if name not in tables]
        if missing:
            raise ValueError(
                f"source {source!r} has no table {', '.join(missing)}; tables: "
                + ", ".join(tables)
            )
        shown = list(dict.fromkeys(wanted))
    elif len(tables) <= MAX_TABLES:
        shown = list(tables)
    else:
        return "\n".join([head, *_overview(source, run, tables, refs)])
    budgets = _column_budgets({name: len(tables[name]) for name in shown})
    out = [f'{head}; lines starting with ">" are sample rows']
    for name in shown:
        out += _profile_table(
            run, name, refs[name], tables[name], budgets[name], sqlite
        )
    return "\n".join(out)


def _overview(
    source: str,
    run: Callable[[str], list[dict[str, Any]]],
    tables: dict[str, list[tuple[str, str]]],
    refs: dict[str, str],
) -> list[str]:
    """One line per table (rows, columns) for a source too wide to profile whole."""
    names = list(tables)[:OVERVIEW_TABLES]
    counts = run(
        " UNION ALL ".join(
            f"SELECT {i} AS i, COUNT(*) AS n FROM {refs[name]}"
            for i, name in enumerate(names)
        )
    )
    rows = {row["i"]: row["n"] for row in counts}
    lines = [
        _clip(
            f"{name} ({rows[i]} rows): " + ", ".join(col for col, _ in tables[name]),
            OVERVIEW_WIDTH,
        )
        for i, name in enumerate(names)
    ]
    if len(tables) > len(names):
        rest = ", ".join(list(tables)[len(names) :])
        lines.append(
            _clip(f"+{len(tables) - len(names)} more tables: {rest}", OVERVIEW_WIDTH)
        )
    lines.append(
        f"profile tables in full: dct query {source} --table NAME [--table NAME ...]"
    )
    return lines


def _tables(
    rows: Sequence[dict[str, Any]],
) -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
    """Schema rows -> ({table: [(column, type)]}, {table: quoted SQL reference}).

    Non-main schemas qualify the display name; the reference quotes schema and
    table separately so a dot inside a name stays inside it.
    """
    tables: dict[str, list[tuple[str, str]]] = {}
    refs: dict[str, str] = {}
    for row in rows:
        main = row["s"] == "main"
        name = row["t"] if main else f"{row['s']}.{row['t']}"
        refs.setdefault(
            name, _quote(row["t"]) if main else f"{_quote(row['s'])}.{_quote(row['t'])}"
        )
        tables.setdefault(name, []).append((row["c"], row["ty"]))
    return tables, refs


def _column_budgets(column_counts: dict[str, int]) -> dict[str, int]:
    """Split the line budget over tables: small tables show all, large share the rest."""
    remaining = LINE_BUDGET - 1 - len(column_counts) * _TABLE_LINES
    remaining = max(remaining, 6 * len(column_counts))
    budgets: dict[str, int] = {}
    pending = len(column_counts)
    for name, count in sorted(column_counts.items(), key=lambda item: item[1]):
        budgets[name] = min(count, remaining // pending)
        remaining -= budgets[name]
        pending -= 1
    return budgets


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _kind(db_type: str) -> str:
    if is_complex(db_type) or "[" in db_type:
        return "nested"
    if is_numeric(db_type):
        return "number"
    if is_temporal(db_type):
        return "time"
    return "text"


def _has_date(db_type: str) -> bool:
    return any(part in db_type.upper() for part in ("DATE", "TIMESTAMP"))


def _median_sql(col: str, table: str, sqlite: bool) -> str:
    if not sqlite:
        return f"MEDIAN({col})"
    return (
        f"(SELECT {col} FROM {table} WHERE {col} IS NOT NULL ORDER BY {col} "
        f"LIMIT 1 OFFSET (SELECT (COUNT({col}) - 1) / 2 FROM {table}))"
    )


def _profile_table(
    run: Callable[[str], list[dict[str, Any]]],
    name: str,
    table: str,
    columns: list[tuple[str, str]],
    budget: int,
    sqlite: bool,
) -> list[str]:
    kinds = [_kind(ty) for _, ty in columns]
    exprs = ["COUNT(*) AS n"]
    for i, ((col, _), kind) in enumerate(zip(columns, kinds, strict=True)):
        q = _quote(col)
        exprs.append(f"COUNT({q}) AS nn{i}")
        if kind == "nested":
            continue
        exprs.append(f"COUNT(DISTINCT {q}) AS d{i}")
        if kind in ("number", "time"):
            exprs += [f"MIN({q}) AS lo{i}", f"MAX({q}) AS hi{i}"]
        if kind == "number":
            exprs.append(f"{_median_sql(q, table, sqlite)} AS med{i}")
    (stats,) = run(f"SELECT {', '.join(exprs)} FROM {table}")
    rows = stats["n"]

    branches: list[str] = []
    for i, ((col, _), kind) in enumerate(zip(columns, kinds, strict=True)):
        if kind in ("nested", "time") or not stats[f"d{i}"]:
            continue
        q = _quote(col)
        # Low-cardinality columns list every value; text keeps the commonest few.
        if kind == "number" and stats[f"d{i}"] > NUMBER_VALUES:
            continue
        cap = "" if stats[f"d{i}"] <= LOW_CARDINALITY else f" LIMIT {EXAMPLES}"
        branches.append(
            f"SELECT * FROM (SELECT {i} AS i, CAST({q} AS VARCHAR) AS v, "
            f"COUNT(*) AS n FROM {table} WHERE {q} IS NOT NULL "
            f"GROUP BY 2 ORDER BY 3 DESC, 2{cap}) AS b{i}"
        )
    values: dict[int, list[tuple[str, int]]] = {}
    if branches:
        for row in run(" UNION ALL ".join(branches)):
            values.setdefault(row["i"], []).append((row["v"], row["n"]))

    grains = _grains(run, table, columns, kinds, stats, sqlite)
    lines = [f"{name}: {rows} rows, {len(columns)} columns"]
    for i, (col, ty) in enumerate(columns[:budget]):
        lines.append(
            _clip(
                f"  {col} {ty}"
                + _nulls(rows, stats[f"nn{i}"])
                + _detail(kinds[i], stats, i, values.get(i, []), grains.get(i))
            )
        )
    if budget < len(columns):
        hidden = ", ".join(col for col, _ in columns[budget:])
        lines.append(_clip(f"  +{len(columns) - budget} more columns: {hidden}"))

    sample = run(f"SELECT * FROM {table} LIMIT {SAMPLE_ROWS}")
    names = [col for col, _ in columns[:SAMPLE_COLUMNS]]
    cells = [{k: _cell(v) for k, v in row.items()} for row in sample]
    lines += (f"> {line}" for line in rows_csv_lines(cells, names))
    return lines


def _grains(
    run: Callable[[str], list[dict[str, Any]]],
    table: str,
    columns: list[tuple[str, str]],
    kinds: list[str],
    stats: dict[str, Any],
    sqlite: bool,
) -> dict[int, str]:
    """Grain of each date column whose values are one per distinct day, else absent."""
    branches = []
    for i, ((col, ty), kind) in enumerate(zip(columns, kinds, strict=True)):
        # A bare TIME has no calendar day to take gaps between.
        if kind != "time" or stats[f"d{i}"] < 3 or not _has_date(ty):
            continue
        q = _quote(col)
        if sqlite:
            day, gap = (
                f"date({q})",
                "CAST(julianday(LEAD(d) OVER (ORDER BY d)) - julianday(d) AS INTEGER)",
            )
        else:
            day, gap = (
                f"CAST({q} AS DATE)",
                "date_diff('day', d, LEAD(d) OVER (ORDER BY d))",
            )
        branches.append(
            f"SELECT {i} AS i, MIN(g) AS lo, MAX(g) AS hi, COUNT(*) AS days FROM "
            f"(SELECT {gap} AS g FROM (SELECT DISTINCT {day} AS d FROM {table} "
            f"WHERE {day} IS NOT NULL) AS x) AS y"
        )
    grains: dict[int, str] = {}
    if branches:
        for row in run(" UNION ALL ".join(branches)):
            # More distinct timestamps than days means events, not a calendar grain.
            if row["days"] != stats[f"d{row['i']}"]:
                continue
            for grain, low, high in _GRAINS:
                if low <= row["lo"] and row["hi"] <= high:
                    grains[row["i"]] = grain
                    break
    return grains


def _nulls(rows: int, non_null: int) -> str:
    if non_null in (rows, 0):
        return ""
    return f"  {100 * (rows - non_null) / rows:.3g}% null"


def _detail(
    kind: str,
    stats: dict[str, Any],
    i: int,
    values: list[tuple[str, int]],
    grain: str | None,
) -> str:
    if kind == "nested":
        return ""
    distinct = stats[f"d{i}"]
    if not stats["n"]:
        return ""
    if not distinct:
        return "  all null"
    if kind == "time":
        span = f"  {_text(stats[f'lo{i}'])} .. {_text(stats[f'hi{i}'])}"
        return span + (f" {grain}" if grain else "")
    if distinct <= (NUMBER_VALUES if kind == "number" else LOW_CARDINALITY):
        return "  " + ", ".join(
            f"{_clip(v, CELL_WIDTH) or chr(34) * 2} {n}" for v, n in values
        )
    if kind == "number":
        low, high, median = (stats[f"{k}{i}"] for k in ("lo", "hi", "med"))
        return f"  {_text(low)} .. {_text(high)}, median {_text(median)}"
    return f"  {distinct} distinct, e.g. " + ", ".join(
        _clip(v, CELL_WIDTH) for v, _ in values
    )


def _text(value: Any) -> str:  # type-state: explicit_any — a raw query cell
    """A number or date as short display text."""
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.3f}".rstrip("0") if abs(value) >= 1 else f"{value:.4g}"
    return str(value).removesuffix(" 00:00:00")[:19]


def _cell(value: Any) -> str | None:  # type-state: explicit_any — a raw query cell
    if value is None:
        return None
    return _clip(_text(value) if not isinstance(value, str) else value, CELL_WIDTH)


def _clip(text: str, width: int = LINE_WIDTH) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."
