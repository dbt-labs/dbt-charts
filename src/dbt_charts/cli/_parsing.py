"""Shared argument parsing: `--flag key=value` pairs and board path arguments."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import typer


def parse_kv_pairs(items: Iterable[str], flag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise typer.BadParameter(f"{flag} expects key=value, got: {item!r}")
        k, v = item.split("=", 1)
        out[k] = v
    return out


def cwd_first(path: Path) -> Path:
    """Read a relative board argument the way a shell does.

    A relative path that exists from cwd becomes absolute, which every verb
    already treats as an explicit location (and rejects loudly when it sits
    outside the project). One that does not exist from cwd passes through
    unchanged, so project-relative names keep resolving against the project.
    """
    return Path.cwd() / path if not path.is_absolute() and path.exists() else path


def cwd_first_all(paths: list[Path] | None) -> list[Path] | None:
    """Typer callback form of ``cwd_first`` for a repeatable path argument."""
    return None if paths is None else [cwd_first(p) for p in paths]
