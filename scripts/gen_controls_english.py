#!/usr/bin/env python3
"""Regenerate ``templates/scripts/controls_english.js`` from the English catalog.

just gen-controls-english
"""

from __future__ import annotations

import sys
from pathlib import Path

_DBT_CHARTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_DBT_CHARTS_DIR / "src"))

from dbt_charts.core.render.controls import controls_english_source  # noqa: E402

OUT = (
    _DBT_CHARTS_DIR / "src/dbt_charts/core/render/templates/scripts/controls_english.js"
)

if __name__ == "__main__":
    OUT.write_text(controls_english_source(), encoding="utf-8")
