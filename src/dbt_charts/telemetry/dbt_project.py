"""Read the dbt project file telemetry consent and project identity come from."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def read_dbt_project(project_dir: Path) -> dict[str, Any] | None:
    """Return ``dbt_project.yml`` as a mapping, or None when there is none."""
    path = project_dir / "dbt_project.yml"
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return data
