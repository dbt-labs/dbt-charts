"""Typed search verb for the agent API."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterator, Sequence
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dbt_charts.agent_api.boards import board_declaring_keys
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.diagnostics.execution import ExecutionError

if TYPE_CHECKING:
    from dbt_charts.core.compile.parse.board_text import BoardText

from dbt_charts.core.project import (
    Project,
    board_roots,
    board_slug,
    is_package_relpath,
)

# A typical account lists in one page; a compact listing hit is a few hundred bytes.
MAX_SEARCH_LIMIT = 200
DEFAULT_SEARCH_LIMIT = 100

# A keyword search reads and parses at most this many board files per project,
# however many boards mention the query.
MAX_PARSED_CANDIDATES = 60

# Longest notes a hit carries. A hit is for picking a board, not reading one.
MAX_SUMMARY_CHARS = 140

# Maintainer paths that must not appear in agent-facing search summaries.
_INTERNAL_REF_RE = re.compile(r"\s*;?\s*see\s+ai_notes/\S+", re.IGNORECASE)


_T = TypeVar("_T")

# A host's access model: the subset of these project-relative board files its
# caller may see.
BoardVisibility = Callable[[list[str]], Collection[str]]


def cursor_offset(cursor: str | None) -> int:
    """Decode a ``next`` cursor to the offset it resumes at; ``None`` is the start."""
    if cursor is None:
        return 0
    if not cursor.isdecimal():
        raise ValueError(
            f"{cursor!r} is not a cursor: pass the 'next' value from the "
            "previous search_boards result unchanged"
        )
    return int(cursor)


def paginate(
    items: Sequence[_T], *, limit: int, cursor: str | None
) -> tuple[list[_T], str | None]:
    """One page of an already-sorted, already-filtered list, and the cursor to resume at.

    The cursor is ``None`` on the last page. Every host pages through this one
    function so a cursor means the same thing on the connector, in chat, and
    over stdio.
    """
    if limit == 0:
        return [], None  # a cursor here would resume at the same offset forever
    start = cursor_offset(cursor)
    end = start + limit
    return list(items[start:end]), (str(end) if end < len(items) else None)


def _agent_safe_summary(notes: str) -> str:
    """Strip maintainer-only references from board notes."""
    return _INTERNAL_REF_RE.sub("", notes).strip()


def _brief(notes: str) -> str:
    """Notes as a hit prints them: maintainer references gone, one short run."""
    notes = _agent_safe_summary(notes)
    if len(notes) <= MAX_SUMMARY_CHARS:
        return notes
    return notes[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"


class SearchBoardsArgs(BaseModel):
    """Search existing dashboards by keyword, or list them all with an empty query. A keyword search returns ranked hits with a match score, the reasons it matched, and `matched_charts`, the ids of the charts whose id or title matched; a listing is unscored, in path order. Both page with `cursor`/`next`. Each hit is compact: title, notes, and two path forms named for the coordinate system they resolve in: board_path (relative to charts/; a package board's carries a `<pkg>/` namespace prefix) for render_board(path=board_path, chart=<chart id>, ...) and describe_board, and file_path (relative to the project root) for read_file/write_file/edit_file. Reuse the matching field's value unchanged; do not convert between the two. A hit carries no chart list, query names or SQL: call describe_board on the board for those. Use to discover relevant dashboards and reuse validated query patterns before creating new ones."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        description=(
            "Search query text (keywords to match against dashboard metadata). "
            "Pass an empty string to list every board you can access instead "
            "of searching."
        ),
    )
    tags: list[str] | None = Field(
        default=None, description="Filter results to dashboards with ALL of these tags"
    )
    limit: int | None = Field(
        default=None,
        description=(
            f"Maximum results to return (default {DEFAULT_SEARCH_LIMIT}, "
            f"max {MAX_SEARCH_LIMIT})"
        ),
    )
    cursor: str | None = Field(
        default=None,
        description=(
            "Pass the previous result's `next` value, unchanged, to get the "
            "following page."
        ),
    )

    @field_validator("cursor")
    @classmethod
    def _cursor_is_a_next_value(cls, cursor: str | None) -> str | None:
        cursor_offset(cursor)
        return cursor


class BoardListing(BaseModel):
    """What a listing prints for a board: enough to pick one."""

    model_config = ConfigDict(frozen=True)

    title: str
    summary: str
    board_path: str = Field(
        description=(
            "Path relative to charts/ for a host board. A package board's "
            "path carries a `<pkg>/` namespace prefix (dbt_packages/pkg_a/"
            'charts/overview.yml -> "pkg_a/overview.yml") since that prefix '
            "is required for render_board(path=...) to resolve a package "
            "board. Pass unchanged to render_board(path=...)."
        )
    )
    file_path: str = Field(
        description=(
            "Path relative to the project root (charts/ prefix kept). Pass "
            "unchanged to read_file/write_file/edit_file."
        )
    )


