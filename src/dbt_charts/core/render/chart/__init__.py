"""Chart rendering subpackage.

Holds the Vega-Lite emitters for the geo maps (``emitters/``), the feature
pipeline (``features/``), Vega-Lite assembly (``translate.py``), and the
orchestration that hands every other family to the Rust engine.
"""

from dbt_charts.core.render.utils import slug_to_text

__all__ = ["slug_to_text"]
