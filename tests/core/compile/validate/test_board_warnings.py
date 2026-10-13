"""Authoring warnings derived from the compiled Board.

Two shapes of self-titling board, plus the orphan-chart warning that shares the
same walk:

- WARN-DOUBLE-HEADER — a board `title:` over a body that opens with its own
  heading, so the board prints two stacked headers.
- WARN-SINGLE-CHART-REDUNDANT-TITLE — a board title above a lone chart that
  carries its own title.
- WARN-UNREFERENCED-CHART — pinned here because it moved into this module.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.core.compile import compile, compile_file
from dbt_charts.core.compile.compiler import CompileResult
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.project import Project

_QUERIES_AND_CHARTS = """
queries:
  q:
    type: values
    rows:
      - {x: 1, y: 2}
charts:
  titled:
    query: q
    type: bar
    x: x
    y: y
    title: Revenue by Month
  untitled:
    query: q
    type: bar
    x: x
    y: y
"""


def _codes(yaml: str) -> list[str]:
    result = compile(yaml)
    assert result.success, result.errors
    return [w.code for w in result.warnings]


# ───────────────────────────── WARN-DOUBLE-HEADER ─────────────────────────────


def test_h1_opening_the_body_under_a_board_title_warns() -> None:
    result = compile(
        """
title: dbt charts Cloud
text: |
  # dbt charts Cloud

  Every number below is a live query.
"""
    )

    assert result.success, result.errors
    warnings = [w for w in result.warnings if w.code == "WARN-DOUBLE-HEADER"]
    assert len(warnings) == 1
    assert "dbt charts Cloud" in warnings[0].message
    assert "title:" in warnings[0].fix
    # The heading is the line the author has to delete, so it carries the mark.
    # `title:` is the other half of the pair and rides along as a related
    # location — marking `title:` alone pointed at the line that is usually
    # staying.
    assert warnings[0].path == "text"
    assert [r.path for r in warnings[0].related] == ["title"]
    assert warnings[0].related[0].message is not None


def test_h1_with_a_different_text_than_the_title_still_warns() -> None:
    """The reported AI pattern: `title:` plus an unrelated `# Other title`."""
    assert "WARN-DOUBLE-HEADER" in _codes(
        """
title: Markdown Guide
text: |
  # Markdown in dbt charts

  Prose.
"""
    )


def test_h1_body_with_no_board_title_does_not_warn() -> None:
    assert "WARN-DOUBLE-HEADER" not in _codes(
        """
text: |
  # dbt charts Cloud

  Prose.
"""
    )


def test_subheading_differing_from_the_title_does_not_warn() -> None:
    """`## Overview` under `title: Sales` is section structure, not a double header."""
    assert "WARN-DOUBLE-HEADER" not in _codes(
        """
title: Sales
text: |
  ## Overview

  Prose.
"""
    )


def test_subheading_repeating_the_title_warns() -> None:
    assert "WARN-DOUBLE-HEADER" in _codes(
        """
title: Sales
text: |
  ## sales!

  Prose.
"""
    )


def test_heading_that_is_not_the_first_block_does_not_warn() -> None:
    assert "WARN-DOUBLE-HEADER" not in _codes(
        """
title: Sales
text: |
  Intro prose first.

  # Sales
"""
    )


def test_fenced_code_block_opening_with_a_hash_comment_does_not_warn() -> None:
    """Heading truth comes from mdsvg, which reads this as a code block."""
    assert "WARN-DOUBLE-HEADER" not in _codes(
        """
title: Setup
text: |
  ```python
  # Setup
  x = 1
  ```
"""
    )


def test_heading_in_a_rows_text_block_warns() -> None:
    yaml = f"""
title: dbt charts Cloud
{_QUERIES_AND_CHARTS}
rows:
  - text: |
      # dbt charts Cloud

      Prose.
  - untitled
"""
    assert "WARN-DOUBLE-HEADER" in _codes(yaml)


def test_heading_in_a_titled_nested_section_does_not_warn() -> None:
    """Root-scoped: a section card pairing its title with a body hero is allowed."""
    yaml = f"""
