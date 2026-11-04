from metricflow_sql.objects.base import BaseObject
from sqlglot import exp

class Column(BaseObject):
    name: exp.Identifier
    datatype: exp.DataType | None

class Calculation(BaseObject):
    name: exp.Identifier
    expr: exp.Expr

class Relationship(BaseObject):
    name: exp.Identifier
    target: exp.Table

class Table(BaseObject): ...

class View(BaseObject):
    sql: str
