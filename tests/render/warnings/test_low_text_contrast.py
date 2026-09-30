"""Tests for the WARN_LOW_TEXT_CONTRAST render-warning detector.

Detection rule: ``check_text_contrast``/``check_markdown_contrast``
(``render/contrast_warning.py``) run wherever a text block's resolved font
is about to paint — heading and body ink — and record a ``ContrastRecord``
when the ink and its opaque background clear less than the configured WCAG
floor (``chart_rendering.text_contrast``, one config node).
``render/warnings/low_text_contrast.py`` turns each record into a
``Diagnostic``.

Colors are authored explicitly on every board below (never a bare theme
default) per the repo rule against pinning theme/default values in tests.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.board import BoardRenderResult


def _mock_adapter(rows: list[dict[str, Any]]) -> Mock:
    """Adapter registry mock that returns ``rows`` for every query."""
    ok = Mock()
    ok.is_success = True
    ok.data = rows
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    registry = Mock()
    registry.execute.return_value = ok
    registry.project_file_sources.return_value = {}
    return registry


def _render(
    board_yaml: str,
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    *,
    format: str = "svg",
    rows: list[dict[str, Any]] | None = None,
) -> BoardRenderResult:
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    registry = _mock_adapter(rows or [])
    board = InMemoryBoard(board_yaml, path=project.path("charts/_t.yml"))
    return render_dashboard(
        board=board,
        adapter_registry=registry,
        format=format,
        project=project,
        result_cache=None,
    )


# ── the authored case: a text row inside rows: with its own background ─────

_NAVY_ROW_BOARD = """
rows:
  - text: |
      # Sales overview

      Body line here.
    style:
      background: "#0B1F3A"
"""


def test_navy_row_warns_and_names_style_keys(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A ``text:`` row with its own ``style.background`` and the theme's
    default ink for both heading and body must warn twice, naming
    ``style.title.font.color`` for the heading and ``style.font.color`` for
    the body."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    result = _render(_NAVY_ROW_BOARD, tmp_path, local_project)
    matches = [w for w in result.warnings if w.code == WARN_LOW_TEXT_CONTRAST.code]
    assert len(matches) == 2, f"Expected one heading + one body warning; got: {matches}"

    heading = next(w for w in matches if "Heading" in w.message)
    body = next(w for w in matches if "Body text" in w.message)
    assert heading.fix is not None
    assert body.fix is not None
    assert "style.title.font.color" in heading.fix
    assert heading.fix.count("style.title.font.color") == 1
    assert "style.font.color" in body.fix
    assert "style.title.font.color" not in body.fix
    assert heading.path == "rows.0"
    assert body.path == "rows.0"


def test_root_text_reports_path_text(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A board's own root-level ``text:`` (no ``rows:`` wrapper) locates at
    ``path="text"`` — the bare top-level key, not a dotted ``rows.N`` path."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    board_yaml = """
text: |
  # Heading

  Body line here.
style:
  background: "#0B1F3A"
"""
    result = _render(board_yaml, tmp_path, local_project)
    matches = [w for w in result.warnings if w.code == WARN_LOW_TEXT_CONTRAST.code]
    assert len(matches) == 2
    assert all(w.path == "text" for w in matches), matches


def test_two_navy_rows_produce_two_located_warnings(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Two rows with the same low-contrast ink/background pair are two
    distinct authoring mistakes, not one merged warning — each names where
    it is."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    board_yaml = """
rows:
  - text: |
      # First section

      Body one.
    style:
      background: "#0B1F3A"
  - text: |
      # Second section

      Body two.
    style:
      background: "#0B1F3A"