class BoardMatch(BoardListing):
    """A keyword hit: a listing entry plus why it ranked."""

    match_score: float
    match_reasons: list[str]
    matched_charts: list[str] = Field(
        description=(
            "Ids of the charts whose id or title matched the query, in board "
            "order. Empty when the board matched on other fields."
        )
    )


class SearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    errors: list[Diagnostic]
    results: list[BoardMatch | BoardListing]
    next: str | None = Field(
        default=None,
        description="Cursor for the next page; absent on the last page.",
    )


def search_boards(
    query: str,
    project: Project,
    *,
    tags: list[str] | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    cursor: str | None = None,
) -> SearchResult:
    """Search dashboards by keyword with ranked results, or list them all.

    An empty ``query`` lists every board by path instead of ranking. Searches
    every board root. Pages of ``limit`` (default ``DEFAULT_SEARCH_LIMIT``, max
    ``MAX_SEARCH_LIMIT``) resume at ``cursor``. Hosts with a per-principal
    access model (Cloud) must not truncate before filtering — see
    ``search_boards_hits``.
    """
    limit = max(0, min(limit, MAX_SEARCH_LIMIT))
    if limit == 0:
        return SearchResult(success=True, errors=[], results=[])
    page, next_cursor = paginate(
        search_boards_hits(query, project, tags=tags), limit=limit, cursor=cursor
    )
    return SearchResult(success=True, errors=[], results=page, next=next_cursor)


def search_boards_hits(
    query: str,
    project: Project,
    *,
    tags: list[str] | None = None,
    visible: BoardVisibility | None = None,
) -> list[BoardMatch | BoardListing]:
    """Every board matching ``query``, best-first, unlimited.

    An empty ``query`` is a listing, not a search: every board, unscored, in
    path order, titled from ``Project.board_labels`` so a host that stores
    titles and tags pays for no per-board read.

    A keyword search never reads the whole project. Candidates are the board
    files whose text (``project.files.grep``, case-insensitive, one pass per
    query word) or path mentions a word; only the ``MAX_PARSED_CANDIDATES``
    best (boards named for a query word first, then by words mentioned) are
    read, parsed and scored. A query broad enough to name more boards than
    that ranks only that slice. A ``tags`` filter narrows the candidates before
    that cut, reading each board's tags from ``board_labels``.

    This layer has no principal concept (agent_api thin-wrapper rule), so it
    cannot itself decide which hits a caller may see. A host with access
    control (Cloud) calls this directly and passes ``visible``, which narrows
    the board files BEFORE the candidate cap applies, then truncates to the
    caller's ``limit`` — filtering an already-capped result would under-fill:
    a viewer who cannot see the top hits but can see the next few would get a
    false empty result.
    """
    query_stripped = query.strip()
    query_tokens = sorted(_tokenize(query_stripped))
    if query_stripped and not query_tokens:
        return []
    required_tags = {t.lower() for t in tags or []}

    if not query_stripped:
        labels = project.board_labels()
        kept = _keep_visible([label.file_path for label in labels], visible)
        return [
            BoardListing(
                title=label.title,
                summary=_brief(label.notes),
                board_path=label.board_path,
                file_path=label.file_path,
            )
            for label in labels
            if label.file_path in kept and required_tags.issubset(label.tags)
        ]

    scored: list[tuple[float, BoardMatch]] = []
    for entry in _parse_candidates(project, query_tokens, required_tags, visible):
        if not required_tags.issubset(entry["tags"]):
            continue
        score, reasons, matched_charts = _score_entry(entry, query_tokens)
        if score > 0:
            scored.append(
                (
                    score,
                    BoardMatch(
                        title=entry["title"],
                        summary=_brief(entry["notes"]),
                        board_path=entry["board_path"],
                        file_path=entry["file_path"],
                        match_score=round(score, 2),
                        match_reasons=reasons,
                        matched_charts=matched_charts,
                    ),
                )
            )

    scored.sort(key=lambda pair: (-pair[0], pair[1].file_path))
    return [hit for _score, hit in scored]


def _keep_visible(paths: list[str], visible: BoardVisibility | None) -> set[str]:
    return set(paths) if visible is None else set(visible(paths))