title: dbt charts Cloud
{_QUERIES_AND_CHARTS}
rows:
  - title: Regional Detail
    text: |
      # Regional Detail

      Prose.
  - untitled
"""
    assert "WARN-DOUBLE-HEADER" not in _codes(yaml)


def test_heading_in_a_cols_text_cell_warns() -> None:
    """A bare text cell in the first `cols:` slot opens the body just as a row does."""
    yaml = f"""
title: dbt charts Cloud
{_QUERIES_AND_CHARTS}
cols:
  - text: |
      # dbt charts Cloud

      Prose.
  - untitled
"""
    assert "WARN-DOUBLE-HEADER" in _codes(yaml)


def test_heading_in_the_first_tab_does_not_warn() -> None:
    """A tab always labels itself (`TabItem.title` is required), so its body
    heading belongs to the tab, not to the board header."""
    yaml = f"""
title: dbt charts Cloud
{_QUERIES_AND_CHARTS}
tabs:
  items:
    - title: Overview
      text: |
        # dbt charts Cloud

        Prose.
    - title: Detail
      rows: [untitled]
"""
    assert "WARN-DOUBLE-HEADER" not in _codes(yaml)


def test_markdown_board_frontmatter_title_plus_h1_body_warns(tmp_path) -> None:
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.compile.compiler import compile_file

    boards = tmp_path / "charts"
    boards.mkdir()
    (boards / "guide.md").write_text(
        "---\nboard:\n  title: Guide\n---\n\n# Guide\n\nProse.\n"
    )
    project = FilesystemProject(tmp_path)

    result = compile_file(project.path("charts/guide.md").read_board())

    assert result.success, result.errors
    assert "WARN-DOUBLE-HEADER" in [w.code for w in result.warnings]


# ─────────────────────────── WARN-H1-BODY-NO-TITLE ────────────────────────────


def test_h1_body_with_no_title_warns() -> None:
    result = compile(
        """
text: |
  # dbt charts Cloud

  Prose.
"""
    )

    assert result.success, result.errors
    warnings = [w for w in result.warnings if w.code == "WARN-H1-BODY-NO-TITLE"]
    assert len(warnings) == 1
    assert "dbt charts Cloud" in warnings[0].message
    assert "title:" in warnings[0].fix


def test_h1_in_a_rows_text_block_with_no_title_warns() -> None:
    yaml = f"""
{_QUERIES_AND_CHARTS}
rows:
  - text: |
      # dbt charts Cloud

      Prose.
  - untitled
"""
    assert "WARN-H1-BODY-NO-TITLE" in _codes(yaml)


def test_subheading_with_no_title_does_not_warn() -> None:
    """Only a literal H1 stands in for a missing title — `##` is just structure."""
    assert "WARN-H1-BODY-NO-TITLE" not in _codes(
        """
text: |
  ## Overview

  Prose.
"""
    )


def test_prose_with_no_title_does_not_warn() -> None:
    assert "WARN-H1-BODY-NO-TITLE" not in _codes(
        """
text: |
  Just prose, no heading at all.
"""
    )


def test_h1_opening_an_untitled_nested_section_does_not_warn() -> None:
    """Root-only: the check never descends into a nested section's own body,
    same reasoning as test_heading_in_a_titled_nested_section_does_not_warn."""
    yaml = f"""
{_QUERIES_AND_CHARTS}
rows:
  - rows:
      - text: |
          # Regional Detail

          Prose.
  - untitled
"""
    assert "WARN-H1-BODY-NO-TITLE" not in _codes(yaml)


# ───────────────────── WARN-SINGLE-CHART-REDUNDANT-TITLE ──────────────────────


def test_board_title_over_a_single_titled_chart_warns() -> None:
    yaml = f"""
