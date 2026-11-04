"""``boards_dir:`` in ``dbt_charts.yml`` — the project folder that holds boards.

Boards keep one canonical identity (``charts/<rest>``) whatever folder stores
them; a host translates at its storage seam.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pydantic
import pytest
from fastapi.testclient import TestClient

from dbt_charts.agent_api._paths import resolve_board_or_error
from dbt_charts.agent_api.validate import validate_paths
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.compile.config import read_boards_dir
from dbt_charts.core.compile.parse.meta import find_meta_files
from dbt_charts.core.compile.sizing import board_container_width
from dbt_charts.core.diagnostics.diagnostic import Diagnostic
from dbt_charts.core.project import (
    ProjectDirectory,
    ProjectPath,
    board_slug,
    canonical_listing,
    canonical_relpath,
    resolve_directory_url,
    stored_relpath,
)
from dbt_charts.core.serve.alias_index import board_file_candidates, url_for_file
from dbt_charts.core.serve.server import create_server


class TestReadBoardsDir:
    def test_defaults_to_charts(self) -> None:
        assert read_boards_dir({}, "dbt_charts.yml") == "charts"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(".", "."), ("ai_notes", "ai_notes"), ("./docs//notes/", "docs/notes")],
    )
    def test_accepts_a_relative_folder_normalized(
        self, value: str, expected: str
    ) -> None:
        assert read_boards_dir({"boards_dir": value}, "dbt_charts.yml") == expected

    @pytest.mark.parametrize(
        "value", ["", "/abs", "..", "../up", "a/../..", "C:\\x", "a\\b", 3]
    )
    def test_rejects_an_unusable_value(self, value: Any) -> None:
        with pytest.raises(pydantic.ValidationError, match="boards_dir"):
            read_boards_dir({"boards_dir": value}, "dbt_charts.yml")


class TestTranslation:
    """The two pure maps every host shares."""

    def test_default_is_identity(self) -> None:
        for relpath in ("charts", "charts/a.yml", "dbt_charts.yml", "data/x.csv"):
            assert stored_relpath(relpath, "charts") == relpath
            assert canonical_relpath(relpath, "charts") == relpath

    @pytest.mark.parametrize(
        ("boards_dir", "canonical", "stored"),
        [
            (".", "charts", "."),
            (".", "charts/a.md", "a.md"),
            (".", "dbt_charts.yml", "dbt_charts.yml"),
            ("notes", "charts/sub/a.md", "notes/sub/a.md"),
            ("notes", "data/x.csv", "data/x.csv"),
        ],
    )
    def test_canonical_to_stored(
        self, boards_dir: str, canonical: str, stored: str
    ) -> None:
        assert stored_relpath(canonical, boards_dir) == stored

    @pytest.mark.parametrize(
        ("boards_dir", "stored", "canonical"),
        [
            (".", "a.md", "charts/a.md"),
            (".", "sub/b.yml", "charts/sub/b.yml"),
            (".", "dbt_charts.yml", "dbt_charts.yml"),
            (".", "dbt_project.yml", "dbt_project.yml"),
            (".", "profiles.yml", "profiles.yml"),
            (".", "sub/profiles.yml", "charts/sub/profiles.yml"),
            ("notes", "notes/a.md", "charts/a.md"),
            ("notes", "data/x.csv", "data/x.csv"),
            # A real charts/ folder is unreachable once boards live elsewhere.
            ("notes", "charts/a.yml", None),
        ],
    )
    def test_stored_to_canonical(
        self, boards_dir: str, stored: str, canonical: str | None
    ) -> None:
        assert canonical_relpath(stored, boards_dir) == canonical

    @pytest.mark.parametrize(
        "name", ["profiles.yml", "dbt_charts.yml", "dbt_project.yml"]
    )
    def test_a_project_root_file_is_never_a_board(self, name: str) -> None:
        with pytest.raises(FileNotFoundError, match=name):
            stored_relpath(f"charts/{name}", ".")
        assert stored_relpath(f"charts/{name}", "notes") == f"notes/{name}"
        assert stored_relpath(name, ".") == name

    @pytest.mark.parametrize(
        ("boards_dir", "alias"),
        [(".", "a.md"), (".", "sub/b.yml"), ("notes", "notes/sub/b.yaml")],
    )
    def test_a_board_has_one_name(self, boards_dir: str, alias: str) -> None:
        with pytest.raises(FileNotFoundError, match="charts/"):
            stored_relpath(alias, boards_dir)

    @pytest.mark.parametrize(
        ("boards_dir", "relpath"),
        [
            (".", "data/rows.csv"),
            ("notes", "notes/pic.png"),
            ("notes", "elsewhere/a.md"),
            (".", "dbt_packages/pkg/charts/x.yml"),
        ],
    )
    def test_a_file_that_is_not_a_board_keeps_its_path(
        self, boards_dir: str, relpath: str
    ) -> None:
        assert stored_relpath(relpath, boards_dir) == relpath

    def test_listing_under_a_board_path_stays_inside_it(self) -> None:
        stored = ["dbt_charts.yml", "a.md", "sub/b.yml"]
        assert list(canonical_listing("charts", ".", stored)) == [
            "charts/a.md",
            "charts/sub/b.yml",
        ]
        assert list(canonical_listing("charts/sub", ".", ["sub/b.yml"])) == [
            "charts/sub/b.yml"
        ]

    def test_listing_a_non_board_folder_keeps_its_paths(self) -> None:
        assert list(canonical_listing("data", ".", ["data/x.csv"])) == ["data/x.csv"]

    def test_listing_the_root_names_a_note_filed_in_a_charts_folder(self) -> None:
        """Its stored path would read as the board ``charts/a.md``, another file."""
        stored = ["a.md", "charts/a.md"]
        assert list(canonical_listing(".", ".", stored)) == [
            "charts/a.md",
            "charts/charts/a.md",
        ]


def _write(root: Path, files: dict[str, str]) -> None:
    for relpath, content in files.items():
        (root / relpath).parent.mkdir(parents=True, exist_ok=True)
        (root / relpath).write_text(content)


NOTES = {
    "dbt_charts.yml": "boards_dir: .\n",
    "a.md": "# A\n",
    "_private.yml": "x: 1\n",
    "sub/b.yml": "title: B\n",
    "sub/pic.png": "png",
    "data/rows.csv": "a\n1\n",
}


@pytest.fixture
def notes_project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    _write(tmp_path, NOTES)
    return local_project(tmp_path)


class TestFilesystemProjectWithBoardsDirAtRoot:
    def test_reads_and_exists_use_the_canonical_path(
        self, notes_project: FilesystemProject
    ) -> None:
        assert notes_project.read_text("charts/a.md") == "# A\n"
        assert notes_project.exists("charts/sub/b.yml")
        assert notes_project.read_text("data/rows.csv") == "a\n1\n"
        assert notes_project.read_text("dbt_charts.yml") == "boards_dir: .\n"

    def test_a_board_exists_only_under_its_board_path(
        self, notes_project: FilesystemProject
    ) -> None:
        assert notes_project.exists("charts/a.md")
        assert not notes_project.exists("a.md")
        assert not notes_project.exists("sub/b.yml")
        assert not notes_project.exists("charts/dbt_charts.yml")
        with pytest.raises(FileNotFoundError):
            notes_project.read_text("a.md")

    def test_the_boards_meta_cascade_is_read_once(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        _write(
            tmp_path,
            {
                "dbt_charts.yml": "boards_dir: .\n",
                "meta.yml": "width: 900\n",
                "b.md": "# B\n",
            },
        )
        project = local_project(tmp_path)
        found = find_meta_files(project.path("charts/b.md"), project.directory("."))
        assert [m.relpath for m in found] == ["charts/meta.yml"]

    def test_a_dotdot_through_the_boards_path_cannot_escape(
        self, notes_project: FilesystemProject
    ) -> None:
        with pytest.raises(ValueError, match="escape"):
            notes_project.read_text("charts/../../outside.yml")

    def test_boards_list_under_charts_without_the_config_file(
        self, notes_project: FilesystemProject
    ) -> None:
        relpaths = [p.relpath for p in notes_project.iter_boards()]
        assert relpaths == ["charts/a.md", "charts/sub/b.yml"]
        assert [board_slug(r) for r in relpaths] == ["a", "sub/b"]

    def test_a_relative_image_resolves_beside_its_board(
        self, notes_project: FilesystemProject
    ) -> None:
        board = notes_project.path("charts/sub/b.yml")
        image = board.parent / "pic.png"
        assert image.relpath == "charts/sub/pic.png"
        assert notes_project.read_bytes(image.relpath) == b"png"

    def test_directory_listing_hides_the_config_file(
        self, notes_project: FilesystemProject
    ) -> None:
        children = [c.relpath for c in notes_project.iter_dir("charts")]
        assert children == [
            "charts/data",
            "charts/sub",
            "charts/_private.yml",
            "charts/a.md",
        ]

    def test_the_root_url_lists_the_boards_folder(
        self, notes_project: FilesystemProject
    ) -> None:
        resolved = resolve_directory_url(notes_project, "")
        assert isinstance(resolved, list)
        assert [d.relpath for d in resolved] == ["charts"]

    def test_a_directory_url_lists_a_boards_subfolder(
        self, notes_project: FilesystemProject
    ) -> None:
        resolved = resolve_directory_url(notes_project, "sub")
        assert isinstance(resolved, list)
        assert [d.relpath for d in resolved] == ["charts/sub"]

    def test_glob_and_grep_speak_canonical_paths(
        self, notes_project: FilesystemProject
    ) -> None:
        globbed = [p.relpath for p in notes_project.files.glob("charts/**/*.yml")]
        assert globbed == ["charts/_private.yml", "charts/sub/b.yml"]
        hits = [h.relpath for h in notes_project.files.grep("title")]
        assert hits == ["charts/sub/b.yml"]

    def test_a_real_path_maps_to_its_canonical_handle(
        self, notes_project: FilesystemProject
    ) -> None:
        handle = notes_project.path_for_fspath(notes_project.root / "sub" / "b.yml")
        assert handle.relpath == "charts/sub/b.yml"
        directory = notes_project.directory_for_fspath(notes_project.root / "sub")
        assert directory.relpath == "charts/sub"
        assert isinstance(directory, ProjectDirectory)
        assert isinstance(handle, ProjectPath)

    def test_writes_and_deletes_land_in_the_boards_folder(
        self, notes_project: FilesystemProject
    ) -> None:
        notes_project.write_text("charts/new/c.yml", "title: C\n")
        assert (notes_project.root / "new" / "c.yml").read_text() == "title: C\n"
        notes_project.delete_text("charts/new/c.yml")
        assert not (notes_project.root / "new" / "c.yml").exists()

    def test_a_board_compiles_with_the_boards_folders_meta_cascade(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        _write(
            tmp_path,
            {
                "dbt_charts.yml": "boards_dir: .\n",
                "meta.yml": "width: 900\n",
                "sub/b.yml": (
                    "title: B\n"
                    "queries:\n  q:\n    type: values\n    rows:\n      - k: a\n        v: 1\n"
                    "charts:\n  c1:\n    query: q\n    type: kpi\n    value: v\n"
                    "rows:\n  - c1\n"
                ),
            },
        )
        board = local_project(tmp_path).path("charts/sub/b.yml").read_board()
        result = compile_file(board)
        assert result.success, result.diagnostics
        assert (
            board_container_width(result.board, result.board.variable_defaults) == 900
        )

    def test_a_real_file_path_resolves_to_its_board(
        self, notes_project: FilesystemProject
    ) -> None:
        resolved = resolve_board_or_error(
            notes_project.root / "sub" / "b.yml", notes_project
        )
        assert not isinstance(resolved, Diagnostic)
        assert resolved.path is not None
        assert resolved.path.relpath == "charts/sub/b.yml"

    def test_a_slug_resolves_to_its_board(
        self, notes_project: FilesystemProject
    ) -> None:
        resolved = resolve_board_or_error(Path("sub/b"), notes_project)
        assert not isinstance(resolved, Diagnostic)
        assert resolved.path is not None
        assert resolved.path.relpath == "charts/sub/b.yml"

    def test_validate_without_a_path_validates_the_boards_folder(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        _write(
            tmp_path,
            {
                "dbt_charts.yml": "boards_dir: .\n",
                "ok.yml": "title: Ok\ntext: hello\n",
                "_partial.yml": "x: 1\n",
            },
        )
        results = validate_paths(None, project=local_project(tmp_path))
        assert [r.path for r in results] == ["charts/ok.yml"]
        assert all(r.success for r in results)

    def test_the_config_survives_a_second_project_over_the_same_folder(
        self,
        notes_project: FilesystemProject,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        again = local_project(notes_project.root)
        assert [p.relpath for p in again.iter_boards()] == [
            p.relpath for p in notes_project.iter_boards()
        ]


class TestFilesystemProjectWithBoardsDirInAFolder:
    def test_boards_live_in_the_named_folder_only(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        _write(
            tmp_path,
            {
                "dbt_charts.yml": "boards_dir: notes\n",
                "notes/a.md": "# A\n",
                "notes/sub/b.yml": "title: B\n",
                "charts/stray.yml": "title: S\n",
                "data/rows.csv": "a\n1\n",
            },
        )
        project = local_project(tmp_path)
        assert [p.relpath for p in project.iter_boards()] == [
            "charts/a.md",
            "charts/sub/b.yml",
        ]
        assert not project.exists("charts/stray.yml")
        assert project.read_text("data/rows.csv") == "a\n1\n"
        assert [p.relpath for p in project.files.glob("**/*.yml")] == [
            "charts/sub/b.yml",
            "dbt_charts.yml",
        ]


class TestWritesRefuseTheProjectsOwnFiles:
    @pytest.mark.parametrize(
        "relpath",
        [
            "charts/profiles.yml",
            "charts/./profiles.yml",
            "charts/sub/../dbt_charts.yml",
        ],
    )
    def test_write_and_delete_raise_value_error(
        self, notes_project: FilesystemProject, relpath: str
    ) -> None:
        with pytest.raises(ValueError, match="project"):
            notes_project.write_text(relpath, "x: 1\n")
        with pytest.raises(ValueError, match="project"):
            notes_project.delete_text(relpath)
        assert not (notes_project.root / "profiles.yml").exists()
        assert (notes_project.root / "dbt_charts.yml").read_text() == "boards_dir: .\n"


class TestListingsHandBackReadablePaths:
    """Whatever a listing yields reads back as the file it listed."""

    def _assert_readable(self, project: FilesystemProject, under: str) -> list[str]:
        listed = list(project.iter_files(under, recursive=True))
        for relpath in listed:
            assert project.read_text(relpath)
        return listed

    def test_a_dbt_models_folder_lists_its_yaml_as_boards(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        _write(
            tmp_path,
            {
                "dbt_charts.yml": "boards_dir: .\n",
                "models/schema.yml": "version: 2\n",
                "models/orders.sql": "select 1\n",
            },
        )
        listed = self._assert_readable(local_project(tmp_path), "models")
        assert listed == ["charts/models/schema.yml", "models/orders.sql"]

    def test_a_folder_above_a_nested_boards_folder(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        _write(
            tmp_path,
            {
                "dbt_charts.yml": "boards_dir: docs/notes\n",
                "docs/notes/a.md": "# A\n",
                "docs/notes/pic.png": "png",
                "docs/other.txt": "t\n",
            },
        )
        listed = self._assert_readable(local_project(tmp_path), "docs")
        assert listed == ["charts/a.md", "docs/notes/pic.png", "docs/other.txt"]

    def test_a_displaced_charts_folder_never_lists(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        _write(
            tmp_path,
            {"dbt_charts.yml": "boards_dir: notes\n", "charts/old.yml": "t: 1\n"},
        )
        project = local_project(tmp_path)
        assert list(project.iter_files("charts", recursive=True)) == []
        assert list(project.iter_files(".", recursive=True)) == ["dbt_charts.yml"]


class TestServe:
    def test_boards_serve_at_their_slug_with_listings_and_a_root_index(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            {
                "dbt_charts.yml": "boards_dir: .\n",
                "index.md": "# Home Page\n",
                "plan.md": "# The Plan\n",
                "sub/b.md": "# Nested Note\n",
            },
        )
        with TestClient(
            create_server(FilesystemProject(tmp_path)), raise_server_exceptions=False
        ) as client:
            assert "Home Page" in client.get("/").text
            assert "The Plan" in client.get("/plan").text
            assert "Nested Note" in client.get("/sub/b").text
            listing = client.get("/sub/")
            assert listing.status_code == 200
            assert "b" in listing.text
            assert client.get("/dbt_charts").status_code == 404
            assert client.get("/dbt_project").status_code == 404

    def test_editing_boards_dir_while_serving_moves_the_boards_at_once(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path, {"dbt_charts.yml": "boards_dir: .\n", "plan.md": "# Old Plan\n"}
        )
        with TestClient(
            create_server(FilesystemProject(tmp_path)), raise_server_exceptions=False
        ) as client:
            assert "Old Plan" in client.get("/plan").text
            (tmp_path / "plan.md").unlink()
            _write(tmp_path, {"notes/plan.md": "# New Plan\n"})
            config = tmp_path / "dbt_charts.yml"
            config.write_text("boards_dir: notes\n")
            os.utime(config, (time.time() + 10, time.time() + 10))
            assert "New Plan" in client.get("/plan").text


class TestABoardsFolderThatHoldsACharts:
    """Notes already filed under ``charts/`` are boards whose slug starts with
    ``charts/``; their URL must open them, which a default project reserves."""

    LAYOUTS = (
        (
            ".",
            {
                "dbt_charts.yml": "boards_dir: .\n",
                "x.md": "# Top\n",
                "charts/x.md": "# Stray\n",
            },
        ),
        (
            "notes",
            {
                "dbt_charts.yml": "boards_dir: notes\n",
                "notes/x.md": "# Top\n",
                "notes/charts/x.md": "# Stray\n",
            },
        ),
    )

    @pytest.mark.parametrize(("boards_dir", "files"), LAYOUTS)
    def test_the_slug_round_trips_to_the_file(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        boards_dir: str,
        files: dict[str, str],
    ) -> None:
        _write(tmp_path, files)
        project = local_project(tmp_path)
        relpaths = [p.relpath for p in project.iter_boards()]
        assert relpaths == ["charts/charts/x.md", "charts/x.md"]
        assert [board_slug(r) for r in relpaths] == ["charts/x", "x"]
        for relpath in relpaths:
            slug = board_slug(relpath)
            candidates = [c.relpath for c in board_file_candidates(project, slug)]
            assert relpath in candidates
            assert url_for_file(relpath) == f"/{slug}/"
        assert project.read_text("charts/charts/x.md") == "# Stray\n"
        assert project.read_text("charts/x.md") == "# Top\n"

    @pytest.mark.parametrize(("boards_dir", "files"), LAYOUTS)
    def test_serve_opens_the_board_and_lists_the_folder(
        self, tmp_path: Path, boards_dir: str, files: dict[str, str]
    ) -> None:
        _write(tmp_path, files)
        with TestClient(
            create_server(FilesystemProject(tmp_path)), raise_server_exceptions=False
        ) as client:
            assert "Stray" in client.get("/charts/x").text
            assert "Top" in client.get("/x").text
            listing = client.get("/charts/")
            assert listing.status_code == 200
            assert "x.md" in listing.text

    def test_a_default_project_still_reserves_the_charts_url(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, {"charts/x.md": "# Top\n", "charts/charts/x.md": "# Stray\n"})
        project = FilesystemProject(tmp_path)
        assert board_file_candidates(project, "charts/x") == []
        with TestClient(
            create_server(project), raise_server_exceptions=False
        ) as client:
            assert client.get("/charts/x").status_code == 404
            assert "Top" in client.get("/x").text


class TestDefaultStaysIdentical:
    def test_an_explicit_charts_matches_no_setting(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        files = {"charts/a.md": "# A\n", "charts/sub/b.yml": "title: B\n"}
        plain, explicit = tmp_path / "plain", tmp_path / "explicit"
        _write(plain, files)
        _write(explicit, {**files, "dbt_charts.yml": "boards_dir: charts\n"})
        listings = [
            [p.relpath for p in local_project(root).iter_boards()]
            for root in (plain, explicit)
        ]
        assert listings[0] == listings[1] == ["charts/a.md", "charts/sub/b.yml"]
        assert (
            local_project(explicit).charts_dir
            == local_project(explicit).root / "charts"
        )


@pytest.mark.parametrize("config", ["sources:\n  db: {type: duckdb\n", "- a\n- b\n"])
def test_a_broken_config_leaves_the_default_project_readable(
    tmp_path: Path, config: str
) -> None:
    """The config's own fault belongs to whatever reads the config; it must not
    take down every file read, which is what lets a board report its own errors."""
    (tmp_path / "dbt_charts.yml").write_text(config)
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "x.yml").write_text("title: Hello\n")

    project = FilesystemProject(tmp_path)

    assert project.exists("charts/x.yml")
    assert project.read_text("charts/x.yml") == "title: Hello\n"
