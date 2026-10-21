from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict

from dbt_charts.core.compile.resolve.style.sharing import share_unchanged


class _Leaf(BaseModel):
    model_config = ConfigDict(frozen=True)

    color: str | None = None
    width: float = 1.0


class _Node(BaseModel):
    model_config = ConfigDict(frozen=True)

    left: _Leaf = _Leaf()
    right: _Leaf = _Leaf()
    tags: list[str] = []


@dataclasses.dataclass(frozen=True)
class _Products:
    node: _Node
    palette: tuple[str, ...]


def test_an_equal_tree_is_the_reference_tree() -> None:
    ref = _Node(left=_Leaf(color="#111111"), tags=["a"])
    new = _Node(left=_Leaf(color="#111111"), tags=["a"])

    assert share_unchanged(new, ref) is ref


def test_a_changed_leaf_rebuilds_only_its_path() -> None:
    ref = _Node(left=_Leaf(color="#111111"), right=_Leaf(color="#222222"))
    new = _Node(left=_Leaf(color="#999999"), right=_Leaf(color="#222222"))

    shared = share_unchanged(new, ref)

    assert shared == new
    assert shared.right is ref.right
    assert shared.left is new.left


def test_fields_set_is_part_of_equality() -> None:
    """``merge_onto_base`` reads ``model_fields_set`` as authored."""
    ref = _Leaf()
    new = _Leaf(width=1.0)

    shared = share_unchanged(new, ref)

    assert shared is new
    assert shared.model_fields_set == {"width"}


def test_a_rebuilt_node_keeps_its_own_fields_set() -> None:
    ref = _Node(left=_Leaf(color="#111111"))
    new = _Node(left=_Leaf(color="#999999"), right=_Leaf())

    shared = share_unchanged(new, ref)

    assert shared.model_fields_set == {"left", "right"}
    assert shared.right is ref.right


def test_scalars_of_another_type_are_not_equal() -> None:
    assert share_unchanged(1, True) == 1
    assert share_unchanged(1, True) is not True
    assert repr(share_unchanged(0.0, -0.0)) == "0.0"


def test_frozen_dataclasses_share_their_fields() -> None:
    node = _Node(left=_Leaf(color="#111111"))
    ref = _Products(node=node, palette=("#111111", "#222222"))
    new = _Products(
        node=_Node(left=_Leaf(color="#111111")), palette=("#111111", "#333333")
    )

    shared = share_unchanged(new, ref)

    assert shared == new
    assert shared.node is node
    assert shared.palette is new.palette


def test_sequences_of_another_length_are_not_shared() -> None:
    ref = _Node(tags=["a"])
    new = _Node(tags=["a", "b"])

    assert share_unchanged(new, ref).tags is new.tags


def test_dicts_share_equal_values_and_need_type_equal_keys() -> None:
    kept = _Leaf(color="#111111")
    ref = {"a": kept, "b": _Leaf(color="#222222")}
    new = {"a": _Leaf(color="#111111"), "b": _Leaf(color="#999999")}

    shared = share_unchanged(new, ref)

    assert shared == new
    assert shared["a"] is kept
    int_keyed = {1: "x"}
    assert share_unchanged(int_keyed, {True: "x"}) is int_keyed


def test_a_partly_changed_sequence_is_rebuilt_around_shared_items() -> None:
    kept = _Leaf(color="#111111")
    ref = (kept, _Leaf(color="#222222"))
    new = (_Leaf(color="#111111"), _Leaf(color="#999999"))

    shared = share_unchanged(new, ref)

    assert shared == new
    assert shared[0] is kept
    assert shared[1] is new[1]
