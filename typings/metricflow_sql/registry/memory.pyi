from collections.abc import Collection

from metricflow_sql.objects.constellation import Constellation, Metric, Role
from metricflow_sql.objects.dataset import (
    Calculation,
    Column,
    Relationship,
    Table,
    View,
)
from metricflow_sql.objects.entity import Attribute, Entity
from metricflow_sql.objects.wiki import Page
from metricflow_sql.registry.operation import Batch
from sqlglot import exp
from sqlglot.dialects.dialect import NormalizationStrategy

class InMemoryConstellationProxy:
    def column_names(self) -> Collection[exp.Identifier]: ...
    def get_column(self, name: exp.Identifier) -> Metric | Role | None: ...

class InMemoryRegistry:
    dialect: str
    normalization_strategy: NormalizationStrategy
    datasets: dict[exp.Table, Table | View]
    dataset_fields: dict[
        exp.Table, dict[exp.Identifier, Column | Calculation | Relationship]
    ]
    entities: dict[exp.Table, Entity]
    entity_fields: dict[exp.Table, dict[exp.Identifier, Attribute]]
    constellations: dict[exp.Table, Constellation]
    pages: dict[str, Page]
    def __init__(
        self, dialect: str, normalization_strategy: NormalizationStrategy = ...
    ) -> None: ...
    def apply(self, batch: Batch) -> None: ...
    def get_dataset(self, name: exp.Table) -> object | None: ...
    def get_constellation(
        self, name: exp.Table
    ) -> InMemoryConstellationProxy | None: ...
