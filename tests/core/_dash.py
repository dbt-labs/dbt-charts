"""A fitted `stroke-dasharray`: the authored pattern stretched to its stroke's length."""

from __future__ import annotations

import re


def is_stretched(value: str | None, authored: tuple[float, ...]) -> bool:
    """`value` is `authored` scaled by one factor near 1, as a stroke's fit leaves it."""
    if value is None:
        return False
    lengths = [float(n) for n in re.split(r"[ ,]+", value.strip())]
    if len(lengths) != len(authored):
        return False
    scale = lengths[0] / authored[0]
    same_shape = all(
        abs(n - a * scale) < 1e-6 * max(1.0, a)
        for n, a in zip(lengths, authored, strict=True)
    )
    return same_shape and 0.75 < scale < 1.34
