"""Tests for the cache policy models, duration parsing, and cascade resolution.

Covers the cache policy models module: the strict short-duration parser,
the authored CachePatch overlay, the resolved CachePolicy, and
resolve_cache_policy's field-level layered merge.
"""

from datetime import timedelta
from typing import Annotated, Any, get_args, get_origin

import pytest
from pydantic import BaseModel, BeforeValidator, TypeAdapter, ValidationError

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.cache import (
    CachePatch,
    CachePolicy,
    desugar_cache,
    merge_cache_layers,
    parse_duration,
    resolve_cache_policy,
)

from ..conftest import cache_layer

# ── parse_duration ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("90s", timedelta(seconds=90)),
        ("15m", timedelta(minutes=15)),
        ("1h", timedelta(hours=1)),
        ("24h", timedelta(hours=24)),
        ("2d", timedelta(days=2)),
        ("1w", timedelta(weeks=1)),
        ("1h30m", timedelta(hours=1, minutes=30)),
        ("1d 12h", timedelta(days=1, hours=12)),
        ("1H30M", timedelta(hours=1, minutes=30)),  # case-insensitive
        (" 1h ", timedelta(hours=1)),  # surrounding whitespace
    ],
)
def test_parse_duration_valid(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "hour",
        "1",  # bare number, no unit
        "h",  # unit, no number
        "1 fortnight",
        "1mo",  # months are not a unit
        "1y",  # years are not a unit
        "-1h",
        "1.5h",  # no fractional segments
        "1h1h",  # duplicated unit must be rejected, not summed
        "5m5m",
        "PT1H",  # no ISO 8601
        "0s",  # non-positive: write cache: false or cache: forever instead
        "0h0m",
    ],
)
def test_parse_duration_invalid(text: str) -> None:
    with pytest.raises(ValueError, match="[Ii]nvalid duration"):
        parse_duration(text)


def test_ttl_never_is_rejected_as_an_invalid_literal() -> None:
    """`never` is not the never-auto-expire spelling — `forever` is. The old
    literal must not be silently accepted as either a duration or a keyword."""
    with pytest.raises(ValidationError):
        cache_layer({"ttl": "never"})


# ── patch models ─────────────────────────────────────────────────────────────


def test_cache_patch_accepts_partial_fields() -> None:
    patch = cache_layer(False)
    assert patch.enabled is False
    assert patch.ttl is None


def test_cache_patch_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        cache_layer({"method": "results"})


def test_cache_patch_rejects_malformed_ttl() -> None:
    with pytest.raises(ValidationError):
        cache_layer({"ttl": "1 fortnight"})


def test_cache_patch_accepts_valid_ttl() -> None:
    patch = cache_layer({"ttl": "1h30m"})
    assert patch.ttl == "1h30m"


def test_cache_patch_accepts_ttl_forever() -> None:
    patch = cache_layer({"ttl": "forever"})
    assert patch.ttl == "forever"


# ── scalar authoring surface ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("authored", "enabled", "ttl"),
    [
        (False, False, None),
        (True, True, None),
        ("1h", True, "1h"),
        ("5d", True, "5d"),
        ("1h30m", True, "1h30m"),
        ("forever", True, "forever"),
    ],
)
def test_scalar_cache_desugars(
    authored: bool | str, enabled: bool, ttl: str | None
) -> None:
    """`cache: <scalar>` is the whole authoring surface: false is the only
    opt-out, any other value means on."""
    patch = cache_layer(authored)
    assert patch.enabled is enabled
    assert patch.ttl == ttl


def test_scalar_and_block_ttl_are_equivalent() -> None:
    """`cache: 1h` and `cache: {ttl: 1h}` are the same policy — the scalar is
    pure syntax, not a second set of merge semantics."""
    scalar = resolve_cache_policy(_root(), cache_layer("1h"))
    block = resolve_cache_policy(_root(), cache_layer({"ttl": "1h"}))
    assert scalar == block


