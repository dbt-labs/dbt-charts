"""validate() warns when a board link names a board that does not exist."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from dbt_charts.agent_api.validate import ValidateResult, validate
from dbt_charts.cli.filesystem_project import FilesystemProject

_CODE = "WARN-LINK-TARGET-NOT-FOUND"
_EMPTY_BOARD = "rows: []\n"


def _table_board(link: str) -> str:
    """A table whose Email column links to *link*."""
    return f"""
queries:
  people:
    columns: [email]
    values:
      - [a@example.com]
charts:
  people_table:
    type: table
    query: people
    style:
      columns:
        email:
          link: "{link}"
rows:
  - people_table
"""


def _validate(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    link: str,
    *,
    at: str = "charts/people.yml",
    boards: tuple[str, ...] = ("user-360",),
) -> ValidateResult:
    project = local_project(tmp_path)
    for slug in boards:
        project.write_text(f"charts/{slug}.yml", _EMPTY_BOARD)
    project.write_text(at, _table_board(link))
    return validate(Path(at), project=project)


def _target_warnings(result: ValidateResult) -> list[str]:
    return [w.message for w in result.warnings if w.code == _CODE]


def test_link_to_an_existing_board_is_clean(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _validate(tmp_path, local_project, "/user-360?user_email={{ email }}")
    assert _target_warnings(result) == []


def test_link_to_a_missing_board_warns_and_still_saves(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _validate(tmp_path, local_project, "/no-such-board?id={{ email }}")
    assert result.success is True
    [message] = _target_warnings(result)
    assert "/no-such-board" in message
    assert "charts.people_table.style.columns.email.link" in message


def test_link_whose_path_ends_in_a_board_slug_suggests_the_slug(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _validate(
        tmp_path,
        local_project,
        "/acme/sales/d/user-360?user_email={{ email | urlencode }}",
    )
    [warning] = [w for w in result.warnings if w.code == _CODE]
    assert warning.hint == "Did you mean '/user-360'?"


def test_suffix_sugar_resolves_to_the_board(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _validate(tmp_path, local_project, "/user-360.yml")
    assert _target_warnings(result) == []


def test_folder_link_resolves_to_its_index_board(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _validate(tmp_path, local_project, "/reports/", boards=("reports/index",))
    assert _target_warnings(result) == []


def test_relative_link_resolves_against_the_linking_boards_folder(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    kwargs = {"at": "charts/finance/rev.yml", "boards": ("finance/costs",)}
    found = _validate(tmp_path, local_project, "./costs", **kwargs)
    missing = _validate(tmp_path, local_project, "./nope", **kwargs)
    assert _target_warnings(found) == []
    assert len(_target_warnings(missing)) == 1


def test_links_that_name_no_board_are_not_checked(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    for link in (
        "https://example.com/x",
        "mailto:a@example.com",
        "?region={{ email }}",
        "/{{ email }}",
        "/",
    ):
        result = _validate(tmp_path, local_project, link)
        assert _target_warnings(result) == [], link


def test_board_outside_every_board_root_is_not_checked(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _validate(tmp_path, local_project, "/no-such-board", at="loose.yml")
    assert result.success is True
    assert _target_warnings(result) == []


def test_link_to_a_built_in_data_view_is_not_checked(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _validate(tmp_path, local_project, "/data/warehouse/public/orders")
    assert _target_warnings(result) == []


def test_board_links_are_checked_against_one_board_listing(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = local_project(tmp_path)
    project.write_text("charts/user-360.yml", _EMPTY_BOARD)
    project.write_text("charts/people.yml", _table_board("/user-360"))
    with (
        patch.object(project, "iter_boards", wraps=project.iter_boards) as listing,
        patch.object(project, "exists", wraps=project.exists) as probe,
    ):
        result = validate(Path("charts/people.yml"), project=project)
    assert _target_warnings(result) == []
    assert listing.call_count == 1
    assert not [c for c in probe.call_args_list if "user-360" in c.args[0]]


def test_link_under_a_reserved_prefix_that_matches_no_view_still_warns(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _validate(tmp_path, local_project, "/data/a/b/c/d/e/f")
    assert len(_target_warnings(result)) == 1
