from metricflow_sql.objects.base import BaseObject
from sqlglot import exp

class Constellation(BaseObject): ...

class Role(BaseObject):
    name: exp.Identifier
    datatype: exp.DataType | None

class Metric(BaseObject):
    name: exp.Identifier
    expr: exp.Expr
