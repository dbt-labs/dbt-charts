"""Tests for the tool-independent source bundle writer and layout check."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dbt_charts.agent_api.source_bundle import (
    BundleError,
    SourceBundle,
    check_layout,
    staged_bundle,
)


def _complete(bundle: SourceBundle) -> None:
    bundle.write("outline.md", "# outline")
    bundle.write("dashboard.json", b'{"id": 1}')
    bundle.write("queries/a.sql", "SELECT 1")
    bundle.write("png/dashboard.png", b"png")


def test_finish_writes_source_json_with_hash_of_raw_definition(
    tmp_path: Path,
) -> None:
    bundle = SourceBundle(
        tmp_path, tool="looker", url="https://x/1", raw="dashboard.json"
    )
    _complete(bundle)

    bundle.finish(extra={"query_timezones": ["UTC"]})

    source = json.loads((tmp_path / "source.json").read_text())
    assert source["tool"] == "looker"
    assert source["url"] == "https://x/1"
    assert source["raw_sha256"] == hashlib.sha256(b'{"id": 1}').hexdigest()
    assert source["query_timezones"] == ["UTC"]
    assert source["fetched_at"]


@pytest.mark.parametrize(
    "missing",
    ["outline.md", "dashboard.json", "queries/a.sql", "png/dashboard.png"],
)
def test_finish_names_the_missing_piece(tmp_path: Path, missing: str) -> None:
    bundle = SourceBundle(tmp_path, tool="looker", url="u", raw="dashboard.json")
    _complete(bundle)
    (tmp_path / missing).unlink()

    with pytest.raises(BundleError) as exc:
        bundle.finish()

    assert missing.split("/")[0] in str(exc.value)


def test_check_layout_requires_kpi_directory(tmp_path: Path) -> None:
    bundle = SourceBundle(tmp_path, tool="looker", url="u", raw="dashboard.json")
    _complete(bundle)
    bundle.finish()
    (tmp_path / "kpi").rmdir()

    with pytest.raises(BundleError, match="kpi"):
        check_layout(tmp_path, raw="dashboard.json")


def test_staged_bundle_replaces_a_stale_directory_only_with_force(
    tmp_path: Path,
) -> None:
    out = tmp_path / "bundle"
    (out / "kpi").mkdir(parents=True)
    (out / "kpi" / "stale.csv").write_text("old")

    with (
        pytest.raises(BundleError, match="not empty"),
        staged_bundle(out, tool="t", url="u", raw="r.json", force=False),
    ):
        pytest.fail("must not stage into a non-empty directory")

    with staged_bundle(out, tool="t", url="u", raw="r.json", force=True) as bundle:
        bundle.write("r.json", "{}")
        bundle.write("outline.md", "#")
        bundle.write("queries/a.sql", "SELECT 1")
        bundle.write("png/a.png", b"p")
        bundle.finish()

    assert not (out / "kpi" / "stale.csv").exists()
    assert (out / "source.json").is_file()
    assert [p.name for p in tmp_path.iterdir()] == ["bundle"]


def test_staged_bundle_leaves_existing_directory_and_no_temp_on_failure(
    tmp_path: Path,
) -> None:
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "keep.txt").write_text("previous")

    with (
        pytest.raises(BundleError),
        staged_bundle(out, tool="t", url="u", raw="r.json", force=True) as bundle,
    ):
        bundle.finish()

    assert (out / "keep.txt").read_text() == "previous"
    assert [p.name for p in tmp_path.iterdir()] == ["bundle"]
