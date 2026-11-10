"""describe_semantics verb — what a source's MetricFlow bundle offers.

Semantic SQL names datasets, calculations, relationship paths, constellations,
roles and metrics that only the bundle declares; an agent that lists them first
does not guess. The overview names every object and counts its fields; one
named object lists them in full; a search finds objects and fields by keyword.
Reads the bundle's files, never the warehouse.
"""

from __future__ import annotations

import difflib

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.source import (
    MetricFlowConfig,
    declared_metricflow,
)
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_METRICFLOW_NO_BUNDLE,
    ERR_METRICFLOW_UNKNOWN_OBJECT,
)
from dbt_charts.core.diagnostics.codes_query import WARN_SEMANTIC_COMPILE_FAILED
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.metricflow import SemanticLayer
from dbt_charts.core.execute.semantic_bundle import (
    SemanticBundle,
    SemanticConstellation,
    SemanticDataset,
    SemanticEntity,
    SemanticObject,
    SemanticOverview,
    SemanticPage,
    SemanticSearchResult,
)
from dbt_charts.core.inspect.query_validator import QueryDiagnostic
from dbt_charts.core.project import Project

__all__ = [
    "DescribeSemanticsArgs",
    "SemanticBundle",
    "SemanticConstellation",
    "SemanticDataset",
    "SemanticEntity",
    "SemanticObject",
    "SemanticOverview",
    "SemanticPage",
    "SemanticSearchResult",
    "describe_semantics",
    "semantic_diagnostics",
]


class DescribeSemanticsArgs(BaseModel):
    """List what a source's MetricFlow semantic bundle offers. Without `name`: every dataset, entity and constellation with its description and how many columns, calculations, relationships, attributes, roles or metrics it has. Other namespaces and pages are counted, not listed. With `name`: that one object in full (a dataset's columns, calculations, relationships and, when derived, its SQL; an entity's key and attributes; a constellation's roles and metrics; a page's text, such as a saved query's SQL to reuse). With `search`: the objects, fields and pages that match those keywords, or the fields within `name`. Read the `semantic-query` skill and call this before writing semantic SQL, then use only these names: a dataset or constellation as the table, `AGG(calculation)` or `AGG(metric)` for measures, and `relationship.attribute` paths or constellation roles for grouping. Reads the bundle's files, never the warehouse."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        description="A source from dbt_charts.yml that declares `metricflow:`"
    )
    name: str | None = Field(
        None,
        description=(
            "A dataset, entity, constellation or page, to list its fields or read "
            "it in full."
        ),
    )
    search: str | None = Field(
        None,
        description=(
            "Keywords to find objects, fields and pages by name, description or "
            "content, or only the fields within `name`."
        ),
    )


def _declared_bundle(project: Project, source: str) -> MetricFlowConfig:
    """The bundle `source` declares with `metricflow:`.

    Raises:
        ExecutionError: ERR-METRICFLOW-NO-BUNDLE when `source` is not a declared
            project source or declares no `metricflow:`.
    """
    declared = project.sources.sources
    if source not in declared:
        raise ExecutionError.from_code(
            ERR_METRICFLOW_NO_BUNDLE,
            source=source,
            reason=(
                f"is not declared; declared sources: {', '.join(declared) or 'none'}"
            ),
        )
    bundle = declared_metricflow(declared[source])
    if bundle is None:
        raise ExecutionError.from_code(
            ERR_METRICFLOW_NO_BUNDLE,
            source=source,
            reason="declares no `metricflow:` directory of semantic fragments",
        )
    return bundle


def describe_semantics(
    project: Project,
    source: str,
    name: str | None = None,
    search: str | None = None,
) -> SemanticOverview | SemanticObject | SemanticSearchResult:
    """`source`'s MetricFlow bundle: an overview of its datasets, entities and
    constellations, the one named `name` with every field, or what matches `search`,
    within `name` when both are given.

    Raises:
        ExecutionError: ERR-METRICFLOW-NO-BUNDLE when `source` is not a declared
            project source or declares no `metricflow:`; ERR-METRICFLOW-UNKNOWN-OBJECT
            when the bundle declares nothing named `name`; the bundle's own
            ERR-METRICFLOW-* codes when it cannot be loaded.
    """
    bundle = SemanticLayer.load(project, _declared_bundle(project, source)).describe()
    if name is None:
        return bundle.overview() if search is None else bundle.search(search)
    objects = bundle.objects()
    if name in objects:
        return objects[name] if search is None else bundle.search(search, name)
    close = difflib.get_close_matches(name, objects, n=3, cutoff=0.6)
    hint = (
        "; did you mean " + " or ".join(f"`{c}`" for c in close) + "?" if close else ""
    )
    raise ExecutionError.from_code(
        ERR_METRICFLOW_UNKNOWN_OBJECT, name=name, bundle=bundle.name, hint=hint
    )


def semantic_diagnostics(
    project: Project, sql: str, source: str | None, dialect: str | None
) -> list[QueryDiagnostic]:
    """The error MetricFlow reports for `sql`, compiled and never run.

    Empty when `source` declares no bundle, or declares it optional and `sql`
    reads none of its datasets or constellations: the warehouse judges that
    SQL as written. A required bundle judges every statement, so one that
    reads a table outside it is reported as execution would refuse it.
    `dialect` is the one `sql` is written in; the bundle's own when None.
    """
    declared = project.sources.sources.get(source, {}) if source is not None else {}
    bundle = declared_metricflow(declared)
    if bundle is None:
        return []
    try:
        layer = SemanticLayer.load(project, bundle)
        read = dialect or layer.registry.dialect
        if layer.governs(sql, read):
            layer.compile(sql, read, read)
    except ExecutionError as exc:
        return [
            QueryDiagnostic(
                code=WARN_SEMANTIC_COMPILE_FAILED.code,
                severity="error",
                message=str(exc),
            )
        ]
    return []
