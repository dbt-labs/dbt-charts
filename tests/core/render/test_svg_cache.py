"""Content-addressed memo for rendered chart SVGs.

The key is the content: same key ⇒ byte-identical SVG. These tests pin that
property from both sides — every input that changes the drawn output must
change the key, and a hit must return exactly what a cold render returns.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.board_links import LinkContext
from dbt_charts.core.render.svg_cache import (
    MemorySvgStore,
    MintedSvg,
    RenderedSvgCache,
    svg_cache_key,
    svg_cache_scope,
)


def _spec(value: int = 1) -> dict[str, Any]:
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"a": "A", "b": value}]},
        "mark": "bar",
        "encoding": {
            "x": {"field": "a", "type": "nominal"},
            "y": {"field": "b", "type": "quantitative"},
        },
    }


def _key(spec: dict[str, Any], **overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "output_format": "svg",
        "width": 400.0,
        "height": 300.0,
        "is_placeholder": False,
        "resolved_style": resolve_style(get_theme_style()),
        "link_context": None,
    }
    kwargs.update(overrides)
    return svg_cache_key(spec, **kwargs)


def test_key_is_stable_across_dict_ordering() -> None:
    """Key ordering is canonical — two equal specs built in different key order
    must hash the same, or every cache lookup is a coin flip."""
    a = {"mark": "bar", "data": {"values": [{"a": 1, "b": 2}]}}
    b = {"data": {"values": [{"b": 2, "a": 1}]}, "mark": "bar"}

    assert _key(a) == _key(b)


def test_changed_data_changes_key() -> None:
    """The whole point: the spec carries resolved data, so new warehouse rows
    produce a new key with no invalidation policy anywhere."""
    assert _key(_spec(1)) != _key(_spec(2))


def test_changed_dimensions_change_key() -> None:
    spec = _spec()
    assert _key(spec) != _key(spec, width=401.0)
    assert _key(spec) != _key(spec, height=301.0)


def test_changed_format_changes_key() -> None:
    spec = _spec()
    assert _key(spec) != _key(spec, output_format="png")


def test_changed_placeholder_flag_changes_key() -> None:
    spec = _spec()
    assert _key(spec) != _key(spec, is_placeholder=True)


def test_changed_style_changes_key() -> None:
    """resolved_style reaches the output through the placeholder overlay, so it
    is part of the content even though it is not part of the spec."""
    theme = get_theme_style()
    base = resolve_style(theme)
    other = resolve_style(
        theme.model_copy(
            update={"font": theme.font.model_copy(update={"family": "Some Other"})}
        )
    )

    assert _key(_spec(), resolved_style=base) != _key(_spec(), resolved_style=other)


def test_changed_link_context_changes_key() -> None:
    """The href rewrite runs after vl-convert and bakes the host's board-root
    prefix — including Cloud's branch segment — into the markup. Two branches
    previewing an unchanged board must not share an entry."""
    spec = _spec()
    main = LinkContext(root="/acme/sales/d", current_board_slug="orders")
    branch = LinkContext(root="/acme/sales/b/feat/d", current_board_slug="orders")

    assert _key(spec, link_context=main) != _key(spec, link_context=branch)
    assert _key(spec, link_context=None) != _key(spec, link_context=main)


def test_renderer_version_participates_in_key() -> None:
    """A vl-convert bump or an emit-path change must rotate the whole cache
    without a migration."""
    spec = _spec()
    before = _key(spec)
    with mock.patch("dbt_charts.core.render.svg_cache.RENDERER_VERSION", "test-bump"):
        after = _key(spec)

    assert before != after


def test_none_dimensions_are_distinct_from_zero() -> None:
    """``height=None`` means "let Vega auto-size" — a different render from a
    zero-height one, so it must not collide."""
    spec = _spec()
    assert _key(spec, height=None) != _key(spec, height=0.0)


def test_an_unserializable_spec_raises_the_same_error_vl_convert_would() -> None:
    """vl-convert refuses a Decimal-in-a-list too, so hashing is no stricter than
    rendering — but it must fail as the same per-chart error, or injecting a
    cache would turn one chart's error tile into a different one."""
    from decimal import Decimal

    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    spec = _spec()
    spec["data"]["values"][0]["b"] = [Decimal("1.5")]

    with pytest.raises(ChartDataError, match="not JSON-serializable"):
        _key(spec)


def test_an_entry_is_shared_across_boards() -> None:
    """The headline claim: entries are content, so a chart already drawn for one
    board is free for the next — no board, branch, or version coordinate gates it.

    Sibling charts on a *single* board are not this case: each takes its own
    palette slot, so two charts over the same query are genuinely different
    content and each renders once.
    """

    def board(title: str) -> str:
        return f"""
title: {title}
queries:
  q1:
    type: values
    rows:
      - {{month: Jan, revenue: 100}}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
rows:
  - c1
"""

    with svg_cache_scope(RenderedSvgCache()):
        _render(board("Board One"))
        with mock.patch(
            "vl_convert.vegalite_to_svg", wraps=_real_vegalite_to_svg()
        ) as convert:
            second = _render(board("Board Two"))

    assert "<svg" in second
    assert convert.call_count == 0


