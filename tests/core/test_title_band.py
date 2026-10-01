"""Row title band: shared first baseline and shared body top within a cols row."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.board.normalized import TitleShift
from dbt_charts.core.render.title_band import TitleMetrics, row_title_shifts

ZERO = TitleShift(title_dy=0.0, body_dy=0.0)


def test_equal_titles_compute_no_shift() -> None:
    metrics = TitleMetrics(ascent=30.0, tail=8.0)
    assert row_title_shifts([metrics, metrics, metrics]) == [ZERO, ZERO, ZERO]


def test_mixed_sizes_share_the_largest_ascent_and_one_body_top() -> None:
    wide = TitleMetrics(ascent=30.0, tail=8.0)
    narrow = TitleMetrics(ascent=27.0, tail=7.0)
    shifts = row_title_shifts([wide, narrow])
    assert shifts == [ZERO, TitleShift(title_dy=3.0, body_dy=4.0)]
    for metric, shift in zip([wide, narrow], shifts, strict=True):
        assert metric.ascent + shift.title_dy == 30.0
        assert metric.ascent + metric.tail + shift.body_dy == 38.0


def test_wrapped_title_drops_neighbor_bodies_but_not_its_own_baseline() -> None:
    wrapped = TitleMetrics(ascent=30.0, tail=8.0 + 22.0)
    single = TitleMetrics(ascent=30.0, tail=8.0)
    assert row_title_shifts([wrapped, single]) == [
        ZERO,
        TitleShift(title_dy=0.0, body_dy=22.0),
    ]


def test_untitled_items_reserve_no_band() -> None:
    titled = TitleMetrics(ascent=30.0, tail=8.0)
    assert row_title_shifts([titled, None, titled]) == [ZERO, ZERO, ZERO]
    assert row_title_shifts([None, None]) == [ZERO, ZERO]


def test_band_is_the_lowest_shifted_title_block() -> None:
    metrics = [TitleMetrics(30.0, 8.0), TitleMetrics(20.0, 25.0)]
    shifts = row_title_shifts(metrics)
    bottoms = [
        m.ascent + s.title_dy + m.tail for m, s in zip(metrics, shifts, strict=True)
    ]
    assert bottoms == [38.0, 55.0]
    bodies = [
        m.ascent + m.tail + s.body_dy for m, s in zip(metrics, shifts, strict=True)
    ]
    assert bodies == [55.0, 55.0]


def test_a_body_cannot_sit_above_its_title() -> None:
    with pytest.raises(ValueError, match="body cannot sit above"):
        TitleShift(title_dy=3.0, body_dy=1.0)
