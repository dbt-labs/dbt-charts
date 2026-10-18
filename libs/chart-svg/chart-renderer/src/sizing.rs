//! Board sizing pass — computes every layout item's own box (x, y, width,
//! height) from the STATIC resolved board, mirroring two production Python
//! modules at function granularity so the two can be read side by side:
//!
//!   - `dbt_charts.core.compile.sizing`          (width laws — pure config)
//!   - `dbt_charts.core.render.sizing` +
//!     `dbt_charts.core.render.layout_sizing`    (height laws — data-aware)
//!
//! Function names below match their Python counterpart (snake_case kept
//! verbatim where Rust allows it); each doc comment cites the Python
//! function/line it ports.
//!
//! ## Scope: render-first is the real oracle; alignment is a cache
//! reconciliation this port's architecture doesn't need
//!
//! `board_sweep.py` resolves its oracle board with `render_first=True`
//! (`build_resolved_board(..., render_first=True)`) — the same flag
//! production uses for every SVG/HTML render (`renderer.py:628`). Under
//! that flag, `layout_sizing.SizingRenderCtx.executor` is set, so
//! `_make_data_aware_height_provider`'s `provider()` closure DOES reach its
//! Vega render-first branch for every Vega family (bar/line/area/scatter/
//! pie/heatmap): each chart is actually rendered once during sizing to
//! measure its natural height, and `render_ctx.render_cache` is populated
//! with that render for reuse at final composition. `kpi` stays on the
//! static aspect-ratio/theme-constant arm regardless of `render_first`
//! (`NON_ASPECT_RATIO_TYPES`); `table` is special-cased UNCONDITIONALLY,
//! before that branch (measured by an unbounded render,
//! `_get_table_height_from_data`). `callout`/`spark_bar` are also
//! unconditional (`_render_chart_to_svg` natural-height calls). `sizing::render_first_vega_height` is this port's own Vega
//! render-first branch, measured against this real oracle — see
//! `docs/laws-board.md`'s "Render-first is the real oracle" section for the
//! earlier `render_first=False` bug this replaced (a Vega chart sized
//! wrong, e.g. a pie/attached-table case: 300px static vs 384px real).
//!
//! `render_ctx.render_cache` being populated means `calculate_data_aware_
//! layout`'s post-passes DO run in production: `_fix_slot_heights_in_tree`,
//! `_align_all_cols_in_tree` (cols/grid alignment), `_ensure_resolved_
//! variants_in_tree`. None of them are ported here, but not because they
//! are unreachable — because this port's two-pass architecture (compute
//! every item's final box FIRST, then render each chart exactly once,
//! directly at that final box) has no earlier, differently-sized render to
//! reconcile in the first place; those passes exist in Python only to fix
//! up a cache entry from a chart Python already rendered once, at a height
//! that turned out wrong for its final slot. A literal port of the cols-
//! alignment rewrite (`_align_all_cols_in_tree`'s cols branch) was built
//! and tested against a purpose-built harder rung and REGRESSED it —
//! `_fix_slot_heights_in_tree` runs first in production and neutralizes
//! that rewrite in every real case by contaminating `natural_heights` to
//! the row height before alignment ever reads it. `docs/laws-board.md`'s
//! "Why alignment doesn't need porting" section carries the full mechanism,
//! the evidence (four dedicated ladder rungs), and the one real exception
//! (`_correct_support_table_height`, a separately-tracked gap).
//!
//! `compile.sizing.board_container_width` / `get_board_gap` are themselves
//! pure functions of chart config (no data dependency) — their value is
//! identical whether computed by `build_resolved_board_static` or
//! `build_resolved_board`. This module treats them as GIVEN static input
//! (`BoardPayload`'s `width` / `ResolvedLayout.gap`), not re-derived.

use std::collections::{HashMap, HashSet};

use serde::Deserialize;
use serde_json::Value;

/// Every chart's own datasets, keyed by chart id then query name — the
/// same shape `BoardPayload.chart_datasets` carries.
pub type ChartDatasets = HashMap<String, HashMap<String, Vec<serde_json::Map<String, Value>>>>;

/// compile/sizing.py's DEFAULT_GRID_COLUMNS.
pub const DEFAULT_GRID_COLUMNS: i64 = 24;

/// The title baseline's ascent as a fraction of the font size.
const TITLE_ASCENT_RATIO: f64 = 0.8;

/// Round-half-to-even, as Python's `round()` does it. Kept apart from board.rs's
/// private `px()`, which also floors to an integer for SVG sub-pixel snapping.
pub(crate) fn python_round(x: f64) -> f64 {
    let floor = x.floor();
    let diff = x - floor;
    if diff < 0.5 {
        floor
    } else if diff > 0.5 {
        floor + 1.0
    } else if (floor as i64) % 2 == 0 {
        floor
    } else {
        floor + 1.0
    }
}

/// First-baseline distance below a title's top at `font_size`
/// (`title_ascent`): the one rule the Vega-Lite title, the table
/// band and the row title band share.
pub(crate) fn title_ascent(font_size: f64) -> f64 {
    python_round(font_size * TITLE_ASCENT_RATIO)
}

// ===========================================================================
// compile/sizing.py — width laws (pure config, no data)
// ===========================================================================

/// compile/sizing.py::parse_dimension
pub fn parse_dimension(value: Option<&str>, total: f64) -> Option<f64> {
    let v = value?.trim();
    if let Some(pct) = v.strip_suffix('%') {
        return pct.trim().parse::<f64>().ok().map(|p| (p / 100.0) * total);
    }
    if let Some(px) = v.strip_suffix("px") {
        return px.trim().parse::<f64>().ok();
    }
    v.parse::<f64>().ok()
}

/// compile/sizing.py::item_grid_span
pub fn item_grid_span(col_span: Option<i64>, row_span: Option<i64>) -> (i64, i64) {
    (col_span.unwrap_or(1), row_span.unwrap_or(1))
}

/// compile/sizing.py::grid_span_width
pub fn grid_span_width(track_width: f64, span: i64, gap: f64) -> f64 {
    track_width * span as f64 + gap * (span - 1) as f64
}

/// compile/sizing.py::resolve_cols_widths
pub fn resolve_cols_widths(
    specified_widths: &[Option<f64>],
    content_width: f64,
    item_count: usize,
) -> Vec<f64> {
    if item_count == 0 {
        return Vec::new();
    }
    let total_specified: f64 = specified_widths.iter().filter_map(|w| *w).sum();
    let auto_count = specified_widths.iter().filter(|w| w.is_none()).count();

    if auto_count == 0 {
        return specified_widths.iter().map(|w| w.unwrap_or(0.0)).collect();
    }

    let remaining_width = content_width - total_specified;
    if remaining_width > 0.0 {
        let auto_width = remaining_width / auto_count as f64;
        return specified_widths.iter().map(|w| w.unwrap_or(auto_width)).collect();
    }

    let equal_share = content_width / item_count as f64;
    let auto_width = equal_share;
    let specified_budget = (content_width - auto_width * auto_count as f64).max(0.0);
    let scale = if total_specified > 0.0 { specified_budget / total_specified } else { 0.0 };
    specified_widths
        .iter()
        .map(|w| match w {
            None => auto_width,
            Some(width) => width * scale,
        })
        .collect()
}

/// compile/sizing.py::rows_item_width
pub fn rows_item_width(
    user_width: Option<&str>,
    chart_width: Option<f64>,
    available_width: f64,
) -> f64 {
    let mut parsed = parse_dimension(user_width, available_width);
    if parsed.is_none() {
        parsed = chart_width;
    }
    match parsed {
        Some(p) if p > 0.0 => p.min(available_width),
        _ => available_width,
    }
}

// ===========================================================================
// render/sizing.py — height laws
// ===========================================================================

/// render/compile.sizing's per-family theme aspect_ratio slot
/// (`ChartDefaultsDoc`'s own field for the family) — a subset of
/// `_FAMILY_SLOT` (compile/resolve/chart/_kwargs.py) restricted to the
/// chart-only families this rung's corpus actually places.
#[derive(Deserialize, Debug, Clone, Copy)]
pub struct FamilyDefaultsDoc {
    pub aspect_ratio: Option<f64>,
}

#[derive(Deserialize, Debug, Clone, Copy)]
pub struct KpiDefaultsDoc {
    pub default_height: f64,
}

/// `resolved_style.chart_defaults.spark_bar.bar` — the two fields
/// `get_chart_content_height`'s spark_bar branch reads (render/sizing.
/// py:241-252's `sb.bar.height`/`sb.bar.padding`).
#[derive(Deserialize, Debug, Clone, Copy)]
pub struct SparkBarBarDefaultsDoc {
    pub height: f64,
    pub padding: f64,
}

/// `resolved_style.chart_defaults.spark_bar`, narrowed to what the static
/// content-height estimate reads (`max_bars`, `bar.height`, `bar.padding`)
/// — `render/sizing.py:241-252`.
#[derive(Deserialize, Debug, Clone, Copy)]
pub struct SparkBarDefaultsDoc {
    pub max_bars: i64,
    pub bar: SparkBarBarDefaultsDoc,
}

/// `resolved_style.chart_defaults.padding` (`PaddingStyle`), narrowed to
/// the two sides `_unresolved_height` reads (`layout_sizing.py`) —
/// the BOARD's own generic chart padding, read only when a chart never
/// resolved (no chart-local `layout_padding` override to read instead, see
/// `vertical_inset`'s own doc for the resolved-chart counterpart).
#[derive(Deserialize, Debug, Clone, Copy)]
pub struct ChartDefaultsPaddingDoc {
    pub top: f64,
    pub bottom: f64,
}

/// resolved_style.chart_defaults, the theme constants
/// `get_chart_content_height` reads. Deserialized from the STATIC board's
/// own `style.chart_defaults` — a compile-time/theme fact, not data-aware.
#[derive(Deserialize, Debug, Clone)]
pub struct ChartDefaultsDoc {
    pub default_chart_height: f64,
    pub default_table_height: f64,
    pub min_height: f64,
    pub max_height: f64,
    pub aspect_ratio: f64,
    pub padding: ChartDefaultsPaddingDoc,
    pub kpi: KpiDefaultsDoc,
    pub spark_bar: SparkBarDefaultsDoc,
    pub bar: FamilyDefaultsDoc,
    pub line: FamilyDefaultsDoc,
    pub area: FamilyDefaultsDoc,
    pub scatter: FamilyDefaultsDoc,
    pub heatmap: FamilyDefaultsDoc,
    pub pie: FamilyDefaultsDoc,
    pub histogram: FamilyDefaultsDoc,
    pub geoshape: FamilyDefaultsDoc,
    pub point_map: FamilyDefaultsDoc,
}

/// `compile/resolve/chart/_kwargs.py::_FAMILY_SLOT`, restricted to the
/// families `get_chart_content_height`'s aspect-ratio ladder can reach
/// (kpi/table/callout/spark_bar are dispatched by name before this is ever
/// called — see that function's own doc). A type not in `_FAMILY_SLOT` at
/// all, or one production routes to a slot this struct carries no data
/// for, refuses by name rather than silently borrowing the board's own
/// generic `chart_defaults.aspect_ratio` the way an earlier version of
/// this port did (HIGH 1(c) — that silently returned the wrong ratio for
/// `geoshape`/`map`/`point_map`/`bubble_map`, whose own theme default
/// (1.2) differs from the generic one).
fn family_slot_aspect(chart_type: &str, defaults: &ChartDefaultsDoc) -> Result<f64, String> {
    let fam = match chart_type {
        "bar" => &defaults.bar,
        "line" => &defaults.line,
        "area" => &defaults.area,
        "scatter" => &defaults.scatter,
        "heatmap" => &defaults.heatmap,
        "pie" | "donut" => &defaults.pie,
        "histogram" => &defaults.histogram,
        "geoshape" | "map" => &defaults.geoshape,
        "point_map" | "bubble_map" => &defaults.point_map,
        other => return Err(format!("sizing: chart type not ported: {other}")),
    };
    Ok(fam.aspect_ratio.unwrap_or(0.0))
}

/// numeric.py::aspect_ratio_height
fn aspect_ratio_height(width: f64, aspect_ratio: f64, min_height: f64, max_height: f64) -> f64 {
    min_height.max(max_height.min(width / aspect_ratio))
}

