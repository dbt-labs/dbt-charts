"""Board metadata a search keeps between processes.

Each ``dct`` is a new process, and ranking a search takes every board's title
and tags, so a filesystem project keeps what parsing found in
``.dct/board_index.json``. A record stands until its board's ``file_version``
changes; then that board alone is parsed again. Only ``agent_api.search`` reads
the index, and a project with no folder to keep it in has none.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import dbt_charts
from dbt_charts.core.project import BoardLabel, ProjectPath, read_board_text

if TYPE_CHECKING:
    from dbt_charts.core.compile.parse.board_text import BoardText
    from dbt_charts.core.project import Project

# Bump when a record's shape or meaning changes within one release.
_FORMAT = 2


class ChartEntry(TypedDict):
    id: str
    title: str
    query: str | None


class BoardEntry(TypedDict):
    """What search scores on one board."""

    board_path: str
    file_path: str
    title: str
    notes: str
    tags: list[str]
    query_names: list[str]
    charts: list[ChartEntry]
    sql_snippets: list[str]


class _Record(TypedDict):
    #: None when the file could not be stat'd or read: such a record is never
    #: current, since mode changes and a repaired link move no version.
    version: str | None
    board_path: str
    title: str
    notes: str
    tags: list[str]
    entry: BoardEntry | None


@dataclass(frozen=True)
class BoardIndex:
    """Every board in the project, current as of opening."""

    labels: list[BoardLabel]
    entries: dict[str, BoardEntry]


def read_board(path: ProjectPath) -> tuple[BoardLabel, BoardEntry | None]:
    """Parse one board file; the entry is None when it declares no board."""
    label, text, _readable = read_board_text(path)
    return label, _board_entry(label, text)


def open_board_index(project: Project) -> BoardIndex | None:
    """The project's index, brought up to date, or None if it keeps none."""
    # tach-ignore(agent_api->cli: runtime host-type guard; only a filesystem project has a folder to keep the index in)
    from dbt_charts.cli.filesystem_project import LOCAL_STATE_DIR, FilesystemProject

    if not isinstance(project, FilesystemProject):
        return None
    index_file = project.root / LOCAL_STATE_DIR / "board_index.json"
    known = _load(index_file)
    records: dict[str, _Record] = {}
    for path in sorted(project.iter_boards(), key=lambda p: p.relpath):
        if path.is_meta:
            continue
        try:
            version = project.file_version(path.relpath)
        except OSError:
            version = None
        record = known.get(path.relpath)
        if record is None or version is None or record["version"] != version:
            record = _record(path, version)
        records[path.relpath] = record
    if records != known:
        _save(index_file, records)
    return BoardIndex(
        labels=[
            BoardLabel(
                relpath,
                record["board_path"],
                record["title"],
                record["notes"],
                tuple(record["tags"]),
            )
            for relpath, record in records.items()
        ],
        entries={
            relpath: record["entry"]
            for relpath, record in records.items()
            if record["entry"] is not None
        },
    )


def _record(path: ProjectPath, version: str | None) -> _Record:
    label, text, readable = read_board_text(path)
    return _Record(
        version=version if readable else None,
        board_path=label.board_path,
        title=label.title,
        notes=label.notes,
        tags=list(label.tags),
        entry=_board_entry(label, text),
    )


def _board_entry(label: BoardLabel, text: BoardText) -> BoardEntry | None:
    # Deferred: this reads `AuthoredBoard`'s facets, and building the board models
    # is the slowest thing a `dct` start can do. A current index never gets here.
    from dbt_charts.agent_api.boards import board_declaring_keys

    content = text.board
    if not any(key in content for key in board_declaring_keys()):
        return None

    queries = content.get("queries", {})
    charts = content.get("charts", {})

    sql_snippets: list[str] = []
    query_names: list[str] = []
    if isinstance(queries, dict):
        for qname, qdef in queries.items():
            query_names.append(str(qname))
            sql = qdef.get("sql") if isinstance(qdef, dict) else None
            if isinstance(sql, str) and sql:
                sql_snippets.append(sql)

    chart_entries: list[ChartEntry] = []
    if isinstance(charts, dict):
        for raw_chart_id, chart_def in charts.items():
            # YAML mapping keys need not be strings (`2024:` parses as int).
            chart_id = str(raw_chart_id)
            # Search reads raw, possibly-invalid YAML: only a string title is
            # usable here.
            chart_title = (
                chart_def.get("title") if isinstance(chart_def, dict) else None
            )
            chart_entries.append(
                ChartEntry(
                    id=chart_id,
                    title=chart_title
                    if isinstance(chart_title, str) and chart_title
                    else chart_id,
                    query=_chart_query(chart_id, chart_def, query_names),
                )
            )

    return BoardEntry(
        board_path=label.board_path,
        file_path=label.file_path,
        title=text.title,
        notes=text.notes,
        tags=list(text.tags),
        query_names=query_names,
        charts=chart_entries,
        sql_snippets=sql_snippets,
    )


def _chart_query(
    chart_id: str, chart_def: object, query_names: list[str]
) -> str | None:
    """The name `dct query` takes for what the chart reads, or None when the chart's
    own text does not say.

    Compile looks a string up in a registry that also holds `meta.yml` and `extends`
    queries, which this raw read cannot see, so only more than one word counts as
    SQL here. An off-board query whose name has a space and starts like SQL reads
    as SQL.
    """
    from dbt_charts.core.compile.normalize.variables import synthetic_query_name
    from dbt_charts.core.compile.parse.parser import looks_like_sql

    query = chart_def.get("query") if isinstance(chart_def, dict) else None
    if isinstance(query, dict):
        return synthetic_query_name("inline_query", chart_id)
    if not isinstance(query, str):
        return None
    name = query.removeprefix("queries.")
    is_sql = len(name.split()) > 1 and looks_like_sql(name)
    if "#" not in name and name not in query_names and is_sql:
        return synthetic_query_name("inline_query", chart_id)
    return name


def _load(index_file: Path) -> dict[str, _Record]:
    """The records on disk, or nothing to trust: the index is only ever a cache."""
    try:
        document = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict) or document.get("key") != _key():
        return {}
    boards = document.get("boards")
    return boards if isinstance(boards, dict) else {}


def _save(index_file: Path, records: dict[str, _Record]) -> None:
    folder = index_file.parent
    written = folder / f"{index_file.name}.{os.getpid()}"
    try:
        folder.mkdir(exist_ok=True)
        ignore = folder / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
        written.write_text(
            json.dumps({"key": _key(), "boards": records}), encoding="utf-8"
        )
        Path(written).replace(index_file)
    except OSError:
        return  # a read-only project still searches, it just parses every call


def _key() -> str:
    """A parse from another release or format is not this one's parse."""
    return f"{_FORMAT}:{dbt_charts.__version__}"
