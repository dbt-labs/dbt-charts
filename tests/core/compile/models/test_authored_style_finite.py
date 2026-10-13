"""No authored model accepts `.inf` or `.nan` in a float field.

The chart payload is JSON (`allow_nan=False`), so a non-finite style float would
otherwise surface as a `ValueError` mid-render or dump to `null` and read as unset.
"""

from __future__ import annotations

import importlib
import inspect
import math
import pkgutil
import types
import typing

import pytest
from pydantic import BaseModel

import dbt_charts.core.compile.models as models_pkg


def _is_float(annotation: object) -> bool:
    if annotation is float:
        return True
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _is_float(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        return any(_is_float(arg) for arg in typing.get_args(annotation))
    return False


def _authored_models() -> list[type[BaseModel]]:
    """Every model a board author writes: the `*Patch` overlays and the models
    defined in `authored` modules or `primitives`."""
    found: dict[type[BaseModel], None] = {}
    for info in pkgutil.walk_packages(models_pkg.__path__, f"{models_pkg.__name__}."):
        module = importlib.import_module(info.name)
        authored = ".authored" in info.name or info.name.endswith(".primitives")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if not issubclass(cls, BaseModel) or cls.__name__.startswith("Resolved"):
                continue
            if cls.__module__.startswith(models_pkg.__name__) and (
                cls.__name__.endswith("Patch")
                or (authored and cls.__module__ == info.name)
            ):
                found[cls] = None
    return list(found)


_FLOAT_FIELDS = [
    (cls, name)
    for cls in _authored_models()
    for name, field in cls.model_fields.items()
    if _is_float(field.annotation)
]


def test_the_walk_finds_the_authored_float_fields() -> None:
    assert len(_FLOAT_FIELDS) > 150


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
@pytest.mark.parametrize(
    ("cls", "name"),
    _FLOAT_FIELDS,
    ids=[f"{cls.__name__}.{name}" for cls, name in _FLOAT_FIELDS],
)
def test_an_authored_float_field_rejects_a_non_finite_value(
    cls: type[BaseModel], name: str, value: float
) -> None:
    with pytest.raises(ValueError, match="finite"):
        cls.__pydantic_validator__.validate_assignment(
            cls.model_construct(), name, value
        )
