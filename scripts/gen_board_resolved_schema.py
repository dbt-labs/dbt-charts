"""Regenerate the committed board-resolved.schema.json artifact.

Writes: src/dbt_charts/data/schemas/board-resolved/board-resolved.schema.json

Canonicalizes list ordering (allOf/anyOf/enum/oneOf/required) for stable JSON
output across Python versions (dict/set iteration order can otherwise vary the
emitted JSON without changing its meaning).

Run via: just gen-schema-artifacts
     or: just gen-board-resolved-schema
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# In the monorepo this script lives at dbt-charts/scripts/; in the standalone
# export it lands at scripts/ (Copybara core.move("dbt-charts", "")). Either way
# parents[1] is the dbt-charts package root / standalone repo root.
_DBT_CHARTS_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    _DBT_CHARTS_DIR
    / "src"
    / "dbt_charts"
    / "data"
    / "schemas"
    / "board-resolved"
    / "board-resolved.schema.json"
)

# Keys whose list values are sorted for canonical, stable JSON output.
_CANONICAL_SCHEMA_LIST_KEYS = {
    "allOf",
    "anyOf",
    "enum",
    "fileMatch",
    "oneOf",
    "required",
}


def _canonicalize_schema(value: Any, parent_key: str | None = None) -> Any:
    """Normalize schema list ordering so generated JSON is stable across Python versions."""
    if isinstance(value, dict):
        return {
            key: _canonicalize_schema(item, parent_key=key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        normalized = [
            _canonicalize_schema(item, parent_key=parent_key) for item in value
        ]
        if parent_key in _CANONICAL_SCHEMA_LIST_KEYS:
            return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
        return normalized
    return value


def main() -> None:
    from dbt_charts.core.compile.schema.renderers.board_resolved import (
        render_board_resolved_schema,
    )

    schema = _canonicalize_schema(render_board_resolved_schema())

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"✓ {SCHEMA_PATH.relative_to(_DBT_CHARTS_DIR)}")


if __name__ == "__main__":
    sys.exit(main())
