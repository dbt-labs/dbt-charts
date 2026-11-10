"""Import an Omni dashboard into a source bundle (Omni REST API, stdlib only).

The raw definition is the beta dashboard export
(``/api/unstable/documents/<id>/export``): it is the only endpoint that carries
the tile layout, pages and dashboard filters, so a breaking change there fails
the import loudly. Generated SQL comes from planning each saved query
(``planOnly``); saved queries carry their own filters, so SQL does not reflect
dashboard filter defaults (those are listed in the outline).
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dbt_charts.agent_api.source_bundle import (
    BundleError,
    SourceBundle,
    compact,
    option_lines,
    public_url,
    require_env,
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
_STEP_MARKER = re.compile(r"^-- step (\d+), run on (\w+)\r?$", re.MULTILINE)
_USE_RESULT = re.compile(r"-- use result of step 1 as (\w+)\r?\n")
_POST_CLAUSES = re.compile(
    r"^(SELECT|FROM|PIVOT|UNPIVOT|WHERE|GROUP BY|HAVING|QUALIFY|ORDER BY|LIMIT)\b",
    re.MULTILINE,
)
# Purely decorative children: listed in the outline, no query behind them.
DIVIDER_TYPES = ("inline-divider",)
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


class _Unplannable(BundleError):
    """Omni refused to plan one saved query; the bundle can name it and go on."""


class _HttpFailure(BundleError):
    """Omni answered a request with an HTTP error status."""

    def __init__(self, method: str, path: str, code: int, detail: str) -> None:
        self.status = f"HTTP {code} {detail}".strip()
        super().__init__(f"Omni {method} {path} failed: {self.status}")
        self.code = code
        self.detail = detail


class _RenderUnavailable(BundleError):
    """Omni's page render did not finish or is already running; the bundle can
    name the image and go on."""


class _Omni:
    def __init__(self, origin: str) -> None:
        key = require_env(
            ("OMNI_API_KEY",),
            where="Omni → Settings → API Keys (an Omni admin creates them)",
            command="dct import omni <dashboard URL>",
        )["OMNI_API_KEY"]
        self.origin = origin
        self.headers = {
            "Authorization": f"Bearer {key}",
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
                    detail = exc.read().decode(errors="replace")[:300]
                    raise _HttpFailure(method, path, exc.code, detail) from None
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
        for job in jobs:
            if job.get("status") == "FAILED" and (
                job.get("error_type") or ""
            ).startswith("PLAN"):
                raise _Unplannable(job["error_message"])
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
        try:
            started = self.request("POST", path, body)
        except _HttpFailure as exc:
            if exc.code != 409:
                raise
            busy = json.loads(exc.detail)["existing_job_id"]
            raise _RenderUnavailable(
                f"Omni render already in progress (job {busy})"
            ) from None
        job = json.loads(started)["job_id"]
        for _ in range(POLL_LIMIT):
            status = self.get_json(f"{path}/{job}/status")
            if status["status"] == "complete":
                return self.request("GET", f"{path}/{job}")
            if status["status"] == "error":
                raise BundleError(f"Omni download {job} failed: {status.get('error')}")
            time.sleep(POLL_SECONDS)
        raise _RenderUnavailable(f"Omni render {job} did not finish in time")


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
    """Yield ``(tile_id, where)``.

    Text tiles yield ``("text:<content>", where)``, dividers
    ``("divider:<id>", where)`` and tiles of an unknown type
    ``("unsupported:<json [type, id, title]>", where)``.
    """
    for child in node.get("children", []):
        where = " > ".join(path)
        if child.get("type") == "query":
            if child.get("as") != "metadata":
                yield child["id"], where
        elif child.get("type") == "inline-text":
            yield f"text:{child['content']}", where
        elif "children" in child:
            yield from _walk(child, [*path, child.get("name") or "(unnamed)"])
        elif child.get("type") in DIVIDER_TYPES:
            yield f"divider:{child.get('id') or child.get('instanceKey') or ''}", where
        elif child.get("type") not in NON_TILE_TYPES:
            ident = child.get("id") or child.get("instanceKey")
            yield (
                f"unsupported:{json.dumps([child.get('type'), ident, child.get('title')])}",
                where,
            )


def _split_steps(tile_id: str, sql: str) -> tuple[str, str | None, str | None]:
    """Split Omni's two-step SQL into ``(warehouse step, DuckDB post-step, the name
    the post-step reads step 1's result as)``.

    Omni plans a tile that needs a client-side calculation as a warehouse
    query followed by a DuckDB query over its result, in one text, each
    introduced by a ``-- step N, run on X`` marker. SQL with no marker is one
    step and comes back unchanged.
    """
    markers = list(_STEP_MARKER.finditer(sql))
    if not markers:
        return sql, None, None
    if not (
        len(markers) == 2
        and markers[0].start() == 0
        and (markers[0][1], markers[1][1], markers[1][2]) == ("1", "2", "DuckDB")
        and (use := _USE_RESULT.match(sql, markers[1].end() + 1))
    ):
        raise BundleError(
            f"Omni tile `{tile_id}`: SQL has step markers "
            f"{[m[0] for m in markers]} but is not a two-step warehouse query "
            "followed by a DuckDB query over its result; cannot split it"
        )
    return (
        sql[markers[0].end() : markers[1].start()].strip() + "\n",
        sql[use.end() :].strip() + "\n",
        use[1],
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
        BundleError: on a missing key, any failed request, a failed render, a
            dashboard with no tile the bundle can represent, or an incomplete
            bundle. A divider is listed in ``outline.md``; a tile of a type the
            bundle cannot represent, or whose saved query is missing, is named
            there as left out, a tile whose query Omni cannot plan (any
            ``PLAN*`` error type) is named without SQL, and a KPI whose value
            run Omni answers with an HTTP error is named without values, and a page
            image whose render does not finish, or whose dashboard already has a
            render running (HTTP 409), is listed under ``Not fetched``
            (the png requirement is waived); the import continues.
            A tile Omni plans as a warehouse query plus a DuckDB query over its
            result gets ``queries/<id>.sql`` (the warehouse step, runnable as
            is) and ``queries/<id>.post.sql`` (the DuckDB step), named by a
            ``post-step`` line in the outline; a marker shape that is not that
            two-step form fails naming the tile.
            Each tile's SQL and KPI values are computed at the dashboard's filter
            defaults (``dashboard filter applied`` lines in the outline), as the
            dashboard shows them, not at the filters saved on the query.
            Tiles in a top-level container outside any page are listed under a
            ``Dashboard`` section.
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
        pages = []
        loose: list[tuple[str, str]] = []
        for p in metadata["containers"]:
            if p["containerType"] == "page":
                pages.append(
                    (p["name"], p["instanceKey"], list(_walk(p["container"], [])))
                )
            else:
                loose += _walk(p, [p["name"]] if p.get("name") else [])
        if loose:
            pages.append(("Dashboard", None, loose))
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
    left_out: list[str] = []
    represented = 0
    for name, _, placements in pages:
        if name:
            tiles += ["", f"## Page: {name}", ""]
        for tile_id, where in placements:
            if tile_id.startswith("divider:"):
                ident = tile_id.removeprefix("divider:")
                tiles.append(
                    f"- **divider**{f' `{ident}`' if ident else ''} — "
                    f"{where or 'page layout'}"
                )
                continue
            if tile_id.startswith("unsupported:"):
                kind, ident, title = json.loads(tile_id.removeprefix("unsupported:"))
                left_out.append(str(kind))
                tiles += [
                    f"- **unsupported**{f' `{ident}`' if ident else ''}"
                    f"{f' {title!r}' if title else ''} — {where or 'page layout'}",
                    f"    - unsupported tile type {kind!r}: left out",
                ]
                continue
            if tile_id.startswith("text:"):
                represented += 1
                tiles.append(f"- **text** {_title(tile_id[5:])!r} — {where}")
                continue
            if tile_id not in saved:
                left_out.append(f"{tile_id} (no saved query)")
                tiles += [
                    f"- **missing** `{tile_id}` — {where or 'page layout'}",
                    "    - sql: none, the document has no saved query for this tile",
                ]
                continue
            represented += 1
            if tile_id in fetched:
                continue
            fetched.add(tile_id)
            query, overrides = _at_dashboard_filters(
                saved[tile_id], collection, metadata["tileFilterMap"].get(tile_id, {})
            )
            presentation = presentations[query["id"]]
            chart = presentation["visConfig"]["chartType"]
            tiles.append(f"- **{chart}** `{tile_id}` {query['name']!r} — {where}")
            tiles += [f"    - dashboard filter applied: {o}" for o in overrides]
            bundle.write(f"queries/{tile_id}.json", json.dumps(query, indent=2))
            try:
                sql, timezone = omni.plan(query["query"])
            except _Unplannable as exc:
                tiles.append(f"    - sql: none, Omni could not plan it: {exc}")
                continue
            timezones.add(timezone)
            sql, post_sql, step_name = _split_steps(tile_id, sql)
            bundle.write(f"queries/{tile_id}.sql", sql)
            tiles += [
                f"    - {line}"
                for line in _tile_lines(tile_id, query, presentation, metadata)
            ]
            if post_sql is not None:
                bundle.write(f"queries/{tile_id}.post.sql", post_sql)
                clauses = dict.fromkeys(_POST_CLAUSES.findall(post_sql))
                tiles.append(
                    f"    - post-step: queries/{tile_id}.post.sql "
                    f"(DuckDB over step 1's result as `{step_name}`: "
                    f"{', '.join(clauses)})"
                )
            if chart == "kpi":
                kpi_file = f"kpi/{_slug(query['name'])}-{tile_id}.csv"
                try:
                    values = omni.request(
                        "POST",
                        "/v1/query/run",
                        {"query": query["query"], "resultType": "csv"},
                    )
                except _HttpFailure as exc:
                    tiles.append(
                        f"    - values: none, Omni could not run it: {exc.status}"
                    )
                    continue
                bundle.write(
                    kpi_file, _displayed_row(values, query, presentation, tile_id)
                )
                tiles.append(f"    - values: {kpi_file}")
    if left_out and not represented:
        raise BundleError(
            "dashboard has no tile the bundle can represent; left out: "
            + ", ".join(sorted(set(left_out)))
        )
    tiles += [
        f"- **hidden** `{key}` {saved[key]['name']!r} — not on the dashboard, not fetched"
        for key in metadata["hiddenTiles"]
        if key not in fetched
    ]

    filters = [
        f"- {spec['label']} `{key}`"
        f"{' ' + spec['fieldName'] if 'fieldName' in spec else ''}: {_describe(spec)}"
        for key in collection["filterOrder"]
        for spec in [collection["filterConfig"][key]]
    ]
    not_fetched: list[tuple[str, str]] = []
    for name, instance_key, _ in pages:
        png = f"png/{_slug(name)}.png" if name else "png/dashboard.png"
        try:
            bundle.write(png, omni.render_png(dashboard_id, instance_key))
        except _RenderUnavailable as exc:
            not_fetched.append((png, str(exc)))
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
                *(
                    [
                        "## Not fetched: Omni failed to export these",
                        "",
                        *(f"- `{png}`: {error}" for png, error in not_fetched),
                        "",
                    ]
                    if not_fetched
                    else []
                ),
            ]
        ),
    )

    bundle.finish(
        missing_images=[png for png, _ in not_fetched],
        extra={
            "query_timezones": sorted(timezones),
            "kpi_values": "display_formatted",
        },
    )


DASHBOARD_FILTER_KEYS = (
    "kind",
    "type",
    "values",
    "is_negative",
    "appliedLabels",
    "ui_type",
    "isFiscal",
    "left_side",
    "right_side",
    "offset_interval_string",
    "is_inclusive",
)


def _at_dashboard_filters(
    saved: dict[str, Any],
    collection: dict[str, Any],
    listens: dict[str, str | Literal[False]],
) -> tuple[dict[str, Any], list[str]]:
    """The saved query with the dashboard's filter defaults applied, and a line per
    filter that moved.

    The dashboard shows a tile at its filters' defaults, not at the filters saved
    on the query. A dashboard filter with no default (no values, no time range)
    leaves the tile's own filter alone.
    """
    filters = dict(saved["query"]["filters"])
    moved = []
    for filter_id in collection["filterOrder"]:
        field = listens.get(filter_id)
        spec = collection["filterConfig"][filter_id]
        if not field or not (
            spec.get("values") or spec.get("left_side") or spec.get("right_side")
        ):
            continue
        key = field if field in filters else re.sub(r"\[[^\]]*\]$", "", field)
        applied = {k: spec[k] for k in DASHBOARD_FILTER_KEYS if k in spec}
        if filters.get(key) != applied:
            moved.append(f"{spec['label']} = {_describe(applied)}")
        filters[key] = applied
    return {**saved, "query": {**saved["query"], "filters": filters}}, moved


def _displayed_row(
    values: bytes, query: dict[str, Any], presentation: dict[str, Any], tile_id: str
) -> bytes:
    """A KPI csv cut to its header and the one row the tile displays, headline first.

    The headline is the field of the first ``number`` entry of the tile's
    ``markdownConfig`` and ``field.row`` names the row shown: ``_first``,
    ``_second``, ``_last`` or ``_totals.N`` (the last row Omni appends when the
    query asks for column totals). A tile with no ``number`` entry shows its first
    row. A pivoted query's csv has two header rows and no column per field, so it
    is kept as Omni returned it, as is a headline that is not a query field (a
    manual field).
    """
    spec = query["query"]
    if spec["pivots"]:
        return values
    entries = presentation["visConfig"].get("spec", {}).get("markdownConfig", [])
    shown = next((e["config"]["field"] for e in entries if e["type"] == "number"), None)
    header, *data = csv.reader(io.StringIO(values.decode()))
    total = None
    if "::total::" in (spec.get("column_totals") or {}) and data:
        total = data.pop()
    row_spec = "_first" if shown is None else shown["row"]
    if row_spec == "_first":
        picked = data[:1]
    elif row_spec == "_second":
        picked = data[1:2]
    elif row_spec == "_last":
        picked = data[-1:]
    elif re.fullmatch(r"_totals\.\d+", row_spec):
        if data and total is None:
            raise BundleError(
                f"KPI {query['name']!r} (tile {tile_id}): row {row_spec!r} but the "
                "query has no column totals"
            )
        picked = [] if total is None else [total]
    else:
        raise BundleError(
            f"KPI {query['name']!r} (tile {tile_id}): unknown row spec {row_spec!r}"
        )
    columns = list(range(len(header)))
    if shown is not None and shown["field"]["name"] in spec["fields"]:
        lead = spec["fields"].index(shown["field"]["name"])
        columns = [lead, *columns[:lead], *columns[lead + 1 :]]
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(
        [row[i] for i in columns] for row in [header, *picked]
    )
    return out.getvalue().encode()


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
        f"table calc: {c['calc_name']}"
        + (f" {c['label']!r}" if "label" in c else "")
        + _calc_rest(c)
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