def _parse_candidates(
    project: Project,
    query_tokens: list[str],
    required_tags: set[str],
    visible: BoardVisibility | None,
) -> Iterator[dict[str, Any]]:
    """Index entries for the best-matching board files.

    The best ``MAX_PARSED_CANDIDATES`` by (words in the title or slug, words
    mentioned, lines mentioning them) are parsed. Tags narrow the files first,
    from ``board_labels``, which holds them without a read.
    """
    # Deferred: importing the compiler here would load it for every CLI start.
    from dbt_charts.core.compile.parse.board_text import parse_board_text

    board_files = sorted(
        _keep_visible(
            [pf.relpath for pf in project.iter_boards() if not pf.is_meta], visible
        )
    )
    roots = board_roots(project)
    labels = {label.file_path: label for label in project.board_labels()}

    # word -> {board file -> lines mentioning it}; a file named for a word is
    # present with zero lines.
    mentions: dict[str, dict[str, int]] = {}
    for word in query_tokens:
        lines_by_file = dict.fromkeys(board_files, 0)
        for root in roots:
            for hit in project.files.grep(
                word, glob=f"{root.relpath}/**/*", ignore_case=True
            ):
                if hit.relpath in lines_by_file:
                    lines_by_file[hit.relpath] += 1
        mentions[word] = {
            relpath: lines
            for relpath, lines in lines_by_file.items()
            if lines or word in relpath.lower()
        }

    # The text pass never enters dbt_packages, so an installed package's boards
    # cannot be narrowed by it: they are always candidates (a project installs
    # few), and scoring decides.
    package_boards = [f for f in board_files if is_package_relpath(f)]
    eligible = {
        relpath
        for relpath in board_files
        if not is_package_relpath(relpath)
        and relpath in labels
        and required_tags.issubset(labels[relpath].tags)
        and any(relpath in mentions[word] for word in query_tokens)
    }

    def named_words(relpath: str) -> int:
        named = _tokenize(f"{labels[relpath].title} {board_slug(relpath)}")
        return sum(word in named for word in query_tokens)

    candidates = sorted(
        eligible,
        key=lambda relpath: (
            -named_words(relpath),
            -sum(relpath in mentions[word] for word in query_tokens),
            -sum(mentions[word].get(relpath, 0) for word in query_tokens),
            relpath,
        ),
    )[:MAX_PARSED_CANDIDATES]

    for relpath in [*candidates, *package_boards]:
        pf = project.path(relpath)
        try:
            content = pf.read_text()
        except (OSError, UnicodeDecodeError, ExecutionError):
            continue  # not readable as text: nothing to score
        slug = board_slug(relpath)
        text = parse_board_text(slug, content, is_markdown=pf.is_markdown)
        entry = _index_entry_from_content(
            board_path=f"{slug}{PurePosixPath(relpath).suffix}",
            file_path=relpath,
            text=text,
        )
        if entry is not None:
            yield entry


def _index_entry_from_content(
    board_path: str, file_path: str, text: BoardText
) -> dict[str, Any] | None:
    content = text.board
    if not any(key in content for key in board_declaring_keys()):
        return None

    queries = content.get("queries", {})
    charts = content.get("charts", {})

    sql_snippets: list[str] = []
    query_names: list[str] = []
    if isinstance(queries, dict):
        for qname, qdef in queries.items():
            query_names.append(qname)
            if isinstance(qdef, dict) and qdef.get("sql"):
                sql_snippets.append(qdef["sql"])

    chart_entries: list[dict[str, str]] = []
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
                {
                    "id": chart_id,
                    "title": (
                        chart_title
                        if isinstance(chart_title, str) and chart_title
                        else chart_id
                    ),
                }
            )

    return {
        "board_path": board_path,
        "file_path": file_path,
        "title": text.title,
        "notes": _agent_safe_summary(text.notes),
        "tags": set(text.tags),
        "query_names": query_names,
        "charts": chart_entries,
        "sql_snippets": sql_snippets,
    }


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _score_entry(
    entry: dict[str, Any], query_tokens: list[str]
) -> tuple[float, list[str], list[str]]:
    """Return (score, match reasons, ids of the charts that matched)."""
    score = 0.0
    reasons: list[str] = []
    matched: set[str] = set()

    title_tokens = _tokenize(entry["title"])
    notes_tokens = _tokenize(entry["notes"])
    tag_set = entry["tags"]
    query_name_tokens = _tokenize(" ".join(entry["query_names"]))
    sql_tokens = _tokenize(" ".join(entry["sql_snippets"]))
    chart_tokens = [
        (chart["id"], _tokenize(chart["title"]) | _tokenize(chart["id"]))
        for chart in entry["charts"]
    ]

    for qt in query_tokens:
        if qt in title_tokens:
            score += 3.0
            if "title_match" not in reasons:
                reasons.append("title_match")

        if qt in tag_set:
            score += 2.5
            if "tag_match" not in reasons:
                reasons.append("tag_match")

        if qt in notes_tokens:
            score += 1.5
            if "notes_match" not in reasons:
                reasons.append("notes_match")

        for chart_id, tokens in chart_tokens:
            if qt in tokens:
                score += 2.0
                matched.add(chart_id)
                if "chart_match" not in reasons:
                    reasons.append("chart_match")

        if qt in query_name_tokens:
            score += 1.0
            if "metric_overlap" not in reasons:
                reasons.append("metric_overlap")

        if qt in sql_tokens:
            score += 0.5
            if "sql_match" not in reasons:
                reasons.append("sql_match")

    matched_charts = [chart_id for chart_id, _ in chart_tokens if chart_id in matched]
    return score, reasons, matched_charts
