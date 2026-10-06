"""No theme may leave a style slot empty and unlinked when the slot's complete
type has a required field -- an empty, unlinked slot with a required field
turns a partial write into it into field-required errors naming fields the
author never set.

The walk test checks graph membership only: for every built-in theme, every
empty ``BaseModel | None`` field whose type has a required field must have an
inherit-graph container link, with the one switch slot below as the sole
declared exception.

The remaining tests pin ``Style.charts.title`` specifically: that the
InheritSlot cascade actually produces a
complete ``TitleStyle`` for every real theme with zero board patches.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator

import pytest
from pydantic import BaseModel

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
    user_facing_theme_names,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.compile.resolve.style.inherit_graph import (
    _unwrap_model,
    get_inherit_graph,
)

_BUILT_IN_THEMES = user_facing_theme_names()

# Exempt from the container-link rule: gradient presence switches the color
# channel to gradient mode, so an author writing it supplies a palette.
_SWITCH_SLOTS = ("Style.charts.color.gradient",)


def _empty_complete_slots(
    model: BaseModel, prefix: str
) -> Iterator[tuple[str, type[BaseModel]]]:
    """Walk a Style tree, yielding (graph_path, slot_type) for every ``None``
    field whose annotation is a single ``BaseModel | None``. Recurses into
    fields the theme did set."""
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        path = f"{prefix}.{name}"
        if isinstance(value, BaseModel):
            yield from _empty_complete_slots(value, path)
            continue
        if value is not None:
            continue
        slot_type = _unwrap_model(field.annotation)
        if slot_type is not None:
            yield path, slot_type


def _has_required_field(model_type: type[BaseModel]) -> bool:
    return any(f.is_required() for f in model_type.model_fields.values())


@pytest.mark.parametrize("theme_name", _BUILT_IN_THEMES)
def test_every_empty_complete_slot_has_a_container_link(theme_name: str) -> None:
    """An empty slot whose complete type has a required field must be
    reachable via the inherit graph."""
    base = get_theme_style(theme_name)
    graph = get_inherit_graph()
    for path, slot_type in _empty_complete_slots(base, "Style"):
        if path in _SWITCH_SLOTS or not _has_required_field(slot_type):
            continue
        assert path in graph, (
            f"{path} is empty in theme {theme_name!r}, its type "
            f"{slot_type.__name__} has a required field, and it has no "
            "inherit-graph container link"
        )


@pytest.fixture(autouse=True)
def _reset() -> Generator[None, None, None]:
    reset_config()
    yield
    reset_config()


def test_family_title_slots_stay_unlinked() -> None:
    """Per-family title slots stay None-sentinel patches."""
    graph = get_inherit_graph()
    for family in ("bar", "line", "area", "scatter", "pie", "kpi", "table"):
        assert f"Style.charts.{family}.title" not in graph


@pytest.mark.parametrize("theme_name", _BUILT_IN_THEMES)
def test_family_title_context_stays_none_when_unauthored(theme_name: str) -> None:
    """Per-family title slots stay None when unauthored."""
    base = get_theme_style(theme_name)
    ctx = resolve_chart_style_context(base)
    assert ctx.kpi.title is None


@pytest.mark.parametrize("theme_name", _BUILT_IN_THEMES)
def test_charts_title_slot_completes_from_style_title(theme_name: str) -> None:
    """With no board patches, charts.title equals the resolved style.title."""
    base = get_theme_style(theme_name)
    resolved, ctx = resolve_style_and_context(base)
    assert ctx.title == resolved.title
