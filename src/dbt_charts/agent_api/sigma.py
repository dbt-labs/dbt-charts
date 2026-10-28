"""Import a Sigma workbook into a source bundle (Sigma REST API v2, stdlib only).

Credentials come from ``SIGMA_CLIENT_ID`` / ``SIGMA_CLIENT_SECRET``. The API base
differs by Sigma cloud region and cannot be derived from a workbook URL, so it
comes from ``--base-url`` / ``SIGMA_API_BASE``. The access token lives in memory
for the run and is never written anywhere.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dbt_charts.agent_api.source_bundle import (
    BundleError,
    SourceBundle,
    compact,
    option_lines,
    public_url,
    slug,
    staged_bundle,
)

RAW = "contents.json"
POLL_SECONDS = 3.0
POLL_LIMIT = 100
TIMEOUT_SECONDS = 120
PENDING_STATUSES = (404, 409)
SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
NBSP = " "


class _HttpError(BundleError):
    def __init__(self, method: str, path: str, code: int) -> None:
        super().__init__(f"Sigma {method} {path} failed: HTTP {code}")
        self.code = code


def _send(
    api_base: str, method: str, path: str, headers: dict[str, str], body: bytes | None
) -> bytes:
    request = urllib.request.Request(
        f"{api_base}/v2/{path}", data=body, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 — http(s) URL supplied by the user
            return response.read()
    except urllib.error.HTTPError as exc:
        raise _HttpError(method, path, exc.code) from None
    except urllib.error.URLError as exc:
        raise BundleError(f"Sigma {method} {path} failed: {exc.reason}") from None


class _Sigma:
    def __init__(self, base_url: str | None) -> None:
        if not base_url:
            raise BundleError(
                "the Sigma API base depends on your cloud region: pass --base-url "
                "or set SIGMA_API_BASE (e.g. https://aws-api.sigmacomputing.com)"
            )
        for key in ("SIGMA_CLIENT_ID", "SIGMA_CLIENT_SECRET"):
            if not os.environ.get(key):
                raise BundleError(f"set {key} to import from Sigma")
        self.api_base = base_url.rstrip("/")
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": os.environ["SIGMA_CLIENT_ID"],
                "client_secret": os.environ["SIGMA_CLIENT_SECRET"],
            }
        ).encode()
        token = json.loads(_send(self.api_base, "POST", "auth/token", {}, body))
        self.headers = {
            "Authorization": f"Bearer {token['access_token']}",
            "Content-Type": "application/json",
        }

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> bytes:
        data = json.dumps(body).encode() if body is not None else None
        return _send(self.api_base, method, path, self.headers, data)

    def paged(self, path: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        page: str | None = None
        while True:
            separator = "&" if "?" in path else "?"
            cursor = f"&page={urllib.parse.quote(page)}" if page else ""
            reply = json.loads(
                self.request("GET", f"{path}{separator}limit=1000{cursor}")
            )
            entries += reply["entries"]
            page = reply.get("nextPage")
            if not page:
                return entries

    def export_all(
        self, workbook_id: str, jobs: dict[str, dict[str, Any]]
    ) -> dict[str, bytes]:
        """Submit every export, then poll them together; Sigma runs them in parallel.

        ``jobs`` maps a bundle path to the export request body.
        """
        pending: dict[str, str] = {}
        for relpath, body in jobs.items():
            reply = self.request("POST", f"workbooks/{workbook_id}/export", body)
            pending[json.loads(reply)["queryId"]] = relpath
        results: dict[str, bytes] = {}
        for _ in range(POLL_LIMIT):
            if not pending:
                return results
            time.sleep(POLL_SECONDS)
            for query_id, relpath in list(pending.items()):
                try:
                    data = self.request("GET", f"query/{query_id}/download")
                except _HttpError as exc:
                    if exc.code in PENDING_STATUSES:
                        continue
                    raise BundleError(f"Sigma export {relpath} failed: {exc}") from None
                # The download answers 200 with an empty body until the job finishes.
                if data:
                    results[relpath] = data
                    del pending[query_id]
        if pending:
            raise BundleError(
                f"Sigma export {', '.join(pending.values())} did not finish in time"
            )
        return results


def parse_ref(ref: str) -> str:
    """Return the workbook id or url id named by a workbook URL or bare id."""
    if not ref.startswith(("http://", "https://")):
        return ref
    segments = urllib.parse.urlsplit(ref).path.strip("/").split("/")
    if "workbook" not in segments or segments[-1] == "workbook":
        raise BundleError(f"{ref!r} is not a Sigma workbook URL (/workbook/<id>)")
    return segments[segments.index("workbook") + 1]


def _title(element: dict[str, Any]) -> str:
    name = element.get("name")
    if isinstance(name, dict):
        name = name.get("text")
    return (name or "").replace(NBSP, " ").strip()


def _safe_id(element_id: str) -> str:
    if not SAFE_ID.fullmatch(element_id):
        raise BundleError(
            f"unsupported element id {element_id!r} (letters, digits, - _)"
        )
    return element_id


def _layout_positions(layout_xml: str) -> dict[str, list[tuple[str, str, str]]]:
    """Map page id -> [(element id, gridColumn, gridRow)] in reading order."""
    body = re.sub(r"<\?xml[^>]*\?>", "", layout_xml)
    root = ET.fromstring(f"<root>{body}</root>")
    pages: dict[str, list[tuple[str, str, str]]] = {}
    for page in root.findall("Page"):
        items = [
            (e.get("elementId", ""), e.get("gridColumn", ""), e.get("gridRow", ""))
            for e in page.iter("Element")
        ]
        items.sort(
            key=lambda i: (int(i[2].split("/")[0] or 0), int(i[1].split("/")[0] or 0))
        )
        pages[page.get("id", "")] = items
    return pages


# Filter-spec keys that are identity or resolved into the line's column name.
_NOT_SETTINGS = frozenset({"id", "columnId", "source", "state"})
# Element keys shown in the tile heading or as their own lines, not as options.
NON_OPTION_KEYS = frozenset({"id", "kind", "name", "controlId", "controlType", "body"})
# Element keys the dbt Charts theme owns: layout polish, fonts, fills, colors.
STYLING_KEYS = frozenset(
    {
        "align",
        "appearance",
        "borderRadius",
        "colorAssignment",
        "fillColor",
        "fontColor",
        "fontWeight",
        "labelConfig",
        "layout",
        "size",
        "spacing",
        "style",
        "verticalAlign",
    }
)
# Hex colors, theme refs, palettes and widths inside values that carry meaning
# (axis scales, conditional-format rules, column formats).
NESTED_STYLING_KEYS = frozenset(
    {
        "align",
        "backgroundColor",
        "color",
        "customColors",
        "fontStyle",
        "fontWeight",
        "headerWidths",
        "scheme",
        "textWrap",
        "width",
    }
)
# Handled by dedicated lines in `_describe`.
_OWN_LINES = frozenset(
    {"source", "xAxis", "yAxis", "color", "value", "filters", "description", "columns"}
)


def _settings(spec: dict[str, Any]) -> str:
    """Every filter setting that changes results: kind, mode, values, window, preset."""
    return " ".join(
        f"{k}={json.dumps(v)}" for k, v in spec.items() if k not in _NOT_SETTINGS
    )


def _rest(spec: dict[str, Any], shown: frozenset[str]) -> str:
    """Compact JSON of a spec's other options, colors and widths removed."""
    lines = option_lines(
        {k: v for k, v in spec.items() if k not in shown},
        dropped=NESTED_STYLING_KEYS.__contains__,
        nested_dropped=NESTED_STYLING_KEYS,
    )
    return " ".join(lines)


