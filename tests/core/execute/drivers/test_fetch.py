"""The CDN fetcher: one pinned build per driver, never an unverified byte.

dbt v2's lake compute driver has no ADBC wheel on PyPI. dbt publishes it on its
own CDN, and a machine that has run dbt v2 already holds it, so dbt charts
resolves the same path to the same file. What
it does *not* copy from dbt is dbt's tolerance for a triplet with no checksum
row: there, an unknown platform means an unverified download. Here it is an
error raised before any socket opens.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dbt_charts.core.diagnostics.codes_execute import (
    ERR_DRIVER_CHECKSUM_MISMATCH,
    ERR_DRIVER_PLATFORM_UNSUPPORTED,
    ERR_DRIVER_TOO_LARGE,
)
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.drivers._driver_index import (
    CDN_DRIVER_CHECKSUMS,
    CDN_DRIVERS,
    CDN_LIBRARY_CHECKSUMS,
    DOWNLOAD_TIMEOUT_SECONDS,
    pinned_version,
)
from dbt_charts.core.execute.drivers._fetch import (
    DriverTriplet,
    driver_cache_path,
    driver_path,
    driver_url,
    resolve_triplet,
)


def test_importing_the_seam_downloads_nothing() -> None:
    """The driver arrives on first `open_lakecompute`, never at import or install
    time. A fresh interpreter, because a module cached by another test would
    prove nothing about import."""
    program = (
        "import httpx\n"
        "def boom(*args, **kwargs):\n"
        "    raise AssertionError('download at import')\n"
        "httpx.stream = boom\n"
        "import dbt_charts.core.execute.drivers\n"
        "import dbt_charts.core.execute.drivers._fetch\n"
    )
    subprocess.run([sys.executable, "-I", "-c", program], check=True)


def _triplet(driver: str, os_label: str, arch: str) -> DriverTriplet:
    return DriverTriplet(
        driver=driver, os=os_label, arch=arch, version=pinned_version(driver)
    )


class TestTheIndexIsTheSingleHomeForDriverData:
    def test_each_driver_has_exactly_one_pinned_version(self) -> None:
        """The pin is *derived* from the table rather than restated beside it.

        dbt keeps the two apart (`lib.rs` + `checksums.rs`) and has to keep them
        in step by hand; a second dict here would be a second home that can
        disagree with the first.
        """
        for driver in CDN_DRIVERS:
            versions = {
                version
                for (name, _, _, version) in CDN_DRIVER_CHECKSUMS
                if name == driver
            }
            assert len(versions) == 1, f"{driver} has {len(versions)} pinned versions"

    def test_every_driver_the_code_asks_for_has_rows(self) -> None:
        """`CDN_DRIVERS` is derived from the table, so comparing the two would
        be tautological. What is worth pinning is that the set the rest of the
        codebase names — the Dockerfile prefetch, the dispatch — is exactly the
        set with a pin."""
        assert set(CDN_DRIVERS) == {"dbt"}
        for driver in CDN_DRIVERS:
            assert pinned_version(driver)

    def test_a_driver_row_names_its_entry_point_and_warnings_channel(self) -> None:
        """Callers never name the symbol or the option: `open_cdn_driver` reads
        both off the row, so a second driver is one row, not a second code path."""
        row = CDN_DRIVERS["dbt"]
        assert row.entrypoint == "AdbcDriverDbtInit"
        assert row.warnings_option == "adbc.dbt.last_warnings"

    def test_every_checksum_is_a_sha256_digest(self) -> None:
        for digest in CDN_DRIVER_CHECKSUMS.values():
            assert len(digest) == 64
            assert digest == digest.lower()
            bytes.fromhex(digest)


class TestUrlAndCachePathPerPlatform:
    """Every row in the table is a platform dbt publishes for; each one has to
    produce a URL that exists and a cache path dbt's own installer would write."""

    @pytest.mark.parametrize("row", sorted(CDN_DRIVER_CHECKSUMS))
    def test_url_names_the_published_artifact(
        self, row: tuple[str, str, str, str]
    ) -> None:
        driver, os_label, arch, version = row
        url = driver_url(DriverTriplet(driver, os_label, arch, version))
        suffix = {
            "apple-darwin": ".dylib",
            "pc-windows-msvc": ".dll",
        }.get(os_label, ".so")
        # The version is percent-encoded with `-`, `.` and `_` left literal, so
        # the `+` of `0.4.0+dbt0.1.12.gc55396d` — and only it — becomes %2B.
        assert "+" not in url
        assert url == (
            f"https://public.cdn.getdbt.com/fs/adbc/{driver}/adbc_driver_{driver}"
            f"-{version.replace('+', '%2B')}-{arch}-{os_label}{suffix}.zst"
        )

    def test_the_url_carries_no_lib_prefix_on_any_platform(self) -> None:
        for row in CDN_DRIVER_CHECKSUMS:
            assert "/libadbc_driver_" not in driver_url(DriverTriplet(*row))

    def test_cache_path_matches_dbts_layout(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(
            "dbt_charts.core.execute.drivers._fetch.user_cache_dir",
            lambda: str(tmp_path),
        )
        triplet = _triplet("dbt", "apple-darwin", "aarch64")
        assert driver_cache_path(triplet) == (
            tmp_path
            / "com.getdbt"
            / "adbc"
            / "aarch64-apple-darwin"
            / f"libadbc_driver_dbt-{triplet.version}.dylib"
        )

    def test_the_windows_filename_drops_the_lib_prefix(
        self, monkeypatch, tmp_path
    ) -> None:
        """`dll_prefix()` is empty on Windows and `lib` everywhere else. Getting
        this wrong points the cache lookup at a name dbt never wrote, so every
        run re-downloads."""
        monkeypatch.setattr(
            "dbt_charts.core.execute.drivers._fetch.user_cache_dir",
            lambda: str(tmp_path),
        )
        triplet = _triplet("dbt", "pc-windows-msvc", "x86_64")
        assert (
            driver_cache_path(triplet).name == f"adbc_driver_dbt-{triplet.version}.dll"
        )


class TestPlatformResolution:
    """The checksum table is the platform index, not a verification list: which
    manylinux label applies is answered by which row exists."""

    def test_linux_picks_the_manylinux_label_the_table_has_a_row_for(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("platform.machine", lambda: "x86_64")
        assert resolve_triplet("dbt").os == "manylinux_2_28-linux-gnu"

    def test_macos_arm64_resolves_to_the_rust_arch_spelling(self, monkeypatch) -> None:
        """`platform.machine()` says `arm64` on macOS and `AMD64` on Windows;
        the table is keyed by Rust's `env::consts::ARCH`, so an unmapped value
        would miss every row."""
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        triplet = resolve_triplet("dbt")
        assert (triplet.arch, triplet.os) == ("aarch64", "apple-darwin")

    def test_windows_amd64_resolves_to_x86_64(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr("platform.machine", lambda: "AMD64")
        triplet = resolve_triplet("dbt")
        assert (triplet.arch, triplet.os) == ("x86_64", "pc-windows-msvc")

    def test_an_unpublished_operating_system_is_refused_not_assumed_linux(
        self, monkeypatch
    ) -> None:
        """FreeBSD, AIX and Cygwin are not Linux, and a manylinux build will not
        load there. Treating every non-darwin, non-win32 platform as Linux finds
        a row and turns the module's stated contract into a ~10 MB download
        followed by an obscure dlopen failure."""
        for platform_name in ("freebsd14", "aix", "cygwin"):
            monkeypatch.setattr("sys.platform", platform_name)
            monkeypatch.setattr("platform.machine", lambda: "x86_64")
            with pytest.raises(ExecutionError) as excinfo:
                resolve_triplet("dbt")
            assert excinfo.value.code is ERR_DRIVER_PLATFORM_UNSUPPORTED
            assert platform_name in str(excinfo.value)

    def test_a_machine_with_no_row_errors_before_any_network_call(
        self, monkeypatch
    ) -> None:
        """dbt downloads unverified when a triplet has no checksum row
        (`Option<&str>`). Copying that would make an unknown platform a silent
        unsigned binary, so here it is a hard error — and it is raised by
        resolution, which cannot reach the network at all."""
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("platform.machine", lambda: "s390x")
        with pytest.raises(ExecutionError) as excinfo:
            resolve_triplet("dbt")
        assert excinfo.value.code is ERR_DRIVER_PLATFORM_UNSUPPORTED
        assert "s390x" in str(excinfo.value)

    def test_windows_on_arm_has_no_row_and_is_rejected(self, monkeypatch) -> None:
        """dbt publishes Windows for x86_64 only. The table says so, and
        resolution reads the table rather than assuming every arch exists."""
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr("platform.machine", lambda: "ARM64")
        with pytest.raises(ExecutionError) as excinfo:
            resolve_triplet("dbt")
        assert excinfo.value.code is ERR_DRIVER_PLATFORM_UNSUPPORTED


class TestCacheLookup:
    """dbt writes the version into the filename, so a cached build of another
    version is a miss. Loading it would run a binary we never pinned."""

    @pytest.fixture(autouse=True)
    def _cache_root(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "dbt_charts.core.execute.drivers._fetch.user_cache_dir",
            lambda: str(tmp_path),
        )
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")

    def test_a_cached_pin_is_used_without_downloading(self, monkeypatch) -> None:
        """The case a machine that has run `dbt run` is already in."""
        expected = driver_cache_path(resolve_triplet("dbt"))
        expected.parent.mkdir(parents=True)
        expected.write_bytes(b"not really a dylib")
        triplet = resolve_triplet("dbt")
        monkeypatch.setitem(
            CDN_LIBRARY_CHECKSUMS,
            (triplet.driver, triplet.os, triplet.arch, triplet.version),
            hashlib.sha256(b"not really a dylib").hexdigest(),
        )

        def _no_network(*args: object, **kwargs: object) -> None:
            raise AssertionError("a cache hit must not reach the network")

        monkeypatch.setattr("httpx.stream", _no_network)
        assert driver_path("dbt") == expected

    def test_a_cached_build_of_another_version_is_not_substituted(
        self, monkeypatch
    ) -> None:
        triplet = resolve_triplet("dbt")
        neighbor = driver_cache_path(
            DriverTriplet(
                triplet.driver, triplet.os, triplet.arch, "0.4.0+dbt0.1.11.g0000000"
            )
        )
        neighbor.parent.mkdir(parents=True)
        neighbor.write_bytes(b"a driver, but not ours")
        calls: list[str] = []

        def _record(method: str, url: str, **kwargs: object) -> None:
            calls.append(url)
            raise RuntimeError("stop here — the point is that we tried")

        monkeypatch.setattr("httpx.stream", _record)
        with pytest.raises(RuntimeError):
            driver_path("dbt")
        assert calls == [driver_url(triplet)]


class TestIndexPinsTheLibraryToo:
    def test_every_compressed_pin_has_a_library_pin(self) -> None:
        assert set(CDN_LIBRARY_CHECKSUMS) == set(CDN_DRIVER_CHECKSUMS)

    def test_every_library_pin_is_a_sha256_digest(self) -> None:
        for digest in CDN_LIBRARY_CHECKSUMS.values():
            assert len(digest) == 64
            bytes.fromhex(digest)


class TestCachedLibraryVerification:
    """A file already at the pinned path is checked against the decompressed pin
    before it is loaded, because anything able to write the shared cache could
    otherwise plant a library that `dlopen` runs."""

    @pytest.fixture(autouse=True)
    def _cache(self, monkeypatch, tmp_path: Path) -> Path:
        from dbt_charts.core.execute.drivers import _fetch

        monkeypatch.setattr(_fetch, "user_cache_dir", lambda: str(tmp_path))
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        self.body = b"the real library" * 100
        triplet = resolve_triplet("dbt")
        monkeypatch.setitem(
            CDN_LIBRARY_CHECKSUMS,
            (triplet.driver, triplet.os, triplet.arch, triplet.version),
            hashlib.sha256(self.body).hexdigest(),
        )
        self.path = driver_cache_path(triplet)
        self.path.parent.mkdir(parents=True)
        return tmp_path

    def test_correct_bytes_are_accepted(self) -> None:
        self.path.write_bytes(self.body)
        assert driver_path("dbt") == self.path

    def test_wrong_bytes_raise_naming_the_file_and_leave_it_alone(self) -> None:
        self.path.write_bytes(b"planted" * 100)
        with pytest.raises(ExecutionError) as excinfo:
            driver_path("dbt")
        assert excinfo.value.code is ERR_DRIVER_CHECKSUM_MISMATCH
        assert str(self.path) in str(excinfo.value)
        assert "delete" in str(excinfo.value).lower()
        assert self.path.read_bytes() == b"planted" * 100

    def test_a_truncated_file_raises(self) -> None:
        self.path.write_bytes(self.body[:-1])
        with pytest.raises(ExecutionError) as excinfo:
            driver_path("dbt")
        assert excinfo.value.code is ERR_DRIVER_CHECKSUM_MISMATCH

    def test_a_bad_file_is_never_replaced_by_a_download(self, monkeypatch) -> None:
        self.path.write_bytes(b"planted")

        def _no_network(*args: object, **kwargs: object) -> None:
            raise AssertionError("a bad cached file must not trigger a download")

        monkeypatch.setattr("httpx.stream", _no_network)
        with pytest.raises(ExecutionError):
            driver_path("dbt")

    def test_a_file_swapped_after_a_good_load_is_caught_on_the_next_open(
        self,
    ) -> None:
        self.path.write_bytes(self.body)
        stat = self.path.stat()
        driver_path("dbt")
        self.path.write_bytes(b"planted" * 100)
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with pytest.raises(ExecutionError):
            driver_path("dbt")


class TestDownloadVerification:
    @pytest.fixture(autouse=True)
    def _cache_root(self, monkeypatch, tmp_path: Path) -> Path:
        monkeypatch.setattr(
            "dbt_charts.core.execute.drivers._fetch.user_cache_dir",
            lambda: str(tmp_path),
        )
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        return tmp_path

    def test_a_checksum_mismatch_leaves_neither_the_temp_file_nor_the_target(
        self, monkeypatch
    ) -> None:
        import zstandard

        payload = zstandard.ZstdCompressor().compress(b"tampered driver bytes")
        monkeypatch.setattr("httpx.stream", _stream_returning(payload))
        destination = driver_cache_path(resolve_triplet("dbt"))
        with pytest.raises(ExecutionError) as excinfo:
            driver_path("dbt")
        assert excinfo.value.code is ERR_DRIVER_CHECKSUM_MISMATCH
        assert hashlib.sha256(payload).hexdigest() in str(excinfo.value)
        assert not destination.exists()
        assert list(destination.parent.iterdir()) == []

    def test_a_body_that_is_not_an_archive_is_a_checksum_mismatch(
        self, monkeypatch
    ) -> None:
        """The documented primary case for this code: a proxy answering 200 with
        an HTML login page. Decompression fails first, so without draining the
        rest of the body to finish the hash it surfaced as a raw ZstdError and
        the code its own docs describe was unreachable."""
        monkeypatch.setattr(
            "httpx.stream", _stream_returning(b"<html>captive portal</html>" * 8)
        )
        destination = driver_cache_path(resolve_triplet("dbt"))
        with pytest.raises(ExecutionError) as excinfo:
            driver_path("dbt")
        assert excinfo.value.code is ERR_DRIVER_CHECKSUM_MISMATCH
        assert not destination.exists()
        assert list(destination.parent.iterdir()) == []

    def test_a_decompression_bomb_is_stopped_before_it_fills_the_disk(
        self, monkeypatch
    ) -> None:
        """Nothing about a pinned checksum bounds what the bytes expand to, and
        the expansion happens before the hash can be compared.

        The payload is pinned to its *own* hash, so the checksum comparison
        passes and the ceiling is the only thing that can reject it — without
        that, this test goes green on the mismatch path whether the ceiling
        exists or not.
        """
        import zstandard

        payload = zstandard.ZstdCompressor().compress(b"\0" * (2 * 1024 * 1024))
        triplet = resolve_triplet("dbt")
        monkeypatch.setitem(
            CDN_DRIVER_CHECKSUMS,
            (triplet.driver, triplet.os, triplet.arch, triplet.version),
            hashlib.sha256(payload).hexdigest(),
        )
        monkeypatch.setattr(
            "dbt_charts.core.execute.drivers._fetch.MAX_DECOMPRESSED_BYTES", 1024
        )
        monkeypatch.setattr("httpx.stream", _stream_returning(payload))
        destination = driver_cache_path(triplet)
        with pytest.raises(ExecutionError) as excinfo:
            driver_path("dbt")
        assert excinfo.value.code is ERR_DRIVER_TOO_LARGE
        assert not destination.exists()
        assert list(destination.parent.iterdir()) == []

    def test_a_bomb_in_one_network_chunk_is_stopped_in_bounded_blocks(
        self, monkeypatch
    ) -> None:
        """The whole body arrives as one chunk, so only reading the output in
        bounded blocks keeps memory bounded as well as disk."""
        import contextlib

        import zstandard

        payload = zstandard.ZstdCompressor().compress(b"\0" * (8 * 1024 * 1024))
        triplet = resolve_triplet("dbt")
        monkeypatch.setitem(
            CDN_DRIVER_CHECKSUMS,
            (triplet.driver, triplet.os, triplet.arch, triplet.version),
            hashlib.sha256(payload).hexdigest(),
        )
        monkeypatch.setattr(
            "dbt_charts.core.execute.drivers._fetch.MAX_DECOMPRESSED_BYTES", 1024
        )
        monkeypatch.setattr(
            "dbt_charts.core.execute.drivers._fetch._READ_BLOCK_BYTES", 4096
        )

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def iter_bytes(self):
                yield payload

        @contextlib.contextmanager
        def _stream(method, url, **kwargs):
            yield _Response()

        monkeypatch.setattr("httpx.stream", _stream)
        with pytest.raises(ExecutionError) as excinfo:
            driver_path("dbt")
        assert excinfo.value.code is ERR_DRIVER_TOO_LARGE

    def _serve_in_chunks(self, monkeypatch, payload: bytes) -> None:
        """Pin *payload*'s own hash and serve it in 64 KB network chunks."""
        import contextlib

        triplet = resolve_triplet("dbt")
        monkeypatch.setitem(
            CDN_DRIVER_CHECKSUMS,
            (triplet.driver, triplet.os, triplet.arch, triplet.version),
            hashlib.sha256(payload).hexdigest(),
        )

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def iter_bytes(self):
                for start in range(0, len(payload), 65536):
                    yield payload[start : start + 65536]

        @contextlib.contextmanager
        def _stream(method, url, **kwargs):
            yield _Response()

        monkeypatch.setattr("httpx.stream", _stream)

    def test_a_pinned_multi_chunk_artifact_that_is_too_large_says_so(
        self, monkeypatch
    ) -> None:
        """Stopping mid-stream leaves unread bytes behind; the checksum must still
        cover each byte once, so the verdict is the size ceiling, not a mismatch."""
        import zstandard

        payload = zstandard.ZstdCompressor(level=1).compress(os.urandom(600_000))
        self._serve_in_chunks(monkeypatch, payload)
        monkeypatch.setattr(
            "dbt_charts.core.execute.drivers._fetch.MAX_DECOMPRESSED_BYTES", 100_000
        )
        with pytest.raises(ExecutionError) as excinfo:
            driver_path("dbt")
        assert excinfo.value.code is ERR_DRIVER_TOO_LARGE

    def test_the_download_is_durable_and_group_readable(self, monkeypatch) -> None:
        """fsync before the rename, because the cache predicate downstream is
        existence alone; 0644 because the cache is shared with dbt and may be
        shared between users."""
        import zstandard

        body = b"\x7fELF" * 64
        payload = zstandard.ZstdCompressor().compress(body)
        triplet = resolve_triplet("dbt")
        monkeypatch.setitem(
            CDN_DRIVER_CHECKSUMS,
            (triplet.driver, triplet.os, triplet.arch, triplet.version),
            hashlib.sha256(payload).hexdigest(),
        )
        monkeypatch.setitem(
            CDN_LIBRARY_CHECKSUMS,
            (triplet.driver, triplet.os, triplet.arch, triplet.version),
            hashlib.sha256(body).hexdigest(),
        )
        synced: list[int] = []
        monkeypatch.setattr(
            "dbt_charts.core.execute.drivers._fetch.os.fsync", synced.append
        )
        monkeypatch.setattr("httpx.stream", _stream_returning(payload))
        path = driver_path("dbt")
        assert synced, "the staged file was renamed without being flushed to disk"
        assert path.stat().st_mode & 0o777 == 0o644

    def test_the_request_is_bounded_by_the_shared_timeout(self, monkeypatch) -> None:
        import zstandard

        payload = zstandard.ZstdCompressor().compress(b"x")
        seen: dict[str, object] = {}
        stream = _stream_returning(payload)

        def _record(method: str, url: str, **kwargs: object):
            seen.update(kwargs)
            return stream(method, url, **kwargs)

        monkeypatch.setattr("httpx.stream", _record)
        with pytest.raises(ExecutionError):
            driver_path("dbt")
        assert seen["timeout"] == DOWNLOAD_TIMEOUT_SECONDS

    def test_a_verified_download_is_decompressed_and_renamed_into_place(
        self, monkeypatch
    ) -> None:
        """The checksum is taken over the *compressed* bytes — what the CDN
        serves — and the decompressed library is what lands."""
        import zstandard

        body = b"\x7fELF and then some" * 32
        payload = zstandard.ZstdCompressor().compress(body)
        triplet = resolve_triplet("dbt")
        monkeypatch.setitem(
            CDN_DRIVER_CHECKSUMS,
            (triplet.driver, triplet.os, triplet.arch, triplet.version),
            hashlib.sha256(payload).hexdigest(),
        )
        monkeypatch.setitem(
            CDN_LIBRARY_CHECKSUMS,
            (triplet.driver, triplet.os, triplet.arch, triplet.version),
            hashlib.sha256(body).hexdigest(),
        )
        monkeypatch.setattr("httpx.stream", _stream_returning(payload))
        path = driver_path("dbt")
        assert path.read_bytes() == body
        assert list(path.parent.iterdir()) == [path]


def _stream_returning(payload: bytes):
    """A stand-in for ``httpx.stream`` that serves *payload* in small chunks."""
    import contextlib

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, chunk_size: int | None = None):
            for start in range(0, len(payload), 8):
                yield payload[start : start + 8]

    @contextlib.contextmanager
    def _stream(method: str, url: str, **kwargs: object):
        yield _Response()

    return _stream


_LIVE = pytest.mark.skipif(
    not os.environ.get("DCT_NETWORK_TESTS"),
    reason="set DCT_NETWORK_TESTS=1 to reach public.cdn.getdbt.com",
)


@_LIVE
def test_the_pinned_build_really_is_on_the_cdn(monkeypatch, tmp_path: Path) -> None:
    """The URL resolves, and the bytes hash to the pin.

    Everything else about the fetcher is arithmetic on strings; this is what
    asserts the strings name something dbt actually published, and that the
    checksum is taken over the shape the CDN really serves.
    """
    monkeypatch.setattr(
        "dbt_charts.core.execute.drivers._fetch.user_cache_dir", lambda: str(tmp_path)
    )
    path = driver_path("dbt")
    assert path.exists()
    assert path.stat().st_size > 1_000_000
    assert driver_path("dbt") == path


@_LIVE
@pytest.mark.parametrize("row", sorted(CDN_DRIVER_CHECKSUMS))
def test_every_platform_row_names_a_published_artifact(
    row: tuple[str, str, str, str],
) -> None:
    """A HEAD per row, so a wrong URL for a platform this machine is not on
    fails here rather than on the first user who runs it there.

    The test above covers the bytes; this covers the other fourteen names.
    """
    import httpx

    response = httpx.head(
        driver_url(DriverTriplet(*row)), timeout=30, follow_redirects=True
    )
    assert response.status_code == 200, driver_url(DriverTriplet(*row))


def test_draining_hashes_every_byte_exactly_once() -> None:
    """A partial read leaves bytes buffered that `readinto` already hashed; draining
    must not hash them again."""
    from dbt_charts.core.execute.drivers._fetch import _HashingSource

    chunks = [os.urandom(1000) for _ in range(5)]
    hasher = hashlib.sha256()
    source = _HashingSource(iter(chunks), hasher)
    assert len(source.read(100)) == 100
    source.drain()
    assert hasher.digest() == hashlib.sha256(b"".join(chunks)).digest()


@_LIVE
def test_the_library_pins_re_derive_from_the_cdn(tmp_path: Path) -> None:
    """Download each pinned artifact, verify it against the compressed pin,
    decompress it with the fetcher's own code, and compare the decompressed hash
    with CDN_LIBRARY_CHECKSUMS. This is how the pins were derived."""
    from dbt_charts.core.execute.drivers._fetch import _download

    for row in sorted(CDN_LIBRARY_CHECKSUMS):
        destination = tmp_path / "-".join(row[1:3])
        # `_download` itself raises unless both the compressed and the
        # decompressed digests match their pins.
        _download(DriverTriplet(*row), destination)
        assert (
            hashlib.sha256(destination.read_bytes()).hexdigest()
            == CDN_LIBRARY_CHECKSUMS[row]
        )
