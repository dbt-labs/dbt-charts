"""Tests for project-config-driven cache boot enablement.

Covers `resolve_cache_boot`: the flag > env > project-config precedence used
by serve/render/MCP boots to decide whether/where to open the result cache.
The CLI's --cache/DCT_CACHE_PATH env backing is Typer's `envvar=` binding, but
the path itself may still be relative — `resolve_cache_boot` is what makes
`CacheBoot.path` absolute and symlink-resolved, for every branch.
"""

from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import reset_config, resolve_cache_boot


def _project(tmp_path: Path, dbt_charts_yml: str | None = None) -> FilesystemProject:
    if dbt_charts_yml is not None:
        (tmp_path / "dbt_charts.yml").write_text(dbt_charts_yml)
    return FilesystemProject(tmp_path)


class TestResolveCacheBootDefaults:
    def test_no_project_config_defaults_to_shipped_root(self, tmp_path: Path) -> None:
        """No dbt_charts.yml: shipped default (enabled, in-memory) applies."""
        reset_config()
        boot = resolve_cache_boot(_project(tmp_path))
        assert boot.enabled is True
        assert boot.path is None


class TestResolveCacheBootFlagsOverrideConfig:
    def test_no_cache_flag_wins_over_config_enabled(self, tmp_path: Path) -> None:
        reset_config()
        project = _project(tmp_path, "cache:\n  path: .dct/cache.duckdb\n")
        boot = resolve_cache_boot(project, no_cache=True)
        assert boot.enabled is False
        assert boot.path is None

    def test_cache_path_flag_wins_over_config_disabled(self, tmp_path: Path) -> None:
        reset_config()
        project = _project(tmp_path, "cache: false\n")
        explicit = tmp_path / "explicit.duckdb"
        boot = resolve_cache_boot(project, cache_path=explicit)
        assert boot.enabled is True
        assert boot.path == explicit

    def test_relative_cache_path_flag_resolves_against_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`dct serve --cache cache.duckdb` (the CLI help's own example) passes a
        bare relative Path — this must not reach the watcher unresolved, or the
        live-reload exclusion's string-prefix match against it never matches."""
        reset_config()
        project = _project(tmp_path)
        monkeypatch.chdir(tmp_path)
        boot = resolve_cache_boot(project, cache_path=Path("cache.duckdb"))
        assert boot.enabled is True
        assert boot.path == tmp_path / "cache.duckdb"


class TestResolveCacheBootFromProjectConfig:
    def test_config_disabled_still_provisions_the_store(self, tmp_path: Path) -> None:
        """`cache: false` at the root is the cascade default, not a kill switch.

        Nearest scope wins everywhere else in the cascade, so a source, board or
        query below a disabled root can still opt in with `cache: 1h` — and that
        opt-in needs somewhere to land. `--no-cache` is the only kill switch.
        """
        reset_config()
        project = _project(tmp_path, "cache: false\n")
        boot = resolve_cache_boot(project)
        assert boot.enabled is True
        assert boot.path is None

    def test_config_path_opens_persistent_file_relative_to_project_root(
        self, tmp_path: Path
    ) -> None:
        reset_config()
        project = _project(tmp_path, "cache:\n  path: .dct/cache.duckdb\n")
        boot = resolve_cache_boot(project)
        assert boot.enabled is True
        assert boot.path == tmp_path / ".dct" / "cache.duckdb"

    def test_config_no_path_stays_in_memory(self, tmp_path: Path) -> None:
        reset_config()
        project = _project(tmp_path, "cache: 1h\n")
        boot = resolve_cache_boot(project)
        assert boot.enabled is True
        assert boot.path is None
