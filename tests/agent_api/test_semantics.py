"""describe_semantics lists what a source's MetricFlow bundle offers, without a
warehouse connection, so an agent writes semantic SQL from names the bundle
declares instead of guessing them. The overview names the objects and counts
their fields; one named object lists its fields in full."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from dbt_charts.agent_api.semantics import (
    SemanticConstellation,
    SemanticDataset,
    SemanticEntity,
    SemanticOverview,
    SemanticPage,
    SemanticSearchResult,
    describe_semantics,
)
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.semantic_bundle import SemanticBundle, SemanticField

from .._paths import DBT_CHARTS_DIR

needs_metricflow = pytest.mark.skipif(
    importlib.util.find_spec("metricflow_sql") is None,
    reason="metricflow-sql is not installed",
)


@pytest.fixture
def bundle_project(tmp_path: Path) -> FilesystemProject:
    shutil.copytree(
        DBT_CHARTS_DIR / "tests" / "fixtures" / "metricflow-billing",
        tmp_path,
        dirs_exist_ok=True,
    )
    return FilesystemProject(tmp_path)


@needs_metricflow
def test_lists_datasets_with_calculations_and_relationships(
    bundle_project: FilesystemProject,
) -> None:
    invoices = describe_semantics(bundle_project, "db", "invoices")

    assert isinstance(invoices, SemanticDataset)
    assert invoices.description == "One row per Stripe invoice."
    assert [c.name for c in invoices.columns] == [
        "id",
        "account_id",
        "total",
        "amount_paid",
        "created_at",
    ]
    calculations = {c.name: c.expr for c in invoices.calculations}
    assert calculations["collection_rate"] == '"paid_total" / "invoiced_total"'
    assert [(r.name, r.target) for r in invoices.relationships] == [
        ("account", "account")
    ]


@needs_metricflow
def test_lists_entities_with_key_and_attributes(
    bundle_project: FilesystemProject,
) -> None:
    account = describe_semantics(bundle_project, "db", "account")

    assert isinstance(account, SemanticEntity)
    assert [k.name for k in account.key] == ["id"]
    assert [a.name for a in account.attributes] == ["name", "industry"]
    assert account.attributes[1].expr == '"accounts"."industry"'
    assert account.attributes[1].description == "The company's sector."


@needs_metricflow
def test_lists_constellations_with_roles_and_metrics(
    bundle_project: FilesystemProject,
) -> None:
    billing = describe_semantics(bundle_project, "db", "billing")

    assert isinstance(billing, SemanticConstellation)
    assert billing.description == "Billing by account and month."
    assert [(r.name, r.datatype) for r in billing.roles] == [
        ("account", "entity('account')"),
        ("month", "TIMESTAMP"),
    ]
    assert {m.name: m.expr for m in billing.metrics}["uncharged"] == (
        'AGG("invoiced_total") - AGG("charged_amount")'
    )


@needs_metricflow
def test_overview_names_every_object_and_counts_its_fields(
    bundle_project: FilesystemProject,
) -> None:
    overview = describe_semantics(bundle_project, "db")

    assert isinstance(overview, SemanticOverview)
    assert overview.name == "dundersign"
    invoices = {d.name: d for d in overview.datasets}["invoices"]
    assert invoices.description == "One row per Stripe invoice."
    assert invoices.counts == {"columns": 5, "calculations": 4, "relationships": 1}
    assert [e.name for e in overview.entities] == ["account"]
    assert overview.entities[0].counts == {"key": 1, "attributes": 2}
    billing = {c.name: c for c in overview.constellations}["billing"]
    assert billing.counts == {"roles": 2, "metrics": 3}
    # The overview is what an agent reads first: no field is listed in it.
    assert "collection_rate" not in overview.model_dump_json()


@needs_metricflow
def test_an_unknown_name_is_an_error_naming_the_close_ones(
    bundle_project: FilesystemProject,
) -> None:
    with pytest.raises(DbtChartsError, match="'invoice' is not.*invoices"):
        describe_semantics(bundle_project, "db", "invoice")


def test_source_without_a_bundle_is_an_error(
    bundle_project: FilesystemProject,
) -> None:
    with pytest.raises(DbtChartsError, match="'plain' declares no `metricflow:`"):
        describe_semantics(bundle_project, "plain")


def test_unknown_source_is_an_error_naming_the_declared_ones(
    bundle_project: FilesystemProject,
) -> None:
    with pytest.raises(DbtChartsError, match="'nope' is not declared.*db, plain"):
        describe_semantics(bundle_project, "nope")


@needs_metricflow
def test_names_outside_the_default_namespace_keep_it(
    bundle_project: FilesystemProject,
) -> None:
    orders = describe_semantics(bundle_project, "db", "sales.orders")

    assert isinstance(orders, SemanticDataset)
    assert [(r.name, r.target) for r in orders.relationships] == [
        ("account", "account")
    ]


@needs_metricflow
def test_a_constellation_lists_the_fields_it_imports(
    bundle_project: FilesystemProject,
) -> None:
    finance = describe_semantics(bundle_project, "db", "finance")

    assert isinstance(finance, SemanticConstellation)
    assert [r.name for r in finance.roles] == ["account", "month"]
    assert [m.name for m in finance.metrics] == ["invoiced_total", "uncharged"]


SAVED_QUERIES = """
schema_version: v1
bundle: dundersign
generation_meta: {}
dialect: duckdb
datasets:
- name: boards.unpaid_invoices_by_month
  description: Finance board, invoices still unpaid at each month end.
  source:
    type: sql
    sql: SELECT month, SUM(total) AS unpaid FROM invoices GROUP BY 1
    columns:
    - name: month
      datatype: DATE
    - name: unpaid
      datatype: DOUBLE
