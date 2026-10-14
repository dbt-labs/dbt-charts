"""PEP 517 backend: maturin plus the two things it cannot do.

maturin has no VCS version source and cannot reach packages outside the project
root, so this wraps its hooks to stamp the git-tag version and to ship the
vendored `mdsvg` and `d3_format` peer packages.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import io
import os
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

# Build-environment-only dependencies, absent from the dev venv, so imported
# dynamically to keep the module type-clean.
maturin: Any = importlib.import_module("maturin")
setuptools_scm: Any = importlib.import_module("setuptools_scm")


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT
NEVER_SHIPPED = frozenset({".DS_Store", ".git", "__pycache__"})
VENDORED = {
    "mdsvg": REPO_ROOT / "libs/markdown-svg/src/mdsvg",
    "d3_format": REPO_ROOT / "libs/d3-format/src/d3_format",
}

os.environ.setdefault("MATURIN_NO_MISSING_BUILD_BACKEND_WARNING", "1")

get_requires_for_build_wheel = maturin.get_requires_for_build_wheel
get_requires_for_build_editable = maturin.get_requires_for_build_editable


def _record_line(path: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return f"{path},sha256={digest.decode()},{len(data)}"


def _require_vendored_sources() -> None:
    missing = [str(src) for src in VENDORED.values() if not src.is_dir()]
    if missing:
        raise FileNotFoundError(f"vendored package sources not found: {missing}")


def _stamp_version(text: str, old_version: str, version: str) -> str:
    old, new = f"\nVersion: {old_version}\n", f"\nVersion: {version}\n"
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one {old.strip()!r} line in METADATA")
    return text.replace(old, new)


def _version() -> str:
    # Only `dbt-charts-vX.Y.Z` tags count: sibling packages tag this repo too.
    version: str = setuptools_scm.get_version(
        root=str(REPO_ROOT),
        git_describe_command=[
            "git",
            "describe",
            "--dirty",
            "--tags",
            "--long",
            "--match",
            "dbt-charts-v[0-9]*.[0-9]*.[0-9]*",
        ],
        local_scheme="no-local-version",
        fallback_version="0.0.0",
    )
    return version


def _restamp(dist_info: str, version: str) -> tuple[str, str]:
    """Return (old version, dist-info name carrying `version`)."""
    old_version = dist_info.removesuffix(".dist-info").split("-", 1)[1]
    return old_version, dist_info.replace(old_version, version)


def _finalize(wheel: Path, *, editable: bool) -> str:
    """Rewrite the built wheel to carry the tag version and the vendored packages."""
    _require_vendored_sources()
    version = _version()
    with zipfile.ZipFile(wheel) as zin:
        infos = {info.filename: info for info in zin.infolist()}
        entries = {name: zin.read(info) for name, info in infos.items()}
    old_dist_info = next(n for n in entries if n.endswith(".dist-info/WHEEL")).split(
        "/"
    )[0]
    old_version, new_dist_info = _restamp(old_dist_info, version)

    files: dict[str, bytes] = {}
    for name, data in entries.items():
        if name == f"{old_dist_info}/RECORD":
            continue
        original = name
        name = name.replace(old_dist_info, new_dist_info, 1)
        # Keep maturin's mode bits and fixed timestamps (reproducible builds).
        infos[original].filename = name
        if name == f"{new_dist_info}/METADATA":
            data = _stamp_version(data.decode(), old_version, version).encode()
        files[name] = data

    if editable:
        pth = next(n for n in files if n.endswith(".pth"))
        paths = [
            files[pth].decode().strip(),
            *(str(src.parent) for src in VENDORED.values()),
        ]
        files[pth] = ("\n".join(paths) + "\n").encode()
    else:
        for package, src in VENDORED.items():
            for path in sorted(p for p in src.rglob("*") if p.is_file()):
                relative = path.relative_to(src)
                if NEVER_SHIPPED.intersection(relative.parts):
                    continue
                files[f"{package}/{relative.as_posix()}"] = path.read_bytes()

    record = [_record_line(name, data) for name, data in files.items()]
    record.append(f"{new_dist_info}/RECORD,,")
    files[f"{new_dist_info}/RECORD"] = ("\n".join(record) + "\n").encode()

    target = wheel.with_name(wheel.name.replace(old_version, version, 1))
    wheel.unlink()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zout:
        by_name = {info.filename: info for info in infos.values()}
        for name, data in files.items():
            zout.writestr(by_name.get(name, name), data)
    return target.name


def build_sdist(
    sdist_directory: str, config_settings: dict[str, str] | None = None
) -> str:
    """Build maturin's sdist, renamed and PKG-INFO-stamped with the tag version."""
    _require_vendored_sources()
    version = _version()
    built = Path(sdist_directory) / maturin.build_sdist(
        sdist_directory, config_settings
    )
    old_prefix = built.name.removesuffix(".tar.gz")
    old_version = old_prefix.split("-", 1)[1]
    new_prefix = old_prefix.replace(old_version, version, 1)
    target = built.with_name(f"{new_prefix}.tar.gz")
    staged = built.with_name(f"{new_prefix}.tar.gz.tmp")
    with tarfile.open(built) as tin, tarfile.open(staged, "w:gz") as tout:
        for member in tin:
            data = tin.extractfile(member) if member.isfile() else None
            member.name = member.name.replace(old_prefix, new_prefix, 1)
            if member.name == f"{new_prefix}/PKG-INFO" and data:
                text = _stamp_version(data.read().decode(), old_version, version)
                data = io.BytesIO(text.encode())
                member.size = len(text.encode())
            tout.addfile(member, data)
    staged.replace(target)
    if built != target:
        built.unlink()
    return target.name


