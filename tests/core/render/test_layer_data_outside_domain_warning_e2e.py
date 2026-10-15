"""End-to-end: WARN-LAYER-DATA-OUTSIDE-DOMAIN surfaces from render() for a
layer whose authored y domain cuts off some of its own data."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render

_CODE = "WARN-LAYER-DATA-OUTSIDE-DOMAIN"

_ROWS = [
    {"month": "2026-01", "revenue": 5.0, "temp": 250},
    {"month": "2026-02", "revenue": 6.0, "temp": 167},
    {"month": "2026-03", "revenue": 5.5, "temp": 280},
]

_YAML = """
title: Probe
charts:
  c:
    query: q
    type: line
    x: month
    y: revenue
    layers:
      - type: line
        y: temp
        axis_y:
          {position}
          scale:
            domain: {domain}
            clip: {clip}
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - c
"""


def _codes(domain: str, clip: str, position: str = "position: right") -> list:
    return _warnings(_YAML.format(domain=domain, clip=clip, position=position))


def _warnings(board_yaml: str) -> list:
    result = compile(board_yaml)
    assert result.success and result.board is not None, result.errors
    ok = Mock()
    ok.is_success = True
    ok.data = _ROWS
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    registry = Mock()
    registry.execute.return_value = ok
    executor = Executor(
        result.board, adapter_registry=registry, query_registry=result.query_registry
    )
    rendered = render(result.board, executor, format="svg")
    assert rendered.board_error is None and rendered.output
    return [w for w in rendered.warnings if w.code == _CODE]


def test_fires_when_domain_excludes_data() -> None:
    (w,) = _codes("[230, 290]", "true")
    assert w.chart == "c"
    assert w.path == "charts.c.layers.0.axis_y.scale.domain"


@pytest.mark.parametrize(
    ("domain", "clip"), [("[230, 290]", "false"), ("[100, 300]", "true")]
)
def test_silent_when_unclipped_or_in_range(domain: str, clip: str) -> None:
    assert _codes(domain, clip) == []


def test_silent_on_shared_scale_layer() -> None:
    """No `position` means the layer shares the base y scale; its domain is never honored."""
    assert (
        _codes("[230, 290]", "true", position="grid:\n            visible: true") == []
    )


def test_fires_on_unpinned_layer_when_a_sibling_pins_a_side() -> None:
    """One layer pinning a side makes every layer independent, so the unpinned
    layer's domain is honored (and clipped) too."""
    board_yaml = _YAML.replace(
        "    layers:\n",
        "    layers:\n      - type: line\n        y: revenue\n"
        "        axis_y:\n          position: right\n",
    ).format(domain="[230, 290]", clip="true", position="title: Temp")
    (w,) = _warnings(board_yaml)
    assert w.path == "charts.c.layers.1.axis_y.scale.domain"


@pytest.mark.parametrize("domain", ["[]", "[230]", "[1, 2, 3]"])
def test_domain_must_be_a_pair(domain: str) -> None:
    result = compile(
        _YAML.format(domain=domain, clip="true", position="position: right")
    )
    assert not result.success
    assert any("domain" in str(e) for e in result.errors)
