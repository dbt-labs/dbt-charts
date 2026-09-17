"""Warehouse-native attribution, carried on the connection credential.

Stage: EXECUTE

Snowflake, Databricks, Trino and Postgres each expose a dedicated field naming who is
connecting, and each dbt adapter surfaces it as an ordinary credential. Setting one
costs nothing: it travels with the connect/login request, so a warehouse-native answer
to "is this dbt charts" lands in its own column instead of requiring a regex over the
query text.

What we may write there is bounded by pooling. ``_source_config_hash`` deliberately
ignores ``attribution`` so a per-team label does not cost a connection handshake per
source — which means one connection serves several sources, and a worker holds it
across renders. Only :func:`~dbt_charts.core.attribution.connection_identity` is true
for all of them, so only that is sent. Team, board and query ride the query comment.

The rest of ``_ADAPTER_TYPE_MAP`` is absent for four different reasons:

- **BigQuery** — carries the whole payload as structured job labels already.
- **Redshift and Athena** — each has exactly one session-identifying knob, and
  unlike the fields above it is not inert. Redshift's ``SET query_group`` is
  the one mechanism dbt leaves unexposed as a credential at all; Athena's
  ``work_group`` is an exposed credential, but WLM rules route on
  ``query_group`` and workgroup-scoped data limits/engine settings route on
  ``work_group``, so writing either to gain a label could silently move a
  customer's queries elsewhere. A deliberate no on both, not an oversight.
- **ClickHouse** — ``log_comment`` is the field, and it is inert, but it is a
  *setting* rather than a credential: dbt-clickhouse would send it with every
  request, and a ``readonly = 1`` user cannot send settings at all.
- **DuckDB and Spark** — DuckDB runs in-process, with no session to identify. Spark
  was never assessed; if someone adds it, that is a new decision to make rather than
  one already taken here.
"""

from __future__ import annotations

import json

from dbt_charts.core.attribution import connection_identity

# adapter type → the credential field that warehouse exposes for connection identity.
_CREDENTIAL_FIELD: dict[str, str] = {
    "snowflake": "query_tag",
    "databricks": "query_tags",
    "trino": "client_tags",
    "postgres": "application_name",
    "postgresql": "application_name",
}


def native_attribution_credential(adapter_type: str) -> dict[str, str | list[str]]:
    """The credential field this warehouse exposes for attribution, and its value.

    Empty when the warehouse has no such field — see the module docstring for which
    ones, and why each is absent. Callers merge the result into their credential
    kwargs *without overwriting*: every one of these fields is author-settable, and
    an authored value may drive real monitoring or governance rules.

    The value shape follows what each adapter's credential accepts: a JSON object
    string for Snowflake and Databricks (dbt-databricks parses it back into a tag
    dict), a list of ``key=value`` strings for Trino, and a bare name for Postgres —
    whose ``application_name`` caps near 63 bytes and cannot hold JSON.
    """
    field = _CREDENTIAL_FIELD.get(adapter_type)
    if field is None:
        return {}
    identity = connection_identity()
    if field == "application_name":
        return {field: identity["app"]}
    if field == "client_tags":
        return {field: [f"{key}={value}" for key, value in sorted(identity.items())]}
    return {field: json.dumps(identity)}
