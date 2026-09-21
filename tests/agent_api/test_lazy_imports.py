"""Guard dbt_charts.agent_api's PEP 562 laziness: importing it must not eagerly
pull in the heavy compile/execute/render/dbt stack.

Runs in a subprocess (a fresh interpreter) because sys.modules only reflects
what this process has imported so far — inside the same pytest run, an
earlier test may have already imported the modules under test here.
"""

from __future__ import annotations

import subprocess
import sys


def _sys_modules_after(import_statement: str) -> set[str]:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; {import_statement}; print('\\n'.join(sys.modules))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return set(result.stdout.splitlines())


def test_importing_agent_api_does_not_load_the_compile_execute_stack() -> None:
    modules = _sys_modules_after("import dbt_charts.agent_api")
    heavy = {
        m
        for m in modules
        if m.startswith("dbt_charts.core.compile")
        or m.startswith("dbt_charts.core.execute")
        or m == "dbt_common"
        or m.startswith("dbt_common.")
    }
    assert not heavy, f"import dbt_charts.agent_api eagerly loaded: {sorted(heavy)}"


def test_importing_cli_main_does_not_load_dbt_common_or_core_compile() -> None:
    modules = _sys_modules_after("import dbt_charts.cli.main")
    heavy = {
        m
        for m in modules
        if m.startswith("dbt_charts.core.compile")
        or m == "dbt_common"
        or m.startswith("dbt_common.")
    }
    assert not heavy, f"import dbt_charts.cli.main eagerly loaded: {sorted(heavy)}"


def test_importing_the_executor_does_not_load_dbt_core() -> None:
    """A board of inline `values` renders through the executor without dbt-core."""
    modules = _sys_modules_after("import dbt_charts.core.execute.executor")
    heavy = {
        m
        for m in modules
        if m in ("dbt", "dbt_common") or m.startswith(("dbt.", "dbt_common."))
    }
    assert not heavy, f"importing the executor eagerly loaded: {sorted(heavy)[:10]}"


def _lazy_attrs_of(module_path: str) -> dict[str, tuple[str, str]]:
    import importlib

    module = importlib.import_module(module_path)
    return module._LAZY_ATTRS  # noqa: SLF001 — inspecting the module's own dispatch table


def test_no_lazy_attr_shares_its_source_submodules_own_leaf_name() -> None:
    """A colliding name silently flips from function to module by import order.

    Importing `pkg.leaf` for any reason binds it onto `pkg` under `leaf` as a
    side effect of Python's own import machinery — a rebind PEP 562
    `__getattr__` cannot see or prevent. That side effect lands specifically
    on `pkg` (the immediate parent of the imported submodule), so a
    `_LAZY_ATTRS` entry is unsafe only when its own package IS that parent
    and its key equals the leaf. (See the regression this test guards:
    `agent_api.schema_hints`/`validate_query`, `core.compile`/`render` — but
    `dbt_charts.compile`/`.render` alone are fine, since their side effect
    lands on `dbt_charts.core`, not on `dbt_charts` itself.)
    """
    for package in (
        "dbt_charts",
        "dbt_charts.core",
        "dbt_charts.core.execute",
        "dbt_charts.core.inspect",
        "dbt_charts.agent_api",
    ):
        for name, (module_path, attr_name) in _lazy_attrs_of(package).items():
            if module_path == package:
                continue  # the "re-export a submodule of this same package" case
            parent, _, leaf = module_path.rpartition(".")
            assert parent != package or name != leaf or not attr_name, (
                f"{package}._LAZY_ATTRS[{name!r}] resolves an attribute from "
                f"{module_path!r}, a direct submodule of {package!r} whose own "
                f"leaf name is also {name!r} — this alias silently becomes the "
                f"raw module instead of {attr_name!r} whenever anything "
                "imports that submodule first."
            )