/// render/sizing.py::get_chart_content_height. `resolve_chart_item_height`
/// still dispatches `table` (unbounded-render sizing) and `spark_bar`
/// (natural-render sizing) by name BEFORE ever calling this — those two
/// branches here are the STATIC placeholder Python itself falls back to
/// only when no resolved chart exists to size from (`_unresolved_height`'s
/// caller, `unresolved_height` below), never reached from a resolve-
/// succeeded chart's own sizing.
///
/// `spark_bar_titled` is `None` from every caller except `unresolved_
/// height`: kpi/table/callout ignore it, and the Vega aspect-ratio ladder
/// never reaches the spark_bar arm. A spark_bar `chart_error` item has no
/// resolved `SparkBarChart` to read `chart.title` off (render/sizing.
/// py:250's `isinstance(chart, SparkBarChart) and chart.title`) — the
/// compiled `ChartIdentity` carries no title field — so `unresolved_height`
/// passes `None` and this refuses by name rather than guessing whether the
/// missing chart was titled.
pub fn get_chart_content_height(
    chart_type: &str,
    authored_height: Option<f64>,
    aspect_ratio: Option<f64>,
    min_height: Option<f64>,
    max_height: Option<f64>,
    width: Option<f64>,
    spark_bar_titled: Option<bool>,
    defaults: &ChartDefaultsDoc,
) -> Result<f64, String> {
    if chart_type == "kpi" {
        // kpi ignores an authored chart-root height entirely — the theme
        // constant is the only law (render/sizing.py:233-234).
        return Ok(defaults.kpi.default_height);
    }
    if chart_type == "table" {
        return Ok(defaults.default_table_height);
    }
    if chart_type == "callout" {
        // Natural-height element — the real height comes from render-first
        // sizing; this static estimate is a placeholder (render/sizing.
        // py:236-238).
        return Ok(0.0);
    }
    if chart_type == "spark_bar" {
        let sb = &defaults.spark_bar;
        let body = sb.max_bars as f64 * (sb.bar.height + sb.bar.padding) + sb.bar.padding;
        return match spark_bar_titled {
            Some(true) => Ok(body + crate::constants::estimates::SPARK_BAR_TITLE_HEIGHT_PX),
            Some(false) => Ok(body),
            None => Err(
                "sizing: chart_error spark_bar content height needs an authored title fact \
                 not forwarded by the payload"
                    .to_string(),
            ),
        };
    }
    // Step 1: explicit chart-root height wins everything (no clamping).
    if let Some(h) = authored_height {
        return Ok(h);
    }
    // Steps 2-4: aspect-ratio-driven sizing for plot-style charts.
    if let Some(w) = width {
        if w > 0.0 {
            let min_h = min_height.unwrap_or(defaults.min_height);
            let max_h = max_height.unwrap_or(defaults.max_height);
            if let Some(ar) = aspect_ratio {
                return Ok(aspect_ratio_height(w, ar, min_h, max_h));
            }
            let family_aspect = family_slot_aspect(chart_type, defaults)?;
            let aspect = if family_aspect > 0.0 { family_aspect } else { defaults.aspect_ratio };
            if aspect > 0.0 {
                return Ok(aspect_ratio_height(w, aspect, min_h, max_h));
            }
        }
    }
    Ok(defaults.default_chart_height)
}

/// layout_sizing.py::_make_data_aware_height_provider's `_unresolved_height`: the
/// slot height for a chart whose resolve failed, reached at every one of
/// production's degrade sites — HIGH 1(a). The counterpart to
/// `vertical_inset` below: reads the BOARD's own generic chart padding
/// (`resolved_style.chart_defaults.padding`) rather than a chart-local
/// `layout_padding` override, since there is no resolved chart here to have
/// baked one onto.
fn unresolved_height(
    chart_type: &str,
    authored_height: Option<f64>,
    aspect_ratio: Option<f64>,
    min_height: Option<f64>,
    max_height: Option<f64>,
    spark_bar_titled: Option<bool>,
    width: f64,
    card_padding: f64,
    defaults: &ChartDefaultsDoc,
) -> Result<f64, String> {
    let content = get_chart_content_height(
        chart_type,
        authored_height,
        aspect_ratio,
        min_height,
        max_height,
        Some(width),
        spark_bar_titled,
        defaults,
    )?;
    Ok(content + 2.0 * card_padding + defaults.padding.top + defaults.padding.bottom)
}

/// layout_sizing.py::_make_data_aware_height_provider's `_vertical_inset`
/// closure: card_pad on top+bottom plus any chart-local
/// style.<family>.padding.
fn vertical_inset(card_padding: f64, layout_padding_top: f64, layout_padding_bottom: f64) -> f64 {
    2.0 * card_padding + layout_padding_top + layout_padding_bottom
}

fn layout_padding_vertical(chart_json: &Value) -> Result<(f64, f64), String> {
    let padding = chart_json
        .get("layout_padding")
        .ok_or("chart item missing layout_padding")?;
    let top = padding.get("top").and_then(Value::as_f64).ok_or("layout_padding.top missing")?;
    let bottom = padding
        .get("bottom")
        .and_then(Value::as_f64)
        .ok_or("layout_padding.bottom missing")?;
    Ok((top, bottom))
}

/// `additive_padding(card_padding, chart.layout_padding)` (spec_builders.py) —
/// card padding on all four sides plus the chart-local override, the
/// `Padding` a Vega-family `Payload` carries so its own spec insets its ink.
fn additive_padding(chart_json: &Value, card_padding: f64) -> Result<crate::Padding, String> {
    let lp = chart_json.get("layout_padding").ok_or("chart item missing layout_padding")?;
    let side = |name: &str| -> Result<f64, String> {
        lp.get(name).and_then(Value::as_f64).ok_or_else(|| format!("layout_padding.{name} missing"))
    };
    Ok(crate::Padding {
        left: card_padding + side("left")?,
        right: card_padding + side("right")?,
        top: card_padding + side("top")?,
        bottom: card_padding + side("bottom")?,
    })
}

/// pie/arc have a fixed natural height (a direct-label aspect ratio, or an
/// arc-attached-table composition) — layout_sizing.py's
/// `_FIXED_ASPECT_PIE_TYPES`.
const FIXED_ASPECT_PIE_TYPES: [&str; 2] = ["pie", "arc"];

/// The height change at which production re-renders a Vega chart instead of
/// reusing its cached render: `abs(natural_h - effective_target) < 1.0`
/// skips it (`layout_sizing.py:1354`, and `_fix_slot_heights_in_tree`'s
/// identical guard).
const RERENDER_MIN_DELTA_PX: f64 = 1.0;

/// Whether a chart that sized to `natural` and sits at `height` was re-rendered.
pub(crate) fn height_moved(natural: f64, height: f64) -> bool {
    (height - natural).abs() >= RERENDER_MIN_DELTA_PX
}

/// compile/models/chart/normalized/_base.py:202's `NON_ASPECT_RATIO_TYPES`:
/// families with no natural aspect-ratio height of their own (row-count,
/// theme-constant, or natural-render sizing instead) — excluded from
/// `_col_chart_body_target`'s Vega-only max and from `_fix_slot_heights_in_
/// tree`/`_align_all_cols_in_tree`'s cols-branch rewrite.
pub(crate) fn is_non_aspect_ratio_type(chart_type: &str) -> bool {
    matches!(chart_type, "table" | "kpi" | "spark_bar" | "callout")
}

/// layout_sizing.py's render-first Vega branch
/// (`_make_data_aware_height_provider`'s `provider()`):
/// render the chart at `(width, static_estimate)` through the port's own
/// families (standing in for vl-convert) and read back the real height via
/// `extract_svg_height` — the same fact `_render_chart_to_svg`/
/// `render_chart_item` return (`layout_sizing.py::_render_chart_to_svg`,
/// chart/rendering.py:340-406). Then apply the SAME clamp/trust split
/// production applies to its own real render: a `_FIXED_ASPECT_PIE_TYPES`
/// chart clamps only a tiny (<=2px) overshoot back to the estimate and
/// otherwise trusts the real value even when it undershoots or overshoots
/// by more; every other Vega family clamps BOTH an undershoot and a small
/// (<=2px) overshoot to the estimate, trusting only a real overshoot past
/// that.
fn render_first_vega_height(
    chart_json: &Value,
    _chart_id: &str,
    rows: &[serde_json::Map<String, Value>],
    datasets: &HashMap<String, Vec<serde_json::Map<String, Value>>>,
    width: f64,
    static_estimate: f64,
    ctx: &SizingCtx,
) -> Result<f64, String> {
    let chart_type =
        chart_json.get("chart_type").and_then(Value::as_str).ok_or("chart missing chart_type")?;
    // A family/shape this port refuses is not a sizing failure: the render
    // pass (`board::render_chart_item`) hits the SAME refusal on its own
    // `render_fitted` call and paints an error card into the slot instead
    // of failing the board (item b) — so sizing must produce SOME box for
    // that slot too, or the render pass never gets to run at all. Falling
    // back to the static estimate here is exactly the "undershoot" branch
    // `clamp_render_first_height` already takes for a real render that
    // comes back short — this chart just always takes it.
    let Some(actual) = rendered_vega_height(chart_json, rows, datasets, width, static_estimate, ctx)? else {
        return Ok(static_estimate);
    };
    Ok(clamp_render_first_height(chart_type, actual, static_estimate))
}

/// The height of `chart_json` rendered through the port's own families at
/// `(width, height)` — `_render_chart_to_svg`'s `extract_svg_height` — or
/// `None` when the port refuses the chart.
fn rendered_vega_height(
    chart_json: &Value,
    rows: &[serde_json::Map<String, Value>],
    datasets: &HashMap<String, Vec<serde_json::Map<String, Value>>>,
    width: f64,
    height: f64,
    ctx: &SizingCtx,
) -> Result<Option<f64>, String> {
    let padding = additive_padding(chart_json, ctx.card_padding)?;
    let payload = crate::Payload {
        cell_text: Vec::new(),
        max_template_output_bytes: None,
        resolved: chart_json.clone(),
        rows: rows.to_vec(),
        width,
        height,
        background: Some(ctx.chart_bg.to_string()),
        fonts_dir: ctx.fonts_dir.clone(),
        padding: Some(padding),
        board_style: Some(ctx.board_style.clone()),
        language: None,
        datasets: Some(datasets.clone()),
        // This IS production's own "static estimate, before any layout
        // decision" measurement (`layout_sizing.py`'s `item.height == 0`
        // branch) — the FIRST of two call sites `Payload::measuring`
        // exists for; the second is a cols-row chart's fresh re-render
        // (`calculate_cols_boxes`'s own `fresh_render` set, `board.rs::
        // render_chart_item`).
        variables: Default::default(),
        filter_variables: Default::default(),
        interactive_controls: Default::default(),
        link_context: None,
        measuring: true,
        title_shift: crate::TitleShift::default(),
        is_placeholder: false,
        authored_height: None,
    };
    // `render_width_fitted`, not bare `render` and not the full
    // `render_fitted`: a support_table chart's outer canvas must already be
    // WIDTH-corrected before this reads its height back (production applies
    // that same correction inline in its own render-first sizing pass), but
    // the HEIGHT correction must NOT run here — production defers it to the
    // final render, after the box this height feeds has already been
    // decided (`layout_sizing.py::_correct_support_table_height`, called from
    // `_align_cols_heights` and `_fix_slot_heights_in_tree`; see
    // `render_fitted`'s own doc).
    //
    let Ok((svg, _)) = crate::render_width_fitted(&payload, ctx.fonts) else {
        return Ok(None);
    };
    crate::board::extract_svg_height(&svg).map(Some)
}

/// The clamp/trust split itself, isolated from the render call so it's
/// unit-testable without a real chart render (`layout_sizing.py`'s `provider()`).
pub(crate) fn clamp_render_first_height(chart_type: &str, actual: f64, static_estimate: f64) -> f64 {
    if FIXED_ASPECT_PIE_TYPES.contains(&chart_type) {
        if (0.0..=2.0).contains(&(actual - static_estimate)) {
            return static_estimate;
        }
        return actual;
    }
    if actual - static_estimate <= 2.0 {
        return static_estimate;
    }
    actual
}

