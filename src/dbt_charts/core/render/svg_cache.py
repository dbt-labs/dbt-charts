"""Content-addressed memo for rendered chart SVGs.

Stage: RENDER (vl-convert)
Purpose: Give a render somewhere to keep the SVG a chart already rendered to, so a
re-render of unchanged content skips vl-convert, which dominates render compute.

The key is **the content**, never the version that asked for it. It hashes the
Vega-Lite spec (which already carries the resolved rows and the theme-cascaded
encodings), the dimensions, the output format, the placeholder flag, the board's
resolved style, the active link context, and a renderer fingerprint. Same key
therefore means byte-identical SVG, and that one property is what the whole
design rests on:

- Staleness is **unrepresentable**, not merely unlikely. New warehouse rows, a
  theme edit, a variable change, an edited nested board, and a vl-convert upgrade
  each produce a different spec or a different fingerprint, so each produces a
  different key. There is no invalidation policy here because there is nothing
  to invalidate — only entries to evict.
- Nothing version-shaped belongs in the key. A board's git blob sha rotates for
  every chart on the board whenever any one of them is edited, so keying on it
  would miss on exactly the case this exists for; keying on it *and* reading the
  previous version's entry would answer with the previous render of the chart
  that just changed. The same goes for a chart's id: it is board-local identity,
  stable across edits while the content under it changes.

**Nothing here touches a database, and that is the whole point.** A durable host
supplies a `loader` — an opaque callable from key to SVG — and the memo consults
it on a miss, once per distinct key it still holds, then buffers what to write
back. So the render reads about one row per distinct chart-content it draws, and
never more because of what the store happens to be holding.

That bound is the reason this is demand-driven rather than bulk-filled. The key
is content, so new warehouse rows mint a fresh key for every chart that drew
them; entries accumulate with *time*, not with a board's chart count. Anything
that fetched a whole board's worth up front would therefore transfer a board's
history into memory to answer for its N charts, and no ceiling on that fetch is
better than not making it — the key of the row you want is already in hand at the
moment you want it.

`board_path` and `chart_id` ride alongside the entries as host metadata and are
never part of the key — the same split `QueryResultCache` makes between its key
and its `board_slug`/`query_name`. Neither is a fetch axis: a lookup is by
content, so they answer only "which chart is this row, and where did it come
from?" — for a host's own debugging and attribution.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from dbt_charts.core.diagnostics.chart_data import ChartDataError

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
    from dbt_charts.core.render.board_links import LinkContext

# Bump when something outside the two versioned dependencies below alters the
# SVG for an unchanged spec. Rarely needed: the fingerprint already folds in the
# installed vl-convert version *and* this package's own, and the package version
# is what tracks the vendored font set and every post-processing step in
# render_svg_content. A host store need not expire anything — an upgrade rotates
# every entry on its own, with no migration and no manual purge.
RENDERER_VERSION = "3"

# Largest single entry worth keeping. Above this the entry would dominate any
# store it is drained into and push out the small hot entries that make an edit
# feel instant — and re-rendering one chart is cheap next to that.
MAX_ENTRY_BYTES = 4 * 1024 * 1024

# MemorySvgStore's total budget, in encoded bytes; least-recently-used entries go
# first once it is exceeded.
MEMORY_STORE_MAX_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class MintedSvg:
    """An entry this render produced, for a host that wants to keep it.

    ``chart_id`` is diagnostic metadata, not part of ``key`` — it answers
    "which chart is this 3 MB row?" without ever narrowing what the entry can
    answer for.
    """

    key: str
    svg: str
    chart_id: str


class RenderedSvgCache:
    """The render's SVG memo: an LRU dict over a host's optional ``loader``.

    A render with no memo bound goes through vl-convert for every chart. With a
    cache but no ``loader`` it is a within-render memo only: two
    identical charts on one board share a render, nothing survives the process.

    A host that wants durability passes a ``loader`` and reads ``touched`` /
    ``minted`` afterwards to write back. ``loader`` is called once per distinct
    key while that entry stays resident — a hit is seated, a miss is remembered
    — so a board of N charts costs about N reads however much history the store
    holds. (Only a render drawing past ``max_entries`` distinct charts re-reads,
    having evicted the entry it seated.)

    Entries are evicted, never invalidated: the key is content, so an entry is
    correct until it is dropped.
    """

    def __init__(
        self,
        loader: Callable[[str], str | None] | None = None,
        max_entries: int = 4096,
        max_entry_bytes: int = MAX_ENTRY_BYTES,
    ) -> None:
        self._loader = loader
        self._entries: OrderedDict[str, str] = OrderedDict()
        self._max_entries = max_entries
        self._max_entry_bytes = max_entry_bytes
        self._missing: set[str] = set()
        self._touched: set[str] = set()
        self._minted: dict[str, MintedSvg] = {}

    def get(self, key: str) -> str | None:
        svg = self._entries.get(key)
        if svg is not None:
            self._entries.move_to_end(key)
            return svg
        if self._loader is None or key in self._missing:
            return None
        loaded = self._loader(key)
        if loaded is None:
            # Remembered so two identical charts on one board cost one read.
            self._missing.add(key)
            return None
        self._touched.add(key)
        self._seat(key, loaded)
        return loaded

    def put(self, key: str, svg: str, chart_id: str) -> None:
        if len(svg.encode()) > self._max_entry_bytes:
            return
        self._minted[key] = MintedSvg(key=key, svg=svg, chart_id=chart_id)
        self._seat(key, svg)

    def _seat(self, key: str, svg: str) -> None:
        """Insert as most-recent and trim to the ceiling.

        Shared by both entry paths deliberately: an entry the loader supplied is
        as much a claim on the ceiling as one this render minted, and letting
        loaded entries in around it is precisely how a bulk pre-fill used to
        seat far more than ``max_entries`` and bound nothing.
        """
        self._entries[key] = svg
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            evicted, _ = self._entries.popitem(last=False)
            self._minted.pop(evicted, None)

    @property
    def touched(self) -> frozenset[str]:
        """Keys this render took from the loader.

        A host bumps these so a chart that draws on every page load doesn't age
        out of an LRU store precisely because it keeps hitting. Minted keys are
        not here: their insert stamps the same column already.
        """
        return frozenset(self._touched)

    @property
    def minted(self) -> tuple[MintedSvg, ...]:
        """Entries this render produced that did not come from the loader."""
        return tuple(self._minted.values())


class MemorySvgStore:
    """A process-lifetime, byte-bounded LRU a host hands each render's memo.

    Each render gets its own `RenderedSvgCache` with ``loader=store.load`` and
    calls ``store.save(memo.minted)`` afterwards. The memo is not thread-safe, so
    it stays per-render; only this store is shared across concurrent renders.
    """

    def __init__(self, max_bytes: int = MEMORY_STORE_MAX_BYTES) -> None:
        self._entries: OrderedDict[str, str] = OrderedDict()
        self._max_bytes = max_bytes
        self._bytes = 0
        self._lock = threading.Lock()

    def load(self, key: str) -> str | None:
        with self._lock:
            svg = self._entries.get(key)
            if svg is not None:
                self._entries.move_to_end(key)
            return svg

    def save(self, minted: Iterable[MintedSvg]) -> None:
        with self._lock:
            for entry in minted:
                old = self._entries.pop(entry.key, None)
                if old is not None:
                    self._bytes -= len(old.encode())
                self._entries[entry.key] = entry.svg
                self._bytes += len(entry.svg.encode())
            while self._bytes > self._max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._bytes -= len(evicted.encode())


_active_cache: ContextVar[RenderedSvgCache | None] = ContextVar(
    "_active_svg_cache", default=None
)


def active_svg_cache() -> RenderedSvgCache | None:
    """The memo for the render in flight, or None when nobody supplied one.

    None is a real state, not a missing value: every caller that has not opted
    in renders uncached, exactly as before this module existed.
    """
    return _active_cache.get()


@contextmanager
def svg_cache_scope(cache: RenderedSvgCache | None) -> Generator[None]:
    """Bind *cache* for the duration of a render.

    Scoped rather than global so one render's memo cannot leak into an unrelated
    render, and restored on exit so nested renders (a nested board rendered inside
    a board) put the outer memo back.
    """
    token = _active_cache.set(cache)
    try:
        yield
    finally:
        _active_cache.reset(token)


@lru_cache(maxsize=1)
def _renderer_fingerprint() -> str:
    """The versions that decide the bytes: the renderer, and us.

    ``dbt-charts`` is in here because the emit path, the SVG post-processing, and
    the vendored font set all ship with this package and all change the output of
    an unchanged spec. Without it a release would serve the previous release's
    pixels out of a durable store until someone remembered to bump
    ``RENDERER_VERSION`` by hand.
    """
    from importlib.metadata import version  # noqa: PLC0415

    return f"{version('vl-convert-python')}/{version('dbt-charts')}"


def svg_cache_key(
    spec: dict[str, Any],
    *,
    output_format: str,
    width: float | None,
    height: float | None,
    is_placeholder: bool,
    resolved_style: ResolvedStyle,
    link_context: LinkContext | None,
) -> str:
    """Hash everything that can change the rendered bytes.

    Call this *before* rendering: `render_vega_spec` pops its ``$df_*`` sentinels
    out of ``spec`` in place, and those sentinels change the output, so a key
    taken afterwards would collide across genuinely different renders.

    Two arguments are content that is **not** in the spec, and both are here for
    the same reason — `render_vega_spec` reads them after vl-convert returns:
    ``resolved_style`` through the placeholder overlay, and ``link_context``
    through the sentinel-href rewrite, which bakes the host's board-root prefix
    (including the branch segment) straight into the markup. Leave either out and
    one board's SVG answers for another's links. Anything a future
    post-processing step reads belongs here too — the rule is that the key covers
    every input to the bytes, not merely every input to Vega.

    ``spec`` is serialized strictly, and deliberately: vl-convert serializes the
    same dict itself and refuses exactly what `json.dumps` refuses, so a spec that
    cannot be hashed could never have rendered. The failure is re-raised as the
    same per-chart error vl-convert's own refusal produces, so injecting a cache
    cannot turn one chart's error tile into a whole failed board.
    """
    try:
        canonical_spec = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ChartDataError(f"Chart spec is not JSON-serializable: {exc}") from exc
    payload = json.dumps(
        [
            canonical_spec,
            output_format,
            width,
            height,
            is_placeholder,
            repr(resolved_style),
            repr(link_context),
            RENDERER_VERSION,
            _renderer_fingerprint(),
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()