"""
    result = _render(board_yaml, tmp_path, local_project)
    matches = [w for w in result.warnings if w.code == WARN_LOW_TEXT_CONTRAST.code]
    assert len(matches) == 4, f"Expected two headings + two bodies; got: {matches}"
    paths = {w.path for w in matches}
    assert paths == {"rows.0", "rows.1"}
    row0_kinds = {
        "Heading" if "Heading" in w.message else "Body"
        for w in matches
        if w.path == "rows.0"
    }
    row1_kinds = {
        "Heading" if "Heading" in w.message else "Body"
        for w in matches
        if w.path == "rows.1"
    }
    assert row0_kinds == {"Heading", "Body"}
    assert row1_kinds == {"Heading", "Body"}


def test_navy_row_warns_in_text_and_json_formats(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Render-time warnings are collected during the one draw every format
    performs, so the code must reach text and json results too."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    for fmt in ("text", "json"):
        result = _render(_NAVY_ROW_BOARD, tmp_path, local_project, format=fmt)
        codes = {w.code for w in result.warnings}
        assert WARN_LOW_TEXT_CONTRAST.code in codes, f"format={fmt}: got {codes}"


def test_row_with_readable_override_does_not_warn(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Whitening both inks on the same navy background clears the floor."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    board_yaml = """
rows:
  - text: |
      # Sales overview

      Body line here.
    style:
      background: "#0B1F3A"
      font:
        color: "#FFFFFF"
      title:
        font:
          color: "#FFFFFF"
"""
    result = _render(board_yaml, tmp_path, local_project)
    codes = {w.code for w in result.warnings}
    assert WARN_LOW_TEXT_CONTRAST.code not in codes


def test_default_light_theme_board_emits_no_contrast_warning(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    board_yaml = """
rows:
  - text: |
      # Overview

      A perfectly ordinary paragraph.
"""
    result = _render(board_yaml, tmp_path, local_project)
    codes = {w.code for w in result.warnings}
    assert "WARN-LOW-TEXT-CONTRAST" not in codes


def test_default_dark_theme_board_emits_no_contrast_warning(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    board_yaml = """
theme: neon
rows:
  - text: |
      # Overview

      A perfectly ordinary paragraph.
"""
    result = _render(board_yaml, tmp_path, local_project)
    codes = {w.code for w in result.warnings}
    assert "WARN-LOW-TEXT-CONTRAST" not in codes


def test_multi_paragraph_body_warns_once_not_per_paragraph(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """mdsvg scopes one CSS class to every paragraph's ink — three paragraphs
    in the same low-contrast ink must warn once, not three times."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    board_yaml = """
rows:
  - text: |
      First paragraph.

      Second paragraph.

      Third paragraph.
    style:
      background: "#0B1F3A"
"""
    result = _render(board_yaml, tmp_path, local_project)
    matches = [w for w in result.warnings if w.code == WARN_LOW_TEXT_CONTRAST.code]
    assert len(matches) == 1, f"Expected exactly one body warning; got: {matches}"


def test_raw_html_only_block_is_checked_as_body_ink(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A block containing only raw HTML has no heading or paragraph node,
    but it still paints in the resolved body ink — it must be checked too."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    board_yaml = """
html_policy: trusted-raw
text: |
  <div>Just some raw HTML, no markdown paragraph.</div>
style:
  background: "#0B1F3A"
"""
    result = _render(board_yaml, tmp_path, local_project)
    matches = [w for w in result.warnings if w.code == WARN_LOW_TEXT_CONTRAST.code]
    assert len(matches) == 1
    assert "Body text" in matches[0].message


# ── the painted canvas, not style.charts.background ─────────────────────────


def test_charts_background_does_not_supply_the_check(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """``style.charts.background`` colors chart cards, not the board/row
    canvas a text block paints on — a navy row with white ink must read as
    readable even when the board's ``charts.background`` is white."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    board_yaml = """
style:
  charts:
    background: "#FFFFFF"
rows:
  - text: |
      # Heading

      Body line here.
    style:
      background: "#0B1F3A"
      font:
        color: "#FFFFFF"
      title:
        font:
          color: "#FFFFFF"
"""
    result = _render(board_yaml, tmp_path, local_project)
    codes = {w.code for w in result.warnings}
    assert WARN_LOW_TEXT_CONTRAST.code not in codes, (
        f"white ink on the row's own navy background is readable: {result.warnings}"
    )


def test_charts_background_does_not_falsely_warn_the_other_direction(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A white row with the theme's default (dark) ink must read as readable
    even when the board's ``charts.background`` is navy."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    board_yaml = """
