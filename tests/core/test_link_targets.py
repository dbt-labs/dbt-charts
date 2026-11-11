"""check_link_targets reads a board's link facts and the project's board list."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.link_targets import check_link_targets


def _warned(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    link: str,
    relpath: str = "charts/people.yml",
    slug: str | None = None,
) -> list[tuple[str | None, bool]]:
    project = local_project(tmp_path)
    project.write_text("charts/user-360.yml", "rows: []\n")
    project.write_text("charts/sub/detail.yml", "rows: []\n")
    found = check_link_targets([("style.footer.link", link, False)], project, relpath)
    return [(d.field, slug is not None and slug in d.message) for d in found]


def test_unknown_target_warns_with_its_field_path_and_slug(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    assert _warned(tmp_path, local_project, "/nope", slug="nope") == [
        ("style.footer.link", True)
    ]


def test_known_target_is_clean(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    assert _warned(tmp_path, local_project, "/user-360") == []


def test_templated_target_is_skipped(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    assert _warned(tmp_path, local_project, "/{{ page }}") == []


def test_built_in_view_is_skipped(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    assert _warned(tmp_path, local_project, "/data/nothing-here") == []


def test_relative_link_resolves_from_a_nested_folder(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    assert _warned(tmp_path, local_project, "detail", "charts/sub/other.yml") == []
    assert _warned(
        tmp_path, local_project, "missing", "charts/sub/other.yml", "sub/missing"
    ) == [("style.footer.link", True)]
