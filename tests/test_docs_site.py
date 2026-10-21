"""Tests for docs site URL resolution."""

from __future__ import annotations

import pytest

from dbt_charts._docs_site import (
    DEFAULT_DOCS_SITE_URL,
    docs_site_url,
    yaml_reference_url,
)

INTERNAL_HOSTS = ("it-dataface.com", "it-fivetran.com")


def test_default_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped default must be a public host, not an internal one.

    Customers with no ``DCT_DOCS_URL`` get this URL in structured-error doc
    links and error hints, so an internal hostname here ships dead help to
    every installed wheel.
    """
    monkeypatch.delenv("DCT_DOCS_URL", raising=False)
    assert not any(host in DEFAULT_DOCS_SITE_URL for host in INTERNAL_HOSTS)
    assert docs_site_url() == DEFAULT_DOCS_SITE_URL


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCT_DOCS_URL", "https://docs.example.test/")
    assert docs_site_url() == "https://docs.example.test"


def test_empty_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCT_DOCS_URL", "   ")
    assert docs_site_url() == DEFAULT_DOCS_SITE_URL


def test_yaml_reference_url_follows_the_docs_site_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DCT_DOCS_URL", "https://docs.example.test/")
    assert yaml_reference_url() == "https://docs.example.test/reference/yaml-reference/"
