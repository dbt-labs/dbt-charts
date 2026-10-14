"""Regression: slow test families must override the repo-wide 30s timeout.

`timeout = 30` in root pyproject.toml kills tests that legitimately run
longer on a loaded machine (wheel build + venv install). Those families
carry a module-level `pytest.mark.timeout` override; this test protects
that override from being silently dropped.
"""

from __future__ import annotations

from . import test_oss_install_smoke, test_postgres_explain_check


def test_oss_install_smoke_has_extended_timeout() -> None:
    marks = test_oss_install_smoke.pytestmark
    timeout_marks = [m for m in marks if m.name == "timeout"]
    assert timeout_marks, "test_oss_install_smoke module must carry a timeout mark"
    (seconds,) = timeout_marks[0].args
    assert seconds > 30, "wheel build + venv install exceeds the repo-wide 30s timeout"


def test_oss_install_smoke_still_carries_xdist_group() -> None:
    marks = test_oss_install_smoke.pytestmark
    xdist_marks = [m for m in marks if m.name == "xdist_group"]
    assert xdist_marks, (
        "test_oss_install_smoke module must stay in the dbt_charts_wheel group"
    )
    assert xdist_marks[0].args == ("dbt_charts_wheel",)


def test_postgres_explain_check_has_extended_timeout() -> None:
    marks = test_postgres_explain_check.pytestmark
    timeout_marks = [m for m in marks if m.name == "timeout"]
    assert timeout_marks, "test_postgres_explain_check must carry a timeout mark"
    (seconds,) = timeout_marks[0].args
    assert seconds > 30, (
        "cluster boot (initdb + pg_ctl + readiness) can exceed the repo-wide "
        "30s timeout"
    )


def test_postgres_explain_check_still_carries_xdist_group() -> None:
    marks = test_postgres_explain_check.pytestmark
    xdist_marks = [m for m in marks if m.name == "xdist_group"]
    assert xdist_marks, "cluster tests must share one worker"
    assert xdist_marks[0].args == ("postgres_cluster",)
