"""Bootstrap a dbt MacroManifest from pip-installed dbt packages.

dbt-core's BaseAdapter introspection API (`list_schemas`, `list_relations`,
`get_columns_in_relation`, ...) routes through `execute_macro()`, which needs
a populated `_macro_resolver`. Without it, every adapter built by
`build_adapter()` raises "no macro registered" the moment we try to introspect.

This module loads the bundled macros — dbt-core's global project plus the
active warehouse adapter's package and those it depends on — into a fresh
`MacroManifest` that can be attached to an adapter via `set_macro_resolver()`.
No user files required, no `dbt_project.yml`, no `target/manifest.json`. Just
pip-installed packages.

Coupled to dbt-internal API (`MacroParser`, `MacroManifest`,
`load_source_file`, `FACTORY.packages`); the smoke tests are the canary for
upgrades.
"""

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path  # noqa: TID251 — reads dbt package macro dirs off disk
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dbt.contracts.graph.manifest import MacroManifest

logger = logging.getLogger(__name__)


class DbtMacroLoaderError(RuntimeError):
    """Raised when bootstrapping the dbt MacroManifest fails."""


def _resolve_macro_packages(adapter_type: str) -> list[tuple[str, Path]]:
    """Return [(package_name, package_root), ...] for warehouse + global macros.

    Uses dbt's adapter FACTORY to resolve include paths the same way dbt-core
    does — by loading the plugin and reading the registered include directory.
    Warehouse packages (adapter first, then its dependencies, e.g. redshift →
    postgres) come first so their macros parse before the global ones,
    matching dbt-core's lookup precedence (warehouse-specific overrides win).
    """
    from dbt.adapters.factory import FACTORY

    FACTORY.load_plugin(adapter_type)
    return [
        *(
            (plugin.project_name, FACTORY.packages[plugin.project_name])
            for plugin in FACTORY.get_adapter_plugins(adapter_type)
        ),
        ("dbt", FACTORY.packages["dbt"]),
    ]


@cache
def load_macro_manifest(adapter_type: str) -> MacroManifest:
    """Build a MacroManifest containing dbt-core + warehouse macros.

    Cached per adapter_type for the process lifetime — the bundled macros
    don't change between calls within a single session.
    """
    from dbt.contracts.files import ParseFileType
    from dbt.contracts.graph.manifest import MacroManifest, Manifest
    from dbt.parser.macros import MacroParser
    from dbt.parser.read_files import load_source_file
    from dbt.parser.search import FileBlock

    packages = _resolve_macro_packages(adapter_type)
    manifest = Manifest()
    for package_name, package_root in packages:
        macros_dir = Path(package_root) / "macros"
        if not macros_dir.is_dir():
            raise DbtMacroLoaderError(
                f"dbt {package_name} macros not found at {macros_dir}. "
                f"This usually means the dbt-core or dbt-{adapter_type} "
                f"package layout changed; run the smoke test in "
                f"test_dbt_macro_loader.py to see what dbt-core version "
                f"this code was last validated against."
            )
        # MacroParser only reads project_root / project_name / macro_paths off
        # the project arg, so a SimpleNamespace duck-type is sufficient.
        fake_project = SimpleNamespace(
            project_root=str(package_root),
            project_name=package_name,
            macro_paths=["macros"],
        )
        parser = MacroParser(fake_project, manifest)  # type: ignore[arg-type]
        for path in parser.get_paths():
            source_file = load_source_file(path, ParseFileType.Macro, package_name, {})
            assert source_file is not None
            parser.parse_file(FileBlock(source_file))

    logger.debug(
        "Loaded %d dbt macros for adapter_type=%s", len(manifest.macros), adapter_type
    )
    return MacroManifest(manifest.macros)
