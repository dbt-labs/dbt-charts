"""Render dbt Charts boards inside a Streamlit app.

``st_board`` mounts one board: point it at a project and a board file,
optionally hand it a live Snowpark session (Streamlit in Snowflake), and it
mounts the board with its own controls live. ``st_project`` serves every board
in a project as one app, navigated like ``dct serve``.
"""

from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from dbt_charts._install_hint import install_hint

try:
    import streamlit as st
    import streamlit.components.v2 as st_components
except ImportError as e:
    raise ImportError(
        "dbt_charts.integrations.streamlit requires the 'streamlit' package. "
        f"Install with: {install_hint('streamlit')}"
    ) from e

from dbt_charts.agent_api import ProjectSession
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import raise_on_dashboard_failure
from dbt_charts.core.compile import compile, compile_file
from dbt_charts.core.compile.config import get_project_markdown_metadata_table
from dbt_charts.core.compile.template.variables import variables_from_query_pairs
from dbt_charts.core.execute.adapters.snowpark_adapter import (
    SnowparkAdapter,
    SnowparkSession,
)
from dbt_charts.core.project import (
    BOARD_CANDIDATE_SUFFIXES,
    ProjectDirectory,
    ProjectPath,
    board_relpath_candidates,
    board_root_for_relpath,
    board_roots,
    board_slug,
    resolve_directory_url,
)
from dbt_charts.core.render.board_links import LinkContext
from dbt_charts.core.render.dir_context import directory_listing
from dbt_charts.core.render.nav import nav_content_left, render_nav_html

# The board's root <svg> opens its container; chrome above it carries icons.
_SVG_OPEN_TAG_RE = re.compile(r"data-dbt-board>\s*<svg\b([^>]*)>")

# A framed board posts its picks ("dbt-variable-change") and its board links
# ("dbt-board-*") to the parent; the nav strip's HTML links are not the board
# runtime's, so the bridge catches those itself. The frame grows to its page:
# a nav strip above the board breaks the board's own proportions.
_BRIDGE_JS = """
export default function ({ data, parentElement, setStateValue, setTriggerValue }) {
  let frame = parentElement.querySelector("iframe");
  if (!frame) {
    frame = document.createElement("iframe");
    frame.style.cssText = "display:block;width:100%;border:0";
    parentElement.appendChild(frame);
    frame.onBoardMessage = (event) => {
      if (event.source !== frame.contentWindow || !event.data) return;
      const { type, href } = event.data;
      if (type === "dbt-variable-change") {
        setStateValue("variables", event.data.variables);
      } else if (frame.navigate && type === "dbt-board-open-tab") {
        const target = new URL(href, window.location.origin);
        const params = new URLSearchParams(target.search);
        params.set("board", target.pathname);
        const url = `${window.location.pathname}?${params}${target.hash}`;
        window.open(url, "_blank", "noopener");
      } else if (frame.navigate && type && type.startsWith("dbt-board-")) {
        frame.navigate(href);
      }
    };
    window.addEventListener("message", frame.onBoardMessage);
  }
  frame.navigate = data.navigate ? (href) => setTriggerValue("navigate", href) : null;
  frame.style.height = "";
  frame.style.aspectRatio = `${data.width} / ${data.height}`;
  frame.onload = () => {
    const page = frame.contentWindow;
    const fit = () => {
      frame.style.aspectRatio = "auto";
      frame.style.height = `${page.document.documentElement.scrollHeight}px`;
    };
    fit();
    new page.ResizeObserver(fit).observe(page.document.body);
    if (!frame.navigate) return;
    page.document.addEventListener("click", (event) => {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest("a[href]");
      if (!link || link.ownerSVGElement) return;
      const href = link.getAttribute("href");
      if (!href.startsWith("/") || href.startsWith("//")) return;
      event.preventDefault();
      frame.navigate(href);
    });
  };
  frame.srcdoc = data.html;
  return () => window.removeEventListener("message", frame.onBoardMessage);
}
"""

_BOARD_FRAME = st_components.component("dbt_charts_board", js=_BRIDGE_JS)


def _board_size(rendered_html: str) -> tuple[int, int]:
    """The board's own (width, height), read from its root ``<svg>``.

    The page CSS scales the board to its container's width, so the frame needs
    both to keep the board's proportions at whatever width it lands in.
    """
    opening_tag = _SVG_OPEN_TAG_RE.search(rendered_html)
    if opening_tag is None:
        raise ValueError(
            "Rendered board HTML has no <svg> root element to size the iframe from."
        )
    size = []
    for attr in ("width", "height"):
        match = re.search(rf'\b{attr}="([\d.]+)"', opening_tag.group(1))
        if match is None:
            raise ValueError(
                f"Rendered board's <svg> root element has no {attr} attribute."
            )
        size.append(math.ceil(float(match.group(1))))
    return size[0], size[1]


def _session(
    project: FilesystemProject, session: SnowparkSession | None
) -> ProjectSession:
    return ProjectSession.from_project(
        project,
        allow_mutating_methods=True,
        extra_adapters_factory=(
            (lambda: [SnowparkAdapter(session=session, project=project)])
            if session is not None
            else None
        ),
    )


def _picked(frame_key: str) -> dict[str, Any]:
    """What the viewer picked in the board mounted at *frame_key*."""
    return st.session_state.get(frame_key, {}).get("variables", {})


def _mount(html: str, frame_key: str, navigate: bool) -> Any:
    width, height = _board_size(html)
    return _BOARD_FRAME(
        key=frame_key,
        # A board dims its charts on a pick and waits for a new document, so
        # every render must reach the frame, even one identical to the last.
        data={
            "html": html,
            "width": width,
            "height": height,
            "navigate": navigate,
            "render": time.time_ns(),
        },
        on_variables_change=lambda: None,
        on_navigate_change=lambda: None,
    )


