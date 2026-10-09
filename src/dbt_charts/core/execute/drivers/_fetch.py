"""Resolve a CDN driver to a file on this machine, downloading it if it is absent.

Stage: EXECUTE

Mirrors dbt v2's install path (``dbt-adbc/src/install.rs``) closely enough that
the two share a cache: same directory, same filename, same version in the name.
That sharing is the point — a machine that has run ``dbt run`` needs no download
at all, and ``dbt system install-drivers`` populates the cache for a firewalled
host, which is why there is no mirror or override environment variable here.

The cache key includes the pinned version, so a cached build of another version
is a miss, never a substitute. A file at the pinned path is not trusted for being
there: the cache is shared with dbt and writable by anything running as the user,
and the path is `dlopen`ed. It is hashed against the pinned digest of the
decompressed library on every open, and a
mismatch raises rather than re-downloading over dbt's file. A download is hashed
against :data:`CDN_DRIVER_CHECKSUMS` as it streams, its decompressed output
against :data:`CDN_LIBRARY_CHECKSUMS`, and lands by atomic rename only if both
match. A platform with no checksum row is refused before any socket opens.

This module reaches the local filesystem, which ``core`` otherwise does not do
(``core/AGENTS.md``): a driver is where the engine meets the machine it is
running on, and a cache shared with dbt cannot be expressed as project I/O.
``platformdirs`` and ``zstandard`` are declared by the ``lakecompute`` extra.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import secrets
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path  # noqa: TID251 — the driver cache dbt shares, not project I/O
from typing import Any
from urllib.parse import quote

import httpx
import zstandard
from platformdirs import user_cache_dir

from dbt_charts.core.diagnostics.codes_execute import (
    ERR_DRIVER_CHECKSUM_MISMATCH,
    ERR_DRIVER_PLATFORM_UNSUPPORTED,
    ERR_DRIVER_TOO_LARGE,
)
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.drivers._driver_index import (
    CDN_DRIVER_CHECKSUMS,
    CDN_HOST,
    CDN_LIBRARY_CHECKSUMS,
    DOWNLOAD_TIMEOUT_SECONDS,
    MAX_DECOMPRESSED_BYTES,
    pinned_version,
)

# dbt's cache namespace, not ours: the whole point is to find the file `dbt run`
# already downloaded.
_CACHE_NAMESPACE = "com.getdbt"

# `platform.machine()` answers `arm64` on macOS and `AMD64` on Windows; the
# published artifacts are named for Rust's `env::consts::ARCH`. Without this
# translation every lookup would miss every row on two of three platforms.
_ARCH_BY_MACHINE: dict[str, str] = {
    "aarch64": "aarch64",
    "arm64": "aarch64",
    "amd64": "x86_64",
    "x86_64": "x86_64",
}

# Candidate OS labels per `sys.platform`, in the order they are tried. A tuple
# rather than a single label because a Linux driver is named for the manylinux
# image it was built in and that differs between drivers; which one applies is
# answered by which row the checksum table has, not by anything about the
# running machine. The lake compute driver ships 2_28 today, so that is the only
# Linux label here — add the next one when a row needs it.
#
# The map is exhaustive on purpose. FreeBSD, AIX and Cygwin are not Linux and a
# manylinux build will not load there, so they must reach the refusal below
# rather than matching a row that does not describe them.
_OS_CANDIDATES: dict[str, tuple[str, ...]] = {
    "darwin": ("apple-darwin",),
    "linux": ("manylinux_2_28-linux-gnu",),
    "win32": ("pc-windows-msvc",),
}

# Decompressed output is read in blocks of this size, which bounds the memory a
# hostile body can claim before MAX_DECOMPRESSED_BYTES is checked.
_READ_BLOCK_BYTES = 1024 * 1024

_SUFFIXES: dict[str, str] = {"apple-darwin": ".dylib", "pc-windows-msvc": ".dll"}


@dataclass(frozen=True)
class DriverTriplet:
    """One published build: which driver, for which platform, at which version."""

    driver: str
    os: str
    arch: str
    version: str

    @property
    def suffix(self) -> str:
        """The shared-library extension for this build's OS."""
        return _SUFFIXES.get(
            self.os, ".so"
        )  # type-state: silent_fallback — .so is the Linux suffix; the table holds only the two exceptions

    @property
    def filename(self) -> str:
        """The cached filename dbt writes — no ``lib`` prefix on Windows."""
        prefix = "" if self.os == "pc-windows-msvc" else "lib"
        return f"{prefix}adbc_driver_{self.driver}-{self.version}{self.suffix}"


