"""Vega-Lite schema version constant and validation utilities."""

# Single source of truth for the Vega-Lite schema version dbt charts targets.
# Bumping this is a code change (model/contract updates may be required), so
# it lives as a Python constant rather than authorable YAML.
# Keep its patch version aligned with the Vega-Lite build bundled by vl-convert-python.
from __future__ import annotations

VEGA_LITE_SCHEMA_URL = "https://vega.github.io/schema/vega-lite/v6.4.1.json"
