"""Host-agnostic inbound-link scanning for move_file.

Scans the boards tree for references to a dashboard slug -- ``link:`` chart
fields and markdown link targets -- so a move can rewrite what it can prove
is a reference and report the rest. A first-class internal-link graph is out
of scope here (a likely-separate dbt_charts.core initiative); this is a narrow,
best-effort heuristic: exact syntactic matches are rewritten, everything else
(the slug's basename appearing in prose, without recognized link syntax) is
reported as a fuzzy hit and never touched.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.project import (
    CHARTS_SUBDIR,
    Project,
    board_slug as _core_board_slug,
)

_BOARDS_GLOB = f"{CHARTS_SUBDIR}/**/*"


def board_slug(relpath: str) -> str | None:
    """Return the dashboard slug for a ``charts/`` path (no prefix, no suffix).

    ``charts/finance/rev.yml`` -> ``finance/rev``. Returns None for a path
    outside ``charts/`` -- link scanning is host-boards-only (see
    ``_BOARDS_GLOB``), so a package board's slug is out of scope here too.

    No try/except around the delegated call: any relpath that passes the
    ``charts/`` prefix guard above always has ``charts`` as its first path
    part, so ``_core_board_slug`` -- via ``_split_board_root`` -- can never
    raise ValueError for it.
    """
    if not relpath.startswith(f"{CHARTS_SUBDIR}/"):
        return None
    return _core_board_slug(relpath)


def _link_field_pattern(slug: str) -> re.Pattern[str]:
    escaped = re.escape(slug)
    # `url:` is the object form's line: `link: {url: /slug, open: modal}`.
    return re.compile(rf'((?:link|url):\s*["\']?/?){escaped}(?=[\s"\'/?,}}]|$)')


def _markdown_link_pattern(slug: str) -> re.Pattern[str]:
    escaped = re.escape(slug)
    return re.compile(rf"(\]\(/?){escaped}(?=[?)])")


class LinkHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    line_number: int
    line: str


class LinkScanResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    exact: list[LinkHit] = Field(default_factory=list)
    fuzzy: list[LinkHit] = Field(default_factory=list)


def scan_links(project: Project, slug: str) -> LinkScanResult:
    """Scan the boards tree for references to *slug* (e.g. ``finance/rev``).

    Greps for the slug's basename (the last path segment), so a same-folder
    relative reference (``link: rev`` inside another board already under
    ``finance/``) is picked up by the grep too -- but the exact-match regexes
    below only recognize the *full* slug, so a bare relative reference is
    always classified FUZZY (reported, never rewritten), never EXACT. Full
    relative-link resolution is out of scope here (see the module docstring);
    only a full-slug ``link:`` chart field or markdown link target classifies
    as EXACT.
    """
    basename = slug.rsplit("/", 1)[-1]
    link_re = _link_field_pattern(slug)
    md_re = _markdown_link_pattern(slug)
    exact: list[LinkHit] = []
    fuzzy: list[LinkHit] = []
    for hit in project.files.grep(basename, glob=_BOARDS_GLOB):
        bucket = (
            exact if (link_re.search(hit.line) or md_re.search(hit.line)) else fuzzy
        )
        bucket.append(
            LinkHit(path=hit.relpath, line_number=hit.line_number, line=hit.line)
        )
    return LinkScanResult(exact=exact, fuzzy=fuzzy)


def plan_link_rewrites(
    project: Project, old_slug: str, new_slug: str
) -> tuple[LinkScanResult, dict[str, str]]:
    """Compute exact-match link rewrites *old_slug* -> *new_slug* without writing.

    Returns the scan (so the caller can report ``exact`` vs ``fuzzy``)
    alongside a ``{path: rewritten_content}`` mapping for every file with an
    exact hit. The caller commits it however it needs to: one file at a time
    (``rewrite_links``, below) or batched into a single commit (Cloud's
    governed dashboard move).
    """
    result = scan_links(project, old_slug)
    if not result.exact:
        return result, {}
    link_re = _link_field_pattern(old_slug)
    md_re = _markdown_link_pattern(old_slug)
    replacement = new_slug.replace("\\", "\\\\")
    rewrites: dict[str, str] = {}
    for path in dict.fromkeys(hit.path for hit in result.exact):
        content = project.read_text(path)
        content = link_re.sub(rf"\g<1>{replacement}", content)
        content = md_re.sub(rf"\g<1>{replacement}", content)
        rewrites[path] = content
    return result, rewrites


def rewrite_links(project: Project, old_slug: str, new_slug: str) -> LinkScanResult:
    """Rewrite exact ``link:``/markdown references to *old_slug* -> *new_slug*
    in place; fuzzy prose hits are left untouched.

    Returns the scan computed before rewriting, so the caller can report
    exactly what changed (``exact``) vs what needs a human look (``fuzzy``).
    """
    result, rewrites = plan_link_rewrites(project, old_slug, new_slug)
    for path, content in rewrites.items():
        project.write_text(path, content)
    return result
