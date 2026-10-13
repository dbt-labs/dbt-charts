"""The top-of-board nav is serve-only page chrome.

``dct serve`` builds the nav as a plain-HTML fragment and injects it into the
served board's page body (no board, no SVG). The static render path
(``dct render`` → ``render``) must NOT carry it. These tests pin both halves of
that contract, plus the nav's markup: a home icon, a custom file menu, and real
anchor links, themed from the board's resolved style via CSS custom properties.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.serve.server import create_server

_BOARD = """\
title: Overview
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
"""


def _project(tmp_path: Path) -> Path:
    boards = tmp_path / "charts"
    (boards / "support").mkdir(parents=True)
    (boards / "overview.yml").write_text(_BOARD)
    (boards / "support" / "index.yml").write_text(_BOARD)
    (boards / "support" / "tickets.yml").write_text(_BOARD)
    return tmp_path


@pytest.mark.parametrize(
    ("page_url", "download_url"),
    [
        ("/a/", "/a.svg"),
        ("/b/", "/b.svg"),
        ("/", "/.svg"),
    ],
)
def test_nav_download_url_serves_the_page_board(
    tmp_path: Path, page_url: str, download_url: str
) -> None:
    """The download URL exports the board its page renders."""
    boards = tmp_path / "charts"
    (boards / "b").mkdir(parents=True)
    (boards / "index.yml").write_text(_BOARD.replace("Overview", "Root"))
    (boards / "a.yml").write_text(_BOARD.replace("Overview", "Plain"))
    (boards / "b" / "index.yml").write_text(_BOARD.replace("Overview", "Folder"))
    with TestClient(
        create_server(FilesystemProject(tmp_path)),
        raise_server_exceptions=False,
    ) as client:
        page = client.get(page_url)
        download = client.get(download_url)
    assert page.status_code == 200, page.text
    assert download.status_code == 200, download.text
    assert download.headers["content-type"].startswith("image/svg+xml")
    assert "attachment" in download.headers.get("content-disposition", "")
    page_title = {"/a/": "Plain", "/b/": "Folder", "/": "Root"}[page_url]
    assert page_title in page.text
    assert page_title in download.text


def test_export_url_prefers_the_folder_index_over_a_same_named_board(
    tmp_path: Path,
) -> None:
    boards = tmp_path / "charts"
    (boards / "a").mkdir(parents=True)
    (boards / "a.yml").write_text(_BOARD.replace("Overview", "Plain"))
    (boards / "a" / "index.yml").write_text(_BOARD.replace("Overview", "Folder"))
    with TestClient(
        create_server(FilesystemProject(tmp_path)),
        raise_server_exceptions=False,
    ) as client:
        page = client.get("/a/")
        download = client.get("/a.svg")
        index_download = client.get("/a/index.svg")
    assert "Folder" in page.text
    assert download.status_code == 200, download.text
    assert "Folder" in download.text
    assert "Plain" not in download.text
    assert index_download.status_code == 200, index_download.text
    assert "Folder" in index_download.text


@pytest.mark.parametrize("download_url", ["/d.svg", "/.svg"])
def test_export_url_of_a_directory_listing_is_404(
    tmp_path: Path, download_url: str
) -> None:
    boards = tmp_path / "charts"
    (boards / "d").mkdir(parents=True)
    (boards / "d" / "tickets.yml").write_text(_BOARD)
    (boards / "d.yml").write_text(_BOARD)
    with TestClient(
        create_server(FilesystemProject(tmp_path)),
        raise_server_exceptions=False,
    ) as client:
        response = client.get(download_url)
    assert response.status_code == 404
    assert "//" not in response.json()["detail"]


def test_served_board_includes_nav(tmp_path: Path) -> None:
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview")
    assert response.status_code == 200
    assert "dbt-nav" in response.text


def test_server_nav_false_omits_nav(tmp_path: Path) -> None:
    """`server: {nav: false}` in dbt_charts.yml suppresses the top-of-board nav."""
    project = _project(tmp_path)
    (project / "dbt_charts.yml").write_text("server:\n  nav: false\n")
    with TestClient(
        create_server(FilesystemProject(project)),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview")
    assert response.status_code == 200
    assert "dbt-nav" not in response.text
    # Sibling folders are no longer appended inline with pipes.
    assert "/support/" not in response.text


def test_served_nav_contains_icon_svg_and_links(tmp_path: Path) -> None:
    """Nav must carry the home icon SVG and anchor links as plain page HTML."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview")
    assert response.status_code == 200
    # The nav is plain HTML in the page body — not wrapped in an SVG foreignObject.
    assert "<foreignObject" not in response.text
    # Inline icon SVG is embedded (house icon path)
    assert "<svg" in response.text
    assert "M2.35 7.15 8 2.25" in response.text
    assert 'x="14" y="32" width="16" height="24"' not in response.text
    assert 'x="34" y="12" width="16" height="44"' not in response.text
    assert 'aria-label="Project home"' in response.text