style:
  charts:
    background: "#0B1F3A"
rows:
  - text: |
      # Heading

      Body line here.
    style:
      background: "#FFFFFF"
"""
    result = _render(board_yaml, tmp_path, local_project)
    codes = {w.code for w in result.warnings}
    assert WARN_LOW_TEXT_CONTRAST.code not in codes, (
        f"dark default ink on the row's own white background is readable: {result.warnings}"
    )


def test_navy_row_still_warns_and_reports_the_real_background(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A navy row with the theme's default ink still warns, and the message
    names the row's own navy background — not any ``charts.background``."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    result = _render(_NAVY_ROW_BOARD, tmp_path, local_project)
    matches = [w for w in result.warnings if w.code == WARN_LOW_TEXT_CONTRAST.code]
    assert len(matches) == 2
    assert all("#0b1f3a" in w.message for w in matches), matches


def test_text_item_inherits_its_container_background(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A text item with no background of its own, nested inside a ``cols:``
    container that sets one, paints on the container's background — not the
    root board's."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    board_yaml = """
rows:
  - cols:
      - text: |
          Body line here.
    style:
      background: "#0B1F3A"
"""
    result = _render(board_yaml, tmp_path, local_project)
    matches = [w for w in result.warnings if w.code == WARN_LOW_TEXT_CONTRAST.code]
    assert len(matches) == 1
    assert "#0b1f3a" in matches[0].message


# ── a container's own bg_rect paints under its own children too, not just
#    under the nested-board level above it (an alpha container's layout-level
#    ``_bg_rect`` is a real, separate paint the canvas must account for) ──────

# Same alpha container nested one level inside each of the four layout
# kinds -- {0} is the ink color, so one template covers the still-warns and
# the silent-once-correct cases below.
_ALPHA_CONTAINER_BOARD_TEMPLATE = """
style:
  background: "#FFFFFF"
rows:
  - style:
      background: "#0B1F3A50"
      font:
        color: "{ink}"
    {child}"""

_ALPHA_CONTAINER_CHILDREN = {
    "rows": "rows:\n      - text: Body text here.\n",
    "cols": "cols:\n      - text: Body text here.\n",
    "grid": "grid:\n      items:\n        - item:\n            text: Body text here.\n",
    "tabs": (
        "tabs:\n      items:\n        - title: Tab 1\n          text: Body text here.\n"
    ),
}


def _render_alpha_container(
    container_key: str,
    ink: str,
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> BoardRenderResult:
    board_yaml = _ALPHA_CONTAINER_BOARD_TEMPLATE.format(
        ink=ink, child=_ALPHA_CONTAINER_CHILDREN[container_key]
    )
    return _render(board_yaml, tmp_path, local_project)


@pytest.mark.parametrize("container_key", ["rows", "cols", "grid", "tabs"])
def test_alpha_container_still_warns_with_the_full_composite_depth(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    container_key: str,
) -> None:
    """White ink on this container warns against the composite of every
    ``_bg_rect`` the SVG actually stacks (the container's own nested-board-level
    paint plus its layout-level paint; the leaf board wrapping the bare
    ``text:`` paints nothing) -- any other count reports the wrong canvas."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST
    from dbt_charts.core.render.contrast_warning import composite_over_canvas

    result = _render_alpha_container(container_key, "#FFFFFF", tmp_path, local_project)
    svg = result.data
    assert isinstance(svg, str)
    layer_count = svg.lower().count('fill="#0b1f3a50"')

    expected_canvas = "#ffffff"
    for _ in range(layer_count):
        composited = composite_over_canvas("#0B1F3A50", expected_canvas)
        assert composited is not None
        expected_canvas = composited

    matches = [w for w in result.warnings if w.code == WARN_LOW_TEXT_CONTRAST.code]
    assert len(matches) == 1, (
        f"expected white ink on the {layer_count}-layer composite "
        f"{expected_canvas} to warn; got: {result.warnings}"
    )
    assert expected_canvas in matches[0].message, (
        f"reported background must be the real {layer_count}-layer composite "
        f"{expected_canvas}, matching every rect the SVG actually painted: "
        f"{matches[0].message}"
    )