/// layout_sizing.py::_make_data_aware_height_provider's spark_bar branch: its own unconditional
/// natural-height render (production's `_render_chart_to_svg(resolved_spark_bar, executor,
/// variables, width, height=None, resolved_style=resolved_style)`, no `padding=` kwarg — unlike
/// the Vega render-first branch above, which passes `additive_padding(card_pad, chart_padding)`),
/// read back via `extract_svg_height`, then `_vertical_inset` added on top verbatim (`return
/// actual_height + _vertical_inset(resolved_spark_bar)`), no clamp. `payload.height = 0.0`
/// reproduces `height=None` on the Rust side: `spark_bar.rs::render` only trusts `payload.height`
/// when it is `> 0.0`, falling through to its own `title_height + num_bars * row_height +
/// bar.padding` content-first formula otherwise — the same natural-render path production's
/// `height=None` reaches.
fn spark_bar_natural_height(
    chart_json: &Value,
    _chart_id: &str,
    rows: &[serde_json::Map<String, Value>],
    width: f64,
    inset: f64,
    ctx: &SizingCtx,
) -> Result<f64, String> {
    let payload = crate::Payload {
        cell_text: Vec::new(),
        max_template_output_bytes: None,
        resolved: chart_json.clone(),
        rows: rows.to_vec(),
        width,
        height: 0.0,
        background: Some(ctx.chart_bg.to_string()),
        fonts_dir: ctx.fonts_dir.clone(),
        padding: None,
        board_style: Some(ctx.board_style.clone()),
        language: None,
        datasets: None,
        // spark_bar has no rail-concat law `measuring` gates — inert here.
        variables: Default::default(),
        filter_variables: Default::default(),
        interactive_controls: Default::default(),
        link_context: None,
        measuring: false,
        title_shift: crate::TitleShift::default(),
        is_placeholder: false,
        authored_height: None,
    };
    // Same reasoning as `render_first_vega_height`'s own fallback above: a
    // spark_bar shape this port refuses must not fail sizing, since the
    // render pass still needs a box to paint its own error card into. Unlike
    // that Vega branch, the chart resolved on THIS path (`chart_json["title"]`
    // is in hand), so the fallback is production's own `_unresolved_height`
    // law (`layout_sizing.py`'s `_unresolved_height`) — the same static content estimate
    // `get_chart_content_height`'s spark_bar branch already computes for the
    // `chart_error` path — never the invented zero an earlier version of
    // this fallback returned.
    let Ok(svg) = crate::render_fitted(&payload, ctx.fonts) else {
        let title = chart_json.get("title").and_then(Value::as_str).unwrap_or("");
        return unresolved_height(
            "spark_bar",
            None,
            None,
            None,
            None,
            Some(!title.is_empty()),
            width,
            ctx.card_padding,
            ctx.defaults,
        );
    };
    let actual = crate::board::extract_svg_height(&svg.svg)?;
    Ok(actual + inset)
}


/// layout_sizing.py::_make_data_aware_height_provider's width correction — the
/// floor/shrink arithmetic for a support_table chart whose first render's outer
/// width overshoots the requested slot (autosize:pad adds axis-label/legend overhead beyond
/// spec.width). Isolated from the render calls so it's unit-testable
/// without a real chart render, same pattern as `clamp_render_first_height`.
/// Returns `Ok(None)` when there's no overhead to correct, `Ok(Some(shrunk_width))`
/// when a correction is needed, `Err` when a horizontal bar's corrected width
/// would fall below the readability floor (the same `provider()` block).
pub(crate) fn support_table_width_correction(
    chart_id: &str,
    chart_type: &str,
    orientation: Option<&str>,
    width: f64,
    actual_width: f64,
    plot_width_floor_ratio: f64,
) -> Result<Option<f64>, String> {
    let overhead = actual_width - width;
    if overhead <= 0.0 {
        return Ok(None);
    }
    let would_be_width = width - overhead;
    // Gated on the same category-axis-vertical condition
    // apply_chart_support_table_post_pass uses to pick the column path — a
    // top/bottom row strip reserves no width at all, so this floor is meant
    // only for the column block a horizontal bar's plot competes with.
    let category_axis_vertical = chart_type == "bar" && orientation == Some("horizontal");
    let floor_px = width * plot_width_floor_ratio;
    if category_axis_vertical && would_be_width < floor_px {
        return Err(format!(
            "chart {chart_id:?}: attaching support_table leaves the plot an \
             estimated {would_be_width:.0}px wide on a {width:.0}px card, below \
             the {floor_px:.0}px floor it needs to stay readable. A plot this \
             narrow is a missing chart, not a squeezed one, and would paint \
             values beside nothing. Drop a support_table column, widen the \
             card, or move the block to `position: right`."
        ));
    }
    Ok(Some(would_be_width.max(1.0)))
}

/// layout_sizing.py::_correct_support_table_height: a
/// support_table chart's `autosize:pad` canvas can overshoot its target slot
/// height the same way `support_table_width_correction` overshoots width, and
/// production re-renders once at `target - overhead` to land back on target.
/// The title offset is not part of this correction: it is a closed-form term
/// on `plot_top` itself
/// (`support_table_strip::support_table_title_offset_correction`).
/// Isolated from the render call so it's unit-testable without a real chart
/// render, same pattern as `support_table_width_correction`. Returns `None`
/// when there's no overhead to correct.
pub(crate) fn support_table_height_correction(target: f64, actual_height: f64) -> Option<f64> {
    let overhead = actual_height - target;
    if overhead <= 0.0 {
        return None;
    }
    Some((target - overhead).max(1.0))
}

/// layout_sizing.py's `_get_table_height_from_data`: the height the table
/// draws at the slot's inner width with nothing bounding it, `None` for a
/// table the engine cannot draw (the caller sizes it as any chart whose
/// error card the main pass paints).
fn get_table_height_from_data(
    chart_json: &Value,
    rows: &[serde_json::Map<String, Value>],
    width: f64,
    card_padding: f64,
    ctx: &SizingCtx,
) -> Result<Option<f64>, String> {
    let padding = additive_padding(chart_json, card_padding)?;
    let payload = crate::Payload {
        cell_text: Vec::new(),
        max_template_output_bytes: None,
        resolved: chart_json.clone(),
        rows: rows.to_vec(),
        width: (width - padding.left - padding.right).max(0.0),
        height: 0.0,
        background: Some(ctx.chart_bg.to_string()),
        fonts_dir: ctx.fonts_dir.clone(),
        padding: None,
        board_style: Some(ctx.board_style.clone()),
        language: None,
        datasets: None,
        // table has no rail-concat law `measuring` gates — inert here.
        variables: Default::default(),
        filter_variables: Default::default(),
        interactive_controls: Default::default(),
        link_context: None,
        measuring: false,
        title_shift: crate::TitleShift::default(),
        is_placeholder: false,
        authored_height: None,
    };
    Ok(crate::table_natural_height(&payload, ctx.fonts).ok())
}

/// layout_sizing.py's `static_estimate`: the FULL slot height (content +
/// inset) of a Vega-family chart before any render, because it doubles as the
/// render's own `height` arg (slot_height) AND the clamp baseline; the
/// render-first result is returned as-is (`provider()`'s bare `return
/// actual_height`), never re-added to inset.
pub fn static_slot_estimate(
    chart_json: &Value,
    authored_height: Option<f64>,
    width: f64,
    card_padding: f64,
    defaults: &ChartDefaultsDoc,
) -> Result<f64, String> {
    let chart_type =
        chart_json.get("chart_type").and_then(Value::as_str).ok_or("chart missing chart_type")?;
    let (padding_top, padding_bottom) = layout_padding_vertical(chart_json)?;
    let inset = vertical_inset(card_padding, padding_top, padding_bottom);
    let aspect_ratio = chart_json.get("aspect_ratio").and_then(Value::as_f64);
    let min_height = chart_json.get("min_height").and_then(Value::as_f64);
    let max_height = chart_json.get("max_height").and_then(Value::as_f64);
    let content_estimate = get_chart_content_height(
        chart_type,
        authored_height,
        aspect_ratio,
        min_height,
        max_height,
        Some(width),
        None,
        defaults,
    )?;
    Ok(content_estimate + inset)
}

/// layout_sizing.py::_make_data_aware_height_provider's `provider()`
/// closure: table's unbounded render (unconditional), then callout's own
/// natural-height render (no dataset needed — a callout's SVG never reads
/// query rows), then kpi's static estimate, then spark_bar's own
/// natural-height render (needs its dataset, so it dispatches after the
/// `chart_datasets` lookup below), then render-first for every other Vega
/// family (bar/line/area/scatter/pie/heatmap — the branch gated on
/// `render_ctx.executor is not None`, unconditionally true here since the
/// port always has a renderer), then the static aspect-ratio fallback kpi
/// uses (`NON_ASPECT_RATIO_TYPES`).
pub fn resolve_chart_item_height(
    chart_json: &Value,
    authored_height: Option<f64>,
    width: f64,
    card_padding: f64,
    ctx: &SizingCtx,
) -> Result<f64, String> {
    let chart_type =
        chart_json.get("chart_type").and_then(Value::as_str).ok_or("chart missing chart_type")?;
    let (padding_top, padding_bottom) = layout_padding_vertical(chart_json)?;
    let inset = vertical_inset(card_padding, padding_top, padding_bottom);

    // `_render_chart_to_svg(resolved_callout, ..., height=None, ...)`
    // (layout_sizing.py's callout branch): the family's natural height
    // verbatim, no `+ inset` — a callout's padding is baked inside its own
    // card, not a layout-level inset (`SVG_LAYOUT_PADDED_TYPES`, same as
    // kpi/table/spark_bar).
    if chart_type == "callout" {
        let payload = crate::Payload {
            cell_text: Vec::new(),
            max_template_output_bytes: None,
            resolved: chart_json.clone(),
            rows: Vec::new(),
            width,
            height: 0.0,
            background: Some(ctx.chart_bg.to_string()),
            fonts_dir: ctx.fonts_dir.clone(),
            padding: None,
            board_style: Some(ctx.board_style.clone()),
            language: None,
            datasets: None,
            // callout has no rail-concat law `measuring` gates — inert here.
            variables: Default::default(),
            filter_variables: Default::default(),
            interactive_controls: Default::default(),
            link_context: None,
            measuring: false,
            title_shift: crate::TitleShift::default(),
            is_placeholder: false,
            authored_height: None,
        };
        let (_, natural_height) = crate::families::callout::render(&payload, ctx.fonts)
            .map_err(|e| format!("chart refused during sizing: {e}"))?;
        return Ok(natural_height);
    }
    if chart_type == "kpi" {
        let content = get_chart_content_height(
            chart_type,
            authored_height,
            None,
            None,
            None,
            Some(width),
            None,
            ctx.defaults,
        )?;
        return Ok(content + inset);
    }

    let chart_id = chart_json.get("id").and_then(Value::as_str).ok_or("chart missing id")?;
    let query_name =
        chart_json.get("query_name").and_then(Value::as_str).ok_or("chart missing query_name")?;
    let datasets = ctx
        .chart_datasets
        .get(chart_id)
        .ok_or_else(|| format!("no datasets forwarded for chart {chart_id}"))?;
    let rows = datasets
        .get(query_name)
        .ok_or_else(|| format!("chart {chart_id}: dataset {query_name} not forwarded"))?;

    if chart_type == "table" {
        // A table the engine cannot draw is sized as any chart the main pass
        // draws an error card for (`_unresolved_height`).
        return match get_table_height_from_data(chart_json, rows, width, card_padding, ctx)? {
            Some(height) => Ok(height + inset),
            None => unresolved_height("table", None, None, None, None, None, width, card_padding, ctx.defaults),
        };
    }
    if chart_type == "spark_bar" {
        return spark_bar_natural_height(chart_json, chart_id, rows, width, inset, ctx);
    }

    let static_estimate = static_slot_estimate(chart_json, authored_height, width, card_padding, ctx.defaults)?;
    render_first_vega_height(
        chart_json,
        chart_id,
        rows,
        datasets,
        width,
        static_estimate,
        ctx,
    )
}

// ===========================================================================
// Item boxes: layout-type dimension assignment
// ===========================================================================

