"""Tests for DbtRefResolver — the one place dbt refs become relation names.

Manifest reads go through the Project seam (Cloud's git-blob store serves the
same content a local checkout does), so these drive an InMemoryProject whose
files exist nowhere on disk: a raw-Path implementation finds nothing.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.dbt_manifest import LoadedManifest, ref_index
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_DBT_CALL_UNSUPPORTED,
    ERR_DBT_MANIFEST_MISSING,
    ERR_DBT_REF_UNKNOWN_NODE,
    ERR_DBT_SOURCE_UNKNOWN_TABLE,
)
from dbt_charts.core.execute.adapters.dbt_utils import (
    DbtRefResolver,
    resolve_dbt_refs_with_provenance,
)
from dbt_charts.core.execute.dbt_jinja import has_dbt_jinja
from dbt_charts.core.project import Project

_DEV = {
    "nodes": {
        "model.test.orders": {
            "resource_type": "model",
            "name": "orders",
            "schema": "dev_dave",
            "alias": "orders",
        }
    },
    "sources": {},
}
_REF_SQL = "SELECT * FROM {{ ref('orders') }}"


class TestResolveThroughProjectSeam:
    def test_resolves_against_target_manifest(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        resolver = DbtRefResolver(
            in_memory_project(tmp_path, {"target/manifest.json": json.dumps(_DEV)})
        )

        sql, relations = resolver.resolve(_REF_SQL)

        assert sql == "SELECT * FROM dev_dave.orders"
        assert [r.ref_name for r in relations] == ["orders"]

    def test_committed_snapshot_is_not_a_manifest_source(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """A project carrying only manifest.snapshot.json is manifest-missing —
        the candidate was removed, not aliased (no read-fallback)."""
        resolver = DbtRefResolver(
            in_memory_project(tmp_path, {"manifest.snapshot.json": json.dumps(_DEV)})
        )

        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(_REF_SQL)
        assert exc_info.value.code is ERR_DBT_MANIFEST_MISSING

    def test_sql_without_dbt_jinja_is_untouched(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """No manifest read at all — plain SQL never needs one."""
        resolver = DbtRefResolver(in_memory_project(tmp_path, {}))

        sql, relations = resolver.resolve("SELECT 1 AS n")

        assert sql == "SELECT 1 AS n"
        assert relations == []


class TestResolveAgainstTargetPath:
    """A source's target_path picks the manifest its refs resolve against."""

    _PROD = {
        "nodes": {
            "model.test.orders": {
                "resource_type": "model",
                "name": "orders",
                "schema": "prod",
                "alias": "orders",
            }
        },
        "sources": {},
    }

    def _project(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> Project:
        return in_memory_project(
            tmp_path,
            {
                "target/manifest.json": json.dumps(_DEV),
                "target/prod/manifest.json": json.dumps(self._PROD),
            },
        )

    def test_each_target_path_resolves_to_its_own_relation(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        resolver = DbtRefResolver(self._project(tmp_path, in_memory_project))

        assert resolver.resolve(_REF_SQL)[0] == "SELECT * FROM dev_dave.orders"
        assert (
            resolver.resolve(_REF_SQL, "target/prod")[0] == "SELECT * FROM prod.orders"
        )
        assert resolver.resolve(_REF_SQL)[0] == "SELECT * FROM dev_dave.orders"

    def test_missing_target_path_names_the_path_and_never_uses_the_default(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        resolver = DbtRefResolver(self._project(tmp_path, in_memory_project))

        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(_REF_SQL, "target/staging")

        assert exc_info.value.code is ERR_DBT_MANIFEST_MISSING
        assert "target/staging/manifest.json" in str(exc_info.value)
        assert "target/manifest.json" not in str(exc_info.value).replace(
            "target/staging/manifest.json", ""
        )


class TestMissingManifest:
    """A ref() that cannot be resolved must name the manifest, not blame Jinja."""

    @pytest.mark.parametrize(
        ("sql", "kind"),
        [
            (_REF_SQL, "ref()"),
            ("SELECT * FROM {{ source('raw', 'events') }}", "source()"),
            # "array_ref(" contains "ref(" — the macro named must still be source().
            (
                "SELECT array_ref(x) FROM {{ source('raw', 'events') }}",
                "source()",
            ),
        ],
    )
    def test_raises_coded_error(
        self,
        sql: str,
        kind: str,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        resolver = DbtRefResolver(in_memory_project(tmp_path, {}))

        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(sql)

        assert exc_info.value.code is not None
        assert exc_info.value.code.code == "ERR-DBT-MANIFEST-MISSING"
        assert kind in str(exc_info.value)
        assert "target/manifest.json" in str(exc_info.value)

    def test_raises_without_a_project_to_read(self) -> None:
        """A host that passes no project cannot resolve a ref either — say so."""
        with pytest.raises(DbtChartsError):
            DbtRefResolver(None).resolve(_REF_SQL)


class TestConcurrentLoad:
    def test_parallel_resolve_never_sees_half_loaded_manifest(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression: the executor's worker threads share one resolver, and
        the first load marked itself done before the manifest was read —
        concurrent resolvers saw manifest=None and raised
        ERR-DBT-MANIFEST-MISSING even though the manifest existed."""
        import dbt_charts.core.dbt_manifest as dbt_manifest_mod

        project = in_memory_project(
            tmp_path, {"target/manifest.json": json.dumps(_DEV)}
        )
        real_load = dbt_manifest_mod.load_manifest

        def slow_load(
            p: Project, target_path: str | None = None
        ) -> LoadedManifest | None:
            time.sleep(0.1)
            return real_load(p)

        monkeypatch.setattr(dbt_manifest_mod, "load_manifest", slow_load)
        resolver = DbtRefResolver(project)

        n_threads = 4
        barrier = threading.Barrier(n_threads)
        errors: list[Exception] = []
        results: list[str] = []

        def worker() -> None:
            barrier.wait()
            try:
                sql, _ = resolver.resolve(_REF_SQL)
                results.append(sql)
            except DbtChartsError as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent resolve raised: {errors[0]}"
        assert results == ["SELECT * FROM dev_dave.orders"] * n_threads


_MANIFEST = {
    "nodes": {
        "model.analytics.customers": {
            "resource_type": "model",
            "name": "customers",
            "schema": "analytics",
            "alias": "customers",
        },
        "seed.analytics.raw_customers": {
            "resource_type": "seed",
            "name": "raw_customers",
            "schema": "analytics",
            "relation_name": "analytics.raw_customers",
        },
        "snapshot.analytics.customers_snapshot": {
            "resource_type": "snapshot",
            "name": "customers_snapshot",
            "schema": "snapshots",
            "relation_name": "snapshots.customers_snapshot",
        },
        "test.analytics.not_null_customers_id": {
            "resource_type": "test",
            "name": "not_null_customers_id",
            "schema": "analytics_dbt_test__audit",
        },
    },
    "sources": {
        "source.analytics.stripe.payments": {
            "source_name": "stripe",
            "name": "payments",
            "schema": "raw_stripe",
        }
    },
}

_INDEX = ref_index(
    LoadedManifest(raw=_MANIFEST, relpath="target/manifest.json", version="v1")
)


class TestCallForms:
    """Every call form `has_dbt_jinja` accepts must be one substitution rewrites.

    Detection matches on the `{{ ref(` prefix, so it admits a wider language than
    a stricter substitution pattern rewrites — and anything in the gap satisfies
    the manifest gate, then survives untouched into the SQL handed to the
    warehouse. That is the silent escape the unknown-node error exists to close,
    reached by a route that never gets to the lookup which raises it. Each case
    asserts detection *and* substitution so the two cannot drift apart again.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM {{ ref('customers') }}",
            "SELECT * FROM {{ref('customers')}}",
            "SELECT * FROM {{- ref('customers') }}",
            "SELECT * FROM {{ ref('customers') -}}",
            "SELECT * FROM {{- ref('customers') -}}",
            "SELECT * FROM {{-ref('customers')-}}",
            "SELECT * FROM {{ ref( 'customers' ) }}",
            'SELECT * FROM {{ ref("customers") }}',
        ],
    )
    def test_every_detected_ref_form_resolves(self, sql: str) -> None:
        assert has_dbt_jinja(sql), "fixture must be a form detection accepts"
        resolved, relations = resolve_dbt_refs_with_provenance(sql, _INDEX)
        assert resolved == "SELECT * FROM analytics.customers"
        assert [r.ref_name for r in relations] == ["customers"]

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM {{ source('stripe', 'payments') }}",
            "SELECT * FROM {{source('stripe','payments')}}",
            "SELECT * FROM {{- source('stripe', 'payments') }}",
            "SELECT * FROM {{ source('stripe', 'payments') -}}",
            "SELECT * FROM {{- source('stripe', 'payments') -}}",
            "SELECT * FROM {{-source('stripe','payments')-}}",
            "SELECT * FROM {{ source( 'stripe' , 'payments' ) }}",
            'SELECT * FROM {{ source("stripe", "payments") }}',
        ],
    )
    def test_every_detected_source_form_resolves(self, sql: str) -> None:
        assert has_dbt_jinja(sql), "fixture must be a form detection accepts"
        resolved, relations = resolve_dbt_refs_with_provenance(sql, _INDEX)
        assert resolved == "SELECT * FROM raw_stripe.payments"
        assert [r.ref_name for r in relations] == ["stripe.payments"]

    def test_trim_markers_are_matched_but_not_honored(self) -> None:
        """Jinja would strip the neighboring space; in SQL that welds tokens."""
        sql = "SELECT * FROM {{- ref('customers') -}} WHERE id > 0"
        resolved, _ = resolve_dbt_refs_with_provenance(sql, _INDEX)
        assert resolved == "SELECT * FROM analytics.customers WHERE id > 0"

    def test_adjacent_calls_resolve_independently(self) -> None:
        """The quoted-argument pattern must not span from one call into the next."""
        sql = "SELECT * FROM {{ ref('customers') }} JOIN {{ ref('raw_customers') }}"
        resolved, relations = resolve_dbt_refs_with_provenance(sql, _INDEX)
        assert resolved == (
            "SELECT * FROM analytics.customers JOIN analytics.raw_customers"
        )
        assert [r.ref_name for r in relations] == ["customers", "raw_customers"]

    def test_trimmed_unknown_ref_raises_rather_than_surviving(self) -> None:
        """The trim form must reach the unknown-node error, not slip past it."""
        with pytest.raises(DbtChartsError) as exc_info:
            resolve_dbt_refs_with_provenance(
                "SELECT * FROM {{- ref('customer') -}}", _INDEX
            )
        assert exc_info.value.code is ERR_DBT_REF_UNKNOWN_NODE


class TestUnsupportedCallForms:
    """A call detection routed here but substitution can't rewrite must raise.

    Detection matches the `{{ ref(` prefix; substitution needs a fully-parsed
    call. No amount of shared pattern-building closes that gap — a prefix
    matcher always accepts more than a parser. So the resolver checks its own
    output instead: anything still Jinja after substitution is a form this
    engine does not support, and saying so beats handing `{{` to the warehouse
    or guessing at what the author meant.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            # Package-qualified. Real dbt syntax; resolving it would mean
            # scoping the manifest lookup by package, which this engine does
            # not do — and ignoring the package would silently pick whichever
            # same-named model came first.
            "SELECT * FROM {{ ref('other_pkg', 'customers') }}",
            # Versioned refs.
            "SELECT * FROM {{ ref('customers', version=2) }}",
            "SELECT * FROM {{ ref('customers', v=2) }}",
            # Macro-computed argument — not a literal we can read.
            "SELECT * FROM {{ ref(var('model_name')) }}",
            "SELECT * FROM {{ source(var('src'), 'payments') }}",
        ],
    )
    def test_unrewritable_call_raises_instead_of_surviving(self, sql: str) -> None:
        assert has_dbt_jinja(sql), "fixture must be a form detection accepts"
        with pytest.raises(DbtChartsError) as exc_info:
            resolve_dbt_refs_with_provenance(sql, _INDEX)
        assert exc_info.value.code is ERR_DBT_CALL_UNSUPPORTED

    def test_diagnostic_quotes_the_offending_call(self) -> None:
        with pytest.raises(DbtChartsError) as exc_info:
            resolve_dbt_refs_with_provenance(
                "SELECT * FROM {{ ref('other_pkg', 'customers') }}", _INDEX
            )
        assert "ref('other_pkg', 'customers')" in str(exc_info.value)

    def test_a_resolvable_call_alongside_an_unsupported_one_still_raises(self) -> None:
        """Partial rewriting must not ship: one bad call fails the whole query."""
        sql = (
            "SELECT * FROM {{ ref('customers') }} "
            "JOIN {{ ref('customers', version=2) }}"
        )
        with pytest.raises(DbtChartsError) as exc_info:
            resolve_dbt_refs_with_provenance(sql, _INDEX)
        assert exc_info.value.code is ERR_DBT_CALL_UNSUPPORTED


class TestRefableNodeKinds:
    """`ref()` addresses seeds and snapshots too, not just models."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("customers", "analytics.customers"),
            ("raw_customers", "analytics.raw_customers"),
            ("customers_snapshot", "snapshots.customers_snapshot"),
        ],
    )
    def test_ref_resolves_every_refable_node_kind(
        self, name: str, expected: str
    ) -> None:
        sql, relations = resolve_dbt_refs_with_provenance(
            f"SELECT * FROM {{{{ ref('{name}') }}}}", _INDEX
        )
        assert sql == f"SELECT * FROM {expected}"
        assert [r.ref_name for r in relations] == [name]

    def test_ref_to_a_test_node_is_not_resolvable(self) -> None:
        """Data tests share the nodes map but are not addressable by ref()."""
        sql = "SELECT * FROM {{ ref('not_null_customers_id') }}"
        with pytest.raises(DbtChartsError) as exc_info:
            resolve_dbt_refs_with_provenance(sql, _INDEX)
        assert exc_info.value.code is ERR_DBT_REF_UNKNOWN_NODE

    def test_available_candidates_span_every_refable_kind(self) -> None:
        """The 'available' list must not lie about what ref() can reach."""
        sql = "SELECT * FROM {{ ref('nope') }}"
        with pytest.raises(DbtChartsError) as exc_info:
            resolve_dbt_refs_with_provenance(sql, _INDEX)
        available = exc_info.value.fields["available"]
        assert available == ["customers", "customers_snapshot", "raw_customers"]


