"""A stand-in ``Executor.lookup`` for tests that mock the executor."""

from collections.abc import Iterable

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.lookup import Lookup, Miss


def all_miss(names: Iterable[str], variables: object = None) -> Lookup:
    """Every queried name is a miss: nothing is cached."""
    query = SqlQuery(sql="SELECT 1")
    return Lookup({}, tuple(Miss(name, query, ("", "", "")) for name in names), {})