#[derive(Debug, Clone, Copy, Default)]
pub struct ItemBox {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// Per-item authoring facts `ResolvedLayoutItem` does not preserve (the
/// STATIC resolved board carries only compile-time-independent shape) —
/// mirrors `LayoutItem`'s own grid/authoring fields
/// (compile/models/board/normalized.py), forwarded by board_sweep.py as a
/// harness fact alongside the resolved chart JSON.
#[derive(Debug, Clone, Default)]
pub struct ItemAuthoring {
    pub user_width: Option<String>,
    pub layout_height: Option<String>,
    pub row: Option<i64>,
    pub col: Option<i64>,
    pub row_span: Option<i64>,
    pub col_span: Option<i64>,
    pub chart_height: Option<f64>,
    pub chart_width: Option<f64>,
    /// The COMPILED chart's own authored `aspect_ratio`/`min_height`/
    /// `max_height` — `unresolved_height`'s (HIGH 1(a)) only route to them
    /// for a `chart_error` item, which carries no resolved chart to read
    /// these off directly the way `resolve_chart_item_height` does.
    pub chart_aspect_ratio: Option<f64>,
    pub chart_min_height: Option<f64>,
    pub chart_max_height: Option<f64>,
}

/// render/sizing.py::_resolve_layout_height
fn resolve_layout_height(authoring: &ItemAuthoring, available: f64) -> Option<f64> {
    let h = parse_dimension(authoring.layout_height.as_deref(), available)?;
    if h > 0.0 {
        Some(h)
    } else {
        None
    }
}

/// `board_alignment_overrides`'s own closure signature — see
/// `SizingCtx::board_alignment_overrides`'s doc for what it computes.
type AlignmentOverridesFn<'a> = dyn Fn(&Value, f64, f64, f64) -> Result<HashMap<String, f64>, String> + 'a;

/// Bundles the sizing-pass constants every measurement/assignment function
/// below needs, mirroring `layout_sizing.SizingRenderCtx` — one struct
/// instead of five positional floats/refs threaded through every call.
pub struct SizingCtx<'a> {
    pub gap: f64,
    pub card_gap: f64,
    pub card_padding: f64,
    pub chart_datasets: &'a ChartDatasets,
    pub defaults: &'a ChartDefaultsDoc,
    /// Render-first inputs — everything `render_first_vega_height` needs to
    /// call the port's own `render()` the way `_render_chart_to_svg`
    /// (`layout_sizing.py::_render_chart_to_svg`) calls `render_resolved_chart` during
    /// sizing: the board-style envelope every chart `Payload` carries, the
    /// chart's own background (`resolved_style.background`, the same value
    /// the layout bg rect paints), the fonts directory, and the loaded
    /// `FontBook` itself.
    pub board_style: &'a crate::BoardStyle,
    pub chart_bg: &'a str,
    pub fonts_dir: Option<String>,
    pub fonts: &'a crate::text::FontBook,
    /// A nested `type: board` item's own total content height at a given
    /// candidate width — `render/sizing.py::get_item_content_height`'s board
    /// branch (`nested_board_sizing_context` + a recursive `calculate_
    /// layout_height`), which needs mdsvg title/text measurement (`title.rs`/
    /// `prose.rs`) this module has no reason to depend on. `board.rs` binds
    /// the closure once per render and threads it down through every nested
    /// level — the same `card_gap`/`payload`/`fonts` triple every call in the
    /// tree needs, captured once instead of re-plumbed as positional args.
    pub nested_board_height: &'a dyn Fn(&Value, f64) -> Result<f64, String>,
    /// `layout_sizing.py::_col_chart_body_target`'s board branch: a nested `type: board` item's own
    /// contribution to a cols row's `chart_target` — `item.height` (empirically the row's own
    /// `row_height`, the third argument here — see `board.rs`'s own citation for why that
    /// equality holds) when every sub-item is a Vega chart in a `rows` layout, else the max of
    /// its DIRECT sub-items' own natural heights. Bound in `board.rs` (needs
    /// `nested_board_item_boxes`, which needs font measurement this module has no reason to
    /// depend on).
    pub board_chart_target_contribution: &'a dyn Fn(&Value, f64, f64) -> Result<f64, String>,
    /// `layout_sizing.py::_align_board_charts`, run once a cols row's own
    /// `chart_target` is known: the flat `chart_id -> forced_height` map
    /// for every DIRECT Vega chart child of a nested board item the
    /// alignment condition (`cols` layout, or exactly one Vega chart)
    /// reaches — empty when it does not apply. Bound in `board.rs`
    /// alongside `board_chart_target_contribution` for the same reason.
    pub board_alignment_overrides: &'a AlignmentOverridesFn<'a>,
    /// The board's own `resolved_style.layout.tabs.bar_height` /
    /// `.details.summary_height` — theme constants `_calculate_tabs_
    /// dimensions`/`details_chrome_height`/`is_item_collapsed_summary` read.
    /// Threaded per `SizingCtx` build site (one per nesting level) since a
    /// nested board can theme-override its own `style.layout` independently
    /// of its parent, same as every other per-level style field here.
    pub tabs_bar_height: f64,
    pub details_summary_height: f64,
    /// `variable_values` — the SAME raw request-level overrides dict at
    /// EVERY nesting level (`render_nested_board`'s own `variables`
    /// parameter is the caller's, never re-merged per level — the same
    /// citation `BoardPayload.variable_values`'s own doc carries). Needed
    /// only for `_is_details_item_expanded`'s "live variable value" branch;
    /// its `board.meta` fallback is not ported (this port carries no
    /// per-board `meta` field) and refuses by name instead of guessing.
    pub variable_values: &'a serde_json::Map<String, Value>,
}

/// A CHEAP raw-JSON gate for a refusal site that cannot afford a full
/// resolved-chart deserialize. It calls `families::line::shape` itself
/// rather than re-deriving the decision, reading the IDENTICAL field path
/// `render_line_panel` does (`color.is_some() && style.endpoint_labels.
/// visible`).
fn railconcat_eligible_json(chart: &Value) -> bool {
    let chart_type = chart.get("chart_type").and_then(Value::as_str);
    if !matches!(chart_type, Some("line") | Some("area")) {
        return false;
    }
    let is_multi = chart.get("color").is_some_and(|c| !c.is_null());
    let show_rail = is_multi
        && chart
            .get("style")
            .and_then(|s| s.get("endpoint_labels"))
            .and_then(|e| e.get("visible"))
            .and_then(Value::as_bool)
            .unwrap_or(false);
    crate::families::line::shape(show_rail) == crate::families::line::Shape::RailConcat
}

/// One layout item's resolved height, at a given width — the single entry
/// point every measurement/assignment pass below calls, mirroring
/// `_resolve_height`'s dispatch (render/sizing.py:167-189) minus the
/// height_provider indirection (this module has exactly one provider).
/// `render/chart/rendering.py:116`'s details gate applies to EITHER item
/// kind — checked before the kind dispatch, same ordering `board.rs`'s own
/// render-side check already uses.
fn item_height(item: &LayoutItemInput, ctx: &SizingCtx, width: f64) -> Result<f64, String> {
    match item.kind {
        "chart" => {
            // `get_item_content_height`'s "chart" branch never reads
            // details fields at all — production's own details gate for a
            // chart item fires only on the RENDER side (`chart/rendering.
            // py:116`, either item kind). Refusing here instead reaches
            // the SAME final outcome (the whole board refuses on this
            // shape) one step earlier, since a sizing refusal composition
            // never reaches render anyway.
            if item.details_present {
                return Err("board details section not modeled".to_string());
            }
            let chart_json = match item.chart {
                Some(c) => c,
                // Item (b): a chart that never resolved no longer fails
                // sizing (or, by extension, the whole board) — the render
                // pass (`board::render_chart_item`) paints an error card
                // into this slot instead. There is no RESOLVED chart here
                // to size the way `resolve_chart_item_height` does, but the
                // COMPILED chart still exists (production's own `_unresolved_
                // height`, HIGH 1(a)) — `chart_error.identity.chart_type`
                // plus the authored facts `board_sweep.py` now forwards
                // alongside it (`ItemAuthoring::chart_aspect_ratio`/
                // `chart_min_height`/`chart_max_height`) are exactly what
                // `unresolved_height` needs to size the same box production
                // would for this placement.
                None if item.chart_error.is_some() => {
                    let failure = item
                        .chart_error
                        .expect("checked by the guard above");
                    let chart_type = failure
                        .get("identity")
                        .and_then(|i| i.get("chart_type"))
                        .and_then(Value::as_str)
                        .ok_or("chart_error missing identity.chart_type")?;
                    return unresolved_height(
                        chart_type,
                        item.authoring.chart_height,
                        item.authoring.chart_aspect_ratio,
                        item.authoring.chart_min_height,
                        item.authoring.chart_max_height,
                        // No authored-title fact is forwarded for a
                        // chart_error item — see `get_chart_content_height`'s
                        // own doc for why spark_bar refuses on `None` here.
                        None,
                        width,
                        ctx.card_padding,
                        ctx.defaults,
                    );
                }
                None => return Err("chart item missing its resolved chart".to_string()),
            };
            if let Some(overridden) = item.chart_height_override {
                // A parent row's `_align_board_charts` already decided this
                // height; `forced_fresh_renders` recovers the unforced natural.
                return Ok(overridden);
            }
            resolve_chart_item_height(
                chart_json,
                item.authoring.chart_height,
                width,
                ctx.card_padding,
                ctx,
            )
        }
        "board" => {
            let board_json =
                item.board.ok_or("board item missing its resolved board")?;
            if item.details_present {
                let details_variable = item.details_variable.expect(
                    "details_present implies details_variable is Some (ItemDoc::details_present)",
                );
                if item_details_expanded(details_variable, ctx)? {
                    let content_h = (ctx.nested_board_height)(board_json, width)?;
                    return Ok(details_chrome_height(ctx) + content_h);
                }
                return Ok(ctx.details_summary_height);
            }
            (ctx.nested_board_height)(board_json, width)
        }
        other => Err(format!("board item type not modeled: {other}")),
    }
}

/// `render/sizing.py::is_item_collapsed_summary` + `_is_details_item_
/// expanded`'s "live variable value" branch — the ONLY branch reachable
/// on this port: `variable_values` is always populated (see `SizingCtx.
/// variable_values`'s own doc), so the `board.meta` fallback branch
/// production falls to when the details variable is absent from
/// `variables` is unreachable on a real render and refuses by name rather
/// than being approximated with no `meta` field to read.
fn item_details_expanded(details_variable: &str, ctx: &SizingCtx) -> Result<bool, String> {
    match ctx.variable_values.get(details_variable) {
        Some(Value::Bool(b)) => Ok(*b),
        Some(Value::String(s)) => Ok(s.eq_ignore_ascii_case("true")),
        None => Err("board details expanded-state fallback (board.meta) not ported".to_string()),
        Some(other) => Err(format!(
            "board details expanded-state variable of type {other:?} not modeled"
        )),
    }
}

/// `render/sizing.py::details_chrome_height`: the vertical overhead the
/// details chrome (summary bar) adds above an EXPANDED nested board's own
/// content — `summary_height + gap + card_gap`, using the CALLING layout's
/// own `gap`/`card_gap` (already `ctx.gap`/`ctx.card_gap` at this
/// recursion level), matching production's own per-call-site parameters.
fn details_chrome_height(ctx: &SizingCtx) -> f64 {
    ctx.details_summary_height + ctx.gap + ctx.card_gap
}

/// render/sizing.py::_clamp_to_authored_ceiling
fn clamp_to_authored_ceiling(authored: f64, content: f64) -> f64 {
    if content > 0.0 {
        authored.min(content)
    } else {
        authored
    }
}

/// One layout item's chart-only facts, gathered once per item so every
/// measurement/assignment pass below reads the same tuple.
pub struct LayoutItemInput<'a> {
    pub kind: &'a str,
    pub chart: Option<&'a Value>,
    /// `ResolvedLayoutItem.chart_error` — set instead of `chart` when the
    /// chart failed to RESOLVE (a data/config error at the chart's own
    /// cascade, not a rendering one). A real board's compile-time `Chart`
    /// object is present either way, so production's own sizing pass
    /// degrades to `_unresolved_height` (HIGH 1(a), `unresolved_height`
    /// above) rather than failing — this is the raw `ChartResolveFailure`
    /// JSON (`identity`/`diagnostic`) that degrade reads its `chart_type`
    /// from.
    pub chart_error: Option<&'a Value>,
    pub authoring: &'a ItemAuthoring,
    /// `ResolvedLayoutItem.board` — the nested board's own resolved JSON,
    /// `Some` only when `kind == "board"`.
    pub board: Option<&'a Value>,
    /// `item.details_variable and item.details_summary` both set
    /// (`render/chart/rendering.py:116`'s gate) — a board details section is
    /// refused unconditionally this rung, for either item kind.
    pub details_present: bool,
    /// `ResolvedLayoutItem.details_variable` — `Some` exactly when
    /// `details_present` is true (`ItemDoc::details_present`'s own
    /// both-fields gate); read by `item_details_expanded`.
    pub details_variable: Option<&'a str>,
    /// `layout_sizing.py::_align_board_charts`'s own `sub.height = ...`
    /// write, applied upstream (`board.rs::board_alignment_overrides`) —
    /// `Some` only for a DIRECT Vega chart child of a nested board that
    /// sits in a parent `cols` row and keeps the written height: a `rows`
    /// (or `grid`) layout with exactly one Vega chart. A nested
    /// `cols` layout never gets one (its own row alignment rewrites it).
    /// `item_height` returns this verbatim, bypassing the natural-height
    /// computation entirely, the same direct assignment production makes.
    pub chart_height_override: Option<f64>,
}

/// Chart ids a parent row's alignment forced (`chart_height_override`) to a
/// height at least a pixel off their unforced natural: production re-renders
/// them (`_align_board_charts` -> `_align_cols_heights`, `layout_sizing.py:1354`),
/// so the paint is a fresh `measuring` render. The set belongs to this one
/// nested layout; a chart id shared with a direct chart elsewhere is not
/// affected. Each forced chart costs one extra `item_height` evaluation (a
/// sizing-probe render) to recover its unforced natural.
pub fn forced_fresh_renders(
    items: &[LayoutItemInput],
    boxes: &[ItemBox],
    ctx: &SizingCtx,
) -> Result<HashSet<String>, String> {
    let mut fresh = HashSet::new();
    for (item, item_box) in items.iter().zip(boxes) {
        let (Some(chart), Some(_)) = (item.chart, item.chart_height_override) else {
            continue;
        };
        let Some(id) = chart.get("id").and_then(Value::as_str) else {
            continue;
        };
        let unforced = LayoutItemInput { chart_height_override: None, ..*item };
        if height_moved(item_height(&unforced, ctx, item_box.width)?, item_box.height) {
            fresh.insert(id.to_string());
        }
    }
    Ok(fresh)
}

