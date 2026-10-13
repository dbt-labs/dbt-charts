"""Warehouse query attribution — what ran this query, and whose budget it belongs to.

Stage: leaf (no compile/execute/render dependencies).

Every query dbt charts sends carries an attribution payload so a warehouse cost model
can tell a dbt charts query apart from ad-hoc human querying. Without it a dashboard
render is indistinguishable from someone typing SQL into a console, and cost
attribution has to guess.

Two halves, with different owners:

- **Identity** is engine-owned and derived — the app, the surface, the board and query
  that caused the call. Authoring it would create a second source of truth that drifts
  on the first rename, so authors cannot set it. Most of it sits under the
  ``dbt_charts_`` namespace; ``app`` deliberately does not, because dbt emits
  ``app=dbt`` and sharing that one key lets a cost model ask "which tool ran this"
  once instead of per tool.
- **Ownership** (``team``, ``cost_center``, …) cannot be derived from anything the
  engine knows. It is authored per source via ``attribution:``.

The payload reaches the warehouse through ``execute/adapters/query_header.py``; this
module stays free of any dbt import so the compile layer can share its vocabulary.

Engine-owned values are *normalized* to the label charset (we generate them, so a board
slug of ``gtm/weekly-revenue`` becoming ``gtm-weekly-revenue`` is our own bookkeeping).
Authored values are *validated* and rejected — silently rewriting what an author typed
is the failure mode where a cost model quietly attributes spend to the wrong team.
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType

# The engine's namespace. Authors cannot write keys under it, so a cost model can
# trust that a `dbt_charts_` key means the engine said so.
RESERVED_PREFIX = "dbt_charts_"

# `app` is deliberately NOT prefixed: it is the cross-tool discriminator dbt also
# emits (`app=dbt`), so one predicate answers "which tool ran this" across both. That
# leaves it inside the space an author could otherwise write, so it is reserved by
# name — without this, `attribution: {app: looker}` would shadow the engine's value,
# since authored pairs merge over engine ones.
RESERVED_KEYS = frozenset({"app"})

# BigQuery's job-label rules are the strictest of any warehouse we emit to, so every
# key and value is held to them regardless of dialect: one rule set means the same
# source config behaves identically everywhere, instead of a value that works on
# Snowflake and is silently truncated on BigQuery.
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_VALUE_PATTERN = re.compile(r"^[a-z0-9_-]{0,63}$")
_NON_LABEL_CHARS = re.compile(r"[^a-z0-9_-]")
_MAX_VALUE_LENGTH = 63

# A frozen empty mapping, not `{}`: a mutable ContextVar default is shared across
# every context that never sets one, so a stray write would leak between them.
_NO_ATTRIBUTION: Mapping[str, str] = MappingProxyType({})

_runtime: ContextVar[Mapping[str, str]] = ContextVar(
    "dbt_charts_attribution", default=_NO_ATTRIBUTION
)

# Set once by the entry point (see set_surface); empty until one declares itself.
_process_surface: dict[str, str] = {}

# The dimensions a scope may establish. `surface` is here too so a narrower scope
# (the inspector, say) can override what the entry point declared.
#
# `actor`, `client`, and `request` are per-request identity, not engine-derived
# state: a host establishes them here, at this ContextVar seam, specifically
# because `set_surface()`'s process-global dict and `connection_identity()`'s
# pooled-connection payload are both cross-tenant traps for them — a Cloud
# process serves many users concurrently, and neither of those two places
# unwinds per request the way `attribute()`'s scope does. Values are opaque
# IDs the host mints, never emails or display names: the label charset would
# mangle a PII value anyway, and warehouse query history is broadly readable
# and long-retained. `tool` is per-call, set by the tool dispatcher; like the
# others it must stay off `connection_identity()`.
SCOPE_DIMENSIONS = frozenset(
    {"surface", "board", "query", "target", "actor", "client", "request", "tool"}
)


def validate_attribution(attribution: Mapping[str, str]) -> None:
    """Raise ``ValueError`` on a reserved or malformed authored attribution pair.

    Runs at the compile boundary, on values already rendered through dbt Jinja, so an
    ``{{ env_var(...) }}`` result is held to the same rules as a literal.
    """
    for key, value in attribution.items():
        if key.startswith(RESERVED_PREFIX) or key in RESERVED_KEYS:
            raise ValueError(
                f"attribution key {key!r} is reserved: the engine sets it. Keys "
                f"beginning with {RESERVED_PREFIX!r}, and "
                f"{', '.join(sorted(repr(k) for k in RESERVED_KEYS))}, are engine-owned"
            )
        if not _KEY_PATTERN.match(key):
            raise ValueError(
                f"attribution key {key!r} is invalid: keys must match "
                f"[a-z][a-z0-9_-]{{0,62}}"
            )
        if not _VALUE_PATTERN.match(value):
            raise ValueError(
                f"attribution value for {key!r} is invalid: values must match "
                f"[a-z0-9_-]{{0,63}}"
            )


def engine_attribution() -> dict[str, str]:
    """The constant half of the engine's identity: what produced this query."""
    from dbt_charts import __version__

    return {
        # `app` matches dbt's own default query comment, which emits `app=dbt` — so a
        # cost model can filter one key across both tools instead of two.
        "app": "dbt-charts",
        "dbt_charts_version": _normalize(__version__),
    }


