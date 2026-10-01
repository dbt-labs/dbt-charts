"""Regression test: measuring or rendering markdown with http:// image URLs
must not make network calls.

Compare.yaml embeds images like:
    ![Looker screenshot](http://127.0.0.1:9893/preview/...)

mdsvg's SVGRenderer defaults to fetch_image_sizes=True, which calls
urllib.request.urlopen() synchronously for every http:// image URL. When the
asset server (port 9893) is not reachable the call blocks for the full 10s
timeout; two such embeds = 20-30s per request, blocking the asyncio event loop.

The fix: pass fetch_image_sizes=False when constructing the SVGRenderer.

Both passes are covered here. The sizing pass runs *before* render, so a
fetching sizing pass defeats the guarantee entirely — and it also makes sizing
disagree with render, which draws every image at image_fallback_aspect_ratio.
"""

from unittest.mock import patch

from .._prose_plan import full_width_plan


def _render_text_with_http_image(text: str, width: float = 800.0) -> None:
    """Call the internal render-text helper with a markdown string."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.boards import _render_text_svg

    _render_text_svg(
        text,
        {},
        width,
        resolved_style=resolve_style(get_theme_style()),
        text_style=resolve_style(get_theme_style()).text,
        painted_canvas=None,
        plan=full_width_plan(resolve_style(get_theme_style()), width),
    )


def test_http_image_in_text_block_does_not_call_urlopen() -> None:
    """Rendering markdown with http:// img embed must not call urlopen."""
    markdown = (
        "## Looker Snapshot\n\n"
        "![Looker dashboard screenshot](http://127.0.0.1:9893/preview/visual_compare/looker_cache/1328.png)\n"
    )

    with patch("urllib.request.urlopen") as mock_urlopen:
        _render_text_with_http_image(markdown)

    mock_urlopen.assert_not_called()


def test_http_image_in_columned_text_does_not_call_urlopen() -> None:
    """Rendering multi-column markdown with http:// img embed must not call urlopen."""
    from dbt_charts.core.compile.config import get_theme_style

    markdown = (
        "## dbt charts Port\n\n"
        "![Rendered board](http://127.0.0.1:9893/preview/evals/runs/20260424-064324/renders/1328.png)\n"
    )

    theme_text = get_theme_style(None).text
    col_style = theme_text.column.model_copy(update={"max_number": 2})
    text_style = theme_text.model_copy(update={"column": col_style})

    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.boards import _render_text_svg

    with patch("urllib.request.urlopen") as mock_urlopen:
        _render_text_svg(
            markdown,
            {},
            800.0,
            text_style=text_style,
            resolved_style=resolve_style(get_theme_style()),
            painted_canvas=None,
            plan=full_width_plan(resolve_style(get_theme_style()), 800.0),
        )

    mock_urlopen.assert_not_called()


_IMAGE_MARKDOWN = (
    "## Looker Snapshot\n\n"
    "![Looker dashboard screenshot](http://127.0.0.1:9893/preview/visual_compare/looker_cache/1328.png)\n"
)


def _columned_text_style(max_number: int | None = None, max_chars: int | None = None):
    """Board body-text style whose column config routes through the columned path."""
    from dbt_charts.core.compile.config import get_theme_style

    theme_text = get_theme_style(None).text
    col = theme_text.column.model_copy(
        update={"max_number": max_number, "max_chars": max_chars}
    )
    return theme_text.model_copy(update={"column": col})


def test_markdown_text_height_does_not_call_urlopen() -> None:
    """Sizing markdown with an http:// img embed must not call urlopen."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_markdown_text_height

    with patch("urllib.request.urlopen") as mock_urlopen:
        get_markdown_text_height(
            _IMAGE_MARKDOWN,
            800.0,
            resolved_style=resolve_style(get_theme_style()),
            text_style=resolve_style(get_theme_style()).text,
            plan=full_width_plan(resolve_style(get_theme_style()), 800.0),
        )

    mock_urlopen.assert_not_called()


def test_columned_text_height_single_column_does_not_call_urlopen() -> None:
    """columned_text_height_estimate's n_cols <= 1 branch must not call urlopen.

    max_chars far wider than the available width collapses the estimate to a
    single column, which measures the whole document in one call.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_markdown_text_height

    with patch("urllib.request.urlopen") as mock_urlopen:
        get_markdown_text_height(
            _IMAGE_MARKDOWN,
            400.0,
            text_style=_columned_text_style(max_chars=1000),
            resolved_style=resolve_style(get_theme_style()),
            plan=full_width_plan(resolve_style(get_theme_style()), 400.0),
        )

    mock_urlopen.assert_not_called()


def test_markdown_text_height_does_not_probe_image_dimensions() -> None:
    """Sizing must not probe image dimensions at all, by any scheme.

    The urlopen tests above only cover ``get_image_size``'s http branch. A
    ``data:`` URI takes its local-file branch instead, which stats the URI as
    a path -- so patching urlopen would not catch a regression there. Pin the
    probe itself: the sizing pass never calls it.
    """
    import base64

    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_markdown_text_height

    payload = base64.b64encode(b"<svg><desc>" + b"A" * 4096 + b"</desc></svg>")
    data_uri = "data:image/svg+xml;base64," + payload.decode("ascii")

    # return_value=None is what the probe yields for an unmeasurable image, so
    # a regression fails on the assertion below rather than on mock plumbing.
    with patch("mdsvg.renderer.get_image_size", return_value=None) as mock_probe:
        get_markdown_text_height(
            f"![logo]({data_uri})",
            400.0,
            resolved_style=resolve_style(get_theme_style()),
            text_style=resolve_style(get_theme_style()).text,
            plan=full_width_plan(resolve_style(get_theme_style()), 400.0),
        )

    mock_probe.assert_not_called()


def test_columned_text_height_multi_column_does_not_call_urlopen() -> None:
    """columned_text_height_estimate's multi-column branch must not call urlopen.

    This branch builds its own SVGRenderer and measures block by block, a
    separate construction site from the single-column one.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_markdown_text_height

    with patch("urllib.request.urlopen") as mock_urlopen:
        get_markdown_text_height(
            _IMAGE_MARKDOWN,
            800.0,
            text_style=_columned_text_style(max_number=2),
            resolved_style=resolve_style(get_theme_style()),
            plan=full_width_plan(resolve_style(get_theme_style()), 800.0),
        )

    mock_urlopen.assert_not_called()
