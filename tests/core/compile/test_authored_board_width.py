"""``frame.width`` binds exactly; ``frame.max_width`` bounds the content hug.

Two keys, two contracts. ``width`` authored anywhere in the cascade (board,
extends template, even meta.yml) is the board's exact width — the layout
distributes it. ``max_width`` (the theme's, or a project meta.yml's) only
bounds the content hug: with no ``width`` the board measures its charts'
preferred widths and hugs them, so a lone KPI does not sit in a 1200px card.
Before the split, one key played both roles by provenance and an authored
``width: 1800`` silently lost to a pair of theme defaults (1292).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.compile.sizing import board_container_width
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.board_resolve import build_resolved_board

_QUERY = """
queries:
  q:
    type: values
    rows:
      - {k: a, v: 1}
"""

_TWO_BARS = """
charts:
  c1: {query: q, type: bar, x: k, y: v}
  c2: {query: q, type: bar, x: k, y: v}
cols:
  - c1
  - c2
"""

_ONE_KPI = """
charts:
  c1: {query: q, type: kpi, value: v}
rows:
  - c1
"""


def _container(style: str, body: str) -> float:
    result = compile_board(f"title: T\n{style}{_QUERY}{body}")
    assert result.success, result.diagnostics
    return board_container_width(result.board, result.board.variable_defaults)


def test_authored_width_wider_than_content_is_honored() -> None:
    """The regression: two default bars measure 1292, but 1800 was authored."""
    assert _container("style:\n  frame:\n    width: 1800\n", _TWO_BARS) == 1800.0


def test_root_width_shorthand_is_honored() -> None:
    """Root-board ``width:`` normalizes to style.frame.width and binds the same."""
    assert _container("width: 1800\n", _TWO_BARS) == 1800.0


def test_authored_width_narrower_than_content_still_binds() -> None:
    """Authored width was already exact in this direction; keep it that way."""
    assert _container("style:\n  frame:\n    width: 800\n", _TWO_BARS) == 800.0


def test_unauthored_width_is_still_bounded_by_the_theme() -> None:
    """No width anywhere: wide content is bounded by the theme's max_width."""
    result = compile_board(f"title: T\n{_QUERY}{_TWO_BARS}")
    assert result.success, result.diagnostics
    assert board_container_width(result.board, result.board.variable_defaults) == float(
        result.board.resolved_style.frame.max_width
    )


def test_unauthored_narrow_content_still_hugs() -> None:
    """A lone KPI must not inflate to the full theme width."""
    assert _container("", _ONE_KPI) < 600.0


def test_authored_width_beats_narrow_content() -> None:
    """A lone KPI on an authored-width board fills it rather than hugging."""
    assert _container("style:\n  frame:\n    width: 1000\n", _ONE_KPI) == 1000.0


# ---------------------------------------------------------------------------
# Two keys, two contracts: width binds from anywhere in the cascade;
# max_width only bounds the hug. No provenance — the key is the intent.
# ---------------------------------------------------------------------------


def _container_under_meta(
    board_body: str, meta: str, in_memory_project: Callable, slug: str
) -> float:
    project = in_memory_project(
        Path(f"/tmp/test-width-{slug}"),
        {
            "charts/board.yml": f"title: T\n{_QUERY}{board_body}",
            "charts/meta.yml": meta,
        },
    )
    result = compile_file(project.path("charts/board.yml").read_board())
    assert result.success, result.errors
    return board_container_width(result.board, result.board.variable_defaults)


_META_MAX = "style:\n  frame:\n    max_width: 1100\n"


def test_meta_max_width_does_not_bind_narrow_content(
    in_memory_project: Callable,
) -> None:
    """A project max_width is a bound: a lone KPI under it still hugs."""
    assert _container_under_meta(_ONE_KPI, _META_MAX, in_memory_project, "hug") < 600.0


def test_meta_max_width_caps_wide_content(in_memory_project: Callable) -> None:
    """Two bars want 1292; the project's max_width 1100 bounds the hug."""
    assert (
        _container_under_meta(_TWO_BARS, _META_MAX, in_memory_project, "cap") == 1100.0
    )


def test_meta_width_binds_every_board(in_memory_project: Callable) -> None:
    """A project that authors width (not max_width) says exactly-this-wide."""
    meta = "style:\n  frame:\n    width: 1100\n"
    assert _container_under_meta(_ONE_KPI, meta, in_memory_project, "bind") == 1100.0


def test_board_own_width_binds_over_meta_max(in_memory_project: Callable) -> None:
    """The board file's own width wins the merge and binds exactly."""
    body = f"style:\n  frame:\n    width: 1500\n{_ONE_KPI}"
    assert _container_under_meta(body, _META_MAX, in_memory_project, "own") == 1500.0


# ---------------------------------------------------------------------------
# A chart's own ``width:`` keeps its documented fixed footprint when the
# board authors a width: the rows slot pins to the chart, it does not
# stretch to the full bound row.
# ---------------------------------------------------------------------------

_FIXED_CHART = """
charts:
  c1: {query: q, type: bar, x: k, y: v, width: 320}
rows:
  - c1
"""


