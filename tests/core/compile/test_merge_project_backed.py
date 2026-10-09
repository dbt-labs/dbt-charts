"""Parity test: an in-memory Project subclass vs the filesystem Project.

The extends/meta cascade (merge.py/meta.py) reads all project content through
``Project``/``ProjectPath``/``ProjectDirectory`` handles, never off disk directly.
This pins that seam: a ``Project`` whose four file-access methods are overridden to
serve an in-memory dict resolves relative extends refs, named-board extends, and the
meta chain identically to the on-disk filesystem ``Project`` — which is exactly how
an embedding host (Cloud's git-blob store) plugs in a non-disk backing store.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from functools import cached_property
from pathlib import Path

import pytest
import yaml

from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.project import (
    Project,
    ProjectDirectory,
    ProjectPath,
    iter_dir_from_relpaths,
)

from ...conftest import InMemoryFileQueries

# ---------------------------------------------------------------------------
# In-memory Project for testing — overrides the file-access methods, no disk
# ---------------------------------------------------------------------------


class InMemoryProject(Project):
    """Project backed by a plain dict. Overrides the same file-access methods a
    real embedding host (Cloud) overrides to serve project files without disk."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = dict(files)

    @property
    def sources(self) -> ProjectSourcesConfig:
        return ProjectSourcesConfig(sources={})

    @cached_property
    def files(self) -> InMemoryFileQueries:
        return InMemoryFileQueries(self)

    def exists(self, relpath: str) -> bool:
        return relpath in self._files

    def read_text(self, relpath: str) -> str:
        try:
            return self._files[relpath]
        except KeyError:
            raise FileNotFoundError(f"in-memory: no such file {relpath!r}") from None

    def read_bytes(self, relpath: str) -> bytes:
        return self.read_text(relpath).encode("utf-8")

    def iter_files(self, under: str, *, recursive: bool) -> Iterator[str]:
        prefix = "" if under == "." else f"{under}/"
        matches = [p for p in self._files if p.startswith(prefix)]
        if not recursive:
            # Immediate children only — no path separator past the prefix.
            matches = [p for p in matches if "/" not in p[len(prefix) :]]
        return iter(sorted(matches))

    def iter_dir(self, under: str) -> Iterator[ProjectPath | ProjectDirectory]:
        return iter_dir_from_relpaths(self, under, self._files)

    def write_text(self, relpath: str, content: str) -> None:
        self._files[relpath] = content

    def delete_text(self, relpath: str) -> None:
        if relpath not in self._files:
            raise FileNotFoundError(relpath)
        del self._files[relpath]


# ---------------------------------------------------------------------------
# Fixture data shared across both backends
# ---------------------------------------------------------------------------

# A board with: extends (relative path ref + named board in charts/), and a meta chain.
_BOARD_YAML = """\
title: FaceTitle
extends:
  - ./_base.yml
  - shared_template
"""

_BASE_YAML = """\
title: BaseTitle
notes: BaseNote
"""

_SHARED_TEMPLATE_YAML = """\
notes: SharedNote
"""

_ROOT_META_YAML = """\
title: RootMetaTitle
"""

_SUB_META_YAML = """\
notes: SubMetaNote
"""


def _build_in_memory_project() -> InMemoryProject:
    files = {
        "charts/sub/board.yaml": _BOARD_YAML,
        "charts/sub/_base.yml": _BASE_YAML,
        "shared_template.yaml": _SHARED_TEMPLATE_YAML,
        "charts/meta.yml": _ROOT_META_YAML,
        "charts/sub/meta.yml": _SUB_META_YAML,
    }
    return InMemoryProject(files)


def _build_filesystem_project(
    tmp_path: Path, local_project: Callable[..., Project]
) -> Project:
    def write(relpath: str, content: str) -> None:
        p = tmp_path / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    write("charts/sub/board.yaml", _BOARD_YAML)
    write("charts/sub/_base.yml", _BASE_YAML)
    write("shared_template.yaml", _SHARED_TEMPLATE_YAML)
    write("charts/meta.yml", _ROOT_META_YAML)
    write("charts/sub/meta.yml", _SUB_META_YAML)

    return local_project(tmp_path)