title: Revenue
{_QUERIES_AND_CHARTS}
rows: [titled]
"""
    result = compile(yaml)

    assert result.success, result.errors
    warnings = [
        w for w in result.warnings if w.code == "WARN-SINGLE-CHART-REDUNDANT-TITLE"
    ]
    assert len(warnings) == 1
    assert "Revenue by Month" in warnings[0].message
    assert warnings[0].path == "charts.titled.title"
    assert warnings[0].chart == "titled"


def test_single_titled_chart_inside_a_titled_section_does_not_warn() -> None:
    """A section title in between makes three headers, not the two the message claims."""
    yaml = f"""
title: Revenue
{_QUERIES_AND_CHARTS}
rows:
  - title: Section A
    rows: [titled]
"""
    assert "WARN-SINGLE-CHART-REDUNDANT-TITLE" not in _codes(yaml)


def test_single_titled_chart_inside_an_untitled_wrapper_still_warns() -> None:
    """An untitled wrapper adds no header, so the board still stacks exactly two."""
    yaml = f"""
title: Revenue
{_QUERIES_AND_CHARTS}
rows:
  - cols: [titled]
"""
    assert "WARN-SINGLE-CHART-REDUNDANT-TITLE" in _codes(yaml)


def test_single_untitled_chart_under_a_board_title_does_not_warn() -> None:
    """The board title is the only header — that is the shape we want."""
    yaml = f"""
title: Revenue
{_QUERIES_AND_CHARTS}
rows: [untitled]
"""
    assert "WARN-SINGLE-CHART-REDUNDANT-TITLE" not in _codes(yaml)


def test_single_titled_chart_with_no_board_title_does_not_warn() -> None:
    yaml = f"""
{_QUERIES_AND_CHARTS}
rows: [titled]
"""
    assert "WARN-SINGLE-CHART-REDUNDANT-TITLE" not in _codes(yaml)


def test_two_charts_under_a_board_title_does_not_warn() -> None:
    yaml = f"""
title: Revenue
{_QUERIES_AND_CHARTS}
rows: [titled, untitled]
"""
    assert "WARN-SINGLE-CHART-REDUNDANT-TITLE" not in _codes(yaml)


def test_single_kpi_under_a_board_title_does_not_warn() -> None:
    """KpiChart has no title envelope — it labels itself with `label:`."""
    yaml = """
title: Revenue
queries:
  q:
    type: values
    rows:
      - {x: 1}
charts:
  score:
    query: q
    type: kpi
    value: x
    label: Revenue
rows: [score]
"""
    assert "WARN-SINGLE-CHART-REDUNDANT-TITLE" not in _codes(yaml)


def test_single_callout_under_a_board_title_does_not_warn() -> None:
    """A callout's title is a prose lead-in, not a chart header."""
    yaml = """
title: Heads Up
charts:
  note:
    type: callout
    message: Numbers refresh hourly.
    title: Heads Up
rows: [note]
"""
    assert "WARN-SINGLE-CHART-REDUNDANT-TITLE" not in _codes(yaml)


def test_single_titled_chart_alongside_prose_does_not_warn() -> None:
    """Prose between the two titles means the board does not read as self-headed."""
    yaml = f"""
title: Revenue
{_QUERIES_AND_CHARTS}
rows:
  - text: Revenue held flat through Q3 while volume grew.
  - titled
"""
    assert "WARN-SINGLE-CHART-REDUNDANT-TITLE" not in _codes(yaml)


# ─────────────────────── WARN-UNREFERENCED-CHART (moved) ──────────────────────


def test_unreferenced_chart_still_warns_after_the_move() -> None:
    yaml = f"""
title: Revenue
{_QUERIES_AND_CHARTS}
rows: [titled]
"""
    result = compile(yaml)

    assert result.success, result.errors
    orphans = [w for w in result.warnings if w.code == "WARN-UNREFERENCED-CHART"]
    assert len(orphans) == 1
    assert "untitled" in orphans[0].message


_QUERIES_AND_THREE_CHARTS = """
queries:
  q:
    type: values
    rows:
      - {x: 1, y: 2}
charts:
  titled:
    query: q
    type: bar
    x: x
    y: y
    title: Revenue by Month
  untitled:
    query: q
    type: bar
    x: x
    y: y
  orphan:
    query: q
    type: bar
    x: x
    y: y
"""


