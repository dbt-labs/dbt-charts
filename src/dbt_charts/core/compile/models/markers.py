"""Field markers for dbt charts model annotations.

Markers are attached to Pydantic fields via Annotated[T, Marker()] and
are picked up by introspection.py to enrich the schema IR.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

from dbt_charts.core.colors import AUTHORED_COLOR_PATTERN, check_authored_color
from dbt_charts.core.fonts import (
    AUTHORED_FONT_STACK_PATTERN,
    check_authored_font_stack,
)


@dataclasses.dataclass(frozen=True)
class Inherit:
    """Single parent link for a leaf field in the compiled style tree.

    Attached via ``Annotated[T, Inherit(from_path="...")]`` on a leaf field.
    ``build_inherit_graph()`` reads these to populate the ``InheritGraph``.

    Args:
        from_path: Absolute dot-path of the direct parent to inherit from when
                   this leaf has no authored value.  Must exist in the model's
                   path trie; validation occurs at graph-build time.
                   Example: ``"charts.font.color"``.
    """

    from_path: str


@dataclasses.dataclass(frozen=True)
class Absent:
    """``None`` is a real state of this field, not an unfilled leaf.

    Attached via ``Annotated[T | None, Absent("no link")]``. ``meaning`` says
    which state ``None`` is. Every other style field is concrete after the
    cascade, and an authored ``null`` is accepted only on a field carrying this.
    """

    meaning: str


@dataclasses.dataclass(frozen=True)
class NullHint:
    """What to write instead when an authored ``null`` is rejected on this field.

    Appended to the rejection message, for a field whose ``null`` reads as an
    intent ("no title") that a different key expresses.
    """

    instead: str


@dataclasses.dataclass(frozen=True)
class AutoUnset:
    """``auto`` on this field leaves the choice to the renderer.

    Attached via ``Annotated[T | None, AutoUnset()]``: the marker adds the
    ``auto`` arm to validation and to the published schema. A theme spells the
    default ``auto`` so the field is total; resolve reads it as ``None`` once
    the cascade has run, so the declared type is the resolved one.
    """

    def __get_pydantic_core_schema__(
        self,
        source: Any,  # type-state: explicit_any — pydantic's annotation hook receives any source type
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        return core_schema.no_info_wrap_validator_function(
            lambda value, validate: value if value == AUTO else validate(value),
            handler(source),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda value: value
            ),
        )


@dataclasses.dataclass(frozen=True)
class Overlay:
    """A slot of overrides authored over a parent slot, merged at chart resolve.

    Attached via ``Annotated[T, Overlay()]`` on a nested model field. Its leaves
    are ``None`` until an author sets one; the cascade never fills them, so
    ``None`` there means "no override", not an unfilled value.
    """


@dataclasses.dataclass(frozen=True)
class InheritSlot:
    """Marks that an entire nested slot mirrors another subtree.

    Attached via ``Annotated[T, InheritSlot(from_path="...")]`` on a nested
    model field.  ``build_inherit_graph()`` expands this to per-leaf
    ``Inherit`` chains: every leaf under the annotated field gets a fallback
    to the corresponding leaf under ``from_path``.

    Args:
        from_path: Absolute dot-path of the source subtree.  Must resolve to a
                   subtree with the same leaf structure as the annotated field's
                   type.  Validation occurs at graph-build time.
                   Example: ``"charts.axis"`` on an ``axis_x`` field.
        links: Leaf field name to the absolute path it inherits from in place
               of the slot's own source, so one leaf of the slot follows a
               different parent than its siblings. Matched by name at every
               depth of the annotated field's subtree. Each name must be a real
               field on the annotated field's type and each path must exist,
               both validated at graph-build time. Example: a KPI affix font
               inherits ``family``/``size`` from ``charts.kpi.font`` but its
               ``color`` from the headline value's.
    """

    from_path: str
    links: Mapping[str, str] = dataclasses.field(default_factory=dict, hash=False)


@dataclasses.dataclass(frozen=True)
class SchemaSugar:
    """Declares a synthetic authoring-sugar field with no backing Pydantic field.

    Unrelated to the inherit-graph markers (``Inherit``/``InheritSlot``) above —
    this one is read by ``schema/introspection.py``'s ``introspect()``, not
    ``build_inherit_graph()``. Attached to a model's own ``Annotated`` input
    wrapper (e.g. ``AuthoredBoardInput = Annotated[AuthoredBoard,
    BeforeValidator(...), SchemaSugar(...)]``), not to a field — the sugar key
    (``theme:``) is a sibling key a ``BeforeValidator`` folds into a real field
    (``extends:``) and deletes, so no field on the model itself carries it.
    ``introspect()`` reads this metadata off the wrapper and appends a
    synthetic ``SchemaField`` to the wrapped model's ``AuthorableModel`` entry,
    so the sugar key still reaches the generated JSON Schema, docs, and
    highlight manifest without a hand-maintained duplicate of its shape.

    Args:
        name: The authored YAML key (e.g. ``"theme"``).
        type_repr: The field's ``SchemaField.type_repr`` value (e.g. ``"str"``).
        enum_values: Legal string values, or None for an unconstrained type.
        description: Published field description (same rules as
                      ``Field(description=...)`` — see ``models/AGENTS.md``).
    """

    name: str
    type_repr: str
    enum_values: tuple[str, ...] | None
    description: str


class Strategy(StrEnum):
    """Valid merge strategies for the Merge marker."""

    OVERRIDE = "override"
    DEEP = "deep"
    BY_KEY = "by_key"


@dataclasses.dataclass(frozen=True)
class Merge:
    """Merge strategy marker for the board-resolution engine.

    Declares how a field is combined when two board fragments are merged.
    Attached via ``Annotated[T, Merge(Strategy.X)]``; a field without one
    infers its strategy from its type.

    - ``override`` — upper's explicitly-set non-None value replaces lower's.
                     When upper has not set the field, lower is preserved.
    - ``deep``     — recurse into the nested patch model.
    - ``by_key``   — dict union; upper wins per key.
    """

    file: Strategy


@dataclasses.dataclass(frozen=True)
class NotInherited:
    """The field does not cross into a nested board that authors its own style.

    A per-board structural or root-only concern, not thematic identity: the
    ancestor's value is dropped before the nested board's patch merges onto it.
    Attached via ``Annotated[T, NotInherited()]``.
    """


@dataclasses.dataclass(frozen=True)
class ResetOnSet:
    """Setting ``field`` on an upper layer discards the lower layer's ``resets``.

    Declared as a class-level ``merge_reset`` on a model whose ``field`` names
    a base that the layer replaces outright, so fields the upper layer leaves
    unset fall back to that base and not to the lower layer.
    """

    field: str
    resets: frozenset[str]


@dataclasses.dataclass(frozen=True)
class Exclusive:
    """Fields sharing a *group* are alternatives: setting one drops the others.

    When an upper layer sets any member of the group to a non-None value, every
    member of the lower layer is cleared first, so the upper layer's members
    replace the lower layer's wholesale.

    Attached via ``Annotated[T, Exclusive("layout")]``.
    """

    group: str


@dataclasses.dataclass(frozen=True)
class SkipInheritSlots:
    """Suppresses InheritSlot expansion for a field and its entire subtree.

    Attached via ``Annotated[T, SkipInheritSlots()]`` on any field.

    **On a nested model field**: ``build_inherit_graph()`` recurses into the
    field's type without activating any ``InheritSlot`` markers inside it, and
    without propagating the outer slot context into the subtree.  Use when a
    shared model contains ``InheritSlot`` annotations that are correct in one
    context but must not apply at this field path.  Example: ``TitleStyle``
    defines ``font: Annotated[FontStyle, InheritSlot(from_path="font")]`` for the
    root ``style.title`` path, but the per-chart title override field in
    ``_ChartStyleBase`` must not inherit from the root cascade.

    **On a leaf scalar field**: prevents the leaf from being added to the graph
    via slot expansion.  Use on axis style fields that the style resolver does
    not cascade — e.g. ``grid.dash``, ``ticks.count``, ``scale``, ``position`` —
    so the inherit graph only declares what the resolver actually does.

    ``cascade=True`` — for optional nested-model fields that the resolver
    cascades at container granularity (copies the whole object when the child is
    None, rather than filling individual leaf fields).  When set, the graph
    emits a *container-level* link ``child_path → parent_path`` instead of
    suppressing the field entirely.  Leaf writes through a ``None`` intermediate
    are not viable (``_set`` skips them), so container links are the only
    mechanism.  Only applicable on ``T | None`` nested-model fields inside an
    active slot context.
    """

    cascade: bool = False


@dataclasses.dataclass(frozen=True)
class Facet:
    """Base for semantic facets — what a field *means*, beyond what it holds.

    The schema records a field's type and its prose description. Neither
    distinguishes ``font.color`` ("Text color as a CSS color string",
    ``str | None``) from ``font.family`` ("Font family name", ``str | None``),
    so every consumer that needs the difference has had to keep its own list of
    key names, out of reach of the type system and free to drift.

    A facet travels with its field through renames and moves, and reaches the IR
    as ``SchemaField.facets``. Attached via ``Annotated[T, Color()]``.

    ``pattern`` is the regex the facet's validator enforces, stated again in the
    editor schema.
    """

    @property
    def pattern(self) -> str | None:
        return None


def _checked(inner: CoreSchema, check: Callable[[str], str]) -> CoreSchema:
    return core_schema.no_info_after_validator_function(
        lambda value: check(value) if isinstance(value, str) else value, inner
    )


@dataclasses.dataclass(frozen=True)
class Color(Facet):
    """The value is a CSS color, so an editor can offer a swatch.

    ``column_ref``: the value may instead name a query column (a table column's
    own colors), so it is neither shape- nor name-checked.
    """

    column_ref: bool = False
    # Placeholders this one field takes besides a color.
    keywords: tuple[str, ...] = ()

    @property
    def pattern(self) -> str | None:
        return None if self.column_ref else AUTHORED_COLOR_PATTERN

    def __get_pydantic_core_schema__(
        self,
        source: Any,  # type-state: explicit_any — pydantic's annotation hook receives any source type
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        if self.column_ref:
            return handler(source)
        return _checked(handler(source), check_authored_color)


@dataclasses.dataclass(frozen=True)
class Format(Facet):
    """The value is a number or time format: a predefined name, or a d3 spec.

    The engine's own names reach the schema as `FormatAlias`, but a board's
    `style.formats` map adds aliases of its own, and `formats` merges key-wise
    down the cascade — so the legal names on a chart depend on the board it
    sits in, which no wheel-shipped Literal can hold. The facet is what lets a
    surface holding a board widen the offer to the aliases that board defines,
    without matching field names or comparing values against the built-in set.
    A slot is judged by the column it paints, so the same field is a currency on
    one chart and a date on the next.
    """


# The width-following placeholder a chart title font takes on its leaves.
AUTO = "auto"


def _reject_auto(value: str, keywords: tuple[str, ...]) -> str:
    if value == AUTO and AUTO not in keywords:
        raise ValueError(f"{AUTO!r} is only valid on a chart title font")
    return value


@dataclasses.dataclass(frozen=True)
class FontWeightValue(Facet):
    """The value is a font weight; only a chart title font takes ``auto``."""

    keywords: tuple[str, ...] = ()

    @property
    def pattern(self) -> str | None:
        return None if AUTO in self.keywords else f"^(?!{AUTO}$)"

    def __get_pydantic_core_schema__(
        self,
        source: Any,  # type-state: explicit_any — pydantic's annotation hook receives any source type
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        return _checked(handler(source), lambda v: _reject_auto(v, self.keywords))


@dataclasses.dataclass(frozen=True)
class FontFamily(Facet):
    """The value names a font family or stack.

    The wheel knows exactly which faces it ships (``core.fonts``), so an
    editor can offer them — as shortcuts, never a closed set: any CSS family
    or stack stays a legal value. An entry that is not a valid CSS identifier
    is quoted; a character that could end the declaration fails validation.
    ``keywords`` are placeholders this one field takes instead.
    """

    keywords: tuple[str, ...] = ()

    @property
    def pattern(self) -> str | None:
        if AUTO in self.keywords:
            return AUTHORED_FONT_STACK_PATTERN
        return AUTHORED_FONT_STACK_PATTERN.replace("^", f"^(?!{AUTO}$)", 1)

    def __get_pydantic_core_schema__(
        self,
        source: Any,  # type-state: explicit_any — pydantic's annotation hook receives any source type
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        return _checked(
            handler(source),
            lambda v: (
                v
                if v in self.keywords
                else check_authored_font_stack(_reject_auto(v, self.keywords))
            ),
        )


@dataclasses.dataclass(frozen=True)
class Palette(Facet):
    """The value names a color palette, or spells one out as its stops.

    The wheel ships a closed set of palette names, and an editor can offer them
    as shortcuts rather than a closed set: a bare list of CSS colors stays a
    legal value here too.

    **Not** a theme's ``palettes:`` role (``category``, ``sequence``). A role is
    theme-scope vocabulary — ``compile/validate/palettes.py`` rejects a bare one
    on a board's or chart's own ``style:`` with ERR-PALETTE-UNKNOWN, and the
    parse gate defers role-shaped strings rather than catching them, so a menu
    that offers a role writes a board which stops compiling. An editor wanting
    to show what the theme uses resolves the role to its target name first.

    The names themselves reach the IR on ``enum_values`` either way. What the
    facet adds is that the field *means* a palette: without it a consumer sees
    ``<enum> | list[str] | str`` — the same shape as a chart's ``y`` — and the
    list arm wins, which is how the categorical palettes drew as text boxes
    full of hex while their vocabulary sat unused beside them.

    The named-palette vocabulary answers only "what can the whole field be
    written as" — never "what can one item of the list be". A palette *name*
    inside the list (``palette: ["editorial-10", "#4e79a7"]``) is not a color
    or a token dbt Charts can read as one: a list is already literal stops,
    not a name to expand, so the name is painted as authored rather than
    resolved — never what an author meant. A consumer building a
    multi-value edit from ``enum_values`` on a ``Palette``-faceted ``list``
    control must write one of them as the whole value, never wrap two of
    them into the list.
    """


@dataclasses.dataclass(frozen=True)
class ExplicitTag(Facet):
    """The field is a union tag the normalizer never infers — authors must
    write it. Reference renderers key on this to keep the tag's description,
    where an inferable tag's description would only restate its const cell."""


