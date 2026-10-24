"""RenderResult — the return type of render()."""

from pydantic import BaseModel

from dbt_charts.core.diagnostics import Diagnostic


class RenderResult(BaseModel):
    """Result of render().

    ``chart_errors`` is every chart-scoped failure render() saw this call —
    from the draw (the SVG rasterization every format runs, so the
    measurement-warning family and ERR-CHART-PAINTED-NO-MARKS can be
    detected) and, for a data-bearing format, also from that format's own
    layout-tree walk. A caller inspecting *any* format's failures (an agent
    reading ``json``) wants this full set.

    ``payload_errors`` is the errors that actually invalidate ``output`` —
    not strictly a subset of ``chart_errors``: for svg/html/png/pdf the
    payload IS the drawing, so this equals ``chart_errors``; for a
    data-bearing format (json/text/yaml/data) the payload is the layout-tree
    walk, so this holds only the walk's own errors — a chart that painted no
    marks but walked fine does not invalidate a data-bearing payload, even
    though it is still reported in ``chart_errors``. When a chart fails in
    both the draw and the walk with the same ``(chart_id, code)``, renderer.py's
    merge drops the walk's diagnostic from ``chart_errors`` as a duplicate —
    so in that case ``chart_errors`` holds the *draw's* diagnostic object and
    ``payload_errors`` a *different* (walk) diagnostic object for the same
    failure, not the same object. This matters because only ``chart_errors``
    is stamped with authored source location (``board.py``'s
    ``stamp_diagnostics`` call) — a ``payload_errors`` consumer gets a
    diagnostic with no ``range``/authored path in that case.

    ``terminal`` is grouped with the drawing-payload formats above (its
    ``payload_errors`` equals ``chart_errors``) even though its own payload
    is actually an independent walk that never touches ``svg_content`` —
    structurally the same shape as a data-bearing format.

    Don't unify the two success gates: ``render_pipeline.py`` judges success
    by ``board_error is None and not payload_errors`` (copying YAML — a
    chart that draws nothing doesn't matter), while ``board.py``'s
    ``status="partial"`` and ``cli/commands/render.py``'s
    ``fail_on_chart_errors`` judge by ``chart_errors`` instead
    (``BoardRenderResult`` doesn't carry ``payload_errors`` at all) — ``dct
    render`` is a user asking to see the board, where a chart that draws
    nothing does matter. The same blank-chart board can legitimately be a
    success through one gate and a failure through the other.

    ``query_ms`` is the wall time of the pre-render query pass — cache reads
    and warehouse queries together. ``None`` when the render failed before
    that pass ran. ``prefetch_query_names`` is what a drawn render left
    unexecuted in the hidden tabs (inner tabs of a hidden tab included) whose cache policy sets ``prefetch``;
    a host warms them with ``prefetch_tab_queries``.
    """

    output: str | bytes | None = None
    chart_errors: list[Diagnostic] = []
    payload_errors: list[Diagnostic] = []
    board_error: Diagnostic | None = None
    warnings: list[Diagnostic] = []
    suppressed_warnings: list[Diagnostic] = []
    query_ms: float | None = None
    prefetch_query_names: frozenset[str] = frozenset()
