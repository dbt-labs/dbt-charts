"""Every theme states its muted and accent inks once, as the roles of those names."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import dbt_charts.core.defaults.themes as themes

THEMES = ("stark", "clarity", "paper", "vivid", "neon")


def _style(theme: str) -> dict[str, Any]:
    path = Path(next(iter(themes.__path__))) / f"{theme}.yaml"
    return yaml.safe_load(path.read_text())["style"]


def test_base_accent_names_its_role() -> None:
    assert _style("_base")["accent"] == "accent"


def test_base_secondary_font_names_the_muted_role() -> None:
    assert _style("_base")["fonts"]["secondary"]["color"] == "muted"


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("ink", ["muted", "accent"])
def test_a_theme_rebinds_the_role_not_the_root_key(theme: str, ink: str) -> None:
    assert ink not in _style(theme)


@pytest.mark.parametrize("theme", THEMES)
def test_every_theme_binds_its_muted_ink(theme: str) -> None:
    assert "muted" in _style(theme).get("roles", {})
