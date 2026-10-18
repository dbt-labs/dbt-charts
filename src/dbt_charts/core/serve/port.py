"""Port resolution for dct serve / dct-playground.

Resolution order:
1. Explicit --port CLI flag (highest priority)
2. DCT_PORT env var
3. port field in dbt_charts.yml
4. Hash-based port from project directory path
5. Auto-increment fallback if chosen port is occupied
"""

import hashlib
import os
import socket
import sys
from pathlib import Path  # noqa: TID251 — reads dbt_charts.yml, hashes project dir path

# Range for hash-based ports: 9000–9999 (1000 slots).
_PORT_RANGE_START = 9000
_PORT_RANGE_SIZE = 1000

# How many consecutive ports auto-resolution tries before giving up.
PORT_SCAN_ATTEMPTS = 50


def port_from_project_dir(project_dir: Path) -> int:
    """Derive a deterministic port from the project directory path."""
    resolved = str(project_dir.resolve())
    digest = hashlib.sha256(resolved.encode()).hexdigest()
    return _PORT_RANGE_START + int(digest, 16) % _PORT_RANGE_SIZE


def _validate_port(port: int, source: str) -> None:
    """Raise ValueError if *port* is outside the valid TCP range."""
    if not (1 <= port <= 65535):
        raise ValueError(f"{source} must be between 1 and 65535, got {port}")


def _can_listen(port: int, host: str) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if sys.platform != "win32":
            # The server binds with SO_REUSEADDR, so a port whose last server
            # just closed (connections in TIME_WAIT) is free to it. Windows has
            # no TIME_WAIT bind conflict, and there the flag would let a bind
            # succeed beside a live listener.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            # Linux lets two SO_REUSEADDR sockets bind one port and refuses the
            # second only at listen(), which is what the server does next.
            s.listen(1)
        except OSError:
            return False
    return True


def is_port_available(port: int, host: str = "localhost") -> bool:
    """Return True if a server can listen on *port* at *host* (IPv4 only).

    The wildcard address is probed too: BSD/macOS let a bind to one address
    succeed beside a listener on the wildcard.
    """
    return all(
        _can_listen(port, address) for address in dict.fromkeys([host, "0.0.0.0"])
    )  # noqa: S104


def find_available_port(
    preferred: int,
    host: str = "localhost",
    max_attempts: int = PORT_SCAN_ATTEMPTS,
) -> int:
    """Return *preferred* if free, otherwise scan upward until a free port is found."""
    for offset in range(max_attempts):
        candidate = preferred + offset
        if candidate > 65535:
            break
        if is_port_available(candidate, host):
            return candidate
    raise RuntimeError(
        f"Could not find an available port after {max_attempts} attempts "
        f"(starting from {preferred})"
    )


def _read_port_from_config(project_dir: Path) -> int | None:
    """Read ``server.port`` from dbt_charts.yml, if present.

    TODO(fileplugin): reads dbt_charts.yml directly off disk. Local-serve only
    (the listen port is a process concern), so it stays filesystem-coupled; a
    Cloud backend never binds a port here.
    """
    import yaml

    from dbt_charts.core.project import PROJECT_CONFIG_NAME

    cfg_path = project_dir / PROJECT_CONFIG_NAME
    if not cfg_path.exists():
        return None
    try:
        parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        data = parsed or {}  # type-state: silent_fallback — empty config, not a bug
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {cfg_path}: {e}") from e
    server = data.get("server")
    if server is None:
        return None
    if not isinstance(server, dict):
        raise ValueError(
            f"Invalid server config in {cfg_path}: expected mapping, "
            f"got {type(server).__name__}"
        )
    raw = server.get("port")
    if raw is None:
        return None
    try:
        port = int(raw)
    except (ValueError, TypeError):
        raise ValueError(
            f"Invalid server.port in {cfg_path}: expected integer, got {raw!r}"
        ) from None
    _validate_port(port, f"server.port in {cfg_path}")
    return port


def preferred_port(*, project_dir: Path, default_port: int | None = None) -> int:
    """The port auto-resolution starts from, before skipping occupied ones.

    Priority: DCT_PORT env > dbt_charts.yml server.port > hash-based default.
    """
    env_val = os.environ.get("DCT_PORT")  # noqa: TID251 — DCT_PORT server knob
    if env_val is not None:
        try:
            env_port = int(env_val)
        except ValueError:
            raise ValueError(f"DCT_PORT must be an integer, got: {env_val!r}") from None
        _validate_port(env_port, "DCT_PORT")
        return env_port

    config_port = _read_port_from_config(project_dir)
    if config_port is not None:
        return config_port

    if default_port is not None:
        _validate_port(default_port, "default_port")
        return default_port
    return port_from_project_dir(project_dir)


def resolve_port(
    *,
    project_dir: Path,
    explicit_port: int | None = None,
    host: str = "localhost",
    default_port: int | None = None,
) -> int:
    """Resolve the port to serve on.

    Priority: explicit_port > DCT_PORT env > dbt_charts.yml server.port >
    hash-based default.

    Explicit ports (--port flag) are returned as-is — if the user asked for a
    specific port, we respect that even if it's occupied (uvicorn will error).

    For auto-resolved ports, we auto-increment to the next available port if the
    chosen one is occupied.
    """
    if explicit_port is not None:
        _validate_port(explicit_port, "--port")
        return explicit_port
    return find_available_port(
        preferred_port(project_dir=project_dir, default_port=default_port), host
    )
