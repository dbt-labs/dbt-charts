#!/usr/bin/env python3
"""Regenerate ``core/text/data/cldr_first_day.json``: CLDR's first weekday for
the region of every ``TimeLocaleName``.

    just gen-cldr-first-day
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from typing import get_args

_DBT_CHARTS_DIR = Path(__file__).resolve().parents[1]
_SRC = _DBT_CHARTS_DIR / "src"
_OUT = _SRC / "dbt_charts" / "core" / "text" / "data" / "cldr_first_day.json"

sys.path.insert(0, str(_SRC))

CLDR_TAG = "48.2.3"
_PATH = "cldr-json/cldr-core/supplemental/weekData.json"
_URL = f"https://raw.githubusercontent.com/unicode-org/cldr-json/{CLDR_TAG}/{_PATH}"


def first_day_table(first_day: dict[str, str], regions: list[str]) -> dict[str, str]:
    """A missing region raises; it never falls back to the ``001`` default."""
    return {region: first_day[region] for region in sorted(regions)}


def main() -> None:
    from dbt_charts.core.text.format_d3 import TimeLocaleName

    with urllib.request.urlopen(_URL) as response:  # noqa: S310 -- pinned https URL
        week_data = json.load(response)
    regions = sorted({name[3:] for name in get_args(TimeLocaleName)})
    table = first_day_table(week_data["supplemental"]["weekData"]["firstDay"], regions)
    document = {
        "source": f"unicode-org/cldr-json {CLDR_TAG} {_PATH} supplemental.weekData.firstDay",
        "firstDay": table,
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(document, indent=2) + "\n")
    print(f"✓ {_OUT.relative_to(_DBT_CHARTS_DIR)}")


if __name__ == "__main__":
    main()