def test_chart_focus_with_every_chart_in_layout_does_not_warn() -> None:
    yaml = f"""
title: Revenue
{_QUERIES_AND_CHARTS}
chart_focus: titled
rows: [titled, untitled]
"""
    result = compile(yaml)

    assert result.success, result.errors
    codes = [w.code for w in result.warnings]
    assert "WARN-UNREFERENCED-CHART" not in codes


def test_chart_focus_still_warns_for_a_chart_no_layout_places() -> None:
    yaml = f"""
title: Revenue
{_QUERIES_AND_THREE_CHARTS}
chart_focus: titled
rows: [titled, untitled]
"""
    result = compile(yaml)

    assert result.success, result.errors
    orphans = [w for w in result.warnings if w.code == "WARN-UNREFERENCED-CHART"]
    assert len(orphans) == 1
    assert "orphan" in orphans[0].message


def test_unfocused_board_orphan_warning_is_unchanged() -> None:
    yaml = f"""
title: Revenue
{_QUERIES_AND_THREE_CHARTS}
rows: [titled, untitled]
"""
    result = compile(yaml)

    assert result.success, result.errors
    orphans = [w for w in result.warnings if w.code == "WARN-UNREFERENCED-CHART"]
    assert len(orphans) == 1
    assert "orphan" in orphans[0].message


def test_chart_focus_key_and_chart_flag_agree_on_orphan_warnings() -> None:
    """`chart_focus:` in YAML and `--chart` (post-compile focus_on_chart) must not drift.

    `--chart` compiles the unfocused board (so warnings see the full layout) and
    narrows only afterward; `chart_focus:` must produce the identical warning set.
    """
    yaml = f"""
title: Revenue
{_QUERIES_AND_THREE_CHARTS}
rows: [titled, untitled]
"""
    flag_result = compile(yaml)
    assert flag_result.success, flag_result.errors
    assert flag_result.board is not None
    flag_orphans = {
        w.message for w in flag_result.warnings if w.code == "WARN-UNREFERENCED-CHART"
    }

    key_yaml = f"""
title: Revenue
chart_focus: titled
{_QUERIES_AND_THREE_CHARTS}
rows: [titled, untitled]
"""
    key_result = compile(key_yaml)
    assert key_result.success, key_result.errors
    key_orphans = {
        w.message for w in key_result.warnings if w.code == "WARN-UNREFERENCED-CHART"
    }

    assert flag_orphans == key_orphans
    assert len(flag_orphans) == 1


def test_double_header_range_lands_on_the_heading_line_not_the_whole_text_block() -> (
    None
):
    """The mark is the one line to delete, not the whole body.

    `text:` is a block scalar spanning many lines; without needle narrowing the
    range would cover all of them and the author would have to find the heading
    themselves.
    """
    yaml = """title: dbt charts Cloud
text: |
  # dbt charts Cloud

  Every number below is a live query.

  More prose here.
"""
    result = compile(yaml, file="f.yaml")

    assert result.success, result.errors
    (w,) = [d for d in result.warnings if d.code == "WARN-DOUBLE-HEADER"]
    assert w.range is not None
    # "# dbt charts Cloud" is the third line of the file (1-based).
    assert w.range.start_line == 3
    assert w.range.end_line == 3
    assert w.range.columns is not None
    # And the related `title:` mark resolved too.
    assert w.related[0].range is not None
    assert w.related[0].range.start_line == 1


def test_double_header_keeps_the_block_range_when_the_heading_is_reflowed() -> None:
    """A folded (`>`) body reflows physical lines, so the needle cannot be
    matched against one — the coarse mark is kept rather than guessing."""
    yaml = """title: Sales
text: >
  # Sales

  Prose.
"""
    result = compile(yaml, file="f.yaml")

    assert result.success, result.errors
    warnings = [d for d in result.warnings if d.code == "WARN-DOUBLE-HEADER"]
    for w in warnings:
        assert w.range is not None
        # Not narrowed to a single line by a needle that could not be verified.
        assert w.range.columns is None


