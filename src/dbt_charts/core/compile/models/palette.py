"""Target Palette record model — unified palette shape, plus the shipped catalog scan.

The goal shape for all authored palette YAML files:
    {name, extends?, colors?, aliases?, description?}

Current status:
- tone/ family: already in this shape — round-trips correctly today.
- categorical/scaffold/sequential/diverging: still use legacy shapes
  (stops:, spine:, flat-key hex mapping). Migration pending.

No family/type discriminator. No subclasses.

The catalog scan and anti-pattern names live here, not in the resolver,
because `schema_names.py` derives its closed palette lists from them while
the models it feeds are still importing; the resolver imports the models.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, TypedDict, get_args

import yaml
from pydantic import BaseModel, ConfigDict

from dbt_charts.core.utils import YAML_LOADER

if TYPE_CHECKING:
    from importlib_resources.abc import Traversable

# Alias values: hex color string, another alias name, or 1-indexed slot ref (int).
PaletteAliasValue = str | int


class Palette(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    extends: str | None = None
    colors: list[str] | None = None
    aliases: dict[str, PaletteAliasValue] | None = None
    description: str | None = None
    # Authoring-aid / metadata fields; declared explicitly so extra="forbid"
    # doesn't reject them.
    family: str | None = None
    design_notes: str | None = None
    r8_validation: dict[str, Any] | None = None


PaletteFamily = Literal["sequential", "diverging", "categorical", "scaffold", "tone"]
PALETTE_FAMILIES: tuple[PaletteFamily, ...] = get_args(PaletteFamily)

# Known perceptual anti-patterns, refused outright.
HARD_FAIL_PALETTE_NAMES: frozenset[str] = frozenset({"jet", "rainbow", "hsv"})

# RdYlGn/parula resolve (for migration paths) but emit a warning.
# The mapped substitute is the nearest dbt charts palette so dashboards don't crash
# when users encounter these names from prior tools. Warning text names
# the substitution explicitly.
PALETTE_WARN_ALIASES: dict[str, str] = {
    "RdYlGn": "dbt-div-crimson-green",
    "parula": "dbt-seq-blue",
}


class PaletteIndexEntry(TypedDict):
    family: PaletteFamily
    path: Traversable


def build_palette_index(palettes_dir: Traversable) -> dict[str, PaletteIndexEntry]:
    """Scan ``palettes_dir/<family>/*.yml`` into a ``name → (family, path)`` map."""
    idx: dict[str, PaletteIndexEntry] = {}
    for family in PALETTE_FAMILIES:
        fam_dir = palettes_dir / family
        if not fam_dir.is_dir():
            continue
        for yml in sorted(
            (p for p in fam_dir.iterdir() if p.name.endswith(".yml")),
            key=lambda p: p.name,
        ):
            data = yaml.load(yml.read_text(encoding="utf-8"), Loader=YAML_LOADER)
            if not isinstance(data, dict) or "name" not in data:
                continue
            idx[data["name"]] = {"family": family, "path": yml}
    return idx
