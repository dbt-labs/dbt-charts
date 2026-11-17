"""TDD tests for board aliases and URL resolution index.

Tests that:
- An alias URL 30x-redirects to the board's canonical file-path URL
- Query string is preserved in the redirect
- An alias colliding with a real file path fails only the board declaring it
- Two boards claiming the same alias fail both boards, and the rest still serve
- URL normalization (trailing slash, percent-decode, absolute)
- Aliasing a generated system-view route redirects (D-21 override)
- Unknown URL still 404s
- The alias field is parsed from YAML front-matter by the compiler
- The compiled Board carries aliases
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.aliases import normalize_alias_url
from dbt_charts.core.serve.alias_index import AliasIndex
from dbt_charts.core.serve.server import create_server

# ---------------------------------------------------------------------------
# Minimal board YAML
# ---------------------------------------------------------------------------

_SALES_BOARD = """\
title: Sales Dashboard
aliases:
  - /reports/old-sales/
  - /legacy/sales/
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

_OTHER_BOARD = """\
title: Other Dashboard
queries:
  q:
    type: values
    rows:
      - {n: 2}
charts:
  t:
    query: q
    type: table
rows:
  - t
"""

_DATA_ALIAS_BOARD = """\
title: Data Override
aliases:
  - /data/warehouse/analytics/sales/
queries:
  q:
    type: values
    rows:
      - {n: 3}
charts:
  t:
    query: q
    type: table
rows:
  - t
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def alias_project(tmp_path: Path) -> Path:
    """Project with a board that has aliases."""
    boards = tmp_path / "charts"
    boards.mkdir()
    (boards / "sales.yml").write_text(_SALES_BOARD)
    (boards / "other.yml").write_text(_OTHER_BOARD)
    return tmp_path


@pytest.fixture
def data_alias_project(tmp_path: Path) -> Path:
    """Project where a board aliases a system-view-style route."""
    boards = tmp_path / "charts"
    boards.mkdir()
    (boards / "sales.yml").write_text(_DATA_ALIAS_BOARD)
    return tmp_path


@pytest.fixture
def collision_project(tmp_path: Path) -> Path:
    """Project where an alias collides with a real file path."""
    boards = tmp_path / "charts"
    boards.mkdir()
    # sales.yml has an alias pointing at /other/ — but charts/other.yml exists
    collision_board = """\
title: Collision Board
aliases:
  - /other/
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
    (boards / "sales.yml").write_text(collision_board)
    (boards / "other.yml").write_text(_OTHER_BOARD)
    return tmp_path


@pytest.fixture
def duplicate_alias_project(tmp_path: Path) -> Path:
    """Project where two boards claim the same alias."""
    boards = tmp_path / "charts"
    boards.mkdir()
    board_a = """\
title: Board A
aliases:
  - /shared-alias/
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
    board_b = """\
title: Board B
aliases:
  - /shared-alias/
queries:
  q:
    type: values
    rows:
      - {n: 2}
charts:
  t:
    query: q
    type: table
rows:
  - t
