"""Import an Omni dashboard into a source bundle (Omni REST API, stdlib only).

The raw definition is the beta dashboard export
(``/api/unstable/documents/<id>/export``): it is the only endpoint that carries
the tile layout, pages and dashboard filters, so a breaking change there fails
the import loudly. Generated SQL comes from planning each saved query
(``planOnly``); saved queries carry their own filters, so SQL does not reflect
dashboard filter defaults (those are listed in the outline).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dbt_charts.agent_api.source_bundle import (
    BundleError,
    SourceBundle,
    compact,
    option_lines,
    public_url,
    staged_bundle,
)

RAW = "dashboard.json"
POLL_SECONDS = 2.0
POLL_LIMIT = 150
TIMEOUT_SECONDS = 120
# Omni allows 60 requests per minute per key and answers 429 beyond that.
RATE_LIMIT_SECONDS = 10.0
RATE_LIMIT_RETRIES = 12
# Container children that are controls, not tiles; filters are read from filterConfig.
NON_TILE_TYPES = ("filter", "control", "inline-page-switcher")
# Saved-query keys that are identity, Omni internals, or shown as their own lines.
QUERY_NON_OPTION_KEYS = frozenset(
    {
        "calculations",
        "dbtMode",
        "default_group_by",
        "dimensionIndex",
        "fields",
        "filters",
        "join_paths_from_topic_name",
        "manualSort",
        "model_extension_id",
        "modelId",
        "parsed",
        "pivots",
        "rewriteSql",
        "sorts",
        "table",
        "version",
    }
)
# Query-presentation keys that are identity or internals; `visConfig` and the
# query are expanded separately, `resultConfig` is table styling.
PRESENTATION_NON_OPTION_KEYS = frozenset(
    {
        "automaticVis",
        "createdAt",
        "editingModelObjectName",
        "filterOrder",
        "id",
        "isSql",
        "miniUuid",
        "modelId",
        "name",
        "organizationId",
        "prefersChart",
        "query",
        "queryId",
        "resultConfig",
        "topicName",
        "type",
        "updatedAt",
        "visConfig",
        "visConfigId",
    }
)
# visConfig keys that are identity or internals; `spec` is expanded and
# `fields` is the tile's field list.
VIS_NON_OPTION_KEYS = frozenset(
    {"createdAt", "fields", "id", "jsonHash", "spec", "version"}
)
# visConfig.spec keys the dbt Charts theme owns: fonts, banding, alignment and
# per-column width (`columnFormats` holds only align and width).
SPEC_STYLING_KEYS = frozenset(
    {
        "alignment",
        "columnFormats",
        "rowBanding",
        "styles",
        "tableType",
        "truncateHeaders",
        "verticalAlignment",
    }
)
# Hex colors, palette names, widths and alignment inside values that carry
# meaning (conditional-format rules, column formats, series).
NESTED_STYLING_KEYS = frozenset(
    {
        "_scheme",
        "align",
        "backgroundColor",
        "color",
        "fontSize",
        "fontWeight",
        "width",
    }
)
FILTER_KEYS = ("ui_type", "isFiscal", "left_side", "right_side", "values")


def parse_ref(ref: str, *, base_url: str | None) -> tuple[str, str]:
    """Return ``(dashboard_id, origin)`` for a dashboard URL or bare id."""
    if ref.startswith(("http://", "https://")):
        parts = urllib.parse.urlsplit(ref)
        segments = parts.path.strip("/").split("/")
        if "dashboards" not in segments[:-1]:
            raise BundleError(
                f"{ref!r} is not an Omni dashboard URL (/dashboards/<id>)"
            )
        dashboard_id = segments[segments.index("dashboards") + 1]
        origin = f"{parts.scheme}://{parts.netloc}"
    else:
        dashboard_id, origin = ref, None
    return dashboard_id, _origin(base_url or origin)


def _origin(base_url: str | None) -> str:
    if not base_url:
        raise BundleError(
            "pass --base-url or set OMNI_BASE_URL (e.g. https://acme.omniapp.co); "
            "a bare dashboard id carries no origin"
        )
    return base_url.rstrip("/")


class _Omni:
    def __init__(self, origin: str) -> None:
        if not os.environ.get("OMNI_API_KEY"):
            raise BundleError("set OMNI_API_KEY to import from Omni")
        self.origin = origin
        self.headers = {
            "Authorization": f"Bearer {os.environ['OMNI_API_KEY']}",
            "Content-Type": "application/json",
        }

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> bytes:
        request = urllib.request.Request(
            f"{self.origin}/api{path}",
            method=method,
            headers=self.headers,
            data=None if body is None else json.dumps(body).encode(),
        )
        for _ in range(RATE_LIMIT_RETRIES):
            try:
                with urllib.request.urlopen(
                    request, timeout=TIMEOUT_SECONDS
                ) as response:  # noqa: S310 — http(s) URL supplied by the user
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code != 429:
                    raise BundleError(
                        f"Omni {method} {path} failed: HTTP {exc.code}"
                    ) from None
                time.sleep(RATE_LIMIT_SECONDS)
            except urllib.error.URLError as exc:
                raise BundleError(
                    f"Omni {method} {path} failed: {exc.reason}"
                ) from None
        raise BundleError(f"Omni {method} {path} failed: HTTP 429 (rate limit)")

    def get_json(self, path: str) -> Any:  # Omni response bodies are untyped JSON
        return json.loads(self.request("GET", path))

    def plan(self, query: dict[str, Any]) -> tuple[str, str]:
        """Return ``(sql, query_timezone)`` for a saved query without running it."""
        reply = self.request(
            "POST", "/v1/query/run", {"query": query, "planOnly": True}
        )
        jobs = [json.loads(line) for line in reply.decode().splitlines() if line]
        planned = [j for j in jobs if "summary" in j]
        if len(planned) != 1 or planned[0]["status"] != "PLANNED":
            raise BundleError(f"Omni did not plan the query: {reply.decode()[:300]}")
        summary = planned[0]["summary"]
        for field in ("display_sql", "query_timezone"):
            if field not in summary:
                raise BundleError(f"Omni's query plan has no {field}")
        return summary["display_sql"], summary["query_timezone"]

    def render_png(self, dashboard_id: str, page: str | None) -> bytes:
        path = f"/v1/dashboards/{dashboard_id}/download"
        body: dict[str, Any] = {"format": "png"}
        if page:
            body["containerPages"] = [page]
        job = json.loads(self.request("POST", path, body))["job_id"]
        for _ in range(POLL_LIMIT):
            status = self.get_json(f"{path}/{job}/status")
            if status["status"] == "complete":
                return self.request("GET", f"{path}/{job}")
            if status["status"] == "error":
                raise BundleError(f"Omni download {job} failed: {status.get('error')}")
            time.sleep(POLL_SECONDS)
        raise BundleError(f"Omni download {job} did not finish in time")


@dataclass(frozen=True)
class OmniDashboard:
    identifier: str
    scope: str
    name: str
    url: str


def list_omni_dashboards(*, base_url: str | None) -> list[OmniDashboard]:
    """Return every non-deleted document that has a dashboard, across all pages."""
    omni = _Omni(_origin(base_url))
    found: list[OmniDashboard] = []
    cursor = ""
    while True:
        page = omni.get_json(f"/v1/documents?pageSize=100{cursor}")
        for record in page["records"]:
            if record["hasDashboard"] and not record["deleted"]:
                try:
                    found.append(
                        OmniDashboard(
                            record["identifier"],
                            record["scope"],
                            record["name"],
                            record["url"],
                        )
                    )
                except KeyError as exc:
                    raise BundleError(
                        f"Omni document listing lacks {exc.args[0]!r}: {record}"
                    ) from None
        if not page["pageInfo"]["hasNextPage"]:
            return found
        cursor = f"&cursor={urllib.parse.quote(page['pageInfo']['nextCursor'])}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "tile"


def _describe(spec: dict[str, Any]) -> str:
    parts = [spec.get("kind") or spec["type"]]
    parts += [
        f"{key}={spec[key]}"
        for key in FILTER_KEYS
        if spec.get(key) not in (None, False, [], "")
    ]
    return " ".join(parts)


def _walk(node: dict[str, Any], path: list[str]) -> Iterator[tuple[str, str]]:
    """Yield ``(tile_id, where)``; text tiles yield ``("text:<content>", where)``."""
    for child in node.get("children", []):
        where = " > ".join(path)
        if child.get("type") == "query":
            if child.get("as") != "metadata":
                yield child["id"], where
        elif child.get("type") == "inline-text":
            yield f"text:{child['content']}", where
        elif "children" in child:
            yield from _walk(child, [*path, child.get("name") or "(unnamed)"])
        elif child.get("type") not in NON_TILE_TYPES:
            raise BundleError(
                f"unsupported tile type {child.get('type')!r} "
                f"(id {child.get('id') or child.get('instanceKey')}) in {where or 'page layout'}"
            )


def _title(text: str) -> str:
    lines = [line.lstrip("# ").strip() for line in text.splitlines()]
    return next((line for line in lines if line), "(empty)")


def import_omni(
    ref: str, *, out: Path | None, base_url: str | None, force: bool
) -> Path:
    """Write a source bundle for one Omni dashboard into ``out``.

    ``out`` defaults to ``./omni-<dashboard id>``. The bundle is built beside
    ``out`` and moved into place once complete; a non-empty ``out`` is refused
    unless ``force``.

    Raises:
        BundleError: on a missing key, any failed request, a failed render, an
            unsupported tile, or an incomplete bundle.
    """
    dashboard_id, origin = parse_ref(ref, base_url=base_url)
    out = out or Path(f"omni-{_slug(dashboard_id)}")
    omni = _Omni(origin)
    raw = omni.request("GET", f"/unstable/documents/{dashboard_id}/export")
    url = public_url(
        ref if ref.startswith("http") else f"{origin}/dashboards/{dashboard_id}"
    )
    with staged_bundle(out, tool="omni", url=url, raw=RAW, force=force) as bundle:
        bundle.write(RAW, raw)
        _fill_bundle(omni, bundle, json.loads(raw)["dashboard"], dashboard_id, url)
    return out


def _fill_bundle(
    omni: _Omni,
    bundle: SourceBundle,
    dashboard: dict[str, Any],
    dashboard_id: str,
    url: str,
) -> None:
    metadata = dashboard["metadata"]
    collection = dashboard["queryPresentationCollection"]

    saved = {
        q["queryIdentifierMapKey"]: q
        for q in omni.get_json(f"/v1/documents/{dashboard_id}/queries")["queries"]
    }
    presentations = {
        m["queryPresentation"]["id"]: m["queryPresentation"]
        for m in collection["queryPresentationCollectionMemberships"]
    }
    texts = {t["miniUuid"]: t["content"] for t in metadata["textTiles"]}

    pages: list[tuple[str | None, str | None, list[tuple[str, str]]]]
    # Absent (not empty) on dashboards that use the plain grid layout.
    if metadata.get("containers"):
        pages = [
            (p["name"], p["container"]["instanceKey"], list(_walk(p["container"], [])))
            for p in metadata["containers"]
        ]
    else:
        grid = sorted(metadata["layouts"]["lg"], key=lambda t: (t["y"], t["x"]))
        placed = [
            (
                f"text:{texts[t['i']]}" if t["i"] in texts else t["i"],
                f"y {t['y']}, x {t['x']}, {t['w']}x{t['h']}",
            )
            for t in grid
        ]
        pages = [(None, None, placed)]

    names = [_slug(name) for name, _, _ in pages if name]
    if len(set(names)) != len(names):
        raise BundleError(
            "dashboard page names collide as file names: "
            + ", ".join(name for name, _, _ in pages if name)
        )
    timezones: set[str] = set()
    fetched: set[str] = set()
    tiles: list[str] = []
    for name, _, placements in pages:
        if name:
            tiles += ["", f"## Page: {name}", ""]
        for tile_id, where in placements:
            if tile_id.startswith("text:"):
                tiles.append(f"- **text** {_title(tile_id[5:])!r} — {where}")
                continue
            if tile_id not in saved:
                raise BundleError(
                    f"layout tile {tile_id} has no saved query in the document"
                )
            if tile_id in fetched:
                continue
            fetched.add(tile_id)
            query = saved[tile_id]
            presentation = presentations[query["id"]]
            chart = presentation["visConfig"]["chartType"]
            tiles.append(f"- **{chart}** `{tile_id}` {query['name']!r} — {where}")
            sql, timezone = omni.plan(query["query"])
            timezones.add(timezone)
            bundle.write(f"queries/{tile_id}.json", json.dumps(query, indent=2))
            bundle.write(f"queries/{tile_id}.sql", sql)
            tiles += [
                f"    - {line}"
                for line in _tile_lines(tile_id, query, presentation, metadata)
            ]
            if chart == "kpi":
                kpi_file = f"kpi/{_slug(query['name'])}-{tile_id}.csv"
                bundle.write(
                    kpi_file,
                    omni.request(
                        "POST",
                        f"/v1/documents/{dashboard_id}/queries/{tile_id}/run",
                        {"resultType": "csv"},
                    ),
                )
                tiles.append(f"    - values: {kpi_file}")
    tiles += [
        f"- **hidden** `{key}` {saved[key]['name']!r} — not on the dashboard, not fetched"
        for key in metadata["hiddenTiles"]
    ]

    filters = [
        f"- {spec['label']} `{key}`"
        f"{' ' + spec['fieldName'] if 'fieldName' in spec else ''}: {_describe(spec)}"
        for key in collection["filterOrder"]
        for spec in [collection["filterConfig"][key]]
    ]
    bundle.write(
        "outline.md",
        "\n".join(
            [
                f"# {dashboard['name']}",
                "",
                f"Omni: {url}",
                "",
                "## Dashboard filters",
                "",
                *filters,
                "",
                "## Tiles",
                *tiles,
                "",
            ]
        ),
    )

    for name, instance_key, _ in pages:
        png = f"png/{_slug(name)}.png" if name else "png/dashboard.png"
        bundle.write(png, omni.render_png(dashboard_id, instance_key))
    bundle.finish(
        extra={
            "query_timezones": sorted(timezones),
            "kpi_values": "display_formatted",
        }
    )


def _tile_lines(
    tile_id: str,
    query: dict[str, Any],
    presentation: dict[str, Any],
    metadata: dict[str, Any],
) -> list[str]:
    spec = query["query"]
    vis = presentation["visConfig"]
    lines = [
        f"queries/{tile_id}.sql ({spec['table']})",
        f"config: queries/{tile_id}.json, "
        f"dashboard.json#queryPresentation[{presentation['id']}]",
        "fields: " + ", ".join(spec["fields"]),
    ]
    if spec["pivots"]:
        lines.append("pivots: " + ", ".join(spec["pivots"]))
    lines += [
        f"sort: {s['column_name']} {'desc' if s['sort_descending'] else 'asc'}"
        for s in spec["sorts"]
    ]
    lines += [
        f"table calc: {c['calc_name']} {c['label']!r}" + _calc_rest(c)
        for c in spec["calculations"]
    ]
    lines += [f"filter: {field} {_describe(f)}" for field, f in spec["filters"].items()]
    for filter_id, field in metadata["tileFilterMap"].get(tile_id, {}).items():
        if field:
            lines.append(f"listens: {filter_id} -> {field}")
    for options, dropped in (
        (spec, QUERY_NON_OPTION_KEYS),
        (presentation, PRESENTATION_NON_OPTION_KEYS),
        (vis, VIS_NON_OPTION_KEYS),
        (vis.get("spec", {}), SPEC_STYLING_KEYS),
    ):
        lines += option_lines(
            options,
            dropped=dropped.__contains__,
            nested_dropped=NESTED_STYLING_KEYS,
        )
    return lines


def _calc_rest(calc: dict[str, Any]) -> str:
    rest = {k: v for k, v in calc.items() if k not in ("calc_name", "label")}
    return f" = {compact(rest)}" if rest else ""
