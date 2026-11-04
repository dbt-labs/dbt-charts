"""FR-001 + FR-002 regression: build_adapter_registry requires a project —
no cwd or env fallback."""

from __future__ import annotations

import pytest

from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry


class TestBuildAdapterRegistryRequiresProjectRoot:
    def test_omitting_project_raises_typeerror(self) -> None:
        with pytest.raises(TypeError):
            build_adapter_registry()  # type: ignore[call-arg]