def _describe(
    el: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    controls: dict[str, list[str]],
    sql_ids: set[str],
) -> list[str]:
    cols = {c["id"]: c for c in el.get("columns", [])}

    def col(cid: str | dict[str, Any]) -> str:
        if isinstance(cid, dict):
            cid = cid.get("columnId", "")
        c = cols.get(cid, {})
        label = c.get("name")
        formula = c.get("formula", cid)
        return f"{label} = {formula}" if label else formula

    def with_rest(head: str, spec: dict[str, Any], shown: set[str]) -> str:
        rest = _rest(spec, frozenset(shown))
        return f"{head} [{rest}]" if rest else head

    lines = [f"config: {RAW}#elements[{el['id']}]"]
    if el["id"] in sql_ids:
        lines.append(f"sql: queries/{el['id']}.sql")
    src = el.get("source", {})
    if el["kind"] == "control":
        vsrc = src
        if vsrc.get("kind") == "source":
            owner = by_id.get(vsrc["source"].get("elementId"), {})
            owner_cols = {c["id"]: c for c in owner.get("columns", [])}
            formula = owner_cols.get(vsrc.get("columnId"), {}).get(
                "formula", vsrc.get("columnId")
            )
            lines.append(
                with_rest(
                    f"source: values from {_title(owner)}: {formula}",
                    vsrc,
                    {"kind", "source", "columnId"},
                )
            )
        elif vsrc:
            lines.append(f"source: {compact(vsrc)}")
    elif src.get("elementId"):
        source_title = _title(by_id.get(src["elementId"], {})) or src["elementId"]
        lines.append(
            with_rest(
                f"source: {source_title} ({src['elementId']})",
                src,
                {"kind", "elementId"},
            )
        )
    elif src.get("kind") == "join":
        lines.append(f"source: join {json.dumps(src.get('joins'))}")
    elif src:
        lines.append(f"source: {compact(src)}")
    for axis in ("xAxis", "yAxis"):
        spec = el.get(axis)
        if spec:
            ids = spec.get("columnIds") or [spec.get("columnId")]
            head = f"{axis}: " + "; ".join(col(i) for i in ids if i)
            lines.append(with_rest(head, spec, {"columnId", "columnIds"}))
    if el.get("color"):
        column = col(el["color"]["column"]) if el["color"].get("column") else ""
        lines.append(with_rest(f"color: {column}", el["color"], {"column"}))
    if el["kind"] != "control" and el.get("value"):
        lines.append(with_rest(f"value: {col(el['value'])}", el["value"], {"columnId"}))
    for f in el.get("filters", []) if el["kind"] != "control" else []:
        state = " (disabled)" if f.get("state") == "disabled" else ""
        lines.append(f"filter{state}: {col(f.get('columnId', ''))} {_settings(f)}")
    desc = el.get("description")
    if isinstance(desc, dict) and desc.get("text"):
        lines.append(f"description: {desc['text'].strip()}")
    elif isinstance(desc, str) and desc.strip():
        lines.append(f"description: {desc.strip()}")
    if el.get("columns"):
        lines.append("columns:")
    for c in el.get("columns", []):
        label = f"{c['name']} = " if c.get("name") else ""
        head = f"  - {c['id']}: {label}{c['formula']}"
        lines.append(with_rest(head, c, {"id", "name", "formula"}))
    if el["kind"] == "control":
        targets = sorted(
            {
                _title(by_id.get(f["source"].get("elementId"), {}))
                for f in el.get("filters", [])
            }
        )
        lines.append(f"control {el.get('controlId')}: {el.get('controlType')}")
        lines.append(f"filters: targets={targets}")
    if el["kind"] == "text":
        lines.append("text: " + re.sub(r"\s+", " ", el.get("body", "")))
    # A control's `value` is a setting, not a column reference.
    own = _OWN_LINES - {"value"} if el["kind"] == "control" else _OWN_LINES
    lines += option_lines(
        {k: v for k, v in el.items() if k not in NON_OPTION_KEYS | own},
        dropped=STYLING_KEYS.__contains__,
        nested_dropped=NESTED_STYLING_KEYS,
    )
    if el["id"] in controls:
        lines.append(f"filtered by controls: {', '.join(controls[el['id']])}")
    return lines


