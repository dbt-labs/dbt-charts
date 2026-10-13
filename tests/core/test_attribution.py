"""Tests for the warehouse query attribution vocabulary.

Pin the behavior a warehouse cost model depends on:

- authored attribution is validated, never silently sanitized
- the engine's ``dbt_charts_`` namespace cannot be shadowed by an author
- runtime context accumulates across scopes and unwinds cleanly

The dbt-side transport is covered by ``execute/adapters/test_query_header.py``.
"""

from __future__ import annotations

import re

import pytest

import dbt_charts.core.attribution as attribution_module
from dbt_charts.core.attribution import (
    attribute,
    connection_identity,
    current_attribution,
    set_surface,
    validate_attribution,
)


class TestValidateAttribution:
    def test_accepts_a_well_formed_pair(self) -> None:
        validate_attribution({"team": "architecture-analytics"})

    def test_accepts_an_empty_value(self) -> None:
        validate_attribution({"team": ""})

    def test_rejects_the_reserved_engine_prefix(self) -> None:
        with pytest.raises(ValueError, match="reserved"):
            validate_attribution({"dbt_charts_surface": "looker"})

    def test_rejects_the_unprefixed_app_key(self) -> None:
        """`app` is the cross-tool discriminator and sits outside the dbt_charts_ namespace,
        so nothing but this check stops an author shadowing it."""
        with pytest.raises(ValueError, match="reserved"):
            validate_attribution({"app": "looker"})

    def test_rejects_an_uppercase_key(self) -> None:
        with pytest.raises(ValueError, match="keys must match"):
            validate_attribution({"Cost Center": "data-platform"})

    def test_rejects_a_key_starting_with_a_digit(self) -> None:
        with pytest.raises(ValueError, match="keys must match"):
            validate_attribution({"2team": "x"})

    def test_rejects_a_value_with_spaces(self) -> None:
        with pytest.raises(ValueError, match="values must match"):
            validate_attribution({"team": "Architecture Analytics"})

    def test_rejects_an_over_long_value(self) -> None:
        with pytest.raises(ValueError, match="values must match"):
            validate_attribution({"team": "a" * 64})

    def test_names_the_offending_key(self) -> None:
        with pytest.raises(ValueError, match="cost_center"):
            validate_attribution({"cost_center": "Data Platform"})


class TestProcessSurface:
    def test_absent_until_an_entry_point_declares_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No surface is invented — a cost model never reads one nobody set."""
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        assert "dbt_charts_surface" not in current_attribution()

    def test_set_surface_reaches_the_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        set_surface("cloud")
        assert current_attribution()["dbt_charts_surface"] == "cloud"

    def test_a_scope_overrides_the_process_surface(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        set_surface("serve")
        with attribute({"surface": "inspect"}, {}):
            assert current_attribution()["dbt_charts_surface"] == "inspect"
        assert current_attribution()["dbt_charts_surface"] == "serve"


class TestConnectionIdentity:
    """What may ride a pooled connection.

    A connection is shared by every source with the same connection identity, and a
    worker holds it across renders — so anything here must be true for every source
    and every query using it. Per-query and authored pairs ride the comment instead.
    """

    def test_carries_the_engine_constants(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        assert connection_identity()["app"] == "dbt-charts"

    def test_carries_the_process_surface(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        set_surface("serve")
        assert connection_identity()["dbt_charts_surface"] == "serve"

    def test_excludes_per_query_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`dbt_charts_board` on a shared connection would label a second source's queries."""
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        with attribute({"board": "revenue", "query": "monthly"}, {}):
            identity = connection_identity()
        assert "dbt_charts_board" not in identity
        assert "dbt_charts_query" not in identity

    def test_excludes_authored_attribution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two sources on one warehouse share a pool; a team here would mislabel one."""
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        with attribute({}, {"team": "finance"}):
            assert "team" not in connection_identity()


class TestRuntimeContext:
    def test_absent_outside_any_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        assert current_attribution() == {}

    def test_scope_sets_prefixed_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        with attribute({"surface": "cli"}, {}):
            assert current_attribution() == {"dbt_charts_surface": "cli"}

    def test_scope_unwinds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        with attribute({"surface": "cli"}, {}):
            pass
        assert current_attribution() == {}

    def test_nested_scopes_merge(self) -> None:
        with (
            attribute({"surface": "cloud"}, {}),
            attribute({"board": "gtm/weekly-revenue"}, {}),
        ):
            current = current_attribution()
        assert current["dbt_charts_surface"] == "cloud"
        assert current["dbt_charts_board"] == "gtm-weekly-revenue"

    def test_inner_scope_overrides_the_same_key(self) -> None:
        with attribute({"surface": "cli"}, {}), attribute({"surface": "inspect"}, {}):
            assert current_attribution()["dbt_charts_surface"] == "inspect"

    def test_unset_fields_are_omitted_not_emitted_empty(self) -> None:
        with attribute({"surface": "cli"}, {}):
            assert "dbt_charts_board" not in current_attribution()

    def test_rejects_an_unknown_dimension(self) -> None:
        """A typo'd dimension is a caller bug, not a key that quietly vanishes."""
        with (
            pytest.raises(ValueError, match="unknown attribution dimension"),
            attribute({"surfce": "cli"}, {}),
        ):
            pass

    def test_engine_values_are_normalized_to_the_label_charset(self) -> None:
        """Engine-owned values are ours to normalize; authored ones are validated."""
        with attribute({"board": "GTM/Weekly Revenue"}, {}):
            assert current_attribution()["dbt_charts_board"] == "gtm-weekly-revenue"


