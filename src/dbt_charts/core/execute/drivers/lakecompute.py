"""Lake compute on dbt v2's ADBC driver.

Stage: EXECUTE

The option names are the driver's own `adbc.dbt.*` keys, which dbt v2's
`dbt-auth` crate fills from a `type: lakecompute` profile. A source field maps
to one of them or is rejected at parse time; none is dropped here.

The driver requires `adbc.dbt.base_url`. dbt's profile layer defaults it, so
this does too, from ``execution.lakecompute.default_base_url``. dbt additionally
reads ``DBT_COMPUTE_BASE_URL`` for that default; core reads no environment, so a
profile that relies on the variable writes it as ``base_url`` through
``env_var()`` and both engines agree.
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import TYPE_CHECKING

from dbt_charts._install_hint import install_hint
from dbt_charts.core.compile.config import get_lakecompute_config
from dbt_charts.core.compile.models.source import (
    LakeComputeApiKeySource,
    LakeComputeFivetranSource,
    LakeComputeOktaSource,
    LakeComputeSourceBase,
    LakeComputeTokenSource,
)
from dbt_charts.core.diagnostics.codes_execute import ERR_ADAPTER_NOT_INSTALLED
from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    AdapterNotInstalledError,
)
from dbt_charts.core.execute.drivers._cdn import open_cdn_driver

if TYPE_CHECKING:
    from dbt_charts.core.execute.drivers.base import DriverConnection

_PREFIX = "adbc.dbt."

# The packages the `lakecompute` extra installs: the loader, the decompressor for
# the CDN artifact, and the finder for the cache dbt shares.
_EXTRA_MODULES = ("adbc_driver_manager", "zstandard", "platformdirs")


def lakecompute_db_kwargs(
    config: LakeComputeSourceBase, timeout_seconds: int | None
) -> dict[str, str]:
    """The driver's database options for *config*, sending nothing for unset fields."""
    options = {
        f"{_PREFIX}base_url": config.base_url
        or get_lakecompute_config().default_base_url,
    }
    if config.catalog_bundle is not None:
        options[f"{_PREFIX}catalog_bundle"] = config.catalog_bundle
    if timeout_seconds is not None:
        options[f"{_PREFIX}timeout_seconds"] = str(timeout_seconds)
    if isinstance(config, LakeComputeTokenSource):
        options[f"{_PREFIX}auth.method"] = config.method
        options[f"{_PREFIX}auth.token"] = config.token
    elif isinstance(config, LakeComputeApiKeySource):
        options[f"{_PREFIX}auth.method"] = config.method
        options[f"{_PREFIX}auth.api_key"] = config.api_key
    elif isinstance(config, LakeComputeOktaSource):
        options[f"{_PREFIX}auth.method"] = config.method
        okta = {
            "auth_url": config.okta_auth_url,
            "token_url": config.okta_token_url,
            "client_id": config.okta_client_id,
        }
        options |= {
            f"{_PREFIX}auth.okta.{key}": value
            for key, value in okta.items()
            if value is not None
        }
    elif isinstance(config, LakeComputeFivetranSource):
        options[f"{_PREFIX}auth.method"] = config.method
        options[f"{_PREFIX}auth.fivetran.credential"] = config.fivetran_credential
        if config.fivetran_auth_url is not None:
            options[f"{_PREFIX}auth.fivetran.api_url"] = config.fivetran_auth_url
    return options


def require_lakecompute_extra() -> None:
    """Raise unless the ``lakecompute`` extra's packages are importable.

    Raises:
        AdapterNotInstalledError: A package of the extra is missing.
    """
    for module in _EXTRA_MODULES:
        if find_spec(module) is None:
            raise AdapterNotInstalledError.from_code(
                ERR_ADAPTER_NOT_INSTALLED,
                adapter_type="lakecompute",
                package=module.replace("_", "-"),
                install=install_hint("lakecompute"),
            )


def open_lakecompute(
    config: LakeComputeSourceBase, timeout_seconds: int | None
) -> DriverConnection:
    """Open a lake compute session, downloading the pinned driver on first use.

    Raises:
        AdapterNotInstalledError: The ``lakecompute`` extra is not installed.
    """
    require_lakecompute_extra()
    return open_cdn_driver("dbt", lakecompute_db_kwargs(config, timeout_seconds))