def test_served_nav_contains_custom_file_menu(tmp_path: Path) -> None:
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview")
    assert response.status_code == 200
    assert "dbt-nav-file-trigger" in response.text
    assert "dbt-nav-file-menu" in response.text
    assert "dbt-nav-menu-option" in response.text
    assert "select.dbt-nav-files" not in response.text


def test_served_nav_file_menu_is_in_place_dropdown(tmp_path: Path) -> None:
    """With the nav as plain page HTML there is no SVG to clip the menu, so the
    dropdown is an ordinary absolutely-positioned element — no portal to body."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview")
    assert response.status_code == 200
    # The menu positions in place within the strip, not portaled to the body.
    assert ".dbt-nav-file-menu{" in response.text
    assert "position:absolute" in response.text
    assert "body>.dbt-nav-file-menu" not in response.text


def test_served_nav_script_wires_custom_menu(tmp_path: Path) -> None:
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview")
    assert response.status_code == 200
    assert "data-dbt-nav-menu-id" in response.text
    assert "dbt-nav-menu-open" in response.text
    assert "aria-expanded" in response.text
    assert "Escape" in response.text
    assert "ArrowDown" in response.text


def test_served_nav_script_aligns_to_board_content_inset(tmp_path: Path) -> None:
    """The HTML nav mirrors the scaled board content inset instead of using a
    fixed page padding."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview")
    assert response.status_code == 200
    assert '<div class="dbt-nav" data-dbt-content-left="' in response.text
    assert '<svg data-dbt-content-left="' not in response.text
    assert "--dbt-nav-page-inset" in response.text
    assert "getBoundingClientRect" in response.text
    assert "viewBox.baseVal" in response.text
    assert "resize" in response.text


def test_served_nested_board_includes_breadcrumb(tmp_path: Path) -> None:
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/support/tickets")
    assert response.status_code == 200
    assert "dbt-nav" in response.text
    # Breadcrumb links back up to the containing directory.
    assert "/support/" in response.text


def test_static_render_has_no_nav(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A plain html render must not carry the serve-injected nav."""
    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.execute.adapters import build_adapter_registry
    from dbt_charts.core.render import render

    result = compile(_BOARD)
    assert result.board is not None
    rendered = render(
        result.board,
        Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        ),
        format="html",
    )
    html = rendered.output
    assert isinstance(html, str)
    assert "dbt-nav" not in html


def test_nav_contains_download_control(tmp_path: Path) -> None:
    """Served board HTML must carry the download select and its options."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview")
    assert response.status_code == 200
    assert "dbt-nav-download" in response.text
    assert 'value="svg"' in response.text
    assert 'value="png"' in response.text
    assert 'value="pdf"' in response.text


