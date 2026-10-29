from metricflow_sql.compiler.catalog.semantic import SemanticCatalog

class Cluster:
    def __init__(self, catalogs: dict[str, SemanticCatalog] | None = ...) -> None: ...
