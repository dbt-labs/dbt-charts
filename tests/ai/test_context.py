"""Tests for DbtChartsAIContext."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PureWindowsPath

import pytest

from dbt_charts.agent_api import ProjectSession
from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.cli.filesystem_project import FilesystemProject


@pytest.fixture
def ctx(tmp_path: Path) -> DbtChartsAIContext:
    return DbtChartsAIContext(
        project_session=ProjectSession.open(tmp_path, read_only=True),
        dashboards_directory=tmp_path / "charts",
    )


class TestResolveDashboardPath:
    """resolve_dashboard_path resolves relative paths against the configured directory."""

    def test_resolves_relative_path_inside_dashboards_directory(
        self, ctx: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        (boards_dir / "sales.yml").write_text("title: Sales\n")
        result = ctx.resolve_dashboard_path(Path("sales.yml"))
        assert result == boards_dir / "sales.yml"

    def test_rejects_absolute_path(
        self, ctx: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        with pytest.raises(ValueError, match="must be relative"):
            ctx.resolve_dashboard_path(tmp_path / "sales.yml")

    def test_rejects_path_traversal(
        self, ctx: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        (tmp_path / "charts").mkdir()
        with pytest.raises(ValueError, match="escape"):
            ctx.resolve_dashboard_path(Path("../secret.yml"))

    @pytest.mark.windows
    def test_rejects_posix_absolute_path_under_windows_semantics(
        self, ctx: DbtChartsAIContext
    ) -> None:
        """``path.is_absolute()`` only recognizes the running host's own
        convention: ``PureWindowsPath("/etc/passwd").is_absolute()`` is
        False (no drive letter), so on a Windows host this POSIX-absolute
        sandbox check silently skipped, falling through to the weaker
        traversal-only guard below. Drive with ``PureWindowsPath`` directly
        (not a real ``Path``), so the misclassification reproduces on any
        host, including POSIX CI.
        """
        with pytest.raises(ValueError, match="must be relative"):
            ctx.resolve_dashboard_path(PureWindowsPath("/etc/passwd"))  # type: ignore[arg-type]

    def test_defaults_to_boards_subdir_when_no_directory_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        ctx = DbtChartsAIContext(
            project_session=ProjectSession.open(tmp_path, read_only=True)
        )
        result = ctx.resolve_dashboard_path(Path("sales.yml"))
        assert result == boards_dir / "sales.yml"


class TestDbtChartsAIContextHoldsProjectSession:
    """DbtChartsAIContext exposes the ProjectSession + its env."""

    def test_context_exposes_project_session_and_env(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.execute.env import build_exec_env

        registry = build_exec_env(local_project(tmp_path), read_only=True)

        class _StubProjectSession:
            project = local_project(tmp_path)
            env = registry

            def refresh(self) -> None: ...

        ctx = DbtChartsAIContext(project_session=_StubProjectSession())  # type: ignore[arg-type]
        assert ctx.project_session.env is registry

    def test_resolve_dashboard_path_uses_project_root_not_cwd(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """The cwd fallback at ai/context.py:35 is replaced with
        self.project_session.project.root / 'charts'."""
        from dbt_charts.core.execute.env import build_exec_env

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "charts").mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)  # ensure cwd != project_root

        registry = build_exec_env(local_project(project_dir), read_only=True)

        class _StubProjectSession:
            project = local_project(project_dir)
            env = registry

            @property
            def charts_dir(self) -> Path:
                return self.project.charts_dir

            def refresh(self) -> None: ...

        ctx = DbtChartsAIContext(project_session=_StubProjectSession())  # type: ignore[arg-type]
        resolved = ctx.resolve_dashboard_path(Path("sales.yml"))
        assert resolved == project_dir / "charts" / "sales.yml"


class TestResolveWithSymlinkedChartsDir:
    def test_symlinked_charts_dir_does_not_raise_escapes_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """resolve_dashboard_path works when charts/ is a symlink to a real directory.

        Regression: base_dir was self.project_session.charts_dir (not resolved).
        resolved.relative_to(base_dir) rejects every path when charts/ is a symlink
        because the resolved path starts with the symlink *target*, not the link.
        Fix: call .resolve() on base_dir at the containment-check call site.
        """
        real_boards = tmp_path / "real_boards"
        real_boards.mkdir()
        (real_boards / "dashboard.yml").write_text("title: Symlinked\n")

        link_boards = tmp_path / "charts"
        link_boards.symlink_to(real_boards)

        from dbt_charts.core.execute.env import build_exec_env

        registry = build_exec_env(local_project(tmp_path), read_only=True)

        class _StubProjectSession:
            project = local_project(tmp_path)
            env = registry

            @property
            def charts_dir(self) -> Path:
                return self.project.charts_dir

            def refresh(self) -> None: ...

        ctx = DbtChartsAIContext(project_session=_StubProjectSession())  # type: ignore[arg-type]
        # This must not raise "escapes scoped directory"
        result = ctx.resolve_dashboard_path(Path("dashboard.yml"))
        assert result == real_boards / "dashboard.yml"