# ---------------------------------------------------------------------------
# Parity test: merged_patch output must be identical for both backends
# ---------------------------------------------------------------------------


def test_merged_patch_in_memory_equals_filesystem(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    """merged_patch on an in-memory project produces the same patch as a filesystem project.

    Priority (low→high): root meta < sub meta < board extends chain < board own fields.

    Expected resolution:
      - title: "FaceTitle" (board own field wins over meta and extends)
      - notes: "SharedNote" (shared_template wins over _base; sub meta loses to extends)
    """
    from dbt_charts.core.compile.merge import merged_patch
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    board_relpath = "charts/sub/board.yaml"

    # --- filesystem project ---
    fs_project = _build_filesystem_project(tmp_path, local_project)
    fs_board_file = fs_project.path(board_relpath)
    fs_board_node = BoardPatch.model_validate(
        yaml.safe_load(fs_board_file.read_text()) or {}
    )
    theme_sink_fs: list[str] = []
    fs_result = merged_patch(
        fs_board_node,
        fs_board_file,
        fs_project.directory("."),
        theme_sink_fs,
    )

    # --- in-memory project ---
    # No filesystem root at all: an accidental disk read is structurally
    # impossible here, not merely avoided by picking a distinct tmp_path.
    mem_project = _build_in_memory_project()
    mem_board_file = mem_project.path(board_relpath)
    mem_board_node = BoardPatch.model_validate(
        yaml.safe_load(mem_board_file.read_text()) or {}
    )
    theme_sink_mem: list[str] = []
    mem_result = merged_patch(
        mem_board_node,
        mem_board_file,
        mem_project.directory("."),
        theme_sink_mem,
    )

    # Both backends must resolve theme extends identically (empty here — no
    # theme-bearing extends in the fixture — but the channel must stay in parity).
    assert theme_sink_fs == theme_sink_mem

    # Both patches must agree on all set fields.
    assert fs_result.model_fields_set == mem_result.model_fields_set
    assert fs_result.model_dump(exclude_unset=True) == mem_result.model_dump(
        exclude_unset=True
    )

    # Spot-check the priority ordering: board own field > meta > extends
    assert mem_result.title == "FaceTitle"  # type: ignore[union-attr]
    assert mem_result.notes == "SharedNote"  # type: ignore[union-attr]


def test_merged_patch_cycle_detection_via_relpath() -> None:
    """Cycle detection works correctly with relpath-based identity (not absolute Path).

    A→B→A through ../ traversal must still raise CompilationError naming the cycle.
    """
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.merge import merged_patch
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    # a.yaml extends ../b.yaml (which is at root level)
    # b.yaml extends charts/a.yaml (cycle back)
    files = {
        "charts/a.yaml": "extends: ./../b.yaml\n",
        "b.yaml": "extends: ./charts/a.yaml\n",
    }
    project = InMemoryProject(files)
    board_file = project.path("charts/a.yaml")
    board_node = BoardPatch.model_validate({"extends": "./../b.yaml"})

    with pytest.raises(CompilationError, match="[Cc]ircular"):
        merged_patch(board_node, board_file, project.directory("."), None)


def test_resolve_meta_lint_in_memory() -> None:
    """resolve_meta_lint works on an in-memory project (no disk I/O)."""
    from dbt_charts.core.compile.parse.meta import resolve_meta_lint

    files = {
        "charts/meta.yml": "lint:\n  ignore:\n    - WARN-REAGGREGATION\n",
        "charts/sub/meta.yml": "lint:\n  ignore:\n    - WARN-REAGGREGATION\n",
        "charts/sub/board.yaml": "title: T\n",
    }
    project = InMemoryProject(files)
    board_file = project.path("charts/sub/board.yaml")

    result = resolve_meta_lint(board_file, project.directory("."))
    assert result is not None
    assert "WARN-REAGGREGATION" in result.ignore
    assert "WARN-REAGGREGATION" in result.ignore