/// A nested `grid` layout holding a rail-concat chart that a parent cols row
/// realigns would need the fresh render `forced_fresh_renders` models, which
/// is only pinned against production for a nested `rows` layout; refuse by
/// name instead of painting the chart unrefreshed. A tabs layout holds only
/// nested boards, never a direct chart, so it needs no such gate.
pub fn refuse_forced_rail_charts(items: &[LayoutItemInput]) -> Result<(), String> {
    for item in items {
        if let (Some(chart), Some(_)) = (item.chart, item.chart_height_override) {
            if railconcat_eligible_json(chart) {
                return Err(
                    "sizing: a rail-concat chart realigned by a parent cols row inside a \
                     nested grid board is not modeled (only a nested rows board is)"
                        .to_string(),
                );
            }
        }
    }
    Ok(())
}

/// render/sizing.py::_calculate_rows_dimensions, ported as its own two-pass
/// function (first pass: per-item specified/content heights and their
/// totals; second pass: box assignment) — NOT fused with the measurement
/// pass the way an earlier version of this function was. The fusion
/// silently dropped `_calculate_rows_dimensions`'s own overflow-scaling
/// branch (`remaining < auto_total`: every auto item shrinks by the SAME
/// ratio to fit `available_height`), reachable whenever `available_height`
/// comes in a hair short of an auto item's own natural content height —
/// exactly what `boxes_for_layout`'s real (no longer `INFINITY`-clamped)
/// `available_height` for a nested tabs layout can now produce one level
/// down (`ladder/boards/tabs/05-tabs-nested-in-rows-with-title`: oracle
/// `431.99999999999994` vs the fused version's unscaled `432.0`).
pub fn calculate_rows_boxes(
    items: &[LayoutItemInput],
    available_width: f64,
    available_height: f64,
    ctx: &SizingCtx,
) -> Result<Vec<ItemBox>, String> {
    let n = items.len();
    if n == 0 {
        return Ok(Vec::new());
    }
    let total_gap = (ctx.gap + ctx.card_gap) * (n - 1) as f64;
    let available_content_height = available_height - total_gap;

    let item_widths: Vec<f64> = items
        .iter()
        .map(|item| {
            rows_item_width(
                item.authoring.user_width.as_deref(),
                item.authoring.chart_width,
                available_width,
            )
        })
        .collect();

    let mut specified_heights: Vec<Option<f64>> = Vec::with_capacity(n);
    let mut content_heights: Vec<f64> = Vec::with_capacity(n);
    let mut total_specified = 0.0;
    for (i, item) in items.iter().enumerate() {
        match resolve_layout_height(item.authoring, available_content_height) {
            Some(authored) => {
                let content = item_height(item, ctx, item_widths[i])?;
                let ceiling = clamp_to_authored_ceiling(authored, content);
                specified_heights.push(Some(ceiling));
                total_specified += ceiling;
                content_heights.push(ceiling);
            }
            None => {
                specified_heights.push(None);
                content_heights.push(item_height(item, ctx, item_widths[i])?);
            }
        }
    }

    let remaining = (available_content_height - total_specified).max(0.0);
    let auto_total: f64 = content_heights
        .iter()
        .zip(&specified_heights)
        .filter(|(_, s)| s.is_none())
        .map(|(h, _)| *h)
        .sum();

    let mut boxes = Vec::with_capacity(n);
    let mut current_y = 0.0;
    for i in 0..n {
        let height = match specified_heights[i] {
            Some(h) => h,
            None if auto_total > 0.0 && remaining < auto_total => {
                content_heights[i] * (remaining / auto_total)
            }
            None => content_heights[i],
        };
        boxes.push(ItemBox {
            x: 0.0,
            y: current_y,
            width: item_widths[i],
            height,
        });
        current_y += height + ctx.gap + ctx.card_gap;
    }
    Ok(boxes)
}

/// `render/sizing.py::_measure_rows_layout_height`, ported as its OWN
/// function rather than derived from `calculate_rows_boxes`'s assigned
/// boxes — `boxes.last().y + boxes.last().height` sums the SAME per-item
/// heights production's own measure loop does, but in a different
/// floating-point grouping: `calculate_rows_boxes`'s running `current_y`
/// interleaves `height + gap + card_gap` once per item (matching
/// production's own SECOND, assignment pass, `_calculate_rows_dimensions`'s
/// `current_y += item.height + gap + card_gap` — bit-identical there),
/// while `_measure_rows_layout_height` sums every item's height FIRST in
/// its own loop, then adds `(gap + card_gap) * (n - 1)` ONCE at the very
/// end — one multiplication, one final addition, not `n - 1` repeated
/// additions of the same gap term. Algebraically equal, not bit-identical:
/// a rows layout with many items accumulates rounding drift between the
/// two groupings (`playground/time-unit-label-cadence-matrix`'s container-
/// height `box_diff`, oracle `5784.349999999999` vs the derived-from-boxes
/// value `5784.35`). This function reproduces the MEASURE grouping exactly
/// so a caller needing "this rows layout's own total height" (not its
/// items' individual boxes) gets production's own bits, not the assign
/// pass's differently-grouped equivalent.
pub fn measure_rows_layout_height(
    items: &[LayoutItemInput],
    available_width: f64,
    ctx: &SizingCtx,
) -> Result<f64, String> {
    let mut total_height = 0.0;
    for item in items {
        let width = rows_item_width(
            item.authoring.user_width.as_deref(),
            item.authoring.chart_width,
            available_width,
        );
        let content = item_height(item, ctx, width)?;
        let height = match resolve_layout_height(item.authoring, 0.0) {
            Some(authored) => clamp_to_authored_ceiling(authored, content),
            None => content,
        };
        total_height += height;
    }
    if items.len() > 1 {
        total_height += (ctx.gap + ctx.card_gap) * (items.len() - 1) as f64;
    }
    Ok(total_height)
}

/// render/sizing.py::resolve_active_tab_index: a variable override wins,
/// else the authored default, else the first tab — clamped so an
/// out-of-range default never indexes past the last item. Shared by the
/// sizing pass (below) and `board.rs`'s own render dispatch, mirroring
/// production's single source of truth for which tab is active.
pub fn resolve_active_tab_index(
    n_items: usize,
    tab_variable: Option<&str>,
    tab_slugs: &[String],
    default_tab: i64,
    variable_values: &serde_json::Map<String, Value>,
) -> Result<usize, String> {
    let mut active = default_tab.max(0) as usize;
    if let Some(var_name) = tab_variable {
        if !tab_slugs.is_empty() && !variable_values.is_empty() {
            if let Some(var_value) = variable_values.get(var_name) {
                match var_value {
                    Value::String(s) if !s.is_empty() => {
                        active = tab_slugs.iter().position(|slug| slug == s).ok_or_else(|| {
                            format!(
                                "ERR-TAB-UNKNOWN: tab variable {var_name:?} is {s:?}, \
                                 which is not a tab on this board (tabs: {})",
                                tab_slugs.join(", ")
                            )
                        })?;
                    }
                    // Falsy (empty string / null) — Python's own `if
                    // var_value` guard skips the override attempt entirely,
                    // same as an absent key.
                    Value::String(_) | Value::Null => {}
                    other => {
                        return Err(format!(
                            "board tab-active variable of type {other:?} not modeled"
                        ));
                    }
                }
            }
        }
    }
    Ok(active.min(n_items.saturating_sub(1)))
}

/// render/sizing.py::_calculate_tabs_dimensions: every item shares ONE
/// uniform box — the ACTIVE tab's own content height (measured at full
/// `available_width`, unlike a rows/cols item — a tab has no authored
/// width-fraction concept), min-clamped to `available_height - tab_bar_
/// height` with no floor at 0 (a literal port of production's own
/// arithmetic) — at `(x=0, y=tab_bar_height, width=available_width)`.
/// Every item gets assigned this SAME box even though only the active one
/// ever renders, mirroring production's own SIZED `ResolvedLayoutItem`
/// (`board_sweep.py`'s own `_oracle_layout_boxes` reads `item.x/y/width/
/// height` off EVERY item, not just the active one).
pub fn calculate_tabs_boxes(
    items: &[LayoutItemInput],
    available_width: f64,
    available_height: f64,
    tab_variable: Option<&str>,
    tab_slugs: &[String],
    default_tab: i64,
    ctx: &SizingCtx,
) -> Result<Vec<ItemBox>, String> {
    if items.is_empty() {
        return Ok(Vec::new());
    }
    let active_index = resolve_active_tab_index(
        items.len(),
        tab_variable,
        tab_slugs,
        default_tab,
        ctx.variable_values,
    )?;
    let active_content_h = item_height(&items[active_index], ctx, available_width)?;
    let content_height = active_content_h.min(available_height - ctx.tabs_bar_height);
    Ok(items
        .iter()
        .map(|_| ItemBox {
            x: 0.0,
            y: ctx.tabs_bar_height,
            width: available_width,
            height: content_height,
        })
        .collect())
}

/// layout_sizing.py::_col_chart_body_target: max natural chart
/// body height across a cols row's Vega items — a direct chart item reads
/// its PASS-1-fixed natural (see `calculate_cols_boxes`'s doc); a nested
/// `type: board` item delegates to `ctx.board_chart_target_contribution`
/// (bound in `board.rs`, ported alongside `_align_board_charts` — see its
/// own doc for why the two live together).
fn col_chart_body_target(
    items: &[LayoutItemInput],
    fixed_naturals: &[Option<f64>],
    resolved_widths: &[f64],
    row_height: f64,
    ctx: &SizingCtx,
) -> Result<f64, String> {
    let mut max_h = 0.0_f64;
    for (i, item) in items.iter().enumerate() {
        if let Some(chart) = item.chart {
            let Some(chart_type) = chart.get("chart_type").and_then(Value::as_str) else {
                continue;
            };
            if is_non_aspect_ratio_type(chart_type) {
                continue;
            }
            if let Some(h) = fixed_naturals[i] {
                max_h = max_h.max(h);
            }
        } else if let Some(board_json) = item.board {
            let contribution =
                (ctx.board_chart_target_contribution)(board_json, resolved_widths[i], row_height)?;
            max_h = max_h.max(contribution);
        }
    }
    Ok(max_h)
}

/// layout_sizing.py::_effective_alignment_target_for_chart: caps
/// a `_FIXED_ASPECT_PIE_TYPES` chart's growth at its own natural height —
/// every other family takes the target as-is.
pub(crate) fn effective_alignment_target_for_chart(
    chart_type: &str,
    natural_h: Option<f64>,
    target_height: f64,
) -> f64 {
    if let Some(natural) = natural_h {
        if FIXED_ASPECT_PIE_TYPES.contains(&chart_type) && target_height > natural {
            return natural;
        }
    }
    target_height
}