@pytest.mark.parametrize("container_key", ["rows", "cols", "grid", "tabs"])
def test_alpha_container_silent_once_the_full_composite_passes(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    container_key: str,
) -> None:
    """Black ink on this same container is readable against the two stacked
    layers of alpha navy; counting a phantom third layer reports a darker
    canvas and falsely warns."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    result = _render_alpha_container(container_key, "#000000", tmp_path, local_project)
    codes = {w.code for w in result.warnings}
    assert WARN_LOW_TEXT_CONTRAST.code not in codes, (
        f"black ink on the fully-composited two-layer canvas is readable: "
        f"{result.warnings}"
    )


def test_quick_guide_example_reports_the_real_backgrounds(
    local_project: Callable[..., FilesystemProject],
) -> None:
    """The shipped ``quick-guide`` example nests a text row with its own
    dark background inside a board whose ``style.charts.background`` is
    white — this pins that specific real-world reproduction directly."""
    from dbt_charts.core.compile import compile_file
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.execute.adapters import build_adapter_registry
    from dbt_charts.core.render import render

    from ..._paths import DBT_CHARTS_DIR

    project = local_project(DBT_CHARTS_DIR)
    result = compile_file(
        project.path(
            "examples/playground/charts/reference/quick-guide.yml"
        ).read_board()
    )
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    rendered = render(result.board, executor, format="svg")
    matches = [w for w in rendered.warnings if w.code == WARN_LOW_TEXT_CONTRAST.code]
    paths = {w.path for w in matches}
    assert "rows.14.rows.0" not in paths, (
        f"header row's own dark background makes its cream ink readable: {matches}"
    )
    row3 = next((w for w in matches if w.path == "rows.14.rows.3"), None)
    assert row3 is not None, f"expected rows.14.rows.3 to warn; got: {matches}"
    assert "#f6f1e7" in row3.message, (
        f"expected the board's own cream background, not charts.background: {row3.message}"
    )


# ── validation ordering and unparseable backgrounds ─────────────────────────


def test_short_title_sizes_reports_the_real_validation_error(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A ``style.title.sizes`` override with fewer than 6 entries plus a
    heading must surface the engine's own "must have 6 entries" message —
    not an unrelated ``min() iterable argument is empty`` from the contrast
    check racing ahead of that validation."""
    board_yaml = """
rows:
  - text: |
      ### A heading
    style:
      title:
        sizes: [30, 20]
"""
    result = _render(board_yaml, tmp_path, local_project)
    assert result.board_error is not None
    assert "must have 6 entries" in result.board_error.message
    assert "min()" not in result.board_error.message


