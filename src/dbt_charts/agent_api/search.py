"""Typed search verb for the agent API."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterator
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dbt_charts.agent_api.board_index import (
    BoardEntry,
    BoardIndex,
    open_board_index,
    read_board,
)
from dbt_charts.agent_api.paging import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    cursor_offset,
    paginate,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.diagnostics import Diagnostic

if TYPE_CHECKING:
    from dbt_charts.core.compile.config import ProjectSourcesConfig

from dbt_charts.core.project import (
    ALL_FILES_GLOB,
    CHARTS_SUBDIR,
    PROJECT_CONFIG_NAME,
    BoardLabel,
    Project,
    assert_relpath,
    board_roots,
    board_slug,
    glob_to_regex,
    is_package_relpath,
)

# A keyword search reads and parses at most this many board files per project,
# however many boards mention the query.
MAX_PARSED_CANDIDATES = 60

# Longest notes a hit carries. A hit is for picking a board, not reading one.
MAX_SUMMARY_CHARS = 140

# Longest matching line a file hit carries; a minified one-liner is not a hit.
MAX_HIT_TEXT_CHARS = 240

# What each non-board kind searches, as project-relative globs. Models are
# dbt's default ``models/`` tree; ``all`` is every file the project holds.
_KIND_GLOBS: dict[str, tuple[str, ...]] = {
    "meta": (f"{CHARTS_SUBDIR}/**/meta.yml", f"{CHARTS_SUBDIR}/**/meta.yaml"),
    "models": ("models/**/*.sql", "models/**/*.yml", "models/**/*.yaml"),
    "config": (PROJECT_CONFIG_NAME, "dbt_project.yml"),
    "all": (ALL_FILES_GLOB,),
}

# A host's access model: the subset of these project-relative files its caller
# may see.
PathVisibility = Callable[[list[str]], Collection[str]]


# Maintainer paths that must not appear in agent-facing search summaries.
_INTERNAL_REF_RE = re.compile(r"\s*;?\s*see\s+ai_notes/\S+", re.IGNORECASE)


def _agent_safe_summary(notes: str) -> str:
    """Strip maintainer-only references from board notes."""
    return _INTERNAL_REF_RE.sub("", notes).strip()


def brief(notes: str) -> str:
    """Notes as a hit prints them: maintainer references gone, one short run."""
    notes = _agent_safe_summary(notes)
    if len(notes) <= MAX_SUMMARY_CHARS:
        return notes
    return notes[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"


class SearchFilesArgs(BaseModel):
    """Search the project's files: its boards by default, or its folder defaults (`meta.yml`), dbt models, and config. `kind` picks what is searched: `boards` (the default; YAML and markdown boards), `meta` (every `meta.yml`, the folder defaults and themes), `models` (the dbt project's `.sql` and `.yml` model files, where a table's columns are described), `config` (`dbt_charts.yml` and `dbt_project.yml`), or `all` (every file). For `boards`, `text` is a keyword query and omitting it lists every board you can access; a keyword search returns ranked hits with a match score, the reasons it matched, `matched_charts`, the ids of the charts whose id or title matched, and `charts`, those same charts with the query each reads, and a listing is unscored, in path order. Each board hit is compact: title, notes, and two path forms named for the coordinate system they resolve in: board_path (relative to charts/; a package board's carries a `<pkg>/` namespace prefix) for render_board(path=board_path, chart=<chart id>, ...) and describe_board, and file_path (relative to the project root) for read_file. Reuse the matching field's value unchanged; do not convert between the two. A board hit carries no full chart list or SQL: call describe_board on the board for those. For every other kind, `text` is a case-insensitive substring and the result is the matching lines (`file_path`, `line`, `text`); omit it to list the files. `path` is a glob over project-relative paths (`*` within a folder, `**` across folders) that narrows any kind. All results page with `cursor`/`next`. Use to find existing boards and reuse validated query patterns before creating new ones, and to read a project's conventions with read_file."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["boards", "meta", "models", "config", "all"] = Field(
        default="boards",
        description=(
            "What to search: boards (default), meta (meta.yml folder defaults), "
            "models (dbt model .sql/.yml files), config (dbt_charts.yml, "
            "dbt_project.yml), or all (every file)."
        ),
    )
    path: str | None = Field(
        default=None,
        description=(
            "Glob over project-relative paths, e.g. 'charts/finance/**'. "
            "Narrows the kind; omit to search all of it."
        ),
    )
    text: str | None = Field(
        default=None,
        description=(
            "boards: keywords to match against board metadata. Other kinds: a "
            "case-insensitive substring to find in file contents. Omit (or pass "
            "an empty string) to list instead of searching."
        ),
    )
    tags: list[str] | None = Field(
        default=None,
        description="boards only: filter to boards with ALL of these tags",
    )
    limit: int | None = Field(
        default=None,
        description=(
            f"Maximum results to return (default {DEFAULT_PAGE_LIMIT}, "
            f"max {MAX_PAGE_LIMIT})"
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

    @field_validator("path")
    @classmethod
    def _path_stays_in_the_project(cls, path: str | None) -> str | None:
        if path is not None:
            assert_relpath(path)
        return path

    @model_validator(mode="after")
    def _tags_belong_to_boards(self) -> SearchFilesArgs:
        if self.tags and self.kind != "boards":
            raise ValueError("tags only apply to kind 'boards'")
        return self


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
            "unchanged to read_file/write_file/apply_patch."
        )
    )


