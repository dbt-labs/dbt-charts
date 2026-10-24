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


class TestHostBoardShadowsPackageBoard:
    def test_host_board_serves_and_package_sibling_still_appears(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "charts" / "pkg_a").mkdir(parents=True)
        (tmp_path / "charts" / "pkg_a" / "overview.yml").write_text(
            "title: Host Overview\ntext: from host\n"
        )
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text(_OVERVIEW_BOARD)
        (pkg_charts / "other.yml").write_text("title: Pkg Other\ntext: from pkg\n")

        app = create_server(FilesystemProject(tmp_path))
        with TestClient(app, raise_server_exceptions=True) as client:
            overview = client.get("/pkg_a/overview/")
            other = client.get("/pkg_a/other/")
        assert overview.status_code == 200
        assert "from host" in overview.text
        assert other.status_code == 200
        assert "from pkg" in other.text


class TestResolveDirectoryUrl:
    """Unit coverage for resolve_directory_url's package-root matching, which
    comes from the shared board_directory_candidates (project.py)."""

    def test_host_clean_path_resolves_under_charts_dir(self, pkg_project: Path) -> None:
        from dbt_charts.core.project import resolve_directory_url

        (pkg_project / "charts" / "sub").mkdir()
        project = FilesystemProject(pkg_project)
        resolved = resolve_directory_url(project, "sub")
        assert isinstance(resolved, list)
        assert [d.relpath for d in resolved] == ["charts/sub"]

    def test_package_namespace_resolves_under_its_own_charts_dir(
        self, pkg_project: Path
    ) -> None:
        from dbt_charts.core.project import resolve_directory_url

        project = FilesystemProject(pkg_project)
        resolved = resolve_directory_url(project, "pkg_a")
        assert isinstance(resolved, list)
        assert [d.relpath for d in resolved] == ["dbt_packages/pkg_a/charts"]

    def test_root_clean_path_resolves_to_charts_dir_alone(
        self, pkg_project: Path
    ) -> None:
        from dbt_charts.core.project import resolve_directory_url

        project = FilesystemProject(pkg_project)
        resolved = resolve_directory_url(project, "")
        assert isinstance(resolved, list)
        assert [d.relpath for d in resolved] == ["charts"]

    def test_charts_prefixed_path_yields_no_candidates(self, pkg_project: Path) -> None:
        from dbt_charts.core.project import resolve_directory_url

        project = FilesystemProject(pkg_project)
        assert resolve_directory_url(project, "charts/x") == []


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


class TestHostDirectoryMergesWithPackage:
    @staticmethod
    def _client(tmp_path: Path, *, host_ns_file: bool = False) -> TestClient:
        (tmp_path / "charts" / "pkg_a").mkdir(parents=True)
        (tmp_path / "charts" / "pkg_a" / "overview.yml").write_text(
            "title: Host\ntext: from host\n"
        )
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text(_OVERVIEW_BOARD)
        (pkg_charts / "other.yml").write_text("title: Pkg Other\ntext: x\n")
        (pkg_charts / "index.yml").write_text("title: Pkg Index\ntext: pkg index\n")
        if host_ns_file:
            (tmp_path / "charts" / "pkg_a.yml").write_text(
                "title: Host Ns\ntext: host namespace board\n"
            )
        return TestClient(
            create_server(FilesystemProject(tmp_path)), raise_server_exceptions=True
        )

    def test_package_index_renders_when_host_dir_has_none(self, tmp_path: Path) -> None:
        with self._client(tmp_path) as client:
            response = client.get("/pkg_a/")
        assert response.status_code == 200
        assert "pkg index" in response.text

    def test_directory_listing_merges_host_and_package_per_slug(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "charts" / "pkg_a").mkdir(parents=True)
        (tmp_path / "charts" / "pkg_a" / "overview.yml").write_text("title: H\n")
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text(_OVERVIEW_BOARD)
        (pkg_charts / "other.yml").write_text("title: O\ntext: x\n")
        client = TestClient(create_server(FilesystemProject(tmp_path)))
        with client:
            response = client.get("/pkg_a/")
            root = client.get("/")
        assert response.text.count('<a href="/pkg_a/overview">') == 1
        assert '<a href="/pkg_a/other">' in response.text
        assert root.text.count('<a href="/pkg_a/">') == 1

    def test_host_namespace_board_shadows_package_index(self, tmp_path: Path) -> None:
        with self._client(tmp_path, host_ns_file=True) as client:
            response = client.get("/pkg_a/")
        assert response.status_code == 200
        assert "host namespace board" in response.text
        assert "pkg index" not in response.text
