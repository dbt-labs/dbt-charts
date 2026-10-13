"""The dbt-side transport for query attribution.

Stage: EXECUTE

dbt reads two *independent* members off a connection manager's ``query_header``:

- ``add(sql)`` — via ``BaseConnectionManager._add_query_comment``, which every
  adapter's ``add_query`` calls. This is the universal comment path.
- ``comment.query_comment`` — dbt-bigquery's ``get_labels_from_query_comment`` reads
  this *attribute*, JSON-parses it, and passes the result to
  ``QueryJobConfig(labels=...)``. It never parses the SQL it emits.

Driving them separately is the point: BigQuery takes structured job labels while its
query text stays byte-identical, and every other warehouse — none of which has a
structured mechanism to lose — takes the comment.

The payload itself is built in ``core/attribution.py``, which stays free of any dbt
import so the compile layer can share its vocabulary.
"""

from __future__ import annotations

from dbt.adapters.base.query_headers import MacroQueryStringSetter, _QueryComment

from dbt_charts.core.attribution import attribution_payload


class QueryHeader(MacroQueryStringSetter):
    """Supplies attribution where dbt expects a macro-rendered query comment.

    Subclasses dbt's own setter — that is the type ``BaseConnectionManager``
    declares — but deliberately skips its ``__init__``: the parent builds a Jinja
    generator from a project's ``query-comment`` macro and a manifest, and dbt charts
    has neither. Everything the parent would stash is derived per read instead.

    Holds no attribution of its own. One header serves every query on a pooled
    connection, and a pool is shared by every source with the same connection
    identity, so anything captured here would outlive the query it described.

    The payload needs no escaping. Every key and value is held to the label charset,
    which cannot express ``*/``, so the comment cannot be terminated early.
    """

    def __init__(self, emit_sql_comment: bool) -> None:
        self._emit_sql_comment = emit_sql_comment

    @property
    def comment(self) -> _QueryComment:
        """The label source — dbt's own type, built fresh from the ambient context.

        dbt-bigquery reads ``comment.query_comment`` off this to derive job labels.
        """
        return _QueryComment(attribution_payload())

    @comment.setter
    def comment(self, value: _QueryComment) -> None:
        """Attribution is derived per read, never stored.

        Only dbt's ``__init__`` writes this, and we do not call it. Raising rather
        than dropping the write means a future dbt that does write it fails loudly
        instead of silently serving a stale comment.
        """
        raise AttributeError(
            "QueryHeader.comment is derived from the ambient attribution context "
            "and cannot be assigned"
        )

    def add(self, sql: str) -> str:
        """Prepend the payload as a leading block comment, or pass *sql* through."""
        if not self._emit_sql_comment:
            return sql
        return f"/* {attribution_payload()} */\n{sql}"

    def set(self, name: str, query_header_context: None) -> None:
        """No-op. dbt's ``connection_named`` opens every connection with this.

        dbt uses it to render a per-node comment from a macro; our payload is derived
        from the ambient context on each read, so there is no per-node state to stash.

        The context is typed ``None`` because dbt charts always opens connections
        without one. Should a call site ever start passing a real context, that is a
        new contract and the type checker should say so rather than let it be
        silently ignored here.
        """

    def reset(self) -> None:
        """No-op, for the same reason as :meth:`set` — nothing is stashed to clear."""
