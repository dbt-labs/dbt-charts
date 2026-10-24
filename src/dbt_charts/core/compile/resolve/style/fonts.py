"""Expand named fonts (``font: label`` / ``font: {extends: label, ...}``) into slots.

Runs once, after every layer has merged, so a board that redefines a
``style.fonts`` entry reaches every slot a theme bound to that name. Nothing
downstream of the cascade ever sees ``extends``.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from dbt_charts.core.colors import is_unknown_color_name
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.primitives import NamedFont, SingleLineFontStyle
from dbt_charts.core.compile.models.style.theme.annotations import AnnotationFontStyle
from dbt_charts.core.compile.models.style.theme.board import ChartTitleFontStyle
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_COLOR_TOKEN,
    ERR_FONT_REGISTRY,
    ERR_FONT_UNKNOWN,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.theme.style import Style

T = TypeVar("T", bound=BaseModel)

# `sizes` is a heading ramp, read from the registry; a slot never carries it.
_REGISTRY_ONLY = frozenset({"extends", "sizes"})
_RAMP_LEVELS = {"heading": "H1-H6", "section": "H2-H6"}


def resolve_registry(registry: Mapping[str, NamedFont]) -> dict[str, NamedFont]:
    """Return ``registry`` with every entry's ``extends`` chain folded in.

    An entry is its parent's resolved fields plus its own, own fields winning.
    ``sizes`` is a heading ramp and is never inherited.
    """
    resolved: dict[str, NamedFont] = {}

    def resolve(name: str, chain: tuple[str, ...]) -> NamedFont:
        if name in resolved:
            return resolved[name]
        entry = registry[name]
        if entry.extends is None:
            resolved[name] = entry
            return entry
        if entry.extends in chain:
            cycle = " -> ".join((*chain[chain.index(entry.extends) :], entry.extends))
            raise CompilationError.from_code(
                ERR_FONT_REGISTRY,
                message=f"Fonts extend in a cycle at style.fonts: {cycle}.",
            )
        _check_known(entry.extends, registry, f"style.fonts.{name}")
        parent = resolve(entry.extends, (*chain, entry.extends))
        fields = {
            **parent.model_dump(exclude_unset=True, exclude=set(_REGISTRY_ONLY)),
            **entry.model_dump(exclude_unset=True, exclude={"extends"}),
        }
        if name in _RAMP_LEVELS and fields.get("size") is not None:
            raise CompilationError.from_code(
                ERR_FONT_REGISTRY,
                message=(
                    f"The '{name}' font takes sizes ({_RAMP_LEVELS[name]}), not "
                    f"size, but "
                    f"inherits size {fields['size']} from '{entry.extends}'."
                ),
            )
        resolved[name] = NamedFont.model_validate(fields)
        return resolved[name]

    for name in registry:
        font = resolve(name, (name,))
        if font.color and is_unknown_color_name(font.color):
            raise CompilationError.from_code(
                ERR_COLOR_TOKEN,
                scope=f"style.fonts.{name}.color",
                reason=f"'{font.color}' is not a CSS color name or a palette token",
            )
    return resolved


def _check_known(name: str, registry: Mapping[str, NamedFont], path: str) -> None:
    if name not in registry:
        known = sorted(registry)
        raise CompilationError.from_code(
            ERR_FONT_UNKNOWN,
            message=f"Unknown font '{name}' at {path}. Known fonts: {', '.join(known)}.",
        )


def expand_named_fonts(
    node: T, registry: Mapping[str, NamedFont], path: str = "style"
) -> T:
    """Return ``node`` with every ``extends`` slot replaced by its registry font.

    ``path`` is the dotted authored location of ``node``, used in errors.
    """
    node_cls = type(node)
    if isinstance(
        node, (SingleLineFontStyle, ChartTitleFontStyle, AnnotationFontStyle)
    ):
        if node.extends is None:
            return node
        _check_known(node.extends, registry, path)
        entry = registry[node.extends]
        # A single-line slot has no leading, so a registry font's is dropped.
        slot_fields = node_cls.model_fields
        fields = {
            **{
                k: v
                for k, v in entry.model_dump(
                    exclude_unset=True, exclude=set(_REGISTRY_ONLY)
                ).items()
                if k in slot_fields
            },
            **node.model_dump(exclude_unset=True, exclude=set(_REGISTRY_ONLY)),
        }
        return node_cls.model_validate(fields)
    updates: dict[str, object] = {}  # type-state: object_annotation — field values
    for name, value in node:
        new = _expand_value(value, registry, f"{path}.{name}")
        if new != value:
            updates[name] = new
    return node.model_copy(update=updates) if updates else node


def _expand_value(
    value: object,  # type-state: object_annotation — any model field value
    registry: Mapping[str, NamedFont],
    path: str,
) -> object:  # type-state: object_annotation — the value with named fonts expanded
    if isinstance(value, BaseModel):
        return expand_named_fonts(value, registry, path)
    if isinstance(value, list):
        return [_expand_value(v, registry, f"{path}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, dict):
        return {k: _expand_value(v, registry, f"{path}.{k}") for k, v in value.items()}
    return value


@functools.cache
def _slot_graph() -> Mapping[str, str]:
    from dbt_charts.core.compile.models.style.theme.style import Style
    from dbt_charts.core.compile.resolve.style.inherit_graph import build_slot_graph

    return build_slot_graph(Style)


def _slot_at(
    style: BaseModel, path: str
) -> SingleLineFontStyle | ChartTitleFontStyle | AnnotationFontStyle | None:
    node: object = style  # type-state: object_annotation — a walk down model attributes
    for segment in path.split("."):
        node = getattr(
            node, segment, None
        )  # type-state: silent_fallback — a path off the model has no slot
        if node is None:
            return None
    return (
        node
        if isinstance(
            node, (SingleLineFontStyle, ChartTitleFontStyle, AnnotationFontStyle)
        )
        else None
    )


def inherited_font_name(
    style: Style, family: str | None, style_path: str
) -> tuple[str, bool]:
    """The named font a slot reads, and whether it is tweaked there.

    ``style`` is a merged style before any name is expanded (expansion discards
    the names). A chart's text reads, in order, the family's layer, the
    charts-level block, then the root style; a board only the last. In each, a
    slot with no ``extends`` of its own takes its parent slot's, up the inherit
    graph. The name is the first one found; the font is *tweaked* when a layer
    passed on the way sets fields of its own. ``("", False)`` when none names a
    font.
    """
    candidates = (
        (f"charts.{family}.{style_path}", f"charts.{style_path}", style_path)
        if family is not None
        else (style_path,)
    )
    graph = _slot_graph()
    tweaked = False
    for candidate in candidates:
        slot = _slot_at(style, candidate)
        if slot is None:
            continue
        tweaked = tweaked or any(
            value is not None
            for field, value in slot.model_dump().items()
            if field != "extends"
        )
        parent: str | None = f"Style.{candidate}"
        while parent is not None:
            ancestor = _slot_at(style, parent.removeprefix("Style."))
            if ancestor is not None and ancestor.extends is not None:
                return ancestor.extends, tweaked
            parent = graph.get(parent)
    return "", False