def _outline(
    workbook: dict[str, Any],
    pages: list[dict[str, Any]],
    positions: dict[str, list[tuple[str, str, str]]],
    sql_ids: set[str],
) -> str:
    elements = workbook["contents"]["elements"]
    by_id = {e["id"]: e for e in elements}
    controls: dict[str, list[str]] = {}
    for e in elements:
        if e["kind"] == "control":
            for f in e.get("filters", []):
                controls.setdefault(f["source"].get("elementId"), []).append(
                    e.get("controlId")
                )
    lines = [
        f"# {workbook['name']}",
        "",
        f"Sigma: {public_url(workbook['url'])}",
        f"Path: {workbook['path']}",
        "",
    ]
    for page in pages:
        hidden = " (hidden — data prep)" if page.get("hidden") else ""
        lines += [f"## Page: {page['name']}{hidden}", ""]
        for element_id, grid_col, grid_row in positions.get(page["pageId"], []):
            el = by_id.get(element_id)
            if el is None:
                continue
            lines.append(
                f"- **{el['kind']}** `{element_id}` {_title(el)!r} "
                f"— cols {grid_col}, rows {grid_row}"
            )
            lines += [
                f"    - {line}" for line in _describe(el, by_id, controls, sql_ids)
            ]
        lines.append("")
    return "\n".join(lines)


def _find_workbook(sigma: _Sigma, ref: str) -> dict[str, Any]:
    wanted = parse_ref(ref)
    # A pasted URL may carry a name slug before the id: /workbook/<Name>-<id>.
    is_url = ref.startswith(("http://", "https://"))
    for entry in sigma.paged("workbooks"):
        ids = (entry["workbookUrlId"], entry["workbookId"])
        if wanted in ids or (is_url and wanted.endswith(tuple(f"-{i}" for i in ids))):
            return entry
    raise BundleError(f"no workbook matching {ref!r} is visible to these credentials")


