"""Compose the `dct --version` line: version, install path, Python, editable flag."""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import dbt_charts


@dataclass(frozen=True)
class VersionInfo:
    version: str
    package_dir: Path
    python_version: str
    python_executable: str
    editable: bool

    def render(self) -> str:
        editable = " (editable)" if self.editable else ""
        return (
            f"dct {self.version} from {self.package_dir}{editable} "
            f"(Python {self.python_version}, {self.python_executable})"
        )


def _read_direct_url_json() -> str | None:
    try:
        return metadata.distribution("dbt-charts").read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None


def _is_editable() -> bool:
    raw = _read_direct_url_json()
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return bool(data.get("dir_info", {}).get("editable"))


def collect() -> VersionInfo:
    return VersionInfo(
        version=dbt_charts.__version__,
        package_dir=Path(dbt_charts.__file__).parent,
        python_version=platform.python_version(),
        python_executable=sys.executable,
        editable=_is_editable(),
    )
