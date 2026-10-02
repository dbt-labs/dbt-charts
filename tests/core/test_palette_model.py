"""Tests for the unified Palette Pydantic model.

TDD: these tests are written before the model exists.
"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from dbt_charts.core.compile.models.palette import Palette

from .._paths import DBT_CHARTS_PKG_DIR

_PALETTES_DIR = DBT_CHARTS_PKG_DIR / "core" / "defaults" / "palettes"


class TestPaletteModel:
    def test_aliases_only_tone_file_parses(self):
        """The real tone/negative.yml on disk parses correctly."""
        raw = yaml.safe_load((_PALETTES_DIR / "tone" / "negative.yml").read_text())
        p = Palette.model_validate(raw)
        assert p.name == "negative"
        assert p.colors is None
        assert p.aliases is not None
        assert "solid" in p.aliases
        assert isinstance(p.aliases["solid"], str)
        assert p.aliases["solid"].startswith("#") and len(p.aliases["solid"]) == 7

    def test_colors_and_aliases_scaffold_file_parses(self):
        """A colors+aliases file parses correctly.

        This uses synthetic data representing the target shape that Task C will
        migrate scaffold YAMLs into (colors: list + aliases: mapping).
        Current scaffold YAMLs use stops:/spine: — that migration is Task C.
        """
        data = {
            "name": "dbt-grays",
            "description": "Neutral grays.",
            "colors": ["#FAFAFA", "#F7F8FA", "#222222"],
            "aliases": {
                "gray-025": "#FAFAFA",
                "gray-90": "#222222",
            },
        }
        p = Palette.model_validate(data)
        assert p.name == "dbt-grays"
        assert p.colors is not None
        assert len(p.colors) == 3
        assert p.aliases is not None
        assert p.aliases["gray-90"] == "#222222"

    def test_authoring_aid_fields_accepted(self):
        """design_notes and r8_validation are declared fields, not extras."""
        data = {
            "name": "negative",
            "aliases": {"solid": "#94001e"},
            "design_notes": "H=22° warm red ...",
            "r8_validation": {"cvd_pass": True},
        }
        p = Palette.model_validate(data)
        assert p.name == "negative"
        assert p.design_notes == "H=22° warm red ..."

    def test_missing_name_fails_validation(self):
        """A palette record with no name field raises a validation error."""
        with pytest.raises(ValidationError):
            Palette.model_validate({"aliases": {"solid": "#94001e"}})

    def test_aliases_int_values_accepted(self):
        """Integer values in aliases are accepted (1-indexed slot refs for scaffold)."""
        data = {
            "name": "my-scaffold",
            "aliases": {
                "primary": 1,
                "secondary": 2,
                "accent": "#ff0000",
            },
        }
        p = Palette.model_validate(data)
        assert p.aliases is not None
        assert p.aliases["primary"] == 1
        assert p.aliases["accent"] == "#ff0000"

    def test_extends_field_accepted(self):
        """Optional extends field is accepted."""
        data = {"name": "child-palette", "extends": "parent-palette"}
        p = Palette.model_validate(data)
        assert p.extends == "parent-palette"

    def test_all_fields_optional_except_name(self):
        """Minimal palette with only name."""
        p = Palette.model_validate({"name": "minimal"})
        assert p.name == "minimal"
        assert p.extends is None
        assert p.colors is None
        assert p.aliases is None
        assert p.description is None

    def test_all_tone_files_round_trip(self):
        """Every tone YAML on disk parses through the model (tone family is already migrated)."""
        tone_dir = _PALETTES_DIR / "tone"
        for yml in sorted(tone_dir.glob("*.yml")):
            raw = yaml.safe_load(yml.read_text())
            p = Palette.model_validate(raw)
            assert p.name == raw["name"], f"{yml.name}: name mismatch"
            assert p.aliases is not None, f"{yml.name}: expected aliases"

    def test_scaffold_round_trips_after_task_c_migration(self):
        """Task C migrated scaffold YAMLs to the unified colors:/aliases: shape.

        The real dbt-grays.yml now parses correctly through the Palette model.
        """
        raw = yaml.safe_load((_PALETTES_DIR / "scaffold" / "dbt-grays.yml").read_text())
        p = Palette.model_validate(raw)
        assert p.name == "dbt-grays"
        assert p.colors is not None
        assert p.aliases is not None
        assert "ink" in p.aliases and isinstance(p.aliases["ink"], int)
        assert "canvas" in p.aliases and isinstance(p.aliases["canvas"], int)
