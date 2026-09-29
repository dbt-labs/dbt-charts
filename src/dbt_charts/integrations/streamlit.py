"""Render a dbt charts board inside a Streamlit app.

``st_board`` is the one entry point: point it at a project and a board file,
optionally hand it a live Snowpark session (Streamlit in Snowflake), and it
mounts the board with its own controls live.
"""

from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any

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
from dbt_charts.core.execute.adapters.snowpark_adapter import (
    SnowparkAdapter,
    SnowparkSession,
)

_SVG_OPEN_TAG_RE = re.compile(r"<svg\b([^>]*)>")

# A board in a frame posts {type: "dbt-variable-change", variables} to its parent.
_BRIDGE_JS = """
export default function ({ data, parentElement, setStateValue }) {
  let frame = parentElement.querySelector("iframe");
  if (!frame) {
    frame = document.createElement("iframe");
    frame.style.cssText = "display:block;width:100%;height:auto;border:0";
    parentElement.appendChild(frame);
    frame.onBoardMessage = (event) => {
      if (event.source !== frame.contentWindow) return;
      if (!event.data || event.data.type !== "dbt-variable-change") return;
      setStateValue("variables", event.data.variables);
    };
    window.addEventListener("message", frame.onBoardMessage);
  }
  frame.style.aspectRatio = `${data.width} / ${data.height}`;
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
            anything exposing ``.sql(sql).to_local_iterator()``). When given, SQL
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
    picked = st.session_state.get(frame_key, {}).get("variables", {})
    project_session = ProjectSession.from_project(
        project,
        extra_adapters_factory=(
            (lambda: [SnowparkAdapter(session=session, project=project)])
            if session is not None
            else None
        ),
    )
    with project_session as ps:
        result = ps.render_board(
            project.path_for_fspath((project.root / board).resolve()).read_board(),
            variables={**picked, **(variables or {})},
            format="html",
            standalone=True,
            controls=True,
        )
        raise_on_dashboard_failure(result)

    assert isinstance(result.data, str)
    width, height = _board_size(result.data)
    _BOARD_FRAME(
        key=frame_key,
        # A board dims its charts on a pick and waits for a new document, so
        # every render must reach the frame, even one identical to the last.
        data={
            "html": result.data,
            "width": width,
            "height": height,
            "render": time.time_ns(),
        },
        # Declares `variables` as state the component may set.
        on_variables_change=lambda: None,
    )
