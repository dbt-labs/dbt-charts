"""Lake compute's connection options: every source field reaches the driver or
is rejected at parse time, and nothing else is sent.

The option names are dbt v2's (`dbt-auth/src/lake_compute/mod.rs`); the driver
requires `adbc.dbt.base_url`, which dbt's own profile layer supplies and dbt
charts therefore supplies too, from config rather than a literal.
"""

from __future__ import annotations

from typing import Any, get_args
from unittest.mock import patch

import pytest

from dbt_charts.core.compile.config import get_lakecompute_config
from dbt_charts.core.compile.models.source import (
    LakeComputeApiKeySource,
    LakeComputeFivetranSource,
    LakeComputeOktaSource,
    LakeComputeSourceBase,
    LakeComputeTokenSource,
)
from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    AdapterNotInstalledError,
)
from dbt_charts.core.execute.drivers.lakecompute import (
    lakecompute_db_kwargs,
    open_lakecompute,
)

_DEFAULT_URL = get_lakecompute_config().default_base_url


def _token(**fields: Any) -> LakeComputeTokenSource:
    return LakeComputeTokenSource(type="lakecompute", token="t", **fields)


def test_token_auth_maps_to_the_driver_options() -> None:
    assert lakecompute_db_kwargs(_token(), None) == {
        "adbc.dbt.base_url": _DEFAULT_URL,
        "adbc.dbt.auth.method": "token",
        "adbc.dbt.auth.token": "t",
    }


def test_api_key_auth() -> None:
    config = LakeComputeApiKeySource(type="lakecompute", method="api_key", api_key="k")
    kwargs = lakecompute_db_kwargs(config, None)
    assert kwargs["adbc.dbt.auth.method"] == "api_key"
    assert kwargs["adbc.dbt.auth.api_key"] == "k"
    assert "adbc.dbt.auth.token" not in kwargs


def test_okta_auth_sends_only_the_urls_and_client_that_are_set() -> None:
    config = LakeComputeOktaSource(
        type="lakecompute",
        method="okta_browser",
        okta_auth_url="https://a",
        okta_client_id="c",
    )
    kwargs = lakecompute_db_kwargs(config, None)
    assert kwargs["adbc.dbt.auth.method"] == "okta_browser"
    assert kwargs["adbc.dbt.auth.okta.auth_url"] == "https://a"
    assert kwargs["adbc.dbt.auth.okta.client_id"] == "c"
    assert "adbc.dbt.auth.okta.token_url" not in kwargs


def test_fivetran_auth_maps_the_credential_and_its_url() -> None:
    config = LakeComputeFivetranSource(
        type="lakecompute",
        method="fivetran",
        fivetran_credential="p",
        fivetran_auth_url="https://f",
    )
    kwargs = lakecompute_db_kwargs(config, None)
    assert kwargs["adbc.dbt.auth.method"] == "fivetran"
    assert kwargs["adbc.dbt.auth.fivetran.credential"] == "p"
    assert kwargs["adbc.dbt.auth.fivetran.api_url"] == "https://f"


def test_the_endpoint_is_the_sources_own_when_set() -> None:
    kwargs = lakecompute_db_kwargs(_token(base_url="https://staging/"), None)
    assert kwargs["adbc.dbt.base_url"] == "https://staging/"


def test_the_endpoint_defaults_from_config_not_from_a_literal() -> None:
    """The driver documents `base_url` as required, so it is always sent."""
    with patch(
        "dbt_charts.core.execute.drivers.lakecompute.get_lakecompute_config"
    ) as getter:
        getter.return_value.default_base_url = "https://from-config/"
        kwargs = lakecompute_db_kwargs(_token(), None)
    assert kwargs["adbc.dbt.base_url"] == "https://from-config/"


def test_catalog_bundle_is_sent_only_when_set() -> None:
    assert "adbc.dbt.catalog_bundle" not in lakecompute_db_kwargs(_token(), None)
    kwargs = lakecompute_db_kwargs(_token(catalog_bundle="analytics"), None)
    assert kwargs["adbc.dbt.catalog_bundle"] == "analytics"


def test_the_timeout_is_sent_as_the_drivers_own_option() -> None:
    kwargs = lakecompute_db_kwargs(_token(), 60)
    assert kwargs["adbc.dbt.timeout_seconds"] == "60"


def test_no_timeout_means_the_option_is_absent() -> None:
    assert "adbc.dbt.timeout_seconds" not in lakecompute_db_kwargs(_token(), None)


@pytest.mark.parametrize(
    "variant",
    [
        LakeComputeTokenSource,
        LakeComputeApiKeySource,
        LakeComputeOktaSource,
        LakeComputeFivetranSource,
    ],
)
def test_every_connection_field_reaches_the_driver(
    variant: type[LakeComputeSourceBase],
) -> None:
    """No silent drop: each field a variant declares, once set, is sent. A field
    added to a variant without a mapping fails here. `cache` is cache policy,
    `max_query_duration_seconds` travels as the timeout argument, and `metricflow`
    names a semantic bundle the compiler reads, not the driver."""
    not_connection = {
        "type",
        "method",
        "cache",
        "max_query_duration_seconds",
        "metricflow",
    }
    fields = {
        name: f"v-{name}" for name in variant.model_fields if name not in not_connection
    }
    method = get_args(variant.model_fields["method"].annotation)[0]
    config = variant(type="lakecompute", method=method, **fields)
    assert set(fields.values()) <= set(lakecompute_db_kwargs(config, None).values())


def test_opening_hands_the_options_to_the_cdn_driver() -> None:
    with patch(
        "dbt_charts.core.execute.drivers.lakecompute.open_cdn_driver"
    ) as open_cdn:
        connection = open_lakecompute(_token(), 30)
    assert connection is open_cdn.return_value
    assert open_cdn.call_args.args[0] == "dbt"
    assert open_cdn.call_args.args[1] == lakecompute_db_kwargs(_token(), 30)


def test_a_missing_driver_manager_raises_the_install_hint() -> None:
    with (
        patch(
            "dbt_charts.core.execute.drivers.lakecompute.find_spec",
            side_effect=lambda name: (
                None if name == "adbc_driver_manager" else object()
            ),
        ),
        pytest.raises(AdapterNotInstalledError) as excinfo,
    ):
        open_lakecompute(_token(), None)
    assert "dbt-charts[lakecompute]" in str(excinfo.value)