/// layout_sizing.py::_fix_slot_heights_in_tree, the cols-row
/// slice: once `_calculate_cols_dimensions` has assigned every item the
/// row's own height, a Vega item whose PRISTINE natural (measured while
/// `_col_chart_body_target` — see below) is still >= 1px away from that
/// row height gets its natural OVERWRITTEN to the row height itself (mirrors
/// production re-rendering at the slot height and caching that as the new
/// "natural"). Below the 1px band the natural is left untouched — this is
/// the skip band `_align_all_cols_in_tree`'s cols branch (below) can still
/// rewrite a box through.
///
/// Production's re-render (`_render_chart_to_svg(..., height=item.height)`)
/// takes whatever height comes back with NO clamp of its own — for a plain
/// Vega chart that equals `row_height` because the VL spec's own `height`
/// key is set to it and Vega respects it, which is why overwriting straight
/// to `row_height` is exact for every family EXCEPT one case: a
/// `_FIXED_ASPECT_PIE_TYPES` chart carrying an `attached_table`.
/// An attached-table pie ignores the slot `height`: it composes its own canvas from
/// `wheel_width x board_style.continuous_height` plus the attached table's
/// own row count (`pie.rs`'s `Some(attached)` arm), a
/// pure function of the chart's own content and width, never of the row's
/// assigned height. Re-rendering it at `row_height` is therefore a no-op —
/// production's real re-render returns the SAME composed height regardless
/// — so this pass must leave its already-correct `naturals[i]` (computed
/// once by `render_first_vega_height` at this same width) untouched rather
/// than force it to `row_height`. A non-attached pie/arc (direct-label
/// wheel) has no such carve-out: its VL spec's `height` key IS `row_height`
/// on re-render and Vega honors it, so it takes the general overwrite like
/// any other Vega family. `ladder/sizing/23-pie-attached-table-height-
/// independent-of-row` pins this, mutation-checked; `ladder/sizing/15`/`16`
/// (a plain, non-attached pie that DOES need the overwrite) stay green.
///
/// The overwrite is also inexact for a small-multiples chart: its composite
/// canvas is a step function of the height it is asked for (every ink term is
/// ceiled, and the overshoot correction divides across panels), so the
/// re-render comes back `actual_height` taller or shorter than `row_height`
/// and production records THAT as the natural. Such a chart is re-rendered
/// here, unclamped, and the row's alignment target reads its real canvas.
fn fix_slot_heights(
    items: &[LayoutItemInput],
    row_height: f64,
    naturals: &[Option<f64>],
    widths: &[f64],
    ctx: &SizingCtx,
) -> Result<Vec<Option<f64>>, String> {
    let mut fixed = naturals.to_vec();
    if row_height <= 0.0 {
        return Ok(fixed);
    }
    for (i, item) in items.iter().enumerate() {
        let Some(chart) = item.chart else { continue };
        let Some(chart_type) = chart.get("chart_type").and_then(Value::as_str) else {
            continue;
        };
        if is_non_aspect_ratio_type(chart_type) {
            continue;
        }
        if FIXED_ASPECT_PIE_TYPES.contains(&chart_type)
            && chart.get("attached_table").is_some_and(|v| !v.is_null())
        {
            continue;
        }
        let Some(natural) = naturals[i] else { continue };
        if !height_moved(natural, row_height) {
            continue;
        }
        fixed[i] = Some(if chart.get("multiples").is_some_and(|m| !m.is_null()) {
            facet_height_at(chart, widths[i], row_height, ctx)?
        } else {
            row_height
        });
    }
    Ok(fixed)
}

/// A small-multiples chart's real canvas height when rendered at `height`
/// (`_fix_slot_heights_in_tree`'s re-render); a chart this port refuses keeps
/// the slot height, like `render_first_vega_height`'s fallback.
fn facet_height_at(chart_json: &Value, width: f64, height: f64, ctx: &SizingCtx) -> Result<f64, String> {
    let chart_id = chart_json.get("id").and_then(Value::as_str).ok_or("chart missing id")?;
    let query_name =
        chart_json.get("query_name").and_then(Value::as_str).ok_or("chart missing query_name")?;
    let datasets = ctx
        .chart_datasets
        .get(chart_id)
        .ok_or_else(|| format!("no datasets forwarded for chart {chart_id}"))?;
    let rows = datasets
        .get(query_name)
        .ok_or_else(|| format!("chart {chart_id}: dataset {query_name} not forwarded"))?;
    Ok(rendered_vega_height(chart_json, rows, datasets, width, height, ctx)?.unwrap_or(height))
}

/// render/sizing.py::_measure_cols_layout_height +
/// render/sizing.py::_calculate_cols_dimensions, fused (see
/// `calculate_rows_boxes`'s doc for why measurement and assignment collapse
/// into one pass here) — PLUS layout_sizing.py::calculate_data_aware_
/// layout's two later tree-wide passes, ported at this row's own scope:
/// `_fix_slot_heights_in_tree` (`fix_slot_heights` above) then
/// `_align_all_cols_in_tree`'s cols branch (`col_chart_body_
/// target` + `effective_alignment_target_for_chart` above). Both read/write
/// `render_ctx.natural_heights` keyed by `(chart_id, width)` GLOBALLY across
/// the whole board tree in production; every real natural_heights read this
/// row's own pass 2 can reach was written by THIS row's own pass 1 or
/// measurement step (a chart cannot appear twice in one cols row), so a
/// local `Vec<Option<f64>>` scoped to `items` is the exact same data,
/// addressed by index instead of a global map.
///
/// `_align_all_cols_in_tree`'s OWN re-render call, `_align_cols_heights`
/// (`layout_sizing.py:1322-1425`), is ported for BOTH branches: `item.board is not None`
/// (`_align_board_charts`, via `ctx.board_alignment_overrides` — see that
/// function's own citation for why the write DOES reach the frozen tree)
/// AND the direct-chart-item branch, which re-renders a Vega item at the
/// alignment target to refresh `render_ctx.render_cache` — a `(chart_id,
/// width, height)` cache miss on the new height, not a reuse of the
/// sizing probe's cached SVG. `render_chart_item` (board.rs) has no such
/// cache to reconcile: it renders each chart exactly once, directly at
/// its final box height, so the port models the SAME fresh render as a
/// `fresh_render` marker (below) that flips `Payload::measuring` back to
/// `true` for that one paint — the rail-concat probe-wrap law then fires
/// on it exactly like it fired on the sizing probe. `docs/laws-board.md`'s
/// "Nested boards" section carries the full mechanism.
///
/// `_snapshot_authored_slot_heights` (`layout_sizing.py:1709-1743`), the step production runs
/// between these two passes, is not ported either: it only ever READS
/// `item.height`/`item.layout_height` into an output dict
/// (`authored_slot_heights`) whose only reader in `core/` is a WARNING
/// DETECTOR — `warnings/layout_min_exceeds_height.py:57`, reached through
/// `WarningContext` — not a render clamp; `renderer.py:629` is only the
/// pass-in site, and `renderer.py:225-231` says outright this is "warning
/// diagnostics only, not the board render path". It never mutates
/// `item.height`, `natural_heights`, or any value this function or its two
/// ported passes read, so it can never move a box — porting a Rust
/// analog would compute a value with no consumer, the same "captured but
/// not wired" shape docs/laws-board.md already documents for `_correct_
/// support_table_height`'s sibling gaps, not a box-differential risk.
/// A cols row's pristine measurement: every item's resolved width, its
/// natural height, and the row height they set.
struct ColsMeasure {
    widths: Vec<f64>,
    /// Pristine per-item naturals — `None` for an item with an authored
    /// LAYOUT height (never measured, mirrors `_calculate_cols_dimensions`
    /// skipping the height provider for that branch) or a non-Vega family
    /// `col_chart_body_target`/`fix_slot_heights` exclude anyway.
    naturals: Vec<Option<f64>>,
    row_height: f64,
}

fn measure_cols(
    items: &[LayoutItemInput],
    available_width: f64,
    available_height: f64,
    ctx: &SizingCtx,
) -> Result<ColsMeasure, String> {
    let n = items.len();
    let total_gap = (ctx.gap + ctx.card_gap) * (n as i64 - 1) as f64;
    let content_width = available_width - total_gap;
    let specified_widths: Vec<Option<f64>> = items
        .iter()
        .map(|item| parse_dimension(item.authoring.user_width.as_deref(), content_width))
        .collect();
    let widths = resolve_cols_widths(&specified_widths, content_width, n);

    let mut max_specified = 0.0_f64;
    let mut max_auto = 0.0_f64;
    let mut naturals: Vec<Option<f64>> = Vec::with_capacity(n);
    for (i, item) in items.iter().enumerate() {
        match resolve_layout_height(item.authoring, available_height) {
            Some(resolved) => {
                max_specified = max_specified.max(resolved);
                naturals.push(None);
            }
            None => {
                let h = item_height(item, ctx, widths[i])?;
                max_auto = max_auto.max(h);
                naturals.push(Some(h));
            }
        }
    }
    let max_content_height = max_specified.max(max_auto);
    let row_height = if max_specified >= max_auto {
        max_content_height
    } else {
        max_content_height.min(available_height)
    };
    Ok(ColsMeasure { widths, naturals, row_height })
}

/// render/sizing.py::_measure_cols_layout_height: the cols row's own height,
/// before `_fix_slot_heights_in_tree`/`_align_all_cols_in_tree` move a chart
/// past it. A small-multiples chart re-rendered at the row height can come
/// back taller, and the aligned chart boxes follow it while the row keeps the
/// measured height — so a container reads this, not its boxes' tallest.
pub fn measure_cols_layout_height(
    items: &[LayoutItemInput],
    available_width: f64,
    available_height: f64,
    ctx: &SizingCtx,
) -> Result<f64, String> {
    if items.is_empty() {
        return Ok(0.0);
    }
    Ok(measure_cols(items, available_width, available_height, ctx)?.row_height)
}

pub fn calculate_cols_boxes(
    items: &[LayoutItemInput],
    available_width: f64,
    available_height: f64,
    ctx: &SizingCtx,
) -> Result<(Vec<ItemBox>, HashMap<String, f64>, HashSet<String>), String> {
    let n = items.len();
    if n == 0 {
        return Ok((Vec::new(), HashMap::new(), HashSet::new()));
    }
    let effective_gap = ctx.gap + ctx.card_gap;
    let ColsMeasure { widths: resolved_widths, naturals, row_height } =
        measure_cols(items, available_width, available_height, ctx)?;

    let fixed_naturals = fix_slot_heights(items, row_height, &naturals, &resolved_widths, ctx)?;
    let chart_target =
        col_chart_body_target(items, &fixed_naturals, &resolved_widths, row_height, ctx)?;

    let mut item_heights = vec![row_height; n];
    // `layout_sizing.py::_align_all_cols_in_tree`'s cols branch:
    // the direct-chart-item rewrite above, THEN `_align_cols_
    // heights`'s own board-item branch (`_align_board_charts`) — both
    // gated on the SAME `if chart_target > 0:`.
    let mut overrides: HashMap<String, f64> = HashMap::new();
    // `layout_sizing.py::_align_cols_heights`'s direct-chart branch:
    // a chart whose PRISTINE natural (`naturals[i]`, the
    // sizing probe's own measured height) isn't already this row's
    // assigned height gets a FRESH `_render_chart_to_svg(...,
    // height=item.height)` — a cache miss (`render_cache` keys on
    // `(chart_id, width, height)`, `board.rs::render_chart_item`'s own
    // doc), not a reuse of the sizing probe's cached SVG. `fresh_render`
    // is a SEPARATE set from `overrides` on purpose: `overrides` already
    // means "chart id -> forced height for a nested board's OWN sizing"
    // (`board_alignment_overrides`, below), keyed by chart id alone — the
    // corpus places one chart in several slots, so a direct chart here
    // sharing an id with a chart inside a nested board placed in the SAME
    // row would collide in a single shared map (the direct insert and the
    // nested `extend` overwriting each other by item order, and a nested
    // board's own alignment wrongly flipping an unrelated direct chart's
    // `measuring` to `true` — a real double-growth risk for a rail chart
    // with disagreeing wraps). `render_chart_item` treats a chart id
    // present in `fresh_render` as getting this SAME second real render
    // production performs, so the RailConcat probe-wrap law (`Payload::
    // measuring`) fires on it exactly like it fired on the first
    // (`render_first_vega_height`'s own) probe — not a reuse of the
    // already-decided slot render.
    let mut fresh_render: HashSet<String> = HashSet::new();
    if chart_target > 0.0 {
        for (i, item) in items.iter().enumerate() {
            if let Some(chart) = item.chart {
                let Some(chart_type) = chart.get("chart_type").and_then(Value::as_str) else {
                    continue;
                };
                if is_non_aspect_ratio_type(chart_type) {
                    continue;
                }
                item_heights[i] = effective_alignment_target_for_chart(
                    chart_type,
                    fixed_naturals[i],
                    chart_target,
                );
                if let (Some(natural), Some(id)) =
                    (naturals[i], chart.get("id").and_then(Value::as_str))
                {
                    if height_moved(natural, item_heights[i]) {
                        fresh_render.insert(id.to_string());
                    }
                }
            } else if let Some(board_json) = item.board {
                let item_overrides = (ctx.board_alignment_overrides)(
                    board_json,
                    resolved_widths[i],
                    chart_target,
                    row_height,
                )?;
                overrides.extend(item_overrides);
            }
        }
    }

    let mut boxes = Vec::with_capacity(n);
    let mut current_x = 0.0;
    for i in 0..n {
        let width = resolved_widths[i];
        boxes.push(ItemBox {
            x: current_x,
            y: 0.0,
            width,
            height: item_heights[i],
        });
        current_x += width + effective_gap;
    }
    Ok((boxes, overrides, fresh_render))
}

