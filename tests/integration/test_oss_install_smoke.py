"""End-to-end install-smoke regression for the published `dbt-charts` wheel.

Builds the wheel, installs it into a fresh venv rooted **outside the repo**,
and exercises the surface a PyPI user encounters on first install:

- `dct --help` loads the full Typer app (subcommands resolved).
- `import dbt_charts` produces a PEP 440-shaped `__version__` and resolves
  to `<venv>/site-packages/dbt_charts/`, not the in-repo `src/dbt_charts/`.
- `dct render <fixture>` succeeds on a self-contained inline-CSV board.
- `dct mcp serve` without the `[mcp]` extras exits 1 with a friendly hint
  that names the missing packages.
- The wheel's `METADATA` `Version:` carries no PEP 440 local label
  (`+g<sha>`) and `RECORD` contains the top-level `dbt_charts/` package
  but no `AGENTS.md` / `CLAUDE.md` files.

The outside-the-repo venv is load-bearing: with src/ layout there is no
top-level `dbt_charts/` package the implicit-CWD `sys.path` can find. A venv
under the repo would silently mask packaging bugs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from .._extra_probes import EXTRA_PROBES, FIXTURE_DIR
from .._paths import DBT_CHARTS_DIR

# Share `built_dbt_charts_wheel` with the other wheel-consuming files so
# `uv build` runs once per artifact across the suite.
pytestmark = [
    pytest.mark.xdist_group("dbt_charts_wheel"),
    # Wheel build + clean-venv install exceeds the repo-wide 30s timeout on
    # loaded machines; the setup cost is billed to the first test in the module.
    pytest.mark.timeout(180),
    # Resolving the wheel's dependencies into the fresh venv reaches PyPI, and one
    # of them (dbt-core-experimental-parser) downloads a release artifact from
    # GitHub inside its own build backend, via a raw urlopen that honors no proxy
    # CA configuration. Behind an intercepting proxy that is an unconditional
    # CERTIFICATE_VERIFY_FAILED, so the module cannot run there at all — it was
    # erroring out of every local `just ci`, six failures deep, none of them real.
    # `just test` already deselects `network`; this puts the module in
    # the same bucket so a sandboxed run goes green and CI (which has egress)
    # still covers it.
    pytest.mark.network,
]


def _venv_python(venv_root: Path) -> Path:
    if sys.platform == "win32":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def _make_smoke_venv(wheel: Path, prefix: str, extras: str = "") -> Iterator[Path]:
    """Create a venv outside the repo, install the wheel (+ extras), yield python.

    `tempfile.TemporaryDirectory()` rooted at the platform default (`/tmp` or
    `/var/folders` on macOS/Linux) — explicitly NOT under DBT_CHARTS_DIR so that
    `sys.path` resolution inside the venv cannot reach the repo's source tree.
    """
    with tempfile.TemporaryDirectory(prefix=prefix) as base:
        base_path = Path(base).resolve()
        assert DBT_CHARTS_DIR not in base_path.parents, (
            f"smoke-venv leaked into repo: {base_path}"
        )
        venv_root = base_path / "venv"
        # `uv venv` creates a clean venv with the host interpreter; pair it with
        # `uv pip install --python <venv-py>` so we never rely on `ensurepip`
        # (which is unavailable on uv-managed CPython builds). Leave stderr
        # uncaptured so a failure surfaces in the pytest log directly.
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(venv_root)],
            check=True,
        )
        py = _venv_python(venv_root)
        install_target = f"{wheel}[{extras}]" if extras else str(wheel)
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(py),
                "--quiet",
                install_target,
            ],
            check=True,
        )
        yield py


@pytest.fixture(scope="module")
def smoke_venv(built_dbt_charts_wheel: Path) -> Iterator[Path]:
    """Create a venv outside the repo, install the bare wheel, yield python."""
    yield from _make_smoke_venv(built_dbt_charts_wheel, prefix="dct-oss-smoke-")


def _run(
    py: Path,
    *args: str,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the smoke venv's python with deterministic Rich output formatting.

    ``timeout`` bounds commands that would block if their gate ever stopped
    firing (``dct mcp serve``), so a regression fails instead of hanging.
    """
    merged_env = {**os.environ, "COLUMNS": "200", "FORCE_COLOR": "0"}
    if env:
        merged_env.update(env)
    # Drop variables that would point the subprocess back at the repo's venv.
    merged_env.pop("VIRTUAL_ENV", None)
    merged_env.pop("PYTHONPATH", None)
    return subprocess.run(
        [str(py), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=merged_env,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
    )


# --- CLI smoke ---------------------------------------------------------------


def test_dct_help_and_version_in_clean_venv(smoke_venv: Path) -> None:
    """CLI loads end-to-end + version resolves via `importlib.metadata`."""
    help_result = _run(smoke_venv, "-m", "dbt_charts.cli.main", "--help")
    assert help_result.returncode == 0, help_result.stderr
    # Listing subcommands proves the full Typer app loaded, not just the
    # entry module — a missing optional import in any registered command
    # would crash on `--help`.
    assert "render" in help_result.stdout.lower()

    version_result = _run(
        smoke_venv, "-c", "import dbt_charts; print(dbt_charts.__version__)"
    )
    assert version_result.returncode == 0, version_result.stderr
    printed = version_result.stdout.strip()
    # PEP 440-shaped: 0.1.2 or 0.1.2.dev45 or 1.0.0rc1 etc. Reject local
    # labels — the explicit METADATA test pins the `+g<sha>` suppression.
    assert re.match(r"^\d+\.\d+\.\d+([a-z]+\d+|\.dev\d+)?$", printed), printed


def test_dct_render_smoke_in_clean_venv(smoke_venv: Path, tmp_path: Path) -> None:
    """build → install → render an inline-CSV board → non-empty SVG."""
    work = tmp_path / "render"
    work.mkdir()
    shutil.copy(FIXTURE_DIR / "charts" / "sample.yml", work / "sample.yml")
    shutil.copy(FIXTURE_DIR / "sample.csv", work / "sample.csv")
    shutil.copy(FIXTURE_DIR / "dbt_charts.yml", work / "dbt_charts.yml")

    result = _run(
        smoke_venv,
        "-m",
        "dbt_charts.cli.main",
        "render",
        "sample.yml",
        "--format",
        "svg",
        "--output",
        str(work / "sample.svg"),
        "--project-dir",
        str(work),
        cwd=work,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    output = work / "sample.svg"
    assert output.exists() and output.stat().st_size > 0


def test_dct_mcp_friendly_message_without_extras(smoke_venv: Path) -> None:
    """`dct mcp serve` without the `[mcp]` extras prints a usable install hint.

    Without the `[mcp]` extras installed, `dct mcp serve` must:
      - exit non-zero (1),
      - name the installer-appropriate command (`pip install` here),
      - render the literal `[mcp]` string. Rich would otherwise interpret
        the brackets as markup and elide them, leaving the user with an
        unrunnable `pip install 'dbt-charts @ git+…'` suggestion.

    The bracket assertion is the load-bearing one: the `mcp` extra's only
    package is also called `mcp`, so grepping the package name would pass on
    the extra name alone and could not fail.

    `DCT_NO_AUTO_INSTALL=1` disables the interactive auto-install fallback
    so the friendly panel is the deterministic output.
    """
    result = _run(
        smoke_venv,
        "-m",
        "dbt_charts.cli.main",
        "mcp",
        "serve",
        env={"DCT_NO_AUTO_INSTALL": "1", "DCT_NO_WORKSPACE_GUARD": "1"},
        # `mcp serve` blocks past its gate; bound it so a regression fails
        # cleanly rather than hanging until the module-level pytest timeout.
        timeout=30,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n{combined}"
    )
    assert "pip install" in combined, combined
    assert "[mcp]" in combined, f"missing literal [mcp] in:\n{combined}"


def test_installed_dbt_charts_resolves_outside_repo(smoke_venv: Path) -> None:
    """`import dbt_charts` resolves to site-packages, not the in-repo src tree."""
    result = _run(
        smoke_venv,
        "-c",
        "import dbt_charts; print(dbt_charts.__file__)",
    )
    assert result.returncode == 0, result.stderr
    file_path = result.stdout.strip()
    assert "/site-packages/" in file_path, file_path
    assert str(DBT_CHARTS_DIR) not in file_path, (
        f"dbt_charts resolved to in-repo source: {file_path}"
    )


def test_docs_reference_topic_in_clean_venv(smoke_venv: Path) -> None:
    """`docs(topic="reference")` succeeds from the installed wheel."""
    result = _run(
        smoke_venv,
        "-c",
        "from dbt_charts.agent_api.docs import docs; r = docs(topic='reference'); assert r.success, r.errors",
    )
    assert result.returncode == 0, result.stderr


# --- Wheel shape invariants --------------------------------------------------


def _read_wheel_member(wheel_path: Path, suffix: str) -> str:
    with zipfile.ZipFile(wheel_path) as zf:
        name = next(n for n in zf.namelist() if n.endswith(suffix))
        return zf.read(name).decode()


@pytest.fixture(scope="session")
def wheel_record(built_dbt_charts_wheel: Path) -> list[str]:
    return _read_wheel_member(built_dbt_charts_wheel, ".dist-info/RECORD").splitlines()


@pytest.fixture(scope="session")
def wheel_metadata(built_dbt_charts_wheel: Path) -> str:
    return _read_wheel_member(built_dbt_charts_wheel, ".dist-info/METADATA")


def test_wheel_metadata_no_local_label(wheel_metadata: str) -> None:
    """`_build_backend.py` strips the `+g<sha>` PEP 440 local label.

    PyPI rejects uploads whose version carries a local label, so an unstripped
    suffix would block release. The check pins the empirical behavior of
    `local_scheme = "no-local-version"` against an untagged commit.
    """
    version_lines = [
        line for line in wheel_metadata.splitlines() if line.startswith("Version:")
    ]
    assert len(version_lines) == 1, version_lines
    version = version_lines[0].removeprefix("Version:").strip()
    assert re.match(r"^\d+\.\d+\.\d+([a-z]+\d+|\.dev\d+)?$", version), (
        f"unexpected version shape (local label leak?): {version!r}"
    )
    assert "+" not in version, f"PEP 440 local label present: {version!r}"


def test_wheel_record_layout(wheel_record: list[str]) -> None:
    """RECORD ships the published-artifact shape end-to-end.

    Top-level `dbt_charts/`, vendored `mdsvg/`, LICENSE, and at least one font.
    The negative `no src/ prefix` assertion is the load-bearing one — that is
    the most likely src-layout regression and would not be caught by the
    anchor-per-category checks in `test_wheel_asset_inventory.py`.
    """
    paths = {line.split(",")[0] for line in wheel_record if line.strip()}

    # Top-level Python package — NOT `src/dbt_charts/...`. The negative
    # assertion catches the case where a future packaging change accidentally
    # nests sources under `src/` inside the wheel.
    assert "dbt_charts/__init__.py" in paths
    assert not any(p.startswith("src/") for p in paths), [
        p for p in paths if p.startswith("src/")
    ]

    # Vendored markdown-svg ships as a top-level package alongside `dbt_charts/`.
    assert "mdsvg/__init__.py" in paths

    # PEP 639 places `license-files` entries under `<dist-info>/licenses/`.
    license_paths = [p for p in paths if p.endswith("/LICENSE") or p == "LICENSE"]
    assert license_paths, "LICENSE missing from wheel RECORD"

    # Bundled fonts under the package's font directory. Font path is a render
    # contract: a missing font is a crash, not a fallback.
    font_paths = [
        p
        for p in paths
        if p.startswith("dbt_charts/core/render/fonts/") and p.endswith(".ttf")
    ]
    assert font_paths, "no bundled fonts under dbt_charts/core/render/fonts/"


def test_wheel_record_excludes_dev_context(wheel_record: list[str]) -> None:
    """No `AGENTS.md` / `CLAUDE.md` files leak into the published wheel.

    Backstops `exclude = ["**/AGENTS.md", "**/CLAUDE.md"]` on the wheel target
    in `dbt-charts/pyproject.toml`: any drift in that rule lands in this RECORD.
    """
    paths = [line.split(",")[0] for line in wheel_record if line.strip()]
    leaked = [p for p in paths if p.endswith("/AGENTS.md") or p.endswith("/CLAUDE.md")]
    assert not leaked, f"developer-context files leaked into wheel: {leaked}"


# ---- Extras probes -----------------------------------------------------------
# mcp 2.0 imported fine and broke only when `dct mcp serve` actually built the
# server — installing an extra proves nothing; the probe must exercise what
# the extra exists for. One shared venv installs every probed extra together
# (the probes are independent — each just imports or constructs, none mutate
# shared state) so the PyPI resolve is paid once for the whole table, not once
# per extra.


@pytest.fixture(scope="module")
def smoke_venv_with_extras(built_dbt_charts_wheel: Path) -> Iterator[Path]:
    """Like `smoke_venv`, but with every probed extra installed at once."""
    yield from _make_smoke_venv(
        built_dbt_charts_wheel,
        prefix="dct-oss-smoke-extras-",
        extras=",".join(EXTRA_PROBES),
    )


@pytest.mark.parametrize("extra", sorted(EXTRA_PROBES))
def test_extra_probe(smoke_venv_with_extras: Path, extra: str) -> None:
    """Each declared extra must do the one thing it exists for, once installed.

    Depth is deliberately per-extra: an import is enough where the extra only
    needs its declared package importable; `mcp` constructs the actual server,
    because that is exactly where the private-API coupling — and the mcp 2.0
    break — lives.
    """
    result = _run(smoke_venv_with_extras, "-c", EXTRA_PROBES[extra])
    assert result.returncode == 0, (
        f"[{extra}] probe failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---- Profiler-absent assertions ------------------------------------------------
# After the super_schema extraction, these modules must not be importable from
# the OSS dbt-charts package. The wheel-time check_published_artifact.py gate is
# the primary guard; this runtime assertion catches the case where the wheel
# ships clean but a sibling package leaks the module path back into sys.modules.


def test_profiler_modules_absent_from_oss_install(smoke_venv: Path) -> None:
    """Profiler modules are not importable after a clean pip install of dbt-charts.

    Each assertion uses `importlib.util.find_spec` rather than a try/import so
    that a present-but-broken module fails the test rather than masking the leak.
    """
    check_script = (
        "import importlib.util as u\n"
        "_absent = [\n"
        "    'dbt_charts.core.inspect.inspector',\n"
        "    'dbt_charts.core.inspect.storage',\n"
        "    'dbt_charts.core.inspect.sources.super_schema',\n"
        "    'dbt_charts.core.inspect.semantic_detector',\n"
        "    'dbt_charts.core.inspect.quality_detector',\n"
        "    'dbt_charts.core.inspect.grain_detector',\n"
        "]\n"
        "present = [m for m in _absent if u.find_spec(m) is not None]\n"
        "if present:\n"
        "    raise AssertionError(f'Profiler modules found in OSS wheel: {present}')\n"
        "print('OK: all profiler modules absent from OSS install')\n"
    )
    result = _run(smoke_venv, "-c", check_script)
    assert result.returncode == 0, (
        f"Profiler modules leaked into OSS wheel.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
