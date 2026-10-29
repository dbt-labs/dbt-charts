from sqlglot import exp

DEFAULT_NAMESPACE: str

def in_namespace(name: exp.Table, namespace: exp.Identifier) -> exp.Table: ...
