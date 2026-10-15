"""`DCT_PREBUILT_WHEEL` hands the wheel tests a wheel built elsewhere (CI's engine job)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from ..conftest import dbt_charts_wheel


def _no_build(*args: Any, **kwargs: Any) -> None:
    raise AssertionError(f"built a wheel despite DCT_PREBUILT_WHEEL: {args}")


def test_prebuilt_wheel_is_used_without_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = tmp_path / "dbt_charts-1.0.0-cp311-abi3-manylinux_2_17_x86_64.whl"
    wheel.write_bytes(b"")
    monkeypatch.setenv("DCT_PREBUILT_WHEEL", str(wheel))
    monkeypatch.setattr(subprocess, "run", _no_build)

    assert dbt_charts_wheel(tmp_path / "out") == wheel


def test_missing_prebuilt_wheel_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DCT_PREBUILT_WHEEL", str(tmp_path / "absent.whl"))
    monkeypatch.setattr(subprocess, "run", _no_build)

    with pytest.raises(FileNotFoundError, match="DCT_PREBUILT_WHEEL"):
        dbt_charts_wheel(tmp_path / "out")
