"""Tests for port resolution logic."""

import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from dbt_charts.core.serve.port import (
    find_available_port,
    is_port_available,
    port_from_project_dir,
    resolve_port,
)

# --- port_from_project_dir ---


def test_port_from_project_dir_returns_int_in_range():
    """Hash-based port is within the expected range."""
    port = port_from_project_dir(Path("/some/project"))
    assert 9000 <= port <= 9999


def test_port_from_project_dir_is_deterministic():
    """Same directory always produces the same port."""
    a = port_from_project_dir(Path("/some/project"))
    b = port_from_project_dir(Path("/some/project"))
    assert a == b


def test_port_from_project_dir_differs_for_different_dirs():
    """Different directories (almost certainly) get different ports."""
    a = port_from_project_dir(Path("/project/alpha"))
    b = port_from_project_dir(Path("/project/beta"))
    # Not guaranteed unique, but extremely likely for these two strings
    assert a != b


def test_port_from_project_dir_resolves_path(tmp_path):
    """Relative and absolute paths to the same dir give the same port."""
    # Use a real tmp_path so resolve() works
    subdir = tmp_path / "myproject"
    subdir.mkdir()
    abs_port = port_from_project_dir(subdir)
    rel_port = port_from_project_dir(subdir.resolve())
    assert abs_port == rel_port


# --- find_available_port ---


def test_find_available_port_returns_preferred_when_free():
    """When the preferred port is free, it is returned."""
    # Bind a socket to find a free port, then release it
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        free_port = s.getsockname()[1]
    # Now free_port should be available
    assert find_available_port(free_port) == free_port


def test_find_available_port_skips_occupied():
    """When the preferred port is occupied, the next available port is returned."""
    # Occupy a port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("localhost", 0))
        # Listen like a real server: Linux lets our SO_REUSEADDR server share a
        # port with a socket that is bound but not listening.
        s.listen(1)
        occupied_port = s.getsockname()[1]
        # While occupied, find_available_port should skip it
        result = find_available_port(occupied_port)
        assert result != occupied_port
        assert result > occupied_port


def test_find_available_port_max_attempts():
    """Raises RuntimeError after exhausting max_attempts."""
    # Mock is_port_available to always return False
    with (
        patch("dbt_charts.core.serve.port.is_port_available", return_value=False),
        pytest.raises(RuntimeError, match="Could not find"),
    ):
        find_available_port(9876, max_attempts=5)


# --- resolve_port ---


def test_resolve_port_explicit_flag_wins(tmp_path):
    """--port flag takes highest priority."""
    port = resolve_port(explicit_port=4444, project_dir=tmp_path)
    assert port == 4444


def test_resolve_port_env_var_second(tmp_path, monkeypatch):
    """DCT_PORT env var is used when no explicit port."""
    monkeypatch.setenv("DCT_PORT", "5555")
    port = resolve_port(project_dir=tmp_path)
    # Auto-increment may bump if 5555 is occupied, but should start from 5555
    assert port >= 5555


def test_resolve_port_env_var_invalid(tmp_path, monkeypatch):
    """Invalid DCT_PORT raises a clear error."""
    monkeypatch.setenv("DCT_PORT", "not_a_number")
    with pytest.raises(ValueError, match="DCT_PORT must be an integer"):
        resolve_port(project_dir=tmp_path)


def test_resolve_port_config_file_third(tmp_path):
    """server.port in dbt_charts.yml is used when no flag or env var."""
    (tmp_path / "dbt_charts.yml").write_text("server:\n  port: 6666\n")
    port = resolve_port(project_dir=tmp_path)
    # Auto-increment may bump if 6666 is occupied, but should start from 6666
    assert port >= 6666


def test_resolve_port_config_file_invalid(tmp_path):
    """Invalid server.port in dbt_charts.yml raises a clear error."""
    (tmp_path / "dbt_charts.yml").write_text("server:\n  port: banana\n")
    with pytest.raises(ValueError, match="Invalid server.port in"):
        resolve_port(project_dir=tmp_path)


def test_resolve_port_hash_based_default(tmp_path):
    """Falls back to hash-based port when nothing else is set."""
    port = resolve_port(project_dir=tmp_path)
    assert 9000 <= port <= 9999
    # Should be deterministic
    assert port == resolve_port(project_dir=tmp_path)


def test_resolve_port_auto_increment_on_occupied(tmp_path):
    """When the resolved port is occupied, auto-increments to next free one."""
    expected_port = port_from_project_dir(tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("localhost", expected_port))
        # Listen like a real server: Linux lets our SO_REUSEADDR server share a
        # port with a socket that is bound but not listening.
        s.listen(1)
        port = resolve_port(project_dir=tmp_path)
        assert port != expected_port
        assert port > expected_port


def test_resolve_port_env_var_out_of_range(tmp_path, monkeypatch):
    """DCT_PORT outside 1-65535 raises ValueError."""
    monkeypatch.setenv("DCT_PORT", "0")
    with pytest.raises(ValueError, match="must be between 1 and 65535"):
        resolve_port(project_dir=tmp_path)


def test_resolve_port_config_port_out_of_range(tmp_path):
    """server.port in dbt_charts.yml outside 1-65535 raises ValueError."""
    (tmp_path / "dbt_charts.yml").write_text("server:\n  port: 99999\n")
    with pytest.raises(ValueError, match="must be between 1 and 65535"):
        resolve_port(project_dir=tmp_path)


def test_resolve_port_default_port_parameter(tmp_path):
    """default_port overrides hash-based default when no other source is set."""
    port = resolve_port(project_dir=tmp_path, default_port=8085)
    assert port >= 8085


def test_resolve_port_explicit_port_validated(tmp_path):
    """Explicit --port outside valid range raises ValueError."""
    with pytest.raises(ValueError, match="must be between 1 and 65535"):
        resolve_port(explicit_port=0, project_dir=tmp_path)


def test_resolve_port_explicit_not_auto_incremented(tmp_path):
    """Explicit --port is returned as-is even if occupied (user's choice)."""
    port = resolve_port(explicit_port=7777, project_dir=tmp_path)
    assert port == 7777


# Sweeping every shipped example project's port is intentionally not
# covered here — most of them stay at the monorepo examples/ root, outside
# dbt-charts/.


@pytest.mark.windows
def test_is_port_available_after_server_closed_with_time_wait():
    """A port whose last server just closed (connections in TIME_WAIT) can be bound
    again, as the server itself binds it with SO_REUSEADDR."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn does
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = server.accept()
    conn.close()  # the server side closes first, so its end sits in TIME_WAIT
    server.close()
    client.close()
    assert is_port_available(port, "127.0.0.1")


@pytest.mark.windows
@pytest.mark.parametrize("listen_on", ["127.0.0.1", "0.0.0.0"])  # noqa: S104
def test_is_port_available_false_while_listening(listen_on: str):
    """A wildcard listener counts too: with SO_REUSEADDR, BSD/macOS let a
    127.0.0.1 bind succeed beside it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind((listen_on, 0))
        server.listen(1)
        assert not is_port_available(server.getsockname()[1], "127.0.0.1")