"""
    (boards / "a.yml").write_text(board_a)
    (boards / "b.yml").write_text(board_b)
    return tmp_path


# ---------------------------------------------------------------------------
# Tests: alias → redirect
# ---------------------------------------------------------------------------


class TestAliasRedirect:
    def test_alias_url_redirects_to_canonical(self, alias_project: Path) -> None:
        """Requesting an alias URL returns a 30x redirect to the file-path URL."""
        with TestClient(
            create_server(FilesystemProject(alias_project)),
            raise_server_exceptions=False,
            follow_redirects=False,
        ) as client:
            response = client.get("/reports/old-sales/")
        assert response.status_code in (302, 307), (
            f"Expected 30x redirect, got {response.status_code}. "
            f"Body: {response.text[:400]}"
        )
        location = response.headers.get("location", "")
        assert "sales" in location, (
            f"Redirect should point at /sales/ or similar, got: {location!r}"
        )
        # Must not serve the board at the alias URL
        assert "Sales Dashboard" not in response.text

    def test_alias_redirect_preserves_query_string(self, alias_project: Path) -> None:
        """Query string is forwarded to the canonical URL in the redirect."""
        with TestClient(
            create_server(FilesystemProject(alias_project)),
            raise_server_exceptions=False,
            follow_redirects=False,
        ) as client:
            response = client.get("/reports/old-sales/?region=West&year=2024")
        assert response.status_code in (302, 307)
        location = response.headers.get("location", "")
        assert "region=West" in location, (
            f"Query string must be forwarded. location={location!r}"
        )
        assert "year=2024" in location

    def test_second_alias_also_redirects(self, alias_project: Path) -> None:
        """/legacy/sales/ (second alias) also redirects to /sales/."""
        with TestClient(
            create_server(FilesystemProject(alias_project)),
            raise_server_exceptions=False,
            follow_redirects=False,
        ) as client:
            response = client.get("/legacy/sales/")
        assert response.status_code in (302, 307)

    def test_canonical_url_still_serves(self, alias_project: Path) -> None:
        """The board's file-path URL still serves 200 normally."""
        with TestClient(
            create_server(FilesystemProject(alias_project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/sales/")
        assert response.status_code == 200
        assert "Sales Dashboard" in response.text

    def test_unknown_url_still_404s(self, alias_project: Path) -> None:
        """An unknown URL that has no alias and no file still 404s."""
        with TestClient(
            create_server(FilesystemProject(alias_project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/nonexistent-thing/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Tests: data / system-view alias override
# ---------------------------------------------------------------------------


class TestSystemViewAliasOverride:
    def test_alias_to_data_route_redirects(self, data_alias_project: Path) -> None:
        """Aliasing a data-style route redirects to the authored board (D-21 override)."""
        with TestClient(
            create_server(FilesystemProject(data_alias_project)),
            raise_server_exceptions=False,
            follow_redirects=False,
        ) as client:
            response = client.get("/data/warehouse/analytics/sales/")
        assert response.status_code in (
            302,
            307,
        ), (
            f"Expected redirect when board aliases data route, got {response.status_code}"
        )
        location = response.headers.get("location", "")
        assert "sales" in location


# ---------------------------------------------------------------------------
# Tests: collision detection (build-time errors)
# ---------------------------------------------------------------------------


class TestCollisionDetection:
    def test_alias_collision_with_real_file_fails_only_that_board(
        self, collision_project: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """An alias shadowing a real board is that board's error, not a redirect."""
        index = AliasIndex.build(local_project(collision_project))

        assert index.lookup("/other/") is None
        assert [e.code for e in index.errors["charts/sales.yml"]] == ["ERR-BOARD-ALIAS"]
        assert "charts/other.yml" not in index.errors

    def test_duplicate_alias_fails_both_boards_and_redirects_nowhere(
        self,
        duplicate_alias_project: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Two boards claiming one alias: each names the other; neither wins."""
        index = AliasIndex.build(local_project(duplicate_alias_project))

        assert index.lookup("/shared-alias/") is None
        (a_error,) = index.errors["charts/a.yml"]
        (b_error,) = index.errors["charts/b.yml"]
        assert a_error.code == b_error.code == "ERR-BOARD-ALIAS"
        assert "charts/b.yml" in a_error.message
        assert "charts/a.yml" in b_error.message


# ---------------------------------------------------------------------------
# Tests: URL normalization
# ---------------------------------------------------------------------------


class TestUrlNormalization:
    def test_percent_decoded_alias_matches(self, tmp_path: Path) -> None:
        """An alias with a percent-encoded character is decoded before matching."""
        boards = tmp_path / "charts"
        boards.mkdir()
        board_yaml = """\
title: Spaces Dashboard
aliases:
  - /my%20sales/
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
        (boards / "sales.yml").write_text(board_yaml)
        with TestClient(
            create_server(FilesystemProject(tmp_path)),
            raise_server_exceptions=False,
            follow_redirects=False,
        ) as client:
            response = client.get("/my%20sales/")
        assert response.status_code in (302, 307), (
            f"Percent-encoded alias should match decoded request. "
            f"Got {response.status_code}"
        )

    def test_alias_without_leading_slash_is_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """An alias without a leading slash is an error at index build time."""
        from dbt_charts.core.serve.alias_index import AliasIndex

        boards = tmp_path / "charts"
        boards.mkdir()
        bad_board = """\
title: Bad Alias
aliases:
  - reports/missing-slash/
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
        (boards / "bad.yml").write_text(bad_board)
        index = AliasIndex.build(local_project(tmp_path))
        (error,) = index.errors["charts/bad.yml"]
        assert error.code == "ERR-BOARD-ALIAS"
        assert "leading /" in error.message

    def test_trailing_slash_normalized(self, tmp_path: Path) -> None:
        """Alias without trailing slash and request with trailing slash both match."""
        boards = tmp_path / "charts"
        boards.mkdir()
        board_yaml = """\
title: No Trailing Slash
aliases:
  - /old-reports
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
        (boards / "reports.yml").write_text(board_yaml)
        with TestClient(
            create_server(FilesystemProject(tmp_path)),
            raise_server_exceptions=False,
            follow_redirects=False,
        ) as client:
            response = client.get("/old-reports/")
        assert response.status_code in (302, 307), (
            "Request with trailing slash should match alias without trailing slash "
            f"(both normalized to trailing slash). Got {response.status_code}"
        )

    def test_self_alias_does_not_create_redirect_loop(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A board aliasing its own canonical URL is a no-op, not a self-redirect.

        Regression: mapping canonical→canonical would 302 the URL to itself,
        producing an infinite redirect loop. The index must not record it.
        """
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "sales.yml").write_text("title: Sales\naliases:\n  - /sales/\n")
        index = AliasIndex.build(local_project(tmp_path))
        assert index.lookup("/sales/") is None, (
            "A board aliasing its own canonical URL must not produce a redirect "
            "(self-redirect loop)."
        )

    def test_markdown_board_frontmatter_alias(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A .md board declares aliases under its frontmatter ``board:`` block.

        Not at the top level: ``markdown.py``'s contract is that board config
        lives exclusively under ``board:`` and every other frontmatter key is
        document metadata. ``aliases`` is a board field, so reading it anywhere
        else would let a claim resolve here and nowhere else that compiles the
        same bytes.
        """
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "notes.md").write_text(
            "---\n"
            "board:\n"
            "  aliases:\n    - /old-notes/\n"
            "  queries:\n    q: {type: values, rows: [{n: 1}]}\n"
            "---\n# Notes\n"
        )
        index = AliasIndex.build(local_project(tmp_path))
        assert index.lookup("/old-notes/") == "/notes/"

    def test_markdown_top_level_frontmatter_alias_is_document_metadata(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A top-level ``aliases:`` key is document metadata, never a claim.

        A task file or research note may carry an ``aliases:`` key of its own;
        interpreting it as a route claim would have such a file quietly seize a
        URL it knows nothing about.
        """
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "notes.md").write_text(
            "---\n"
            "aliases:\n  - /old-notes/\n"
            "board:\n"
            "  queries:\n    q: {type: values, rows: [{n: 1}]}\n"
            "---\n# Notes\n"
        )
        index = AliasIndex.build(local_project(tmp_path))
        assert index.lookup("/old-notes/") is None

    def test_folder_index_board_alias(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A folder index.* board can declare aliases; its URL is the folder (D-20)."""
        boards = tmp_path / "charts" / "reports"
        boards.mkdir(parents=True)
        (boards / "index.yml").write_text(
            "title: Reports\n"
            "aliases:\n  - /old-reports/\n"
            "queries: {q: {type: values, rows: [{n: 1}]}}\n"
            "charts: {t: {query: q, type: table}}\n"
            "rows: [t]\n"
        )
        index = AliasIndex.build(local_project(tmp_path))
        assert index.lookup("/old-reports/") == "/reports/"


# ---------------------------------------------------------------------------
# Tests: compiler model (aliases threaded through compile pipeline)
# ---------------------------------------------------------------------------


class TestCompiledBoardAliases:
    def test_aliases_present_on_compiled_board(self) -> None:
        """Aliases authored in YAML appear on the compiled Board object."""
        from dbt_charts.core.compile import compile

        yaml_content = """\
title: Aliased Board
aliases:
  - /old-path/
  - /another-old-path/
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
        result = compile(yaml_content)
        assert result.board is not None
        assert result.board.aliases == ["/old-path/", "/another-old-path/"]

    def test_board_without_aliases_has_empty_list(self) -> None:
        """A board with no aliases: field has an empty list on the compiled Board."""
        from dbt_charts.core.compile import compile

        yaml_content = """\
title: Plain Board
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
        result = compile(yaml_content)
        assert result.board is not None
        assert result.board.aliases == []


# ---------------------------------------------------------------------------
# Tests: case sensitivity (D-19: alias matching is case-sensitive)
# ---------------------------------------------------------------------------


class TestCaseSensitivity:
    def test_alias_case_sensitive_no_match(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Alias /Sales/ must NOT match request /sales/ (case-sensitive per D-19)."""
        from dbt_charts.core.serve.alias_index import AliasIndex

        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "reports.yml").write_text(
            """\
title: Case Test
aliases:
  - /Sales/
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
        )
        index = AliasIndex.build(local_project(tmp_path))
        # The alias /Sales/ must match exactly — /sales/ must NOT redirect.
        assert index.lookup("/Sales/") is not None, "Exact-case alias must match"
        assert index.lookup("/sales/") is None, (
            "Wrong-case request must not match alias"
        )


# ---------------------------------------------------------------------------
# Tests: project-root layout (boards at project root, no charts/ subdir)
# ---------------------------------------------------------------------------


class TestParameterizedAlias:
    """Capture-segment aliases: /items/<name> → redirect /detail/?name=<captured>."""

    _DETAIL_BOARD = """\
title: Detail
aliases:
  - /items/<name>
variables:
  name: { input: text }
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

    @pytest.fixture
    def param_project(self, tmp_path: Path) -> Path:
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "detail.yml").write_text(self._DETAIL_BOARD)
        return tmp_path

    def test_build_matches_pattern_and_captures_param(
        self, param_project: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        index = AliasIndex.build(local_project(param_project))
        matches = index.match_patterns("/items/m2/")
        assert matches == [("/detail/", {"name": "m2"})], (
            "Pattern alias /items/<name> should match /items/m2/ and capture name"
        )

    def test_pattern_alias_redirects_with_captured_param(
        self, param_project: Path
    ) -> None:
        with TestClient(
            create_server(FilesystemProject(param_project)),
            raise_server_exceptions=False,
            follow_redirects=False,
        ) as client:
            response = client.get("/items/m2")
        assert response.status_code == 302, (
            f"Capture alias should 302-redirect, got {response.status_code}. "
            f"Body: {response.text[:300]}"
        )
        location = response.headers.get("location", "")
        assert "/detail" in location and "name=m2" in location, (
            f"Redirect should target the board URL with the captured query param, "
            f"got: {location!r}"
        )

    def test_pattern_alias_capture_wins_over_a_colliding_query_param(
        self, param_project: Path
    ) -> None:
        """`/items/m2?name=m9` redirects with `name=m2` once — never both.

        The query string is carried as pairs now (a repeated key is a list
        value), so a colliding param must be dropped rather than merged, or the
        next request would fold `m9` and `m2` into one list for a scalar.
        """
        with TestClient(
            create_server(FilesystemProject(param_project)),
            raise_server_exceptions=False,
            follow_redirects=False,
        ) as client:
            response = client.get("/items/m2?name=m9&foo=bar")
        location = response.headers.get("location", "")
        assert "name=m2" in location and "m9" not in location, location
        assert location.count("name=") == 1, location
        assert "foo=bar" in location, location

    def test_pattern_alias_merges_extra_query_string(self, param_project: Path) -> None:
        with TestClient(
            create_server(FilesystemProject(param_project)),
            raise_server_exceptions=False,
            follow_redirects=False,
        ) as client:
            response = client.get("/items/m2?foo=bar")
        location = response.headers.get("location", "")
        assert "name=m2" in location and "foo=bar" in location, (
            f"Existing query params must be preserved alongside the capture, "
            f"got: {location!r}"
        )

    def test_real_file_beats_pattern_alias(self, param_project: Path) -> None:
        """A real board at the captured path wins over the pattern (no redirect)."""
        (param_project / "charts" / "items").mkdir()
        (param_project / "charts" / "items" / "special.yml").write_text(_OTHER_BOARD)
        with TestClient(
            create_server(FilesystemProject(param_project)),
            raise_server_exceptions=False,
            follow_redirects=False,
        ) as client:
            response = client.get("/items/special/")
        assert response.status_code == 200, (
            f"A real board must take precedence over a pattern alias, "
            f"got {response.status_code}"
        )
        assert "Other Dashboard" in response.text

    def test_ambiguous_patterns_fail_both_boards(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Two patterns with the same shape (/items/<name> vs /items/<id>) error."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "a.yml").write_text(
            "title: A\naliases:\n  - /items/<name>\n"
            "queries: {q: {type: values, rows: [{n: 1}]}}\n"
            "charts: {t: {query: q, type: table}}\nrows: [t]\n"
        )
        (boards / "b.yml").write_text(
            "title: B\naliases:\n  - /items/<id>\n"
            "queries: {q: {type: values, rows: [{n: 2}]}}\n"
            "charts: {t: {query: q, type: table}}\nrows: [t]\n"
        )
        index = AliasIndex.build(local_project(tmp_path))
        assert index.match_patterns("/items/x/") == []
        assert set(index.errors) == {"charts/a.yml", "charts/b.yml"}


# ---------------------------------------------------------------------------
# Tests: a bad alias fails its own boards, never the server
# ---------------------------------------------------------------------------


class TestServerAliasErrors:
    def test_duplicate_alias_fails_only_the_claiming_boards(
        self, duplicate_alias_project: Path
    ) -> None:
        (duplicate_alias_project / "charts" / "other.yml").write_text(_OTHER_BOARD)
        with TestClient(
            create_server(FilesystemProject(duplicate_alias_project)),
            raise_server_exceptions=True,
            follow_redirects=False,
        ) as client:
            other = client.get("/other/")
            a = client.get("/a/")
            b = client.get("/b/")
            shared = client.get("/shared-alias/")

        assert other.status_code == 200
        assert "Other Dashboard" in other.text
        for page in (a, b):
            assert page.status_code == 422
            assert "ERR-BOARD-ALIAS" in page.text
        assert shared.status_code == 404

    def test_alias_shadowing_a_board_fails_only_its_own_board(
        self, collision_project: Path
    ) -> None:
        with TestClient(
            create_server(FilesystemProject(collision_project)),
            raise_server_exceptions=True,
            follow_redirects=False,
        ) as client:
            other = client.get("/other/")
            sales = client.get("/sales/")

        assert other.status_code == 200
        assert sales.status_code == 422
        assert "ERR-BOARD-ALIAS" in sales.text


def test_normalize_alias_url_is_percent_decoded_and_trailing_slash_canonical() -> None:
    assert normalize_alias_url("/data/db/main/tickets/detail") == (
        "/data/db/main/tickets/detail/"
    )
    assert normalize_alias_url("/rep%20orts/") == "/rep orts/"


def test_normalize_alias_url_rejects_a_relative_alias() -> None:
    with pytest.raises(ValueError, match="not absolute"):
        normalize_alias_url("data/db/main/tickets/detail/")
