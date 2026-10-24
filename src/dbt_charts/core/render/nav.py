"""Top-of-board navigation strip for ``dct serve`` and other hosts.

Stage: RENDER
Purpose: derive the nav's routing facts from a dir context dict and render them
into an HTML block via a Jinja template. The serve layer injects that HTML
directly into the served board's page body — no board, no SVG. Theme colors are
injected as CSS custom properties on the ``.dbt-nav`` wrapper by the serve
layer — this module owns only the routing facts and the markup.

Hosts that serve a whole project (``dct serve``, the Streamlit integration)
build and render the nav; static ``dct render`` output never carries it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.render.dir_context import lazy_dir_context

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Board
    from dbt_charts.core.project import ProjectDirectory

logger = logging.getLogger(__name__)


def _crumbs_from_url(dir_url: str) -> list[dict[str, str]]:
    """Derive crumb dicts from ``this_dir["url"]`` (already mount-stripped).

    E.g. ``"/support/"``           → ``[{"label": "support", "url": "/support/"}]``
         ``"/enterprise/finance/"``→ ``[{"label": "enterprise", "url": "/enterprise/"},
                                        {"label": "finance", "url": "/enterprise/finance/"}]``
         ``"/"``                   → ``[]``
    """
    parts = [p for p in dir_url.strip("/").split("/") if p]
    crumbs: list[dict[str, str]] = []
    accum = ""
    for part in parts:
        accum = f"{accum}/{part}"
        crumbs.append({"label": part, "url": f"{accum}/"})
    return crumbs


def _visible_crumbs(
    crumbs: list[dict[str, str]],
) -> list[dict[str, str] | None]:
    """Collapse middle directory crumbs for compact serve navigation."""
    if len(crumbs) <= 4:
        return list(crumbs)
    return [crumbs[0], None, *crumbs[-2:]]


def nav_context(
    current_label: str,
    current_url: str,
    dir_ctx: dict[str, Any],
) -> dict[str, Any] | None:
    """Build the Jinja context for the nav strip, or None when dir_ctx is empty.

    Args:
        current_label: text shown in the dropdown trigger. Also the value matched
            against ``f.label`` for the file-menu checkmark — pass the board's stem
            for a board URL; pass the directory's name (or "/") for a listing.
        current_url: where the dropdown trigger links to. Empty string means the
            trigger is not a link (used for directory listings, which have no
            self-URL distinct from the directory the menu already lists).
        dir_ctx: Result of ``lazy_dir_context()``. An empty dict suppresses the
            nav strip (returns None).

    Returns None when ``dir_ctx`` is falsy.
    """
    if not dir_ctx:
        return None

    is_directory_listing = current_url == ""
    this_dir_url = str(dir_ctx["this_dir"]["url"])
    files = [
        {
            "label": f"{s['label']}/" if s["is_dir"] else s["label"],
            "url": s["url"],
        }
        for s in dir_ctx["siblings"]
        if not (this_dir_url == "/" and s["is_dir"] and s["name"] == "inspect")
    ]

    parent_dir = dir_ctx["parent_dir"]
    parent = {"label": "Up one level", "url": parent_dir["url"]} if parent_dir else None
    crumbs = _crumbs_from_url(this_dir_url)
    if is_directory_listing:
        crumbs = crumbs[:-1]
    return {
        "visible_crumbs": _visible_crumbs(crumbs),
        "current_label": current_label,
        "current_file_url": current_url or str(dir_ctx["this_dir"]["url"]),
        "parent": parent,
        "files": files,
    }


def render_nav_html(
    nav_dir: ProjectDirectory,
    url_mount_dir: str,
    current_label: str,
    board_theme: str | None,
    content_left: str,
    show_download: bool,
    show_actions: bool,
) -> str:
    """Build the top-of-page nav as a plain-HTML fragment for the page body.

    The nav is page chrome the host places above the board. It renders directly
    as HTML — no board, no SVG. Theme
    colors are injected as CSS custom properties on the ``.dbt-nav`` wrapper,
    so the nav tracks the served board's theme (including a per-board ``theme:``
    override) without a hardcoded color table.

    Args:
        nav_dir: Directory whose contents populate the file-menu dropdown — the
            served board's parent for board URLs; the directory itself for listings.
        url_mount_dir: forwarded to ``lazy_dir_context``.
        current_label: trigger label; also the value matched in the file menu
            for highlighting and for deriving the trigger href (the URL of the
            matching peer board, or empty for a directory listing where no peer
            matches).
        board_theme: served board's ``theme:`` value (None falls back to default).
            Passed by the caller so the nav doesn't have to recompile the board.
        show_download: whether to render the download-format menu (false for
            listings, which have no exportable chart content).
        show_actions: whether to render the data link, view modes and download
            menu at all. They lean on ``dct serve``'s own routes and window
            sizing, so a host embedding the board in a frame turns them off.

    Returns "" when there's nothing to show or it fails — the nav is chrome and
    must never break the page it sits above.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.template_loader import render_template

    try:
        dir_ctx = lazy_dir_context(nav_dir, url_mount_dir=url_mount_dir)
        current_url = next(
            (
                s["url"]
                for s in dir_ctx["siblings"]
                if not s["is_dir"] and s["label"] == current_label
            ),
            "",
        )
        ctx = nav_context(current_label, current_url, dir_ctx)
        if ctx is None:
            return ""

        from dbt_charts.core.render.chrome_css import theme_to_css

        rstyle = resolve_style(get_theme_style(board_theme))
        return render_template(
            "nav/nav-fragment.html",
            **ctx,
            content_left=content_left,
            show_download=show_download,
            show_actions=show_actions,
            theme_css=theme_to_css(rstyle),
        )
    except (DbtChartsError, ValueError, RuntimeError):
        if logger.isEnabledFor(logging.DEBUG):
            raise
        logger.exception("Error rendering nav for %s", nav_dir.relpath)
        return ""


def nav_content_left(board: Board | None) -> str:
    """Where the nav's left edge lines up with the board's content."""
    if board is None:
        return ""
    frame = board.resolved_style.frame
    return f"{float(frame.margin) + float(frame.card_padding):g}"