def test_malformed_scalar_reports_the_duration_error() -> None:
    with pytest.raises(ValidationError, match="[Ii]nvalid duration"):
        cache_layer("1 fortnight")


@pytest.mark.parametrize(
    "authored",
    [
        {"enabled": False},
        {"enabled": True},
        {"enabled": True, "ttl": "1h"},
        {"enabled": False, "ttl": "1h"},
    ],
)
def test_authored_enabled_key_is_rejected(authored: dict[str, Any]) -> None:
    """`enabled` is derived from the scalar, never authored — an author
    reaching for it gets pointed at `cache: false`.

    Every spelling is rejected, including the dump of an off layer that kept
    a ttl."""
    with pytest.raises(ValidationError, match="cache: false"):
        cache_layer(authored)


def test_bare_cache_key_names_the_four_spellings() -> None:
    """`cache:` with nothing after it is a plausible half-typed block.

    Without a message of its own it reports pydantic's "valid dictionary or
    instance of CachePatch", which names an internal class and none of the
    spellings an author could fix it with.
    """
    with pytest.raises(ValidationError, match="cache: 1h"):
        cache_layer(None)


@pytest.mark.parametrize("authored", ["1h", "forever", True, False, {"ttl": "5m"}, {}])
def test_a_layer_survives_a_dump_and_revalidate(authored: object) -> None:
    """Every authorable state must round-trip through `model_dump()`.

    The extends cascade dumps a board's own patch and re-validates it, so a
    state the dump cannot spell is a state silently lost in production.
    """
    patch = cache_layer(authored)
    assert cache_layer(patch.model_dump(exclude_unset=True)) == patch


def test_an_off_layer_carrying_a_ttl_dumps_with_the_internal_key() -> None:
    """The one state no scalar spells: `false` alone would drop the ttl.

    It has no authored spelling, so consumers pass the model, not its dump.
    """
    merged = merge_cache_layers(cache_layer("1h"), cache_layer(False))
    assert merged.model_dump(exclude_unset=True) == {"enabled": False, "ttl": "1h"}


def test_enabled_is_absent_from_the_authorable_schema() -> None:
    """`enabled` is not authorable, so it must not reach the IR the IDE schema,
    YAML reference, and agent prompt are generated from — the scalars are the
    whole authoring surface."""
    from dbt_charts.core.compile.schema.introspection import introspect

    cache_model = introspect().models["CachePatch"]
    assert [f.name for f in cache_model.fields] == ["ttl", "prefetch"]


# ── CachePolicy ──────────────────────────────────────────────────────────────


def test_policy_ttl_timedelta_parses_duration() -> None:
    policy = CachePolicy.model_validate({"enabled": True, "ttl": "15m"})
    assert policy.ttl_timedelta == timedelta(minutes=15)


def test_policy_no_ttl_means_never_expires() -> None:
    policy = CachePolicy.model_validate({"enabled": True})
    assert policy.ttl is None
    assert policy.ttl_timedelta is None


def test_ttl_timedelta_parses_duration_only_once() -> None:
    """ttl_timedelta is on the executor's per-lookup hot path — cache it.

    Repeated `.ttl_timedelta` reads on the same policy instance must not
    re-run parse_duration; the strict regex parse pays only on first access.
    """
    from dbt_charts.core.compile.models import cache as cache_mod

    policy = CachePolicy.model_validate({"enabled": True, "ttl": "15m"})

    calls = 0
    real_parse_duration = cache_mod.parse_duration

    def _counting_parse_duration(text: str) -> timedelta:
        nonlocal calls
        calls += 1
        return real_parse_duration(text)

    cache_mod.parse_duration = _counting_parse_duration
    try:
        first = policy.ttl_timedelta
        second = policy.ttl_timedelta
        third = policy.ttl_timedelta
    finally:
        cache_mod.parse_duration = real_parse_duration

    assert first == second == third == timedelta(minutes=15)
    assert calls == 1, f"parse_duration called {calls} times, expected exactly 1"


