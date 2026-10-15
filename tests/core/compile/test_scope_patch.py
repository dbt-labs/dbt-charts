"""TDD tests for scope_patch — the nested-scope patch accumulator.

Shared by the compile cascade (dispatch.compile_board_resolved_style) and the
design-verb read-only walk (agent_api.design._scope_style_patch): both derive
a scope's in-force patch from its parent's the same way, via this one helper.
"""

from __future__ import annotations

from dbt_charts.core.compile.merge import scope_patch
from dbt_charts.core.compile.models.style.authored import StylePatch


def test_own_none_returns_inherited_unchanged() -> None:
    inherited = StylePatch.model_validate({"muted": "#111111"})
    assert scope_patch(inherited, None) is inherited


def test_inherited_none_returns_own_unchanged() -> None:
    own = StylePatch.model_validate({"muted": "#222222"})
    assert scope_patch(None, own) is own


def test_both_none_returns_none() -> None:
    assert scope_patch(None, None) is None


def test_both_set_strips_not_inherited_fields_then_merges() -> None:
    inherited = StylePatch.model_validate(
        {"muted": "#111111", "gap": 60, "frame": {"card_padding": 90}}
    )
    own = StylePatch.model_validate({"accent": "#222222"})

    merged = scope_patch(inherited, own)

    assert merged.model_dump(exclude_unset=True) == {
        "muted": "#111111",
        "accent": "#222222",
    }