def resolve_triplet(driver: str) -> DriverTriplet:
    """The build of *driver* published for this machine.

    Raises:
        ExecutionError: ERR-DRIVER-PLATFORM-UNSUPPORTED when no build exists for
            this OS and CPU. Raised before any network call — an unpublished
            platform is a refusal, not an unverified download.
    """
    version = pinned_version(driver)
    arch = _ARCH_BY_MACHINE.get(
        platform.machine().lower(), platform.machine()
    )  # type-state: silent_fallback — an unmapped machine keeps its own name so the error below can print it
    for os_label in _OS_CANDIDATES.get(
        sys.platform, ()
    ):  # type-state: silent_fallback — no candidates IS the refusal: an unlisted platform falls straight to the raise below
        if (driver, os_label, arch, version) in CDN_DRIVER_CHECKSUMS:
            return DriverTriplet(driver, os_label, arch, version)
    raise ExecutionError.from_code(
        ERR_DRIVER_PLATFORM_UNSUPPORTED,
        driver=driver,
        arch=arch,
        os=sys.platform,
        available=", ".join(
            sorted(
                f"{row_arch} {row_os}"
                for (row_driver, row_os, row_arch, _) in CDN_DRIVER_CHECKSUMS
                if row_driver == driver
            )
        ),
    )


def driver_url(triplet: DriverTriplet) -> str:
    """Where the CDN publishes *triplet*.

    The version is percent-encoded with ``-``, ``.`` and ``_`` left literal, so
    a local version reaches the CDN with its ``+`` as ``%2B``. The published
    name carries no ``lib`` prefix on any platform, unlike the cached one.
    """
    version = quote(triplet.version, safe="-._")
    return (
        f"https://{CDN_HOST}/fs/adbc/{triplet.driver}/adbc_driver_{triplet.driver}"
        f"-{version}-{triplet.arch}-{triplet.os}{triplet.suffix}.zst"
    )


def driver_cache_path(triplet: DriverTriplet) -> Path:
    """Where dbt's installer would have written *triplet* on this machine.

    ``user_cache_dir()`` is called with no application name on purpose: the
    namespace is dbt's ``com.getdbt``, and passing an app name would append
    ``\\Cache`` on Windows and land beside dbt's directory rather than in it.

    Example, for the lake compute build on an Apple Silicon Mac::

        ~/Library/Caches/com.getdbt/adbc/aarch64-apple-darwin/libadbc_driver_dbt-<version>.dylib
    """
    return (
        Path(user_cache_dir())
        / _CACHE_NAMESPACE
        / "adbc"
        / f"{triplet.arch}-{triplet.os}"
        / triplet.filename
    )


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_READ_BLOCK_BYTES):
            hasher.update(block)
    return hasher.hexdigest()


def _verify_library(triplet: DriverTriplet, path: Path) -> None:
    """Raise unless the file at *path* is the pinned decompressed library.

    Never repairs: the file may be dbt's, so the remedy is the user's to apply.
    """
    actual = _file_digest(path)
    expected = CDN_LIBRARY_CHECKSUMS[
        (triplet.driver, triplet.os, triplet.arch, triplet.version)
    ]
    if actual != expected:
        raise ExecutionError.from_code(
            ERR_DRIVER_CHECKSUM_MISMATCH,
            subject=str(path),
            actual=actual,
            expected=expected,
            outcome=(
                "It was not loaded or replaced, since the cache is shared with "
                "dbt. Delete the file and retry."
            ),
        )


def driver_path(driver: str) -> Path:
    """The pinned build of *driver* on disk, downloading it if it is not there.

    Raises:
        ExecutionError: ERR-DRIVER-PLATFORM-UNSUPPORTED for an unpublished
            platform, ERR-DRIVER-CHECKSUM-MISMATCH when a download or an
            already-cached file does not hash to the pin, ERR-DRIVER-TOO-LARGE
            when a download expands past the ceiling.
        httpx.HTTPError: The CDN could not be reached or refused the request.
    """
    triplet = resolve_triplet(driver)
    destination = driver_cache_path(triplet)
    if not destination.exists():
        _download(triplet, destination)
    _verify_library(triplet, destination)
    return destination


