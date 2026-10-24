"""Tests for required-variable serve errors."""

from pathlib import Path

import pytest

_BOARD_YAML = """\
variables:
  account_id:
    input: text
    label: Account ID
    required: true
queries:
  q:
    type: values
    rows:
      - {id: 1}
charts:
  c:
    query: q
    type: kpi
    value: id
rows:
  - c
"""

_BOARD_TWO_VARS = """\
variables:
  account_id:
    input: text
    label: Account ID
    required: true
  region:
    input: text
    label: Region
    required: true
queries:
  q:
    type: values
    rows:
      - {id: 1}
charts:
  c:
    query: q
    type: kpi
    value: id
rows:
  - c
"""


@pytest.fixture
def board_file(tmp_path: Path) -> Path:
    boards = tmp_path / "charts"
    boards.mkdir()
    f = boards / "required_var_board.yml"
    f.write_text(_BOARD_YAML)
    return f


@pytest.fixture
def two_var_board_file(tmp_path: Path) -> Path:
    boards = tmp_path / "charts"
    boards.mkdir()
    f = boards / "two_var_board.yml"
    f.write_text(_BOARD_TWO_VARS)
    return f


def test_serve_missing_required_variable_renders_structured_error(
    board_file: Path,
) -> None:
    """Serve returns the normal structured error page when a required var is absent."""
    from fastapi.testclient import TestClient

    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.serve.server import create_server

    app = create_server(FilesystemProject(board_file.parent.parent))
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(f"/{board_file.stem}")
    assert resp.status_code == 422
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "ERR-INPUT-INVALID" in body
    assert "Missing required variables: account_id (Account ID)" in body
    assert "?account_id=" in body
    assert "default:" in body
    assert 'type="submit"' not in body
    assert 'name="account_id"' not in body


def test_serve_missing_required_variable_error_page_tracks_theme(
    tmp_path: Path,
) -> None:
    """The structured error page uses the board theme, not a hardcoded
    palette."""
    from fastapi.testclient import TestClient

    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.serve.server import create_server

    rstyle = resolve_style(get_theme_style("neon"))
    boards = tmp_path / "charts"
    boards.mkdir()
    board_file = boards / "dark_required_var.yml"
    board_file.write_text("theme: neon\n" + _BOARD_YAML)

    app = create_server(FilesystemProject(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(f"/{board_file.stem}")

    assert resp.status_code == 422
    body = resp.text
    assert f"--panel-bg: {rstyle.background};" in body
    assert f"--text: {rstyle.font.color};" in body
    assert f"--secondary: {rstyle.secondary_color};" in body
    assert f"--border: {rstyle.border.color};" in body
    assert f"--accent: {rstyle.accent};" in body
    assert f"--font-family: {rstyle.font.family};" in body


def test_serve_required_variable_provided_renders_ok(board_file: Path) -> None:
    """Server renders normally when the required variable is provided as a query param."""
    from fastapi.testclient import TestClient

    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.serve.server import create_server

    app = create_server(FilesystemProject(board_file.parent.parent))
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(f"/{board_file.stem}?account_id=acct_123")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Missing required variables" not in resp.text


def test_serve_required_variable_default_renders_ok(tmp_path: Path) -> None:
    """A required variable with a default must not fail at serve time."""
    from fastapi.testclient import TestClient

    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.serve.server import create_server

    boards = tmp_path / "charts"
    boards.mkdir()
    board_file = boards / "required_var_default.yml"
    board_file.write_text(
        """\
variables:
  account_id:
    input: text
    label: Account ID
    required: true
    default: acct_default
queries:
  q:
    type: values
    rows:
      - {id: 1}
charts:
  c:
    query: q
    type: kpi
    value: id
rows:
  - c
"""
    )

    app = create_server(FilesystemProject(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(f"/{board_file.stem}")
    assert resp.status_code == 200
    assert "Missing required variables" not in resp.text


def test_serve_partial_required_vars_shows_only_missing_variables(
    two_var_board_file: Path,
) -> None:
    """When only some required vars are provided, the error lists only the missing ones."""
    from fastapi.testclient import TestClient

    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.serve.server import create_server

    app = create_server(FilesystemProject(two_var_board_file.parent.parent))
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(f"/{two_var_board_file.stem}?account_id=acct_123")
    assert resp.status_code == 422
    body = resp.text
    assert "Missing required variables: region (Region)" in body
    assert "?region=" in body
    assert "Account ID" not in body


def test_missing_variable_dict_keys_match_dataclass_fields() -> None:
    """Pin the round trip: serve reconstructs MissingVariable(**d) from the dict
    that MissingRequiredVariablesError emits in fields['missing']. If either side
    grows a field, this test fails before runtime.
    """
    from dataclasses import fields as dataclass_fields

    from dbt_charts.core.render.errors import (
        MissingRequiredVariablesError,
        MissingVariable,
    )

    mv = MissingVariable(
        key="account_id", label="Account ID", notes="desc", input_type="text"
    )
    err = MissingRequiredVariablesError([mv])
    emitted_keys = set(err.fields["missing"][0].keys())
    dataclass_keys = {f.name for f in dataclass_fields(MissingVariable)}
    assert emitted_keys == dataclass_keys
    # Reconstruct with **d - TypeError here means a schema drift.
    rebuilt = MissingVariable(**err.fields["missing"][0])
    assert rebuilt == mv
