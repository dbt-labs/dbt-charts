"""Import a Hex project into a source bundle (Hex public API v1, stdlib only).

Credentials come from ``HEX_API_TOKEN``; the token lives in memory for the run
and is never written anywhere. The API base comes from ``--base-url`` /
``HEX_BASE_URL``, else from the origin of a project URL, else the public
``https://app.hex.tech/api/v1`` (the origin Hex documents).

The bundle is built from the published app's export (``project.hex.yaml``: cells,
chart specs, input parameters, app layout) plus a PNG per chart cell from the
latest completed run; a key that cannot list runs, or a project with none, still
gets the bundle, with each chart's render named under "Not fetched". Hex offers no whole-app screenshot, no run-result data
endpoint (so ``kpi/`` stays empty) and no render for non-chart cells.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from dbt_charts.agent_api.source_bundle import (
    BundleError,
    SourceBundle,
    option_lines,
    public_url,
    require_env,
    staged_bundle,
    url_origin,
)

DEFAULT_API_BASE = "https://app.hex.tech/api/v1"
RAW = "project.hex.yaml"
PAGE_SIZE = 100
RETRY_LIMIT = 5
RETRY_SECONDS = 15.0
TIMEOUT_SECONDS = 120
# Hex sits behind Cloudflare, which rejects the default Python-urllib agent (error 1010).
USER_AGENT = "dct-import-hex"
CHART_SIZE = "width=1200&height=800"
# Config keys the outline lists as their own lines.
CELL_SHOWN_KEYS = frozenset(
    {
        "cells",
        "comparisonColumn",
        "dataFrameVariableName",
        "dataframe",
        "dataframeName",
        "defaultValue",
        "inputType",
        "name",
        "resultVariableName",
        "richText",
        "showComparison",
        "source",
        "title",
        "valueAggregate",
        "valueColumn",
        "valueVariableName",
    }
)
# Config keys whose listed sub-keys are their own lines; the rest are options.
CELL_PARTLY_SHOWN = {
    "chartSpec": frozenset({"type", "layers"}),
    "filters": frozenset({"filters"}),
    "options": frozenset({"valueOptions"}),
    "pivotConfig": frozenset({"values"}),
    "spec": frozenset({"fields"}),
}
# Hex color assignments, which the dbt Charts theme owns; a color-by column
# (`colorDataFrameColumn`) is an encoding and stays in the outline.
CELL_STYLING_KEYS = frozenset(
    {"color", "colorMappings", "colorOrder", "colorsBySeriesValues"}
)
NESTED_STYLING_KEYS = CELL_STYLING_KEYS
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class _HexHTTPError(BundleError):
    def __init__(self, message: str, code: int, detail: str) -> None:
        super().__init__(f"{message}: {detail}" if detail else message)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class HexProject:
    id: str
    title: str
    last_published_at: str | None


def parse_ref(ref: str, *, base_url: str | None) -> tuple[str, str]:
    """Return ``(project_id, api_base)`` for a project URL or bare project id."""
    origin = None
    if ref.startswith(("http://", "https://")):
        parts = urllib.parse.urlsplit(ref)
        origin = url_origin(parts)
        found = _UUID.search(parts.path)
        project_id = found.group() if found else None
    else:
        project_id = ref if _UUID.fullmatch(ref) else None
    if project_id is None:
        raise BundleError(f"{ref!r} is not a Hex project URL or project id (a UUID)")
    if base_url:
        return project_id, base_url.rstrip("/")
    return project_id, f"{origin}/api/v1" if origin else DEFAULT_API_BASE


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(exc.read())["message"])
    except (ValueError, KeyError, TypeError):
        return ""


class _Hex:
    def __init__(self, api_base: str) -> None:
        token = require_env(
            ("HEX_API_TOKEN",),
            where="Hex → Settings → API keys → New API key",
            command="dct import hex <project URL>",
        )["HEX_API_TOKEN"]
        self.api_base = api_base
        self.headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        }

    def request(self, method: str, path: str, body: Any = None) -> bytes:
        headers = dict(self.headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        for attempt in range(RETRY_LIMIT):
            request = urllib.request.Request(
                f"{self.api_base}{path}", data=data, method=method, headers=headers
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=TIMEOUT_SECONDS
                ) as response:  # noqa: S310 — http(s) URL supplied by the user
                    return response.read()
            except urllib.error.HTTPError as exc:
                # Chart images are limited to 20 per minute.
                if exc.code == 429 and attempt < RETRY_LIMIT - 1:
                    time.sleep(RETRY_SECONDS)
                    continue
                raise _HexHTTPError(
                    f"Hex {method} {path} failed: HTTP {exc.code}",
                    exc.code,
                    _error_detail(exc),
                ) from None
            except urllib.error.URLError as exc:
                raise BundleError(f"Hex {method} {path} failed: {exc.reason}") from None
        raise AssertionError("unreachable")

    def get_json(self, path: str) -> Any:
        return json.loads(self.request("GET", path))


def list_hex_projects(base_url: str | None) -> list[HexProject]:
    """Return every project the token can view, newest first."""
    hex_api = _Hex((base_url or DEFAULT_API_BASE).rstrip("/"))
    projects: list[HexProject] = []
    after = None
    while True:
        query = f"limit={PAGE_SIZE}"
        if after:
            query += f"&after={urllib.parse.quote(after, safe='')}"
        page = hex_api.get_json(f"/projects?{query}")
        for item in page["values"]:
            for key in ("id", "title", "lastPublishedAt"):
                if key not in item:
                    raise BundleError(f"Hex project list entry has no {key!r}: {item}")
            projects.append(
                HexProject(
                    item["id"], " ".join(item["title"].split()), item["lastPublishedAt"]
                )
            )
        after = page["pagination"]["after"]
        if len(page["values"]) < PAGE_SIZE or not after:
            return projects


def _walk(cells: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for cell in cells:
        yield cell
        if cell["cellType"] == "COLLAPSIBLE":
            yield from _walk(cell["config"]["cells"])


def _name(cell: dict[str, Any]) -> str:
    title = cell["cellLabel"] or (
        cell["config"]["title"] if cell["cellType"] == "METRIC" else None
    )
    if cell["cellType"] == "METRIC" and not title:
        raise BundleError(f"Hex metric cell {cell['cellId']} has no label or title")
    return repr(title) if title else "(untitled)"


SUPPORTED_CELL_TYPES = frozenset(
    {
        "SQL",
        "CHARTV2",
        "EXPLORE",
        "METRIC",
        "PIVOT",
        "TABLE_DISPLAY",
        "FILTER",
        "CODE",
        "MARKDOWN",
        "TEXT",
        "COLLAPSIBLE",
        "INPUT",
    }
)
_SERIES_SHOWN = ("type", "dataFrameColumns", "axis")


def _options(options: dict[str, Any]) -> list[str]:
    return option_lines(
        options,
        dropped=CELL_STYLING_KEYS.__contains__,
        nested_dropped=NESTED_STYLING_KEYS,
    )


def _cell_options(cell: dict[str, Any]) -> list[str]:
    config = cell["config"]
    options = {k: v for k, v in config.items() if k not in CELL_SHOWN_KEYS}
    for key, shown in CELL_PARTLY_SHOWN.items():
        if config.get(key):
            options[key] = {k: v for k, v in config[key].items() if k not in shown}
    return _options(options)


def _chart_lines(cell: dict[str, Any]) -> list[str]:
    spec = cell["config"]["chartSpec"]
    if spec["type"] != "layered":
        raise BundleError(
            f"chart cell {cell['cellId']} has unsupported chart spec type {spec['type']!r}"
        )
    lines = []
    for layer in spec["layers"]:
        x_axis = layer["xAxis"]
        for series in layer["series"]:
            axis = series["axis"]
            aggregate = axis.get("aggregate")
            columns = ", ".join(series["dataFrameColumns"])
            value = f"{aggregate}({columns})" if aggregate else columns
            lines.append(
                f"layer: {series['type']}, x={x_axis['dataFrameColumn']}, y={value}"
            )
            lines += _options(
                {
                    **{k: v for k, v in series.items() if k not in _SERIES_SHOWN},
                    **{k: v for k, v in axis.items() if k != "aggregate"},
                }
            )
        lines += _options(
            {
                **{k: v for k, v in x_axis.items() if k != "dataFrameColumn"},
                **{k: v for k, v in layer.items() if k not in ("xAxis", "series")},
            }
        )
    return lines


def _explore_lines(cell: dict[str, Any]) -> list[str]:
    config = cell["config"]
    fields = []
    for field in config["spec"]["fields"]:
        value = field["value"]
        if "aggregation" in field:
            value = f"{field['aggregation']}({value})"
        if "truncUnit" in field:
            value = f"{value} by {field['truncUnit']}"
        fields.append(f"{field['channel']}={value}")
    return [f"dataframe: {config['dataframe']}", "fields: " + ", ".join(fields)]


def _input_line(cell: dict[str, Any]) -> str:
    config = cell["config"]
    line = (
        f"- {config['name']} ({config['inputType']}) default: {config['defaultValue']}"
    )
    options = (config["options"] or {}).get("valueOptions")
    return f"{line}; options: {', '.join(map(str, options))}" if options else line


def _cell_lines(cell: dict[str, Any]) -> list[str]:
    kind, config = cell["cellType"], cell["config"]
    if kind == "SQL":
        return [
            f"queries/{cell['cellId']}.sql -> dataframe {config['resultVariableName']}"
        ]
    if kind == "CHARTV2":
        return _chart_lines(cell)
    if kind == "EXPLORE":
        return _explore_lines(cell)
    if kind == "METRIC":
        comparison = (
            f"; comparison {config['comparisonColumn']}"
            if config["showComparison"]
            else ""
        )
        return [
            f"value: {config['valueAggregate']}({config['valueColumn']}) "
            f"of {config['valueVariableName']}{comparison}"
        ]
    if kind == "PIVOT":
        values = ", ".join(
            f"{v['aggregation']}({v['field']})" for v in config["pivotConfig"]["values"]
        )
        return [f"dataframe: {config['dataframeName']}", f"values: {values}"]
    if kind == "TABLE_DISPLAY":
        return [f"dataframe: {config['dataFrameVariableName']}"]
    if kind == "FILTER":
        columns = ", ".join(f["column"] for f in config["filters"]["filters"])
        return [f"dataframe: {config['dataframeName']}", f"filters: {columns}"]
    if kind == "CODE":
        first = config["source"].strip().splitlines()[:1]
        return [f"Python is not migrated; starts: {first[0] if first else '(empty)'}"]
    if kind == "MARKDOWN":
        return (
            [config["source"].strip().splitlines()[0]]
            if config["source"].strip()
            else []
        )
    if kind in ("TEXT", "COLLAPSIBLE", "INPUT"):
        return []
    raise AssertionError(f"cell type {kind!r} is not in SUPPORTED_CELL_TYPES")


def _describe(cell: dict[str, Any], render: str | None) -> list[str]:
    kind = cell["cellType"]
    if kind not in SUPPORTED_CELL_TYPES:
        return [
            f"- **unmigrated** {cell['cellId']} {_name(cell)} \u2014 {kind}: "
            "not representable"
        ]
    head = f"- **{kind}** `{cell['cellId']}` {_name(cell)}"
    if kind == "CODE":
        head += ": UNMIGRATED (Python cell)"
    lines = [
        head,
        *(
            f"    - {line}"
            for line in [
                f"config: {RAW}#cells[{cell['cellId']}]",
                *_cell_lines(cell),
                *_cell_options(cell),
            ]
        ),
    ]
    if render:
        lines.append(f"    - {render}")
    return lines


def import_hex(
    ref: str, *, out: Path | None, base_url: str | None, force: bool
) -> Path:
    """Write a source bundle for one Hex project's published app into ``out``.

    ``out`` defaults to ``./hex-<project id>``. The bundle is built beside
    ``out`` and moved into place once complete; a non-empty ``out`` is refused
    unless ``force``.

    A chart render Hex cannot provide (the key cannot list the project's runs,
    HTTP 403; the project has no completed run; or one cell's image request
    fails) is named in outline.md under "Not fetched" and its ``png/`` file is
    omitted; the rest of the bundle is written. An app tab that places a cell
    the export lacks lists it as ``missing`` in the outline, and a cell type the
    importer cannot represent is listed as ``unmigrated``.

    Raises:
        BundleError: on a missing token, any other failed request, or an
            app that places no cell the export contains and the importer can
            represent.
    """
    project_id, api_base = parse_ref(ref, base_url=base_url)
    out = out or Path(f"hex-{project_id}")
    hex_api = _Hex(api_base)
    exported = json.loads(
        hex_api.request(
            "POST",
            "/projects/export",
            body={"projectId": project_id, "version": "latest"},
        )
    )
    document = yaml.safe_load(exported["content"])
    run = _latest_run(hex_api, project_id)
    if ref.startswith("http"):
        url = public_url(ref)
    elif isinstance(run, dict):
        url = public_url(run["runUrl"])
    else:
        url = url_origin(urllib.parse.urlsplit(api_base))
    with staged_bundle(out, tool="hex", url=url, raw=RAW, force=force) as bundle:
        bundle.write(RAW, exported["content"])
        _fill_bundle(hex_api, bundle, document, project_id, run, url)
    return out


def _latest_run(hex_api: _Hex, project_id: str) -> dict[str, Any] | str:
    """The latest completed run, or why there is none: a key that cannot list
    runs (HTTP 403) or a project that has never completed one."""
    try:
        runs = hex_api.get_json(
            f"/projects/{project_id}/runs?limit=1&statusFilter=COMPLETED"
        )
    except _HexHTTPError as exc:
        if exc.code != 403:
            raise
        return str(exc)
    if not runs["runs"]:
        return f"Hex project {project_id} has no completed run"
    return runs["runs"][0]


def _fill_bundle(
    hex_api: _Hex,
    bundle: SourceBundle,
    document: dict[str, Any],
    project_id: str,
    run: dict[str, Any] | str,
    url: str,
) -> None:
    workspace = urllib.parse.urlsplit(url).path.strip("/").split("/")[0]
    cells = {c["cellId"]: c for c in _walk(document["cells"])}
    placed: list[tuple[str, list[tuple[str, str]]]] = []
    seen: set[str] = set()
    first_missing: str | None = None
    for tab in document["appLayout"]["tabs"]:
        lines: list[tuple[str, str]] = []
        for row in tab["rows"]:
            for column in row["columns"]:
                where = f"cols {column['start']}-{column['end']}"
                for element in column["elements"]:
                    if element["type"] != "CELL":
                        raise BundleError(
                            f"Hex app element of type {element['type']!r} in tab "
                            f"{tab['name']!r} is not supported"
                        )
                    cell_id = element["cellId"]
                    if cell_id in cells:
                        seen.add(cell_id)
                    elif first_missing is None:
                        first_missing = (
                            f"Hex app tab {tab['name']!r} places cell {cell_id}, "
                            "which is not in the project"
                        )
                    lines.append((where, cell_id))
        placed.append((tab["name"], lines))
    if first_missing and not seen:
        raise BundleError(first_missing)
    unsupported = [
        f"{c['cellId']} {_name(c)} ({c['cellType']})"
        for c in cells.values()
        if c["cellType"] not in SUPPORTED_CELL_TYPES
    ]
    if unsupported and not any(
        cells[c]["cellType"] in SUPPORTED_CELL_TYPES for c in seen
    ):
        raise BundleError(
            "Hex app places no cell the importer can represent; unsupported: "
            + ", ".join(unsupported)
        )

    renders: dict[str, str] = {}
    not_fetched: dict[str, str] = {}
    for cell_id in seen:
        cell = cells[cell_id]
        if cell["cellType"] not in ("CHARTV2", "EXPLORE"):
            continue
        image = None
        failure = run if isinstance(run, str) else None
        if isinstance(run, dict):
            path = f"/projects/{project_id}/runs/{run['runId']}/cells/{cell_id}/image?{CHART_SIZE}"
            try:
                image = hex_api.get_json(path)
            except _HexHTTPError as exc:
                # Explore cells shown as tables have no chart to export.
                if (
                    cell["cellType"] == "EXPLORE"
                    and exc.code == 400
                    and "not a chart visualization" in exc.detail
                ):
                    renders[cell_id] = "no render: Hex exports only chart cells"
                    continue
                failure = str(exc)
        if image is None:
            renders[cell_id] = f"render: none, Hex could not provide it: {failure}"
            not_fetched[f"png/{cell_id}.png"] = str(failure)
            continue
        bundle.write(f"png/{cell_id}.png", base64.b64decode(image["imageBase64"]))
        renders[cell_id] = f"render: png/{cell_id}.png"

    for cell in cells.values():
        if cell["cellType"] == "SQL":
            bundle.write(f"queries/{cell['cellId']}.sql", cell["config"]["source"])

    controls = [_input_line(c) for c in cells.values() if c["cellType"] == "INPUT"]
    body: list[str] = []
    for tab_name, tab_cells in placed:
        body += [f"### Tab: {tab_name}", ""]
        for where, cell_id in tab_cells:
            if cell_id in cells:
                described = _describe(cells[cell_id], renders.get(cell_id))
            else:
                described = [
                    f"- **missing** {cell_id} \u2014 not in the project's export"
                ]
            body += [f"{where}:", *described]
        body.append("")
    unplaced = [c for c in cells.values() if c["cellId"] not in seen]
    if unplaced:
        body += ["### Cells not in the app", ""]
        for cell in unplaced:
            body += _describe(cell, None)
        body.append("")
    bundle.write(
        "outline.md",
        "\n".join(
            [
                f"# {document['meta']['title']}",
                "",
                f"Hex: {url}{f' (workspace {workspace})' if workspace else ''}",
                "",
                "## Input parameters",
                "",
                *controls,
                "",
                "## App",
                "",
                *body,
                *(
                    [
                        "## Not fetched: Hex failed to produce these",
                        "",
                        *(f"- `{path}`: {why}" for path, why in not_fetched.items()),
                        "",
                    ]
                    if not_fetched
                    else []
                ),
            ]
        ),
    )
    extra: dict[str, str | list[str]] = {"project_id": project_id}
    if workspace:
        extra["workspace"] = workspace
    if isinstance(run, dict):
        extra["project_version"] = run["projectVersion"]
        extra["run_id"] = run["runId"]
    bundle.finish(extra=extra, missing_images=list(not_fetched))