/// render/sizing.py::_measure_grid_layout_height +
/// render/sizing.py::_calculate_grid_dimensions, fused. Two branches are
/// not ported: auto-flow placement (an item with no authored `col`/`row`)
/// refuses by name; the overflow-scale-down path (`_calculate_grid_
/// dimensions`'s `auto_scale` when total row height exceeds
/// `available_height`) has no named refusal — a grid whose rows would
/// legitimately need it produces a numeric `boxes` DIFF instead of a
/// refusal, since detecting "would have overflowed" costs the same
/// arithmetic as porting the scale-down itself.
pub fn calculate_grid_boxes(
    items: &[LayoutItemInput],
    columns: i64,
    available_width: f64,
    _available_height: f64,
    ctx: &SizingCtx,
) -> Result<(Vec<ItemBox>, HashMap<String, f64>, HashSet<String>), String> {
    let effective_gap = ctx.gap + ctx.card_gap;
    let total_gap_x = effective_gap * (columns - 1) as f64;
    let col_width = (available_width - total_gap_x) / columns as f64;

    let mut row_heights: HashMap<i64, f64> = HashMap::new();
    let mut placements = Vec::with_capacity(items.len());
    // A direct chart item's own PRISTINE natural (row_span==1: identical
    // to `content` below; row_span>1: `content` is still this item's own
    // full requirement, comparable against the grouped height the same
    // way — see the `fresh_render` check below). `None` for a non-Vega/
    // board item, mirroring `calculate_cols_boxes`'s own `naturals`.
    let mut naturals: Vec<Option<f64>> = Vec::with_capacity(items.len());
    for item in items {
        let col = item
            .authoring
            .col
            .ok_or("sizing: grid auto-flow placement not ported (item has no authored col)")?;
        let row = item
            .authoring
            .row
            .ok_or("sizing: grid auto-flow placement not ported (item has no authored row)")?;
        let (col_span, row_span) = item_grid_span(item.authoring.col_span, item.authoring.row_span);
        let width = grid_span_width(col_width, col_span, effective_gap);
        let content = item_height(item, ctx, width)?;
        let height_per_row = content / row_span as f64;
        for r in row..row + row_span {
            let entry = row_heights.entry(r).or_insert(0.0);
            if height_per_row > *entry {
                *entry = height_per_row;
            }
        }
        naturals.push(item.chart.and(Some(content)));
        placements.push((col, row, col_span, row_span, width));
    }

    let mut sorted_rows: Vec<i64> = row_heights.keys().copied().collect();
    sorted_rows.sort_unstable();
    let mut row_y: HashMap<i64, f64> = HashMap::new();
    let mut current_y = 0.0;
    for r in sorted_rows {
        row_y.insert(r, current_y);
        current_y += row_heights[&r] + effective_gap;
    }

    let mut boxes = Vec::with_capacity(items.len());
    for (col, row, _col_span, row_span, width) in placements {
        let x = col as f64 * (col_width + effective_gap);
        let y = row_y[&row];
        let mut height = 0.0;
        for r in row..row + row_span {
            height += row_heights[&r];
        }
        height += effective_gap * (row_span - 1) as f64;
        boxes.push(ItemBox {
            x,
            y,
            width,
            height,
        });
    }

    // `layout_sizing.py::_align_grid_rows` (called from `_align_all_cols_
    // in_tree`'s "grid" arm, unconditionally — grid has no upfront
    // `chart_target > 0` gate the way cols does): groups items by `(row,
    // item.height)` — EXACT float equality, a dict key in production —
    // and for any group with more than one item, delegates to
    // `_align_cols_heights(row_items, row_height, render_ctx)`. Its
    // `item.board is not None` branch recurses into `_align_board_
    // charts`, which rewrites a box (the same board-item box rewrite
    // `board.rs::board_alignment_overrides` already ports for the cols
    // case) — reused verbatim here with `target_height == row_height`
    // (grid's own `_align_grid_rows` has no separate `chart_target`
    // concept the way cols's `_col_chart_body_target` does — the group's
    // own shared height IS the target). Its direct-chart-item branch
    // writes no box, but DOES trigger the SAME fresh-render `Payload::
    // measuring` law `calculate_cols_boxes` models — see this crate's own
    // `fresh_render` doc there for why it is a SEPARATE set from
    // `overrides`, not folded into the same map.
    let mut overrides: HashMap<String, f64> = HashMap::new();
    let mut fresh_render: HashSet<String> = HashSet::new();
    let mut row_groups: Vec<(i64, f64, Vec<usize>)> = Vec::new();
    for (i, item) in items.iter().enumerate() {
        let row = item.authoring.row.unwrap_or(0);
        let height = boxes[i].height;
        match row_groups.iter_mut().find(|(r, h, _)| *r == row && *h == height) {
            Some(group) => group.2.push(i),
            None => row_groups.push((row, height, vec![i])),
        }
    }
    for (_, row_height, idxs) in &row_groups {
        if idxs.len() <= 1 {
            continue;
        }
        for &i in idxs {
            if let Some(board_json) = items[i].board {
                let item_overrides = (ctx.board_alignment_overrides)(
                    board_json,
                    boxes[i].width,
                    *row_height,
                    *row_height,
                )?;
                overrides.extend(item_overrides);
            } else if let Some(chart) = items[i].chart {
                if let (Some(natural), Some(id)) =
                    (naturals[i], chart.get("id").and_then(Value::as_str))
                {
                    if height_moved(natural, boxes[i].height) {
                        fresh_render.insert(id.to_string());
                    }
                }
            }
        }
    }
    Ok((boxes, overrides, fresh_render))
}

#[derive(Debug, Clone, Copy)]
pub struct ContainerBox {
    pub width: f64,
    pub height: f64,
    pub content_width: f64,
    pub content_height: f64,
}

