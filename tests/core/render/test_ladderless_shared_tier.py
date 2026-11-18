"""An axis whose ticks Vega-style rules draw at render time (no baked ladder)
labels them at one shared tier, like a baked ladder: a vertical axis states
the unit once on its top tick, a horizontal one on every tick."""

from __future__ import annotations

import re

from .._svg_render import render_board_to_svg


def _ticks(svg: str, role: str) -> list[str]:
    axis = re.search(rf'data-dct-role="{role}"[^>]*>(.*?)</g>\s*</g>', svg, flags=re.S)
    assert axis is not None, role
    texts = re.findall(r"<text[^>]*>([^<]*)</text>", axis.group(1))
    stripped = [t.strip("\u2004\u2005\u200a\u200b\u2006\u2007") for t in texts]
    return [t for t in stripped if re.search(r"\d", t)]


def _scatter(values: list[tuple[float, float]]) -> str:
    rows = "".join(f"      - [{x}, {y}]\n" for x, y in values)
    return render_board_to_svg(
        "queries:\n  q:\n    type: values\n    columns: [x, y]\n    values:\n"
        f"{rows}charts:\n  c:\n    query: q\n    type: scatter\n    x: x\n    y: y\n"
        "rows:\n  - c\n"
    )


def test_a_scatter_x_axis_repeats_one_shared_tier_on_every_tick() -> None:
    svg = _scatter(
        [(120000, 40), (500000, 55), (900000, 20), (1400000, 80), (1750000, 60)]
    )
    x_ticks = [t for t in _ticks(svg, "x-axis") if re.search(r"[1-9]", t)]
    assert len(x_ticks) > 3
    assert all(re.fullmatch(r"[\d.,]+m", t) for t in x_ticks), x_ticks


def test_a_scatter_x_axis_that_does_not_compact_prints_plain_digits() -> None:
    svg = _scatter([(900, 40), (1900, 55), (2600, 20), (3600, 80)])
    x_ticks = _ticks(svg, "x-axis")
    assert all(re.fullmatch(r"[\d,]+", t) for t in x_ticks), x_ticks
    assert "1,000" in x_ticks, x_ticks


def test_a_histogram_bin_axis_prints_plain_digits_at_one_tier() -> None:
    rows = "".join(f"      - [{v}]\n" for v in (300, 900, 1500, 2200, 2600, 3100, 3400))
    svg = render_board_to_svg(
        f"queries:\n  q:\n    type: values\n    columns: [v]\n    values:\n{rows}"
        "charts:\n  c:\n    query: q\n    type: histogram\n    x: v\n"
        "rows:\n  - c\n"
    )
    x_ticks = _ticks(svg, "x-axis")
    assert "1,000" in x_ticks, x_ticks
    assert all(re.fullmatch(r"[\d,]+", t) for t in x_ticks), x_ticks
