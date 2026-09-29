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

from dbt_charts.core.compile import compile

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


# ─────────────────────────── WARN-ADJACENT-TEXT-ROWS ──────────────────────────


# A passage long enough to earn a second column, so long enough to fragment the
# page when another one is stacked under it.
_PASSAGE = (
    "Three replicates per level, twelve runs, every one at 77 of 77 cases, and no "
    "level beats medium. Accuracy falls monotonically as effort rises, while "
    "latency climbs and output tokens more than triple. Those rising counts are "
    "the point: the budget genuinely moved, so this is not a flag that failed to "
    "fire. It is a model that thinks harder and gets slightly worse at the task, "
    "which is the opposite of the direction the hypothesis predicted it would "
    "move. Cost is the one axis where the cheap tier keeps its advantage, and it "
    "was never the axis in question: an order of magnitude per run buys nothing "
    "if the answer is wrong and arrives later. Latency is not ambiguous either — "
    "it climbs at every level and never once reverses, so the budget bought a "
    "slower model rather than a better one, on both benchmarks and at every "
    "difficulty band the canary separates."
)
_CAPTION = "A one-line caption introducing the passage below it."
_TABLE = "| effort | accuracy |\n|---|---:|\n" + "| medium | 66.7% |\n" * 120


def _prose(text: str = _PASSAGE, **extra: object) -> dict[str, object]:
    """One `- text:` row. A fresh dict per call — a shared one dumps as a YAML
    anchor, which compiles but makes the fixture unreadable in a failure."""
    return {**extra, "text": text}


def _board(**board: object) -> str:
    import yaml

    return yaml.safe_dump(board, sort_keys=False, allow_unicode=True, width=10_000)


def _adjacent(yaml_text: str) -> list:
    result = compile(yaml_text)
    assert result.success, result.errors
    return [w for w in result.warnings if w.code == "WARN-ADJACENT-TEXT-ROWS"]


def test_two_adjacent_text_rows_warn() -> None:
    (w,) = _adjacent(_board(rows=[_prose(), _prose()]))
    assert w.path == "rows.1"
    assert [r.path for r in w.related] == ["rows.0"]


def test_a_run_of_four_text_rows_warns_once() -> None:
    warnings = _adjacent(_board(rows=[_prose() for _ in range(4)]))
    assert len(warnings) == 1
    assert warnings[0].message.startswith("4 text-only rows")


def test_two_runs_separated_by_a_chart_warn_separately() -> None:
    rows = [_prose(), _prose(), "untitled"]
    yaml_text = _QUERIES_AND_CHARTS + _board(rows=rows + [_prose(), _prose()])
    assert len(_adjacent(yaml_text)) == 2


def test_a_text_row_next_to_a_chart_row_does_not_warn() -> None:
    yaml_text = _QUERIES_AND_CHARTS + _board(rows=[_prose(), "untitled", _prose()])
    assert not _adjacent(yaml_text)


def test_adjacent_prose_in_cols_does_not_warn() -> None:
    """Side-by-side prose is a deliberate spread, not a fragmented flow."""
    assert not _adjacent(_board(cols=[_prose(), _prose()]))


def test_titled_prose_rows_warn() -> None:
    """The shape real boards are written in — a title per section of prose.

    Two of them fragment exactly the way two untitled blocks do, and merging
    keeps both sections: the first title heads the block, the second becomes a
    heading inside its prose.
    """
    yaml_text = _board(
        rows=[
            _prose(title="Accuracy declines"),
            _prose(title="Where an effect would have hidden"),
        ]
    )
    assert len(_adjacent(yaml_text)) == 1


def test_a_row_holding_a_chart_beside_its_prose_does_not_participate() -> None:
    """A slot with a layout of its own is a section, not a block of prose."""
    yaml_text = _QUERIES_AND_CHARTS + _board(
        rows=[
            _prose(),
            _prose(title="A section", rows=["untitled"]),
        ]
    )
    assert not _adjacent(yaml_text)


