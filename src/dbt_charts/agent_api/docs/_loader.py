"""Docs verb for the agent API — slices DBT_CHARTS_SYNTAX.md, serves the
generated references beside it.

`DBT_CHARTS_SYNTAX.md` is the hand-authored source. H2 headings define topics;
topic IDs are slugified headings. Bare `docs()` returns the topic index (slug +
one-line description per H2, then one entry per generated reference).
`docs(topic="<slug>")` returns one slice. `docs(topic="all")` returns the
syntax file plus the generated field reference — the unsliced read must not
omit grammar keys only the reference documents. `docs(topic="reference")`
returns the field spec rendered from the compiler's models.
`docs(topic="error-reference")` / `docs(topic="warning-reference")` return the
diagnostic references rendered from `diagnostics.REGISTRY`. The generated
references are derived on first read, never shipped as files, so they cannot
drift from the code they describe.
`docs(search=...)` ranks H2/H3 units of the syntax file and the generated
references with BM25.
"""

from __future__ import annotations

import difflib
import functools
import importlib.resources
import math
import re
from collections import Counter
from itertools import zip_longest
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.chart.authored import SUPPORTED_AUTHORED_CHART_TYPES
from dbt_charts.core.compile.schema import get_schema_for_prompt
from dbt_charts.core.diagnostics.render_reference import render_reference

_SYNTAX_FILE = importlib.resources.files("dbt_charts") / "DBT_CHARTS_SYNTAX.md"

_TOPIC_RE = re.compile(r"^[a-z0-9-]+$")
_ALL_TOPIC = "all"
_REFERENCE_TOPIC = "reference"
_ERROR_REFERENCE_TOPIC = "error-reference"
_WARNING_REFERENCE_TOPIC = "warning-reference"
_DIAGNOSTIC_LEVELS: dict[str, Literal["error", "warning"]] = {
    _ERROR_REFERENCE_TOPIC: "error",
    _WARNING_REFERENCE_TOPIC: "warning",
}
_CHARTS_TOPIC = "charts"

DocsMode = Literal["index", "topic", "search"]


class DocsCorpusMissingError(RuntimeError):
    """The bundled syntax guide is unreachable. Indicates a broken wheel install."""


