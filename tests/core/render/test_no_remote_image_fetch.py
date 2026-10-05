"""Rendering a board never fetches a remote image over the network.

A render worker that GETs whatever URL a board author typed is blind SSRF on
Cloud, and every unreachable host stalls the render. Every surface that
paints markdown -- title, text block (paragraph, list item, table cell) and
callout -- is exercised through both the sizing pass and the paint pass.
A ``data:`` URI is the one image mdsvg always measures, by decoding the
payload already in hand.
"""

from pathlib import Path
from unittest.mock import patch

from dbt_charts.core.board import render_dashboard
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import InMemoryBoard

from ...conftest import InMemoryProject
from .._prose_plan import full_width_plan

_BOARD = """\
title: '![t](https://example.invalid/title.png) Sales'
text: |
  Intro ![p](https://example.invalid/para.png) text

  - ![l](https://example.invalid/list.png) item

  | a | b |
  | --- | --- |
  | ![c](https://example.invalid/cell.png) | x |
rows:
  - note:
      type: callout
      title: '![ct](https://example.invalid/callout-title.png) Heads up'
      message: 'See ![m](https://example.invalid/message.png)'
"""


def test_rendering_a_board_never_fetches_a_remote_image() -> None:
    project = InMemoryProject(Path("/proj"), {})

    with patch("urllib.request.urlopen") as mock_urlopen:
        result = render_dashboard(
            board=InMemoryBoard(_BOARD, path=None),
            project=project,
            adapter_registry=build_adapter_registry(project, read_only=False),
            result_cache=None,
            format="svg",
        )

    assert result.status == "ok", result.chart_errors or result.board_error
    assert isinstance(result.data, str)
    for name in ("title", "para", "list", "cell", "callout-title", "message"):
        assert f'href="https://example.invalid/{name}.png"' in result.data, name
    mock_urlopen.assert_not_called()


def test_markdown_text_height_measures_data_uri_with_no_io() -> None:
    """Sizing DOES probe a ``data:`` URI's own dimensions -- pure decode, no I/O.

    ``_get_data_uri_image_size`` decodes the payload already in hand, so
    measuring it never risks a network fetch.
    """
    import base64

    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_markdown_text_height
    from mdsvg import get_image_size

    payload = base64.b64encode(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="600">'
        b"<desc>" + b"A" * 4096 + b"</desc></svg>"
    )
    data_uri = "data:image/svg+xml;base64," + payload.decode("ascii")

    with patch("mdsvg.renderer.get_image_size", wraps=get_image_size) as mock_probe:
        get_markdown_text_height(
            f"![logo]({data_uri})",
            400.0,
            resolved_style=resolve_style(get_theme_style()),
            text_style=resolve_style(get_theme_style()).text,
            base_dir=None,
            plan=full_width_plan(resolve_style(get_theme_style()), 400.0),
        )

    mock_probe.assert_called_once()
