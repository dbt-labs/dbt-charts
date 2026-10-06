"""``natural_content_width`` is the least budget at which the allocator never overflows."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.style.authored import TableColumnConfig
from dbt_charts.core.render.chart.table_support import (
    calculate_column_layout,
    natural_content_width,
)

_HEADROOM = 0.15
_CELL_PAD = 10.0


def _natural(
    columns: list[str],
    configs: dict[str, TableColumnConfig],
    demands: dict[str, float],
    headers: dict[str, float] | None = None,
    floors: dict[str, float] | None = None,
) -> float | None:
    return natural_content_width(
        columns,
        configs,
        demands=demands,
        header_demands=dict.fromkeys(columns, 0.0) | (headers or {}),
        word_floors=dict.fromkeys(columns, 0.0) | (floors or {}),
        content_headroom=_HEADROOM,
        cell_pad=_CELL_PAD,
    )


def test_auto_columns_sum_their_demands() -> None:
    assert _natural(["a", "b"], {}, {"a": 100, "b": 60}) == 160


def test_header_and_word_floor_raise_a_column() -> None:
    # A header needs its label plus both cell paddings to stay on one line.
    assert (
        _natural(
            ["a", "b"], {}, {"a": 100, "b": 60}, headers={"a": 130}, floors={"b": 90}
        )
        == 240
    )


def test_px_pin_is_taken_as_is() -> None:
    configs = {"a": TableColumnConfig(width=300)}
    assert _natural(["a", "b"], configs, {"a": 100, "b": 60}) == 360


def test_max_width_caps_an_auto_column() -> None:
    configs = {"b": TableColumnConfig(max_width=250)}
    got = _natural(["a", "b"], configs, {"a": 100, "b": 900})
    assert got == pytest.approx(100 * (1 + _HEADROOM) + 250)


def test_compact_columns_get_headroom_when_text_columns_exist() -> None:
    got = _natural(["a", "b"], {}, {"a": 100, "b": 400})
    assert got == pytest.approx(100 * (1 + _HEADROOM) + 400)


def test_percent_pins_solve_against_the_rest() -> None:
    configs = {"a": TableColumnConfig(width="25%")}
    assert _natural(["a", "b"], configs, {"a": 10, "b": 150}) == pytest.approx(200)


def test_percent_pins_at_or_over_100_have_no_natural_width() -> None:
    configs = {
        "a": TableColumnConfig(width="60%"),
        "b": TableColumnConfig(width="40%"),
    }
    assert _natural(["a", "b", "c"], configs, {"c": 80}) is None


@pytest.mark.parametrize(
    ("configs", "demands", "headers", "floors"),
    [
        ({}, {"a": 90, "b": 70, "c": 120}, {"a": 120, "b": 40, "c": 50}, {}),
        (
            {"a": TableColumnConfig(width=200)},
            {"a": 90, "b": 70, "c": 120},
            {},
            {"b": 60},
        ),
        (
            {"c": TableColumnConfig(max_width=300)},
            {"a": 90, "b": 500, "c": 900},
            {},
            {"b": 80, "c": 100},
        ),
        ({}, {"a": 80, "b": 300, "c": 400, "d": 90}, {"a": 100}, {"b": 70}),
        (
            {"a": TableColumnConfig(width="30%")},
            {"a": 90, "b": 300, "c": 80},
            {},
            {},
        ),
        (
            {"a": TableColumnConfig(max_width=40)},
            {"a": 200, "b": 300},
            {},
            {"b": 300},
        ),
    ],
    ids=[
        "compact",
        "px-pin",
        "max-width",
        "mixed-headroom",
        "percent",
        "max-width-on-compact",
    ],
)
def test_layout_at_natural_width_does_not_overflow(
    configs: dict[str, TableColumnConfig],
    demands: dict[str, float],
    headers: dict[str, float],
    floors: dict[str, float],
) -> None:
    columns = list(demands)
    budget = _natural(columns, configs, demands, headers, floors)
    assert budget is not None
    widths, _, actual = calculate_column_layout(
        columns,
        configs,
        budget,
        demands=demands,
        header_demands=headers,
        word_floors=floors,
        content_headroom=_HEADROOM,
        cell_pad=_CELL_PAD,
    )
    assert actual <= budget + 0.01
    for col in columns:
        cfg = configs.get(col)
        if cfg is not None and cfg.width is not None:
            continue
        # One-line header: label plus both cell paddings.
        assert widths[col] >= headers.get(col, 0.0) + 2 * _CELL_PAD - 0.01
        # No column is squeezed below its demand (px cap aside).
        cap = cfg.max_width if cfg is not None else None
        want = demands[col] if cap is None else min(demands[col], cap)
        assert widths[col] >= want - 0.01


def test_percent_max_width_does_not_cap_the_natural_width() -> None:
    """A percentage cap is relative to the final width, unknown while hugging."""
    configs = {"b": TableColumnConfig(max_width="50%")}
    got = _natural(["a", "b"], configs, {"a": 100, "b": 400})
    assert got == pytest.approx(100 * (1 + _HEADROOM) + 400)