def test_negative_frame_width_is_rejected() -> None:
    """``style.frame.width`` rejects non-positive values like root ``width:`` does.

    The two forms are documented as equivalent; before ``gt=0`` on the theme
    field, ``frame: {width: -200}`` compiled — and now that authored widths
    bind, it would have rendered.
    """
    result = compile_board(
        f"title: T\nstyle:\n  frame:\n    width: -200\n{_QUERY}{_ONE_KPI}"
    )
    assert not result.success
    assert any("width" in str(e.message) for e in result.errors), result.errors


def _chart_slot(board, chart_id: str, local_project: Callable) -> float:
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=None,
    )
    resolved, _cache = build_resolved_board(board, executor, {}, render_first=False)
    stack = [resolved.layout]
    while stack:
        for item in stack.pop().items:
            if item.chart is not None and item.chart.id == chart_id:
                return item.width
            if item.board is not None:
                stack.append(item.board.layout)
    raise AssertionError(f"chart {chart_id!r} not placed")


def test_chart_width_pins_slot_in_bound_board(local_project: Callable) -> None:
    """Board width 700, chart width 320: the slot is 320, not the full row."""
    result = compile_board(
        f"title: T\nstyle:\n  frame:\n    width: 700\n{_QUERY}{_FIXED_CHART}"
    )
    assert result.success, result.diagnostics
    assert board_container_width(result.board, result.board.variable_defaults) == 700.0
    assert _chart_slot(result.board, "c1", local_project) == 320.0


def _percent_rows_board(style: str) -> str:
    return (
        f"title: T\n{style}{_QUERY}"
        "charts:\n  c1: {query: q, type: bar, x: k, y: v}\n"
        "rows:\n  - width: 50%\n    rows:\n      - c1\n"
    )


def test_percent_rows_item_width_is_half_the_row(local_project: Callable) -> None:
    """A '50%' rows-item width means half the row — bound or not.

    One meaning everywhere: it never feeds the content hug (which would
    compound with the split into a quarter-width slot), and it is never a
    silent no-op on a width-bound board.
    """
    for style in ("", "style:\n  frame:\n    width: 1600\n"):
        result = compile_board(_percent_rows_board(style))
        assert result.success, result.diagnostics
        board = result.board
        content = board_container_width(board, board.variable_defaults) - 2 * float(
            board.resolved_style.frame.margin
        )
        assert _chart_slot(board, "c1", local_project) == content / 2, (
            style or "unbound"
        )


def test_percent_rows_item_width_never_feeds_the_hug() -> None:
    """The compounding half of the contract, pinned directly: an unbound
    board's container is identical with and without the '50%' wrapper. If
    the percentage fed the hug, the wrapper would shrink the container and
    the assignment split would compound it into a quarter-width slot."""
    with_pct = compile_board(_percent_rows_board(""))
    without = compile_board(
        _percent_rows_board("").replace(
            "  - width: 50%\n    rows:\n      - c1", "  - c1"
        )
    )
    assert with_pct.success and without.success
    assert board_container_width(
        with_pct.board, with_pct.board.variable_defaults
    ) == board_container_width(without.board, without.board.variable_defaults)


def _container_extending(
    fragment: str, in_memory_project: Callable, slug: str
) -> float:
    project = in_memory_project(
        Path(f"/tmp/test-extends-{slug}"),
        {
            "charts/_wide.yml": fragment,
            "charts/board.yml": (
                f"title: T\nextends: ./_wide.yml\n{_QUERY}{_TWO_BARS}"
            ),
        },
    )
    result = compile_file(project.path("charts/board.yml").read_board())
    assert result.success, result.errors
    return board_container_width(result.board, result.board.variable_defaults)


def test_extends_width_binds_like_the_boards_own(in_memory_project: Callable) -> None:
    """A width declared in an extends: template binds — the author named the file.

    This is the task's headline defect one file over: a template's 1800 must
    not silently lose to a pair of theme-default bars. Both authoring forms
    are documented as equivalent, so the root width: sugar binds too.
    """
    fragment = "style:\n  frame:\n    width: 1800\n"
    assert _container_extending(fragment, in_memory_project, "frame") == 1800.0
    assert _container_extending("width: 1800\n", in_memory_project, "sugar") == 1800.0


def test_theme_extends_width_still_caps(in_memory_project: Callable) -> None:
    """A built-in theme in extends: supplies only the cap, never a binding width."""
    project = in_memory_project(
        Path("/tmp/test-theme-extends"),
        {"charts/board.yml": f"title: T\nextends: paper\n{_QUERY}{_ONE_KPI}"},
    )
    result = compile_file(project.path("charts/board.yml").read_board())
    assert result.success, result.errors
    assert board_container_width(result.board, result.board.variable_defaults) < 600.0


def test_width_pair_on_the_root_is_a_validation_error_naming_both_keys() -> None:
    """Authoring both keys is refused with a validation code, not ERR-INTERNAL."""
    result = compile_board("width: 900\nstyle:\n  frame:\n    width: 800\nrows: []\n")
    assert not result.success
    (error,) = result.errors
    assert error.code == "ERR-VALIDATION-FIELD"
    assert "'width:'" in error.message and "'style.frame.width:'" in error.message
