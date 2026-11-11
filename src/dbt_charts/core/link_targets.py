"""Warn when an authored board link names a board the project does not have."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_charts.core.diagnostics.codes_compile import WARN_LINK_TARGET_NOT_FOUND
from dbt_charts.core.diagnostics.diagnostic import Diagnostic
from dbt_charts.core.diagnostics.hints import suggest_close_board
from dbt_charts.core.project import board_slug
from dbt_charts.core.registered_views.loader import load_builtin_registry
from dbt_charts.core.registered_views.router import RouteRouter
from dbt_charts.core.render.board_links import board_link_slug, is_board_link

if TYPE_CHECKING:
    from collections.abc import Iterable

    from dbt_charts.core.project import Project


_SYSTEM_VIEWS = RouteRouter(load_builtin_registry())


def check_link_targets(
    links: Iterable[tuple[str, str, bool]], project: Project, relpath: str
) -> list[Diagnostic]:
    """Warn on each board link whose target matches no board in *project*.

    *links* are the board's `(field path, link, exempt)` facts. *relpath* is the
    checked board's own project path, the anchor for relative links; a board
    outside every board root has no slug, so its links are not checked. A path
    holding a template is skipped, as is one a built-in view (``/data/...``)
    serves: neither names a board file.
    """
    try:
        current = board_slug(relpath)
    except ValueError:
        return []
    targets = [
        (field_path, link, slug)
        for field_path, link, _ in links
        if is_board_link(link)
        and (slug := board_link_slug(link, current))
        and "{" not in slug
        and _SYSTEM_VIEWS.match(f"/{slug}") is None
    ]
    if not targets:
        return []
    known = _board_slugs(project)
    return [
        Diagnostic.from_code(
            WARN_LINK_TARGET_NOT_FOUND,
            message=WARN_LINK_TARGET_NOT_FOUND.message_template.format(
                link=link, field_path=field_path, slug=slug
            ),
            fix=WARN_LINK_TARGET_NOT_FOUND.fix_template,
            hint=suggest_close_board(slug, known),
            field=field_path,
        )
        for field_path, link, slug in targets
        if slug not in known
    ]


def _board_slugs(project: Project) -> set[str]:
    """Every board's slug, plus each folder's collapsed ``index`` slug."""
    slugs = {board_slug(pf.relpath) for pf in project.iter_boards()}
    return slugs | {s.removesuffix("/index") for s in slugs}
