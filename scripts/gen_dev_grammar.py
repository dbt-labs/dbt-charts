"""Regenerate the DEV grammar the Rust migrate crate embeds.

Writes dbt-charts/src/dbt_charts/data/schemas/yaml/dev.json: the live board
grammar a release would freeze, descriptions stripped (a wording edit is not a
grammar change). The crate (`libs/chart-svg/migrate`) migrates every document
to it.

`--check` compares the file instead of writing it.

Run via: just gen-dev-grammar
"""

from __future__ import annotations

import sys
from pathlib import Path

from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    compute_current_schema,
    structural_schema_bytes,
)

DEV_GRAMMAR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "dbt_charts"
    / "data"
    / "schemas"
    / "yaml"
    / "dev.json"
)

expected = structural_schema_bytes(compute_current_schema())
if "--check" in sys.argv:
    if not DEV_GRAMMAR.is_file() or DEV_GRAMMAR.read_bytes() != expected:
        sys.exit(f"{DEV_GRAMMAR} is stale: run `just gen-dev-grammar`")
else:
    DEV_GRAMMAR.write_bytes(expected)
    print(f"Updated {DEV_GRAMMAR}")