/// render/sizing.py::calculate_layout_height, dispatch only. The "rows"
/// summation-order law (`_measure_rows_layout_height`'s exact left-to-right
/// item-height sum plus one gap term, never `boxes.last().y + height`'s
/// different float grouping — board_sweep.py's strict `container.height`
/// comparator caught the two disagreeing by a ULP on
/// playground/callout-tones) lives in [`measure_rows_layout_height`] alone
/// now: both of this function's real callers (`board.rs`) already route
/// "rows" through that dedicated re-derivation before ever reaching this
/// dispatch, so a "rows" arm here would be a second, unreachable
/// implementation of the same law — reviewed as exactly that (round 2
/// HIGH-R2-B). `_measure_tabs_layout_height` returns `content_height +
/// tab_bar_height` for the ACTIVE tab alone; `calculate_tabs_boxes` (below)
/// already assigns that identical `(y=tab_bar_height, height=content_
/// height)` box to EVERY item in the layout (mirroring
/// `_calculate_tabs_dimensions`'s own uniform assignment across all tabs,
/// matching production's own SIZED `ResolvedLayoutItem`s), so "rows" and
/// "tabs" share this one arm — every box shares one bottom edge, same
/// shape as "grid".
pub fn calculate_layout_height(layout_type: &str, boxes: &[ItemBox], min_height: f64) -> f64 {
    if boxes.is_empty() {
        return min_height;
    }
    let measured = match layout_type {
        "rows" | "tabs" => boxes.last().map(|b| b.y + b.height).unwrap_or(0.0),
        "cols" => boxes.iter().map(|b| b.height).fold(0.0, f64::max),
        "grid" => boxes.iter().map(|b| b.y + b.height).fold(0.0, f64::max),
        _ => 0.0,
    };
    measured.max(min_height)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_unknown_tab_value_is_refused_not_mapped_to_the_default() {
        let slugs = vec!["first".to_string(), "second".to_string()];
        let mut values = serde_json::Map::new();
        values.insert("tab".to_string(), Value::String("second".into()));
        assert_eq!(resolve_active_tab_index(2, Some("tab"), &slugs, 0, &values), Ok(1));
        values.insert("tab".to_string(), Value::String("nope".into()));
        let err = resolve_active_tab_index(2, Some("tab"), &slugs, 0, &values).unwrap_err();
        assert!(err.starts_with("ERR-TAB-UNKNOWN:"), "{err}");
        assert!(err.ends_with("(tabs: first, second)"), "{err}");
    }

    // ---- layout_sizing.py's render-first clamp/trust split ----

    #[test]
    fn clamp_render_first_height_non_pie_clamps_undershoot() {
        assert_eq!(clamp_render_first_height("bar", 340.0, 350.0), 350.0);
    }

    #[test]
    fn clamp_render_first_height_non_pie_clamps_small_overshoot() {
        assert_eq!(clamp_render_first_height("bar", 351.5, 350.0), 350.0);
    }

    #[test]
    fn clamp_render_first_height_non_pie_trusts_real_overshoot() {
        assert_eq!(clamp_render_first_height("line", 420.0, 350.0), 420.0);
    }

    #[test]
    fn clamp_render_first_height_pie_clamps_only_tiny_overshoot() {
        assert_eq!(clamp_render_first_height("pie", 351.5, 350.0), 350.0);
    }

    #[test]
    fn clamp_render_first_height_pie_trusts_real_undershoot() {
        // Unlike every other family, pie/arc trust an undershoot as-is —
        // an arc-attached-table composition can legitimately be shorter
        // than the aspect-ratio estimate.
        assert_eq!(clamp_render_first_height("pie", 300.0, 350.0), 300.0);
    }

    #[test]
    fn clamp_render_first_height_pie_trusts_real_overshoot_past_two_px() {
        assert_eq!(clamp_render_first_height("arc", 500.0, 350.0), 500.0);
    }

    // ---- layout_sizing.py's support_table width correction ----

    #[test]
    fn support_table_width_correction_no_overhead_returns_none() {
        assert_eq!(
            support_table_width_correction("x", "bar", None, 400.0, 400.0, 0.30).unwrap(),
            None
        );
    }

    #[test]
    fn support_table_width_correction_negative_overhead_returns_none() {
        assert_eq!(
            support_table_width_correction("x", "bar", None, 400.0, 380.0, 0.30).unwrap(),
            None
        );
    }

    #[test]
    fn support_table_width_correction_line_shrinks_regardless_of_orientation() {
        // A vertical line/area/bar has no category-axis-vertical floor —
        // the floor is gated to horizontal bar's column block only.
        assert_eq!(
            support_table_width_correction("x", "line", None, 400.0, 460.0, 0.30).unwrap(),
            Some(340.0)
        );
    }

    #[test]
    fn support_table_width_correction_horizontal_bar_above_floor_shrinks() {
        // width=400, overhead=60 -> would_be=340, floor=400*0.30=120: clears it.
        assert_eq!(
            support_table_width_correction(
                "x",
                "bar",
                Some("horizontal"),
                400.0,
                460.0,
                0.30
            )
            .unwrap(),
            Some(340.0)
        );
    }

    #[test]
    fn support_table_width_correction_horizontal_bar_below_floor_errors() {
        // width=400, overhead=320 -> would_be=80, floor=400*0.30=120: below it.
        let err = support_table_width_correction(
            "narrow_chart",
            "bar",
            Some("horizontal"),
            400.0,
            720.0,
            0.30,
        )
        .unwrap_err();
        assert!(err.contains("narrow_chart"), "error names the chart: {err}");
        assert!(err.contains("below"), "error states the floor violation: {err}");
    }

    #[test]
    fn support_table_width_correction_floors_shrunk_width_at_one_pixel() {
        // An extreme overhead would drive would_be_width negative; the
        // corrected width never goes below 1.0px.
        assert_eq!(
            support_table_width_correction("x", "line", None, 100.0, 5000.0, 0.30).unwrap(),
            Some(1.0)
        );
    }

    // ---- layout_sizing.py::_correct_support_table_height (height half) ----

    #[test]
    fn support_table_height_correction_no_overhead_returns_none() {
        assert_eq!(support_table_height_correction(400.0, 400.0), None);
    }

    #[test]
    fn support_table_height_correction_negative_overhead_returns_none() {
        assert_eq!(support_table_height_correction(400.0, 380.0), None);
    }

    #[test]
    fn support_table_height_correction_shrinks_by_the_overhead() {
        // target=400, actual=460 -> overhead=60 -> corrected=340.
        assert_eq!(support_table_height_correction(400.0, 460.0), Some(340.0));
    }

    #[test]
    fn support_table_height_correction_floors_shrunk_height_at_one_pixel() {
        assert_eq!(support_table_height_correction(100.0, 5000.0), Some(1.0));
    }

    // ---- compile/sizing.py::parse_dimension ----

    #[test]
    fn parse_dimension_percent() {
        assert_eq!(parse_dimension(Some("30%"), 1000.0), Some(300.0));
    }

    #[test]
    fn parse_dimension_px() {
        assert_eq!(parse_dimension(Some("200px"), 1000.0), Some(200.0));
    }

    #[test]
    fn parse_dimension_bare_number() {
        assert_eq!(parse_dimension(Some("200"), 1000.0), Some(200.0));
    }

    #[test]
    fn parse_dimension_none_is_auto() {
        assert_eq!(parse_dimension(None, 1000.0), None);
    }

    // ---- compile/sizing.py::grid_span_width / item_grid_span ----

    #[test]
    fn grid_span_width_single_track() {
        assert_eq!(grid_span_width(100.0, 1, 20.0), 100.0);
    }

    #[test]
    fn grid_span_width_multi_track_bakes_internal_gaps() {
        // compile/sizing.py:91-93 — track_width * span + gap * (span - 1).
        assert_eq!(grid_span_width(100.0, 3, 20.0), 340.0);
    }

    #[test]
    fn item_grid_span_defaults_to_one() {
        assert_eq!(item_grid_span(None, None), (1, 1));
        assert_eq!(item_grid_span(Some(3), Some(2)), (3, 2));
    }

    // ---- compile/sizing.py::resolve_cols_widths ----

    #[test]
    fn resolve_cols_widths_all_auto_splits_equally() {
        let widths = resolve_cols_widths(&[None, None], 1000.0, 2);
        assert_eq!(widths, vec![500.0, 500.0]);
    }

    #[test]
    fn resolve_cols_widths_mixed_explicit_and_auto() {
        // 300 explicit + one auto item absorbing the 700 remainder.
        let widths = resolve_cols_widths(&[Some(300.0), None], 1000.0, 2);
        assert_eq!(widths, vec![300.0, 700.0]);
    }

    #[test]
    fn resolve_cols_widths_overflow_scales_explicit_and_reserves_auto_share() {
        // Explicit widths (900) exceed content_width (800) with one auto
        // sibling: equal_share = 800/2 = 400, specified_budget =
        // max(800 - 400*1, 0) = 400, scale = 400/900.
        let widths = resolve_cols_widths(&[Some(900.0), None], 800.0, 2);
        assert_eq!(widths[1], 400.0);
        assert!((widths[0] - 900.0 * (400.0 / 900.0)).abs() < 1e-9);
    }

    // ---- compile/sizing.py::rows_item_width ----

    #[test]
    fn rows_item_width_defaults_to_available_width() {
        assert_eq!(rows_item_width(None, None, 600.0), 600.0);
    }

    #[test]
    fn rows_item_width_authored_percent_caps_at_row() {
        assert_eq!(rows_item_width(Some("50%"), None, 600.0), 300.0);
    }

    #[test]
    fn rows_item_width_chart_width_fallback() {
        assert_eq!(rows_item_width(None, Some(400.0), 600.0), 400.0);
    }

    #[test]
    fn rows_item_width_never_exceeds_available() {
        assert_eq!(rows_item_width(None, Some(900.0), 600.0), 600.0);
    }

    // ---- render/sizing.py::get_chart_content_height ----

    fn test_defaults() -> ChartDefaultsDoc {
        ChartDefaultsDoc {
            default_chart_height: 300.0,
            default_table_height: 250.0,
            min_height: 200.0,
            max_height: 600.0,
            aspect_ratio: 1.5,
            padding: ChartDefaultsPaddingDoc {
                top: 0.0,
                bottom: 0.0,
            },
            kpi: KpiDefaultsDoc {
                default_height: 80.0,
            },
            spark_bar: SparkBarDefaultsDoc {
                max_bars: 10,
                bar: SparkBarBarDefaultsDoc {
                    height: 18.0,
                    padding: 2.0,
                },
            },
            bar: FamilyDefaultsDoc {
                aspect_ratio: Some(1.39),
            },
            line: FamilyDefaultsDoc { aspect_ratio: None },
            area: FamilyDefaultsDoc { aspect_ratio: None },
            scatter: FamilyDefaultsDoc { aspect_ratio: None },
            heatmap: FamilyDefaultsDoc { aspect_ratio: None },
            pie: FamilyDefaultsDoc { aspect_ratio: Some(1.0) },
            histogram: FamilyDefaultsDoc { aspect_ratio: None },
            geoshape: FamilyDefaultsDoc { aspect_ratio: Some(1.2) },
            point_map: FamilyDefaultsDoc { aspect_ratio: Some(1.2) },
        }
    }

    #[test]
    fn get_chart_content_height_kpi_ignores_authored_height() {
        let d = test_defaults();
        let h =
            get_chart_content_height("kpi", Some(999.0), None, None, None, Some(300.0), None, &d)
                .unwrap();
        assert_eq!(h, 80.0);
    }

    #[test]
    fn get_chart_content_height_authored_height_wins_no_clamp() {
        let d = test_defaults();
        // 9999 is far outside [min_height, max_height] — authored height is
        // never clamped (render/sizing.py:260-262).
        let h = get_chart_content_height(
            "bar",
            Some(9999.0),
            None,
            None,
            None,
            Some(600.0),
            None,
            &d,
        )
        .unwrap();
        assert_eq!(h, 9999.0);
    }

    #[test]
    fn get_chart_content_height_chart_root_aspect_ratio() {
        let d = test_defaults();
        let h = get_chart_content_height(
            "bar",
            None,
            Some(2.0),
            None,
            None,
            Some(600.0),
            None,
            &d,
        )
        .unwrap();
        assert_eq!(h, 300.0); // 600 / 2.0, inside [200, 600]
    }

    #[test]
    fn get_chart_content_height_family_aspect_ratio_fallback() {
        let d = test_defaults();
        let h =
            get_chart_content_height("bar", None, None, None, None, Some(600.0), None, &d)
                .unwrap();
        assert_eq!(h, (600.0_f64 / 1.39).clamp(200.0, 600.0));
    }

    #[test]
    fn get_chart_content_height_global_aspect_ratio_fallback() {
        let d = test_defaults();
        // line has no family-level aspect_ratio -> falls to global 1.5.
        let h =
            get_chart_content_height("line", None, None, None, None, Some(600.0), None, &d)
                .unwrap();
        assert_eq!(h, (600.0_f64 / 1.5).clamp(200.0, 600.0));
    }

    #[test]
    fn get_chart_content_height_no_width_falls_to_default() {
        let d = test_defaults();
        let h = get_chart_content_height("bar", None, None, None, None, None, None, &d).unwrap();
        assert_eq!(h, 300.0);
    }

    #[test]
    fn get_chart_content_height_clamps_to_max() {
        let d = test_defaults();
        let h = get_chart_content_height(
            "bar",
            None,
            Some(0.1),
            None,
            None,
            Some(60.0),
            None,
            &d,
        )
        .unwrap();
        assert_eq!(h, 600.0); // 60/0.1=600, exactly the max
    }

    /// HIGH 1(c) regression: a chart type `_FAMILY_SLOT` does not name at
    /// all (a genuine typo/unported family) refuses by name rather than
    /// silently borrowing the board's own generic aspect ratio. `histogram`
    /// was this test's own probe until PR 9133 ported it — a fabricated
    /// type name never collides with a future port the way a real,
    /// currently-unported family's name eventually will.
    #[test]
    fn get_chart_content_height_unknown_type_refuses_by_name() {
        let d = test_defaults();
        let err = get_chart_content_height(
            "not-a-real-chart-type",
            None,
            None,
            None,
            None,
            Some(600.0),
            None,
            &d,
        )
        .unwrap_err();
        assert!(err.contains("not-a-real-chart-type"), "{err}");
    }

    /// HIGH 1(c): `geoshape`/`point_map` (and their `map`/`bubble_map`
    /// aliases) route through `_FAMILY_SLOT` to their OWN theme aspect
    /// ratio (1.2 in `_base.yaml`), not the board's generic one (1.5 in
    /// `test_defaults`) — the exact bug the review demonstrated (an earlier
    /// version of `family_slot_aspect` returned 1.5 here).
    #[test]
    fn get_chart_content_height_geoshape_and_point_map_use_their_own_aspect_ratio() {
        let d = test_defaults();
        for (chart_type, alias) in [("geoshape", "map"), ("point_map", "bubble_map")] {
            let direct =
                get_chart_content_height(chart_type, None, None, None, None, Some(600.0), None, &d)
                    .unwrap();
            let aliased =
                get_chart_content_height(alias, None, None, None, None, Some(600.0), None, &d)
                    .unwrap();
            let expected = (600.0_f64 / 1.2).clamp(200.0, 600.0);
            assert_eq!(direct, expected, "{chart_type}");
            assert_eq!(aliased, expected, "{alias}");
        }
    }

    /// HIGH 1(a): `guides/error-handling_0`'s `broken_chart` — an
    /// unresolved `table` item — sizes to production's own number:
    /// `default_table_height(250) + 2·card_padding(16) + 0 + 0 = 282`, not
    /// the invented `default_chart_height(300) + 2·16 = 332` an earlier
    /// version of this port returned.
    #[test]
    fn unresolved_height_table_matches_production_282() {
        let d = test_defaults();
        let h =
            unresolved_height("table", None, None, None, None, None, 600.0, 16.0, &d).unwrap();
        assert_eq!(h, 282.0);
    }

    /// HIGH 1(b) sibling law, pinned at the `get_chart_content_height`
    /// level: spark_bar's static content estimate is production's own
    /// `max_bars * (bar.height + bar.padding) + bar.padding` (render/
    /// sizing.py:241-252) — 10*(18+2)+2 = 202 with this repo's theme
    /// defaults — never the invented zero an earlier version of
    /// `spark_bar_natural_height`'s fallback returned.
    #[test]
    fn get_chart_content_height_spark_bar_untitled_matches_production_202() {
        let d = test_defaults();
        let h = get_chart_content_height(
            "spark_bar",
            None,
            None,
            None,
            None,
            Some(600.0),
            Some(false),
            &d,
        )
        .unwrap();
        assert_eq!(h, 202.0);
    }

    /// A titled spark_bar adds the engine's own `title_height` (24, mirrored
    /// from `spark_bar.rs::TITLE_HEIGHT`) on top of the same 202px body.
    #[test]
    fn get_chart_content_height_spark_bar_titled_adds_title_height() {
        let d = test_defaults();
        let h = get_chart_content_height(
            "spark_bar",
            None,
            None,
            None,
            None,
            Some(600.0),
            Some(true),
            &d,
        )
        .unwrap();
        assert_eq!(h, 226.0);
    }

    /// HIGH 1(a): a `chart_error` spark_bar item has no resolved chart to
    /// read `chart.title` off, and no authored-title fact is forwarded for
    /// it — refuses by name rather than silently guessing untitled.
    #[test]
    fn get_chart_content_height_spark_bar_with_no_title_fact_refuses() {
        let d = test_defaults();
        let err = get_chart_content_height(
            "spark_bar",
            None,
            None,
            None,
            None,
            Some(600.0),
            None,
            &d,
        )
        .unwrap_err();
        assert!(err.contains("title"), "{err}");
    }

    // ---- render/sizing.py::_clamp_to_authored_ceiling ----

    #[test]
    fn clamp_to_authored_ceiling_shrinks_to_shorter_content() {
        assert_eq!(clamp_to_authored_ceiling(300.0, 200.0), 200.0);
    }

    #[test]
    fn clamp_to_authored_ceiling_keeps_authored_when_content_overflows() {
        assert_eq!(clamp_to_authored_ceiling(300.0, 400.0), 300.0);
    }

    #[test]
    fn clamp_to_authored_ceiling_keeps_authored_when_no_content_signal() {
        assert_eq!(clamp_to_authored_ceiling(300.0, 0.0), 300.0);
    }

    // ---- calculate_layout_height ----
    // The "rows" summation-order law lives in `measure_rows_layout_height`
    // alone now (review round 2 HIGH-R2-B) — this dispatch's own "rows"
    // arm just reads `boxes.last().y + height`, same as "tabs"/"grid",
    // with no summation of its own left to pin here.

    #[test]
    fn calculate_layout_height_rows_reads_the_last_box_bottom_edge() {
        let boxes = vec![
            ItemBox {
                x: 0.0,
                y: 0.0,
                width: 600.0,
                height: 100.0,
            },
            ItemBox {
                x: 0.0,
                y: 120.0,
                width: 600.0,
                height: 200.0,
            },
        ];
        assert_eq!(calculate_layout_height("rows", &boxes, 0.0), 320.0);
    }

    #[test]
    fn calculate_layout_height_cols_is_max_height() {
        let boxes = vec![
            ItemBox {
                x: 0.0,
                y: 0.0,
                width: 300.0,
                height: 100.0,
            },
            ItemBox {
                x: 300.0,
                y: 0.0,
                width: 300.0,
                height: 200.0,
            },
        ];
        assert_eq!(calculate_layout_height("cols", &boxes, 0.0), 200.0);
    }

    #[test]
    fn calculate_layout_height_respects_min_height_floor() {
        let boxes = vec![ItemBox {
            x: 0.0,
            y: 0.0,
            width: 600.0,
            height: 50.0,
        }];
        assert_eq!(calculate_layout_height("rows", &boxes, 400.0), 400.0);
    }

    #[test]
    fn calculate_layout_height_empty_layout_is_min_height() {
        assert_eq!(calculate_layout_height("rows", &[], 40.0), 40.0);
    }
}
