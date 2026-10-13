"""ref_index() reads each column's ``meta.format`` from models and sources."""

from __future__ import annotations

from typing import Any

from dbt_charts.core.dbt_manifest import LoadedManifest, ref_index


def _loaded(raw: dict[str, Any]) -> LoadedManifest:
    return LoadedManifest(raw=raw, relpath="target/manifest.json", version="v1")


def test_formats_are_keyed_by_ref_name_and_source_table() -> None:
    index = ref_index(
        _loaded(
            {
                "nodes": {
                    "model.p.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "schema": "analytics",
                        "columns": {
                            "amount": {
                                "name": "amount",
                                "meta": {"format": "currency"},
                            },
                            "note": {"name": "note", "meta": {"owner": "data"}},
                            "id": {"name": "id"},
                        },
                    },
                    "model.p.plain": {
                        "resource_type": "model",
                        "name": "plain",
                        "schema": "analytics",
                    },
                },
                "sources": {
                    "source.p.raw.events": {
                        "source_name": "raw",
                        "name": "events",
                        "schema": "raw",
                        "columns": {
                            "cost": {"name": "cost", "meta": {"format": "percent"}}
                        },
                    }
                },
            }
        )
    )

    assert index.formats == {
        "orders": {"amount": "currency"},
        "raw.events": {"cost": "percent"},
    }


def test_a_non_string_meta_format_is_kept_as_authored() -> None:
    index = ref_index(
        _loaded(
            {
                "nodes": {
                    "model.p.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "schema": "analytics",
                        "columns": {"amount": {"meta": {"format": None}}},
                    }
                }
            }
        )
    )
    assert index.formats == {"orders": {"amount": None}}


def test_a_duplicate_name_takes_the_formats_of_the_node_whose_relation_won() -> None:
    def model(package: str, fmt: str) -> dict[str, Any]:
        return {
            "resource_type": "model",
            "name": "orders",
            "schema": package,
            "columns": {"amount": {"meta": {"format": fmt}}},
        }

    def source(schema: str, fmt: str) -> dict[str, Any]:
        return {
            "source_name": "raw",
            "name": "events",
            "schema": schema,
            "columns": {"cost": {"meta": {"format": fmt}}},
        }

    index = ref_index(
        _loaded(
            {
                "nodes": {
                    "model.a.orders": model("first", "currency"),
                    "model.b.orders": model("second", "percent"),
                },
                "sources": {
                    "source.a.raw.events": source("first", "currency"),
                    "source.b.raw.events": source("second", "percent"),
                },
            }
        )
    )

    assert index.refs["orders"][1] == "first"
    assert index.sources[("raw", "events")][1] == "first"
    assert index.formats == {
        "orders": {"amount": "currency"},
        "raw.events": {"cost": "currency"},
    }
