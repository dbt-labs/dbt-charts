"""Helpers for the per-fetcher outline coverage tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dbt_charts.agent_api.source_bundle import is_empty


def tile_block(outline: str, tile_id: str) -> str:
    """The outline lines of the tile whose heading line contains `` `tile_id` ``."""
    block: list[str] = []
    inside = False
    for line in outline.splitlines():
        if line.startswith("- "):
            inside = f"`{tile_id}`" in line
        if inside:
            block.append(line)
    assert block, f"no tile {tile_id} in the outline"
    return "\n".join(block)


def missing_options(
    config: dict[str, Any], block: str, is_dropped: Callable[[str], bool]
) -> list[str]:
    """Non-empty option keys of `config` with no `key:` line in `block`, not dropped."""
    return [
        key
        for key, value in config.items()
        if not is_empty(value) and f"{key}:" not in block and not is_dropped(key)
    ]