def test_a_table_only_row_does_not_participate() -> None:
    """Merging a table into a prose column squeezes it — the split is right."""
    from dbt_charts.core.compile.validate.board_warnings import (
        _MIN_FRAGMENTING_CHARS,
    )

    # Pin the size floor out of the picture, so this test can only pass via the
    # no-paragraph branch. Asserting a literal here let a raised floor silently
    # take over as the reason and delete the coverage.
    assert len(_TABLE) > _MIN_FRAGMENTING_CHARS
    assert not _adjacent(_board(rows=[_prose(), _prose(_TABLE)]))


def test_a_self_styled_prose_row_does_not_participate() -> None:
    """Its `style:` is what makes it a separate row — one block, one style."""
    yaml_text = _board(
        rows=[
            _prose(),
            _prose(style={"text": {"column": {"max_number": 3}}}),
        ]
    )
    assert not _adjacent(yaml_text)


def test_a_short_caption_row_does_not_participate() -> None:
    """One column at any width, so stacking it misaligns nothing."""
    assert not _adjacent(_board(rows=[_prose(), _prose(_CAPTION)]))
    assert not _adjacent(_board(rows=[_prose(_CAPTION), _prose()]))


def test_adjacent_text_rows_inside_a_nested_section_warn() -> None:
    """A section's rows fragment exactly the way the root's do."""
    yaml_text = _board(
        rows=[
            {
                "title": "A section",
                "rows": [_prose(), _prose()],
            }
        ]
    )
    assert len(_adjacent(yaml_text)) == 1


def test_the_size_floor_is_two_columns_worth_of_the_packers_own_measure() -> None:
    """The floor approximates render constants compile cannot import.

    `viable_column_count` is `line_count // MIN_LINES_PER_COLUMN`, so six lines
    buy the *first* column and twelve buy the second — a floor derived from six
    lines fires on blocks that provably render single-column. The test suite is
    not bound by the compile-to-render layering, so it can pin the arithmetic
    against the real constants and catch a drift in either.
    """
    from dbt_charts.core.compile.validate.board_warnings import (
        _MIN_FRAGMENTING_CHARS,
    )
    from dbt_charts.core.render.column_packer import (
        _MULTI_COLUMN_AIM_CHARS,
        MIN_LINES_PER_COLUMN,
        viable_column_count,
    )

    assert viable_column_count(MIN_LINES_PER_COLUMN * 2 - 1, 3) == 1
    assert viable_column_count(MIN_LINES_PER_COLUMN * 2, 3) == 2
    assert _MIN_FRAGMENTING_CHARS == 2 * MIN_LINES_PER_COLUMN * _MULTI_COLUMN_AIM_CHARS


def test_an_exclusion_ends_a_run_rather_than_being_skipped_over() -> None:
    """Passage, caption, passage is zero warnings — not one spanning the caption.

    Deliberate, and the registered doc says so: the author put something between
    the two passages. Pinned because the alternative (skip the excluded row and
    keep the run going) is a one-word change that no other test would catch.
    """
    assert not _adjacent(_board(rows=[_prose(), _prose(_CAPTION), _prose()]))


def test_a_conditionally_visible_prose_row_does_not_participate() -> None:
    """Merging it into the row above would publish prose the author hid."""
    yaml_text = _board(
        variables={"show_more": {"data_type": "boolean", "default": False}},
        rows=[_prose(), _prose(visible="show_more")],
    )
    assert not _adjacent(yaml_text)


def test_a_details_disclosure_prose_row_does_not_participate() -> None:
    """Merging it would collapse the disclosure into the block above it."""
    yaml_text = _board(
        variables={"open_notes": {"data_type": "boolean", "default": False}},
        rows=[_prose(), _prose(details="open_notes")],
    )
    assert not _adjacent(yaml_text)


def test_a_prose_row_with_an_authored_height_does_not_participate() -> None:
    """One merged block has one height."""
    assert not _adjacent(_board(rows=[_prose(), _prose(height="200px")]))