class Topic(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    content: str = ""


class TopicEntry(BaseModel):
    """One row in the docs topic index: slug + heading + first-line description."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    description: str = ""


class DocsSearchHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    topic: str = Field(description="Topic slug accepted by `docs(topic=...)`")
    title: str = Field(description="H2 heading of the topic")
    section: str = Field(description="Nearest heading (H3 or H2) around the hit")
    score: float
    content: str = Field(
        description="The whole matched unit (an H3 subsection, or an H2's text before its first H3), heading included"
    )


class DocsArgs(BaseModel):
    """Browse the dbt charts YAML reference offline. Modes: no args = topic index (one row per topic), topic='<slug>' = one section, topic='all' = the syntax guide plus the generated field reference, unsliced, search='<query>' = ranked term search across every section and the generated references. Use this before writing YAML to learn field names, valid values, and examples. Call with no args first to see the available topics."""

    topic: str | None = Field(
        None,
        description="Topic slug from the `dct docs` topic index (e.g. 'board', 'charts', 'all', 'reference', 'error-reference', 'warning-reference'). With `search`, scopes the hits to that topic.",
    )
    search: str | None = Field(
        None,
        description="Free-text query — BM25-ranked across every topic section and the generated field/error/warning references; multi-word queries match on terms, not the exact phrase",
    )
    limit: int = Field(5, ge=1, le=20, description="Max search hits to return")

    model_config = ConfigDict(extra="forbid")


class DocsResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool = True
    mode: DocsMode
    topic: Topic | None = None
    search: list[DocsSearchHit] = []
    topics: list[TopicEntry] = []
    errors: list[str] = []
    hints: list[str] = []


def docs(
    topic: str | None = None,
    search: str | None = None,
    limit: int = 5,
) -> DocsResult:
    """Look up a docs topic, fetch the whole file, or search the corpus.

    - ``docs()`` — index mode: one entry per H2 with first-line description,
      then one per generated reference.
    - ``docs(topic="board")`` — return content for one H2 section.
    - ``docs(topic="all")`` — return ``DBT_CHARTS_SYNTAX.md`` plus the generated
      field reference, unsliced.
    - ``docs(topic="reference")`` — return the auto-generated field spec.
    - ``docs(topic="error-reference")`` / ``docs(topic="warning-reference")`` —
      return the auto-generated diagnostic reference.
    - ``docs(search="grid")`` — BM25-ranked hits over H2/H3 units of every
      topic plus the generated references; ``topic`` scopes the hits.
    """
    if search is not None:
        if topic == _ALL_TOPIC:
            topic = None
        if topic is not None and topic not in _search_scopes():
            return DocsResult(
                success=False,
                mode="search",
                errors=[f"Unknown topic: {topic}"],
                hints=[
                    "Run `dct docs` for the topic index, or search without a topic to cover everything"
                ],
            )
        try:
            return DocsResult(mode="search", search=_search(search, limit, topic))
        except DocsCorpusMissingError as exc:
            # An envelope, not a raise, so `--json` stays JSON on a broken install.
            return DocsResult(success=False, mode="search", errors=[str(exc)])

    if topic is None:
        return DocsResult(mode="index", topics=_topic_index())

    if topic == _ALL_TOPIC:
        # "Unsliced" has to mean it: the hand-authored guide alone omits every
        # grammar key only the generated field reference documents.
        return DocsResult(
            mode="topic",
            topic=Topic(
                id=_ALL_TOPIC,
                title="dbt charts YAML Syntax + Field Reference",
                content=f"{read_full_text()}\n{_generated_text(_REFERENCE_TOPIC)}",
            ),
        )

    if topic in _GENERATED_TOPICS:
        return DocsResult(
            mode="topic",
            topic=Topic(
                id=topic,
                title=_GENERATED_TOPICS[topic].title,
                content=_generated_text(topic),
            ),
        )

    if topic in SUPPORTED_AUTHORED_CHART_TYPES:
        # Chart types (bar, kpi, heatmap, spark_bar, ...) are documented as
        # part of the `charts` H2, not one H2 each, and some (spark_bar,
        # point_map, bubble_map) contain underscores _TOPIC_RE rejects --
        # route the type name to that topic instead of "Unknown topic" /
        # "Invalid topic id", keeping the requested id on the result.
        title, body = _load_sections()[_CHARTS_TOPIC]
        return DocsResult(
            mode="topic", topic=Topic(id=topic, title=title, content=body)
        )

    if not _TOPIC_RE.fullmatch(topic):
        return DocsResult(
            success=False,
            mode="topic",
            errors=[f"Invalid topic id: {topic!r}"],
            hints=[
                "Topic ids match [a-z0-9-]+ (lowercased H2 headings); run `dct docs` for the topic index"
            ],
        )

    sections = _load_sections()
    if topic not in sections:
        all_topics = list(sections.keys())
        close = difflib.get_close_matches(topic, all_topics, n=3, cutoff=0.6) or list(
            dict.fromkeys(hit.topic for hit in _search(topic, 3, None))
        )
        hints = (
            [f"Did you mean: {', '.join(close)}"]
            if close
            else ["Run `dct docs` for the topic index"]
        )
        return DocsResult(
            success=False,
            mode="topic",
            errors=[f"Unknown topic: {topic}"],
            hints=hints,
        )

    title, body = sections[topic]
    return DocsResult(mode="topic", topic=Topic(id=topic, title=title, content=body))


def read_full_text() -> str:
    """Return the raw ``DBT_CHARTS_SYNTAX.md`` content unsliced.

    Shared file-load helper for callers that want the hand-authored guide: the
    agent system prompt and the playground AI service.
    """
    try:
        return _SYNTAX_FILE.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DocsCorpusMissingError(
            f"Docs source file missing (path={_SYNTAX_FILE}). "
            "The dbt charts wheel is broken or the file was monkeypatched away."
        ) from exc


def slugify(heading: str) -> str:
    """Slugify an H2 heading to a topic ID.

    Locked-in rule: lowercase, strip leading `#`, trim whitespace, replace
    runs of whitespace with single hyphens, keep ASCII letters/digits/hyphens,
    drop everything else.
    """
    text = heading.lstrip("#").strip().lower()
    text = re.sub(r"\s+", "-", text)
    return re.sub(r"[^a-z0-9-]+", "", text)


class _GeneratedTopic(NamedTuple):
    """How the topic index describes one generated reference."""

    title: str
    description: str


_GENERATED_TOPICS = {
    _REFERENCE_TOPIC: _GeneratedTopic(
        "dbt charts YAML Field Reference (generated)",
        "Every board field, generated from the compiler's own models.",
    ),
    _ERROR_REFERENCE_TOPIC: _GeneratedTopic(
        "dbt charts Error Reference (generated)",
        "Every ERR- code, with what raises it and how to fix it.",
    ),
    _WARNING_REFERENCE_TOPIC: _GeneratedTopic(
        "dbt charts Warning Reference (generated)",
        "Every WARN- code, with what raises it and how to fix it.",
    ),
}


@functools.cache
def _generated_text(topic: str) -> str:
    """Render one generated reference; cached because search reads all three
    on every call and the field reference walks every model."""
    if topic == _REFERENCE_TOPIC:
        return get_schema_for_prompt() + "\n"
    return render_reference(_DIAGNOSTIC_LEVELS[topic], with_anchors=False)


def _slice(text: str, marker: str) -> list[tuple[str, str]]:
    """Split markdown into ``(heading, body)`` at lines starting with ``marker``.

    Body starts at the heading line and runs up to (not including) the next
    heading at that level. Text before the first heading is dropped. Order
    matches the file.
    """
    sections: list[tuple[str, str]] = []
    title: str | None = None
    chunk: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith(marker):
            if title is not None:
                sections.append((title, "".join(chunk).rstrip() + "\n"))
            title = line[len(marker) :].strip()
            chunk = [line]
        elif title is not None:
            chunk.append(line)
    if title is not None:
        sections.append((title, "".join(chunk).rstrip() + "\n"))
    return sections


def _load_sections() -> dict[str, tuple[str, str]]:
    """Slice DBT_CHARTS_SYNTAX.md on H2 headers into ``{slug: (title, body)}``."""
    return {
        slugify(title): (title, body) for title, body in _slice(read_full_text(), "## ")
    }


def _first_description_line(body: str) -> str:
    """Return the first non-empty, non-heading prose line from a section body.

    The body starts with the H2 line itself. Skip the H2 line, skip blank
    lines, skip sub-headings (lines starting with `#`), and return the first
    real text line with minimal markdown stripping (backticks, bold/italic).
    """
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        return _strip_markdown(line)
    return ""


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_INLINE_RE = re.compile(r"[`*_]+")


def _strip_markdown(text: str) -> str:
    text = _MD_LINK_RE.sub(r"\1", text)
    return _MD_INLINE_RE.sub("", text).strip()


def _topic_index() -> list[TopicEntry]:
    """Return one TopicEntry per H2 section in file order, then one per
    generated reference."""
    return [
        TopicEntry(id=slug, title=title, description=_first_description_line(body))
        for slug, (title, body) in _load_sections().items()
    ] + [
        TopicEntry(id=slug, title=entry.title, description=entry.description)
        for slug, entry in _GENERATED_TOPICS.items()
    ]


class _Unit(NamedTuple):
    """One searchable slice: an H3 subsection, or an H2's text before its first H3."""

    topic: str
    title: str
    section: str
    body: str


_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_TITLE_BOOST = 3
_BM25_K1 = 1.2
_BM25_B = 0.75


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens; a snake_case field also yields its parts, so
    `axis y` finds `axis_y` and `axis_y` still matches exactly."""
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text.lower()):
        out.append(tok)
        if "_" in tok:
            out.extend(part for part in tok.split("_") if part)
    return out


def _units() -> list[_Unit]:
    units = [
        _Unit(slugify(title), title, section, body)
        for title, h2_body in _slice(read_full_text(), "## ")
        for section, body in _subsections(title, h2_body)
    ]
    for topic in _GENERATED_TOPICS:
        units.extend(
            _Unit(topic, title, section, body)
            for title, h2_body in _slice(_generated_text(topic), "## ")
            for section, body in _subsections(title, h2_body)
        )
    return units


def _subsections(title: str, h2_body: str) -> list[tuple[str, str]]:
    """Split an H2 body into its preamble and H3 units, dropping any with no
    prose (a bare `## charts` domain header in the diagnostics references)."""
    head, _, rest = h2_body.partition("\n### ")
    units = [(title, head)]
    if rest:
        units.extend(_slice("### " + rest, "### "))
    return [(section, body) for section, body in units if _first_description_line(body)]


def _search_scopes() -> set[str]:
    return set(_load_sections()) | set(_GENERATED_TOPICS)


def _source(unit: _Unit) -> str:
    if unit.topic in _DIAGNOSTIC_LEVELS:
        return "diagnostics"
    return "reference" if unit.topic == _REFERENCE_TOPIC else "syntax"


def _search(query: str, limit: int, scope: str | None) -> list[DocsSearchHit]:
    """BM25 over H2/H3 units; title tokens count `_TITLE_BOOST` times.

    Hits are interleaved across the three sources (syntax narrative, field
    reference, diagnostics), each in its own BM25 order, so the best hit from
    each reaches the top.
    """
    terms = set(_tokens(query))
    if not terms:
        return []
    units = [u for u in _units() if scope is None or u.topic == scope]
    docs_tf = [
        Counter(_tokens(u.body) + _tokens(u.section) * _TITLE_BOOST) for u in units
    ]
    avg_len = sum(sum(tf.values()) for tf in docs_tf) / max(len(docs_tf), 1)
    n = len(units)
    idf = {
        t: math.log(1 + (n - df + 0.5) / (df + 0.5))
        for t in terms
        if (df := sum(t in tf for tf in docs_tf))
    }
    scored: list[tuple[float, _Unit]] = []
    for unit, tf in zip(units, docs_tf, strict=True):
        norm = _BM25_K1 * (1 - _BM25_B + _BM25_B * sum(tf.values()) / avg_len)
        score = sum(
            idf[t] * tf[t] * (_BM25_K1 + 1) / (tf[t] + norm) for t in idf if t in tf
        )
        if score > 0:
            scored.append((score, unit))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    by_source: dict[str, list[tuple[float, _Unit]]] = {
        "syntax": [],
        "reference": [],
        "diagnostics": [],
    }
    for score, unit in scored:
        by_source[_source(unit)].append((score, unit))
    interleaved = [
        pair
        for round_ in zip_longest(*by_source.values())
        for pair in round_
        if pair is not None
    ]
    return [
        DocsSearchHit(
            topic=unit.topic,
            title=unit.title,
            section=unit.section,
            score=round(score, 2),
            content=unit.body.strip(),
        )
        for score, unit in interleaved[:limit]
    ]
