"""normalize_chart — authored chart dict → discriminated normalized family model.

Parses chart_def through the AuthoredChart discriminated union, then maps
typed authored fields to the per-family normalized model.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dbt_charts.core.project import ProjectDirectory


from pydantic import BaseModel, TypeAdapter, ValidationError

from dbt_charts.core.compile.errors import CompilationError, ReferenceError
from dbt_charts.core.compile.models.board.normalized import Layout
from dbt_charts.core.compile.models.cache import INHERIT_CACHE, CachePatch
from dbt_charts.core.compile.models.chart.authored import (
    SUPPORTED_AUTHORED_CHART_TYPES,
    AuthoredChart,
    CartesianLayer,
)
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    CalloutChart,
    Chart,
    GeoshapeChart,
    HeatmapChart,
    KpiChart,
    LineChart,
    PieChart,
    PointMapChart,
    ScatterChart,
    SparkBarChart,
    TableChart,
)
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.query.authored import _BaseQueryFields
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.compile.models.refs import QueryRef
from dbt_charts.core.compile.normalize.queries import (
    normalize_query,
    resolve_external_query,
)
from dbt_charts.core.compile.normalize.variables import synthetic_query_name
from dbt_charts.core.compile.parse.parser import looks_like_sql
from dbt_charts.core.compile.support_table import apply_measure_format_to_support_table
from dbt_charts.core.compile.template.jinja import extract_variable_dependencies
from dbt_charts.core.diagnostics.codes_compile import ERR_MULTIPLES_SELF_CROSSED
from dbt_charts.core.text.case import apply_case, inferred_display_name

_AUTHORED_CHART_ADAPTER: TypeAdapter[AuthoredChart] = TypeAdapter(AuthoredChart)


def normalize_chart(
    chart_id: str,
    chart_def: Any,
    query_registry: dict[str, AnyQuery],
    base_dir: ProjectDirectory | None = None,
    default_source: str | None = None,
    source_path: str = "",
    defined_in_other_file: bool = False,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
) -> Chart:
    """Authored chart dict → discriminated normalized family model.

    Parses chart_def through the AuthoredChart discriminated union (validation
    is the authored model's job), then resolves query, computes deps, and builds
    the per-family normalized model directly — without going through the flat Chart.
    """
    # Parse to typed authored model.
    if isinstance(chart_def, BaseModel):
        authored: Any = chart_def
    elif isinstance(chart_def, dict):
        # Friendly, actionable errors for the two most common authoring mistakes
        # before the raw discriminated-union error would fire.
        _type = chart_def.get("type")
        if isinstance(_type, str) and _type not in SUPPORTED_AUTHORED_CHART_TYPES:
            raise CompilationError(
                f"Chart '{chart_id}' has unknown type '{_type}'. "
                f"Valid types: {sorted(SUPPORTED_AUTHORED_CHART_TYPES)}"
            )
        try:
            authored = _AUTHORED_CHART_ADAPTER.validate_python(chart_def)
        except ValidationError as exc:
            raise CompilationError(
                f"Chart '{chart_id}' failed validation: {exc}"
            ) from exc
    else:
        raise CompilationError(f"Invalid chart definition: {chart_def}")

    chart_type: str = authored.type

    # --- Donut → pie alias ---
    is_donut = chart_type == "donut"
    if is_donut:
        chart_type = "pie"

    # --- callout: query-less, chrome-less family ---
    # CalloutChart is the only family whose authored model extends bare
    # BaseModel rather than _BaseChartFields — it has no query/notes/
    # link/conditional_formatting fields at all. Dispatch it before the
    # shared query-resolution block below, which assumes those fields exist.
    if chart_type == "callout":
        message = authored.message
        if not message:
            raise CompilationError(
                f"Chart '{chart_id}' (callout) requires 'message' field."
            )
        callout_deps: set[str] = set()
        if authored.title:
            callout_deps |= extract_variable_dependencies(authored.title)
        return CalloutChart(
            id=chart_id,
            type="callout",
            message=message,
            title=authored.title or None,
            source_path=source_path,
            defined_in_other_file=defined_in_other_file,
            variable_dependencies=frozenset(callout_deps),
            query_is_inline=False,
            style=authored.style,
            warnings_ignore=list(authored.warnings_ignore or []),
        )

    # --- Resolve query ---
    raw_query = authored.query
    query: AnyQuery | None = None
    query_name: str | None = None
    query_is_inline = False

    if raw_query is None:
        pass
    elif isinstance(raw_query, QueryRef):
        query_ref = raw_query.ref
        if query_ref.startswith("queries."):
            query_ref = query_ref[8:]
        if "#" in query_ref:
            query_ref, query_registry = resolve_external_query(
                query_ref,
                query_registry,
                base_dir,
                sources=sources,
                cache_root=cache_root,
                board_cache=board_cache,
            )
        if query_ref not in query_registry:
            raise ReferenceError(
                query_ref, f"chart '{chart_id}'", ref_path=["charts", chart_id, "query"]
            )
        query = query_registry[query_ref]
        query_name = query_ref
    elif isinstance(raw_query, _BaseQueryFields):
        inline_name = synthetic_query_name("inline_query", chart_id)
        normalized = normalize_query(
            inline_name,
            raw_query.model_dump(exclude_none=True),
            default_source=default_source,
            base_dir=base_dir,
            sources=sources,
            cache_root=cache_root,
            board_cache=board_cache,
        )
        query_registry[inline_name] = normalized
        query = normalized
        query_name = inline_name
        query_is_inline = True
    elif isinstance(raw_query, str):
        query_ref = raw_query
        if query_ref.startswith("queries."):
            query_ref = query_ref[8:]
        if "#" in query_ref:
            query_ref, query_registry = resolve_external_query(
                query_ref,
                query_registry,
                base_dir,
                sources=sources,
                cache_root=cache_root,
                board_cache=board_cache,
            )
        if query_ref not in query_registry:
            if looks_like_sql(query_ref):
                inline_name = synthetic_query_name("inline_query", chart_id)
                normalized = normalize_query(
                    inline_name,
                    {"sql": query_ref},
                    default_source=default_source,
                    base_dir=base_dir,
                    sources=sources,
                    cache_root=cache_root,
                    board_cache=board_cache,
                )
                query_registry[inline_name] = normalized
                query_ref = inline_name
                query_is_inline = True
            else:
                raise ReferenceError(
                    query_ref,
                    f"chart '{chart_id}'",
                    ref_path=["charts", chart_id, "query"],
                )
        query = query_registry[query_ref]
        query_name = query_ref
    else:
        raise CompilationError(
            f"Chart '{chart_id}' has invalid query field: {type(raw_query)}"
        )

    # --- Variable dependencies ---
    chart_deps: set[str] = set()
    title = getattr(authored, "title", None)
    subtitle = getattr(authored, "subtitle", None)
    if title:
        chart_deps |= extract_variable_dependencies(title)
    if subtitle:
        chart_deps |= extract_variable_dependencies(subtitle)
    if query is not None and query.variable_dependencies:
        chart_deps |= query.variable_dependencies

    # conditional_formatting is carried through unresolved: role-indirected
    # color tokens (e.g. category[1]) need theme palettes/roles context that
    # only exists at style-resolution time, not here. Resolution happens in
    # resolve/chart/_kwargs.py's _base_kwargs, against chart_style_context.
    # Absent-by-design on families with no CF lowering path (structurally
    # narrowed off the authored model), same rationale as title/subtitle above.
    conditional_formatting = getattr(authored, "conditional_formatting", None)

    # --- Common base fields ---
    base: dict[str, Any] = {
        "id": chart_id,
        "query": query,
        "query_name": query_name,
        "variable_dependencies": chart_deps,
        "source_path": source_path,
        "defined_in_other_file": defined_in_other_file,
        "query_is_inline": query_is_inline,
        "notes": authored.notes
        or "",  # type-state: silent_fallback — as title/subtitle
        "link": authored.link,
        "conditional_formatting": conditional_formatting,
        "warnings_ignore": list(authored.warnings_ignore or []),
    }
    shared: dict[str, Any] = {
        "title": title or "",
        "subtitle": subtitle or "",
    }

    # Style: extract per-family patch directly from authored.  Sizing sentinels
    # (aspect_ratio, min_height, max_height) only exist on cartesian-family style
    # patches, so _build_cartesian reads them itself — not every chart_type has
    # a style patch with these fields (e.g. KpiChartStylePatch does not).
    authored_style = authored.style

    # --- Dispatch by type ---
    match chart_type:
        case "bar" | "histogram":
            return _build_cartesian(
                BarChart,
                base,
                shared,
                authored,
                authored_style,
                type=chart_type,
                chart_id=chart_id,
                stack=authored_style.stack if authored_style is not None else None,
                layers=authored.layers if authored.layers is not None else [],
                query_registry=query_registry,
                default_source=default_source,
                sources=sources,
                cache_root=cache_root,
                board_cache=board_cache,
            )
        case "line":
            return _build_cartesian(
                LineChart,
                base,
                shared,
                authored,
                authored_style,
                type="line",
                chart_id=chart_id,
                layers=authored.layers if authored.layers is not None else [],
                query_registry=query_registry,
                default_source=default_source,
                sources=sources,
                cache_root=cache_root,
                board_cache=board_cache,
            )
        case "area":
            return _build_cartesian(
                AreaChart,
                base,
                shared,
                authored,
                authored_style,
                type="area",
                chart_id=chart_id,
                stack=authored_style.stack if authored_style is not None else None,
                layers=authored.layers if authored.layers is not None else [],
                query_registry=query_registry,
                default_source=default_source,
                sources=sources,
                cache_root=cache_root,
                board_cache=board_cache,
            )
        case "scatter":
            return _build_cartesian(
                ScatterChart,
                base,
                shared,
                authored,
                authored_style,
                type="scatter",
                chart_id=chart_id,
                size=authored.size,
                shape=authored.shape,
                layers=authored.layers if authored.layers is not None else [],
                query_registry=query_registry,
                default_source=default_source,
                sources=sources,
                cache_root=cache_root,
                board_cache=board_cache,
            )
        case "heatmap":
            return _build_cartesian(
                HeatmapChart,
                base,
                shared,
                authored,
                authored_style,
                type="heatmap",
                query_registry=query_registry,
                default_source=default_source,
                sources=sources,
                cache_root=cache_root,
                board_cache=board_cache,
            )
        case "pie":
            theta = authored.theta
            if not theta:
                raise CompilationError(
                    f"Chart '{chart_id}' (pie) requires 'theta' field."
                )
            # Apply donut default inner_radius if this was authored as "donut"
            pie_style = authored_style
            if is_donut and pie_style is not None:
                if pie_style.inner_radius is None:
                    pie_style = pie_style.model_copy(update={"inner_radius": 0.6})
            elif is_donut and pie_style is None:
                from dbt_charts.core.compile.models.style.authored import (
                    PieChartStylePatch,
                )

                pie_style = PieChartStylePatch(inner_radius=0.6)  # type: ignore[call-arg]
            # Donut center total label auto-injection. The value's format
            # defaults separately, at resolve time (compile/resolve/chart/pie.py),
            # once it's a style-cascade field and the full theme/board/chart-local
            # merge is available.
            total = authored.total
            inner_radius = pie_style.inner_radius if pie_style is not None else None
            is_donut_shape = isinstance(inner_radius, (int, float)) and inner_radius > 0
            if is_donut_shape and theta and (total is None or total.label is None):
                slug = inferred_display_name(theta)
                auto_label = (
                    slug
                    if slug.isupper() and " " not in slug
                    else apply_case(slug, "title")
                )
                if "total" not in theta.lower():
                    auto_label = f"Total {auto_label}"
                from dbt_charts.core.compile.models.chart.authored import ChartTotal

                if total is None:
                    total = ChartTotal(visible=True, label=auto_label)
                else:
                    total = total.model_copy(update={"label": auto_label})
            return PieChart(
                **base,
                **shared,
                type="pie",
                theta=theta,
                color=authored.color,
                total=total,
                height=authored.height,
                width=authored.width,
                style=pie_style,
            )
        case "kpi":
            value = authored.value
            if not value:
                raise CompilationError(
                    f"Chart '{chart_id}' (kpi) requires 'value' field."
                )
            return KpiChart(
                **base,
                type="kpi",
                value=value,
                label=authored.label or "",
                support=authored.support,
                variant=authored.variant,
                background=authored.background,
                style=authored_style,
            )
        case "table":
            return TableChart(
                **base,
                **shared,
                type="table",
                rows=authored.rows,
                columns=authored.columns,
                values=authored.values,
                style=authored_style,
            )
        case "geoshape" | "map":
            return GeoshapeChart(
                **base,
                **shared,
                type=authored.type,
                color=authored.color,
                geo=authored.geo,
                geo_source=authored.geo_source,
                lookup=authored.lookup,
                value=authored.value,
                projection=authored.projection,
                height=authored.height,
                width=authored.width,
                aspect_ratio=(
                    authored_style.aspect_ratio if authored_style is not None else None
                ),
                style=authored_style,
            )
        case "point_map" | "bubble_map":
            return PointMapChart(
                **base,
                **shared,
                type=authored.type,
                color=authored.color,
                geo=authored.geo,
                geo_source=authored.geo_source,
                lookup=authored.lookup,
                value=authored.value,
                projection=authored.projection,
                basemap=authored.basemap,
                latitude=authored.latitude,
                longitude=authored.longitude,
                size=authored.size,
                collapse=authored.collapse,
                height=authored.height,
                width=authored.width,
                aspect_ratio=(
                    authored_style.aspect_ratio if authored_style is not None else None
                ),
                style=authored_style,
            )
        case "spark_bar":
            # spark_bar's authored schema has no color/sort fields (single-series,
            # fixed sizing — see _CartesianChartFields vs _SharedChartFields in
            # compile/models/chart/authored/_base.py); the normalized/resolved
            # models carry them as always-None for shape parity with other
            # chart families, not because spark_bar can author them.
            return SparkBarChart(
                **base,
                **shared,
                type="spark_bar",
                x=authored.x,
                y=authored.y,
                color=None,
                sort=None,
                style=authored_style,
            )
        case _:
            raise CompilationError(
                f"Chart '{chart_id}' has unrecognized type {chart_type!r}"
            )


def _build_cartesian(
    cls: type,
    base: dict[str, Any],
    shared: dict[str, Any],
    authored: Any,
    authored_style: Any,
    type: str,
    chart_id: str = "",
    query_registry: dict[str, AnyQuery] | None = None,
    default_source: str | None = None,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
    **extra: Any,
) -> Any:
    """Build a cartesian normalized chart.

    Only called for cartesian-family chart types (bar/histogram/line/area/
    scatter/heatmap), so authored_style — when set — is guaranteed to be a
    cartesian style patch carrying aspect_ratio/min_height/max_height/
    number_format/axis_y.
    """
    multiples = authored.multiples
    if (
        multiples is not None
        and multiples.rows is not None
        and multiples.rows == multiples.columns
    ):
        raise CompilationError.from_code(
            ERR_MULTIPLES_SELF_CROSSED, field=multiples.rows
        )
    aspect_ratio: float | None = None
    promo_min_height: float | None = None
    promo_max_height: float | None = None
    measure_format: str | FormatConfig | None = None
    if authored_style is not None:
        aspect_ratio = authored_style.aspect_ratio
        promo_min_height = authored_style.min_height
        promo_max_height = authored_style.max_height
        if authored_style.number_format:
            measure_format = authored_style.number_format
        elif (
            authored_style.axis_y is not None
            and authored_style.axis_y.labels is not None
            and authored_style.axis_y.labels.format
        ):
            measure_format = authored_style.axis_y.labels.format
    support_table = authored.support_table
    if (
        support_table is not None
        and measure_format is not None
        and isinstance(authored.y, str)
    ):
        support_table = apply_measure_format_to_support_table(
            support_table, measure_format, authored.y
        )
    layers = extra.get("layers")
    if layers and query_registry is not None:
        _promote_layer_inline_queries(
            chart_id,
            layers,
            query_registry,
            default_source,
            sources=sources,
            cache_root=cache_root,
            board_cache=board_cache,
        )
    return cls(
        **base,
        **shared,
        type=type,
        x=authored.x,
        y=authored.y,
        color=authored.color,
        x_label=authored.x_label,
        y_label=authored.y_label,
        sort=authored.sort,
        multiples=authored.multiples,
        support_table=support_table,
        height=authored.height,
        width=authored.width,
        aspect_ratio=aspect_ratio,
        min_height=promo_min_height,
        max_height=promo_max_height,
        style=authored_style,
        **extra,
    )


def _promote_layer_inline_queries(
    chart_id: str,
    layers: list[CartesianLayer],
    query_registry: dict[str, AnyQuery],
    default_source: str | None,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
) -> None:
    """Promote inline SQL strings in layer.query fields into query_registry in-place."""
    for i, layer in enumerate(layers):
        layer_query = layer.query
        if layer_query is None or layer_query in query_registry:
            continue
        if not looks_like_sql(layer_query):
            raise ReferenceError(
                layer_query,
                f"chart '{chart_id}', layer {i}",
                ref_path=["charts", chart_id, "layers"],
            )
        layer_inline_name = synthetic_query_name("inline_query_layer", chart_id, i)
        query_registry[layer_inline_name] = normalize_query(
            layer_inline_name,
            {"sql": layer_query},
            default_source=default_source,
            sources=sources,
            cache_root=cache_root,
            board_cache=board_cache,
        )
        layer.query = layer_inline_name


# Maps authored chart-type values to the sub-key on ChartStylePatch.
# Used at cascade/pipeline time (finding the family sub-patch for a given chart type).
_AUTHORED_FAMILY_TO_MONOLITHIC_KEY: dict[str, str] = {
    "bar": "bar",
    "histogram": "bar",
    "line": "line",
    "area": "area",
    "scatter": "scatter",
    "pie": "pie",
    "donut": "pie",
    "kpi": "kpi",
    "table": "table",
    "spark_bar": "spark_bar",
    "heatmap": "heatmap",
    "map": "geoshape",
    "geoshape": "geoshape",
    "point_map": "point_map",
    "bubble_map": "point_map",
    "callout": "callout",
}


def kpi_subtitle_error(chart_id: str | None) -> str:
    where = f"KPI chart '{chart_id}' " if chart_id else "KPI charts "
    return (
        f"{where}uses `subtitle:` which is no longer part of the KPI "
        "surface. Use the structured `support: {value, label}` block instead."
    )


def _collect_charts_from_layout(
    layout: Layout,
    charts: dict[str, Chart],
) -> None:
    """Collect all inline charts from layout into the charts dict.

    This normalizes the data model so ALL charts end up in board.charts,
    making chart lookup a simple dict access (board.charts[chart_id])
    instead of requiring layout traversal.

    Args:
        layout: The layout to extract charts from
        charts: Dict to add charts to (modified in place)
    """
    for item in layout.items:
        if item.type == "chart" and item.chart:
            # Add inline chart to charts dict if not already there
            chart_id = item.chart.id
            if chart_id and chart_id not in charts:
                charts[chart_id] = item.chart

        elif item.type == "board" and item.board:
            # Recurse into nested boards
            _collect_charts_from_layout(item.board.layout, charts)
            # Also collect charts from nested board's charts dict
            if item.board.charts:
                for chart_id, chart in item.board.charts.items():
                    if chart_id not in charts:
                        charts[chart_id] = chart


# Chart keys that carry data-binding field references (axis, encoding, geo, etc.).
# Presentation-only keys (title, subtitle, description, link, format, ...) are
# excluded — a dotted string in those positions is not a field ref.
_FIELD_CHANNEL_KEYS: frozenset[str] = frozenset(
    {
        "x",
        "y",
        "color",
        "theta",
        "size",
        "shape",
        "sort",
        "value",
        "latitude",
        "longitude",
        "geo",
        "lookup",
        "background",
    }
)


def generate_inline_chart_id(
    chart_def: Any, fallback_id: str, used_ids: set[str]
) -> str:
    """Generate a unique chart ID for an inline chart definition.

    Uses the chart's authored display text (``label`` for KPI, ``title``
    otherwise) if available; falls back to the provided ID. Handles
    duplicates by appending a number.

    Args:
        chart_def: The chart definition dict
        fallback_id: Fallback ID to use if no title (e.g., "row0")
        used_ids: Set of already-used chart IDs (will be mutated)

    Returns:
        A unique chart ID
    """

    # KPI charts carry their authored display text in ``label`` (the slot
    # rendered above the headline value); every other chart type uses
    # ``title``. Omitted display text stays omitted, so inline ids fall back
    # to the row/col-derived id when neither slot is authored.
    if isinstance(chart_def, dict):
        chart_type = chart_def.get("type")
        title = (
            chart_def.get("label") if chart_type == "kpi" else chart_def.get("title")
        )
    else:
        chart_type = getattr(chart_def, "type", None)
        if chart_type == "kpi":
            title = getattr(chart_def, "label", None)
        else:
            title = getattr(chart_def, "title", None)

    # Generate base slug
    base_slug = _title_to_slug(title) if title else fallback_id

    # Ensure uniqueness
    if base_slug not in used_ids:
        used_ids.add(base_slug)
        return base_slug

    # Append number to make unique
    counter = 2
    while f"{base_slug}-{counter}" in used_ids:
        counter += 1
    unique_id = f"{base_slug}-{counter}"
    used_ids.add(unique_id)
    return unique_id


def _title_to_slug(title: str) -> str:
    """Convert a title to a URL-friendly slug.

    Args:
        title: Display title (e.g., "My First Chart")

    Returns:
        Slug string (e.g., "my-first-chart")
    """
    # Convert to lowercase
    slug = title.lower()
    # Replace spaces and underscores with hyphens
    slug = slug.replace(" ", "-").replace("_", "-")
    # Remove any characters that aren't alphanumeric or hyphens
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    # Collapse multiple hyphens
    slug = re.sub(r"-+", "-", slug)
    # Strip leading/trailing hyphens
    slug = slug.strip("-")
    return slug or "chart"
