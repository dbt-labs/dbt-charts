from metricflow_sql.compiler.catalog.cluster import Cluster
from sqlglot.dialects.dialect import NormalizationStrategy

class Plan:
    out_sql: str

class Compiler:
    def __init__(
        self,
        cluster: Cluster | None = ...,
        catalog: str | None = ...,
        db: str | None = ...,
        input_dialect: str = ...,
        output_dialect: str = ...,
        normalization_strategy: NormalizationStrategy | None = ...,
    ) -> None: ...
    def compile(self, sql: str, *, read_only: bool = ...) -> Plan: ...
