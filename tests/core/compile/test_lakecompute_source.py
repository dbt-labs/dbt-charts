"""The `lakecompute` source: dbt v2's profile keys, flat, one variant per auth method.

Each auth method is its own closed model, so a combination dbt v2 would reject
(a token alongside an API key) cannot be typed at all.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.source import (
    VALID_SOURCE_TYPES,
    LakeComputeApiKeySource,
    LakeComputeFivetranSource,
    LakeComputeOktaSource,
    LakeComputeSourceConfig,
    LakeComputeTokenSource,
    parse_source_config,
)

_VARIANTS: dict[str, tuple[type, dict[str, Any]]] = {
    "token": (LakeComputeTokenSource, {"token": "t"}),
    "api_key": (LakeComputeApiKeySource, {"api_key": "k"}),
    "okta_browser": (
        LakeComputeOktaSource,
        {
            "okta_auth_url": "https://a",
            "okta_token_url": "https://t",
            "okta_client_id": "c",
        },
    ),
    "fivetran": (
        LakeComputeFivetranSource,
        {"fivetran_credential": "p", "fivetran_auth_url": "https://f"},
    ),
}


def _source(method: str | None, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"type": "lakecompute", **fields}
    if method is not None:
        base["method"] = method
    return base


@pytest.mark.parametrize("method", sorted(_VARIANTS))
def test_each_auth_method_parses_to_its_own_variant(method: str) -> None:
    cls, fields = _VARIANTS[method]
    parsed = parse_source_config(_source(method, **fields))
    assert type(parsed) is cls
    assert parsed.method == method


def test_method_defaults_to_token() -> None:
    """dbt's documented default; a profile that omits `method:` means token."""
    parsed = parse_source_config(_source(None, token="t"))
    assert type(parsed) is LakeComputeTokenSource
    assert parsed.method == "token"


def test_lakecompute_is_a_valid_source_type() -> None:
    assert "lakecompute" in VALID_SOURCE_TYPES


def test_optional_fields_are_optional() -> None:
    parsed = parse_source_config(
        _source("token", token="t", base_url="https://x/", catalog_bundle="b")
    )
    assert (parsed.base_url, parsed.catalog_bundle) == ("https://x/", "b")
    bare = parse_source_config(_source("token", token="t"))
    assert (bare.base_url, bare.catalog_bundle) == (None, None)


def test_max_query_duration_seconds_is_accepted() -> None:
    parsed = parse_source_config(
        _source("token", token="t", max_query_duration_seconds=30)
    )
    assert parsed.max_query_duration_seconds == 30


@pytest.mark.parametrize(
    "extra",
    [
        {"warehouse": "w"},
        {"database": "d"},
        {"schema": "s"},
        {"attribution": {"team": "analytics"}},
        {"api_key": "k"},
        {"fivetran_credential": "p"},
    ],
)
def test_an_unknown_key_or_another_methods_field_is_rejected(
    extra: dict[str, Any],
) -> None:
    """`database:`/`schema:` would be silent no-ops (nothing on the read path
    consumes them), and `attribution:` has no query-header channel on the driver
    path yet, so each fails extra-forbidden rather than being accepted and
    dropped."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        parse_source_config(_source("token", token="t", **extra))


@pytest.mark.parametrize(
    ("method", "missing"),
    [
        ("token", "token"),
        ("api_key", "api_key"),
        ("fivetran", "fivetran_credential"),
    ],
)
def test_a_method_without_its_credential_is_a_missing_field(
    method: str, missing: str
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        parse_source_config(_source(method))
    assert [e["loc"][-1] for e in excinfo.value.errors()] == [missing]
    assert excinfo.value.errors()[0]["type"] == "missing"


def test_an_unknown_method_names_the_valid_ones() -> None:
    with pytest.raises(
        ValueError,
        match=r"Unknown lakecompute method 'password'; expected one of: "
        r"token, api_key, okta_browser, fivetran",
    ):
        parse_source_config(_source("password", token="t"))


@pytest.mark.parametrize("retired", ["alt", "lake_compute"])
def test_a_retired_spelling_names_the_current_one(retired: str) -> None:
    """dbt v2 rejects these rather than aliasing them, so dbt Charts does too."""
    with pytest.raises(
        ValueError, match=f"'{retired}' is not a source type"
    ) as excinfo:
        parse_source_config({"type": retired, "token": "t"})
    assert "dbt v2 spells lake compute 'lakecompute'" in str(excinfo.value)


def test_the_json_schema_shows_flat_fields_under_one_of() -> None:
    schema = TypeAdapter(LakeComputeSourceConfig).json_schema()
    variants = schema["oneOf"]
    assert len(variants) == 4
    resolved = [schema["$defs"][v["$ref"].rsplit("/", 1)[1]] for v in variants]
    properties = {name for v in resolved for name in v["properties"]}
    assert {
        "token",
        "api_key",
        "okta_auth_url",
        "okta_token_url",
        "okta_client_id",
        "fivetran_credential",
        "fivetran_auth_url",
        "base_url",
        "catalog_bundle",
    } <= properties
    assert "auth" not in properties