@dataclass(frozen=True)
class SigmaWorkbook:
    url_id: str
    name: str
    path: str
    url: str
    updated_at: str


def list_sigma_workbooks(
    text: str | None, *, base_url: str | None
) -> list[SigmaWorkbook]:
    """List visible workbooks, newest first.

    ``text`` filters on the workbook path and name, case-insensitively.

    Raises:
        BundleError: on missing credentials or API base, a failed request, or a
            listing entry that lacks a field.
    """
    needle = (text or "").lower()
    found: list[SigmaWorkbook] = []
    for entry in _Sigma(base_url).paged("workbooks"):
        try:
            workbook = SigmaWorkbook(
                entry["workbookUrlId"],
                entry["name"],
                entry["path"],
                entry["url"],
                entry["updatedAt"],
            )
        except KeyError as exc:
            raise BundleError(
                f"Sigma workbook listing lacks {exc.args[0]!r}: {entry}"
            ) from None
        if needle in f"{workbook.path} {workbook.name}".lower():
            found.append(workbook)
    return sorted(found, key=lambda w: w.updated_at, reverse=True)


def import_sigma(
    ref: str, *, out: Path | None, base_url: str | None, force: bool
) -> Path:
    """Write a source bundle for one Sigma workbook into ``out``.

    ``out`` defaults to ``./sigma-<workbook url id>``. The bundle is built beside
    ``out`` and moved into place once complete; a non-empty ``out`` is refused
    unless ``force``.

    Raises:
        BundleError: on missing credentials or API base, any failed request or
            export, an unsupported element id, or an incomplete bundle.
    """
    sigma = _Sigma(base_url)
    meta = _find_workbook(sigma, ref)
    workbook_id = meta["workbookId"]
    out = out or Path(f"sigma-{slug(meta['workbookUrlId'], fallback='workbook')}")
    raw = sigma.request("GET", f"workbooks/{workbook_id}?includeContents=true")
    workbook = json.loads(raw)
    pages = sigma.paged(f"workbooks/{workbook_id}/pages")
    queries = sigma.paged(f"workbooks/{workbook_id}/queries")
    url = public_url(ref if ref.startswith("http") else workbook["url"])
    with staged_bundle(out, tool="sigma", url=url, raw=RAW, force=force) as bundle:
        bundle.write(RAW, raw)
        bundle.write("pages.json", json.dumps(pages, indent=1))
        _fill_bundle(sigma, bundle, workbook_id, workbook, pages, queries)
    return out


def _fill_bundle(
    sigma: _Sigma,
    bundle: SourceBundle,
    workbook_id: str,
    workbook: dict[str, Any],
    pages: list[dict[str, Any]],
    queries: list[dict[str, Any]],
) -> None:
    sql_ids: set[str] = set()
    for query in queries:
        element_id = _safe_id(query["elementId"])
        if element_id in sql_ids:
            raise BundleError(f"element {element_id} has more than one compiled query")
        sql_ids.add(element_id)
        bundle.write(f"queries/{element_id}.sql", query["sql"])
    positions = _layout_positions(workbook["contents"]["layout"])
    placed = {element_id for items in positions.values() for element_id, _, _ in items}
    if unplaced := sql_ids - placed:
        raise BundleError(
            f"compiled queries for elements not in the layout: {', '.join(sorted(unplaced))}"
        )
    bundle.write("outline.md", _outline(workbook, pages, positions, sql_ids))

    jobs: dict[str, dict[str, Any]] = {}
    visible = {p["pageId"] for p in pages if not p.get("hidden")}
    for index, page in enumerate(pages):
        if page["pageId"] in visible:
            name = f"png/{index:02d}-{slug(page['name'], fallback='page')}.png"
            jobs[name] = {"format": {"type": "png"}, "pageId": page["pageId"]}
    on_visible = {
        element_id
        for page_id, items in positions.items()
        if page_id in visible
        for element_id, _, _ in items
    }
    for el in workbook["contents"]["elements"]:
        if el["kind"] == "kpi-chart" and el["id"] in on_visible:
            name = f"kpi/{slug(_title(el), fallback='kpi')}-{_safe_id(el['id'])}.csv"
            jobs[name] = {"format": {"type": "csv"}, "elementId": el["id"]}
    for relpath, data in sigma.export_all(workbook_id, jobs).items():
        bundle.write(relpath, data)
    bundle.finish()
