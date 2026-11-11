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
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import dbt_charts
from dbt_charts.core.project import BoardLabel, ProjectPath, read_board_text

if TYPE_CHECKING:
    from dbt_charts.core.compile.engine import EngineIndex
    from dbt_charts.core.compile.parse.board_text import BoardText
    from dbt_charts.core.project import Project

# Bump when a record's shape or meaning changes within one release.
_FORMAT = 4


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
    #: The version of every other file the engine read to compile the board (an
    #: `extends:` target, a `meta.yml`, `dbt_charts.yml`), "" for one that was absent.
    reads: dict[str, str]
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
    label, text, _readable, index = read_board_text(path)
    return label, _board_entry(label, text, index)


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
        if (
            record is None
            or version is None
            or record["version"] != version
            or record["reads"] != _versions(project, record["reads"])
        ):
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


def _versions(project: Project, relpaths: Iterable[str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for relpath in relpaths:
        try:
            versions[relpath] = project.file_version(relpath)
        except OSError:
            versions[relpath] = ""
    return versions


def _record(path: ProjectPath, version: str | None) -> _Record:
    label, text, readable, index = read_board_text(path)
    reads: dict[str, str] = {}
    if index is not None:
        # The sources the compile ran against come from `dbt_charts.yml`, and a dbt
        # project's implicit one from `dbt_project.yml`, though the engine reads neither.
        reads = _versions(
            path.project,
            {*index["files"], "dbt_charts.yml", "dbt_project.yml"} - {path.relpath},
        )
    # A board the engine refused may be refused for what another file holds, which
    # no version here tracks: it is read again each call. Markdown is never compiled.
    known = index is not None or path.is_markdown
    return _Record(
        version=version if readable and known else None,
        reads=reads,
        board_path=label.board_path,
        title=label.title,
        notes=label.notes,
        tags=list(label.tags),
        entry=_board_entry(label, text, index),
    )


def _board_entry(
    label: BoardLabel, text: BoardText, index: EngineIndex | None
) -> BoardEntry | None:
    """The entry for a file that is a board, None for one that is not.

    A board the engine does not read (markdown, or one that does not fold) is
    entered from its text alone, with no queries or charts: search never guesses
    them.
    """
    queries = charts = []
    if index is not None:
        queries, charts = index["queries"], index["charts"]
    else:
        # Deferred: this reads `AuthoredBoard`'s facets, and building the board models
        # is the slowest thing a `dct` start can do. A current index never gets here.
        from dbt_charts.agent_api.boards import board_declaring_keys

        if not any(key in text.board for key in board_declaring_keys()):
            return None
    return BoardEntry(
        board_path=label.board_path,
        file_path=label.file_path,
        title=text.title,
        notes=text.notes,
        tags=list(text.tags),
        query_names=[q["name"] for q in queries],
        charts=[
            ChartEntry(id=c["id"], title=c["title"] or c["id"], query=c["query"])
            for c in charts
        ],
        sql_snippets=[q["sql"] for q in queries if q["sql"]],
    )


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
