"""TDD tests for AliasIndex.build routed through InMemoryProject.

Exercises the four invariants from the task:
  (a) In-memory boards' aliases are indexed.
  (b) A non-list aliases: value (e.g. a dict) is skipped, not raised.
  (c) A non-string item in aliases: list is skipped, not raised.

These tests must run with zero filesystem access — all board content lives in
the InMemoryProject dict.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.core.project import Project
from dbt_charts.core.serve.alias_index import AliasIndex


def _build(
    files: dict[str, str],
    in_memory_project: Callable[[Path, dict[str, str]], Project],
) -> AliasIndex:
    project = in_memory_project(Path("/fake"), files)
    return AliasIndex.build(project)


class TestAliasIndexInMemoryBoards:
    def test_in_memory_board_aliases_indexed(
        self, in_memory_project: Callable[[Path, dict[str, str]], Project]
    ) -> None:
        """An in-memory board with aliases: list is indexed correctly."""
        index = _build(
            {
                "charts/sales.yml": (
                    "title: Sales\n"
                    "aliases:\n  - /old-sales/\n"
                    "queries: {q: {type: values, rows: [{n: 1}]}}\n"
                    "charts: {t: {query: q, type: table}}\n"
                    "rows: [t]\n"
                ),
            },
            in_memory_project,
        )
        assert index.lookup("/old-sales/") == "/sales/"

    def test_non_list_aliases_skipped_not_raised(
        self, in_memory_project: Callable[[Path, dict[str, str]], Project]
    ) -> None:
        """A board with aliases: dict (e.g. a color-alias palette) is silently skipped.

        A palette YAML accidentally placed under charts/ must not crash the alias index.
        """
        index = _build(
            {
                "charts/palette.yml": "red: '#ff0000'\nblue: '#0000ff'\n",
                "charts/sales.yml": (
                    "title: Sales\n"
                    "aliases:\n  - /old-sales/\n"
                    "queries: {q: {type: values, rows: [{n: 1}]}}\n"
                    "charts: {t: {query: q, type: table}}\n"
                    "rows: [t]\n"
                ),
            },
            in_memory_project,
        )
        assert index.lookup("/old-sales/") == "/sales/"

    def test_non_string_alias_item_skipped_not_raised(
        self, in_memory_project: Callable[[Path, dict[str, str]], Project]
    ) -> None:
        """A board with a non-string item in aliases: list is silently skipped (not raised).

        Consistent with non-list skipping — shape errors surface at compile/validate.
        """
        # aliases: list contains an integer, not a string.
        index = _build(
            {
                "charts/bad.yml": "title: Bad\naliases:\n  - 42\n",
                "charts/sales.yml": (
                    "title: Sales\n"
                    "aliases:\n  - /old-sales/\n"
                    "queries: {q: {type: values, rows: [{n: 1}]}}\n"
                    "charts: {t: {query: q, type: table}}\n"
                    "rows: [t]\n"
                ),
            },
            in_memory_project,
        )
        # bad board's alias (integer) must not reach the index.
        # The real board alias IS indexed.
        assert index.lookup("/old-sales/") == "/sales/"


class TestAliasIndexIndexBoardCanonicalUrl:
    """An index board's canonical URL collapses the `index` leaf (a display
    concern), even though board_slug -- the file-identity slug -- keeps it."""

    def test_nested_index_board_alias_redirects_to_collapsed_url(
        self, in_memory_project: Callable[[Path, dict[str, str]], Project]
    ) -> None:
        index = _build(
            {
                "charts/reports/index.yml": (
                    "title: Reports\n"
                    "aliases:\n  - /old-reports/\n"
                    "queries: {q: {type: values, rows: [{n: 1}]}}\n"
                    "charts: {t: {query: q, type: table}}\n"
                    "rows: [t]\n"
                ),
            },
            in_memory_project,
        )
        assert index.lookup("/old-reports/") == "/reports/"


class TestAliasIndexCollisionDetectionViaProject:
    """AliasIndex.build's collision check must see real boards served from a
    non-filesystem Project store, not just ones written to disk."""

    def test_alias_colliding_with_real_board_in_project_store_is_an_error(
        self, in_memory_project: Callable[[Path, dict[str, str]], Project]
    ) -> None:
        """The shadow check sees a board that lives only in the Project store."""
        index = _build(
            {
                "charts/sales.yml": (
                    "title: Sales\n"
                    "aliases:\n  - /reports/\n"
                    "queries: {q: {type: values, rows: [{n: 1}]}}\n"
                    "charts: {t: {query: q, type: table}}\n"
                    "rows: [t]\n"
                ),
                "charts/reports.yml": (
                    "title: Reports\n"
                    "queries: {q: {type: values, rows: [{n: 1}]}}\n"
                    "charts: {t: {query: q, type: table}}\n"
                    "rows: [t]\n"
                ),
            },
            in_memory_project,
        )
        (error,) = index.errors["charts/sales.yml"]
        assert "shadows" in error.message
        assert index.lookup("/reports/") is None


class TestAliasIndexEscapingAliasDoesNotCrashBuild:
    """An alias with enough ../ to resolve outside the project root must be
    treated as "not a collision" (matching the pre-seam Path.exists()==False
    behavior for an out-of-root path), not crash AliasIndex.build."""

    def test_escaping_alias_does_not_raise(
        self, in_memory_project: Callable[[Path, dict[str, str]], Project]
    ) -> None:
        """normalize_alias_url does not strip '..' segments, so an authored alias
        like '/../../../../etc/evil/' reaches _file_url_exists unchanged.

        Failing state: _file_url_exists calls project.path_for_fspath(...)
        with no containment guard; the candidate resolves outside
        project.root, so path_for_fspath raises ValueError uncaught —
        crashing AliasIndex.build (server boot / every request refresh).

        Passing state: the ValueError is caught and treated as "no
        collision", matching the old Path.exists()==False-on-escape
        semantics — the alias is indexed normally.
        """
        index = _build(
            {
                "charts/sales.yml": (
                    "title: Sales\n"
                    "aliases:\n  - /../../../../etc/evil/\n"
                    "queries: {q: {type: values, rows: [{n: 1}]}}\n"
                    "charts: {t: {query: q, type: table}}\n"
                    "rows: [t]\n"
                ),
            },
            in_memory_project,
        )
        assert index.lookup("/../../../../etc/evil/") == "/sales/"