def test_memo_round_trips() -> None:
    cache = RenderedSvgCache()
    cache.put("k", "<svg/>", "c1")

    assert cache.get("k") == "<svg/>"
    assert cache.get("missing") is None


def test_memo_evicts_least_recently_used() -> None:
    cache = RenderedSvgCache(max_entries=2)
    cache.put("a", "1", "c1")
    cache.put("b", "2", "c2")
    cache.get("a")  # 'a' is now the most recently used, so 'b' evicts first
    cache.put("c", "3", "c3")

    assert cache.get("a") == "1"
    assert cache.get("b") is None
    assert cache.get("c") == "3"


def test_memo_skips_entries_over_the_byte_ceiling() -> None:
    """One pathological chart must not evict an entire warm cache."""
    cache = RenderedSvgCache(max_entries=8, max_entry_bytes=16)
    cache.put("big", "x" * 64, "c1")

    assert cache.get("big") is None


def test_the_byte_ceiling_counts_bytes_not_characters() -> None:
    """A ceiling shared with a durable store has to mean the same thing on both
    sides; non-ASCII labels are 3-4 bytes each."""
    cache = RenderedSvgCache(max_entry_bytes=16)
    cache.put("wide", "€" * 8, "c1")  # 8 chars, 24 bytes

    assert cache.get("wide") is None


def test_scope_is_not_global() -> None:
    """No ambient cache: a render outside a scope must never read one."""
    from dbt_charts.core.render.svg_cache import active_svg_cache

    cache = RenderedSvgCache()
    with svg_cache_scope(cache):
        assert active_svg_cache() is cache
    assert active_svg_cache() is None


def test_render_vega_spec_serves_a_hit_without_calling_vl_convert() -> None:
    """The whole payoff: an unchanged chart costs a dict lookup, not a render."""
    from dbt_charts.core.render.converters.chart import render_vega_spec

    style = resolve_style(get_theme_style())
    cache = RenderedSvgCache()
    with svg_cache_scope(cache):
        cold = render_vega_spec(_spec(), "svg", style, 400.0, 300.0, False, "c1")
        with mock.patch(
            "vl_convert.vegalite_to_svg",
            side_effect=AssertionError("re-rendered a cached spec"),
        ):
            warm = render_vega_spec(_spec(), "svg", style, 400.0, 300.0, False, "c1")

    assert warm == cold
    assert cold.startswith("<svg")


def test_render_vega_spec_re_renders_when_the_data_changed() -> None:
    """A hit must mean identical content — never merely the same chart."""
    from dbt_charts.core.render.converters.chart import render_vega_spec

    style = resolve_style(get_theme_style())
    with svg_cache_scope(RenderedSvgCache()):
        first = render_vega_spec(_spec(1), "svg", style, 400.0, 300.0, False, "c1")
        second = render_vega_spec(_spec(999), "svg", style, 400.0, 300.0, False, "c1")

    assert first != second


def test_render_vega_spec_is_uncached_outside_a_scope() -> None:
    """No ambient cache — an opt-out caller renders every time, as before."""
    from dbt_charts.core.render.converters.chart import render_vega_spec

    style = resolve_style(get_theme_style())
    render_vega_spec(_spec(), "svg", style, 400.0, 300.0, False, "c1")
    with mock.patch(
        "vl_convert.vegalite_to_svg", wraps=_real_vegalite_to_svg()
    ) as convert:
        render_vega_spec(_spec(), "svg", style, 400.0, 300.0, False, "c1")

    assert convert.called


def test_whole_board_render_reuses_every_chart_the_second_time() -> None:
    """End to end: re-rendering an unchanged board reaches vl-convert zero times.

    Covers the sizing pass too — it renders each chart to measure true height, so
    a cache that only served the main pass would still call through here.

    The render helper pins the clock, so cold and warm stamp the same time.
    """
    from .._svg_render import render_board_to_svg

    cache = RenderedSvgCache()
    with svg_cache_scope(cache):
        cold = render_board_to_svg()
        with mock.patch(
            "vl_convert.vegalite_to_svg", wraps=_real_vegalite_to_svg()
        ) as convert:
            warm = render_board_to_svg()

    assert "<svg" in cold
    assert warm == cold
    assert convert.call_count == 0


def test_a_different_variable_value_is_not_a_cache_hit() -> None:
    """Variables never appear in the key — they don't need to. They change the
    spec (through the data they filter and the titles they interpolate), and the
    spec is what is hashed."""
    cache = RenderedSvgCache()
    with svg_cache_scope(cache):
        east = _render_with_variables({"region": "East"})
        west = _render_with_variables({"region": "West"})

    assert "East" in east
    assert "West" in west


_VARIABLE_BOARD = """
title: Regional
variables:
  region:
    default: East
queries:
  q1:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
    title: "Revenue — {{ region }}"
rows:
  - c1
"""


def _render_with_variables(variables: dict[str, Any]) -> str:
    return _render(_VARIABLE_BOARD, variables=variables)


