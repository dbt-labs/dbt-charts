from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

from ._paths import DBT_CHARTS_DIR


def test_git_init_writes_no_hook_samples(tmp_path: Path) -> None:
    template_dir = os.environ.get("GIT_TEMPLATE_DIR")

    assert template_dir
    assert list(Path(template_dir).iterdir()) == []

    result = subprocess.run(
        ["git", "init"], cwd=tmp_path, text=True, capture_output=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert list((tmp_path / ".git" / "hooks").glob("*.sample")) == []


def test_xdist_workers_are_capped() -> None:
    pyproject = tomllib.loads((DBT_CHARTS_DIR / "pyproject.toml").read_text("utf-8"))

    assert "--maxprocesses=6" in pyproject["tool"]["pytest"]["ini_options"]["addopts"]
