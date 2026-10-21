"""dbt_charts.agent_api.examples — registry of complete board specimens.

A specimen is one board YAML file under ``dbt_charts/ai/examples/<category>/``,
addressed by the path-shaped slug ``<category>/<stem>``. Where ``skills`` hands
an agent workflow prose and the ``chart`` skill hands it field reference, this hands it a
whole working board: layout, chart wiring, and query shapes in one artifact the
agent can copy and edit.

Every specimen renders standalone — inline ``columns``/``values`` data, no
``source:``, no external ``extends:``. That is what makes the printed YAML
paste-able into a project the registry knows nothing about, and it is enforced
by ``dbt-charts/tests/agent_api/test_examples.py``. The trade-off is that
variable-driven boards cannot be specimens today: variables interpolate into
SQL, and SQL needs a source.

``title`` and ``notes`` come from the board's own top-level keys — a
specimen missing either is a packaging error and raises at load time rather
than listing with a blank cell.
"""

from __future__ import annotations

from functools import cache
from pathlib import PurePosixPath

import yaml
from importlib_resources import files
from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.utils import YAML_LOADER

# Anchored on the top-level `dbt_charts` package, matching skills.py — anchoring
# on `dbt_charts.ai` would force-import that package from here.
_EXAMPLES_DIR = files("dbt_charts") / "ai" / "examples"


class Example(BaseModel):
    """Index entry: everything but the YAML body, so a listing stays cheap."""

    model_config = ConfigDict(frozen=True)

    slug: str = Field(..., description="Path-shaped id, `<category>/<name>`.")
    category: str = Field(..., description="Top-level grouping (the directory).")
    title: str = Field(..., description="The board's own `title:`.")
    notes: str = Field(..., description="The board's own `notes:`.")
    line_count: int = Field(..., description="Lines of YAML in the specimen.")


class ExampleDetail(Example):
    """One specimen, with the board YAML an agent copies."""

    yaml: str = Field(..., description="Full board YAML, verbatim.")


class ExampleList(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool = True
    examples: list[Example] = Field(default_factory=list)


class ExampleSearchHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    category: str
    title: str
    notes: str
    score: float


class ExampleSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool = True
    query: str
    hits: list[ExampleSearchHit] = Field(default_factory=list)


class ExampleNotFound(Exception): ...


def _parse_specimen(slug: str, text: str) -> ExampleDetail:
    """Build an ExampleDetail from one specimen's YAML. Raises ValueError on a
    board that can't describe itself — a listing with a blank title is worse
    than a loud packaging failure."""
    parsed = yaml.load(text, Loader=YAML_LOADER)
    if not isinstance(parsed, dict):
        raise ValueError(f"{slug}: specimen is not a YAML mapping")
    title = parsed.get("title")
    notes = parsed.get("notes")
    if not title:
        raise ValueError(f"{slug}: specimen missing top-level 'title'")
    if not notes:
        raise ValueError(f"{slug}: specimen missing top-level 'notes'")
    return ExampleDetail(
        slug=slug,
        category=slug.split("/")[0],
        title=str(title).strip(),
        notes=str(notes).strip(),
        line_count=len(text.splitlines()),
        yaml=text,
    )


@cache
def _load_all() -> dict[str, ExampleDetail]:
    """Walk _EXAMPLES_DIR/<category>/<name>.yml. Cached for process lifetime."""
    specimens: dict[str, ExampleDetail] = {}
    for category in sorted(_EXAMPLES_DIR.iterdir(), key=lambda p: p.name):
        if not category.is_dir():
            continue
        for entry in sorted(category.iterdir(), key=lambda p: p.name):
            if not entry.name.endswith(".yml"):
                continue
            slug = f"{category.name}/{entry.name[: -len('.yml')]}"
            specimens[slug] = _parse_specimen(slug, entry.read_text(encoding="utf-8"))
    return specimens


def list_examples() -> ExampleList:
    """Index of every bundled specimen, slug-sorted, without the YAML bodies."""
    return ExampleList(
        examples=[
            Example(**detail.model_dump(exclude={"yaml"}))
            for detail in _load_all().values()
        ]
    )


def get_example(slug: str) -> ExampleDetail:
    """Return one specimen, YAML included.

    ``slug`` is matched against the registry keys, never joined onto a path, so
    a traversal attempt is just an unknown slug.
    """
    detail = _load_all().get(str(PurePosixPath(slug)))
    if detail is None:
        raise ExampleNotFound(
            f"Unknown example: {slug!r}. Run `dct examples` to list available."
        )
    return detail


def search_examples(query: str, *, limit: int = 10) -> ExampleSearchResult:
    """Substring search across slug (1.0), title/notes (0.8), YAML (0.5)."""
    # Deferred: `dct` startup must not load the compiler.
    from dbt_charts.core.compile.authoring.yaml_patch import MODELINE_PREFIX

    q = query.strip().lower()
    if not q:
        raise ValueError("query must be a non-empty string")

    hits: list[ExampleSearchHit] = []
    for detail in _load_all().values():
        if q in detail.slug.lower():
            score = 1.0
        elif q in detail.title.lower() or q in detail.notes.lower():
            score = 0.8
        elif any(
            q in line
            for line in detail.yaml.lower().splitlines()
            if not line.startswith(MODELINE_PREFIX)
        ):
            score = 0.5
        else:
            continue
        hits.append(
            ExampleSearchHit(
                slug=detail.slug,
                category=detail.category,
                title=detail.title,
                notes=detail.notes,
                score=score,
            )
        )

    hits.sort(key=lambda h: (-h.score, h.slug))
    return ExampleSearchResult(query=query, hits=hits[:limit])
