"""Compile must not repeat work it has already done.

Two things a compile does exactly once, however many times it is asked for
them: cascade a given board style, and parse a given board.

Both are invisible in the output — repeating them yields the same answer, only
slower — so these assert on call counts and on object identity rather than on
any style or position value. Two of them drive `compile()` itself, because a
memo nobody installs is indistinguishable from a working one at the unit level.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
import yaml

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.normalize import dispatch as normalizer
from dbt_charts.core.compile.normalize.dispatch import (
    board_style_cache,
    compile_board_resolved_style,
)
from dbt_charts.core.compile.parse.source_map import build_source_index

_BOARD_YAML = """\
title: Sales
queries:
  revenue:
    sql: |
      select 1 as amount
charts:
  bar:
    type: bar
    query: revenue
    x: amount
    y: amount
"""

# Root plus two nested boards carrying the same style block — the shape the memo
# exists for, and the shape a project of dashboards actually has.
_SIBLING_BOARDS_YAML = """\
title: Board
source: db
queries:
  revenue:
    sql: |
      select 1 as amount
rows:
  - title: Left
    style:
      muted: "#333333"
    charts:
      left:
        type: bar
        query: revenue
        x: amount
        y: amount
  - title: Right
    style:
      muted: "#333333"
    charts:
      right:
        type: bar
        query: revenue
        x: amount
        y: amount
"""


class TestSourceIndexParsesOnce:
    def test_build_source_index_composes_the_yaml_exactly_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        real_compose = yaml.compose

        def counting_compose(stream: Any, *a: Any, **kw: Any) -> Any:
            calls.append("compose")
            return real_compose(stream, *a, **kw)

        monkeypatch.setattr(yaml, "compose", counting_compose)
        build_source_index(_BOARD_YAML, "f.yaml")

        assert len(calls) == 1

    def test_index_carries_all_three_products(self) -> None:
        index = build_source_index(_BOARD_YAML, "f.yaml")

        assert "title" in index.source_map
        assert "queries.revenue.sql" in index.literal_blocks
        assert "charts.bar" in index.container_paths

    def test_unparseable_yaml_yields_an_empty_index(self) -> None:
        index = build_source_index("charts: [unterminated", "f.yaml")

        assert index.source_map == {}
        assert index.literal_blocks == {}
        assert index.container_paths == frozenset()


class TestBoardCascadeIsMemoizedPerCompile:
    def test_equal_inputs_cascade_once_within_one_compile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cascades: list[str] = []
        real = normalizer.resolve_style_and_context

        def counting(*a: Any, **kw: Any) -> Any:
            cascades.append("cascade")
            return real(*a, **kw)

        monkeypatch.setattr(normalizer, "resolve_style_and_context", counting)
        patch = StylePatch.model_validate({"muted": "#333333"})
        with board_style_cache():
            first = compile_board_resolved_style(patch, None, None)
            second = compile_board_resolved_style(patch, None, None)

        assert len(cascades) == 1
        assert first[0] == second[0]

    def test_each_board_scope_keeps_its_own_style_object(self) -> None:
        """The sizing pass tells board scopes apart by ``id(resolved_style)``.

        ``resolved_chart_variant_key`` keys per-chart resolve caches on that id,
        so handing two boards the same object would let one board's chart resolve
        stand in for the other's. Sharing the cascade must not share identity.
        """
        patch = StylePatch.model_validate({"muted": "#333333"})
        with board_style_cache():
            first = compile_board_resolved_style(patch, None, None)
            second = compile_board_resolved_style(patch, None, None)

        assert first[0] is not second[0]
        assert first[0] == second[0]

    def test_different_inputs_resolve_separately(self) -> None:
        with board_style_cache():
            first = compile_board_resolved_style(
                StylePatch.model_validate({"muted": "#333333"}), None, None
            )
            second = compile_board_resolved_style(
                StylePatch.model_validate({"muted": "#444444"}), None, None
            )

        assert first[0] is not second[0]
        assert first[0].muted != second[0].muted

    def test_a_parent_with_different_patches_is_not_shared(self) -> None:
        """An ancestor's own authored style patch is the parent's whole
        contribution to a nested board's cascade — and it must count in the
        cache key, same as the board's own patch does."""
        patch = StylePatch.model_validate({"background": "#ffffff"})
        with board_style_cache():
            root_resolved, root_context, _ = compile_board_resolved_style(
                patch, None, None
            )
            under_root = compile_board_resolved_style(
                patch, root_resolved, root_context
            )
            recolored_patch = StylePatch.model_validate({"muted": "#123456"})
            under_recolored = compile_board_resolved_style(
                patch, root_resolved, root_context, recolored_patch
            )

        assert under_root[0].muted != under_recolored[0].muted

    def test_a_non_finite_value_is_not_confused_with_an_unset_one(self) -> None:
        """A cache key must not lose information the merge still acts on.

        JSON has no inf or nan and writes both as null, so a key built from
        ``model_dump_json`` would serve the ``gap: null`` board whatever the
        ``gap: .inf`` board resolved to.
        """
        with board_style_cache():
            infinite = compile_board_resolved_style(
                StylePatch.model_validate({"gap": math.inf}), None, None
            )
            unset = compile_board_resolved_style(
                StylePatch.model_validate({"gap": None}), None, None
            )

        assert infinite[0].gap == math.inf
        assert unset[0].gap != math.inf

    def test_the_memo_does_not_outlive_the_compile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cascades: list[str] = []
        real = normalizer.resolve_style_and_context

        def counting(*a: Any, **kw: Any) -> Any:
            cascades.append("cascade")
            return real(*a, **kw)

        monkeypatch.setattr(normalizer, "resolve_style_and_context", counting)
        patch = StylePatch.model_validate({"muted": "#333333"})
        with board_style_cache():
            compile_board_resolved_style(patch, None, None)
        with board_style_cache():
            compile_board_resolved_style(patch, None, None)

        assert len(cascades) == 2

    def test_resolving_outside_a_compile_still_works(self) -> None:
        patch = StylePatch.model_validate({"muted": "#333333"})
        outside = compile_board_resolved_style(patch, None, None)
        with board_style_cache():
            inside = compile_board_resolved_style(patch, None, None)

        assert outside[0] == inside[0]

    def test_compiling_a_board_leaves_no_cache_installed(self) -> None:
        """The memo is compile-scoped: nothing may survive into the next caller."""
        from dbt_charts.core.compile import compile
        from dbt_charts.core.compile.normalize.dispatch import _BOARD_STYLE_CACHES

        compile(_BOARD_YAML)

        assert _BOARD_STYLE_CACHES.get() == ()


