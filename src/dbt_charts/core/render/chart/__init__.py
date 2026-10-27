"""Chart rendering subpackage.

Holds the orchestration that hands every chart family to the Rust engine, and
the shared channel and series helpers the warning detectors read (``emitters/``).
"""

from dbt_charts.core.render.utils import slug_to_text

__all__ = ["slug_to_text"]
