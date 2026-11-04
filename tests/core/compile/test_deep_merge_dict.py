"""TDD tests for deep_merge_dict — the shared dict-overlay merge engine.

Was two byte-near-identical private functions (config.py's ``_deep_merge``,
vega_lite/mapping.py's ``_vl_overlay_merge``) before consolidation.
"""

from __future__ import annotations

from dbt_charts.core.utils import deep_merge_dict


def test_deep_merge_dict_recurses_into_nested_mappings() -> None:
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    overlay = {"a": {"y": 99}}
    result = deep_merge_dict(base, overlay)
    assert result == {"a": {"x": 1, "y": 99}, "b": 1}


def test_deep_merge_dict_overlay_wins_on_scalar_conflict() -> None:
    result = deep_merge_dict({"a": 1}, {"a": 2})
    assert result == {"a": 2}


def test_deep_merge_dict_overlay_leaf_replaces_non_dict_base_value() -> None:
    result = deep_merge_dict({"a": {"x": 1}}, {"a": "scalar"})
    assert result == {"a": "scalar"}


def test_deep_merge_dict_deep_copies_overlay_leaves() -> None:
    shared_list = [1, 2, 3]
    overlay = {"a": shared_list}
    result = deep_merge_dict({}, overlay)
    result["a"].append(4)
    assert shared_list == [1, 2, 3]


def test_deep_merge_dict_does_not_mutate_base() -> None:
    base = {"a": {"x": 1}}
    deep_merge_dict(base, {"a": {"y": 2}})
    assert base == {"a": {"x": 1}}
