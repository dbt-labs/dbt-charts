"""Does the memo actually make a render faster? Measure it, don't assume it.

Marked ``slow`` so it stays out of ``just test`` and out of CI's default
lanes — a wall-clock ratio is the wrong thing to gate a merge on. Run it by hand
when you touch the memo, or from a nightly:

    uv run pytest dbt-charts/tests/core/render/test_svg_cache_benchmark.py -m slow -s

``-s`` shows the per-arm milliseconds; the assertion itself is a deliberately
loose floor, because the honest claim is "vl-convert is most of render compute
and this skips it", not any particular multiple on any particular machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render
from dbt_charts.core.render.svg_cache import RenderedSvgCache, svg_cache_scope

_CHARTS = 10

# Inline data on purpose: this measures render *compute*, and a warehouse would
# swamp it with query I/O. That also means the ratio below is not a
# dashboard-latency number for a warehouse-backed board — see
# ai_notes/engine/rendered-svg-memo-measured-2026-08-15.md.
_BOARD = (
    "title: Benchmark\n"
    "queries:\n"
    "  q1:\n"
    "    type: values\n"
    "    rows:\n"
    + "".join(f"      - {{month: M{i}, revenue: {i * 7}}}\n" for i in range(12))
    + "charts:\n"
    + "".join(
        f"  c{i}:\n    query: q1\n    type: bar\n    x: month\n    y: revenue\n"
        for i in range(_CHARTS)
    )
    + "rows:\n"
    + "".join(f"  - c{i}\n" for i in range(_CHARTS))
)


def _render_board() -> str:
    result = compile_board(_BOARD)
    assert result.success, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    return render(result.board, executor, format="svg").output


@pytest.mark.slow
def test_a_warm_memo_reaches_vl_convert_zero_times() -> None:
    """The mechanism behind the number above, asserted directly.

    This one is machine-independent: it counts calls, not milliseconds, so it is
    the part worth trusting if the timings ever look odd.
    """
    from unittest import mock

    import vl_convert as vlc

    memo = RenderedSvgCache()
    with svg_cache_scope(memo):
        _render_board()
        with mock.patch(
            "vl_convert.vegalite_to_svg", wraps=vlc.vegalite_to_svg
        ) as convert:
            _render_board()

    assert convert.call_count == 0