def _prepare_metadata(dist_info: str, metadata_directory: str) -> str:
    """Stamp maturin's metadata-only dist-info so it matches the built wheel."""
    _require_vendored_sources()
    version = _version()
    old_version, new_dist_info = _restamp(dist_info, version)
    source = Path(metadata_directory) / dist_info
    metadata = source / "METADATA"
    metadata.write_text(_stamp_version(metadata.read_text(), old_version, version))
    source.rename(source.with_name(new_dist_info))
    return new_dist_info


def prepare_metadata_for_build_wheel(
    metadata_directory: str, config_settings: dict[str, str] | None = None
) -> str:
    return _prepare_metadata(
        maturin.prepare_metadata_for_build_wheel(metadata_directory, config_settings),
        metadata_directory,
    )


def prepare_metadata_for_build_editable(
    metadata_directory: str, config_settings: dict[str, str] | None = None
) -> str:
    return _prepare_metadata(
        maturin.prepare_metadata_for_build_editable(
            metadata_directory, config_settings
        ),
        metadata_directory,
    )


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, str] | None = None,
    metadata_directory: str | None = None,
) -> str:
    name = maturin.build_wheel(wheel_directory, config_settings, None)
    return _finalize(Path(wheel_directory) / name, editable=False)


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, str] | None = None,
    metadata_directory: str | None = None,
) -> str:
    # Editable installs are local dev builds only: skip the release profile's
    # fat LTO (see [profile.editable] in libs/chart-svg/python/Cargo.toml).
    build_args = maturin.get_maturin_pep517_args(config_settings)
    settings: dict[str, Any] = {
        k: v
        for k, v in (config_settings or {}).items()
        if k not in ("build-args", "maturin.build-args")
    }
    settings["build-args"] = ["--profile", "editable", *build_args]
    name = maturin.build_editable(wheel_directory, settings, None)
    return _finalize(Path(wheel_directory) / name, editable=True)


if __name__ == "__main__":
    # Release CI builds the platform wheels with maturin-action (manylinux
    # containers); this gives them the stamped version and vendored packages
    # build_wheel would have.
    for built in sys.argv[1:]:
        print(_finalize(Path(built), editable=False))