def test_download_svg_returns_attachment(tmp_path: Path) -> None:
    """GET /<board>?format=svg must return SVG with Content-Disposition: attachment."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview?format=svg")
    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")
    assert "overview.svg" in response.headers.get("content-disposition", "")
    assert response.headers.get("content-type", "").startswith("image/svg+xml")
    assert response.content.startswith(b"<svg") or b"<svg" in response.content[:200]


@pytest.mark.skipif(
    importlib.util.find_spec("vl_convert") is None,
    reason="vl-convert not installed in test env",
)
def test_download_png_returns_attachment(tmp_path: Path) -> None:
    """GET /<board>?format=png must return PNG bytes with Content-Disposition: attachment."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview?format=png")
    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")
    assert "overview.png" in response.headers.get("content-disposition", "")
    assert response.headers.get("content-type", "").startswith("image/png")
    # PNG magic bytes: \x89PNG
    assert response.content[:4] == b"\x89PNG"


def test_url_suffix_svg_serves_svg(tmp_path: Path) -> None:
    """GET /overview.svg must serve SVG — same as ?format=svg but via URL suffix."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview.svg")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("image/svg+xml")
    assert b"<svg" in response.content[:200]


def test_url_suffix_yaml_serves_raw_source(tmp_path: Path) -> None:
    """GET /overview.yaml must return the raw YAML source as text/plain."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview.yaml")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("text/plain")
    assert "title: Overview" in response.text


def test_url_suffix_yaml_404_for_unknown_board(tmp_path: Path) -> None:
    """GET /nonexistent.yaml must 404."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/nonexistent.yaml")
    assert response.status_code == 404


def test_no_format_param_returns_html_board(tmp_path: Path) -> None:
    """GET /<board> without format param renders normal HTML board."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("text/html")
    assert "dbt-nav" in response.text


def test_nav_contains_data_link(tmp_path: Path) -> None:
    """Served board HTML must carry an anchor linking to /data/ in the nav."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview")
    assert response.status_code == 200
    assert 'href="/data/"' in response.text


def test_data_link_right_aligned_with_download(tmp_path: Path) -> None:
    """The data link must appear in the right-aligned section alongside download."""
    from ..._nav import nav_html

    html = nav_html(
        "overview",
        "/overview",
        {"this_dir": {"url": "/"}, "siblings": [], "parent_dir": None, "tree": ""},
    )
    # The data link and download section must both be present and the data link
    # must appear in the same right-aligned container as the download control.
    data_pos = html.find('class="dbt-nav-data" href="/data/"')
    download_pos = html.find('class="dbt-nav-download"', data_pos)
    assert data_pos != -1, "data link must be present in nav html"
    assert download_pos != -1, "download control must be present in nav html"
    # Both are in the right-aligned section — data link precedes download control.
    assert data_pos < download_pos, (
        "data link must be left of the download control in markup"
    )
    assert 'class="dbt-nav-actions"' in html


def test_directory_listing_includes_nav(tmp_path: Path) -> None:
    """A directory listing carries the same top-of-page nav chrome as a board:
    the home icon, breadcrumb path, file menu, and /data/ link."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "dbt-nav" in response.text
    # The nav's /data/ link is present on the listing too, same as on a board.
    assert 'class="dbt-nav-data" href="/data/"' in response.text


