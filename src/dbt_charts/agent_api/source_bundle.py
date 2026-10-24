"""Source bundle: the directory an import writes and the migration skill reads.

Layout (``<root>/``)::

    outline.md        tiles in reading order: type, title, position, filters
    source.json       tool, url, fetched_at, raw_sha256, plus tool extras
    <raw>             the tool's raw definition, byte for byte
    queries/          compiled SQL (``*.sql``) and query definitions
    kpi/              ``*.csv`` of single-value tiles (may be empty)
    png/              ``*.png`` renders of the source board
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import urllib.parse
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def url_origin(parts: urllib.parse.SplitResult) -> str:
    """``scheme://host[:port]`` of a split URL, without userinfo."""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def public_url(ref: str) -> str:
    """The user's URL minus userinfo, query string and fragment (they can hold tokens)."""
    parts = urllib.parse.urlsplit(ref)
    return f"{url_origin(parts)}{parts.path}"


def slug(text: str, *, fallback: str) -> str:
    """Lowercase ``text`` as a filename-safe slug; ``fallback`` when nothing is left."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or fallback


class BundleError(Exception):
    """An import could not produce a complete bundle."""


class SourceBundle:
    """Writes one bundle; ``finish`` seals it or raises naming the missing piece."""

    def __init__(self, root: Path, *, tool: str, url: str, raw: str) -> None:
        self.root = root
        self.tool = tool
        self.url = url
        self.raw = raw
        (root / "kpi").mkdir(parents=True, exist_ok=True)

    def write(self, relpath: str, data: str | bytes) -> None:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            path.write_text(data)
        else:
            path.write_bytes(data)

    def finish(self, *, extra: dict[str, str | list[str]] | None = None) -> None:
        raw_path = self.root / self.raw
        if not raw_path.is_file():
            raise BundleError(f"bundle is missing {self.raw}")
        source = {
            "tool": self.tool,
            "url": self.url,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            **(extra or {}),
        }
        self.write("source.json", json.dumps(source, indent=2) + "\n")
        check_layout(self.root, raw=self.raw)


def check_layout(root: Path, *, raw: str) -> None:
    """Raise `BundleError` naming the first required file or directory missing."""
    for name in ("outline.md", "source.json", raw):
        if not (root / name).is_file():
            raise BundleError(f"bundle is missing {name}")
    if not (root / "kpi").is_dir():
        raise BundleError("bundle is missing the kpi/ directory")
    for directory, pattern in (("queries", "*.sql"), ("png", "*.png")):
        if not any((root / directory).glob(pattern)):
            raise BundleError(f"bundle has no {directory}/{pattern} files")


@contextmanager
def staged_bundle(
    out: Path, *, tool: str, url: str, raw: str, force: bool
) -> Generator[SourceBundle]:
    """Build a bundle in a sibling directory and move it to ``out`` once sealed.

    A failed build leaves ``out`` untouched. A non-empty ``out`` is refused
    unless ``force``, which replaces it.
    """
    if out.exists() and any(out.iterdir()) and not force:
        raise BundleError(f"{out} is not empty; pass --force to replace it")
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    try:
        yield SourceBundle(staging, tool=tool, url=url, raw=raw)
        check_layout(staging, raw=raw)
        retired = staging.with_name(f"{staging.name}.old")
        if out.exists():
            out.rename(retired)
        staging.rename(out)
        shutil.rmtree(retired, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