class TestCompileDoesNotRepeatItself:
    """Both memos, exercised through ``compile()`` rather than their own units.

    A memo nobody installs and a memo that never hits are indistinguishable from
    a working one at the unit level: the outputs are equal either way. These pin
    the wiring, so removing the ``board_style_cache()`` scope or re-adding a
    second ``build_source_index`` call fails a test instead of silently costing
    a compile twice the work.
    """

    def test_sibling_boards_with_one_style_cascade_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cascades: list[str] = []
        real = normalizer.resolve_style_and_context

        def counting(*a: Any, **kw: Any) -> Any:
            cascades.append("cascade")
            return real(*a, **kw)

        monkeypatch.setattr(normalizer, "resolve_style_and_context", counting)
        result = compile(_SIBLING_BOARDS_YAML, file="f.yaml")
        assert result.success, [d.message for d in result.errors]

        # Root (no style of its own) plus two siblings sharing one style:
        # one root cascade + one normalize-walk cascade (parent_context is
        # not yet threaded down at that pass) + one propagate-walk cascade
        # (the real parent, what render uses) = three. Without the memo
        # this is five -- each sibling resolved once per walk, with no
        # sharing between the two identically-styled siblings either.
        assert len(cascades) == 3

    def test_a_board_is_composed_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        composes: list[str] = []
        real_compose = yaml.compose

        def counting_compose(stream: Any, *a: Any, **kw: Any) -> Any:
            composes.append("compose")
            return real_compose(stream, *a, **kw)

        monkeypatch.setattr(yaml, "compose", counting_compose)
        result = compile(_SIBLING_BOARDS_YAML, file="f.yaml")
        assert result.success, [d.message for d in result.errors]

        assert len(composes) == 1