def test_directory_listing_nav_omits_download(tmp_path: Path) -> None:
    """You can't export a directory listing, so its nav drops the download menu
    even though the board nav carries one."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        listing = client.get("/")
        board = client.get("/overview")
    assert listing.status_code == 200
    assert "dbt-nav" in listing.text
    # The download <select> and its format options are absent on the listing.
    # (The .dbt-nav-download* CSS selectors live in nav.css and are always
    # included, so assert on the control's option markup, not the class name.)
    assert '<select class="dbt-nav-download"' not in listing.text
    assert 'value="svg"' not in listing.text
    # The board nav still has the download control — the omission is listing-only.
    assert '<select class="dbt-nav-download"' in board.text
    assert 'value="svg"' in board.text


def test_directory_listing_nav_omitted_when_server_nav_false(tmp_path: Path) -> None:
    """`server: {nav: false}` suppresses the nav on directory listings too."""
    project = _project(tmp_path)
    (project / "dbt_charts.yml").write_text("server:\n  nav: false\n")
    with TestClient(
        create_server(FilesystemProject(project)),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "dbt-nav" not in response.text


def test_nested_directory_listing_includes_breadcrumb(tmp_path: Path) -> None:
    """A nested directory listing treats the listed folder as the current nav item."""
    boards = tmp_path / "charts" / "reports"
    boards.mkdir(parents=True)
    (boards / "q1.yml").write_text(_BOARD)
    with TestClient(
        create_server(FilesystemProject(tmp_path)),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/reports/")
    assert response.status_code == 200
    assert "dbt-nav" in response.text
    assert 'class="dbt-nav-file-trigger" href="/reports/"' in response.text
    assert response.text.count(">reports<") == 1


def test_directory_listing_excludes_non_board_files(tmp_path: Path) -> None:
    """Directory listings show only board files (.md, .yml, .yaml) and subdirectories.

    Non-renderable files (.json, .jsonl, .css, etc.) must be absent from the
    listing HTML — linking them would 500 or 404 on click.
    """
    boards = tmp_path / "charts" / "data"
    boards.mkdir(parents=True)
    (boards / "report.yml").write_text(_BOARD)
    (boards / "notes.md").write_text("# Notes\n")
    (boards / "config.yaml").write_text(_BOARD)
    (boards / "registry.json").write_text("{}")
    (boards / "queue.jsonl").write_text("")
    (boards / "styles.css").write_text("body {}")
    subdir = boards / "sub"
    subdir.mkdir()
    (subdir / "child.yml").write_text(_BOARD)

    with TestClient(
        create_server(FilesystemProject(tmp_path)),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/data/")

    assert response.status_code == 200
    # Board files and subdirectory appear in the listing.
    assert "report" in response.text
    assert "notes" in response.text
    assert "config" in response.text
    assert "sub" in response.text
    # Non-renderable files are absent entirely.
    assert "registry.json" not in response.text
    assert "queue.jsonl" not in response.text
    assert "styles.css" not in response.text


def test_served_nav_injects_nav_css_and_js(tmp_path: Path) -> None:
    """The serve wrapper must inject nav.css in a <style> and nav.js in a <script>."""
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview")
    assert response.status_code == 200
    # nav.css is injected as a <style> block by the serve wrapper
    assert ".dbt-nav{" in response.text
    # nav.js is injected as a <script> block by the serve wrapper
    assert "dbt-nav-menu-open" in response.text


def test_nav_html_itself_has_no_style_or_script(tmp_path: Path) -> None:
    """nav_html() must not contain inline <style> or <script> — those live in static files."""
    from ..._nav import nav_html

    dir_ctx = {
        "this_dir": {"url": "/sales/"},
        "siblings": [
            {
                "name": "q3.yml",
                "url": "/sales/q3",
                "is_dir": False,
                "ext": ".yml",
                "label": "q3",
            },
            {
                "name": "q4.yml",
                "url": "/sales/q4",
                "is_dir": False,
                "ext": ".yml",
                "label": "q4",
            },
        ],
        "parent_dir": None,
        "tree": "",
    }
    html = nav_html("q3", "/sales/q3", dir_ctx)
    assert "<style>" not in html
    assert "<style " not in html
    assert "<script>" not in html
    assert "<script " not in html


def test_served_nav_injects_theme_custom_properties(tmp_path: Path) -> None:
    """Theme values from resolved_style must be injected as page-level :root CSS
    custom properties, so the nav tracks the board theme without a hardcoded table."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )

    # Use the neon theme — its accent / input.background differ visually from
    # the default, so any propagation failure is obvious.
    rstyle = resolve_style(get_theme_style("neon"))
    expected_accent = resolve_chart_style_context(
        get_theme_style("neon")
    ).single_series_palette[0]
    expected_input_bg = rstyle.variables.input.background
    expected_menu_bg = rstyle.background

    # Write a board with theme: neon so the server renders it under that theme.
    boards = tmp_path / "charts"
    boards.mkdir(parents=True)
    (boards / "themed.yml").write_text(
        "title: Themed\n"
        "theme: neon\n"
        "queries:\n"
        "  q: {type: values, rows: [{n: 1}]}\n"
        "charts:\n"
        "  t: {query: q, type: table}\n"
        "rows: [t]\n"
    )
    with TestClient(
        create_server(FilesystemProject(tmp_path)),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/themed")
    assert response.status_code == 200
    html = response.text
    # Theme values are injected as a page-level :root block, not inline on the div.
    assert ":root{" in html
    assert 'class="dbt-nav" style=' not in html
    assert f"--dbt-system-accent: {expected_accent}" in html
    assert f"--dbt-system-input-background: {expected_input_bg}" in html
    assert f"--dbt-system-menu-background: {expected_menu_bg}" in html


def test_nav_failure_does_not_break_the_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The nav is chrome: a ValueError while deriving it (e.g. the current file
    missing from its own file menu) must be swallowed, the nav omitted, and the
    board still served. nav_context is called inside the serve try/except for
    exactly this reason — this pins that the board survives its failure."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise ValueError("Nav current file 'overview' is missing from file menu")

    monkeypatch.setattr("dbt_charts.core.serve.server.nav_context", _boom)
    with TestClient(
        create_server(FilesystemProject(_project(tmp_path))),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/overview")
    # The board renders despite the nav raising; the nav is simply absent.
    assert response.status_code == 200
    assert "<svg" in response.text
    assert "dbt-nav" not in response.text


_BROKEN_BOARD = "notes: Broken\n"  # notes alone are no content: fails validation


def test_error_page_includes_nav(tmp_path: Path) -> None:
    """A board that fails to compile still renders the nav chrome, with a link
    back to a sibling board — the user is never stranded on a dead-end page."""
    project = _project(tmp_path)
    (project / "charts" / "broken.yml").write_text(_BROKEN_BOARD)
    with TestClient(
        create_server(FilesystemProject(project)),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/broken")
    assert response.status_code == 422
    assert "dbt-nav" in response.text
    assert 'href="/overview"' in response.text


def test_error_page_omits_nav_when_server_nav_false(tmp_path: Path) -> None:
    """`server: {nav: false}` suppresses the nav on error pages too."""
    project = _project(tmp_path)
    (project / "charts" / "broken.yml").write_text(_BROKEN_BOARD)
    (project / "dbt_charts.yml").write_text("server:\n  nav: false\n")
    with TestClient(
        create_server(FilesystemProject(project)),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/broken")
    assert response.status_code == 422
    assert "dbt-nav" not in response.text


def test_download_error_page_includes_nav(tmp_path: Path) -> None:
    """`?format=` downloads share the board error page, so they share its nav.

    This route is reachable straight from the nav's own download menu — without
    the chrome it is the same dead end the board page used to be.
    """
    project = _project(tmp_path)
    (project / "charts" / "broken.yml").write_text(_BROKEN_BOARD)
    with TestClient(
        create_server(FilesystemProject(project)),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/broken.svg")
    assert response.status_code == 422
    assert "dbt-nav" in response.text
    assert 'href="/overview"' in response.text


def test_download_error_page_omits_nav_when_server_nav_false(tmp_path: Path) -> None:
    """`server: {nav: false}` reaches the download error page too.

    `_render_board_download` defaults `include_nav` to True, so this pins the
    caller actually forwarding the server's setting rather than the default.
    """
    project = _project(tmp_path)
    (project / "charts" / "broken.yml").write_text(_BROKEN_BOARD)
    (project / "dbt_charts.yml").write_text("server:\n  nav: false\n")
    with TestClient(
        create_server(FilesystemProject(project)),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/broken.svg")
    assert response.status_code == 422
    assert "dbt-nav" not in response.text
