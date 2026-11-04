from metricflow_sql.objects.base import BaseObject
from sqlglot import exp

class KeyColumn:
    datatype: exp.DataType | None

class Entity(BaseObject):
    key: dict[exp.Identifier, KeyColumn]

class Attribute(BaseObject):
    name: exp.Identifier
    expr: exp.Expr
