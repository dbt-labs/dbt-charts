"""The directory listing renders at the standard board width.

Serve's fit-width view stretches every board to the window. A listing left to
hug its short text renders a few hundred pixels wide and then scales several
times larger than any other board.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.serve.server import create_server


def test_directory_listing_board_is_standard_width(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    for folder in ("a", "b"):
        (tmp_path / "charts" / folder).mkdir(parents=True)
        (tmp_path / "charts" / folder / "board.yml").write_text("title: x\ntext: hi\n")

    with TestClient(create_server(local_project(tmp_path))) as client:
        response = client.get("/")

    assert response.status_code == 200
    board_svg = re.search(
        r'<div class="dbt-charts-svg-container[^"]*"[^>]*>\s*<svg[^>]*\swidth="([^"]+)"',
        response.text,
    )
    assert board_svg is not None, response.text[:800]
    assert float(board_svg.group(1)) == 1200
