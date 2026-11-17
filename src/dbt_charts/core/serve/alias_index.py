"""Per-project URL→board alias index for redirect resolution.

Builds a URL→canonical-board-file-path mapping from all board files in a project.
Used by the HTTP server to 30x-redirect alias URLs to the board's real path.

Design invariants (from accepted decisions):
- Aliases must be absolute (leading /).
- URLs are trailing-slash-canonical and percent-decoded before matching.
- An alias that is not absolute, shadows a real board file, is claimed by two
  boards, or shares a parameterized shape with another is an error on each board
  involved; the alias redirects nowhere and every other board keeps serving.
- Aliasing a generated system view (data/inspector route) is allowed (override).
- The index is strictly per-project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import yaml

from dbt_charts.core.aliases import normalize_alias_url
from dbt_charts.core.diagnostics import ERR_BOARD_ALIAS, Diagnostic
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.project import (
    BOARD_CANDIDATE_SUFFIXES,
    INDEX_CANDIDATE_NAMES,
    BoardRoot,
    board_directory_candidates,
    board_relpath_candidates,
    board_roots,
    board_slug,
    reserves_charts_url,
)
from dbt_charts.core.utils import YAML_LOADER

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dbt_charts.core.project import Project, ProjectPath


def _is_under_a_board_root(relpath: str) -> bool:
    """True iff relpath sits under the host ``charts/`` or a package root.

    Pure/lexical — reuses ``board_slug``'s own root-shape recognition rather
    than re-deriving it, so this can never drift from the enumeration seam.
    """
    try:
        board_slug(relpath)
    except ValueError:
        return False
    return True


def read_aliases_from_file(board_path: ProjectPath) -> list[str]:
    """Read the aliases list from a YAML or Markdown board file without full compile.

    Reads through the owning Project (the file-access seam) so this works against
    a git-blob store as well as the filesystem. Returns an empty list when the
    file has no aliases, cannot be parsed, or is not a genuine board. Non-list and
    non-string alias values are skipped; shape errors surface at compile/validate.
    """
    if not board_path.exists():
        return []

    if board_path.is_yaml:
        try:
            raw = yaml.load(board_path.read_text(), Loader=YAML_LOADER)
        except yaml.YAMLError:
            # The compile/render path reports parse errors with full context.
            # Skipping here avoids a second, lower-quality error from index build.
            return []
        if not isinstance(raw, dict):
            return []
        aliases_raw = raw.get("aliases")
    elif board_path.is_markdown:
        # Reuse the canonical markdown frontmatter parser — don't duplicate it.
        from dbt_charts.core.compile.parse.markdown import (
            is_markdown_board_content,
            parse_markdown_board,
        )

        text = board_path.read_text()
        in_boards = _is_under_a_board_root(board_path.relpath)
        if not is_markdown_board_content(text, in_boards=in_boards, named=False):
            return []
        try:
            board_yaml, _ = parse_markdown_board(text)
        except (ValueError, yaml.YAMLError):
            # The compile/render path reports parse errors with full context.
            return []
        # Through parse_markdown_board, not the raw frontmatter: `aliases` is a
        # board field, and markdown.py's contract is that board config lives
        # exclusively under `board:` — every other frontmatter key is document
        # metadata. Reading the top level instead would make a markdown claim
        # resolve here and nowhere else that compiles the same file.
        board_config = yaml.load(board_yaml, Loader=YAML_LOADER)
        if not isinstance(board_config, dict):
            return []
        aliases_raw = board_config.get("aliases")
    else:
        return []

    if aliases_raw is None:
        return []
    if not isinstance(aliases_raw, list):
        # Non-list aliases: (e.g. a palette's color-alias dict) — skip.
        # A real board's alias-shape error surfaces at compile/validate.
        return []
    for item in aliases_raw:
        if not isinstance(item, str):
            # Non-string item — same rationale as non-list: skip silently.
            # A real board's alias-shape error surfaces at compile/validate.
            return []
    return list(aliases_raw)


def board_file_candidates(
    project: Project,
    clean_path: str,
) -> list[ProjectPath]:
    """Return the ordered candidate board-file handles for a URL path segment.

    This is the single canonical mapper from URL path → candidate files.  Both
    the server's request resolver (_resolve_board_file_path) and the alias-index
    collision checker (_file_url_exists) call this function so that the two
    never drift apart.

    Scans every board root (host ``charts/`` plus every installed package's
    ``charts/``, see ``board_roots``); a plain ``/charts/*`` URL always 404s
    (that prefix is reserved).

    Args:
        project: the dbt charts project.
        clean_path: URL path with leading/trailing slashes stripped, e.g. "sales"
                    or "pkg_a/overview".  Empty string means the project root.
    """
    if reserves_charts_url(project, clean_path):
        return []
    return [
        project.path(relpath)
        for relpath in board_relpath_candidates(clean_path, board_roots(project))
    ]


def url_for_file(relpath: str) -> str:
    """Return the canonical served URL for a board's project-relative path.

    This is the inverse of board_file_candidates: given a board's relpath
    (under any board root), return the URL a browser would use to reach it.

    board_slug is a *file-identity* slug -- it keeps an ``index`` leaf so
    every board file maps to a distinct value. A URL is a display concern:
    ``reports/index`` and ``reports`` must serve the same page, so the
    trailing ``index`` segment is collapsed here, and only here.
    """
    slug = board_slug(relpath)
    parts = slug.split("/") if slug else []
    if parts and parts[-1] == "index":
        parts = parts[:-1]
    slug = "/".join(parts)
    return f"/{slug}/" if slug else "/"


def _directory_index_relpaths(clean_path: str, roots: Sequence[BoardRoot]) -> list[str]:
    """Candidate index-board relpaths for *clean_path* as a directory URL,
    across every matching board root -- root matching itself comes from
    ``board_directory_candidates`` (shared with dct serve's directory
    router) so package-name matching is never duplicated."""
    relpaths: list[str] = []
    for root, rest in board_directory_candidates(clean_path, roots):
        base = f"{root.relpath}/{rest}" if rest else root.relpath
        relpaths.extend(f"{base}/{name}" for name in INDEX_CANDIDATE_NAMES)
    return relpaths


def _file_url_exists(
    url: str,
    project: Project,
) -> bool:
    """Return True if the normalized URL corresponds to an existing board file.

    Uses board_file_candidates (the canonical mapper) so the existence check
    never drifts from what the server resolver actually resolves.

    An alias is not stripped of ``..`` segments before reaching here
    (normalize_alias_url only enforces a leading/trailing slash), so a candidate
    ref can escape the project root — the join raises ValueError, caught and
    treated as "not found". An escaping alias is a display-page concern (dead
    redirect), not a collision-detection one.
    """
    # url is normalized: starts with /, ends with /
    clean_path = url.strip("/")

    try:
        for candidate in board_file_candidates(project, clean_path):
            if (
                candidate.relpath.endswith(BOARD_CANDIDATE_SUFFIXES)
                and candidate.exists()
            ):
                return True
        # A no-extension URL is a directory URL: it collides only if it holds an
        # index board. There is no Project.is_dir(); probing the index candidates
        # subsumes the check (a directory with no index isn't a collision target).
        if not clean_path or PurePosixPath(clean_path).suffix == "":
            for relpath in _directory_index_relpaths(clean_path, board_roots(project)):
                if project.exists(relpath):
                    return True
    except ValueError:
        return False
    return False


@dataclass
class AliasIndex:
    """Per-project URL→canonical-board-path redirect index.

    Maps normalized alias URLs to the canonical file-path URL of the board
    that declared them. Use `AliasIndex.build()` to construct from disk;
    use `AliasIndex.lookup(url)` to resolve a redirect target.

    The index is intentionally read from raw YAML (not full compile) to keep
    project load cheap and avoid executing queries at startup.
    """

    # normalized URL → canonical URL (e.g. "/reports/old/" → "/sales/")
    _map: dict[str, str] = field(default_factory=dict)
    # Parameterized aliases: (compiled pattern, canonical URL). A pattern like
    # "/milestones/<name>/" captures the segment via named groups and the server
    # redirects to "<canonical>?<name>=<captured>" (capture names come straight
    # off the match's groupdict()).
    _patterns: list[tuple[re.Pattern[str], str]] = field(default_factory=list)
    # Board relpath → why its claims can't be served. Each fails only the boards
    # it names, so one bad alias never takes the rest of the project down.
    errors: dict[str, list[Diagnostic]] = field(default_factory=dict)

    def lookup(self, request_url: str) -> str | None:
        """Return the canonical URL for an exact (non-parameterized) alias, else None.

        Args:
            request_url: The incoming request path (e.g. "/reports/old-sales/").
                         Will be normalized before lookup.
        """
        try:
            normalized = normalize_alias_url(request_url)
        except ValueError:
            return None
        return self._map.get(normalized)

    def match_patterns(self, request_url: str) -> list[tuple[str, dict[str, str]]]:
        """Return (canonical_url, captured_params) for each matching pattern alias.

        Empty when no pattern alias matches; the server uses the first match. The
        captured params are bound to board variables of the same name by the server
        (appended as query params on the redirect to the board's canonical URL).
        """
        try:
            normalized = normalize_alias_url(request_url)
        except ValueError:
            return []
        matches: list[tuple[str, dict[str, str]]] = []
        for pattern, canonical in self._patterns:
            m = pattern.match(normalized)
            if m is not None:
                matches.append((canonical, m.groupdict()))
        return matches

    @classmethod
    def build(
        cls,
        project: Project,
    ) -> AliasIndex:
        """Build the alias index by scanning every board root in the project.

        Scans the host ``charts/`` plus every installed package's ``charts/``
        (see ``board_roots``). Never raises for an authored alias: an alias that
        isn't absolute, shadows a real board file, is claimed by more than one
        board, or matches the same URLs as another board's parameterized alias
        lands in ``errors`` against each board involved and redirects nowhere.
        """
        from dbt_charts.core.registered_views.router import compile_route

        index = cls()

        def reject(relpath: str, alias: str, reason: str) -> None:
            index.errors.setdefault(relpath, []).append(
                DbtChartsError.from_code(
                    ERR_BOARD_ALIAS, alias=alias, reason=reason
                ).to_diagnostic(file=relpath)
            )

        # normalized alias URL → {board relpath: canonical URL}, in scan order
        claims: dict[str, dict[str, str]] = {}
        for pf in project.iter_boards():
            raw_aliases = read_aliases_from_file(pf)
            if not raw_aliases:
                continue
            canonical_url = url_for_file(pf.relpath)
            for raw_alias in raw_aliases:
                try:
                    normalized = normalize_alias_url(raw_alias)
                except ValueError:
                    reject(
                        pf.relpath,
                        raw_alias,
                        "is not absolute: aliases must start with a leading /, "
                        "like /old-reports/.",
                    )
                    continue
                # A board aliasing its own canonical URL is a no-op; mapping
                # canonical→canonical would loop at request time.
                if normalized == canonical_url:
                    continue
                # System-view routes (data/inspector) are generated, not files, so
                # aliasing those is allowed — only real board files are forbidden.
                if _file_url_exists(normalized, project):
                    reject(
                        pf.relpath,
                        raw_alias,
                        "shadows a real board at that URL. Aliases may not shadow "
                        "real board files; aliasing a system-view route "
                        "(data/inspector) is allowed.",
                    )
                    continue
                claims.setdefault(normalized, {})[pf.relpath] = canonical_url

        # pattern shape (captures → <*>) → [(board relpath, alias, canonical URL)]
        shapes: dict[str, list[tuple[str, str, str]]] = {}
        for alias, owners in claims.items():
            if len(owners) > 1:
                for relpath in owners:
                    others = ", ".join(o for o in owners if o != relpath)
                    reject(
                        relpath,
                        alias,
                        f"is also claimed by {others}. Each alias must be unique "
                        "across the project, so no board gets it until all but "
                        "one drop it.",
                    )
                continue
            ((relpath, canonical_url),) = owners.items()
            if "<" in alias or ">" in alias:
                shape = re.sub(r"<[^>]+>", "<*>", alias)
                shapes.setdefault(shape, []).append((relpath, alias, canonical_url))
            else:
                index._map[alias] = canonical_url

        # Two patterns of one shape (/items/<name>, /items/<id>) match the same
        # URLs, and scan order would pick the winner silently.
        for shape, group in shapes.items():
            if len(group) > 1:
                for relpath, alias, _ in group:
                    others = ", ".join(
                        f"{a!r} in {r}" for r, a, _ in group if a != alias
                    )
                    reject(
                        relpath,
                        alias,
                        f"matches the same URLs as {others} (shape {shape!r}). "
                        "Disambiguate the path.",
                    )
                continue
            ((_, alias, canonical_url),) = group
            index._patterns.append((compile_route(alias), canonical_url))
        return index
