"""Cache policy models — authored overlay, resolved policy, duration parsing.

Stage: COMPILE (Input + Resolution)
Purpose: The typed ``cache:`` layer authorable at four scopes (project config →
source → board → query) and the fully-resolved per-query
``CachePolicy`` the executor consumes. Resolution is compile-time: the
normalizer merges the layers field-by-field (nearest scope wins) via
``resolve_cache_policy`` so no cascade logic exists in execute.

The shipped cascade root lives in ``defaults/default_config.yml`` under
``cache:`` — no in-code root default. Every scope is authored as a scalar::

    cache: 1h         # on, expire after an hour
    cache: forever     # on, never auto-expire
    cache: false       # off — the only opt-out
    cache: true        # on, inherit the parent scope's ttl

The project root takes every spelling but ``true``: with no scope above it, an
inherited ttl is not a thing it can have, and the word would quietly resolve to
never-auto-expire (see ``ProjectCacheConfig``).

Unset fields always inherit from the parent *scope* (``exclude_unset``
semantics, mirroring the style ``*Patch`` overlay discipline), so
``cache: 5m`` on a query refines an inherited policy rather than replacing it.
Between *files* — a ``charts/meta.yml`` and the board beneath it — the same
field-by-field rule applies, which is why ``cache: true`` on a board keeps the
directory's ttl instead of jumping to the project root's. The block form
(``cache: {ttl: 5m}``) means exactly the same thing as the scalar and is what
the project scope uses to carry its extra ``path`` key.

``ttl`` lives directly on the layer (no ``invalidate:`` nesting). A future
watermark trigger would join it as a flat sibling key
(``cache: {ttl: 1h, watermark: "SELECT max(...)"}``), not as a submap.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import timedelta
from functools import cached_property
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError as PydanticValidationError,
    model_serializer,
)
from pydantic.json_schema import SkipJsonSchema

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.factories import (
    register_as_own_patch,
    register_shorthand,
)

# Short-duration segments: <number><unit>, units s/m/h/d/w (m = MINUTES, never
# months), compound allowed (1h30m), case- and whitespace-insensitive. No
# months/years (ambiguous length), no fractions, no bare numbers, no ISO 8601.
_DURATION_SEGMENT_RE = re.compile(r"(\d+)\s*([smhdw])", re.ASCII)
_DURATION_FULL_RE = re.compile(r"^(?:\d+\s*[smhdw]\s*)+$", re.ASCII)

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

# Longest ttl an author can mean. A cache entry held for a decade is
# indistinguishable from `cache: forever` in intent, and having a readable
# ceiling is what lets the error say something actionable — `timedelta.max`
# would be technically correct and explain nothing to whoever typed the value.
_MAX_TTL = timedelta(days=3650)
_MAX_TTL_LABEL = "3650d"

# Digits allowed in one segment. This is a precondition for calling ``int()``,
# not a ttl policy: past CPython's 4300-digit conversion limit ``int()`` itself
# raises, and it raises *before* any summed value exists for the ceiling below
# to range-check — so the ceiling alone would never run and a CPython-internals
# message would reach the author. Nine digits is far under that limit and far
# over any real duration; the ceiling is what decides what is actually allowed.
_MAX_SEGMENT_DIGITS = 9


def _too_large_message(text: str) -> str:
    """The one out-of-range message, shared by both guards.

    Both the digit bound and the value ceiling mean the same thing to an author
    — the duration is bigger than a ttl can be — so they say so identically
    rather than exposing which internal limit tripped first.
    """
    return (
        f"Invalid duration {text!r}: too large — the longest ttl is "
        f"{_MAX_TTL_LABEL}. For results that should be kept until a manual "
        "refresh write cache: forever."
    )


def parse_duration(text: str) -> timedelta:
    """Parse a short-duration string (``15m``, ``24h``, ``1h30m``) strictly.

    Units: ``s`` seconds, ``m`` minutes, ``h`` hours, ``d`` days, ``w`` weeks.
    Case- and whitespace-insensitive. A duplicated unit (``1h1h``) is rejected,
    never summed — there is one canonical spelling per duration. The total is
    capped at ``_MAX_TTL``; past that the intent is ``cache: forever``.

    Raises:
        ValueError: On any malformed or out-of-range value. Both are compile
            errors, never silent defaults. Raising ``ValueError`` specifically
            is load-bearing: a pydantic validator turns it into a
            ``ValidationError`` and routes it through the diagnostic system,
            while an ``OverflowError`` from unguarded ``timedelta`` arithmetic
            would escape as a raw traceback.
    """
    normalized = text.strip().lower()
    if not _DURATION_FULL_RE.match(normalized):
        raise ValueError(
            f"Invalid duration {text!r}: expected <number><unit> segments with "
            "units s/m/h/d/w (m = minutes), e.g. '15m', '24h', '1h30m'."
        )
    seen: set[str] = set()
    total_seconds = 0
    for amount, unit in _DURATION_SEGMENT_RE.findall(normalized):
        if unit in seen:
            raise ValueError(
                f"Invalid duration {text!r}: unit '{unit}' appears more than "
                "once — write each unit at most once (e.g. '1h30m', not '1h1h')."
            )
        seen.add(unit)
        # lstrip("0"): the guard is a precondition for int(), so it must count
        # magnitude, not characters — "0000000001w" is one week, and telling
        # its author the value is too large would simply be untrue.
        if len(amount.lstrip("0")) > _MAX_SEGMENT_DIGITS:
            raise ValueError(_too_large_message(text))
        total_seconds += int(amount) * _UNIT_SECONDS[unit]
    if total_seconds > _MAX_TTL.total_seconds():
        raise ValueError(_too_large_message(text))
    if total_seconds == 0:
        raise ValueError(
            f"Invalid duration {text!r}: a ttl must be positive — to disable "
            "caching write cache: false; to keep results until a manual "
            "refresh write cache: forever."
        )
    return timedelta(seconds=total_seconds)


def _validate_duration(
    value: Any,  # type-state: explicit_any — mode="before" validator input; raw YAML value
) -> Any:  # type-state: explicit_any — passthrough of the same boundary value
    if isinstance(value, str):
        parse_duration(value)
    return value


# The schema pattern mirrors `_DURATION_FULL_RE` ignoring case (ECMA regex has
# no inline flags).
_DURATION_SCHEMA_PATTERN = r"^\s*(?:[0-9]+\s*[smhdwSMHDW]\s*)+$"

# The validator runs first so a malformed value gets the parser's actionable
# message; the pattern then only states the shape for JSON Schema.
Duration = Annotated[
    str,
    BeforeValidator(_validate_duration),
    StringConstraints(pattern=_DURATION_SCHEMA_PATTERN),
]
"""A validated short-duration string; malformed values raise at model parse."""


# ── authored overlay (all-Optional; unset fields inherit) ────────────────────


class CachePatch(BaseModel):
    """Cache policy at one scope: cache: 1h / forever / true / false (the only opt-out).

    Authorable at the project, source, board, and query scopes — `cache: true`
    at the project root excepted, since it has no scope to inherit a ttl from.
    Validate authored values through ``CacheInput``, never this class bare:
    ``desugar_cache`` expands the scalar into these fields; the block form
    (``cache: {ttl: 1h}``) means the same thing. Unset fields inherit.

    Keep the summary line above self-contained and naming every authoring form:
    introspection keeps only the first line, so it is the whole description the
    generated YAML reference and the agent prompt carry for this model.
    """

    # Frozen because INHERIT_CACHE is a module-level empty instance used as the
    # default in a dozen signatures — a mutation anywhere would contaminate
    # every compile in the process.
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Never authorable — `cache: false` is the only opt-out, and `desugar_cache`
    # rejects the key outright. This field is where that boolean lands
    # internally, and `internal` keeps it out of the IDE schema, the YAML
    # reference, and the agent prompt.
    enabled: bool | None = Field(
        default=None,
        json_schema_extra={"internal": True},
        description=(
            "Internal: whether results at this scope may be cached — set by "
            "`cache: false` / `cache: true`, never authored directly."
        ),
    )
    ttl: Literal["forever"] | Duration | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "Expire cached results after this wall-clock age (lazy: the next "
            "read recomputes). Short-duration string: units s/m/h/d/w, m = "
            "minutes, compound allowed ('1h30m'). Omit to inherit from the "
            "parent scope; write 'forever' to override an inherited ttl with "
            "never-auto-expire (manual refresh always remains available)."
        ),
        examples=["5m", "1h", "24h", "7d", "forever"],
    )

    @model_serializer(mode="plain")
    def _serialize_as_authored(self) -> bool | str | Mapping[str, str | bool]:
        """Emit the layer in the spelling an author would have written.

        The inverse of ``desugar_cache``, so ``enabled`` stays unauthorable.
        One merged state has no authored spelling: off while still carrying an
        inherited ttl. It dumps with the internal ``enabled`` key, which the
        validator rejects, so a consumer must pass such a layer as the model
        rather than re-validate its dump (``compile`` does).
        """
        # A set-but-null key is dropped: only the project scope's `path` can be
        # one (`ttl: null` is rejected outright), and it re-validates to the
        # same unset state it came from, so nothing is lost by leaving it out —
        # while keeping it would make this a dict of maybe-strings.
        written: dict[str, str] = {
            name: value
            for name in self.model_fields_set
            if name != "enabled" and (value := getattr(self, name)) is not None
        }
        if self.enabled is False and written:
            return {"enabled": False, **written}
        if written.keys() - {"ttl"}:
            # A block — the project scope's `path` travels with its ttl.
            return written
        if self.ttl is not None:
            return self.ttl
        return {} if self.enabled is None else self.enabled


def desugar_cache(
    data: Any,  # type-state: explicit_any — mode="before" validator input; raw YAML value
) -> Any:  # type-state: explicit_any — passthrough of the same boundary value
    """Expand the scalar authoring forms into the fields they stand for.

    ``cache: false`` is the only opt-out; every other authored value means
    caching is on at this scope, so the value's presence resolves
    ``enabled`` and the cascade root needs no ``enabled: true``
    restatement. An empty block authors nothing at all.
    """
    if isinstance(data, bool):
        return {"enabled": data}
    if isinstance(data, str):
        return {"enabled": True, "ttl": data}
    if data is None:
        # A bare `cache:` — a block someone started and never finished.
        # Pydantic's own message for it names this class, which is not a
        # word the author can act on.
        raise ValueError(
            "cache: needs a value — write cache: <duration> (e.g. "
            "cache: 1h), cache: forever, cache: true, or cache: false."
        )
    if not isinstance(data, dict):
        return data
    if "enabled" in data:
        raise ValueError(
            "cache.enabled is not authorable — write cache: false to turn "
            "caching off at this scope, or cache: <duration> (e.g. "
            "cache: 1h) / cache: true to turn it on."
        )
    # An explicit `ttl: null` looks like omission but would silently mean
    # something different under exclude-unset merging — reject it with the
    # two honest spellings instead of shipping a third, invisible one.
    if "ttl" in data and data["ttl"] is None:
        raise ValueError(
            "ttl: null is ambiguous — omit ttl to inherit from the parent "
            "scope, or write cache: forever to never auto-expire."
        )
    # A block that carries any value at all means caching is on.
    return {"enabled": True, **data} if data else data


_CACHE_SHORTHAND = BeforeValidator(
    desugar_cache,
    json_schema_input_type=bool | Literal["forever"] | Duration | CachePatch,
)
CacheInput = Annotated[CachePatch, _CACHE_SHORTHAND]
"""``cache:`` at any scope: a scalar or the block form."""

# CachePatch is all-Optional and is its own patch. Without the registration, the
# `AuthoredBoard` → `BoardPatch` synthesis every file-based compile runs would
# build a `CachePatchPatch`; and the generated patch field drops the annotation's
# validator, so the shorthand is re-attached per patch field.
register_as_own_patch(CachePatch)
register_shorthand(CachePatch, _CACHE_SHORTHAND)


# ── resolved policy (the executor's contract) ────────────────────────────────


class CachePolicy(BaseModel):
    """Fully-resolved cache policy for one query — what the executor consumes.

    Real authored input always gets this stamped by the normalizer from the
    four-scope cascade (whose loud-failure root is the required ``cache`` block
    in the shipped default config). Direct constructors (tests, compile-time
    lowering paths) that skip the cascade default to ``NEVER_CACHED`` below —
    fail-safe: a missed stamp silently means "don't cache" (a visible perf
    cost), never "cache forever" (silently stale data).

    ``frozen=True``: instances are shared as module-level constants
    (``NEVER_CACHED``, and it doubles as ``Query.cache``'s field default)
    across every direct construction site — frozen makes that sharing safe
    by construction, no defensive copying needed at any call site.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = Field(
        default=True,
        description="Whether this query's results may be cached and referenced "
        "via {{ queries.X.cache }}.",
    )
    # None = never auto-expires (manual refresh only): a real policy outcome,
    # not a cascade miss — the cascade root always resolves via
    # default_config.yml, and an author reaches None only via ttl: forever.
    ttl: Duration | None = Field(
        default=None,
        description="Resolved ttl duration string; None means never auto-expire.",
    )

    @cached_property
    def ttl_timedelta(self) -> timedelta | None:
        """The resolved ttl as a timedelta, or None for never-auto-expire.

        Cached: this sits on the executor's per-query cache hot path — every
        persistent read passes it as the reader's freshness bound and every
        write stamps the entry's deadline from it — and `ttl` is set once at
        construction and never mutated afterward, so re-parsing the duration
        string on every read would be pure waste.

        Caveat: `model_copy(update={"ttl": ...})` would NOT invalidate an
        already-computed cache (pydantic's `model_copy` never re-validates) —
        construct a fresh instance instead of copy-updating `ttl` on one that
        may already have been read. No current caller does this.
        """
        return parse_duration(self.ttl) if self.ttl is not None else None


# The fail-safe default for `Query.cache` (compile/models/query/normalized.py)
# and the explicit choice for call sites that structurally never reach the
# result cache (ad-hoc CLI/agent-API probes, internal executor plumbing that
# calls AdapterRegistry.execute directly, bypassing Executor's cache-aware
# path). A single frozen instance, shared everywhere it's read.
NEVER_CACHED = CachePolicy(enabled=False, ttl=None)


# ── cascade resolution ───────────────────────────────────────────────────────


def _as_base_patch(layer: CachePatch) -> CachePatch:
    """Coerce a cascade layer to the base CachePatch shape.

    Subclasses (the project block carries an extra ``path``) are projected
    onto the base shape, preserving which fields were explicitly set so unset
    fields keep inheriting through the merge.
    """
    if type(layer) is CachePatch:
        return layer
    return CachePatch.model_construct(
        _fields_set=layer.model_fields_set & set(CachePatch.model_fields),
        **{name: getattr(layer, name) for name in CachePatch.model_fields},
    )


def merge_cache_layers(*layers: CachePatch) -> CachePatch:
    """Merge cache overlay layers into one patch, nearest last.

    Callers pass layers outermost→innermost (project root, source, board,
    query); a scope that authored no ``cache:`` passes ``INHERIT_CACHE``. The
    merge itself is the shared patch engine (``compile.merge.merge_patches``)
    — the same explicitly-set-wins, field-by-field walk every other cascade
    uses. Used both to fold the full stack into a policy below and to collapse
    the board scope's own layers (a nested board over the board it sits in)
    before that stack is assembled.
    """
    # Runtime import: merge.py sits above the models package in the compile
    # layering; importing it at module top would invert that direction for
    # every models consumer.
    from dbt_charts.core.compile.merge import merge_patches

    merged = CachePatch()
    for layer in layers:
        merged = merge_patches(merged, _as_base_patch(layer), nested=False)
    return merged


_CACHE_ADAPTER: TypeAdapter[CachePatch] = TypeAdapter(CacheInput)


def validate_cache_layer(scope: str, raw: Any) -> CachePatch | None:
    """Validate a raw authored cache value into a cascade layer.

    Accepts None (nothing authored) and an already-validated CachePatch;
    anything else goes through ``CacheInput``'s desugaring, so the
    raw-dict authoring path enforces exactly what the typed authored fields
    do — a malformed value is a compile error, never silently ignored.
    """
    if raw is None or isinstance(raw, CachePatch):
        return raw
    try:
        return _CACHE_ADAPTER.validate_python(raw)
    except PydanticValidationError as e:
        raise CompilationError(f"Query '{scope}': invalid cache value: {e}") from e


INHERIT_CACHE = CachePatch()
"""The empty layer: authors nothing, so every field inherits from the parent.

The default for the board-scope layer threaded through query normalization — a
patch with no fields set contributes nothing to the merge, so "inherit
everything" needs no second, ``None``-shaped spelling of the same state.
"""


def resolve_cache_policy(*layers: CachePatch | None) -> CachePolicy:
    """Merge cache overlay layers into one resolved policy, nearest last.

    Layer order is the authoring cascade: project root → source → board →
    query. Beyond the merge (``merge_cache_layers``) this performs the terminal
    patch→policy conversion, mapping ``ttl: forever`` to the resolved
    ``ttl=None``.

    Raises:
        ValueError: If no layer resolves ``enabled`` — the cascade root is
            required to (any authored value other than ``false`` does); a miss
            is a configuration bug, never a silent default.
    """
    authored = [layer for layer in layers if layer is not None]
    merged = merge_cache_layers(*authored)
    if merged.enabled is None:
        raise ValueError(
            "cache cascade did not resolve 'enabled' — the cascade root "
            "(the cache: value in the project config) must set it."
        )
    return CachePolicy(
        enabled=merged.enabled,
        ttl=None if merged.ttl == "forever" else merged.ttl,
    )
