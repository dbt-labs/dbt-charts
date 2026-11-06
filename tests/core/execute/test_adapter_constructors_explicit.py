"""FR-001 + FR-002 regression: build_exec_env requires a project —
no cwd or env fallback."""

from __future__ import annotations

import pytest

from dbt_charts.core.execute.env import build_exec_env


class TestBuildExecEnvRequiresProjectRoot:
    def test_omitting_project_raises_typeerror(self) -> None:
        with pytest.raises(TypeError):
            build_exec_env()  # type: ignore[call-arg]