@dataclasses.dataclass(frozen=True)
class Channel(Facet):
    """The value names a column of the chart's query, not an appearance.

    `x` and `font.family` are both `str | None` with prose descriptions, and
    nothing in the schema distinguishes "the column this chart plots" from "how
    the chart looks". The difference is not cosmetic: a wrong channel is a
    render that fails on a column that does not exist, a wrong font size is a
    chart that looks slightly off. They want different validation and different
    input — a picker over the query's columns rather than a text box.
    """


@dataclasses.dataclass(frozen=True)
class Content(Facet):
    """The field is one of the ways a board declares content of its own.

    "Is this YAML file a board?" and "does this draft declare anything yet?"
    are one fact asked twice, and both sides answered it with a hand-written
    key list. They drifted: one counted `queries` and missed `text`, the other
    the reverse, so a prose-only board rendered while being invisible to
    `dct search`. The facet puts the answer on the fields; what to do with it
    stays each consumer's own business.
    """


@dataclasses.dataclass(frozen=True)
class Extends(Facet):
    """The field names the boards this one inherits from.

    Content a board renders without declaring: composition folds the base's
    layout into the child, so an `extends:`-only file is a full board carrying
    no `Content` key at all. Separate from `Content` because that is exactly
    where the consumers part ways — such a file *is* a board to list, and *is*
    a draft with nothing of its own to show starter cards for.
    """


@dataclasses.dataclass(frozen=True)
class DisplayText(Facet):
    """The value is prose the author wrote for a reader.

    A title, a label, a description — text whose words are the point, as
    opposed to a name that identifies something (`source`, `type`) or a body
    that carries its own syntax (`Markdown`). It is what a search index should
    hold and what an editor should offer a prose box for.
    """


@dataclasses.dataclass(frozen=True)
class Url(Facet):
    """The value is a link target — a URL or an author-space board path.

    Board navigation is authored as a root-relative path (`/spend/monthly`),
    so a link field is both what render rewrites for its host and what a
    project graph reads to find the edge between two boards.
    """


@dataclasses.dataclass(frozen=True)
class Markdown(Facet):
    """The value is a markdown body, not a plain string.

    Its `[label](/spend)` links are real board navigation once rendered, so a
    markdown field carries edges that no `Url` field names.
    """