# ── resolve_cache_policy ─────────────────────────────────────────────────────


def _root() -> CachePatch:
    """A root layer as the shipped default supplies it: a ttl (which, like any
    non-false authored value, resolves `enabled`)."""
    return cache_layer({"ttl": "24h"})


def test_resolve_root_alone() -> None:
    policy = resolve_cache_policy(_root())
    assert policy.enabled is True
    assert policy.ttl == "24h"


def test_resolve_skips_none_layers() -> None:
    policy = resolve_cache_policy(_root(), None, None, None)
    assert policy.enabled is True
    assert policy.ttl == "24h"


def test_false_layer_disables_but_inherits_ttl() -> None:
    """`cache: false` sets only enabled; unset fields still inherit (the ttl
    is moot while off, but a re-enabling layer below must still see it)."""
    policy = resolve_cache_policy(_root(), cache_layer(False))
    assert policy.enabled is False
    assert policy.ttl == "24h"


def test_nearest_layer_wins_field_by_field() -> None:
    source = cache_layer({"ttl": "1h"})
    query = cache_layer({"ttl": "5m"})
    policy = resolve_cache_policy(_root(), source, query)
    assert policy.ttl == "5m"
    assert policy.enabled is True  # inherited from root; neither layer set it


def test_unset_ttl_inherits_parent_ttl() -> None:
    source = cache_layer({"ttl": "1h"})
    query = cache_layer(False)
    policy = resolve_cache_policy(_root(), source, query)
    assert policy.enabled is False
    assert policy.ttl == "1h"


def test_ttl_forever_overrides_inherited_ttl() -> None:
    """`ttl: forever` explicitly resets an inherited ttl to never-auto-expire,
    resolved to ttl=None on the policy."""
    query = cache_layer({"ttl": "forever"})
    policy = resolve_cache_policy(_root(), query)
    assert policy.ttl is None
    assert policy.ttl_timedelta is None


def test_explicit_null_ttl_is_rejected() -> None:
    """`ttl: null` looks like omission but would merge differently — it must
    error with a pointer to the two honest spellings."""
    with pytest.raises(ValidationError, match="forever"):
        cache_layer({"ttl": None})


def test_false_then_true_layers_reenable() -> None:
    policy = resolve_cache_policy(_root(), cache_layer(False), cache_layer(True))
    assert policy.enabled is True


def test_a_ttl_below_a_false_layer_reenables() -> None:
    """Writing a ttl asks for caching — a nearer `cache: 5m` beats an outer
    `cache: false` rather than being silently ignored while off."""
    policy = resolve_cache_policy(_root(), cache_layer(False), cache_layer("5m"))
    assert policy.enabled is True
    assert policy.ttl == "5m"


def test_unresolved_enabled_raises() -> None:
    """A cascade of layers that authored nothing must fail loudly, never
    default silently — the root is required to resolve `enabled`."""
    with pytest.raises(ValueError, match="enabled"):
        resolve_cache_policy(CachePatch(), None)


def test_prefetch_defaults_false_when_no_layer_sets_it() -> None:
    assert resolve_cache_policy(_root()).prefetch is False


def test_prefetch_inherits_down_the_cascade() -> None:
    policy = resolve_cache_policy(
        _root(), cache_layer({"prefetch": True}), cache_layer({"ttl": "5m"})
    )
    assert policy.prefetch is True


def test_nearer_prefetch_overrides_in_both_directions() -> None:
    on = cache_layer({"prefetch": True})
    off = cache_layer({"prefetch": False})
    assert resolve_cache_policy(_root(), on, off).prefetch is False
    assert resolve_cache_policy(_root(), off, on).prefetch is True


def test_scalar_shorthands_set_no_prefetch() -> None:
    on = cache_layer({"prefetch": True})
    for scalar in ("1h", True):
        assert resolve_cache_policy(_root(), on, cache_layer(scalar)).prefetch is True


