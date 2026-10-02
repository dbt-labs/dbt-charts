"""Tests for core/dbt_manifest.py — the neutral-leaf manifest loader.

Covers: candidate order, file_version-keyed memo (hit / invalidation / FIFO
eviction), the unreadable-manifest error paths, manifests that are missing
dbt-internal top-level keys or carry an unrecognized schema version, nodes
whose shape has drifted, and both real manifest fixtures.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import dbt_charts.core.dbt_manifest as _mod
from dbt_charts.core.dbt_manifest import (
    load_manifest,
    load_manifest_at,
    manifest_relpath,
    ref_index,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import ERR_DBT_TARGET_PATH_INVALID
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.project import Project

from ..._paths import DBT_CHARTS_DIR

_FIXTURES = DBT_CHARTS_DIR / "tests" / "fixtures"

_MINIMAL_MANIFEST = json.dumps(
    {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"
        },
        "nodes": {
            "model.analytics.orders": {
                "resource_type": "model",
                "name": "orders",
                "schema": "analytics",
                "alias": "orders",
            }
        },
        "sources": {},
    }
)

_FUTURE_VERSION_MANIFEST = json.dumps(
    {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v999.json"
        },
        "nodes": {
            "model.analytics.orders": {
                "resource_type": "model",
                "name": "orders",
                "schema": "analytics",
            }
        },
        "sources": {},
    }
)


@pytest.fixture(autouse=True)
def clear_manifest_memo() -> None:
    """Isolate each test from prior memo entries."""
    _mod._memo.clear()


class TestManifestCandidate:
    """target/manifest.json is the only candidate — no committed-snapshot fallback."""

    def test_target_manifest_loads(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        project = in_memory_project(
            tmp_path, {"target/manifest.json": _MINIMAL_MANIFEST}
        )

        loaded = load_manifest(project)

        assert loaded is not None
        assert loaded.relpath == "target/manifest.json"

    def test_snapshot_only_project_is_manifest_missing(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        """A project carrying only manifest.snapshot.json is manifest-missing —
        the candidate was removed, not aliased (no read-fallback)."""
        project = in_memory_project(
            tmp_path, {"manifest.snapshot.json": _MINIMAL_MANIFEST}
        )

        assert load_manifest(project) is None

    def test_stray_snapshot_file_is_ignored_when_target_manifest_present(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        """A leftover manifest.snapshot.json alongside target/manifest.json is
        never consulted — target/ wins regardless of what else sits beside it.
        (The regression detector for the removed candidate itself is
        test_snapshot_only_project_is_manifest_missing above.)"""
        target_content = json.dumps(
            {
                "metadata": {
                    "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"
                },
                "nodes": {},
                "sources": {},
            }
        )
        snapshot_content = json.dumps(
            {
                "metadata": {
                    "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"
                },
                "nodes": {
                    "model.x.sentinel": {
                        "resource_type": "model",
                        "name": "sentinel",
                        "schema": "s",
                    }
                },
                "sources": {},
            }
        )
        project = in_memory_project(
            tmp_path,
            {
                "target/manifest.json": target_content,
                "manifest.snapshot.json": snapshot_content,
            },
        )

        loaded = load_manifest(project)

        assert loaded is not None
        assert loaded.relpath == "target/manifest.json"
        assert "sentinel" not in {
            n["name"] for n in loaded.raw.get("nodes", {}).values()
        }

    def test_returns_none_when_no_manifest_present(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        project = in_memory_project(tmp_path, {})

        assert load_manifest(project) is None


class TestManifestRelpath:
    """Same precedence dbt uses: authored > DBT_TARGET_PATH > target-path: > target."""

    def test_defaults_to_target(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("DBT_TARGET_PATH", raising=False)

        assert manifest_relpath(in_memory_project(tmp_path, {})) == (
            "target/manifest.json"
        )

    def test_dbt_project_target_path(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("DBT_TARGET_PATH", raising=False)
        project = in_memory_project(
            tmp_path, {"dbt_project.yml": "name: p\ntarget-path: build/\n"}
        )

        assert manifest_relpath(project) == "build/manifest.json"

    def test_env_beats_dbt_project(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DBT_TARGET_PATH", "env_target")
        project = in_memory_project(
            tmp_path, {"dbt_project.yml": "name: p\ntarget-path: build\n"}
        )

        assert manifest_relpath(project) == "env_target/manifest.json"

    def test_authored_beats_env(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DBT_TARGET_PATH", "env_target")

        assert manifest_relpath(in_memory_project(tmp_path, {}), "target/prod/") == (
            "target/prod/manifest.json"
        )

    def test_dbt_project_without_target_path_key(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("DBT_TARGET_PATH", raising=False)
        project = in_memory_project(tmp_path, {"dbt_project.yml": "name: p\n"})

        assert manifest_relpath(project) == "target/manifest.json"


class TestInvalidTargetPath:
    """A directory the Project seam cannot read is a coded error, never a raw
    ValueError that crashes validate for boards that never set target_path."""

    @pytest.mark.parametrize("target_path", ["/abs/target", "../build", "t/{{ x }}"])
    def test_authored(
        self,
        target_path: str,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        with pytest.raises(DbtChartsError) as exc_info:
            load_manifest(in_memory_project(tmp_path, {}), target_path)

        assert exc_info.value.code is ERR_DBT_TARGET_PATH_INVALID
        assert "target_path" in str(exc_info.value)

    def test_env(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DBT_TARGET_PATH", "/ci/target")

        with pytest.raises(DbtChartsError) as exc_info:
            load_manifest(in_memory_project(tmp_path, {}))

        assert exc_info.value.code is ERR_DBT_TARGET_PATH_INVALID
        assert "DBT_TARGET_PATH" in str(exc_info.value)

    @pytest.mark.parametrize(
        "dbt_project",
        [
            "target-path: ../build\n",
            "target-path: \"{{ env_var('T') }}\"\n",
            "name: [unclosed\n",
        ],
    )
    def test_dbt_project(
        self,
        dbt_project: str,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        project = in_memory_project(tmp_path, {"dbt_project.yml": dbt_project})

        with pytest.raises(DbtChartsError) as exc_info:
            load_manifest(project)

        assert exc_info.value.code is ERR_DBT_TARGET_PATH_INVALID
        assert "dbt_project.yml" in str(exc_info.value)


class TestOptionalLoad:
    def test_unusable_target_path_reads_as_no_manifest(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DBT_TARGET_PATH", "/ci/target")
        project = in_memory_project(tmp_path, {})

        assert load_manifest(project, optional=True) is None
        with pytest.raises(DbtChartsError):
            load_manifest(project)

    def test_corrupt_manifest_still_raises(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        project = in_memory_project(tmp_path, {"target/manifest.json": "{bad"})

        with pytest.raises(DbtChartsError):
            load_manifest(project, optional=True)


class TestLoadManifestTargetPath:
    def test_loads_the_authored_path_not_the_default(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("DBT_TARGET_PATH", raising=False)
        prod = _MINIMAL_MANIFEST.replace("analytics", "prod_schema")
        project = in_memory_project(
            tmp_path,
            {
                "target/manifest.json": _MINIMAL_MANIFEST,
                "target/prod/manifest.json": prod,
            },
        )

        loaded = load_manifest(project, "target/prod")

        assert loaded is not None
        assert loaded.relpath == "target/prod/manifest.json"
        assert ref_index(loaded).refs["orders"] == ("prod_schema.orders", "prod_schema")

    def test_missing_authored_path_does_not_fall_back_to_default(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        project = in_memory_project(
            tmp_path, {"target/manifest.json": _MINIMAL_MANIFEST}
        )

        assert load_manifest(project, "target/prod") is None


class TestMemo:
    """file_version-keyed memo: hit → no re-read; invalidation on version change."""

    def test_same_version_returns_same_object(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        project = in_memory_project(
            tmp_path, {"target/manifest.json": _MINIMAL_MANIFEST}
        )

        first = load_manifest(project)
        second = load_manifest(project)

        assert first is second

    def test_memo_hit_skips_read(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project = in_memory_project(
            tmp_path, {"target/manifest.json": _MINIMAL_MANIFEST}
        )

        read_count = 0
        real_read = project.read_text

        def counting_read(relpath: str) -> str:
            nonlocal read_count
            read_count += 1
            return real_read(relpath)

        monkeypatch.setattr(project, "read_text", counting_read)

        load_manifest(project)
        assert read_count == 1

        load_manifest(project)
        assert read_count == 1  # second call hit the memo — no re-read

    def test_changed_version_invalidates_memo(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        files: dict[str, str] = {"target/manifest.json": _MINIMAL_MANIFEST}
        project = in_memory_project(tmp_path, files)

        first = load_manifest(project)

        # Change content → new file_version hash
        new_content = json.dumps(
            {
                "metadata": {
                    "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"
                },
                "nodes": {
                    "model.analytics.customers": {
                        "resource_type": "model",
                        "name": "customers",
                        "schema": "analytics",
                    }
                },
                "sources": {},
            }
        )
        files["target/manifest.json"] = new_content

        second = load_manifest(project)

        assert second is not first
        assert "customers" in {n["name"] for n in second.raw["nodes"].values()}  # type: ignore[union-attr]

    def test_memo_fifo_eviction_at_maxsize(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        """When _MEMO_MAXSIZE entries are present, adding one more evicts the oldest."""
        from dbt_charts.core.dbt_manifest import _MEMO_MAXSIZE

        # Fill the memo to capacity using distinct relpaths
        for i in range(_MEMO_MAXSIZE):
            content = json.dumps(
                {
                    "metadata": {
                        "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"
                    },
                    "nodes": {
                        f"model.x.m{i}": {
                            "resource_type": "model",
                            "name": f"m{i}",
                            "schema": "s",
                        }
                    },
                    "sources": {},
                }
            )
            project = in_memory_project(tmp_path, {f"path{i}.json": content})
            load_manifest_at(project, f"path{i}.json")

        assert len(_mod._memo) == _MEMO_MAXSIZE

        # The key for path0 (the first one loaded) should be in the memo still
        first_key = next(iter(_mod._memo))

        # One more entry evicts the oldest
        extra_content = json.dumps(
            {
                "metadata": {
                    "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"
                },
                "nodes": {},
                "sources": {},
            }
        )
        project = in_memory_project(tmp_path, {"extra.json": extra_content})
        load_manifest_at(project, "extra.json")

        assert len(_mod._memo) == _MEMO_MAXSIZE
        assert first_key not in _mod._memo


class TestSchemaVersion:
    """The schema version is not gated — the raw-dict read is version-agnostic."""

    def test_unrecognized_schema_version_loads(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        """A manifest whose version the installed dbt-core would reject still
        loads: nothing on the runtime path deserializes through dbt's typed
        contract, so the version string is metadata we do not read."""
        project = in_memory_project(
            tmp_path, {"target/manifest.json": _FUTURE_VERSION_MANIFEST}
        )

        loaded = load_manifest(project)

        assert loaded is not None
        assert ref_index(loaded).refs == {"orders": ("analytics.orders", "analytics")}


class TestErrorPaths:
    """Corrupt or unreadable manifests raise ERR-DBT-MANIFEST-UNREADABLE."""

    def test_corrupt_json_raises_unreadable(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        project = in_memory_project(
            tmp_path, {"target/manifest.json": "{ not valid json {{"}
        )

        with pytest.raises(DbtChartsError) as exc_info:
            load_manifest(project)

        assert exc_info.value.code is not None
        assert exc_info.value.code.code == "ERR-DBT-MANIFEST-UNREADABLE"

    def test_non_object_json_raises_unreadable(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        """Valid JSON that is not an object never reaches ref_index, where
        raw.get() would be an AttributeError with no diagnostic attached."""
        project = in_memory_project(tmp_path, {"target/manifest.json": "[]"})

        with pytest.raises(DbtChartsError) as exc_info:
            load_manifest(project)

        assert exc_info.value.code is not None
        assert exc_info.value.code.code == "ERR-DBT-MANIFEST-UNREADABLE"
        assert "not an object" in str(exc_info.value)

    def test_seam_coded_error_passes_through_unwrapped(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A Project implementation raising its own coded error keeps that code.

        Cloud's git-blob store raises ERR-REPO-FILE-TOO-LARGE from read_text;
        re-wrapping it would tell the author to rebuild with `dbt parse` for a
        file-size problem.
        """
        from dbt_charts.core.diagnostics.codes_execute import ERR_DBT_MANIFEST_MISSING

        project = in_memory_project(
            tmp_path, {"target/manifest.json": _MINIMAL_MANIFEST}
        )

        def raise_coded(relpath: str) -> str:
            raise ExecutionError.from_code(
                ERR_DBT_MANIFEST_MISSING, kind="ref()", paths=["target/manifest.json"]
            )

        monkeypatch.setattr(project, "read_text", raise_coded)

        with pytest.raises(DbtChartsError) as exc_info:
            load_manifest(project)

        assert exc_info.value.code is ERR_DBT_MANIFEST_MISSING

    def test_unreadable_error_names_the_relpath(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        """load_manifest_at names whatever relpath the caller passed — not
        restricted to the default manifest path."""
        project = in_memory_project(
            tmp_path, {"custom/manifest.json": "{ not valid json {{"}
        )

        with pytest.raises(DbtChartsError) as exc_info:
            load_manifest_at(project, "custom/manifest.json")

        assert "custom/manifest.json" in str(exc_info.value)


class TestMissingTopLevelKeys:
    """A manifest without macros/docs/exposures/etc. loads successfully.

    The runtime path reads the raw dict — it does NOT call
    WritableManifest.from_dict (which requires those keys). This test pins
    that invariant so a future refactor that accidentally re-introduces the
    typed round-trip on the runtime path fails loudly.
    """

    def test_manifest_without_dbt_internal_keys_loads_successfully(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        bare = json.dumps(
            {
                "metadata": {
                    "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"
                },
                "nodes": {
                    "model.analytics.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "schema": "analytics",
                        "relation_name": "analytics.orders",
                    }
                },
                "sources": {},
                # deliberately omitted: macros, docs, exposures, metrics,
                # groups, child_map, parent_map, …
            }
        )
        project = in_memory_project(tmp_path, {"target/manifest.json": bare})

        loaded = load_manifest(project)

        assert loaded is not None
        index = ref_index(loaded)
        assert "orders" in index.refs

    def test_ref_index_from_minimal_manifest(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        project = in_memory_project(
            tmp_path, {"target/manifest.json": _MINIMAL_MANIFEST}
        )
        loaded = load_manifest(project)
        assert loaded is not None

        index = ref_index(loaded)

        assert "orders" in index.refs
        assert index.refs["orders"][1] == "analytics"
        assert "orders" in index.available_refs


class TestDriftedNodeShape:
    """Nodes and sources missing the keys ref_index reads are skipped.

    Downstream this degrades into ERR-DBT-REF-UNKNOWN-NODE /
    ERR-DBT-SOURCE-UNKNOWN-TABLE with a did-you-mean — an actionable error
    naming the ref the author wrote, not an uncaught KeyError.
    """

    def test_drifted_nodes_and_sources_are_skipped(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
    ) -> None:
        drifted = json.dumps(
            {
                "metadata": {
                    "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"
                },
                "nodes": {
                    "model.analytics.orders": {
                        "resource_type": "model",
                        "name": "orders",
                    },
                    "model.analytics.customers": {
                        "resource_type": "model",
                        "name": "customers",
                        "schema": "analytics",
                    },
                },
                "sources": {
                    "source.analytics.raw.events": {
                        "source_name": "raw",
                        "name": "events",
                    },
                    "source.analytics.raw.users": {
                        "source_name": "raw",
                        "name": "users",
                        "schema": "raw",
                    },
                },
            }
        )
        project = in_memory_project(tmp_path, {"target/manifest.json": drifted})
        loaded = load_manifest(project)
        assert loaded is not None

        index = ref_index(loaded)

        assert index.available_refs == ["customers"]
        assert index.available_sources == ["raw.users"]


class TestRealManifestFixtures:
    """Both committed fixtures index through the loader.

    dbt_core_manifest came from dbt-core, fusion_manifest from dbt v2 —
    the two producers write different top-level key sets, and this is what
    pins the loader against real output rather than hand-built stubs.
    """

    @pytest.mark.parametrize(
        ("name", "refs", "sources"),
        [
            (
                "dbt_core_manifest",
                ["fct_orders", "stg_customers", "stg_orders", "stg_products"],
                ["raw.customers", "raw.orders", "raw.products"],
            ),
            (
                "fusion_manifest",
                [
                    "customers",
                    "locations",
                    "metricflow_time_spine",
                    "order_items",
                    "orders",
                    "products",
                    "stg_customers",
                    "stg_locations",
                    "stg_order_items",
                    "stg_orders",
                    "stg_products",
                    "stg_supplies",
                    "supplies",
                ],
                [
                    "ecom.raw_customers",
                    "ecom.raw_items",
                    "ecom.raw_orders",
                    "ecom.raw_products",
                    "ecom.raw_stores",
                    "ecom.raw_supplies",
                ],
            ),
        ],
    )
    def test_fixture_indexes_to_known_refs_and_sources(
        self,
        in_memory_project: Callable[..., Project],
        tmp_path: Path,
        name: str,
        refs: list[str],
        sources: list[str],
    ) -> None:
        content = (_FIXTURES / name / "manifest.json").read_text()
        project = in_memory_project(tmp_path, {"target/manifest.json": content})

        loaded = load_manifest(project)
        assert loaded is not None

        index = ref_index(loaded)

        assert index.available_refs == refs
        assert index.available_sources == sources


class TestManifestProjectDispatch:
    """load_manifest reads through project.manifest_project(), so an external
    dbt_project_dir's target/manifest.json is read, not the dct project's own."""

    def test_reads_manifest_from_external_dbt_root(self, tmp_path: Path) -> None:
        from dbt_charts.cli.filesystem_project import FilesystemProject

        project_root = tmp_path / "myproject"
        project_root.mkdir()
        external_dbt = tmp_path / "external_dbt"
        (external_dbt / "target").mkdir(parents=True)
        (external_dbt / "target" / "manifest.json").write_text(_MINIMAL_MANIFEST)

        project = FilesystemProject(project_root, dbt_root=external_dbt)

        loaded = load_manifest(project)

        assert loaded is not None
        assert loaded.raw["nodes"]

    def test_ignores_manifest_at_dct_project_root_when_external_dir_set(
        self, tmp_path: Path
    ) -> None:
        """A stray target/manifest.json at the dct project root is not read
        once dbt_project_dir points elsewhere: no cross-contamination."""
        from dbt_charts.cli.filesystem_project import FilesystemProject

        project_root = tmp_path / "myproject"
        (project_root / "target").mkdir(parents=True)
        (project_root / "target" / "manifest.json").write_text(
            json.dumps({"nodes": {}, "sources": {}})
        )
        external_dbt = tmp_path / "external_dbt"
        external_dbt.mkdir()  # no target/manifest.json here

        project = FilesystemProject(project_root, dbt_root=external_dbt)

        assert load_manifest(project) is None

    def test_same_dir_case_unregressed(self, tmp_path: Path) -> None:
        """No dbt_project_dir override -> manifest still loads from project root."""
        from dbt_charts.cli.filesystem_project import FilesystemProject

        project_root = tmp_path / "myproject"
        (project_root / "target").mkdir(parents=True)
        (project_root / "target" / "manifest.json").write_text(_MINIMAL_MANIFEST)

        project = FilesystemProject(project_root)

        loaded = load_manifest(project)

        assert loaded is not None
        assert loaded.raw["nodes"]
