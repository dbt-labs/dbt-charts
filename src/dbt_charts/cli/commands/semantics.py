"""semantics command — thin wrapper over dbt_charts.agent_api.semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from dbt_charts.cli._error_format import print_error
from dbt_charts.cli._json_output import print_json_result
from dbt_charts.cli._project import with_project

if TYPE_CHECKING:
    from dbt_charts.agent_api import (
        Project,
        SemanticObject,
        SemanticOverview,
        SemanticSearchResult,
    )


@with_project
def semantics_command(
    source: str,
    name: str | None,
    *,
    project: Project,
    search: str | None = None,
    json_output: bool = False,
) -> None:
    from dbt_charts.agent_api import (
        DbtChartsError,
        SemanticOverview,
        SemanticSearchResult,
        describe_semantics,
    )

    try:
        result = describe_semantics(project, source, name, search)
    except DbtChartsError as exc:
        print_error(str(exc))
        raise typer.Exit(1) from exc
    if json_output:
        print_json_result(result)
    elif isinstance(result, SemanticOverview):
        _print_overview(result, source)
    elif isinstance(result, SemanticSearchResult):
        _print_hits(result)
    else:
        _print_object(result)


_LABELS = {
    "columns": ("column", "columns"),
    "calculations": ("calculation", "calculations"),
    "relationships": ("relationship", "relationships"),
    "key": ("key column", "key columns"),
    "attributes": ("attribute", "attributes"),
    "roles": ("role", "roles"),
    "metrics": ("metric", "metrics"),
}


def _counted(counts: dict[str, int]) -> str:
    return ", ".join(
        f"{n} {_LABELS[kind][0] if n == 1 else _LABELS[kind][1]}"
        for kind, n in counts.items()
    )


def _print_overview(overview: SemanticOverview, source: str) -> None:
    echo = typer.echo
    echo(f"Bundle {overview.name}")
    for heading, summaries in (
        ("Datasets", overview.datasets),
        ("Entities", overview.entities),
        ("Constellations", overview.constellations),
    ):
        echo(f"\n{heading}:")
        for summary in summaries:
            echo(f"  {summary.name}  {summary.description or ''}".rstrip())
            echo(f"    {_counted(summary.counts)}")
    if overview.namespaces:
        echo("\nOther namespaces:")
        for namespace, count in overview.namespaces.items():
            echo(f"  {namespace}  {count}")
    if overview.pages:
        echo("\nPages:")
        for section, count in overview.pages.items():
            echo(f"  {section}  {count}")
    echo(f"\n`dct semantics {source} NAME` lists one object's fields.")
    echo(f"`dct semantics {source} --search WORDS` finds objects and fields.")


def _print_hits(result: SemanticSearchResult) -> None:
    echo = typer.echo
    if not result.hits:
        echo(f"Nothing matches {result.query!r}.")
    for hit in result.hits:
        name = f"{hit.object}.{hit.field}" if hit.field else hit.object
        echo(f"{hit.kind}: {name}  {hit.description or ''}".rstrip())


def _print_object(obj: SemanticObject) -> None:
    from dbt_charts.agent_api.semantics import (
        SemanticDataset,
        SemanticEntity,
        SemanticPage,
    )

    # Plain echo, not rich: it would wrap expressions and read their brackets as markup.
    echo = typer.echo
    echo(f"{obj.name}  {obj.description or ''}".rstrip())
    if isinstance(obj, SemanticPage):
        echo(obj.content)
    elif isinstance(obj, SemanticDataset):
        if obj.sql:
            echo(f"  sql: {obj.sql}")
        echo("  columns: " + ", ".join(f"{c.name} {c.datatype}" for c in obj.columns))
        for calculation in obj.calculations:
            echo(f"  calculation: {calculation.name} = {calculation.expr}")
        for relationship in obj.relationships:
            echo(f"  relationship: {relationship.name} -> {relationship.target}")
    elif isinstance(obj, SemanticEntity):
        echo("  key: " + ", ".join(f"{k.name} {k.datatype}" for k in obj.key))
        for attribute in obj.attributes:
            echo(f"  attribute: {attribute.name} = {attribute.expr}")
    else:
        echo("  roles: " + ", ".join(f"{r.name} {r.datatype}" for r in obj.roles))
        for metric in obj.metrics:
            echo(f"  metric: {metric.name} = {metric.expr}")
