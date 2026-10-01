"""Penalty-driven column packing.

The packer chooses where a line sequence breaks across columns. It follows
TeX's page-breaker model: every candidate break carries a cost, and the split
minimizing total cost wins. Balance is one cost term among several, so a widow
penalty can outrank a small imbalance -- which is the correct trade, and the
reason this is not a chain of if-then rules.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.render.column_packer import (
    MIN_CHARS,
    MIN_LINES_PER_COLUMN,
    PackUnit,
    choose_grid,
    columns_earned,
    pack_columns,
    span_boxes,
)


def paragraphs(*counts: int, advance: float = 20.0) -> list[PackUnit]:
    units: list[PackUnit] = []
    for block_id, n in enumerate(counts):
        units += [
            PackUnit(
                advance=advance,
                space_before=20.0 if (i == 0 and block_id > 0) else 0.0,
                block_id=block_id,
                line_index=i,
                line_total=n,
                splittable=True,
                keep_with_next=False,
            )
            for i in range(n)
        ]
    return units


class TestContinuousFlow:
    def test_a_paragraph_splits_across_a_boundary(self) -> None:
        """The whole point: one paragraph may occupy two columns."""
        units = paragraphs(12)
        assignment = pack_columns(units, columns=2)
        cols = set(assignment)
        assert cols == {0, 1}, "both columns must be used"
        # the single block appears in both columns
        blocks_in_0 = {
            u.block_id for u, c in zip(units, assignment, strict=True) if c == 0
        }
        blocks_in_1 = {
            u.block_id for u, c in zip(units, assignment, strict=True) if c == 1
        }
        assert blocks_in_0 == blocks_in_1 == {0}

    def test_lines_stay_in_document_order(self) -> None:
        units = paragraphs(5, 5, 5)
        assignment = pack_columns(units, columns=3)
        assert assignment == sorted(assignment), (
            "a column may not precede its own start"
        )


class TestWidowsAndOrphans:
    def test_no_orphan_first_line_at_a_column_bottom(self) -> None:
        """A paragraph must not contribute exactly its first line to a column."""
        units = paragraphs(4, 9)
        assignment = pack_columns(units, columns=2)
        for col in (0, 1):
            in_col = [u for u, c in zip(units, assignment, strict=True) if c == col]
            for block_id in {u.block_id for u in in_col}:
                lines = [u for u in in_col if u.block_id == block_id]
                total = lines[0].line_total
                if total > 1 and lines[0].line_index == 0 and len(lines) < total:
                    assert len(lines) >= 2, f"orphan in column {col}"

    def test_no_widow_last_line_alone_at_a_column_top(self) -> None:
        units = paragraphs(9, 4)
        assignment = pack_columns(units, columns=2)
        for col in (0, 1):
            in_col = [u for u, c in zip(units, assignment, strict=True) if c == col]
            for block_id in {u.block_id for u in in_col}:
                lines = [u for u in in_col if u.block_id == block_id]
                total = lines[0].line_total
                if (
                    total > 1
                    and lines[-1].line_index == total - 1
                    and len(lines) < total
                ):
                    assert len(lines) >= 2, f"widow in column {col}"


class TestUnsplittableAndKeepWithNext:
    def test_an_unsplittable_block_lands_in_one_column(self) -> None:
        units = paragraphs(6)
        units.append(
            PackUnit(
                advance=90.0,
                space_before=20.0,
                block_id=99,
                line_index=0,
                line_total=1,
                splittable=False,
                keep_with_next=False,
            )
        )
        units += paragraphs(6)
        assignment = pack_columns(units, columns=2)
        cols = {c for u, c in zip(units, assignment, strict=True) if u.block_id == 99}
        assert len(cols) == 1

    def test_a_heading_is_never_last_in_a_column(self) -> None:
        units = paragraphs(7)
        heading_at = len(units)
        units.append(
            PackUnit(
                advance=26.0,
                space_before=30.0,
                block_id=50,
                line_index=0,
                line_total=1,
                splittable=False,
                keep_with_next=True,
            )
        )
        units += paragraphs(7)
        assignment = pack_columns(units, columns=2)
        heading_col = assignment[heading_at]
        after = list(assignment[heading_at + 1 :])
        assert after[:2] == [
            heading_col,
            heading_col,
        ], "a heading must keep at least two following lines with it"


class TestBalance:
    def test_columns_are_balanced_when_nothing_forbids_it(self) -> None:
        units = paragraphs(10, 10)
        assignment = pack_columns(units, columns=2)
        heights = [
            sum(
                u.advance + u.space_before
                for u, c in zip(units, assignment, strict=True)
                if c == k
            )
            for k in (0, 1)
        ]
        assert abs(heights[0] - heights[1]) <= 2 * 20.0

    def test_balance_yields_to_a_widow_penalty(self) -> None:
        """Perfect balance that strands a line must lose to a slightly uneven split."""
        units = paragraphs(11, 11)
        assignment = pack_columns(units, columns=2)
        first_col_blocks = [
            u
            for u, c in zip(units, assignment, strict=True)
            if c == 0 and u.block_id == 1
        ]
        # if block 1 appears in column 0 at all, it must bring >= 2 lines
        if first_col_blocks:
            assert len(first_col_blocks) >= 2


class TestColumnOrder:
    def test_column_heights_are_non_increasing(self) -> None:
        """An uneven remainder belongs in the earlier column, never the later.

        Text is read top-left first, so a short first column beside a long
        second one reads as a mistake. A flat undershoot surcharge cannot
        express this -- every split has as many short columns as long ones --
        so the packer weights the surcharge by how early the column is.
        """
        for total in (7, 9, 11, 13, 15, 21):
            assignment = pack_columns(paragraphs(total), columns=2)
            heights = [assignment.count(k) for k in range(2)]
            assert heights == sorted(heights, reverse=True), (
                f"{total} lines split {heights}; earlier column must not be shorter"
            )

    def test_non_increasing_holds_for_three_columns(self) -> None:
        for total in (10, 14, 17, 20):
            assignment = pack_columns(paragraphs(total), columns=3)
            heights = [assignment.count(k) for k in range(3)]
            assert heights == sorted(heights, reverse=True), (
                f"{total} lines split {heights}"
            )


class TestSpanBoxes:
    """A span is the box card i of an N-up ``cols:`` row occupies."""

    @pytest.mark.parametrize("grid", [2, 3])
    def test_a_full_width_container_offers_the_whole_grid(self, grid: int) -> None:
        boxes = span_boxes(1128.0, 1128.0, grid, 20.0)
        assert len(boxes) == grid
        assert boxes[0][0] == 0.0
        end = boxes[-1][0] + boxes[-1][1]
        assert end == pytest.approx(1128.0)
        for (x, w), (next_x, _) in zip(boxes, boxes[1:], strict=False):
            assert next_x - (x + w) == pytest.approx(20.0)

    def test_a_two_thirds_card_offers_two_thirds_on_the_board_grid(self) -> None:
        third = (1128.0 - 40.0) / 3
        boxes = span_boxes(2 * third + 20.0, 1128.0, 3, 20.0)
        assert [x for x, _ in boxes] == pytest.approx([0.0, third + 20.0])
        assert [w for _, w in boxes] == pytest.approx([third, third])

    def test_a_card_a_hair_short_of_two_spans_never_overflows(self) -> None:
        third = (1128.0 - 40.0) / 3
        container = 2 * third + 20.0 - 6.7
        boxes = span_boxes(container, 1128.0, 3, 20.0)
        assert len(boxes) == 2
        assert boxes[1][0] + boxes[1][1] == pytest.approx(container)

    @pytest.mark.parametrize("fraction", [0.25, 1 / 3, 0.5])
    def test_a_card_under_two_spans_offers_itself_whole(self, fraction: float) -> None:
        container = 1128.0 * fraction
        assert span_boxes(container, 1128.0, 3, 20.0) == ((0.0, container),)


class TestChooseGrid:
    CHAR_PX = 6.49  # 14px body serif

    def test_thirds_is_the_default(self) -> None:
        assert choose_grid(1128.0, 20.0, 16.0, self.CHAR_PX) == 3

    def test_halves_when_a_third_holds_fewer_than_the_floor(self) -> None:
        third_content = (900.0 - 2 * 20.0) / 3 - 2 * 16.0
        assert third_content / self.CHAR_PX < MIN_CHARS
        assert choose_grid(900.0, 20.0, 16.0, self.CHAR_PX) == 2

    def test_one_up_when_a_half_is_under_the_floor(self) -> None:
        half_content = (600.0 - 20.0) / 2 - 2 * 16.0
        assert half_content / self.CHAR_PX < MIN_CHARS
        assert choose_grid(600.0, 20.0, 16.0, self.CHAR_PX) == 1

    def test_larger_body_type_flips_a_wide_board_to_halves(self) -> None:
        assert choose_grid(1128.0, 20.0, 16.0, 7.4) == 2


class TestOneUpGrid:
    def test_a_one_up_grid_offers_the_container_whole(self) -> None:
        assert span_boxes(568.0, 568.0, 1, 20.0) == ((0.0, 568.0),)

    def test_a_card_on_a_one_up_grid_is_whole_too(self) -> None:
        assert span_boxes(300.0, 568.0, 1, 20.0) == ((0.0, 300.0),)


class TestColumnsEarned:
    def test_a_short_passage_is_one_column(self) -> None:
        for lines in (0, 1, 2, MIN_LINES_PER_COLUMN * 2 - 1):
            assert columns_earned(lines, spans=3) == 1

    def test_each_extra_column_must_earn_the_line_floor(self) -> None:
        assert columns_earned(2 * MIN_LINES_PER_COLUMN, spans=3) == 2
        assert columns_earned(3 * MIN_LINES_PER_COLUMN, spans=3) == 3

    def test_spans_bound_the_count(self) -> None:
        assert columns_earned(500, spans=2) == 2
        assert columns_earned(500, spans=1) == 1


class TestSpanBoxesAtTheDefaultGap:
    """A row of cards with no gap still holds every span at any board width."""

    @pytest.mark.parametrize("grid", [2, 3])
    def test_a_full_width_container_offers_the_whole_grid_at_any_width(
        self, grid: int
    ) -> None:
        for tenths in range(3000, 24000, 7):
            width = tenths / 10
            assert len(span_boxes(width, width, grid, 0.0)) == grid, width

    def test_a_card_a_pixel_short_of_two_spans_keeps_both(self) -> None:
        boxes = span_boxes(751.5, 1128.0, 3, 0.0)
        assert len(boxes) == 2

    def test_a_card_under_two_spans_is_not_rounded_up(self) -> None:
        assert len(span_boxes(1128.0 * 0.5, 1128.0, 3, 0.0)) == 1
