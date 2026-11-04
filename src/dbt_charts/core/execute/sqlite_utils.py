"""Shared SQLite utilities used by execute/ and inspect/sources/.

Kept in execute/ (not inspect/) so inspect/sources/sqlite.py can import
from here without triggering a circular import through inspect/__init__.py
(which imports renderer.py which imports the dbt backend from execute/).
"""

from urllib.request import pathname2url

# SQL to list user tables from sqlite_master. Both SQLiteSchemaSource and
# discovery._discover_tables_sqlite use this constant so both paths agree
# on what counts as a "user table" (filters SQLite's internal bookkeeping).
SQLITE_USER_TABLES_SQL = (
    "SELECT name FROM sqlite_master "
    "WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' "
    "ORDER BY name"
)


def sqlite_ro_uri(path: str) -> str:
    """Build a properly-encoded SQLite read-only URI.

    ``pathname2url`` percent-encodes characters that mis-parse under
    SQLite's URI rules (spaces, ``?``, ``#``, ``%``). Without encoding,
    a path like ``/tmp/my data.sqlite`` silently opens the wrong file
    because the space starts the query-string component.

    Args:
        path: Filesystem path to the SQLite file (absolute or relative).

    Returns:
        URI string suitable for ``sqlite3.connect(uri, uri=True)``.
    """
    return f"file:{pathname2url(path)}?mode=ro"
