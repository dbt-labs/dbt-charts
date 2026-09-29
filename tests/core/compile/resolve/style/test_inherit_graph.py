"""Tests for InheritGraph construction from annotated compiled models."""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import BaseModel, ConfigDict

from dbt_charts.core.compile.models.markers import (
    Inherit,
    InheritSlot,
    SkipInheritSlots,
)
from dbt_charts.core.compile.resolve.style.inherit_graph import (
    build_inherit_graph,
    build_slot_graph,
    flatten_inherit_chains,
)

# ── Fixture models ────────────────────────────────────────────────────────────


class FontFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    color: str
    size: float


class LabelFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    font: FontFixture
    padding: float


class AxisFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    label: LabelFixture
    visible: bool


class ChartsFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    axis: AxisFixture
    axis_x: Annotated[AxisFixture, InheritSlot(from_path="RootFixture.charts.axis")]


class RootFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    charts: ChartsFixture
    name: str


# ── Empty graph ───────────────────────────────────────────────────────────────


def test_empty_graph_for_unannotated_model():
    class Simple(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        x: str
        y: float

    assert build_inherit_graph(Simple) == {}


# ── Inherit leaf ──────────────────────────────────────────────────────────────


def test_inherit_leaf_explicit():
    """Inherit marker on a leaf field records the direct parent link."""

    class Source(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        color: str

    class Target(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        source: Source
        color: Annotated[str, Inherit(from_path="Target.source.color")]

    graph = build_inherit_graph(Target)
    assert graph["Target.color"] == "Target.source.color"


def test_inherit_leaf_single_link():
    """Inherit stores a single direct parent string."""

    class Src(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        a: str

    class Root(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        src: Src
        x: Annotated[str, Inherit(from_path="Root.src.a")]

    graph = build_inherit_graph(Root)
    assert graph["Root.x"] == "Root.src.a"


# ── InheritSlot expansion ─────────────────────────────────────────────────────


def test_inherit_slot_expands_all_leaves():
    """InheritSlot on a container field expands to per-leaf direct parent links."""
    graph = build_inherit_graph(RootFixture)

    assert (
        graph["RootFixture.charts.axis_x.label.font.color"]
        == "RootFixture.charts.axis.label.font.color"
    )
    assert (
        graph["RootFixture.charts.axis_x.label.font.size"]
        == "RootFixture.charts.axis.label.font.size"
    )
    assert (
        graph["RootFixture.charts.axis_x.label.padding"]
        == "RootFixture.charts.axis.label.padding"
    )
    assert (
        graph["RootFixture.charts.axis_x.visible"] == "RootFixture.charts.axis.visible"
    )


def test_unannotated_paths_absent_from_graph():
    """Paths without annotations must not appear in the graph."""
    graph = build_inherit_graph(RootFixture)

    assert "RootFixture.charts.axis.label.font.color" not in graph
    assert "RootFixture.charts.axis.visible" not in graph
    assert "RootFixture.name" not in graph


def test_inherit_slot_entry_count():
    """The graph has exactly the number of leaves under the annotated slot."""
    graph = build_inherit_graph(RootFixture)
    # AxisFixture has 4 leaves: visible, label.padding, label.font.color, label.font.size
    slot_entries = {
        k: v for k, v in graph.items() if k.startswith("RootFixture.charts.axis_x.")
    }
    assert len(slot_entries) == 4


def test_inherit_slot_exclude_omits_leaf_from_expansion():
    """InheritSlot(exclude={"color"}) omits only that leaf from the per-leaf
    expansion; sibling leaves under the same slot still link normally."""

    class ExcludeFontFixture(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        color: str
        size: float

    class ExcludeLabelFixture(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        font: Annotated[
            ExcludeFontFixture,
            InheritSlot(
                from_path="ExcludeRootFixture.charts.font", exclude=frozenset({"color"})
            ),
        ]

    class ExcludeChartsFixture(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        font: ExcludeFontFixture
        label: ExcludeLabelFixture

    class ExcludeRootFixture(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        charts: ExcludeChartsFixture

    graph = build_inherit_graph(ExcludeRootFixture)

    assert "ExcludeRootFixture.charts.label.font.color" not in graph
    assert (
        graph["ExcludeRootFixture.charts.label.font.size"]
        == "ExcludeRootFixture.charts.font.size"
    )


def test_inherit_slot_exclude_unknown_field_raises():
    """A typo'd exclude name (not a field on the annotated slot's type) must
    raise, not silently build a graph identical to no exclude at all — that
    would restore the backfill the exclude was meant to suppress."""

    class BadFontFixture(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        color: str

    class BadLabelFixture(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        font: Annotated[
            BadFontFixture,
            InheritSlot(
                from_path="BadRootFixture.charts.font",
                exclude=frozenset({"not_a_field"}),
            ),
        ]

    class BadChartsFixture(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        font: BadFontFixture
        label: BadLabelFixture

    class BadRootFixture(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        charts: BadChartsFixture

    with pytest.raises(ValueError, match="not_a_field"):
        build_inherit_graph(BadRootFixture)


# ── Path validation ───────────────────────────────────────────────────────────


def test_invalid_inherit_from_path_raises():
    """Inherit pointing at a nonexistent absolute path raises ValueError."""

    class Bad(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        x: Annotated[str, Inherit(from_path="Bad.nonexistent.path")]

    with pytest.raises(ValueError, match="nonexistent.path"):
        build_inherit_graph(Bad)


def test_invalid_inherit_slot_from_path_raises():
    """InheritSlot pointing at a nonexistent subtree raises ValueError."""

    class Inner(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        x: str

    class Bad(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        slot: Annotated[Inner, InheritSlot(from_path="Bad.no.such.path")]

    with pytest.raises(ValueError, match="no.such.path"):
        build_inherit_graph(Bad)


def test_nested_inherit_slot_inside_outer_slot_gets_container_link():
    """A field that itself carries an InheritSlot, reached while already
    inside an outer slot's expansion, must get its own container-level copy
    link at the outer slot's derived path — not just leaf links — so
    apply_inherit can fill it wholesale when the whole nested object is left
    unset. Mirrors ``TitleStyle.font`` (its own
    ``InheritSlot(from_path="Style.font")``) nested inside
    ``ChartsStyle.title``'s ``InheritSlot(from_path="Style.title")``.
    """

    class InnerFixture(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        color: str

    class OuterFixture(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        # Own absolute InheritSlot, same shape as TitleStyle.font's.
        inner: Annotated[InnerFixture, InheritSlot(from_path="NestedRoot.other")]

    class NestedRoot(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        other: InnerFixture
        ref: OuterFixture
        slot: Annotated[OuterFixture, InheritSlot(from_path="NestedRoot.ref")]

    graph = build_inherit_graph(NestedRoot)
    # Container-level link at the outer slot's own derived path: fills
    # slot.inner wholesale from ref.inner when unset, rather than jumping
    # straight to the absolute "other" source.
    assert graph.get("NestedRoot.slot.inner") == "NestedRoot.ref.inner"
    # Leaves still expand through the outer slot too.
    assert graph.get("NestedRoot.slot.inner.color") == "NestedRoot.ref.inner.color"


def test_skip_inherit_slots_suppresses_inner_slot():
    """SkipInheritSlots prevents InheritSlot markers inside a subtree from activating.

    Shared sub-model ``Container`` has ``InheritSlot(from_path="Root.ref")`` on its
    ``sub`` field.  When accessed at ``Root.a``, the slot fires and emits
    ``Root.a.sub.color ← Root.ref.color``.  When accessed at ``Root.b`` (annotated with
    ``SkipInheritSlots``), the inner slot is suppressed and no entries are emitted.
    """

    class Leaf(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        color: str

    class Container(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        # InheritSlot: sub.* mirrors ref.*
        sub: Annotated[Leaf, InheritSlot(from_path="Root.ref")]

    class Root(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        ref: Leaf
        # a: slot fires — Root.a.sub.color ← Root.ref.color
        a: Container
        # b: slot suppressed — no entries emitted for Root.b.*
        b: Annotated[Container, SkipInheritSlots()]

    graph = build_inherit_graph(Root)
    assert graph.get("Root.a.sub.color") == "Root.ref.color"
    assert not any(k.startswith("Root.b.") for k in graph)


# ── build_slot_graph ─────────────────────────────────────────────────────────


def test_build_slot_graph_records_container_link():
    """build_slot_graph records the slot container path, not per-leaf expansions."""
    slot_graph = build_slot_graph(RootFixture)

    # Slot fires at container level
    assert slot_graph.get("RootFixture.charts.axis_x") == "RootFixture.charts.axis"
    # No leaf-level expansion
    assert not any("axis_x." in k for k in slot_graph)


def test_build_slot_graph_excludes_unannotated_paths():
    """Unannotated containers do not appear in the slot graph."""
    slot_graph = build_slot_graph(RootFixture)

    assert "RootFixture.charts.axis" not in slot_graph
    assert "RootFixture.name" not in slot_graph


def test_build_slot_graph_skip_inherit_slots_suppressed():
    """SkipInheritSlots suppresses slot recording in build_slot_graph too."""

    class Leaf(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        color: str

    class Container(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        sub: Annotated[Leaf, InheritSlot(from_path="Root.ref")]

    class Root(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        ref: Leaf
        a: Container
        b: Annotated[Container, SkipInheritSlots()]

    slot_graph = build_slot_graph(Root)
    assert slot_graph.get("Root.a.sub") == "Root.ref"
    assert not any(k.startswith("Root.b.") for k in slot_graph)


def test_style_slot_graph_is_compact():
    """Style slot graph has one entry per slot annotation, not per leaf."""
    from dbt_charts.core.compile.models.style.theme import Style

    slot_graph = build_slot_graph(Style)

    # Container-level entries exist
    assert slot_graph.get("Style.charts.font") == "Style.font"
    assert slot_graph.get("Style.title.font") == "Style.font"
    assert slot_graph.get("Style.charts.legend.label.font") == "Style.charts.font"

    # No leaf-level entries
    assert "Style.charts.font.color" not in slot_graph
    assert "Style.title.font.size" not in slot_graph


# ── flatten_inherit_chains ────────────────────────────────────────────────────


def test_flatten_inherit_chains_two_hop():
    """A→B→C chain: flatten produces (B, C) for A and (C,) for B."""
    links = {"a.b": "a", "a": "root"}
    flat = flatten_inherit_chains(links)
    assert flat["a.b"] == ("a", "root")
    assert flat["a"] == ("root",)


def test_flatten_inherit_chains_single_hop():
    """A→B: flatten produces (B,) for A."""
    links = {"x": "y"}
    flat = flatten_inherit_chains(links)
    assert flat["x"] == ("y",)


def test_flatten_inherit_chains_empty():
    """Empty links produces empty result."""
    assert flatten_inherit_chains({}) == {}


def test_flatten_inherit_chains_root_node_not_in_links():
    """Nodes that are targets but not entries in links are not in the output."""
    links = {"a.b": "a"}
    flat = flatten_inherit_chains(links)
    assert "a" not in flat


# ── Production Style ──────────────────────────────────────────────────────────


def test_style_font_chains_in_graph():
    """Font inherit chains must appear in the Style model graph after annotations.

    Covers the chains declared in task annotate-font-title-and-layout-inherit-chains:
      Style.charts.font.* ← Style.font.*
      Style.title.font.* ← Style.font.*
      Style.title.subtitle.font.* ← Style.font.*
      Style.layout.tabs.font.* ← Style.font.*
      Style.layout.details.font.* ← Style.font.*
      Style.text.font.* ← Style.font.*
      Style.placeholder.overlay.font.* ← Style.font.*
      Style.charts.legend.label.font.* ← Style.charts.font.*
      Style.charts.legend.title.font.* ← Style.charts.font.*
    """
    from dbt_charts.core.compile.models.style.theme import Style

    graph = build_inherit_graph(Style)

    # charts.font inherits from root font
    assert graph.get("Style.charts.font.color") == "Style.font.color"
    assert graph.get("Style.charts.font.size") == "Style.font.size"
    assert graph.get("Style.charts.font.family") == "Style.font.family"

    # title.font inherits from root font
    assert graph.get("Style.title.font.color") == "Style.font.color"
    assert graph.get("Style.title.font.size") == "Style.font.size"

    # title.subtitle.font inherits from root font
    assert graph.get("Style.title.subtitle.font.color") == "Style.font.color"

    # text.font inherits from root font
    assert graph.get("Style.text.font.color") == "Style.font.color"

    # placeholder.overlay.font inherits from root font
    assert graph.get("Style.placeholder.overlay.font.color") == "Style.font.color"

    # layout.tabs.font inherits from root font
    assert graph.get("Style.layout.tabs.font.color") == "Style.font.color"

    # layout.details.font inherits from root font
    assert graph.get("Style.layout.details.font.color") == "Style.font.color"

    # charts.legend.label.font inherits from charts.font (single parent link)
    assert (
        graph.get("Style.charts.legend.label.font.color") == "Style.charts.font.color"
    )
    assert graph.get("Style.charts.legend.label.font.size") == "Style.charts.font.size"

    # charts.legend.title.font inherits from charts.font
    assert (
        graph.get("Style.charts.legend.title.font.color") == "Style.charts.font.color"
    )


def test_style_graph_excludes_root_font():
    """Root Style.font.* paths must not appear as graph keys (they are the inherit sources)."""
    from dbt_charts.core.compile.models.style.theme import Style

    graph = build_inherit_graph(Style)

    # Root font is the source, not a target — no inherit entries for Style.font.*
    assert not any(k.startswith("Style.font.") for k in graph)


def test_charts_title_inherits_from_board_title():
    """Style.charts.title.* carries InheritSlot(from_path="Style.title") --
    every leaf falls back to the board title when unset, whole unset
    sub-objects (container-level links) included."""
    from dbt_charts.core.compile.models.style.theme import Style

    graph = build_inherit_graph(Style)
    assert graph.get("Style.charts.title") == "Style.title"
    assert graph.get("Style.charts.title.font") == "Style.title.font"
    assert graph.get("Style.charts.title.font.family") == "Style.title.font.family"
    assert graph.get("Style.charts.title.sizes") == "Style.title.sizes"
