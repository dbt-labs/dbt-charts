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

from typing import TYPE_CHECKING, Annotated, Any, Literal, TypedDict, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dbt_charts.core.utils import YAML_LOADER

if TYPE_CHECKING:
    from importlib_resources.abc import Traversable

# Alias values: hex color string, another alias name, or 1-indexed slot ref (int).
PaletteAliasValue = str | int

# The renderer's pattern vocabulary (chart-renderer/data/pattern_motifs.json);
# a parity test pins them.
PatternMotif = Literal[
    "hatch-45",
    "hatch-135",
    "bluenoise-dots",
    "dash-field",
    "bendday-hex",
    "worms-fine",
    "crosshatch",
    "pebbles-fine",
    "dots-brick",
    "grid",
    "hatch-0",
    "hatch-90",
    "dither",
]
PatternRamp = Literal["hatch-crosshatch", "hatch-dots", "dots"]
PatternPaletteKind = Literal["categorical", "sequential", "diverging"]


class Palette(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    extends: str | None = None
    colors: list[str] | None = None
    aliases: dict[str, PaletteAliasValue] | None = None
    description: str | None = None
    # Pattern palettes: a categorical one lists motifs, a sequential or diverging
    # one names a ramp.
    kind: PatternPaletteKind | None = None
    motifs: tuple[PatternMotif, ...] | None = None
    ramp: PatternRamp | None = None
    steps: Annotated[int, Field(ge=3, le=9)] | None = None
    # Authoring-aid / metadata fields; declared explicitly so extra="forbid"
    # doesn't reject them.
    family: str | None = None
    design_notes: str | None = None
    r8_validation: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _pattern_shape(self) -> Palette:
        if self.kind is None:
            if (
                self.motifs is not None
                or self.ramp is not None
                or self.steps is not None
            ):
                raise ValueError(
                    f"palette '{self.name}' names motifs or a ramp without a pattern kind."
                )
        else:
            if self.colors is not None:
                raise ValueError(f"pattern palette '{self.name}' draws no colors.")
            if self.kind == "categorical":
                if not self.motifs or self.ramp is not None or self.steps is not None:
                    raise ValueError(
                        f"categorical pattern palette '{self.name}' needs `motifs:` "
                        "and no `ramp:` or `steps:`."
                    )
            elif self.ramp is None or self.steps is None or self.motifs is not None:
                raise ValueError(
                    f"{self.kind} pattern palette '{self.name}' needs `ramp:` and "
                    "`steps:` (3-9) and no `motifs:`."
                )
        return self

    @model_validator(mode="after")
    def _numeric_aliases_stay_outside_the_ladder(self) -> Palette:
        """A numbered address checks aliases before ``colors``, so a numeric alias
        inside ``1..len(colors)`` would silently redefine that stop."""
        stops = 0 if self.colors is None else len(self.colors)
        keys = [] if self.aliases is None else list(self.aliases)
        shadowed = [key for key in keys if key.isdigit() and 1 <= int(key) <= stops]
        if shadowed:
            raise ValueError(
                f"palette '{self.name}' declares numeric alias(es) {shadowed} "
                f"inside its own stops 1-{stops}; declare only stops outside them."
            )
        return self


PaletteFamily = Literal[
    "sequential", "diverging", "categorical", "scaffold", "tone", "pattern"
]
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
