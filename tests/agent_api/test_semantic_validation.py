"""Static validation of semantic SQL compiles it with MetricFlow, never runs it,
and names the declared object a mistyped one was probably meant to be."""

from __future__ import annotations

import importlib.util
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from dbt_charts.agent_api import ProjectSession
from dbt_charts.core.execute import run as run_stage

from .._paths import DBT_CHARTS_DIR

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("metricflow_sql") is None,
    reason="metricflow-sql is not installed",
)


@pytest.fixture
def session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ProjectSession]:
    shutil.copytree(
        DBT_CHARTS_DIR / "tests" / "fixtures" / "metricflow-billing",
        tmp_path,
        dirs_exist_ok=True,
    )

    def no_warehouse(*args: object, **kwargs: object) -> None:
        raise AssertionError("validation must not reach the warehouse")

    monkeypatch.setattr(run_stage, "run_route", no_warehouse)
    with ProjectSession.open(tmp_path) as opened:
        yield opened


def _messages(session: ProjectSession, sql: str, source: str = "db") -> list[str]:
    return [d.message for d in session.validate_query(sql, source=source)]


def test_valid_semantic_sql_passes_without_touching_the_warehouse(
    session: ProjectSession,
) -> None:
    sql = (
        "SELECT account.industry AS industry, AGG(invoiced_total) AS total\n"
        "FROM invoices GROUP BY 1"
    )

    assert session.validate_query(sql, source="db") == []


def test_misspelled_calculation_suggests_the_declared_one(
    session: ProjectSession,
) -> None:
    (diagnostic,) = session.validate_query(
        "SELECT AGG(invoiced_totl) FROM invoices", source="db"
    )

    assert diagnostic.severity == "error"
    assert "Column 'invoiced_totl' could not be resolved" in diagnostic.message
    assert "Did you mean `invoiced_total`?" in diagnostic.message


def test_unknown_attribute_suggests_the_relationship_path(
    session: ProjectSession,
) -> None:
    (message,) = _messages(
        session,
        "SELECT account.industri, AGG(invoiced_total) FROM invoices GROUP BY 1",
    )

    assert "Unknown attribute" in message
    assert "Did you mean `account.industry`?" in message


def test_a_bare_attribute_suggests_the_path_that_reaches_it(
    session: ProjectSession,
) -> None:
    (message,) = _messages(
        session, "SELECT industry, AGG(invoiced_total) FROM invoices GROUP BY 1"
    )

    assert "Did you mean `account.industry`?" in message


def test_misspelled_constellation_role_suggests_the_declared_one(
    session: ProjectSession,
) -> None:
    (message,) = _messages(
        session, "SELECT mnth, AGG(uncharged) FROM billing GROUP BY 1"
    )

    assert "Did you mean `month`?" in message


def test_project_qualified_table_is_reported_with_the_engines_message(
    session: ProjectSession,
) -> None:
    (message,) = _messages(
        session,
        "SELECT AGG(invoiced_total) FROM invoices "
        "JOIN dundersign.main.accounts a ON a.id = invoices.account_id",
    )

    assert 'Unknown table: "main"."accounts"' in message
    assert "Did you mean `accounts`?" in message


def test_count_over_a_constellation_keeps_the_engines_message_without_a_guess(
    session: ProjectSession,
) -> None:
    (message,) = _messages(session, "SELECT COUNT(*) FROM billing")

    assert "Only AGG(metric) can aggregate over a constellation" in message
    assert "Did you mean" not in message


def test_sql_that_reads_nothing_in_the_bundle_is_not_compiled(
    session: ProjectSession,
) -> None:
    assert session.validate_query("SELECT COUNT(*) FROM raw_events", source="db") == []


def test_a_source_without_a_bundle_is_not_compiled(session: ProjectSession) -> None:
    sql = "SELECT AGG(invoiced_totl) FROM invoices"

    assert [d.code for d in session.validate_query(sql, source="plain")] == []


def test_a_required_bundle_reports_a_table_outside_it(session: ProjectSession) -> None:
    """Execution refuses the query, so validation says so first: the message names
    the table, and no SQL reaches the warehouse."""
    (diagnostic,) = session.validate_query(
        "SELECT COUNT(*) AS n FROM raw_events", source="governed"
    )

    assert diagnostic.code == "WARN-SEMANTIC-COMPILE-FAILED"
    assert "requires its MetricFlow bundle" in diagnostic.message
    assert "`raw_events`" in diagnostic.message
