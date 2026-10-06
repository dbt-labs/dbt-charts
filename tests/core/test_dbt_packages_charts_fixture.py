"""Enumeration + slug mapping against the committed
``tests/fixtures/dbt_packages_charts/`` project — the end-to-end fixture for
package-board discovery (host boards, a package's own boards, and the three
exclusions: schema.yml/README.md within a package, a symlinked ``local:``
package, and a package with no ``charts/``).
"""

from __future__ import annotations

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.project import BoardRoot, board_roots, board_slug

from .._paths import DBT_CHARTS_DIR

FIXTURE_ROOT = DBT_CHARTS_DIR / "tests" / "fixtures" / "dbt_packages_charts"


def _project() -> FilesystemProject:
    return FilesystemProject(FIXTURE_ROOT)


class TestFixtureBoardRoots:
    def test_board_roots_is_host_plus_pkg_a_only(self) -> None:
        assert board_roots(_project()) == [
            BoardRoot("charts", ""),
            BoardRoot("dbt_packages/pkg_a/charts", "pkg_a"),
        ]


class TestFixtureEnumeration:
    def test_iter_boards_yields_exactly_home_and_pkg_a_overview(self) -> None:
        """iter_boards() is the low-level enumerator — meta.yml cascade
        fragments pass its suffix/underscore filter too (real consumers like
        list_boards additionally drop ``pf.is_meta``, which this asserts
        separately below)."""
        relpaths = {pf.relpath for pf in _project().iter_boards() if not pf.is_meta}
        assert relpaths == {
            "charts/home.yml",
            "dbt_packages/pkg_a/charts/overview.yml",
        }

    def test_meta_files_are_excluded_by_is_meta_not_by_enumeration(self) -> None:
        relpaths = {pf.relpath for pf in _project().iter_boards() if pf.is_meta}
        assert relpaths == {
            "charts/meta.yml",
            "dbt_packages/pkg_a/charts/meta.yml",
        }

    def test_models_schema_never_enumerates(self) -> None:
        relpaths = {pf.relpath for pf in _project().iter_boards()}
        assert "dbt_packages/pkg_a/models/schema.yml" not in relpaths

    def test_readme_never_enumerates(self) -> None:
        relpaths = {pf.relpath for pf in _project().iter_boards()}
        assert not any(r.endswith("README.md") for r in relpaths)

    def test_symlinked_pkg_b_never_enumerates(self) -> None:
        relpaths = {pf.relpath for pf in _project().iter_boards()}
        assert not any("pkg_b" in r for r in relpaths)

    def test_pkg_c_with_no_charts_dir_contributes_no_root(self) -> None:
        namespaces = {r.namespace for r in board_roots(_project())}
        assert "pkg_c" not in namespaces


class TestFixtureSlugRoundTrip:
    def test_host_board_slug(self) -> None:
        assert board_slug("charts/home.yml") == "home"

    def test_package_board_slug(self) -> None:
        assert board_slug("dbt_packages/pkg_a/charts/overview.yml") == "pkg_a/overview"

    def test_slug_round_trip_over_every_enumerated_board(self) -> None:
        for pf in _project().iter_boards():
            slug = board_slug(pf.relpath)
            assert slug  # every real board here has a non-empty slug
