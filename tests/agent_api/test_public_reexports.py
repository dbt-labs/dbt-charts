import dbt_charts.agent_api as agent_api
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.execute.cache_backend import QueryResultCache
from dbt_charts.core.execute.observability import WarehouseObserver
from dbt_charts.core.execute.source_resolver import (
    AllowlistedSourceResolver,
    DefaultSourceResolver,
    SourceResolver,
)
from dbt_charts.core.fonts import get_fonts_dir
from dbt_charts.core.render.board_links import LinkContext
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render_format import RenderFormat


def test_public_reexports_are_identity_equal_to_canonical() -> None:
    assert agent_api.LinkContext is LinkContext
    assert agent_api.Diagnostic is Diagnostic
    assert agent_api.RenderError is RenderError
    assert agent_api.get_fonts_dir is get_fonts_dir
    assert agent_api.RenderFormat is RenderFormat
    for name in (
        "LinkContext",
        "Diagnostic",
        "RenderError",
        "get_fonts_dir",
        "RenderFormat",
    ):
        assert name in agent_api.__all__, f"{name} missing from __all__"


def test_every_all_entry_resolves() -> None:
    """`from dbt_charts.agent_api import *` must not raise.

    Ruff's F822 (undefined name in `__all__`) is not in this repo's gating rule
    set, so a name added to `__all__` without a matching import passes every
    lint lane and only fails in a consumer's star-import. This is the lane.
    """
    missing = [n for n in agent_api.__all__ if not hasattr(agent_api, n)]
    assert not missing, (
        f"__all__ names nothing importable: {missing} — either import them or "
        "drop the entries; a star-import of agent_api raises AttributeError"
    )


def test_every_lazy_attr_function_is_in_all() -> None:
    """Every lazy-loaded function/class name is re-exported via `__all__`.

    `examples` and `skills` are submodules bound onto the package, not
    function/class re-exports, so they're excluded from this check.
    """
    lazy_names = set(agent_api._LAZY_ATTRS) - {"examples", "skills"}
    missing = lazy_names - set(agent_api.__all__)
    assert not missing, f"lazy attrs missing from __all__: {missing}"


def test_execution_seams_are_reexported() -> None:
    """The execution extension seams are reachable through `dbt_charts.agent_api`.

    Composition roots (CLI, MCP, serve, Cloud, A lIe, Playground) compose against
    `dbt_charts.agent_api`, not `dbt_charts.core.*`. The extension protocols and the
    resolver base impls a root subclasses are re-exported here so callers have one
    public home for the seam surface. `ExecEnv` is intentionally absent — it
    is built via `ProjectSession` / `build_exec_env`, not subclassed.
    """
    assert agent_api.SourceResolver is SourceResolver
    assert agent_api.DefaultSourceResolver is DefaultSourceResolver
    assert agent_api.AllowlistedSourceResolver is AllowlistedSourceResolver
    assert agent_api.QueryResultCache is QueryResultCache
    assert agent_api.WarehouseObserver is WarehouseObserver
    for name in (
        "SourceResolver",
        "DefaultSourceResolver",
        "AllowlistedSourceResolver",
        "QueryResultCache",
        "WarehouseObserver",
    ):
        assert name in agent_api.__all__, f"{name} missing from __all__"
