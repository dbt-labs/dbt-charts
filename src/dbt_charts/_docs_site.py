"""Public docs site base URL for CLI hints and cross-links."""

from __future__ import annotations

import os

DEFAULT_DOCS_SITE_URL = "https://docs.dbtcharts.com"


def docs_site_url() -> str:
    """Return the docs site base URL without a trailing slash.

    ``DCT_DOCS_URL`` overrides the shipped default when set to a non-empty
    value. Used by structured error ``doc_url``s and the error hints that point
    at a docs page.
    """
    raw = os.environ.get("DCT_DOCS_URL", DEFAULT_DOCS_SITE_URL).strip()
    if not raw:
        return DEFAULT_DOCS_SITE_URL
    return raw.rstrip("/")


def yaml_reference_url() -> str:
    """Return the docs site's YAML field reference page, the pointer error hints use."""
    return f"{docs_site_url()}/reference/yaml-reference/"
