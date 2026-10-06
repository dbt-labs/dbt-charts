"""TDD tests for serving boards that live inside an installed dbt package's
``charts/`` directory (``dbt_packages/<pkg>/charts/``), discovered as a
namespaced board root alongside the host ``charts/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.serve.server import create_server

_OVERVIEW_BOARD = """\
title: Zendesk Overview
queries:
  q:
    type: values
    rows:
      - {n: 1}
charts:
  t:
    query: q
    type: table
rows:
  - t
"""

_HOME_BOARD = """\
title: Home
text: welcome
"""


@pytest.fixture
def pkg_project(tmp_path: Path) -> Path:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "home.yml").write_text(_HOME_BOARD)
    pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
    pkg_charts.mkdir(parents=True)
    (pkg_charts / "overview.yml").write_text(_OVERVIEW_BOARD)
    return tmp_path


class TestPackageBoardServes:
    def test_package_board_url_serves(self, pkg_project: Path) -> None:
        with TestClient(
            create_server(FilesystemProject(pkg_project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/pkg_a/overview/")
        assert response.status_code == 200
        assert "Zendesk Overview" in response.text

    def test_package_board_url_serves_without_trailing_slash(
        self, pkg_project: Path
    ) -> None:
        with TestClient(
            create_server(FilesystemProject(pkg_project)),
            raise_server_exceptions=False,
            follow_redirects=True,
        ) as client:
            response = client.get("/pkg_a/overview")
        assert response.status_code == 200
        assert "Zendesk Overview" in response.text

    def test_host_board_still_serves(self, pkg_project: Path) -> None:
        with TestClient(
            create_server(FilesystemProject(pkg_project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/home/")
        assert response.status_code == 200
        assert "Home" in response.text


class TestPackageDirectoryListing:
    def test_package_directory_url_lists_its_boards(self, pkg_project: Path) -> None:
        with TestClient(
            create_server(FilesystemProject(pkg_project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/pkg_a/")
        assert response.status_code == 200
        assert "overview" in response.text

    def test_root_listing_shows_a_package_group(self, pkg_project: Path) -> None:
        with TestClient(
            create_server(FilesystemProject(pkg_project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/")
        assert response.status_code == 200
        assert "pkg_a" in response.text
        assert 'href="/pkg_a/"' in response.text


class TestPackageBoardCollision:
    def test_host_directory_named_after_package_raises_at_startup(
        self, tmp_path: Path
    ) -> None:
        """A host `charts/pkg_a/` directory alongside an installed `pkg_a`
        package is a root-level collision — surfaces as a hard build-time
        error, not silently picking a winner."""
        (tmp_path / "charts" / "pkg_a").mkdir(parents=True)
        (tmp_path / "charts" / "pkg_a" / "x.yml").write_text("title: X\ntext: hi\n")
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text(_OVERVIEW_BOARD)

        app = create_server(FilesystemProject(tmp_path))
        with (
            pytest.raises(ValueError, match="(?i)collision"),
            TestClient(app, raise_server_exceptions=True),
        ):
            pass  # lifespan runs on TestClient __enter__

    def test_host_file_named_after_package_raises_at_startup(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "charts").mkdir(parents=True)
        (tmp_path / "charts" / "pkg_a.yml").write_text("title: X\ntext: hi\n")
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text(_OVERVIEW_BOARD)

        app = create_server(FilesystemProject(tmp_path))
        with (
            pytest.raises(ValueError, match="(?i)collision"),
            TestClient(app, raise_server_exceptions=True),
        ):
            pass


class TestDirectoryCandidates:
    """Unit coverage for _directory_candidates -- root matching now comes from
    the shared board_directory_candidates (project.py), not a bespoke
    partition("/") implementation, so this pins the caller-facing shape
    through that refactor."""

    def test_host_clean_path_resolves_under_charts_dir(self, pkg_project: Path) -> None:
        from dbt_charts.core.serve.server import _directory_candidates

        project = FilesystemProject(pkg_project)
        candidates = _directory_candidates(project.charts_dir, "sub", project)
        assert (project.charts_dir / "sub", "sub") in candidates

    def test_package_namespace_resolves_under_its_own_charts_dir(
        self, pkg_project: Path
    ) -> None:
        from dbt_charts.core.serve.server import _directory_candidates

        project = FilesystemProject(pkg_project)
        candidates = _directory_candidates(project.charts_dir, "pkg_a", project)
        pkg_dir = pkg_project / "dbt_packages" / "pkg_a" / "charts"
        assert (pkg_dir, "pkg_a") in candidates

    def test_root_clean_path_resolves_to_charts_dir_alone(
        self, pkg_project: Path
    ) -> None:
        from dbt_charts.core.serve.server import _directory_candidates

        project = FilesystemProject(pkg_project)
        candidates = _directory_candidates(project.charts_dir, "", project)
        assert candidates == [(project.charts_dir, "")]

    def test_charts_prefixed_path_yields_no_candidates(self, pkg_project: Path) -> None:
        from dbt_charts.core.serve.server import _directory_candidates

        project = FilesystemProject(pkg_project)
        candidates = _directory_candidates(project.charts_dir, "charts/x", project)
        assert candidates == []


class TestAllBoardRootsMtimeSignature:
    """dct serve's reload watch invalidates the alias index when
    _all_board_roots_mtime's signature changes. A max-mtime-only signature
    misses a package add/remove whose own files don't hold the newest mtime
    (e.g. a `dbt deps` reinstall that preserves original file mtimes) -- the
    signature must also encode the SET of package charts/ roots present."""

    def test_adding_an_older_backdated_package_still_changes_the_signature(
        self, tmp_path: Path
    ) -> None:
        import os
        import time

        from dbt_charts.core.serve.server import _all_board_roots_mtime

        (tmp_path / "charts").mkdir()
        home = tmp_path / "charts" / "home.yml"
        home.write_text(_HOME_BOARD)
        newer = time.time()
        os.utime(home, (newer, newer))

        project = FilesystemProject(tmp_path)
        before = _all_board_roots_mtime(project)

        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        pkg_file = pkg_charts / "overview.yml"
        pkg_file.write_text(_OVERVIEW_BOARD)
        older = newer - 1000
        os.utime(pkg_file, (older, older))

        after = _all_board_roots_mtime(project)
        assert after != before

    def test_removing_an_older_package_still_changes_the_signature(
        self, tmp_path: Path
    ) -> None:
        import os
        import time

        from dbt_charts.core.serve.server import _all_board_roots_mtime

        (tmp_path / "charts").mkdir()
        home = tmp_path / "charts" / "home.yml"
        home.write_text(_HOME_BOARD)
        newer = time.time()
        os.utime(home, (newer, newer))

        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        pkg_file = pkg_charts / "overview.yml"
        pkg_file.write_text(_OVERVIEW_BOARD)
        older = newer - 1000
        os.utime(pkg_file, (older, older))

        project = FilesystemProject(tmp_path)
        before = _all_board_roots_mtime(project)

        pkg_file.unlink()
        pkg_charts.rmdir()
        (tmp_path / "dbt_packages" / "pkg_a").rmdir()

        after = _all_board_roots_mtime(project)
        assert after != before


class TestPackageBoardNavigation:
    @pytest.mark.parametrize("url", ["/pkg_a/", "/pkg_a/overview/"])
    def test_up_link_from_the_package_root_routes_to_the_root_listing(
        self, pkg_project: Path, url: str
    ) -> None:
        """A package root's parent directory is the project dir above the
        package, which no URL routes to; the package appears at ``/`` as a
        virtual group, so "up" from it is ``/``."""
        with TestClient(
            create_server(FilesystemProject(pkg_project)),
            raise_server_exceptions=False,
        ) as client:
            page = client.get(url)
            assert page.status_code == 200
            assert "/dbt_packages/" not in page.text
            assert client.get("/").status_code == 200


class TestPackageBoardEditInvalidation:
    def test_editing_an_installed_package_board_changes_the_signature(
        self, tmp_path: Path
    ) -> None:
        """Same package set, newer package files (a `dbt deps` upgrade): the
        signature must move, or the alias index keeps serving stale aliases."""
        import os
        import time

        from dbt_charts.core.serve.server import _all_board_roots_mtime

        (tmp_path / "charts").mkdir()
        home = tmp_path / "charts" / "home.yml"
        home.write_text(_HOME_BOARD)
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        pkg_file = pkg_charts / "overview.yml"
        pkg_file.write_text(_OVERVIEW_BOARD)
        now = time.time()
        os.utime(home, (now, now))
        os.utime(pkg_file, (now - 1000, now - 1000))
        os.utime(pkg_charts, (now - 1000, now - 1000))

        project = FilesystemProject(tmp_path)
        before = _all_board_roots_mtime(project)

        os.utime(pkg_file, (now + 1000, now + 1000))
        os.utime(pkg_charts, (now + 1000, now + 1000))

        assert _all_board_roots_mtime(project) != before


class TestPackageBoardReadOnlyThroughServe:
    def test_package_board_source_serves_as_read_only_text(
        self, pkg_project: Path
    ) -> None:
        """The raw-YAML route (.yaml suffix) serves package board source fine —
        read-only enforcement is a write-path concern (FilesystemProject),
        not a serve-route concern."""
        with TestClient(
            create_server(FilesystemProject(pkg_project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/pkg_a/overview.yaml")
        assert response.status_code == 200
        assert "Zendesk Overview" in response.text
