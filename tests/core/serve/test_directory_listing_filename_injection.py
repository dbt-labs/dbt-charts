"""Regression test: a poisoned filename must not become Jinja template source.

``_render_directory_listing`` builds its listing markdown from on-disk
filenames, then splices that markdown into the packaged ``directory.yml``
board template. If that splice happens via a Jinja render, the *output* text
(now containing the filename verbatim) is handed to ``compile()``, and the
resulting ``board.text`` is Jinja-rendered a second time at display time
(``render/boards.py``'s markdown pass) -- evaluating whatever Jinja syntax the
filename happens to contain. A file whose name is a Jinja expression is then
executed with no board content authored at all.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile as _real_compile
from dbt_charts.core.serve.server import create_server

_PAYLOAD_STEM = "{{7*7}}"


def test_poisoned_filename_renders_literally_in_directory_listing(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A file named with a Jinja expression must render as literal text.

    Failing state: the directory listing evaluates the filename as Jinja,
    so the payload's literal text is gone and its evaluated result (``49``)
    appears in its place.
    """
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / f"{_PAYLOAD_STEM}.yml").write_text("title: dummy\ncharts: {}\nrows: []\n")
    project = local_project(tmp_path)

    with TestClient(create_server(project), raise_server_exceptions=False) as client:
        response = client.get("/")

    preview = f"Body preview: {response.text[:800]}"
    assert response.status_code == 200
    assert "49.yml" not in response.text, (
        "poisoned filename '{{7*7}}.yml' was evaluated as Jinja and rendered "
        f"as '49.yml' in the listing body. {preview}"
    )
    assert f"{_PAYLOAD_STEM}.yml" in response.text, (
        f"poisoned filename must render as literal text somewhere on the "
        f"page. {preview}"
    )
    # The listing body must still be link markup, not just literal text
    # dumped anywhere on the page (e.g. the nav sidebar renders filenames
    # literally through a separate, unaffected code path).
    assert f'<a href="/{_PAYLOAD_STEM}">' in response.text, (
        f"literal filename found, but not as link markup in the listing body. {preview}"
    )


def test_directory_listing_compiles_the_pristine_packaged_template(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """No filename-derived text may reach ``compile()`` -- only the static template.

    Guards against reintroducing the render-then-compile round trip: even a
    version that Jinja-escapes filenames before splicing them into the
    template text would still fail this test, because the compiled source
    would no longer be byte-identical to the packaged ``directory.yml``.
    """
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "tickets.yml").write_text("title: dummy\ncharts: {}\nrows: []\n")
    project = local_project(tmp_path)

    packaged_template = (
        files("dbt_charts.core.render")
        .joinpath("templates/directory.yml")
        .read_text(encoding="utf-8")
    )

    with (
        patch("dbt_charts.core.compile.compile", wraps=_real_compile) as mock_compile,
        TestClient(create_server(project), raise_server_exceptions=False) as client,
    ):
        response = client.get("/")

    assert response.status_code == 200
    mock_compile.assert_called_once()
    compiled_source = mock_compile.call_args[0][0]
    assert compiled_source == packaged_template, (
        "compile() must receive the pristine packaged template, with "
        "filenames substituted only as render-time variables, not spliced "
        f"into template text beforehand. Got: {compiled_source!r}"
    )