class TestActorClientRequestDimensions:
    """`actor`, `client`, `request` — per-request identity (D-05).

    These ride the same `ContextVar` seam as `board`/`query`/`target`, but the
    stakes are higher: a Cloud process serves many tenants concurrently, so
    an actor that leaked into process-global state (`_process_surface`) or a
    pooled connection's identity would attribute one tenant's query to
    another's actor. Both are silent failures, so both are pinned here.
    """

    def test_unknown_dimension_still_raises(self) -> None:
        """Adding real dimensions must not loosen the unknown-dimension guard."""
        with (
            pytest.raises(ValueError, match="unknown attribution dimension"),
            attribute({"principal": "user-1"}, {}),
        ):
            pass

    def test_actor_reaches_current_attribution(self) -> None:
        with attribute({"actor": "user-1", "client": "web", "request": "req-1"}, {}):
            current = current_attribution()
        assert current["dbt_charts_actor"] == "user-1"
        assert current["dbt_charts_client"] == "web"
        assert current["dbt_charts_request"] == "req-1"

    def test_tool_reaches_current_attribution_and_stays_off_connection_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`tool` is per-call: on a pooled connection it would mislabel the next tool."""
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        with attribute({"tool": "execute_query"}, {}):
            assert current_attribution()["dbt_charts_tool"] == "execute_query"
            assert "dbt_charts_tool" not in connection_identity()

    def test_actor_never_reaches_process_surface(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """set_surface()'s module-global dict is a cross-tenant trap for an
        actor — this must stay empty no matter what attribute() establishes."""
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        with attribute({"actor": "user-1", "client": "web", "request": "req-1"}, {}):
            pass
        assert attribution_module._process_surface == {}

    def test_actor_client_request_excluded_from_connection_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pooled connection is shared by every source with the same identity —
        an actor written there would label a second tenant's queries."""
        monkeypatch.setattr(attribution_module, "_process_surface", {})
        with attribute({"actor": "user-1", "client": "web", "request": "req-1"}, {}):
            identity = connection_identity()
        assert "dbt_charts_actor" not in identity
        assert "dbt_charts_client" not in identity
        assert "dbt_charts_request" not in identity

    def test_two_concurrent_contexts_see_only_their_own_actor(self) -> None:
        """A Cloud process serves many users concurrently — attribute()'s
        ContextVar must isolate one request's actor from another's in-flight
        scope. A `threading.Barrier` forces both scopes to be open at once so
        a leak (e.g. a module-global) would actually be observed."""
        import threading

        barrier = threading.Barrier(2)
        seen: dict[str, str] = {}

        def run(actor: str) -> None:
            with attribute({"actor": actor}, {}):
                barrier.wait(timeout=5)
                seen[actor] = current_attribution()["dbt_charts_actor"]

        threads = [
            threading.Thread(target=run, args=("tenant-a",)),
            threading.Thread(target=run, args=("tenant-b",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert seen == {"tenant-a": "tenant-a", "tenant-b": "tenant-b"}

    def test_generated_values_survive_the_label_charset(self) -> None:
        """Charset check on values as attribute() actually normalizes them, not
        on `_normalize` called in isolation — an email or a mixed-case client
        name must come out BigQuery-label-safe."""
        with attribute(
            {
                "actor": "dave@fivetran.com",
                "client": "Claude",
                "request": "REQ 123!",
            },
            {},
        ):
            current = current_attribution()
        for key in ("dbt_charts_actor", "dbt_charts_client", "dbt_charts_request"):
            value = current[key]
            assert "@" not in value
            assert value == value.lower()
            assert re.fullmatch(r"[a-z0-9_-]{0,63}", value), value
