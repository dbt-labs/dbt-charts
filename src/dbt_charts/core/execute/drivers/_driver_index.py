"""The driver data: one row per CDN-served driver, one checksum per published build.

Stage: EXECUTE

Lake compute has no ADBC driver on PyPI: dbt v2 publishes it on
``public.cdn.getdbt.com``, and ``_fetch.py`` downloads it on first use or reuses
the copy dbt already cached.

The pin is derived: :func:`pinned_version` reads it off the checksum rows, and a
test asserts each driver has exactly one. The checksum table is also the platform
index: which manylinux label applies is answered by which row exists, and a
machine with no row is an error before any socket opens.

Bumping a pin means replacing that driver's rows wholesale from ``dbt-adbc/src/lib.rs``
(version) and ``dbt-adbc/src/checksums.rs`` (rows) in a ``dbt-labs/fs`` checkout.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CdnDriver:
    """What is true of one CDN driver beyond its checksums."""

    # The init symbol the driver library exports. dbt's Go builds export the
    # standard `AdbcDriverInit`; the Rust lake compute build does not.
    entrypoint: str
    # The statement option that reports non-fatal backend warnings, or None for
    # a driver with no such channel.
    warnings_option: str | None


CDN_DRIVERS: dict[str, CdnDriver] = {
    "dbt": CdnDriver(
        entrypoint="AdbcDriverDbtInit",
        warnings_option="adbc.dbt.last_warnings",
    ),
}

# (driver, os, arch, version) → SHA-256 of the *compressed* artifact the CDN
# serves, which is what the fetcher hashes as it streams. Copied verbatim from
# `dbt-adbc/src/checksums.rs` for the pinned version.
CDN_DRIVER_CHECKSUMS: dict[tuple[str, str, str, str], str] = {
    (
        "dbt",
        "apple-darwin",
        "aarch64",
        "0.4.0+dbt0.1.12.gc55396d",
    ): "d71943c4a88d7361a55baa726ca6180d3336ab7effe1ccd55b74af95a0095fb8",
    (
        "dbt",
        "apple-darwin",
        "x86_64",
        "0.4.0+dbt0.1.12.gc55396d",
    ): "ef0c46beebeed9f3b5b3037ae0c8c0a28b4960ca3014a4b85a73142abf41d296",
    (
        "dbt",
        "manylinux_2_28-linux-gnu",
        "aarch64",
        "0.4.0+dbt0.1.12.gc55396d",
    ): "420627ea3e0600b3b7aaf772dad71936f92550423e488e23f8927213c42614e0",
    (
        "dbt",
        "manylinux_2_28-linux-gnu",
        "x86_64",
        "0.4.0+dbt0.1.12.gc55396d",
    ): "f5d149d6179fee5aeec87c40a257a4c8b939264b00c31c580105b352074f2007",
    (
        "dbt",
        "pc-windows-msvc",
        "x86_64",
        "0.4.0+dbt0.1.12.gc55396d",
    ): "d0987056bd14ba41690b064b04a6d26f5c7c3279388dcb12d4254401364584ae",
}

# Same keys → SHA-256 of the decompressed library, which is what gets `dlopen`ed
# and what is re-checked against a file already in the cache. dbt publishes no
# such digest: each was derived by downloading the artifact, verifying it against
# the compressed pin above, decompressing it with `_fetch._download`, and hashing
# the result. The macOS arm64 value also matches the file dbt v2 itself cached.
CDN_LIBRARY_CHECKSUMS: dict[tuple[str, str, str, str], str] = {
    (
        "dbt",
        "apple-darwin",
        "aarch64",
        "0.4.0+dbt0.1.12.gc55396d",
    ): "d6fe7fe7836ca63549a3cba9d884fa9118d6340053172f258e1b3a24ddce6567",
    (
        "dbt",
        "apple-darwin",
        "x86_64",
        "0.4.0+dbt0.1.12.gc55396d",
    ): "b4b02f6d9137a5db235d3f14b8a5d008b5eff824b704febc02568289ff9374ab",
    (
        "dbt",
        "manylinux_2_28-linux-gnu",
        "aarch64",
        "0.4.0+dbt0.1.12.gc55396d",
    ): "6445ab6d50dbd151e75250390856cddeefd0cfada719852c2facfd1c94779e57",
    (
        "dbt",
        "manylinux_2_28-linux-gnu",
        "x86_64",
        "0.4.0+dbt0.1.12.gc55396d",
    ): "2e7cec132bdc82f9097ec014cbfd45df507260adafb64dbbaafb56cd28a4bd27",
    (
        "dbt",
        "pc-windows-msvc",
        "x86_64",
        "0.4.0+dbt0.1.12.gc55396d",
    ): "69696c47625689ec1c196e17d2c3d2bbdee5f1a9ff6d7b58e7765600b582b14c",
}

CDN_HOST = "public.cdn.getdbt.com"

# dbt's own global HTTP deadline for a driver download (`DRIVER_DOWNLOAD_TIMEOUT`
# in `dbt-adbc/src/install.rs`). The artifacts are a few MB compressed, so this
# bounds a stalled transfer rather than a slow one.
DOWNLOAD_TIMEOUT_SECONDS = 60

# A ceiling on what one download may expand to. A pinned SHA-256 says nothing
# about that, and the expansion necessarily happens before the hash can be
# compared — so without this a response that decompresses without bound fills
# the disk before anything gets to reject it. The largest driver dbt publishes
# is ~112 MB unpacked, so this is roughly 4x headroom rather than a tight fit.
MAX_DECOMPRESSED_BYTES = 512 * 1024 * 1024


def pinned_version(driver: str) -> str:
    """The one version of *driver* this build of dbt charts will run.

    Raises:
        KeyError: The table holds anything other than exactly one version for
            *driver* — no rows at all, or two pins that disagree.
    """
    versions = {
        version for (name, _, _, version) in CDN_DRIVER_CHECKSUMS if name == driver
    }
    if len(versions) != 1:
        raise KeyError(
            f"{driver!r} has {len(versions)} pinned versions in CDN_DRIVER_CHECKSUMS; "
            f"exactly one row set per driver is the contract"
        )
    return versions.pop()
