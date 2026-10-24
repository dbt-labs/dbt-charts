"""Import a Looker dashboard into a source bundle (Looker API 4.0, stdlib only).

Credentials come from ``LOOKER_CLIENT_ID`` / ``LOOKER_CLIENT_SECRET``. The
access token lives in memory for the run and is never written anywhere. The API
base comes from ``--base-url`` / ``LOOKER_BASE_URL``, else from the origin of a
dashboard URL; the API port differs by instance (often ``:19999``), so a URL on
the UI port needs the explicit base.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dbt_charts.agent_api.source_bundle import (
    BundleError,
    SourceBundle,
    public_url,
    slug,
    staged_bundle,
    url_origin,
)

API_SUFFIX = "/api/4.0"
RAW = "dashboard.json"
POLL_SECONDS = 2.0
POLL_LIMIT = 150
TIMEOUT_SECONDS = 120


def parse_ref(ref: str, *, base_url: str | None) -> tuple[str, str]:
    """Return ``(dashboard_id, api_base)`` for a dashboard URL or bare id."""
    if ref.startswith(("http://", "https://")):
        parts = urllib.parse.urlsplit(ref)
        segments = parts.path.strip("/").split("/")
        marker = next(
            (
                i
                for i, s in enumerate(segments)
                if s in ("dashboards", "dashboards-next")
            ),
            None,
        )
        if marker is None or marker + 1 >= len(segments):
            raise BundleError(
                f"{ref!r} is not a Looker dashboard URL (/dashboards/<id>)"
            )
        dashboard_id = segments[marker + 1]
        origin = url_origin(parts)
    else:
        dashboard_id, origin = ref, None
    if base_url:
        origin = base_url.rstrip("/").removesuffix(API_SUFFIX)
    if origin is None:
        raise BundleError(
            "a bare dashboard id needs the API base: pass --base-url or set "
            "LOOKER_BASE_URL (e.g. https://acme.looker.com:19999)"
        )
    return dashboard_id, f"{origin}{API_SUFFIX}"


def _send(
    api_base: str,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes | None,
) -> bytes:
    request = urllib.request.Request(
        f"{api_base}{path}", data=body, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 — http(s) URL supplied by the user
            return response.read()
    except urllib.error.HTTPError as exc:
        raise BundleError(f"Looker {method} {path} failed: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise BundleError(f"Looker {method} {path} failed: {exc.reason}") from None


def _login(api_base: str) -> str:
    credentials: dict[str, str] = {}
    for key in ("LOOKER_CLIENT_ID", "LOOKER_CLIENT_SECRET"):
        if not os.environ.get(key):
            raise BundleError(f"set {key} to import from Looker")
        credentials[key] = os.environ[key]
    body = urllib.parse.urlencode(
        {
            "client_id": credentials["LOOKER_CLIENT_ID"],
            "client_secret": credentials["LOOKER_CLIENT_SECRET"],
        }
    ).encode()
    reply = _send(api_base, "POST", "/login", {}, body)
    return json.loads(reply)["access_token"]


class _Looker:
    def __init__(self, api_base: str) -> None:
        self.api_base = api_base
        self.headers = {"Authorization": f"token {_login(api_base)}"}

    def request(self, method: str, path: str, *, body: bytes | None = None) -> bytes:
        return _send(self.api_base, method, path, self.headers, body)

    def get(self, path: str) -> bytes:
        return self.request("GET", path)

    def get_json(self, path: str) -> Any:
        return json.loads(self.get(path))

    def render_png(self, dashboard_id: str) -> bytes:
        quoted = urllib.parse.quote(dashboard_id, safe=":")
        task = json.loads(
            self.request(
                "POST",
                f"/render_tasks/dashboards/{quoted}/png?width=1600&height=1200",
                body=b"{}",
            )
        )["id"]
        for _ in range(POLL_LIMIT):
            status = self.get_json(f"/render_tasks/{task}")["status"]
            if status == "success":
                return self.get(f"/render_tasks/{task}/results")
            if status == "failure":
                raise BundleError(f"Looker render task {task} failed")
            time.sleep(POLL_SECONDS)
        raise BundleError(f"Looker render task {task} did not finish in time")


def _flatten(row: dict[str, Any]) -> dict[str, Any]:
    """Pivoted results nest ``{field: {pivot value: v}}``; make them ``field|pivot``."""
    flat: dict[str, Any] = {}
    for field, value in row.items():
        if isinstance(value, dict):
            flat.update({f"{field}|{pivot}": v for pivot, v in value.items()})
        else:
            flat[field] = value
    return flat


def _csv(rows: list[dict[str, Any]], fallback_fields: list[str]) -> str:
    flat_rows = [_flatten(row) for row in rows]
    out = io.StringIO()
    writer = csv.DictWriter(
        out, fieldnames=list(flat_rows[0]) if flat_rows else fallback_fields
    )
    writer.writeheader()
    writer.writerows(flat_rows)
    return out.getvalue()


DYNAMIC_FIELD_KINDS = {
    "table_calculation": "table calc",
    "measure": "custom measure",
    "dimension": "custom dimension",
}


def _dynamic_field_line(field: dict[str, Any], tile: str) -> str:
    for key, label in DYNAMIC_FIELD_KINDS.items():
        if key in field:
            detail = field.get("expression") or field.get("based_on")
            return f"{label}: {field[key]}" + (f" = {detail}" if detail else "")
    raise BundleError(f"tile {tile}: unrecognized dynamic field {sorted(field)}")


def _tile_lines(
    element: dict[str, Any], query: dict[str, Any] | None, tile: str
) -> list[str]:
    lines: list[str] = []
    if query is not None:
        lines.append(f"queries/{query['id']}.sql ({query['model']}/{query['view']})")
        lines.append("fields: " + ", ".join(query["fields"]))
        if query["pivots"]:
            lines.append("pivots: " + ", ".join(query["pivots"]))
        # Looker sends dynamic_fields as a JSON-encoded string.
        for field in json.loads(query["dynamic_fields"] or "[]"):
            lines.append(_dynamic_field_line(field, tile))
        for field, value in (query["filters"] or {}).items():
            lines.append(f"filter: {field} = {value}")
    # result_maker is null on text tiles; listen is null on unlinked filterables.
    for filterable in (element.get("result_maker") or {}).get("filterables") or []:
        for listen in filterable.get("listen") or []:
            lines.append(
                f"listens: {listen['dashboard_filter_name']} -> {listen['field']}"
            )
    return lines


def _active_components(dashboard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    active = [layout for layout in dashboard["dashboard_layouts"] if layout["active"]]
    if not active:
        raise BundleError("dashboard has no active layout")
    return {
        str(c["dashboard_element_id"]): c
        for c in active[0]["dashboard_layout_components"]
    }


def import_looker(
    ref: str, *, out: Path | None, base_url: str | None, force: bool
) -> Path:
    """Write a source bundle for one Looker dashboard into ``out``.

    ``out`` defaults to ``./looker-<dashboard id>``. The bundle is built beside
    ``out`` and moved into place once complete; a non-empty ``out`` is refused
    unless ``force``.

    Raises:
        BundleError: on missing credentials, any failed request, a failed render,
            an unsupported tile, or an incomplete bundle.
    """
    dashboard_id, api_base = parse_ref(ref, base_url=base_url)
    out = out or Path(f"looker-{slug(dashboard_id, fallback='tile')}")
    looker = _Looker(api_base)
    quoted = urllib.parse.quote(dashboard_id, safe=":")
    raw = looker.get(f"/dashboards/{quoted}")
    dashboard = json.loads(raw)
    url = (
        public_url(ref) if ref.startswith("http") else f"{api_base}/dashboards/{quoted}"
    )
    with staged_bundle(out, tool="looker", url=url, raw=RAW, force=force) as bundle:
        bundle.write(RAW, raw)
        _fill_bundle(looker, bundle, dashboard, dashboard_id, url)
    return out


def _fill_bundle(
    looker: _Looker,
    bundle: SourceBundle,
    dashboard: dict[str, Any],
    dashboard_id: str,
    url: str,
) -> None:
    queries: dict[str, dict[str, Any]] = {}

    def fetch_query(query_id: str) -> dict[str, Any]:
        if query_id not in queries:
            queries[query_id] = looker.get_json(f"/queries/{query_id}")
            bundle.write(
                f"queries/{query_id}.json", json.dumps(queries[query_id], indent=2)
            )
            bundle.write(
                f"queries/{query_id}.sql", looker.get(f"/queries/{query_id}/run/sql")
            )
        return queries[query_id]

    placed = _active_components(dashboard)
    elements = {str(e["id"]): e for e in dashboard["dashboard_elements"]}
    reading_order = sorted(
        placed, key=lambda i: (placed[i]["row"], placed[i]["column"])
    )
    reading_order += [i for i in elements if i not in placed]

    tiles: list[str] = []
    for element_id in reading_order:
        element = elements[element_id]
        title = element["title"] or f"({element['type']} tile)"
        tile = f"{title!r} ({element_id})"
        position = placed.get(element_id)
        where = (
            f"row {position['row']}, col {position['column']}, "
            f"{position['width']}x{position['height']}"
            if position
            else "not placed in the layout"
        )
        tiles.append(f"- **{element['type']}** `{element_id}` {title!r} — {where}")
        if not element["query_id"] and element.get("look_id"):
            raise BundleError(
                f"tile {tile} is backed by Look {element['look_id']}; "
                "saved-Look tiles are not supported"
            )
        query = fetch_query(element["query_id"]) if element["query_id"] else None
        lines = _tile_lines(element, query, tile)
        if element.get("merge_result_id"):
            merge_id = element["merge_result_id"]
            merge = looker.get_json(f"/merge_queries/{merge_id}")
            if "single_value" in (
                element["type"],
                (merge.get("vis_config") or {}).get("type"),
            ):
                raise BundleError(
                    f"tile {tile}: values of a merged single-value tile "
                    "cannot be fetched through the Looker API"
                )
            bundle.write(f"queries/merge-{merge_id}.json", json.dumps(merge, indent=2))
            lines.append(f"merged results {merge_id}:")
            for source in merge["source_queries"]:
                fetch_query(source["query_id"])
                lines.append(
                    f"  - source query {source['query_id']} {source['name']!r}: queries/{source['query_id']}.sql"
                )
        if query is not None and "single_value" in (
            element["type"],
            (query["vis_config"] or {}).get("type"),
        ):
            values = looker.get_json(f"/queries/{query['id']}/run/json")
            errors = [r["looker_error"] for r in values if "looker_error" in r]
            if errors:
                raise BundleError(f"tile {tile}: Looker reported {errors[0]!r}")
            name = f"kpi/{slug(title, fallback='tile')}-{element_id}.csv"
            bundle.write(name, _csv(values, query["fields"]))
            lines.append(f"values: {name}")
        tiles += [f"    - {line}" for line in lines]

    filters = [
        f"- {f['name']} ({f['type']}) default: {f['default_value']}"
        for f in dashboard["dashboard_filters"]
    ]
    bundle.write(
        "outline.md",
        "\n".join(
            [
                f"# {dashboard['title']}",
                "",
                f"Looker: {url}",
                "",
                "## Dashboard filters",
                "",
                *filters,
                "",
                "## Tiles",
                "",
                *tiles,
                "",
            ]
        ),
    )
    bundle.write("png/dashboard.png", looker.render_png(dashboard_id))
    timezones = sorted(
        {q["query_timezone"] for q in queries.values() if q["query_timezone"]}
    )
    bundle.finish(extra={"query_timezones": timezones} if timezones else None)