def connection_identity() -> dict[str, str]:
    """The attribution stable enough to ride a pooled connection.

    Engine constants plus the process surface — deliberately *not*
    :func:`current_attribution`, which also carries the per-query scope. A pooled
    connection is shared by every source with the same connection identity and a
    worker holds it across renders, so anything written at connect time must be true
    for every source and every query that will use it. The board, the query, and the
    source's authored ``attribution:`` are none of those things; they ride the query
    comment, which is rebuilt per call.
    """
    return {**engine_attribution(), **_process_surface}


def attribution_payload() -> str:
    """The per-query attribution as the JSON a query comment or job label carries.

    Merge order is immaterial: :func:`validate_attribution` has already rejected
    every engine-owned key at the compile boundary, so an authored pair cannot
    collide with one. For ``app`` that guard is RESERVED_KEYS rather than the
    ``dbt_charts_`` prefix, since it sits outside the namespace.
    """
    return json.dumps({**engine_attribution(), **current_attribution()})


def current_attribution() -> dict[str, str]:
    """The engine identity in effect: the process surface plus enclosing scopes."""
    return {**_process_surface, **_runtime.get()}


def set_surface(surface: str) -> None:
    """Declare which dbt charts entry point this process is: ``cli``, ``serve``, ``cloud``…

    The surface is a property of the running process, not of a request or a session,
    so it is set once at the entry point rather than threaded through every registry
    construction. Nothing is assumed when no entry point declares one — the payload
    omits ``dbt_charts_surface`` rather than inventing a value, so a cost model never
    reads a surface nobody set.

    Deliberately process-wide: unlike a tenant or a user, every session in a given
    process shares the same entry point, so there is no cross-tenant leak here.
    """
    _process_surface[f"{RESERVED_PREFIX}surface"] = _normalize(surface)


@contextmanager
def attribute(pairs: Mapping[str, str], authored: Mapping[str, str]) -> Generator[None]:
    """Establish attribution for every warehouse call made inside the scope.

    *pairs* holds the engine dimensions this scope actually knows — a composition root
    knows the surface but not yet the board; the execute boundary knows the board and
    query. Scopes nest and merge, innermost wins, and a dimension nobody set is simply
    absent from the payload rather than emitted empty. Keys are bare dimension names
    (``board``, not ``dbt_charts_board``); the prefix is added here, which is what makes
    the reserved namespace unreachable from anywhere else. An
    unknown dimension name is a caller bug, so it raises rather than silently
    vanishing.

    *authored* is the resolved source's own ``attribution:`` block, already validated
    at the compile boundary, merged in unprefixed. It rides this per-call scope rather
    than being captured when the adapter is built: connection pools are shared by
    every source with the same *connection* identity, so an adapter-captured value
    would label one source's queries with a different source's team.

    Values are normalized to the label charset here rather than at emission, so the
    payload is already warehouse-safe wherever it is read.

    Note for callers dispatching onto their own threads: ``ContextVar`` does not cross
    ``ThreadPoolExecutor.submit`` on its own — pass ``contextvars.copy_context().run``
    or the scope is invisible to the worker.
    """
    unknown = sorted(set(pairs) - SCOPE_DIMENSIONS)
    if unknown:
        raise ValueError(
            f"unknown attribution dimension(s) {unknown}: "
            f"expected any of {sorted(SCOPE_DIMENSIONS)}"
        )
    established = {
        **dict(authored),
        **{
            f"{RESERVED_PREFIX}{name}": _normalize(value)
            for name, value in pairs.items()
        },
    }
    token = _runtime.set({**_runtime.get(), **established})
    try:
        yield
    finally:
        _runtime.reset(token)


def _normalize(value: str) -> str:
    """Coerce an engine-generated value into the label charset."""
    return _NON_LABEL_CHARS.sub("-", value.strip().lower())[:_MAX_VALUE_LENGTH]