pages:
- name: boards/overdue_accounts
  description: Finance board, accounts overdue by more than thirty days.
  content: |
    # Overdue accounts

    ```sql
    SELECT account_id FROM invoices WHERE days_late > 30
    ```
"""


@pytest.fixture
def saved_query_project(bundle_project: FilesystemProject) -> FilesystemProject:
    (bundle_project.root / "semantics" / "saved.yaml").write_text(SAVED_QUERIES)
    return bundle_project


@needs_metricflow
def test_overview_counts_other_namespaces_instead_of_listing_them(
    saved_query_project: FilesystemProject,
) -> None:
    overview = describe_semantics(saved_query_project, "db")

    assert isinstance(overview, SemanticOverview)
    assert overview.namespaces == {"boards": 1, "sales": 1}
    assert overview.pages == {"boards": 1}
    assert "boards.unpaid_invoices_by_month" not in {d.name for d in overview.datasets}


@needs_metricflow
def test_a_derived_dataset_shows_its_sql(
    saved_query_project: FilesystemProject,
) -> None:
    saved = describe_semantics(
        saved_query_project, "db", "boards.unpaid_invoices_by_month"
    )

    assert isinstance(saved, SemanticDataset)
    assert saved.sql == "SELECT month, SUM(total) AS unpaid FROM invoices GROUP BY 1"


@needs_metricflow
def test_a_page_reads_by_name(saved_query_project: FilesystemProject) -> None:
    page = describe_semantics(saved_query_project, "db", "boards/overdue_accounts")

    assert isinstance(page, SemanticPage)
    assert (
        page.description == "Finance board, accounts overdue by more than thirty days."
    )
    assert "days_late > 30" in page.content


@needs_metricflow
def test_search_finds_a_page_by_its_description_or_its_content(
    saved_query_project: FilesystemProject,
) -> None:
    by_description = describe_semantics(
        saved_query_project, "db", search="overdue accounts"
    )
    by_content = describe_semantics(saved_query_project, "db", search="days_late")

    for found in (by_description, by_content):
        assert isinstance(found, SemanticSearchResult)
        hit = found.hits[0]
        assert (hit.object, hit.kind) == ("boards/overdue_accounts", "page")


@needs_metricflow
def test_a_rare_word_outranks_common_ones(bundle_project: FilesystemProject) -> None:
    # `account` is on many objects; `collection` names one field.
    found = describe_semantics(bundle_project, "db", search="account collection")

    assert isinstance(found, SemanticSearchResult)
    assert found.hits[0].field == "collection_rate"


@needs_metricflow
def test_a_table_dataset_shows_no_sql(bundle_project: FilesystemProject) -> None:
    invoices = describe_semantics(bundle_project, "db", "invoices")

    assert isinstance(invoices, SemanticDataset)
    assert invoices.sql is None


@needs_metricflow
def test_search_finds_objects_and_fields_in_every_namespace(
    saved_query_project: FilesystemProject,
) -> None:
    found = describe_semantics(saved_query_project, "db", search="unpaid invoices")

    assert isinstance(found, SemanticSearchResult)
    assert found.hits[0].object == "boards.unpaid_invoices_by_month"
    assert found.hits[0].field is None

    collection = describe_semantics(saved_query_project, "db", search="collection rate")
    assert isinstance(collection, SemanticSearchResult)
    hit = collection.hits[0]
    assert (hit.object, hit.field, hit.kind) == (
        "invoices",
        "collection_rate",
        "calculation",
    )


@needs_metricflow
def test_search_within_a_named_object_filters_its_fields(
    bundle_project: FilesystemProject,
) -> None:
    found = describe_semantics(
        bundle_project, "db", "invoices", search="total invoiced"
    )

    assert isinstance(found, SemanticSearchResult)
    assert found.hits
    assert {hit.object for hit in found.hits} == {"invoices"}
    assert found.hits[0].field == "invoiced_total"


@needs_metricflow
def test_search_with_no_match_says_so(bundle_project: FilesystemProject) -> None:
    found = describe_semantics(bundle_project, "db", search="zzzqqq")

    assert isinstance(found, SemanticSearchResult)
    assert found.hits == []


@needs_metricflow
def test_search_within_an_unknown_object_fails(
    bundle_project: FilesystemProject,
) -> None:
    with pytest.raises(DbtChartsError, match="'nonesuch' is not a dataset"):
        describe_semantics(bundle_project, "db", "nonesuch", search="total")


@needs_metricflow
def test_a_bundle_with_no_default_namespace_lists_every_object(
    bundle_project: FilesystemProject,
) -> None:
    only_namespaced = SAVED_QUERIES.replace("bundle: dundersign", "bundle: boards")
    fragment = bundle_project.root / "semantics" / "fragment.yaml"
    fragment.write_text(only_namespaced)

    overview = describe_semantics(bundle_project, "db")

    assert isinstance(overview, SemanticOverview)
    assert overview.namespaces == {}
    assert [d.name for d in overview.datasets] == ["boards.unpaid_invoices_by_month"]


@needs_metricflow
def test_search_reaches_entity_attributes(bundle_project: FilesystemProject) -> None:
    found = describe_semantics(bundle_project, "db", "account", search="industry")

    assert isinstance(found, SemanticSearchResult)
    assert [(hit.object, hit.field, hit.kind) for hit in found.hits] == [
        ("account", "industry", "attribute")
    ]


def test_search_keeps_a_field_every_object_declares() -> None:
    def dataset(name: str) -> SemanticDataset:
        field = SemanticField(name="customer_id")
        return SemanticDataset(
            name=name, columns=[field], calculations=[], relationships=[]
        )

    bundle = SemanticBundle(
        name="shop",
        datasets=[dataset("orders"), dataset("invoices")],
        entities=[],
        constellations=[],
    )

    found = bundle.search("customer_id")

    assert [hit.object for hit in found.hits] == ["orders", "invoices"]


def test_a_word_in_every_field_of_an_object_still_matches() -> None:
    orders = SemanticDataset(
        name="orders",
        columns=[SemanticField(name="order_id"), SemanticField(name="order_total")],
        calculations=[],
        relationships=[],
    )
    bundle = SemanticBundle(
        name="shop", datasets=[orders], entities=[], constellations=[]
    )

    found = bundle.search("order", "orders")

    assert {hit.field for hit in found.hits} >= {"order_id", "order_total"}


@needs_metricflow
def test_a_page_named_like_an_object_fails_instead_of_hiding_it(
    bundle_project: FilesystemProject,
) -> None:
    clash = """
schema_version: v1
bundle: dundersign
generation_meta: {}
dialect: duckdb
pages:
- name: invoices
  content: A page in the way.
"""
    (bundle_project.root / "semantics" / "clash.yaml").write_text(clash)

    with pytest.raises(DbtChartsError, match="`invoices`"):
        describe_semantics(bundle_project, "db", "invoices")


@needs_metricflow
def test_a_word_that_strips_to_nothing_matches_nothing(
    bundle_project: FilesystemProject,
) -> None:
    found = describe_semantics(bundle_project, "db", search="ss")

    assert isinstance(found, SemanticSearchResult)
    assert found.hits == []