class TestUnknownRef:
    def test_ref_to_unknown_node_raises(self) -> None:
        """A typo'd ref() must raise, not silently become a bare table name."""
        sql = "SELECT * FROM {{ ref('customer') }}"  # missing trailing 's'
        with pytest.raises(DbtChartsError) as exc_info:
            resolve_dbt_refs_with_provenance(sql, _INDEX)
        assert exc_info.value.code is ERR_DBT_REF_UNKNOWN_NODE
        assert exc_info.value.fields["ref_name"] == "customer"

    def test_ref_to_unknown_node_diagnostic_has_did_you_mean(self) -> None:
        sql = "SELECT * FROM {{ ref('customer') }}"
        with pytest.raises(DbtChartsError) as exc_info:
            resolve_dbt_refs_with_provenance(sql, _INDEX)
        diag = exc_info.value.to_diagnostic()
        assert "ref('customer')" in diag.message
        assert diag.hint is not None
        assert "customers" in diag.hint


class TestUnknownSource:
    def test_source_to_unknown_table_raises(self) -> None:
        sql = "SELECT * FROM {{ source('stripe', 'charges') }}"
        with pytest.raises(DbtChartsError) as exc_info:
            resolve_dbt_refs_with_provenance(sql, _INDEX)
        assert exc_info.value.code is ERR_DBT_SOURCE_UNKNOWN_TABLE

    def test_unknown_source_name_hint_keys_on_the_source_table_pair(self) -> None:
        """The hint compares `source.table`, so a wrong source still suggests."""
        sql = "SELECT * FROM {{ source('braintree', 'payments') }}"
        with pytest.raises(DbtChartsError) as exc_info:
            resolve_dbt_refs_with_provenance(sql, _INDEX)
        assert exc_info.value.code is ERR_DBT_SOURCE_UNKNOWN_TABLE
        diag = exc_info.value.to_diagnostic()
        assert diag.hint is not None
        assert "stripe.payments" in diag.hint