class _HashingSource(io.RawIOBase):
    """A readable over the response body that hashes every byte it hands out.

    Gives the decompressor a bounded read while keeping the hash over the
    compressed bytes, which is what the CDN serves and ``checksums.rs`` records.
    """

    def __init__(self, chunks: Iterator[bytes], hasher: hashlib._Hash) -> None:
        super().__init__()
        self._chunks = chunks
        self._hasher = hasher
        self._buffer = b""

    def readable(self) -> bool:
        return True

    def readinto(
        self,
        buffer: Any,  # type-state: explicit_any — typeshed's writable-buffer alias is not importable at runtime
        /,
    ) -> int:
        view = memoryview(buffer)
        if not self._buffer:
            self._buffer = next(self._chunks, b"")
            self._hasher.update(self._buffer)
        taken, self._buffer = self._buffer[: len(view)], self._buffer[len(view) :]
        view[: len(taken)] = taken
        return len(taken)

    def drain(self) -> None:
        """Hash the chunks not yet pulled, so the verdict covers the whole body.

        The buffered remainder is already hashed: ``readinto`` hashes a chunk when
        it pulls it, before handing any of it out.
        """
        self._buffer = b""
        for chunk in self._chunks:
            self._hasher.update(chunk)


def _download(triplet: DriverTriplet, destination: Path) -> None:
    """Stream, hash, decompress and atomically install *triplet*.

    Output is read in bounded blocks, so a hostile body costs at most one block
    of memory beyond the disk ceiling. Every byte is hashed even after
    decompression has given up: a body that is not an archive is a checksum
    mismatch. The temporary file is a sibling of the destination (the rename
    cannot cross a filesystem) and is removed on every failure path.
    """
    expected = CDN_DRIVER_CHECKSUMS[
        (triplet.driver, triplet.os, triplet.arch, triplet.version)
    ]
    url = driver_url(triplet)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.parent / f".{secrets.token_urlsafe(12)}.download"
    hasher = hashlib.sha256()
    library_hasher = hashlib.sha256()
    # Kept apart because the verdicts differ: bytes that will not expand read as
    # a checksum mismatch (a proxy's login page), while bytes that expand too far
    # may be the pinned artifact and need a code of their own.
    archive_error: Exception | None = None
    oversized = False
    written = 0
    try:
        with (
            staged.open("xb") as handle,
            httpx.stream(
                "GET", url, timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True
            ) as response,
        ):
            response.raise_for_status()
            source = _HashingSource(response.iter_bytes(), hasher)
            reader = zstandard.ZstdDecompressor().stream_reader(
                io.BufferedReader(source)
            )
            while True:
                try:
                    block = reader.read(_READ_BLOCK_BYTES)
                except zstandard.ZstdError as error:
                    archive_error = error
                    break
                else:
                    if not block:
                        break
                    written += len(block)
                    if written > MAX_DECOMPRESSED_BYTES:
                        oversized = True
                        break
                    library_hasher.update(block)
                    handle.write(block)
            source.drain()
            # Durable before the rename: the cache predicate is existence alone,
            # so a truncated file would otherwise be a hit for ever.
            handle.flush()
            os.fsync(handle.fileno())
        actual = hasher.hexdigest()
        if actual != expected:
            raise ExecutionError.from_code(
                ERR_DRIVER_CHECKSUM_MISMATCH,
                subject=url,
                actual=actual,
                expected=expected,
                outcome="The download was discarded.",
            )
        if oversized:
            raise ExecutionError.from_code(
                ERR_DRIVER_TOO_LARGE, url=url, ceiling=MAX_DECOMPRESSED_BYTES
            )
        if archive_error is not None:
            raise archive_error
        library = library_hasher.hexdigest()
        library_expected = CDN_LIBRARY_CHECKSUMS[
            (triplet.driver, triplet.os, triplet.arch, triplet.version)
        ]
        if library != library_expected:
            raise ExecutionError.from_code(
                ERR_DRIVER_CHECKSUM_MISMATCH,
                subject=f"The library unpacked from {url}",
                actual=library,
                expected=library_expected,
                outcome="The download was discarded.",
            )
        # 0644: the cache is shared with dbt and may be shared between users.
        staged.chmod(0o644)
        if destination.exists():
            # Another process won the race; on Windows replacing a file it has
            # already `dlopen`ed raises PermissionError.
            return
        staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)
