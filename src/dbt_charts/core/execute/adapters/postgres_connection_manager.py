"""Postgres connections that read outside a transaction.

Stage: EXECUTE

psycopg2 opens a transaction on the first statement of a non-autocommit connection,
and a dbt charts read never commits — ``auto_begin=False`` only stops *dbt* from opening
one. A pooled connection therefore sat ``idle in transaction`` holding an
``AccessShareLock`` on every table it had read, blocking the ``ACCESS EXCLUSIVE`` that
a dbt table rebuild takes, until the pool was closed.

dbt-postgres grew an ``autocommit`` credential in 1.11, but this package supports 1.10
as well, where an ``autocommit`` key is silently dropped on the way to the driver. So
the setting goes on the psycopg2 handle itself, where no adapter version can ignore it.
Every other warehouse we build already reads outside a transaction: dbt-redshift
and dbt-snowflake open autocommit connections, and BigQuery, Databricks and Spark
have no session transaction to leave open.
"""

from __future__ import annotations

from functools import cache
from typing import Any


@cache
def autocommit_connections(base: type) -> type:
    """Return ``base`` with every connection it opens put in autocommit mode.

    Takes the connection-manager class rather than importing dbt-postgres, so this
    module carries no import-time dependency on an optional warehouse package — and
    so the returned class always derives from the manager the caller's own adapter
    uses. Memoized: one class per base, so ``isinstance`` stays meaningful.
    """

    class _AutocommitConnections(base):
        @classmethod
        def open(cls, connection: Any) -> Any:
            connection = super().open(connection)
            # A profile naming a `role` makes dbt run `set role` at connect, and
            # psycopg2 refuses to switch autocommit on with that transaction still
            # open. Commit it rather than roll it back — `set role` is session state
            # we must keep, and a rollback silently reverts the connection to the
            # login role. A no-op when the profile names no role and nothing was sent.
            connection.handle.commit()
            connection.handle.autocommit = True
            return connection

    return _AutocommitConnections