# --- WARN-FORMAT-UNUSED-NAME -------------------------------------------------

_FORMAT_BOARD = """
title: T
{style}
queries:
  q:
    type: values
{query_extra}    rows:
      - {{month: 2026-01-01, revenue: 1234567}}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
{chart_extra}rows:
  - c
"""


def _format_board(style: str, query_extra: str = "", chart_extra: str = "") -> str:
    return _FORMAT_BOARD.format(
        style=style, query_extra=query_extra, chart_extra=chart_extra
    )


def _unused(result: CompileResult) -> list[Diagnostic]:
    return [w for w in result.warnings if w.code == "WARN-FORMAT-UNUSED-NAME"]


_MINE = "style:\n  formats:\n    mine: ',.0f'"


def test_unused_format_name_warns_once_and_suggests_the_engine_name() -> None:
    result = compile(_format_board("style:\n  formats:\n    date_sho: '%b %Y'"))
    assert result.success
    (warning,) = _unused(result)
    assert warning.path == "style.formats.date_sho"
    assert "date_short" in warning.message


def test_misspelled_date_name_lists_the_date_names() -> None:
    result = compile(_format_board("style:\n  formats:\n    date: '%b %Y'"))
    (warning,) = _unused(result)
    assert "date_short" in warning.message


def test_unused_name_with_no_engine_relative_gets_the_generic_message() -> None:
    result = compile(_format_board("style:\n  formats:\n    default: ',.0f'"))
    (warning,) = _unused(result)
    assert "did you mean" not in warning.message.lower()


def test_name_a_query_formats_column_uses_does_not_warn() -> None:
    board = _format_board(_MINE, query_extra="    formats:\n      revenue: mine\n")
    result = compile(board)
    assert result.success
    assert not _unused(result)


def test_redefined_engine_name_does_not_warn() -> None:
    result = compile(_format_board("style:\n  formats:\n    number: ',.0f'"))
    assert not _unused(result)


def test_inline_chart_query_formats_count_as_used() -> None:
    board = f"""
title: T
{_MINE}
charts:
  c:
    query:
      type: values
      formats:
        revenue: mine
      rows:
        - {{month: 2026-01-01, revenue: 1234567}}
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
    result = compile(board)
    assert result.success
    assert not _unused(result)


def test_text_format_filter_reference_counts_as_used() -> None:
    board = _format_board(
        _MINE, chart_extra="    title: \"{{ 1234 | format('mine') }}\"\n"
    )
    result = compile(board)
    assert result.success
    assert not _unused(result)


def test_name_used_only_by_a_nested_board_counts_as_used() -> None:
    board = f"""
title: T
{_MINE}
rows:
  - title: Inner
    queries:
      q:
        type: values
        formats:
          revenue: mine
        rows:
          - {{month: 2026-01-01, revenue: 1234567}}
    charts:
      c:
        query: q
        type: bar
        x: month
        y: revenue
    rows:
      - c
"""
    result = compile(board)
    assert result.success
    assert not _unused(result)


def _compile_under_meta(
    tmp_path: Path, local_project: Callable[[Path], Project], board_style: str
) -> CompileResult:
    reset_config()
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/meta.yml").write_text(
        "style:\n  formats:\n    shared: ',.0f'\n"
    )
    (tmp_path / "charts/board.yaml").write_text(_format_board(board_style))
    project = local_project(tmp_path)
    return compile_file(project.path("charts/board.yaml").read_board())


def test_name_inherited_from_meta_yml_does_not_warn_in_a_sibling(
    tmp_path: Path, local_project: Callable[[Path], Project]
) -> None:
    result = _compile_under_meta(tmp_path, local_project, "")
    assert result.success
    assert not _unused(result)


def test_unused_name_defined_in_the_board_file_warns_under_meta(
    tmp_path: Path, local_project: Callable[[Path], Project]
) -> None:
    result = _compile_under_meta(tmp_path, local_project, _MINE)
    assert [w.path for w in _unused(result)] == ["style.formats.mine"]