def st_board(
    board: str | Path,
    *,
    project_dir: str | Path,
    session: SnowparkSession | None = None,
    variables: dict[str, Any] | None = None,
    key: str | None = None,
) -> None:
    """Render a dbt charts board inside a Streamlit app, controls live.

    Args:
        board: Board YAML path, relative to ``project_dir``.
        project_dir: The dbt charts project root.
        session: A live Snowpark ``Session`` (or duck-typed equivalent —
            anything whose ``.sql(sql)`` result exposes ``columns`` and
            ``to_local_iterator()``). When given, SQL
            against a ``snowflake`` source runs through it instead of a dbt
            profile — the shape Streamlit in Snowflake hands an app, which has
            no dbt credentials to connect with otherwise. The session must
            already be using the database and schema the source declares;
            its account, role and warehouse are used as they are. Omitted, this
            renders exactly like any other local dbt charts project (DuckDB /
            local dbt profile); a session is never even registered, so it is
            never touched.
        variables: Values the app sets for the board's ``variables:``. They
            win over what the viewer picks in the board, so pass only the ones
            the app owns.
        key: Where Streamlit keeps what the viewer picked. Defaults to one per
            board path; pass your own to mount the same board twice.
    """
    project = FilesystemProject(Path(project_dir).resolve())
    frame_key = f"dbt_charts_board:{board}" if key is None else key
    with _session(project, session) as ps:
        result = ps.render_board(
            project.path_for_fspath((project.root / board).resolve()).read_board(),
            variables={**_picked(frame_key), **(variables or {})},
            # Picked values persist across reruns of an edited board.
            strict_variables=False,
            format="html",
            standalone=True,
            controls=True,
        )
        raise_on_dashboard_failure(result)
    assert isinstance(result.data, str)
    _mount(result.data, frame_key, navigate=False)


def _resolve(
    project: FilesystemProject, url: str
) -> ProjectPath | list[ProjectDirectory]:
    """The board a ``dct serve`` path names, or the directories it lists,
    resolved the way ``dct serve`` resolves it: a directory first (its index
    board, else a listing), then a board file."""
    clean = url.strip("/")
    resolved = resolve_directory_url(project, clean)
    if isinstance(resolved, ProjectPath) or resolved:
        return resolved
    for relpath in board_relpath_candidates(clean, board_roots(project)):
        if relpath.endswith(BOARD_CANDIDATE_SUFFIXES) and project.exists(relpath):
            return project.path(relpath)
    raise FileNotFoundError(f"No board or folder at {url}")


def _mount_dir(project: FilesystemProject, relpath: str) -> str:
    """The board root *relpath* sits under, which the nav's URLs are relative to."""
    root = board_root_for_relpath(relpath, board_roots(project))
    return root.relpath if root is not None else ""


def st_project(project_dir: str | Path, session: SnowparkSession | None = None) -> None:
    """Serve every board in a project as one Streamlit app, navigated like
    ``dct serve``.

    The ``board`` query parameter is the page's ``dct serve`` path
    (``?board=/sales/overview``); the app opens on ``/``. The other query
    parameters set the board's variables. A value the viewer picks on a page
    beats the URL's, and a link's own values replace what the viewer last
    picked on the board it opens.

    Args:
        project_dir: The dbt Charts project root.
        session: Passed to every board render; see ``st_board``.
    """
    project = FilesystemProject(Path(project_dir).resolve())
    url = st.query_params.get("board", "/")
    url_values = variables_from_query_pairs(
        (key, value)
        for key in st.query_params
        if key != "board"
        for value in st.query_params.get_all(key)
    )
    frame_key = f"dbt_charts_board:{url}"
    page = _resolve(project, url)
    with _session(project, session) as ps:
        if isinstance(page, ProjectPath):
            board = page.read_board()
            compiled = compile_file(
                board,
                markdown_metadata_table=get_project_markdown_metadata_table(project),
            )
            url_mount_dir = _mount_dir(project, page.relpath)
            result = ps.render_board(
                board,
                compile_result=compiled,
                variables={**url_values, **_picked(frame_key)},
                strict_variables=False,
                format="html",
                standalone=True,
                controls=True,
                link_context=LinkContext(
                    root="", current_board_slug=board_slug(page.relpath)
                ),
                url_mount_dir=url_mount_dir,
                chrome=render_nav_html(
                    page.parent,
                    url_mount_dir,
                    page.stem,
                    compiled.board.theme if compiled.board is not None else None,
                    nav_content_left(compiled.board),
                    show_download=False,
                    show_actions=False,
                ),
            )
        else:
            board_text, listing = directory_listing(page, url.strip("/"), project)
            compiled = compile(board_text)
            result = ps.render_board(
                compile_result=compiled,
                builtin_variables=listing,
                format="html",
                standalone=True,
                controls=True,
                link_context=LinkContext(root=""),
                chrome=render_nav_html(
                    page[0],
                    _mount_dir(project, page[0].relpath),
                    page[0].name or "/",
                    None,
                    nav_content_left(compiled.board),
                    show_download=False,
                    show_actions=False,
                ),
            )
        raise_on_dashboard_failure(result)
    assert isinstance(result.data, str)
    clicked = _mount(result.data, frame_key, navigate=True).navigate
    if clicked is not None:
        target = urlsplit(clicked)
        values: dict[str, list[str]] = {}
        for key, value in parse_qsl(target.query, keep_blank_values=True):
            values.setdefault(key, []).append(value)
        st.session_state.pop(f"dbt_charts_board:{target.path}", None)
        st.query_params.from_dict({"board": target.path, **values})
        st.rerun()
