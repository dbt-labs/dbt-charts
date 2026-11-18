"""Static guard over every model reachable from ``ResolvedBoard``.

The board artifact is ``TypeAdapter(ResolvedBoard)`` dump then validate, so a
field the dump drops or a union the load cannot disambiguate corrupts a replay
silently. This walks the type tree and fails on each such shape unless it is
allowlisted with the reason it is safe.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, PrivateAttr
from pydantic.fields import FieldInfo

from dbt_charts.core.compile.models.board.resolved import ResolvedBoard

_CONDITIONAL_FAMILIES = (
    "Area",
    "Bar",
    "Geoshape",
    "Heatmap",
    "Kpi",
    "Line",
    "Pie",
    "PointMap",
    "Scatter",
)
_SCALE_UNION = (
    "Members are told apart by extra=forbid and the named variant's required "
    "resolved_stops (primitives.py ResolvedScaleTargetConfig)"
)
_UNVERIFIED = "UNVERIFIED"
_DATE_LOSS = (
    "Known loss: a YAML date reloads as a str, so the loaded board is not equal"
)

# "Model.field" -> why a replayed board is unaffected, or UNVERIFIED (not yet
# proven safe).
ALLOWED: dict[str, str] = {
    **dict.fromkeys(
        [
            "ResolvedStyleChannel.scale",
            "ResolvedHeatmapStyle.color_gradient",
            "ResolvedGradientColorStyle.gradient",
            "ResolvedColumnScaleConfig.background",
            "ResolvedColumnScaleConfig.color",
        ],
        _SCALE_UNION,
    ),
    **dict.fromkeys(
        [f"Resolved{kind}Chart.layers" for kind in ("Area", "Bar", "Line", "Scatter")],
        "Each layer member carries its own Literal `type`, which tells them apart",
    ),
    "ResolvedSupportTable.entries": (
        "Each entry forbids extras and has its own key field "
        "(source, aggregate, per_series, percent)"
    ),
    "Diagnostic.fields": ("JSON-plain by contract: the dump runs with warnings=error"),
    "ValuesQuery._source_description": (
        "Rebuilt from rows by model_post_init on every validation, so the load "
        "recomputes it"
    ),
    "ResolvedLegendStyle.hidden_under_edge": (
        "Only the WARN-LEGEND-HIDDEN-UNDER-EDGE detector reads it, and detectors "
        "run only through _collect_render_warnings (renderer.py), which replay "
        "never calls"
    ),
    **dict.fromkeys(
        [
            "ResolvedBoard.variable_defaults",
            "ValuesQuery.rows",
            "ValuesQuery.values",
            "AuthoredValuesQuery.rows",
            "AuthoredCompactValuesQuery.values",
        ],
        _DATE_LOSS,
    ),
    **dict.fromkeys(
        [
            f"{family}ChartStyleConditional.{operand}"
            for family in _CONDITIONAL_FAMILIES
            for operand in ("eq", "in_", "ne")
        ]
        + [
            f"{model}.{operand}"
            for model in ("KpiGoodRule", "TableColumnConfigConditional")
            for operand in ("eq", "in_", "ne")
        ]
        + [f"TableRowStyleConditional.{operand}" for operand in ("eq", "in_", "ne")]
        + [
            "AuthoredCompactValuesQuery.source",
            "AuthoredSchemaQuery.source",
            "AuthoredSqlQuery.source",
            "AuthoredValuesQuery.source",
            "BaseScaleStylePatch.values",
            "DimensionLabelStylePatch.values",
            "ResolvedAxisElementStyle.values",
            "ResolvedScaleStyle.values",
            "XScaleStylePatch.values",
            "ResolvedGeoshapeChart.geo_projection_params",
            "ResolvedPointMapChart.basemap_geo_format",
            "ResolvedPointMapChart.geo_projection_params",
            "ResolvedStyle.vega_config",
            "ResolvedStyleChannel.literal_value",
            "Variable.default",
        ],
        _UNVERIFIED,
    ),
}


def _members(annotation: Any) -> list[Any]:
    origin = typing.get_origin(annotation)
    if origin is Annotated:
        return _members(typing.get_args(annotation)[0])
    if origin in (Union, types.UnionType):
        return [m for arg in typing.get_args(annotation) for m in _members(arg)]
    if origin is not None:
        return [m for arg in typing.get_args(annotation) for m in _members(arg)]
    return [annotation]


def _is_composite(annotation: Any) -> bool:
    return isinstance(annotation, type) and (
        issubclass(annotation, BaseModel) or dataclasses.is_dataclass(annotation)
    )


def _is_discriminator(meta: Any) -> bool:
    if isinstance(meta, FieldInfo):
        return meta.discriminator is not None
    return type(meta).__name__ == "Discriminator"


def _walk(annotation: Any, discriminated: bool) -> list[str]:
    """Reasons this annotation is lossy, descending through type arguments only."""
    if annotation is Any:
        return ["typing.Any reloads as raw JSON"]
    found: list[str] = []
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is Annotated:
        discriminated = discriminated or any(_is_discriminator(m) for m in args[1:])
        return _walk(args[0], discriminated)
    if origin in (Union, types.UnionType):
        arms = [a for a in args if any(_is_composite(m) for m in _members(a))]
        if len(arms) >= 2 and not discriminated:
            found.append("union of models with no discriminator")
    for arg in args:
        found += _walk(arg, discriminated)
    return found


def _fields(cls: type) -> dict[str, tuple[Any, FieldInfo | None]]:
    if issubclass(cls, BaseModel):
        return {n: (f.annotation, f) for n, f in cls.model_fields.items()}
    hints = typing.get_type_hints(cls, include_extras=True)
    return {
        f.name: (hints[f.name], f.default if isinstance(f.default, FieldInfo) else None)
        for f in dataclasses.fields(cls)
    }


def offenses(root: type) -> dict[str, str]:
    """Map "Model.field" to the reason for every offense reachable from root."""
    out: dict[str, str] = {}
    seen: set[type] = set()
    stack = [root]
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        if issubclass(cls, BaseModel):
            for name in cls.__private_attributes__:
                out[f"{cls.__name__}.{name}"] = "PrivateAttr is not dumped"
        for name, (annotation, info) in _fields(cls).items():
            key = f"{cls.__name__}.{name}"
            if info is not None and info.exclude:
                out[key] = "exclude=True is not dumped"
            reasons = _walk(
                annotation, info is not None and info.discriminator is not None
            )
            if reasons:
                out.setdefault(key, reasons[0])
            stack += [m for m in _members(annotation) if _is_composite(m)]
    return out


def test_walker_flags_each_offense_in_a_toy_model() -> None:
    class A(BaseModel):
        x: Literal["a"] = "a"

    class B(BaseModel):
        x: Literal["b"] = "b"

    @dataclasses.dataclass
    class Loose:
        raw: Any

    class Toy(BaseModel):
        hidden: int = Field(0, exclude=True)
        loose: Any = None
        either: A | B = A()
        tagged: Annotated[A | B, Field(discriminator="x")] = A()
        nested: list[Loose] = []
        _private: int = PrivateAttr(0)

    found = offenses(Toy)
    assert found == {
        "Toy.hidden": "exclude=True is not dumped",
        "Toy.loose": "typing.Any reloads as raw JSON",
        "Loose.raw": "typing.Any reloads as raw JSON",
        "Toy.either": "union of models with no discriminator",
        "Toy._private": "PrivateAttr is not dumped",
    }


def test_resolved_board_tree_has_no_unlisted_lossy_shapes() -> None:
    unlisted = {k: v for k, v in offenses(ResolvedBoard).items() if k not in ALLOWED}
    assert not unlisted, (
        "ResolvedBoard reaches shapes a dump/load round trip can lose; fix them or "
        "add each to ALLOWED with the reason it is safe:\n"
        + "\n".join(f"  {k}: {v}" for k, v in sorted(unlisted.items()))
    )


def test_allowlist_has_no_stale_entries() -> None:
    stale = sorted(set(ALLOWED) - set(offenses(ResolvedBoard)))
    assert not stale, f"ALLOWED entries no longer offend, delete them: {stale}"
