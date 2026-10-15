"""The resolved board defaults carry no engine-decided literal."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style

_AUTO_FORBIDDEN_UNDER = {"endpoint_labels", "support_table", "legend"}


def _leaves(
    node: Any, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], Any]]:
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        items = [(f.name, getattr(node, f.name)) for f in dataclasses.fields(node)]
    elif hasattr(type(node), "model_fields"):
        items = [(name, getattr(node, name)) for name in type(node).model_fields]
    elif isinstance(node, dict):
        items = list(node.items())
    elif isinstance(node, (list, tuple)):
        items = [(str(i), v) for i, v in enumerate(node)]
    else:
        yield path, node
        return
    for name, value in items:
        yield from _leaves(value, (*path, str(name)))


@pytest.mark.parametrize("theme", ["clarity", "paper", "stark", "neon", "vivid"])
def test_chart_defaults_carry_no_engine_decided_literal(theme: str) -> None:
    defaults = resolve_style(get_theme_style(theme)).chart_defaults
    leaked = [
        ".".join(path)
        for path, value in _leaves(defaults)
        if value == "auto"
        and (
            _AUTO_FORBIDDEN_UNDER & set(path)
            or path[-3:-1] == ("title", "font")
            or path[-2:] == ("title", "visible")
        )
    ]
    assert not leaked, leaked
