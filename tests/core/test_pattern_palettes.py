"""Pattern palettes: the `pattern` family, theme roles, and tonal pattern slots."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from dbt_charts.core.compile.models.palette import (
    Palette,
    build_palette_index,
)
from dbt_charts.core.compile.resolve.style.palette import (
    PatternAsPaletteError,
    palette,
    palette_family,
)

from .._paths import DBT_CHARTS_PKG_DIR

_DEFAULTS = DBT_CHARTS_PKG_DIR / "core" / "defaults"
_PALETTES_DIR = _DEFAULTS / "palettes"


def test_pattern_family_is_indexed() -> None:
    index = build_palette_index(_PALETTES_DIR)
    assert index["pattern-mixed-8"]["family"] == "pattern"
    assert index["pattern-hatch"]["family"] == "pattern"
    assert index["pattern-hatch-dots"]["family"] == "pattern"
    assert palette_family("pattern-hatch") == "pattern"


def test_pattern_palette_files_validate() -> None:
    for name, kind in (
        ("pattern-mixed-8", "categorical"),
        ("pattern-hatch", "sequential"),
        ("pattern-hatch-dots", "diverging"),
        ("pattern-regular-8", "categorical"),
        ("pattern-dots", "sequential"),
        ("pattern-mixed-12", "categorical"),
    ):
        data = yaml.safe_load((_PALETTES_DIR / "pattern" / f"{name}.yml").read_text())
        parsed = Palette.model_validate(data)
        assert parsed.kind == kind
    mixed = Palette.model_validate(
        yaml.safe_load((_PALETTES_DIR / "pattern" / "pattern-mixed-8.yml").read_text())
    )
    assert mixed.motifs == (
        "hatch-45",
        "bluenoise-dots",
        "dash-field",
        "bendday-hex",
        "worms-fine",
        "crosshatch",
        "pebbles-fine",
        "dither",
    )


@pytest.mark.parametrize(
    ("name", "steps"),
    [("pattern-hatch", 6), ("pattern-dots", 6), ("pattern-hatch-dots", 7)],
)
def test_ramp_palettes_carry_their_step_count(name: str, steps: int) -> None:
    data = yaml.safe_load((_PALETTES_DIR / "pattern" / f"{name}.yml").read_text())
    assert Palette.model_validate(data).steps == steps


def test_pattern_palette_has_no_color_stops() -> None:
    with pytest.raises(PatternAsPaletteError, match="pattern palette"):
        palette("pattern-hatch")


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "p", "kind": "categorical"},  # no motifs
        {"name": "p", "kind": "sequential", "motifs": ["dither"]},  # ramp required
        {"name": "p", "kind": "categorical", "motifs": ["nope"]},
        {"name": "p", "kind": "sequential", "ramp": "nope"},
        {
            "name": "p",
            "kind": "categorical",
            "motifs": ["dither"],
            "ramp": "hatch-dots",
        },
        {
            "name": "p",
            "kind": "categorical",
            "motifs": ["dither"],
            "colors": ["#000000"],
        },
        {"name": "p", "kind": "categorical", "motifs": ["dither"], "steps": 6},
        {"name": "p", "kind": "sequential", "ramp": "dots"},  # steps required
        {"name": "p", "kind": "sequential", "ramp": "dots", "steps": 2},
        {"name": "p", "kind": "sequential", "ramp": "dots", "steps": 10},
    ],
)
def test_pattern_palette_shape_is_validated(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Palette.model_validate(bad)


def test_theme_base_binds_pattern_roles() -> None:
    base = yaml.safe_load((_DEFAULTS / "themes" / "_base.yaml").read_text())
    roles = base["style"]["palettes"]
    assert roles["pattern_category"] == "pattern-mixed-8"
    assert roles["pattern_sequential"] == "pattern-dots"
    assert roles["pattern_diverging"] == "pattern-hatch-dots"
