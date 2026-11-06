"""Phase 0: TDD seeds pinning the new Cat B function signatures.

One positive test per migrated function. Behavioral pins — no deletion
assertions (`pytest.raises(TypeError, match="old_param")`) per
dbt_charts/AGENTS.md.
"""

import pytest


def test_render_dashboard_requires_project() -> None:
    """render_dashboard's `project: Project` is required (no None allowed).

    Pre-existing `**render_options` swallows unknown kwargs (e.g. legacy
    `project_dir=`), so a rejection-by-name test isn't reliable. Pin the
    positive shape: omitting `project=` raises.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.project import InMemoryBoard

    with pytest.raises(TypeError, match="project"):
        render_dashboard(  # type: ignore[call-arg]
            board=InMemoryBoard("charts: {}", path=None),
            env=None,  # type: ignore[arg-type]
            result_cache=None,
        )