def _render(yaml_content: str, **options: Any) -> str:
    from pathlib import Path

    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.compile import compile as compile_board
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.execute.adapters import build_adapter_registry
    from dbt_charts.core.render import render

    result = compile_board(yaml_content)
    assert result.success
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    return render(result.board, executor, format="svg", **options).output


def _real_vegalite_to_svg() -> Any:
    import vl_convert as vlc

    return vlc.vegalite_to_svg


def test_scope_restores_the_previous_cache() -> None:
    from dbt_charts.core.render.svg_cache import active_svg_cache

    outer, inner = RenderedSvgCache(), RenderedSvgCache()
    with svg_cache_scope(outer):
        with svg_cache_scope(inner):
            assert active_svg_cache() is inner
        assert active_svg_cache() is outer


def test_the_memo_reports_what_a_host_must_write_back() -> None:
    """A host drains these two after a render: what to insert, and what to bump.

    Getting them wrong is how a durable store either loses entries or lets a
    chart that hits on every page load age out of its own LRU.
    """
    cache = RenderedSvgCache(loader={"stored": "<svg>old</svg>"}.get)
    cache.get("stored")
    cache.put("fresh", "<svg>new</svg>", "revenue")

    assert cache.touched == frozenset({"stored"})
    assert [(e.key, e.chart_id) for e in cache.minted] == [("fresh", "revenue")]


def test_an_entry_the_loader_was_never_asked_for_is_not_touched() -> None:
    cache = RenderedSvgCache(loader={"stored": "<svg/>"}.get)

    assert cache.touched == frozenset()
    assert cache.minted == ()


def test_the_loader_is_consulted_only_on_a_miss() -> None:
    """The whole point: one read per distinct hash, never a board's history."""
    asked: list[str] = []

    def loader(key: str) -> str | None:
        asked.append(key)
        return "<svg>stored</svg>" if key == "hit" else None

    cache = RenderedSvgCache(loader=loader)
    assert cache.get("hit") == "<svg>stored</svg>"
    assert cache.get("hit") == "<svg>stored</svg>"

    assert asked == ["hit"]


def test_a_miss_is_remembered_so_one_hash_costs_one_read() -> None:
    """Two identical charts on one board must not both pay the lookup."""
    asked: list[str] = []

    def loader(key: str) -> str | None:
        asked.append(key)
        return None

    cache = RenderedSvgCache(loader=loader)
    assert cache.get("absent") is None
    assert cache.get("absent") is None

    assert asked == ["absent"]


def test_a_minted_entry_is_never_asked_of_the_loader() -> None:
    asked: list[str] = []

    def loader(key: str) -> str | None:
        asked.append(key)
        return None

    cache = RenderedSvgCache(loader=loader)
    cache.put("fresh", "<svg/>", "c1")

    assert cache.get("fresh") == "<svg/>"
    assert asked == []


def test_a_loaded_entry_is_touched_and_never_minted() -> None:
    """Writing a loaded entry back as an insert would be a redundant upsert."""
    cache = RenderedSvgCache(loader={"stored": "<svg/>"}.get)
    cache.get("stored")

    assert cache.touched == frozenset({"stored"})
    assert cache.minted == ()


def test_a_minted_entry_is_never_reported_as_touched() -> None:
    """last_used_at is already stamped by the insert; bumping it is a wasted write."""
    cache = RenderedSvgCache(loader=lambda _key: None)
    cache.put("fresh", "<svg/>", "c1")
    cache.get("fresh")

    assert cache.touched == frozenset()


def test_loaded_entries_obey_the_entry_ceiling() -> None:
    """The bug: preloading seated entries straight into the dict, past the LRU.

    Entries arriving from the store have to go through the same trim as minted
    ones, or the ceiling bounds nothing a host actually fills the memo with.
    """
    asked: list[str] = []

    def loader(key: str) -> str | None:
        asked.append(key)
        return f"<svg>{key}</svg>"

    cache = RenderedSvgCache(loader=loader, max_entries=2)
    for key in ("a", "b", "c"):
        cache.get(key)
    cache.get("a")  # "c" pushed it out, so this is a second read, not a hit

    assert asked == ["a", "b", "c", "a"]


def test_no_loader_means_every_read_misses() -> None:
    """`dct render` passes no store; the memo is then a plain in-render dict."""
    cache = RenderedSvgCache()

    assert cache.get("anything") is None
    assert cache.touched == frozenset()


def test_an_evicted_mint_is_not_reported_for_writing_back() -> None:
    """The store must not be told to insert something the memo already dropped."""
    cache = RenderedSvgCache(max_entries=1)
    cache.put("first", "1", "c1")
    cache.put("second", "2", "c2")

    assert [e.key for e in cache.minted] == ["second"]


def _minted(key: str, size: int) -> MintedSvg:
    return MintedSvg(key=key, svg="x" * size, chart_id=key)


def test_memory_store_evicts_least_recently_used_past_byte_budget() -> None:
    store = MemorySvgStore(max_bytes=25)
    store.save([_minted("a", 10), _minted("b", 10)])
    assert store.load("a") is not None
    store.save([_minted("c", 10)])
    assert store.load("b") is None
    assert store.load("a") is not None
    assert store.load("c") is not None
