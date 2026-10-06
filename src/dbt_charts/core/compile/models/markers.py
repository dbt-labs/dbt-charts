"""Field markers for dbt charts model annotations.

Markers are attached to Pydantic fields via Annotated[T, Marker()] and
are picked up by introspection.py to enrich the schema IR.
"""

from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Literal


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
        exclude: Bare field names to omit from the per-leaf expansion, even
                 though the rest of the nested field still inherits. Matched
                 by name at every depth of the annotated field's subtree (not
                 just its direct fields), so a same-named leaf nested deeper
                 is excluded too. Each name must be a real field on the
                 annotated field's type — validated at graph-build time like
                 ``from_path``. The excluded leaf stays a genuine
                 cascade-managed sentinel (``None`` unless some tier's raw
                 YAML sets it) instead of being unconditionally backfilled
                 from ``from_path``. Example: ``SliceLabelsStyle.font``
                 inherits ``family``/``size``/``weight`` from ``charts.font``
                 but excludes ``color`` so an author's explicit override (or
                 its absence) survives the cascade. Only takes effect where
                 this ``InheritSlot`` establishes a *new* slot context (its
                 ``from_path`` is used); at a derived slot position that
                 preserves an outer slot (e.g. ``axis_x`` reusing ``axis``'s
                 inner ``InheritSlot``), the outer slot's ``exclude`` applies
                 instead — an inner exclude there would break the tier chain
                 that lets a further-out tier's authored value flow through.
    """

    from_path: str
    exclude: frozenset[str] = frozenset()


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


class Strategy(str, Enum):
    """Valid merge strategies for the Merge marker."""

    OVERRIDE = "override"
    DEEP = "deep"
    BY_KEY = "by_key"
    APPEND = "append"
    CHILD = "child"


@dataclasses.dataclass(frozen=True)
class Merge:
    """Merge strategy marker for the board-resolution engine.

    Declares how a field is combined when two board fragments are merged:
    ``file`` for file-relation merges (meta.yml, extends) and ``nested``
    for nested-board-relation merges (child board inside rows/cols/grid/tabs).

    When ``nested`` is None the file strategy is used for both relations.

    Attached via ``Annotated[T, Merge(Strategy.X)]`` or
    ``Annotated[T, Merge(Strategy.X, nested=Strategy.Y)]``.

    - ``override`` — upper's explicitly-set non-None value replaces lower's.
                     When upper has not set the field, lower is preserved.
    - ``deep``     — recurse into the nested patch model.
    - ``by_key``   — dict union; upper wins per key.
    - ``append``   — list concatenation (lower first).
    - ``child``    — upper's value is unconditionally authoritative: lower is
                     discarded regardless of whether upper has set the field
                     (``getattr`` returns the model default, usually ``None``).
                     Contrast with ``override``, which preserves lower when
                     upper is unset.  Typically declared via
                     ``Merge(nested=Strategy.CHILD)`` so it fires only for
                     child-board merges (``nested=True``), not for file-relation
                     merges (extends / meta).
    """

    file: Strategy
    nested: Strategy | None = None


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
    """


@dataclasses.dataclass(frozen=True)
class Color(Facet):
    """The value is a CSS color, so an editor can offer a swatch."""


@dataclasses.dataclass(frozen=True)
class Format(Facet):
    """The value is a number or time format: a predefined name, or a d3 spec.

    The engine's own names reach the schema as `FormatAlias`, but a board's
    `style.formats` map adds aliases of its own, and `formats` merges key-wise
    down the cascade — so the legal names on a chart depend on the board it
    sits in, which no wheel-shipped Literal can hold. The facet is what lets a
    surface holding a board widen the offer to the aliases that board defines,
    without matching field names or comparing values against the built-in set.

    `kind` narrows which half of the engine's vocabulary belongs in the slot.
    Most slots are `"any"`: a `format:` on an axis or a table column is judged
    by the column it paints, and the same field is a currency on one chart and
    a date on the next. Two are not — `number_format` feeds a quantitative axis
    and `time_format` a temporal one — and there the wrong half is a d3 number
    spec baked onto a date, which renders garbage rather than failing. A board's
    own `style.formats` aliases are never kind-narrowed: the engine cannot know
    what spec a user's alias targets, so they stay legal in every slot.
    """

    kind: Literal["any", "number", "time"] = "any"


@dataclasses.dataclass(frozen=True)
class FontFamily(Facet):
    """The value names a font family or stack.

    The wheel knows exactly which faces it ships (``core.fonts``), so an
    editor can offer them — as shortcuts, never a closed set: any CSS family
    or stack stays a legal value.
    """


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