def test_prefetch_only_block_does_not_switch_caching_on() -> None:
    """`prefetch:` says nothing about whether to cache, so a block carrying only
    it must not re-enable a scope above that opted out."""
    policy = resolve_cache_policy(
        _root(), cache_layer(False), cache_layer({"prefetch": False})
    )
    assert policy.enabled is False


def test_prefetch_true_below_a_cache_false_scope_is_an_error() -> None:
    with pytest.raises(CompilationError, match="prefetch"):
        resolve_cache_policy(
            _root(), cache_layer(False), cache_layer({"prefetch": True})
        )


def test_cache_false_below_an_inherited_prefetch_true_just_disables_it() -> None:
    policy = resolve_cache_policy(
        _root(), cache_layer({"prefetch": True}), cache_layer(False)
    )
    assert policy.enabled is False
    assert policy.prefetch is False


# ── shipped config root ──────────────────────────────────────────────────────


def test_default_config_supplies_cache_root() -> None:
    """The shipped default config IS the cascade root — same patch shape as
    every other scope, plus path. Structure-only: the ttl value is a tunable
    default, so it is parsed, not pinned."""
    from dbt_charts.core.compile.config import get_config

    root = get_config().cache
    assert isinstance(root, CachePatch)  # project block doubles as a layer
    policy = resolve_cache_policy(root)
    assert policy.enabled is True  # UX-default identity (feature flag)
    assert policy.ttl is not None
    assert policy.ttl_timedelta is not None


@pytest.mark.parametrize("authored", [False, "4h", "forever"])
def test_project_scope_accepts_the_same_scalars(authored: bool | str) -> None:
    """The project root is authored exactly like every other scope — `path` is
    reachable through the block form, never required to spell a bare ttl."""
    from dbt_charts.core.compile.models.config import ProjectCacheInput

    root = TypeAdapter(ProjectCacheInput).validate_python(authored)
    assert root.path is None
    assert root.enabled is (authored is not False)


def test_project_scope_rejects_bare_true() -> None:
    """The one scalar the root cannot honor: it promises nothing about
    duration, and there is no scope above to take one from, so accepting it
    would silently mean cache-forever."""
    from dbt_charts.core.compile.models.config import ProjectCacheInput

    with pytest.raises(ValidationError, match="forever"):
        TypeAdapter(ProjectCacheInput).validate_python(True)


def _all_models() -> set[type[BaseModel]]:
    seen: set[type[BaseModel]] = set()
    stack: list[type[BaseModel]] = [BaseModel]
    while stack:
        for sub in stack.pop().__subclasses__():
            if sub not in seen:
                seen.add(sub)
                stack.append(sub)
    return seen


def test_every_cache_patch_field_is_annotated_with_cache_input() -> None:
    """A bare `CachePatch` field would accept `{enabled: true}` and reject
    `cache: 1h`: the authoring rules live on the annotation, not the class."""
    import dbt_charts.core.compile.models.config  # noqa: F401  # registers Config
    import dbt_charts.core.compile.models.source  # noqa: F401

    checked = 0
    for model in _all_models():
        field = model.model_fields.get("cache")
        if field is None:
            continue
        # `CacheInput | None` keeps its Annotated arm inside the union.
        arms = [field.annotation, *get_args(field.annotation)]
        if not any(
            issubclass(
                get_args(arm)[0] if get_origin(arm) is Annotated else arm, CachePatch
            )
            for arm in arms
            if isinstance(arm, type) or get_origin(arm) is Annotated
        ):
            continue
        checked += 1
        metadata = [
            *field.metadata,
            *(
                m
                for arm in arms
                if get_origin(arm) is Annotated
                for m in get_args(arm)[1:]
            ),
        ]
        assert any(
            isinstance(m, BeforeValidator) and m.func is desugar_cache for m in metadata
        ), f"{model.__module__}.{model.__name__}.cache is not CacheInput"
    assert checked >= 4