def test_unparseable_background_skips_rather_than_false_positive(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A pattern/gradient background this engine's color parser cannot read
    gives no basis for a contrast check — it must not silently fall back to
    an ancestor's background and compare against the wrong thing."""
    from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST

    board_yaml = """
rows:
  - text: |
      Body line here.
    style:
      background: "linear-gradient(#000000,#111111)"
      font:
        color: "#FFFFFF"
"""
    result = _render(board_yaml, tmp_path, local_project)
    codes = {w.code for w in result.warnings}
    assert WARN_LOW_TEXT_CONTRAST.code not in codes, (
        f"expected a skip, not a check against the wrong background: {result.warnings}"
    )


# ── unit-level: check_text_contrast / config threshold ──────────────────────


def test_check_text_contrast_skips_without_open_sink() -> None:
    """No-op (does not raise) when no collect_contrast_warnings() sink is open."""
    from dbt_charts.core.render.contrast_warning import check_text_contrast

    check_text_contrast("text_body", "#222222", "#0b1f3a", 16.0)


def test_composite_over_canvas_transparent_layer_falls_through_to_parent() -> None:
    from dbt_charts.core.render.contrast_warning import composite_over_canvas

    assert composite_over_canvas("transparent", "#0b1f3a") == "#0b1f3a"


def test_composite_over_canvas_unparseable_layer_makes_the_result_unknown() -> None:
    """A pattern/gradient/url() reference this engine's parser cannot read
    leaves no basis to assume either its own paint or whatever is behind
    it — the result is None, not a silent fall-through to the parent."""
    from dbt_charts.core.render.contrast_warning import composite_over_canvas

    assert composite_over_canvas("linear-gradient(#000,#111)", "#ffffff") is None
    assert composite_over_canvas("linear-gradient(#000,#111)", None) is None


def test_composite_over_canvas_opaque_layer_ignores_the_parent() -> None:
    from dbt_charts.core.render.contrast_warning import composite_over_canvas

    assert composite_over_canvas("#0b1f3a", None) == "#0b1f3a"
    assert composite_over_canvas("#0b1f3a", "#ffffff") == "#0b1f3a"


def test_check_text_contrast_skips_unparseable_ink() -> None:
    from dbt_charts.core.render.contrast_warning import (
        check_text_contrast,
        collect_contrast_warnings,
    )

    with collect_contrast_warnings() as records:
        check_text_contrast("text_body", "not-a-color", "#0b1f3a", 16.0)
    assert records == []


def test_check_text_contrast_composites_alpha_ink_over_background() -> None:
    """Ink with its own alpha is composited over the (opaque) background via
    ``composite_over`` before computing the ratio, instead of being skipped."""
    from dbt_charts.core.render.contrast_warning import (
        check_text_contrast,
        collect_contrast_warnings,
    )

    # 50% black over navy #0b1f3a composites to a slightly-lighter navy --
    # close enough to the background itself to still miss the floor.
    with collect_contrast_warnings() as records:
        check_text_contrast("text_body", "rgba(0,0,0,0.5)", "#0b1f3a", 16.0)
    assert len(records) == 1
    assert records[0].ink == "#06101d"


def test_check_text_contrast_uses_large_text_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ratio that clears the patched large-text floor but not the patched
    normal floor must warn only when the text is NOT large."""
    from types import SimpleNamespace

    from dbt_charts.core.render import contrast_warning
    from dbt_charts.core.render.contrast_warning import (
        check_text_contrast,
        collect_contrast_warnings,
    )

    patched_min_ratio, patched_large_text_min_ratio = 4.0, 3.0
    fake_config = SimpleNamespace(
        text_contrast=SimpleNamespace(
            min_ratio=patched_min_ratio,
            large_text_min_ratio=patched_large_text_min_ratio,
        )
    )
    monkeypatch.setattr(contrast_warning, "get_chart_rendering", lambda: fake_config)

    # #8a8a8a on #ffffff is ~3.45:1 -- clears the patched large-text floor
    # (3.0) but not the patched normal floor (4.0).
    ink, background = "#8a8a8a", "#ffffff"
    with collect_contrast_warnings() as records:
        check_text_contrast("text_body", ink, background, 30.0)  # large (>=24px)
    assert records == []

    with collect_contrast_warnings() as records:
        check_text_contrast("text_body", ink, background, 16.0)  # normal
    assert len(records) == 1
    assert records[0].floor == patched_min_ratio
    assert records[0].large_text is False


def test_check_text_contrast_threshold_reads_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The floor comes from ``get_chart_rendering().text_contrast`` — proven
    by moving it: #767676 on white is 4.54:1, which clears the real 4.5
    default, so patching the floor to 5.0 must make it warn."""
    from types import SimpleNamespace

    from dbt_charts.core.render import contrast_warning
    from dbt_charts.core.render.contrast_warning import (
        check_text_contrast,
        collect_contrast_warnings,
    )

    patched_min_ratio = 5.0
    fake_config = SimpleNamespace(
        text_contrast=SimpleNamespace(
            min_ratio=patched_min_ratio, large_text_min_ratio=1.0
        )
    )
    monkeypatch.setattr(contrast_warning, "get_chart_rendering", lambda: fake_config)

    with collect_contrast_warnings() as records:
        check_text_contrast("text_body", "#767676", "#ffffff", 16.0)
    assert len(records) == 1
    assert records[0].floor == patched_min_ratio