class ChartMatch(BaseModel):
    """A chart a keyword matched, and the query behind it."""

    model_config = ConfigDict(frozen=True)

    name: str
    query: str | None = Field(
        default=None,
        description="The board query this chart reads; absent when the chart "
        "reads none or its own text does not name one.",
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
    charts: list[ChartMatch] = Field(
        description="The charts in `matched_charts`, each with the query it reads."
    )


class FileEntry(BaseModel):
    """A file a non-board search listed."""

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(
        description="Path relative to the project root. Pass unchanged to read_file."
    )


class FileHit(FileEntry):
    """A line of a file that matched ``text``."""

    line: int = Field(description="1-based line number.")
    text: str = Field(description="The matching line.")


class SourceEntry(BaseModel):
    """A source a query may name: its SQL dialect, or its ``type`` when the
    warehouse is only knowable at execute (file sources, ``dbt_profile``).
    ``default`` marks the one source SQL that names none runs on."""

    model_config = ConfigDict(frozen=True)

    name: str
    dialect: str | None = None
    type: str | None = None
    default: bool | None = None

    @model_validator(mode="after")
    def _one_of_dialect_or_type(self) -> SourceEntry:
        if (self.dialect is None) == (self.type is None):
            raise ValueError("a source entry carries exactly one of dialect or type")
        return self


def source_entries(config: ProjectSourcesConfig) -> list[SourceEntry]:
    # Deferred: naming a dialect reads the execution config, and `dct search`
    # prints no sources.
    from dbt_charts.core.compile.sql_guard import dialect_for_source

    entries: list[SourceEntry] = []
    for name in sorted(config.sources):
        dialect = dialect_for_source(name, config.sources)
        entries.append(
            SourceEntry(
                name=name,
                dialect=dialect,
                type=None if dialect is not None else config.sources[name]["type"],
                default=True if name == config.implicit_source else None,
            )
        )
    return entries


class SearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    errors: list[Diagnostic]
    results: list[BoardMatch | BoardListing | FileHit | FileEntry]
    sources: list[SourceEntry] | None = Field(
        description="The sources this project's SQL may name, for execute_sql; "
        "null when the project's `sources:` failed to load (the reason is in errors) "
        "or the caller did not ask for them."
    )
    next: str | None = Field(
        default=None,
        description="Cursor for the next page; absent on the last page.",
    )


def search_files(
    project: Project,
    *,
    kind: str = "boards",
    path: str | None = None,
    text: str | None = None,
    tags: list[str] | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    cursor: str | None = None,
    with_sources: bool = True,
) -> SearchResult:
    """Search a project's boards, or its meta/model/config/other files.

    See ``SearchFilesArgs`` for what each ``kind`` searches. Pages of ``limit``
    (default ``DEFAULT_PAGE_LIMIT``, max ``MAX_PAGE_LIMIT``) resume at
    ``cursor``. Hosts with a per-principal access model (Cloud) must not
    truncate before filtering — see ``search_file_hits``. ``with_sources``
    False leaves ``sources`` null for a caller that prints none.
    """
    limit = max(0, min(limit, MAX_PAGE_LIMIT))
    errors: list[Diagnostic] = []
    sources: list[SourceEntry] | None = None
    if with_sources:
        try:
            sources = source_entries(project.declared_sources)
        except CompilationError as e:
            # Searching never needed the sources, and `kind: config` is how an
            # agent finds the broken file, so report it rather than fail.
            errors.append(e.to_diagnostic(file=PROJECT_CONFIG_NAME))
    if limit == 0:
        return SearchResult(success=True, errors=errors, results=[], sources=sources)
    hits = search_file_hits(project, kind=kind, path=path, text=text, tags=tags)
    page, next_cursor = paginate(hits, limit=limit, cursor=cursor)
    return SearchResult(
        success=True, errors=errors, results=page, next=next_cursor, sources=sources
    )


def search_file_hits(
    project: Project,
    *,
    kind: str = "boards",
    path: str | None = None,
    text: str | None = None,
    tags: list[str] | None = None,
    visible: PathVisibility | None = None,
) -> list[BoardMatch | BoardListing | FileHit | FileEntry]:
    """Every hit for the search, in result order, unlimited.

    ``visible`` is the host's access model, applied to the candidate files
    BEFORE any cap or candidate cut, so a caller who cannot see the top hits
    still gets the next ones rather than a false empty result.
    """
    if path is not None:
        in_scope = glob_to_regex(path)
        host_visible = visible

        def visible_in_scope(paths: list[str]) -> Collection[str]:
            scoped = [p for p in paths if in_scope.match(p)]
            return scoped if host_visible is None else host_visible(scoped)

        visible = visible_in_scope

    if kind == "boards":
        return list(search_boards_hits(text or "", project, tags=tags, visible=visible))
    if kind not in _KIND_GLOBS:
        raise ValueError(
            f"unknown kind {kind!r}: one of boards, {', '.join(_KIND_GLOBS)}"
        )
    globs = _KIND_GLOBS[kind]
    if text is None or not text.strip():
        listed = sorted({pf.relpath for g in globs for pf in project.files.glob(g)})
        kept = _keep_visible(listed, visible)
        return [FileEntry(file_path=p) for p in listed if p in kept]
    lines = [
        hit for g in globs for hit in project.files.grep(text, glob=g, ignore_case=True)
    ]
    kept = _keep_visible(sorted({hit.relpath for hit in lines}), visible)
    return [
        FileHit(file_path=hit.relpath, line=hit.line_number, text=_clip(hit.line))
        for hit in sorted(lines, key=lambda h: (h.relpath, h.line_number))
        if hit.relpath in kept
    ]


def _clip(line: str) -> str:
    line = line.rstrip()
    if len(line) <= MAX_HIT_TEXT_CHARS:
        return line
    return line[: MAX_HIT_TEXT_CHARS - 1].rstrip() + "…"


def search_boards_hits(
    query: str,
    project: Project,
    *,
    tags: list[str] | None = None,
    visible: PathVisibility | None = None,
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
    that cut, reading each board's tags from ``board_labels``. A filesystem
    project answers both labels and parses from its ``BoardIndex``, reparsing
    only the boards edited since the last search.

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
        labels = _board_labels(project, open_board_index(project))
        kept = _keep_visible([label.file_path for label in labels], visible)
        return [
            BoardListing(
                title=label.title,
                summary=brief(label.notes),
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
                        summary=brief(entry["notes"]),
                        board_path=entry["board_path"],
                        file_path=entry["file_path"],
                        match_score=round(score, 2),
                        match_reasons=reasons,
                        matched_charts=matched_charts,
                        charts=[
                            ChartMatch(name=chart["id"], query=chart["query"])
                            for chart in entry["charts"]
                            if chart["id"] in matched_charts
                        ],
                    ),
                )
            )

    scored.sort(key=lambda pair: (-pair[0], pair[1].file_path))
    return [hit for _score, hit in scored]


def _keep_visible(paths: list[str], visible: PathVisibility | None) -> set[str]:
    return set(paths) if visible is None else set(visible(paths))


def _board_labels(project: Project, index: BoardIndex | None) -> list[BoardLabel]:
    return project.board_labels() if index is None else index.labels


def _parse_candidates(
    project: Project,
    query_tokens: list[str],
    required_tags: set[str],
    visible: PathVisibility | None,
) -> Iterator[BoardEntry]:
    """Index entries for the best-matching board files.

    The best ``MAX_PARSED_CANDIDATES`` by (words in the title or slug, words
    mentioned, lines mentioning them) are parsed. Tags narrow the files first,
    from ``board_labels``, which holds them without a read.
    """
    board_files = sorted(
        _keep_visible(
            [pf.relpath for pf in project.iter_boards() if not pf.is_meta], visible
        )
    )
    roots = board_roots(project)
    index = open_board_index(project)
    labels = {label.file_path: label for label in _board_labels(project, index)}

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
        entry = (
            read_board(project.path(relpath))[1]
            if index is None
            else index.entries.get(relpath)
        )
        if entry is not None:
            yield entry


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _score_entry(
    entry: BoardEntry, query_tokens: list[str]
) -> tuple[float, list[str], list[str]]:
    """Return (score, match reasons, ids of the charts that matched)."""
    score = 0.0
    reasons: list[str] = []
    matched: set[str] = set()

    title_tokens = _tokenize(entry["title"])
    notes_tokens = _tokenize(_agent_safe_summary(entry["notes"]))
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
