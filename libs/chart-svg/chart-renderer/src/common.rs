//! Shared across families: serde types (fonts, strokes, axis style slices —
//! fields mirror the corresponding Resolved* pydantic models; each family
//! deserializes only the slice it consumes), measured layout constants
//! (AXIS_FREE_EDGE_PAD, VALUE_AXIS_BREATHING_ROOM), and the tabular-numeric
//! machinery (TAB_FONT/TAB_STYLE/SUFFIX_GAP, format_numeric_auto).

use std::borrow::Cow;

use crate::codes;
use crate::diagnostic::Diagnostic;
use crate::number_locale::active_number_locale;
use crate::svg::xml_escape_attr;
use crate::text::FontBook;
use serde::Deserialize;
use serde_json::{json, Value};

// Axis-free-edge fixed inset — the small, fixed padding Vega-Lite's own
// `autosize: fit` reserves on the edge with no label gutter to size (no
// antialiasing/edge breathing room beyond this). Measured against production
// goldens (charts/bar-charts_1's `product_revenue_columns`, playground/
// temporal-label-cadence-transitions' `dy_14`): plot origin sits at exactly
// `card_padding + 2.0`. Shared by bar.rs (both orientations) and line.rs
// (right-axis left pad) — same measured fact, not two coincidences.
pub const AXIS_FREE_EDGE_PAD: f64 = 2.0;
// Breathing room between a value-axis label's near edge and the tick, for the
// own-side-aligned value axis (the default: right-oriented with labels
// flipped inward, `labelAlign == orient`). Matches `_BREATHING_ROOM_PX` in
// `_measured_label_padding.py` — Vega-Lite's `autosize: fit` doesn't reserve
// gutter for this flipped direction on its own, so dbt charts measures the
// real label width and bakes the result straight into `labelPadding`,
// replacing the themed `axis.labels.padding` (6.0) entirely for this case.
// Confirmed empirically: production's baked `labelPadding` for value ticks
// always equals `max_label_width + 4.0`, never `max_label_width + 6.0`.
// Shared by bar.rs and line.rs.
pub const VALUE_AXIS_BREATHING_ROOM: f64 = 4.0;

/// `(x, y, width, height)` of a card rect for content rendered at the inner
/// `width` × `height`: with `inset`, the rect reaches back out over the
/// padding the caller shrank its render box by, so the card fills the outer
/// box the way a Vega chart's spec-baked background does. Port of
/// `svg_utils.card_box` (`render/svg_utils.py:173`) — the SVG-family
/// (kpi/table) card-fill law, `docs/laws-shared.md#background`.
pub fn card_box(width: f64, height: f64, inset: Option<&crate::Padding>) -> (f64, f64, f64, f64) {
    match inset {
        None => (0.0, 0.0, width, height),
        Some(p) => (-p.left, -p.top, width + p.left + p.right, height + p.top + p.bottom),
    }
}

/// `_layers.py::HOVER_TARGET_SIZE` -- the invisible large-target point
/// overlay's own Vega symbol AREA (radius = `sqrt(size)/2`, ~8.944px). Its
/// ink is deliberately excluded from a family's point reach: the overlay is
/// unclipped-but-invisible and carries no more layout weight than the halo
/// and foreground points already reserve.
pub const HOVER_TARGET_SIZE: f64 = 320.0;

/// Vega's `circle` symbol radius for a mark of area `size`.
///
/// Vega-scenegraph redefines d3-shape's `circle` so that `size` is the area of
/// the mark's BOUNDING SQUARE rather than of the disc, making the radius
/// `√size / 2` — d3-shape's own `√(size/π)` is 2/√π = 1.128× too big. One law
/// for every drawn marker, every halo, and the ink they join a fit with;
/// shared by line.rs (point overlay + halo) and scatter.rs (data marks).
pub fn symbol_radius(size: f64) -> f64 {
    size.sqrt() / 2.0
}

/// How far an unfilled symbol's ring grows its ink: `boundStroke` adds the
/// full stroke width, a filled symbol nothing. Shared by scatter.rs's data
/// marks and bar.rs's right-pinned scatter layer.
pub fn symbol_stroke_ink(filled: Option<bool>, stroke_width: f64) -> f64 {
    if filled == Some(false) { stroke_width } else { 0.0 }
}

/// A tilted category label's own ink: the flat text box (extended one
/// extra line height for a two-row temporal label), rotated about its own
/// anchor when `angle != 0.0` (vega-scenegraph's `text.js bound()` →
/// `Bounds.rotate` — the axis's own side then reads the rotated extent
/// exactly). This is vega-scenegraph's raw text bound with no expand —
/// `text.js bound()` in vega-scenegraph 5.3 adds none. A BAND-scale axis
/// (bar's own ordinal category axis) draws its labels inside the axis
/// group's own `+0.5` crisp translate, which the band axis itself bakes
/// back OUT of its label ink (a term specific to that shape, not this
/// law) — callers on a band scale add their own `±0.5` at the call site;
/// a POINT-scale axis (scatter's nominal x) carries no such bake-out and
/// must not add it. The crowding ladder itself
/// (`vega_fit::category_label_layout`) and the anchor it implies
/// (`vega_fit::vl_x_label_anchor`) already live in `vega_fit.rs` as bare
/// functions every cartesian family can call directly; this is the one
/// remaining piece of the law bar's own ordinal category axis
/// (`bar.rs`'s vertical fit-arm ink closure) and scatter's nominal-x arm
/// both need — one function, not a second reading of it.
pub fn rotated_category_label_ink(
    cx: f64,
    row_y: f64,
    label_w: f64,
    font_size: f64,
    align: &str,
    baseline: &str,
    angle: f64,
    extra_rows: usize,
) -> crate::vega_fit::Bounds {
    let mut ink = crate::vega_fit::text_ink(cx, row_y, label_w, font_size, align, baseline);
    ink.y2 += extra_rows as f64 * crate::text::line_height(font_size);
    if angle != 0.0 {
        ink = ink.rotate(angle, cx, row_y);
    }
    ink
}

/// The drawn `stroke-linecap` for a line: the authored `stroke.cap`
/// (default `"round"`), replaced by `disconnected_cap` (default `"butt"`)
/// whenever the caller draws separate, disconnected sub-paths for a
/// `curve: step`, `connect: false` band-step — the default round cap would
/// otherwise overhang each plateau's own flush band edge by half the
/// stroke width and visually re-weld neighboring plateaus together
/// (`emitters/_layers.py`'s `fg_props["strokeCap"] = line_mark.
/// disconnected_cap` guarded by `line_mark.connect is False`). One law,
/// shared by `line.rs`'s own band-step arm and `layers.rs`'s disconnected
/// step-layer overlay (`docs/laws-layers.md`) — never re-derived per arm.
pub fn resolve_stroke_cap<'a>(cap: Option<&'a str>, disconnected: bool, disconnected_cap: Option<&'a str>) -> &'a str {
    let cap = cap.unwrap_or("round");
    if disconnected {
        disconnected_cap.unwrap_or(cap)
    } else {
        cap
    }
}

/// The font file production's `get_font_path` measures a family with:
/// Source Serif keeps its own file, every other family measures as Inter
/// (the sans stand-in — labels may PAINT in another family, e.g. dbt Sans
/// Tabular, but production-baked gutters were sized with Inter advances).
/// Shared by table.rs's column demands and bar.rs's value-label gutter.
pub fn measure_family_stand_in(family: &str) -> &str {
    if family.contains("Source Serif") {
        // The `Web` alias is a paint-time CSS font name; production measures the
        // bundled file under the authored family.
        family.strip_prefix("'Source Serif 4 Web', ").unwrap_or(family)
    } else {
        "Inter"
    }
}

/// The baked rungs inside the baked domain — production's
/// `ResolvedAxisStyle.drawn_tick_values` (`axis_cascade.py`), the ladder its
/// ruler anchor and its measured `labelPadding` read. A rung a merged scale's
/// nice re-admits is drawn by Vega but never sizes the gutter; a rung past an
/// unbaked edge (`domain_max: None`) sizes it even though Vega drops it.
pub fn drawn_tick_values(ax: &AxisStyle) -> Vec<f64> {
    ax.tick_values
        .iter()
        .copied()
        .filter(|&t| ax.domain_min.is_none_or(|lo| t >= lo) && ax.domain_max.is_none_or(|hi| t <= hi))
        .collect()
}

/// Production's weighted measurer, `get_weighted_font_measurer(family,
/// css_weight).measure(text, size)` (`font_measure.py:133-169`): the family
/// resolves through the same file `measure_family_stand_in` names, instanced at
/// the painted CSS weight. A width production bakes into its own layout is
/// measured this way; a width Vega measures stays on `FontBook::measure`,
/// which vl-convert does not re-instance.
pub fn measure_at_css_weight(
    fonts: &crate::text::FontBook,
    text: &str,
    family: &str,
    size: f64,
    css_weight: &str,
) -> Result<f64, String> {
    fonts.measure_at_weight(
        text,
        measure_family_stand_in(family),
        size,
        mdsvg::fonts::css_weight_axis(css_weight)?,
    )
}

// Tabular numeric font used for all number cells (table.rs body cells, pie.rs
// attached-table value column).
pub const TAB_FONT: &str = "'dbt Sans Tabular', Inter, system-ui, sans-serif";
pub const TAB_STYLE: &str = "font-variant-numeric: tabular-nums lining-nums; \
    font-feature-settings: 'tnum' 1, 'lnum' 1;";
// Gap (px) between a formatted number and its scale suffix (K, M, …).
pub const SUFFIX_GAP: f64 = 3.0;

/// `PredefinedTextFormat` (`text/predefined_formats.py`): names that print a
/// value unchanged and mark it for a link.
const TEXT_FORMATS: [&str; 2] = ["email", "url"];

pub(crate) fn is_text_format(spec: &str) -> bool {
    TEXT_FORMATS.contains(&spec)
}

/// Auto-format a raw numeric value the way production's default table-cell
/// path does for an unformatted column (`format_table_cell_value` →
/// `default_number_format()`, the `number` member): d3 SI notation with an
/// analytic-register suffix split out for separate styling, and plain digits
/// below 1: `format_kpi_parts` of the `number` format, whole: the lanes (word
/// stripped of its gap) and the string `format_value` prints (`measured_*`,
/// word behind the gap it carries). Shared by table.rs's date lane.
pub(crate) fn format_numeric_auto(v: f64) -> Result<KpiParts, String> {
    let number_spec = predefined_number_spec("number").expect("`number` is a predefined format");
    let parts = FormatParts { spec: Some(number_spec), raw: Some("number"), ..FormatParts::default() };
    format_kpi_parts(v, &parts, &PartsOptions::default()).map_err(|e| format!("numeric auto-format {v}: {e}"))
}

/// What the wire tagged on one cell: the Python `str()` of the value, and the
/// non-finite float a `null` stands for (`None` for a Decimal).
#[derive(Clone, Debug, PartialEq)]
pub struct CellTag {
    pub text: String,
    pub non_finite: Option<f64>,
}

/// The tags of one row's tagged cells, keyed by column.
pub type CellRow = std::collections::BTreeMap<String, CellTag>;

/// Per-row tags of the cells the wire tagged (`{"decimal": text}`,
/// `{"non_finite": kind, "text": text}`). A pivot labels its dimension columns
/// with the text, and KPI and spark_bar print a non-finite cell's text;
/// everything else reads the decoded number.
pub type CellText = Vec<CellRow>;

/// The tag of the cell at `row`, `column`, if the wire tagged it.
pub fn tag_at<'a>(tags: &'a [CellRow], row: usize, column: &str) -> Option<&'a CellTag> {
    tags.get(row)?.get(column)
}

/// The one place the wire's cell markers are read, so every family sees plain
/// numbers. JSON has no `NaN`/`Infinity`/exact Decimal, so the Python seam tags
/// a finite Decimal as `{"decimal": str(value)}` and a non-finite float or
/// Decimal as `{"non_finite": "nan" | "inf" | "-inf", "text": str(value)}`. A
/// Decimal becomes the float it converts to; a non-finite value becomes `null`,
/// the missing value every family already handles. Returns the tag of each
/// tagged top-level cell; a malformed marker is an error.
pub fn decode_cells(rows: &mut [serde_json::Map<String, Value>]) -> Result<CellText, String> {
    rows.iter_mut()
        .map(|row| {
            let mut tags = CellRow::new();
            for (column, cell) in row.iter_mut() {
                if let Some(tag) = decode_value(cell)? {
                    tags.insert(column.clone(), tag);
                }
            }
            Ok(tags)
        })
        .collect()
}

fn decode_value(v: &mut Value) -> Result<Option<CellTag>, String> {
    match v {
        Value::Object(o) => {
            let tag = match (o.get("decimal"), o.get("non_finite")) {
                (Some(Value::String(text)), None) if o.len() == 1 => {
                    let number = text
                        .parse::<f64>()
                        .ok()
                        .and_then(serde_json::Number::from_f64)
                        .ok_or_else(|| format!("cell tagged decimal is not a finite number: {text:?}"))?;
                    Some((CellTag { text: text.clone(), non_finite: None }, Value::Number(number)))
                }
                (None, Some(Value::String(kind))) if o.len() <= 2 => {
                    let non_finite = match kind.as_str() {
                        "nan" => f64::NAN,
                        "inf" => f64::INFINITY,
                        "-inf" => f64::NEG_INFINITY,
                        other => return Err(format!("cell tagged non_finite has unknown kind {other:?}")),
                    };
                    let text = o.get("text").and_then(Value::as_str).unwrap_or(kind);
                    Some((CellTag { text: text.to_string(), non_finite: Some(non_finite) }, Value::Null))
                }
                _ => None,
            };
            if let Some((tag, decoded)) = tag {
                *v = decoded;
                return Ok(Some(tag));
            }
            for child in o.values_mut() {
                decode_value(child)?;
            }
        }
        Value::Array(items) => {
            for child in items {
                decode_value(child)?;
            }
        }
        _ => {}
    }
    Ok(None)
}

#[derive(Deserialize, Clone, Default)]
pub struct Font {
    pub family: Option<String>,
    pub color: Option<String>,
    pub size: Option<f64>,
    /// Arrives as "400" on some models and 400.0 on others.
    #[serde(default)]
    pub weight: Option<Value>,
    pub line_height: Option<f64>,
    #[serde(default)]
    pub case: Option<String>,
    /// `normal` / `italic`: a text mark writes `font-style` only when the
    /// resolved font carries one (`apply_label_font`'s `fontStyle`).
    #[serde(default)]
    pub style: Option<String>,
}

impl Font {
    /// ` font-style="…"` when the resolved font carries a style, else nothing.
    pub fn style_attr(&self) -> String {
        self.style
            .as_deref()
            .map(|s| format!(" font-style=\"{}\"", crate::svg::xml_escape_attr(s)))
            .unwrap_or_default()
    }

    pub fn weight_str(&self) -> String {
        match &self.weight {
            // `as_f64()` only returns `None` for a `Number` this crate
            // cannot produce (`arbitrary_precision` is off in Cargo.toml,
            // so every `Number` is backed by an i64/u64/f64 that always
            // converts) — a silent 400 here would be a made-up value, not
            // a real fallback.
            Some(Value::Number(n)) => format!(
                "{}",
                n.as_f64()
                    .expect("serde_json Number without arbitrary_precision always converts")
                    as i64
            ),
            Some(Value::String(s)) => s.clone(),
            _ => "400".into(),
        }
    }
    /// `text`'s width at this font's own painted weight
    /// ([`measure_at_css_weight`]). The resolved style always carries the
    /// weight, so an absent or non-CSS one is a malformed payload, not a 400.
    pub fn measure_weighted(
        &self,
        fonts: &crate::text::FontBook,
        text: &str,
        size: f64,
    ) -> Result<f64, String> {
        if !matches!(self.weight, Some(Value::Number(_) | Value::String(_))) {
            return Err(format!(
                "font.weight must be a CSS weight string or a number to measure {text:?}, got {:?}",
                self.weight
            ));
        }
        measure_at_css_weight(fonts, text, self.family_or("sans-serif"), size, &self.weight_str())
    }
    pub fn size_or(&self, d: f64) -> f64 {
        self.size.unwrap_or(d)
    }
    pub fn family_or<'a>(&'a self, d: &'a str) -> &'a str {
        self.family.as_deref().unwrap_or(d)
    }
}

#[derive(Deserialize, Clone)]
pub struct Stroke {
    pub color: Option<String>,
    pub width: Option<f64>,
    pub join: Option<String>,
    pub cap: Option<String>,
    pub dasharray: Option<Value>,
}

/// A separator line's resolved style — `DividerStyle`
/// (`compile/models/primitives.py`): whether it draws, its color and width, and
/// an optional dash pattern in pixels ("6 3"; unset is solid). Every slot is
/// complete in every theme, so nothing here has a default: a payload missing a
/// key refuses by name rather than drawing a guessed line.
#[derive(Deserialize, Clone, Debug)]
pub struct Divider {
    pub visible: bool,
    pub color: String,
    pub width: f64,
    #[serde(deserialize_with = "de_dasharray")]
    pub dasharray: Option<String>,
}

/// A divider that also reserves `gap` pixels between its line and the content
/// it separates (`style.footer.divider`, `style.charts.support_table.divider`).
#[derive(Deserialize, Clone, Debug)]
pub struct GappedDivider {
    #[serde(flatten)]
    pub line: Divider,
    pub gap: f64,
}

/// Production compiles a divider's dash pattern to space-joined numbers
/// (`DividerStyle._dasharray_is_numbers`); anything else is a stale payload.
fn de_dasharray<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    let dasharray: Option<String> = Option::deserialize(d)?;
    if let Some(pattern) = &dasharray {
        if pattern.is_empty() || pattern.split(' ').any(|part| part.parse::<f64>().is_err()) {
            return Err(serde::de::Error::custom(format!(
                "divider dasharray must be space-joined numbers such as \"6 3\", got {pattern:?}"
            )));
        }
    }
    Ok(dasharray)
}

impl Divider {
    /// ` stroke-dasharray="…"` for a line production draws itself
    /// (`svg_utils.divider_dash_attr`): the compiled pattern verbatim, `""`
    /// when solid.
    pub fn dash_attr(&self) -> String {
        match &self.dasharray {
            None => String::new(),
            Some(dasharray) => format!(" stroke-dasharray=\"{}\"", xml_escape_attr(dasharray)),
        }
    }

    /// ` stroke-dasharray="6,3"` for a line Vega serializes (a Vega-Lite
    /// `rule` mark's `strokeDash`, joined by commas), `""` when solid.
    pub fn vega_dash_attr(&self) -> String {
        match &self.dasharray {
            None => String::new(),
            Some(dasharray) => {
                let parts: Vec<String> = dasharray
                    .split(' ')
                    .map(|part| part.parse::<f64>().expect("validated in de_dasharray").to_string())
                    .collect();
                format!(" stroke-dasharray=\"{}\"", parts.join(","))
            }
        }
    }
}

// ── Ruler overlay ─────────────────────────────────────────────────────────────

/// Ruler overlay on a continuous axis.
///
/// The anchor tick (last when anchor_at_start=false, first when true) gets the
/// magnitude word at the ruler's register (`"K"` for exponent=3,
/// `ResolvedRulerAxis.suffix_string`, `_base.py:161-163`). All others get the
/// reservation padding string.
#[derive(Deserialize, Clone, PartialEq)]
pub struct RulerConfig {
    pub exponent: i32,
    #[serde(default)]
    pub mode: String,
    pub digit_spec: String,
    pub anchor_at_start: bool,
    pub reservation: String,
    /// Currency symbol to render on a tick (production's labelExpr:
    /// `datum.index === <anchor> ? format(v, "$<spec>") : ...`). Carried by
    /// the anchor tick alone or by every non-zero tick — see `prefix_repeats`
    /// below, which decides which. Required on `ResolvedRulerAxis` (a
    /// frozen dataclass field, baked by `ruler_digit_format`) — the empty
    /// string means "no currency".
    pub prefix: String,
    /// Whether the currency `prefix` carries on EVERY non-zero tick
    /// (`compile/resolve/style/scale.py`: `prefix_repeats=not
    /// column_forming`) rather than the anchor tick alone — independent of
    /// `mode`/`suffix_present` (`vl_field_maps.py`'s own `prefix_present`
    /// ternary, gated on `ruler.prefix_repeats`, never `ruler.mode`).
    /// `#[serde(default)]` (anchor-only, the pre-#8803 shape) so the many
    /// hand-built fixtures predating this field keep deserializing; a real
    /// resolved payload always bakes it explicitly.
    #[serde(default)]
    pub prefix_repeats: bool,
    /// The register the magnitude word is spelled in (`_build_ruler`,
    /// `scale.py:149-151`: the authored notation, else analytic for an
    /// ANCHOR-mode scale and narrative for REPEAT), baked on every ruler.
    pub register: String,
}

impl RulerConfig {
    /// Whether the ruler's words are analytic (`"K"`) or narrative (`"k"`),
    /// per the baked `register`.
    pub fn analytic(&self) -> Result<bool, String> {
        match self.register.as_str() {
            "analytic" => Ok(true),
            "narrative" => Ok(false),
            other => Err(format!("ruler register {other:?} is neither analytic nor narrative")),
        }
    }
}

/// The ghost axis `style.axis_y.mirror` asks for ([`AxisStyle::mirror_ghost`]).
pub struct MirrorGhost {
    /// The ghost's own `format` (`ResolvedMirrorAxis.format`); `None` repeats
    /// the primary's labels.
    pub format: Option<FormatSlot>,
    /// The ruler baked for the ghost's own edge when it is not the primary's;
    /// `None` repeats the primary's labels.
    pub ruler: Option<RulerConfig>,
}

impl MirrorGhost {
    /// The ghost's own label for each of `ticks`, `None` when it repeats the
    /// primary's: the ghost's `format` paints as a plain value-axis tick
    /// ([`FormatSlot::paint_tick`], `compose_axis_format`), its own ruler as
    /// `compose_axis_label_expr` does.
    pub fn labels(&self, ticks: &[f64]) -> Result<Option<Vec<String>>, String> {
        if let Some(slot) = &self.format {
            return ticks
                .iter()
                .map(|&tv| slot.paint_tick(tv).map_err(|e| format!("axis_y.mirror.format: {e}")))
                .collect::<Result<_, _>>()
                .map(Some);
        }
        let Some(ruler) = &self.ruler else { return Ok(None) };
        ticks
            .iter()
            .enumerate()
            .map(|(i, &tv)| format_y_label(tv, Some(ruler), None, None, i, ticks.len()))
            .collect::<Result<_, _>>()
            .map(Some)
    }

    /// [`Self::labels`], falling back to the `primary` labels it repeats.
    pub fn texts(&self, ticks: &[f64], primary: &[String]) -> Result<Vec<String>, String> {
        Ok(self.labels(ticks)?.unwrap_or_else(|| primary.to_vec()))
    }

    /// The width of each ghost label, `primary_ws` when it repeats the primary's.
    pub fn widths(
        &self,
        ticks: &[f64],
        primary_ws: &[f64],
        measure: impl Fn(&str) -> Result<f64, String>,
    ) -> Result<Vec<f64>, String> {
        match self.labels(ticks)? {
            Some(labels) => labels.iter().map(|label| measure(label)).collect(),
            None => Ok(primary_ws.to_vec()),
        }
    }
}

/// The edge an x axis hangs from (`axis_x.position`). Every distance away from the plot,
/// the tick, the label row and the title, flips sign on the top edge; the labels anchor
/// on their `bottom` baseline there instead of `top`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum XEdge {
    Bottom,
    Top,
}

impl XEdge {
    /// `+1` below the plot, `-1` above it.
    pub fn sign(self) -> f64 {
        match self {
            XEdge::Bottom => 1.0,
            XEdge::Top => -1.0,
        }
    }

    /// The baseline a flat label anchors on.
    pub fn baseline(self) -> &'static str {
        match self {
            XEdge::Bottom => "top",
            XEdge::Top => "bottom",
        }
    }

    /// The y of the plot edge the axis hangs from, in a plot of height `plot_h`.
    pub fn edge_y(self, plot_h: f64) -> f64 {
        match self {
            XEdge::Bottom => plot_h,
            XEdge::Top => 0.0,
        }
    }

    /// The y of a distance `away` from the plot edge, in a plot of height `plot_h`.
    pub fn at(self, plot_h: f64, away: f64) -> f64 {
        self.edge_y(plot_h) + self.sign() * away
    }
}

/// JS `+text`: what d3 formats for a category tick that reads as a number. A category
/// that does not is NaN, which `gate_label_format` has already refused for a number spec.
pub fn js_number(text: &str) -> f64 {
    match text.trim() {
        "" | "false" => 0.0,
        "true" => 1.0,
        other => other.parse().unwrap_or(f64::NAN),
    }
}

/// Where a leading sign sits against a prefix (`SignPlacement`,
/// `text/format_d3.py:57`).
#[derive(Deserialize, Clone, Copy, Debug, Default, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SignPlacement {
    #[default]
    BeforePrefix,
    AfterPrefix,
}

/// A resolved format slot: `ResolvedFormat`'s wire form
/// (`compile/models/primitives.py`), a bare d3 spec or an object once the slot
/// carries an affix, a notation, a sign placement, a repeat or its authored
/// name (`raw`). One parse and one painter serve every slot in every family.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct FormatSlot {
    pub spec: String,
    pub prefix: String,
    pub suffix: String,
    /// `analytic` or `narrative`: the register an SI letter is spelled in.
    pub notation: Option<String>,
    pub sign_placement: Option<SignPlacement>,
    /// `anchor` or `every`: how a ladder (axis, column, strip) repeats the
    /// affix. A per-value painter ignores it.
    pub repeat: Option<String>,
    pub raw: Option<String>,
}

impl FormatSlot {
    /// The author named a predefined d3 number format (the native names never
    /// reach a d3-painted slot).
    pub fn is_house(&self) -> bool {
        self.raw.as_deref().is_some_and(|r| predefined_number_spec(r).is_some())
    }

    /// The slot as `format_kpi_parts` reads it (`ResolvedFormat` is its
    /// `format_input`): the resolved spec, the authored name house rules key
    /// on, the affixes and the effective sign placement.
    pub(crate) fn parts(&self) -> FormatParts<'_> {
        FormatParts {
            spec: Some(&self.spec),
            raw: self.raw.as_deref(),
            prefix: &self.prefix,
            suffix: &self.suffix,
            notation: self.notation.as_deref(),
            placement: self.placement(),
        }
    }

    /// Whether the slot authors a prefix or suffix text (no notation).
    pub fn has_affix_text(&self) -> bool {
        !self.prefix.is_empty() || !self.suffix.is_empty()
    }

    /// `ResolvedFormat.has_affix`.
    pub fn has_affix(&self) -> bool {
        !self.prefix.is_empty() || !self.suffix.is_empty() || self.notation.is_some()
    }

    /// `ResolvedFormat.placement`: the authored placement, else a spaced
    /// prefix is a word or code ("EUR \u{2212}500") and a glued one a symbol
    /// ("\u{2212}\u{20ac}500") (`default_sign_placement`, `format_d3.py:60`).
    pub fn placement(&self) -> SignPlacement {
        self.sign_placement.unwrap_or_else(|| Self::default_placement(&self.prefix))
    }

    /// `default_sign_placement` (`format_d3.py:60`): a prefix ending in
    /// whitespace is a word or code, anything else a symbol.
    pub fn default_placement(prefix: &str) -> SignPlacement {
        if prefix.chars().next_back().is_some_and(mdsvg::pygments_re::is_py_space) {
            SignPlacement::AfterPrefix
        } else {
            SignPlacement::BeforePrefix
        }
    }

    /// The keys this slot carries beyond its spec, in `ResolvedFormat` order:
    /// the ones a painter must either paint or refuse.
    pub fn carried(&self) -> impl Iterator<Item = &'static str> + '_ {
        [
            ("prefix", !self.prefix.is_empty()),
            ("suffix", !self.suffix.is_empty()),
            ("notation", self.notation.is_some()),
            ("sign_placement", self.sign_placement.is_some()),
            ("repeat", self.repeat.is_some()),
        ]
        .into_iter()
        .filter_map(|(key, set)| set.then_some(key))
    }

    /// The first carried key a value-axis tick painter ([`Self::paint_tick`],
    /// or [`format_y_label`]'s ladder when `ladder`) leaves unpainted: `repeat`
    /// paints only as `every` or on a baked ladder.
    pub fn unpainted_key(&self, ladder: bool) -> Option<&'static str> {
        let repeat_painted = ladder || self.repeat.as_deref() == Some("every");
        self.carried().find(|&key| key == "repeat" && !repeat_painted)
    }

    /// `format_d3(value, spec, prefix, suffix, notation, placement)`
    /// (`text/format_d3.py:572`): the d3 digits, an SI letter respelled at the
    /// slot's notation, then the affix with the sign placed against the prefix.
    pub fn paint(&self, value: f64) -> Result<String, String> {
        self.paint_as(&self.spec, self.notation.as_deref(), value)
    }

    /// [`Self::paint`] of a magnitude with `sign` placed against the prefix
    /// (`sign_affixed_expr`, `vl_field_maps.py:208`): the ranged bar's
    /// signed `Δ` row.
    pub fn paint_signed(&self, sign: char, magnitude: f64) -> Result<String, String> {
        let digits = self.digits(&self.spec, self.notation.as_deref(), magnitude)?;
        Ok(self.wear(&sign.to_string(), &digits))
    }

    /// A plain value-axis tick as `compose_axis_format` paints it
    /// (`vl_field_maps.py:217`): the zero tick stays bare, without the affix
    /// or the spec's own currency symbol; every other tick wears both
    /// (`quantitative_tick_labels`, the gutter's twin).
    pub fn paint_tick(&self, value: f64) -> Result<String, String> {
        if value == 0.0 {
            let bare = match self.spec.as_str() {
                "bytes" | "duration" => self.spec.clone(),
                spec => d3rs::format::without_symbol(spec)?,
            };
            return self.digits(&bare, None, value);
        }
        self.paint(value)
    }

    /// [`Self::paint`] with the digits' spec and notation overridden: the
    /// house register's sub-unit and narrative variants of one slot.
    pub fn paint_as(&self, spec: &str, notation: Option<&str>, value: f64) -> Result<String, String> {
        let digits = self.digits(spec, notation, value)?;
        let (sign, body) = split_sign(&digits);
        Ok(self.wear(sign, body))
    }

    /// A ladder tick's affixed digits (`_affixed_digits_expr`): `spec` paints
    /// the digits at this slot's notation, the slot's affix wraps them, and an
    /// anchored affix (one tick per ladder) leads the sign so the anchor's
    /// digits line up with the bare ticks beside it (`painted_sign_placement`).
    pub fn paint_anchored(&self, spec: &str, value: f64, anchored: bool, pad_table: &[String]) -> Result<String, String> {
        let digits = self.padded_digits(spec, value, pad_table)?;
        let (sign, body) = split_sign(&digits);
        let placement = if anchored { SignPlacement::AfterPrefix } else { self.placement() };
        Ok(self.wear_at(placement, sign, body))
    }

    /// A ladder tick's bare digits at this slot's notation and `spec`
    /// (`numeral_vega_expr(datum.value, tick_label.format, fmt.notation)`):
    /// the text every tick off the anchor paints.
    pub fn bare_digits(&self, spec: &str, value: f64, pad_table: &[String]) -> Result<String, String> {
        self.padded_digits(spec, value, pad_table)
    }

    /// The digits followed by the decimal-alignment pad `pad_table` selects
    /// for them (none when the table is empty).
    fn padded_digits(&self, spec: &str, value: f64, pad_table: &[String]) -> Result<String, String> {
        let digits = self.digits(spec, self.notation.as_deref(), value)?;
        let pad = crate::families::table::decimal_pad_for(pad_table, &digits)?;
        Ok(format!("{digits}{pad}"))
    }

    /// The d3 digits of `value`, an SI letter respelled at `notation`.
    fn digits(&self, spec: &str, notation: Option<&str>, value: f64) -> Result<String, String> {
        if spec.is_empty() && (self.has_affix() || notation.is_some()) {
            return Err("format spec is empty — an affixed or notated slot needs a d3 spec".to_string());
        }
        // The two natives with a Vega arm (`VEGA_ARMS`) paint wherever Vega
        // does; the percent natives have none, so compile refuses them here.
        // `bytes` takes the board's locale there (Vega's `format` reads
        // `config.locale.number`); the Python slots print en-US.
        if spec == "bytes" {
            let (number, unit) = d3rs::native::bytes(value, crate::number_locale::d3_format)?;
            return Ok(format!("{number}{unit}"));
        }
        if spec == "duration" {
            let (number, unit) = d3rs::native::predefined_native(spec, value)?.expect("duration is a native");
            return Ok(format!("{number}{unit}"));
        }
        let digits = crate::number_locale::d3_format(spec, value).map_err(|e| format!("format {spec:?} on {value}: {e}"))?;
        match notation {
            Some(notation) if d3rs::format::parse_parts(spec)?.type_ == 's' => respell_si(&digits, spec, notation),
            _ => Ok(digits),
        }
    }

    /// `affix_signed` (`text/format_d3.py:546`): `body` wrapped in the prefix
    /// and suffix, `sign` placed against the prefix. The one reading of the
    /// sign placement law.
    fn wear(&self, sign: &str, body: &str) -> String {
        self.wear_at(self.placement(), sign, body)
    }

    fn wear_at(&self, placement: SignPlacement, sign: &str, body: &str) -> String {
        if self.prefix.is_empty() {
            return format!("{sign}{body}{}", self.suffix);
        }
        match placement {
            SignPlacement::AfterPrefix => format!("{}{sign}{body}{}", self.prefix, self.suffix),
            SignPlacement::BeforePrefix => format!("{sign}{}{body}{}", self.prefix, self.suffix),
        }
    }
}

/// d3's leading sign and the digits after it (`_affix_signed_expr`'s
/// `substring(text, 0, 1)` split on `^[−+-]`).
fn split_sign(digits: &str) -> (&str, &str) {
    match digits.chars().next() {
        Some(c @ ('\u{2212}' | '+' | '-')) => digits.split_at(c.len_utf8()),
        _ => ("", digits),
    }
}

#[cfg(test)]
impl From<&str> for FormatSlot {
    fn from(spec: &str) -> Self {
        FormatSlot { spec: spec.to_string(), ..FormatSlot::default() }
    }
}

/// `_apply_notation` (`format_d3.py:695`) over `HouseLocale.magnitudes`: d3's
/// trailing SI letter swapped for the active locale's word at `notation` (the
/// word carries its own gap). The SI letter ends the digits, ahead of a
/// currency the locale places after them (`format_d3.py:612`). Any other
/// letter (milli, ...) is left as d3 wrote it.
fn respell_si(digits: &str, spec: &str, notation: &str) -> Result<String, String> {
    let locale = active_number_locale();
    let body = digits.strip_suffix(locale.currency_suffix(spec)).unwrap_or(digits);
    let trailing = &digits[body.len()..];
    let (number, letter) = split_si_suffix(body);
    Ok(match locale.magnitude_word(letter, notation)? {
        Some(word) => format!("{number}{word}{trailing}"),
        None => digits.to_string(),
    })
}

impl<'de> Deserialize<'de> for FormatSlot {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        use serde::de::Error;
        let map = match Value::deserialize(d)? {
            Value::String(spec) => return Ok(FormatSlot { spec, ..FormatSlot::default() }),
            Value::Object(map) => map,
            other => return Err(D::Error::custom(format!("format must be a string or object, got {other}"))),
        };
        let mut slot = FormatSlot::default();
        for (key, val) in map {
            let text = |val: &Value| match val {
                Value::Null => Ok(None),
                Value::String(s) => Ok(Some(s.clone())),
                other => Err(D::Error::custom(format!("format {key:?} must be a string, got {other}"))),
            };
            match key.as_str() {
                "spec" => slot.spec = text(&val)?.unwrap_or_default(),
                "prefix" => slot.prefix = text(&val)?.unwrap_or_default(),
                "suffix" => slot.suffix = text(&val)?.unwrap_or_default(),
                "raw" => slot.raw = text(&val)?,
                "notation" => {
                    slot.notation = text(&val)?;
                    if let Some(n) = &slot.notation {
                        respell_si("1k", "s", n).map_err(D::Error::custom)?;
                    }
                }
                "repeat" => {
                    slot.repeat = text(&val)?;
                    if slot.repeat.as_deref().is_some_and(|r| r != "anchor" && r != "every") {
                        return Err(D::Error::custom(format!("format repeat {val} is neither anchor nor every")));
                    }
                }
                "sign_placement" => {
                    slot.sign_placement = Option::<SignPlacement>::deserialize(val).map_err(D::Error::custom)?;
                }
                other => return Err(D::Error::custom(format!("unknown format key {other:?}"))),
            }
        }
        Ok(slot)
    }
}

/// Non-compacting tick-label decision — `ruler`'s sibling for a ladder that
/// does NOT abbreviate (`ResolvedTickLabel` in
/// `compile/models/style/resolved/_base.py`). Mutually exclusive with
/// `RulerConfig`: a resolved axis carries at most one of the two, and is
/// baked ONLY for the unauthored plain-digit default — an author who writes
/// their own non-SI format (e.g. `axis_y.labels.format: currency_whole` →
/// `"$,.0f"`) gets no `tick_label` at all; production reads `labels.format`
/// (`axis.format` on the VL spec) directly in that case (measured:
/// charts/line-graphs_2's revenue_formatted).
/// `prefix`/`anchor_at_start` mirror `RulerConfig`'s pair for the
/// non-compacting case: when the author's format carries a currency symbol,
/// the symbol paints on the anchor tick only. `tick_label.anchor_at_start`
/// follows the ruler's sign-aware rule over the drawn ticks (largest positive
/// tick when any exist, else most-negative — baked at resolve; the ruler alone
/// keeps the largest-magnitude tick on an unbaked top edge) and is set only
/// when `prefix` is non-empty (None otherwise).
#[derive(Deserialize, Clone)]
pub struct TickLabelConfig {
    pub format: String,
    #[serde(default)]
    pub prefix: String,
    #[serde(default)]
    pub anchor_at_start: Option<bool>,
    /// One pad string per possible trimmed-decimal missing length
    /// (`ResolvedTickLabel.decimal_pad_table`) — appended so every label has
    /// equal rendered advance under a tabular font. Empty when the axis is
    /// non-column-forming or every tick sits at the same decimal depth.
    #[serde(default)]
    pub decimal_pad_table: Vec<String>,
    /// Set only for a ladder-less axis (no baked `tick_values`), where
    /// `format` alone would flatten every tick to plain digits and trade
    /// away k/M compaction. Mutually exclusive with the
    /// prefix/anchor/decimal-pad machinery above — see
    /// `sub_unit_guarded_format`.
    #[serde(default)]
    pub si_format: Option<String>,
    /// The sub-`SUB_UNIT_SCIENTIFIC_FLOOR` register `si_format` falls back
    /// to. Production's `ResolvedTickLabel.__post_init__` requires this
    /// whenever `si_format` is set, but `sub_unit_guarded_format` still
    /// treats it as optional, mirroring `_sub_unit_guarded_format`'s own
    /// signature exactly.
    #[serde(default)]
    pub scientific_format: Option<String>,
}

/// `truncate_text_precise`: `text` when it fits, else the longest char prefix
/// whose `trim_end() + "…"` fits, else the first char.
pub fn truncate_with(
    measure: &dyn Fn(&str) -> Result<f64, String>,
    text: &str,
    max_width: f64,
    ellipsis: bool,
) -> Result<String, String> {
    if measure(text)? <= max_width {
        return Ok(text.to_string());
    }
    let suffix = if ellipsis { "\u{2026}" } else { "" };
    let chars: Vec<char> = text.chars().collect();
    let (mut lo, mut hi, mut best) = (0usize, chars.len(), None::<String>);
    while lo <= hi {
        let mid = (lo + hi) / 2;
        let prefix: String = chars[..mid].iter().collect();
        let cand = format!("{}{suffix}", prefix.trim_end());
        if measure(&cand)? <= max_width {
            best = Some(cand);
            lo = mid + 1;
        } else {
            if mid == 0 {
                break;
            }
            hi = mid - 1;
        }
    }
    Ok(best.filter(|b| !b.is_empty()).unwrap_or_else(|| chars.first().map(|c| c.to_string()).unwrap_or_default()))
}

/// Measured word-wrap with optional line cap + ellipsis — the port of
/// production's `wrap_text_precise` (mdsvg/fonts.py), the function
/// `prepare_title_text` runs for title/subtitle overflow. Semantics ported
/// exactly: whitespace-normalized; per-piece width bookkeeping (line width
/// is the SUM of measured piece widths plus separator widths, as
/// `wrap_measured_pieces` sums them — never a re-measure of the joined
/// string); an over-wide token splits at the longest fitting seam-char
/// (`_/.-?&=:`) prefix first and falls back to the longest fitting char
/// prefix (`split_token_precise`); and when `max_lines` cuts text, every
/// piece from the cut line onward is folded back into ONE string
/// (word-split chunks re-glue without spaces) and char-truncated to
/// `prefix.trim_end() + "…"` (`truncate_text_precise`, which yields the
/// first char when nothing fits). Returns `(lines, truncated)`: `truncated`
/// is true exactly when `max_lines` cut text. Callers measure with the family
/// production's `get_font_measurer(None)` resolves — Inter — regardless of
/// the family the text paints in.
pub fn wrap_text_measured(
    text: &str,
    max_width: f64,
    font_size: f64,
    family: &str,
    fonts: &crate::text::FontBook,
    max_lines: Option<usize>,
    ellipsis: bool,
) -> Result<(Vec<String>, bool), String> {
    let measure = |t: &str| fonts.measure(t, family, font_size);
    wrap_text_with(&measure, text, max_width, max_lines, ellipsis)
}

/// [`wrap_text_measured`] over any measurer: the axis title wraps at its own
/// painted weight, a title block in Inter.
pub fn wrap_text_with(
    measure: &dyn Fn(&str) -> Result<f64, String>,
    text: &str,
    max_width: f64,
    max_lines: Option<usize>,
    ellipsis: bool,
) -> Result<(Vec<String>, bool), String> {
    let normalized = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if normalized.is_empty() {
        return Ok((vec![], false));
    }
    let space_w = measure(" ")?;

    // (text, width, has_separator) — separator is the single space that
    // precedes a piece which starts a new word (never a split-word
    // continuation chunk, and never the very first word).
    struct Piece {
        text: String,
        width: f64,
        has_sep: bool,
    }
    const SEAMS: &[char] = &['_', '/', '.', '-', '?', '&', '=', ':'];
    let mut pieces: Vec<Piece> = Vec::new();
    for (word_index, word) in normalized.split(' ').enumerate() {
        let mut remaining: Vec<char> = word.chars().collect();
        let mut chunk_index = 0usize;
        while !remaining.is_empty() {
            let rem_s: String = remaining.iter().collect();
            let w = measure(&rem_s)?;
            let cut = if w <= max_width {
                remaining.len()
            } else {
                // Longest seam-terminated prefix that fits, else the longest
                // fitting char prefix (at least one char).
                let mut best_seam: Option<usize> = None;
                for (i, ch) in remaining.iter().enumerate() {
                    if i > 0 && SEAMS.contains(ch) {
                        let cand: String = remaining[..=i].iter().collect();
                        if measure(&cand)? <= max_width {
                            best_seam = Some(i + 1);
                        }
                    }
                }
                match best_seam {
                    Some(n) => n,
                    None => {
                        let (mut lo, mut hi, mut best) = (1usize, remaining.len(), 1usize);
                        while lo <= hi {
                            let mid = (lo + hi) / 2;
                            let cand: String = remaining[..mid].iter().collect();
                            if measure(&cand)? <= max_width {
                                best = mid;
                                lo = mid + 1;
                            } else {
                                hi = mid - 1;
                            }
                        }
                        best
                    }
                }
            };
            let piece_s: String = remaining[..cut].iter().collect();
            let piece_w = measure(&piece_s)?;
            pieces.push(Piece {
                text: piece_s,
                width: piece_w,
                has_sep: word_index > 0 && chunk_index == 0,
            });
            remaining.drain(..cut);
            chunk_index += 1;
        }
    }

    // Greedy fill by summed piece widths (wrap_measured_pieces).
    let mut lines: Vec<Vec<&Piece>> = Vec::new();
    let mut line: Vec<&Piece> = Vec::new();
    let mut line_w = 0.0_f64;
    for piece in &pieces {
        let sep_w = if !line.is_empty() && piece.has_sep { space_w } else { 0.0 };
        if line.is_empty() || line_w + sep_w + piece.width <= max_width {
            line_w += sep_w + piece.width;
            line.push(piece);
        } else {
            lines.push(std::mem::take(&mut line));
            line_w = piece.width;
            line.push(piece);
        }
    }
    if !line.is_empty() {
        lines.push(line);
    }

    let join = |ps: &[&Piece]| {
        let mut out = String::new();
        for (i, p) in ps.iter().enumerate() {
            if i > 0 && p.has_sep {
                out.push(' ');
            }
            out.push_str(&p.text);
        }
        out
    };

    if let Some(cap) = max_lines {
        if lines.len() > cap {
            let mut rendered: Vec<String> = lines[..cap - 1].iter().map(|l| join(l)).collect();
            let tail_pieces: Vec<&Piece> = lines[cap - 1..].iter().flatten().copied().collect();
            let tail = join(&tail_pieces);
            let last = truncate_with(measure, &tail, max_width, ellipsis)?;
            rendered.push(last);
            return Ok((rendered, true));
        }
    }
    Ok((lines.iter().map(|l| join(l)).collect(), false))
}

/// `resolve_title_overflow` + `prepare_title_text`: the lines a title or
/// subtitle draws in `limit` px, and whether the line cap cut text (never for
/// `wrap`). An unknown mode takes the wrap-two default.
/// `prepare_title_text` returns `lines[0] if lines else text` for the
/// single-line modes, so a whitespace-only string survives clip and truncate
/// and Vega reserves its full band; the wrapping modes join an empty list
/// into `""`, which reserves nothing (probe: title group origin y 49 for
/// `"   "` against 21 for `""`).
pub fn prepare_title_lines(
    text: &str,
    mode: Option<&str>,
    limit: f64,
    font_size: f64,
    family: &str,
    fonts: &crate::text::FontBook,
) -> Result<(Vec<String>, bool), String> {
    let (cap, ellipsis) = match mode {
        Some("clip") => (Some(1), false),
        Some("truncate") => (Some(1), true),
        Some("wrap") => (None, false),
        _ => (Some(2), true),
    };
    let (lines, truncated) = wrap_text_measured(text, limit, font_size, family, fonts, cap, ellipsis)?;
    Ok((if lines.is_empty() && cap == Some(1) { vec![text.to_string()] } else { lines }, truncated))
}

#[cfg(test)]
mod wrap_text_measured_tests {
    use super::wrap_text_measured;
    use crate::text::FontBook;

    fn fonts() -> FontBook {
        let dir = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        );
        FontBook::load_dir(dir).expect("shipped fonts load")
    }

    const FAM: &str = "'Inter Variable', Inter, sans-serif";

    #[test]
    fn fitting_text_stays_one_line_and_normalizes_whitespace() {
        let f = fonts();
        let lines = wrap_text_measured("hello   world", 500.0, 11.0, FAM, &f, Some(2), true).unwrap().0;
        assert_eq!(lines, vec!["hello world"]);
    }

    #[test]
    fn wrap_two_splits_at_the_measured_width_and_ellipsizes_overflow() {
        let f = fonts();
        // Narrow enough that four words need three lines — the wrap-two cap
        // keeps two, ellipsizing the second.
        let all = wrap_text_measured("alpha beta gamma delta", 60.0, 11.0, FAM, &f, None, false).unwrap().0;
        assert!(all.len() >= 3, "uncapped wrap uses the width: {all:?}");
        let two = wrap_text_measured("alpha beta gamma delta", 60.0, 11.0, FAM, &f, Some(2), true).unwrap().0;
        assert_eq!(two.len(), 2);
        assert!(two[1].ends_with('\u{2026}'), "cut line carries the ellipsis: {two:?}");
        let w = f.measure(&two[1], FAM, 11.0).unwrap();
        assert!(w <= 60.0, "ellipsized line fits the limit: {w}");
    }

    #[test]
    fn truncate_mode_yields_one_ellipsized_line() {
        let f = fonts();
        let lines = wrap_text_measured("a very long chart title indeed", 80.0, 14.0, FAM, &f, Some(1), true).unwrap().0;
        assert_eq!(lines.len(), 1);
        assert!(lines[0].ends_with('\u{2026}'), "{lines:?}");
    }

    #[test]
    fn matches_production_on_the_reviewer_reproduced_divergences() {
        // The three cases review round 1 executed against mdsvg's own
        // wrap_text_precise with the Inter measurer — the port must land
        // the identical lines.
        let f = fonts();
        const INTER: &str = "Inter";
        let a = wrap_text_measured("Revenue share by product line", 200.0, 18.0, INTER, &f, Some(1), true).unwrap().0;
        assert_eq!(a, vec!["Revenue share by pr\u{2026}"]);
        let b1 = wrap_text_measured("Revenue by product category", 160.0, 14.0, INTER, &f, Some(1), true).unwrap().0;
        assert_eq!(b1, vec!["Revenue by product c\u{2026}"]);
        let b2 = wrap_text_measured("Share of total spend by vendor and contract type", 180.0, 14.0, INTER, &f, Some(1), true).unwrap().0;
        assert_eq!(b2, vec!["Share of total spend by v\u{2026}"]);
        let c = wrap_text_measured("https://example.com/very/long/path/segment/here", 120.0, 11.0, INTER, &f, Some(2), true).unwrap().0;
        assert_eq!(c, vec!["https://example.com/", "very/long/path/segm\u{2026}"]);
    }

    #[test]
    fn over_wide_single_word_splits_at_char_granularity() {
        let f = fonts();
        let lines = wrap_text_measured("Antidisestablishmentarianism", 40.0, 11.0, FAM, &f, None, false).unwrap().0;
        assert!(lines.len() > 1, "long word must split: {lines:?}");
        for l in &lines[..lines.len() - 1] {
            assert!(f.measure(l, FAM, 11.0).unwrap() <= 40.0, "chunk fits: {l}");
        }
    }
}

/// The magnitude word for a shared-scale exponent at a register
/// (`numeral_scale.py::suffix_at_register`): the active locale's word, which
/// carries its own gap, none for en-US (analytic `"K"`, narrative `"k"`). An
/// exponent outside the four tiers a shared scale can land on is refused by
/// name.
pub fn suffix_at_register(exponent: i32, analytic: bool) -> Result<String, String> {
    let letter = match exponent {
        3 => "k",
        6 => "M",
        9 => "G",
        12 => "T",
        _ => return Err(format!("magnitude exponent {exponent} is not a shared-scale tier (3/6/9/12)")),
    };
    let notation = if analytic { "analytic" } else { "narrative" };
    Ok(active_number_locale()
        .magnitude_word(letter, notation)?
        .expect("k, M, G and T are magnitude letters"))
}

/// The active locale's magnitude word for d3's SI letter at `notation`, as
/// `format_value` prints it, behind the gap it carries (a no-break space or
/// none; `text/magnitude_words.py`). Any other letter is returned as is.
fn printed_magnitude_word(d3_suffix: &str, notation: &str) -> Result<String, String> {
    Ok(active_number_locale().magnitude_word(d3_suffix, notation)?.unwrap_or_else(|| d3_suffix.to_string()))
}

/// Split a d3-formatted SI string into (number_part, si_char).
///
/// d3rs places the SI prefix immediately after the last digit, e.g. "12.3M".
/// Walk back to find the last digit; everything after is the SI prefix.
pub fn split_si_suffix(s: &str) -> (&str, &str) {
    let pos = s.char_indices().rev().find(|(_, c)| c.is_ascii_digit()).map(|(i, _)| i);
    match pos {
        Some(i) => s.split_at(i + 1),
        None => (s, ""),
    }
}

/// Format a y-axis tick label using the ruler discipline.
///
/// mode "repeat": every non-zero tick carries the magnitude word at the
/// ruler's register ("50k", or "50K" under an authored analytic
/// notation); zero stays bare — matching production's repeat-mode labelExpr.
/// mode "anchor" (default): the anchor tick gets the word, the others the
/// invisible reservation padding, so the column width is constant.
/// Reinsert a currency symbol into a digit-only d3 spec (production's
/// `with_symbol`, numeral_scale.py). The symbol flag sits AFTER the sign in
/// d3's spec grammar ([[fill]align][sign][symbol]…), so a signed spec keeps
/// its sign char first — "+,.1~f" becomes "+$,.1~f", not the unparseable
/// "$+,.1~f" — and d3's own sign-before-symbol placement then applies
/// (format("$,.1~f")(-500) → "−$500", never "$−500").
fn spec_with_symbol(digit_spec: &str, symbol: &str) -> String {
    match digit_spec.chars().next() {
        Some(sign @ ('+' | '-' | '(' | ' ')) => {
            format!("{sign}{symbol}{}", &digit_spec[sign.len_utf8()..])
        }
        _ => format!("{symbol}{digit_spec}"),
    }
}

/// The exact per-tick choice production's `inject_axis_numeral_expr`
/// composes into a ladder-less axis's `labelExpr` (`vl_field_maps.py`),
/// ported here so both the drawn label (`format_y_label`) and its gutter
/// estimate (`estimated_quantitative_tick_labels`'s own port) measure the
/// same string the axis actually paints: magnitude >= 1 takes `si_format`
/// (k/M compaction); below 1 and below `SUB_UNIT_SCIENTIFIC_FLOOR` (and
/// nonzero) takes `scientific_format`; everything else, including zero,
/// takes the plain `format_spec` — mirrors production's
/// `_sub_unit_guarded_format` exactly, `scientific_format` optional and
/// all (production's own signature, even though `ResolvedTickLabel.
/// __post_init__` guarantees it whenever `si_format` is set).
///
/// `affix` is `Some(anchored)` to wear the authored `labels.format` prefix and suffix on a
/// nonzero tick (`anchored`: the ladder baked an anchor, which fixes the sign placement), and
/// `None` for the bare digits.
pub fn sub_unit_guarded_format(
    value: f64,
    format_spec: &str,
    si_format: &str,
    scientific_format: Option<&str>,
    labels_format: Option<&FormatSlot>,
    affix: Option<bool>,
) -> Result<String, String> {
    let sub_unit_sci = scientific_format.filter(|_| value != 0.0 && value.abs() < d3rs::format::SUB_UNIT_SCIENTIFIC_FLOOR);
    let band_spec = if value.abs() >= 1.0 { si_format } else { sub_unit_sci.unwrap_or(format_spec) };
    if let (Some(anchored), Some(slot)) = (affix, labels_format.filter(|s| s.has_affix_text())) {
        if value != 0.0 {
            return slot
                .paint_anchored(band_spec, value, anchored, &[])
                .map_err(|e| format!("tick_label affixed spec {band_spec:?}: {e}"));
        }
    }
    if value.abs() >= 1.0 {
        // Only the SI arm takes the format's notation (`numeral_vega_expr(..,
        // tick_label.si_format, fmt.notation)`).
        return match labels_format {
            Some(slot) => slot.bare_digits(si_format, value, &[]),
            None => crate::number_locale::d3_format(si_format, value),
        }
        .map_err(|e| format!("tick_label si_format {si_format:?}: {e}"));
    }
    crate::number_locale::d3_format(band_spec, value).map_err(|e| format!("tick_label format {band_spec:?}: {e}"))
}

/// Production's `_ESTIMATE_TARGET_COUNTS`.
const ESTIMATE_TARGET_COUNTS: [usize; 7] = [3, 4, 5, 6, 8, 10, 12];

/// Production's `_linear_tick_candidates` (`_measured_label_padding.py:341-
/// 345`): the bounds plus `nice_tick_values` at every estimate count.
fn linear_tick_candidates(lo: f64, hi: f64) -> Vec<f64> {
    let mut candidates = vec![lo, hi];
    for count in ESTIMATE_TARGET_COUNTS {
        candidates.extend(d3rs::ticks::nice_tick_values(lo, hi, count, None));
    }
    candidates
}

/// Production's `_log_tick_candidates` (`_measured_label_padding.py:357-
/// 366`): `k * step^n` for `k` in `1..int(base)` (just `1` for a fractional
/// base) over every exponent from `floor(log_step(lo))` to `ceil(log_step(hi))`,
/// `step` being the base, or its reciprocal below 1 (d3 draws no log ticks
/// for such a base, so the reciprocal's powers stand in), united with the
/// positive members of the linear ladder over the raw `(lo, hi)` (no zero
/// extension) that d3 falls back to when a domain spans too few decades. The
/// exponents use `log(x) / log(step)`, production's `math.log(x, step)`, not
/// `log10`: the two differ in the last ulp at exact powers
/// (`math.log(1000, 10)` is 2.9999999999999996), which moves the floor.
fn log_tick_candidates(axis: &AxisStyle, lo: f64, hi: f64) -> Result<Vec<f64>, String> {
    if lo <= 0.0 {
        return Err(format!("log scale requires positive values, got a minimum of {lo}"));
    }
    let base = axis.log_base();
    let step = if base < 1.0 { 1.0 / base } else { base };
    let multipliers = if base % 1.0 == 0.0 { 1..base as i32 } else { 1..2 };
    let log_step = |v: f64| v.ln() / step.ln();
    let (lo_exp, hi_exp) = (log_step(lo).floor() as i32, log_step(hi).ceil() as i32);
    let mut candidates: Vec<f64> = (lo_exp..=hi_exp)
        .flat_map(|n| multipliers.clone().map(move |k| f64::from(k) * step.powf(f64::from(n))))
        .collect();
    candidates.extend(linear_tick_candidates(lo, hi).into_iter().filter(|&v| v > 0.0));
    Ok(candidates)
}

/// Cover-ladder gutter estimate for an UNBAKED value axis — production's
/// `estimated_quantitative_tick_labels` (`_measured_label_padding.py:274-
/// 337`), called once per chart over the whole dataset from
/// `vl_field_maps.py:298-401`. Shared by `bar.rs` and `line.rs`: both
/// families reach this only when the axis has no baked `tick_values` (a
/// log axis, or — for bar — an independent-scale facet panel, where
/// production's own single per-chart call is the same reserve every panel
/// shares, sized off every row's value across every panel, not this
/// panel's own subset).
///
/// Candidates are `nice_tick_values` at `(3,4,5,6,8,10,12)` — mirroring
/// `_ESTIMATE_TARGET_COUNTS` — over the domain: the AUTHORED
/// `scale.continuous.domain` bounds when the axis pins one ("raw data
/// values outside that range can't produce ticks there"), else the data's
/// own `(data_min, data_max)` extent, always unioned with zero (a
/// zero-anchored scale is common and cheap to cover).
///
/// A log axis (`scale.continuous.type: log`) takes production's
/// `_log_tick_candidates` instead (`:348-361`, selected at `:314-318`):
/// d3's log ticks over the un-extended `(lo, hi)`, see [`log_tick_candidates`].
///
/// Either set is formatted with the SAME per-tick
/// `si_format`/`scientific_format` guard the baked ladder uses (`sub_unit_guarded_format`) but never a ruler's compaction or an
/// anchor-index currency prefix — only the baked sibling
/// `quantitative_tick_labels` takes those. A plain axis (`tick_label` unset)
/// paints `labels_format`'s affix like the drawn ladder ([`FormatSlot::paint_tick`]).
#[allow(clippy::too_many_arguments)]
pub fn estimated_tick_label_gutter(
    fonts: &FontBook,
    family: &str,
    font_size: f64,
    limit: f64,
    tick_label: Option<&TickLabelConfig>,
    labels_format: Option<&FormatSlot>,
    axis: &AxisStyle,
    data_min: f64,
    data_max: f64,
) -> Result<f64, String> {
    let (lo, hi) =
        axis.authored_continuous_domain().map_or((data_min, data_max), |(a, b)| (a.min(b), a.max(b)));
    let candidates = if axis.is_log_scale() {
        log_tick_candidates(axis, lo, hi)?
    } else {
        linear_tick_candidates(lo.min(0.0), hi.max(0.0))
    };
    let measure_family = measure_family_stand_in(family);
    let mut max_w = 0.0_f64;
    for &v in &candidates {
        let lbl = match (tick_label.filter(|tl| tl.si_format.is_some()), labels_format) {
            (Some(tl), _) => {
                let si = tl.si_format.as_deref().expect("filtered on si_format");
                sub_unit_guarded_format(v, &tl.format, si, tl.scientific_format.as_deref(), labels_format, Some(tl.anchor_at_start.is_some()))?
            }
            (None, Some(slot)) => slot.paint_tick(v).map_err(|e| format!("estimated tick label: {e}"))?,
            (None, None) => crate::number_locale::d3_format(",.0~f", v)
                .map_err(|e| format!("estimated tick label \",.0~f\": {e}"))?,
        };
        max_w = max_w.max(fonts.measure(&lbl, measure_family, font_size)?);
    }
    Ok(max_w.min(limit) + VALUE_AXIS_BREATHING_ROOM)
}

/// `stack: normalize` forces `.0%` labels unconditionally, bypassing any
/// ruler, baked `tick_label`, or authored `labels.format` — production's
/// `pin_normalize_axis_format` (`emitters/_cartesian.py:1580-1618`) pops
/// the composed `labelExpr` too, so neither a ruler's compaction nor an
/// unauthored SI `tick_label` default ever reaches a normalize-stacked
/// axis; `format_y_label`'s own ruler-first, tick_label-second precedence
/// must never run for it. Shared by `bar.rs` and `line.rs` so the law
/// lives in one place, not two independent readings of it.
pub fn normalize_percent_label(value: f64) -> Result<String, String> {
    crate::number_locale::d3_format(".0%", value).map_err(|e| format!("normalize tick label: {e}"))
}

pub fn format_y_label(
    value: f64,
    ruler: Option<&RulerConfig>,
    tick_label: Option<&TickLabelConfig>,
    labels_format: Option<&FormatSlot>,
    idx: usize,
    total: usize,
) -> Result<String, String> {
    let Some(ruler) = ruler else {
        // Non-ruler axes: production bakes `tick_label.format` only for the
        // *unauthored* default ladder (plain digits, "," grouped — measured:
        // charts/line-graphs_0's revenue_trend_minimal). An author who wrote
        // their own non-SI format (e.g. `axis_y.labels.format: currency_whole` →
        // `"$,.0f"`) gets NO `tick_label` bake at all — production's VL spec
        // relies on `labels.format` (`axis.format`) directly in that case
        // (measured: charts/line-graphs_2's revenue_formatted — `tick_label`
        // is `None`, `labels.format` is `"$,.0f"`, no `labelExpr`). So the
        // precedence here mirrors the two real resolve-time outcomes:
        // `tick_label` (baked default) first, then `labels.format` (authored
        // literal). Bar and line always bake one of the two
        // (`_base.yaml:220-232`); scatter's resolve path does not (measured:
        // charts/point-charts_2's team_response_dots resolves with `ruler`,
        // `tick_label`, and `labels.format` all `None`) — that gap is a
        // scatter-specific resolve-time fact, not something this renderer
        // can close, so the plain VL default below is a real fallback for
        // scatter, not dead code.
        if let Some(tl) = tick_label {
            // `si_format` is a THIRD, mutually exclusive shape from the
            // prefix/anchor/decimal-pad machinery below (`vl_field_maps.py`'s
            // own docstring): handled first, no ladder to derive a fixed
            // spec from, so magnitude alone (not a baked tick position)
            // picks the register.
            if let Some(si) = tl.si_format.as_deref() {
                let guarded = |affix| sub_unit_guarded_format(value, &tl.format, si, tl.scientific_format.as_deref(), labels_format, affix);
                let bare = guarded(None)?;
                // The authored affix wraps the band's digits (`_affixed_digits_expr`)
                // on every tick, or on the anchor alone once resolve bakes one;
                // zero stays the bare digits.
                let affixed_text = guarded(Some(tl.anchor_at_start.is_some()))?;
                return Ok(match tl.anchor_at_start {
                    None => affixed_text,
                    Some(at_start) if idx == if at_start { 0 } else { total - 1 } => affixed_text,
                    Some(_) => bare,
                });
            }
            // The ladder's affix — the baked currency symbol (`tl.prefix`,
            // folded into the d3 spec so the sign leads it, "−$500") and the
            // authored `labels.format` prefix/suffix — paints on the anchor
            // tick alone (`anchor_at_start` baked: `datum.index === {0|1}`,
            // the first or last drawn tick) or, with `anchor_at_start: None`,
            // on every non-zero tick (`inject_axis_numeral_expr`'s
            // `prefix_present`); a zero tick never wears one.
            let affixed_slot = labels_format.filter(|s| s.has_affix_text());
            let anchor_spec = if tl.prefix.is_empty() {
                tl.format.clone()
            } else {
                spec_with_symbol(&tl.format, &tl.prefix)
            };
            let anchored = tl.anchor_at_start.is_some();
            let on_anchor = value != 0.0
                && match tl.anchor_at_start {
                    Some(true) => idx == 0,
                    Some(false) => idx + 1 == total,
                    None => true,
                };
            let text = match affixed_slot {
                Some(slot) if on_anchor => slot
                    .paint_anchored(&anchor_spec, value, anchored, &[])
                    .map_err(|e| format!("tick_label affixed spec {anchor_spec:?}: {e}"))?,
                Some(slot) => slot
                    .bare_digits(&tl.format, value, &[])
                    .map_err(|e| format!("tick_label format {:?}: {e}", tl.format))?,
                None if on_anchor && !tl.prefix.is_empty() => crate::number_locale::d3_format(&anchor_spec, value)
                    .map_err(|e| format!("tick_label anchor spec {anchor_spec:?}: {e}"))?,
                None => crate::number_locale::d3_format(&tl.format, value)
                    .map_err(|e| format!("tick_label format {:?}: {e}", tl.format))?,
            };
            if tl.decimal_pad_table.is_empty() {
                return Ok(text);
            }
            // `_apply_decimal_pad`: the pad slot follows the painted text's own
            // fractional digits (`_decimal_pad_missing_len_expr`), the anchor's
            // symbol and an authored affix included, then the ternary chain
            // selects table[i] on an exact match for i in 0..len-1 and the
            // LAST entry for everything else.
            let frac = crate::families::table::count_decimals(&text) as i64;
            let precision = tl.decimal_pad_table.len() as i64 - 2;
            let missing = if frac == 0 { precision + 1 } else { precision - frac };
            let table = &tl.decimal_pad_table;
            let pad = table[..table.len() - 1]
                .iter()
                .enumerate()
                .find_map(|(i, p)| (i as i64 == missing).then_some(p.as_str()))
                .unwrap_or_else(|| table[table.len() - 1].as_str());
            return Ok(format!("{text}{pad}"));
        }
        if let Some(slot) = labels_format {
            return slot.paint_tick(value).map_err(|e| format!("labels.format: {e}"));
        }
        // Production's default (no ruler, no explicit format) axis emits
        // labelExpr `format(datum.value, ",.0~f")` — render exactly that
        // spec (U+2212 minus and all), not a hand-rolled approximation.
        return crate::number_locale::d3_format(",.0~f", value)
            .map_err(|e| format!("default axis format ,.0~f: {e}"));
    };
    let divisor = 10f64.powi(ruler.exponent);
    let is_anchor = if ruler.anchor_at_start {
        idx == 0
    } else {
        idx + 1 == total
    };
    // Production's `digit_expr_combined` gates the currency symbol on
    // `prefix_present` (`vl_field_maps.py:722-726`): `datum.value !== 0`
    // when `ruler.prefix_repeats`, else `datum.value !== 0 && datum.index
    // === anchor` — independent of `mode`/`suffix_present` (below), which
    // is what the OLD unconditional `is_anchor` gate here missed (#8803:
    // "Plain-digit currency axis prefix ignores column_forming on
    // horizontal bar measure axis"). The "$" goes INTO the d3 spec, exactly
    // as the expr writes it, so a negative anchor renders "−$1.5", never
    // "$−1.5".
    let carries_prefix = value != 0.0 && (ruler.prefix_repeats || is_anchor);
    // A currency the locale places after the digits goes around the magnitude
    // word ("12.4M €", not "12.4 €M"): `composed_currency`'s lead precedes the
    // digits of the anchor tick, its trail follows the word. An authored affix
    // has no d3 slot: its prefix leads the anchor's digits, its suffix trails
    // the word ("12.4 M EUR"), both on the ticks the currency rides.
    let (lead, trail) = active_number_locale().composed_currency(&ruler.prefix, ruler.prefix_repeats);
    let composed = !lead.is_empty() || !trail.is_empty();
    let authored = labels_format.filter(|slot| !slot.prefix.is_empty() || !slot.suffix.is_empty());
    let authored_prefix = authored.map_or("", |slot| slot.prefix.as_str());
    let authored_suffix = authored.map_or("", |slot| slot.suffix.as_str());
    let scaled = value / divisor;
    let numeral = if carries_prefix && (!lead.is_empty() || !authored_prefix.is_empty()) {
        // The anchored prefix leads the sign (`painted_sign_placement`).
        let spec = if !ruler.prefix.is_empty() && !composed {
            spec_with_symbol(&ruler.digit_spec, &ruler.prefix)
        } else {
            ruler.digit_spec.clone()
        };
        let slot = FormatSlot {
            spec,
            prefix: format!("{authored_prefix}{lead}"),
            notation: authored.and_then(|slot| slot.notation.clone()),
            sign_placement: Some(if ruler.prefix_repeats {
                authored.map_or(SignPlacement::BeforePrefix, FormatSlot::placement)
            } else {
                SignPlacement::AfterPrefix
            }),
            ..FormatSlot::default()
        };
        slot.paint(scaled).map_err(|e| format!("ruler anchor spec {:?}: {e}", slot.spec))?
    } else if carries_prefix && !ruler.prefix.is_empty() && !composed {
        let spec = spec_with_symbol(&ruler.digit_spec, &ruler.prefix);
        crate::number_locale::d3_format(&spec, scaled)
            .map_err(|e| format!("ruler anchor spec {spec:?}: {e}"))?
    } else {
        crate::number_locale::d3_format(&ruler.digit_spec, scaled)
            .map_err(|e| format!("ruler digit_spec {:?}: {e}", ruler.digit_spec))?
    };
    let trail = if carries_prefix { trail } else { String::new() };
    let tail = if carries_prefix { authored_suffix } else { "" };
    let word = suffix_at_register(ruler.exponent, ruler.analytic()?)?;
    if ruler.mode == "repeat" {
        // Repeat mode's `suffix_present` is `datum.value !== 0` — every
        // non-zero tick, not just the anchor — at the ruler's own register
        // (`ResolvedRulerAxis.suffix_string`).
        return Ok(if value == 0.0 { numeral } else { format!("{numeral}{word}{trail}{tail}") });
    }
    if is_anchor {
        // The anchor's unit suffix yields to the reservation padding when
        // the anchor value is 0 — mirroring the labelExpr `(index === a ?
        // format(v, "$<spec>") : format(v, "<spec>")) + (v !== 0 && index
        // === a ? "<suffix_string>" : "<pad>")`.
        if value == 0.0 {
            Ok(format!("{numeral}{}", ruler.reservation))
        } else {
            Ok(format!("{numeral}{word}{trail}{tail}"))
        }
    } else {
        Ok(format!("{numeral}{}{trail}{tail}", ruler.reservation))
    }
}

// ── Axis style (the slice of ResolvedAxisStyle the scaffold reads) ────────

/// The authored chart-level sort (`ChartSort` in
/// `compile/models/chart/authored/_annotations.py`) — orders a categorical
/// channel's domain. Shared by bar.rs and scatter.rs, which apply it with
/// their own family's aggregate.
#[derive(Deserialize)]
pub struct ChartSort {
    pub by: String,
    pub order: SortDir,
}

#[derive(Deserialize, Clone, Copy, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum SortDir {
    Asc,
    Desc,
}

/// The per-category aggregate an authored `sort` ranks by — production's
/// `EncodingSortField.op` (`utils.py::x_domain_order(..., op=)`): `Sum` for
/// a stacked bar sorted by its own measure (`bar_sort_op`), `Min` for every
/// other bar sort and every other dimension axis
/// (`emitters/_cartesian.py::dimension_sort_to_vl` pins `op: "min"`).
#[derive(Clone, Copy, PartialEq)]
pub enum SortAggregate {
    Sum,
    Min,
}

/// Reorder `base_order` (a categorical domain in first-occurrence row
/// order) by an authored `sort`'s per-category aggregate — the one ranking
/// law bar, line/area, and heatmap all share, parameterized on `agg`
/// (`x_domain_order`'s own `op=` in production; bar calls this with
/// `bar_sort_op`'s verdict, line/area/heatmap with `Min`).
///
/// `pin_sorted_domain` bakes the resulting order as an explicit VL
/// `scale.domain` — so an unranked category (no value for the sort field on
/// any row) always TRAILS, on both directions, matching `x_domain_order`'s
/// own `ranked + unranked` return: pinning an explicit domain means Vega
/// just draws the array as given, never consulting its own native
/// sort-comparator placement for what's absent. Ties are stable
/// (`sort_by`), so ties keep first-occurrence order.
///
/// Under `Sum`, a numeric string parses (`row_num` tolerates it, matching
/// production's own `coerce_numeric_cell` under `op == "sum"`); any other
/// non-numeric value refuses by name here — a deliberate divergence, since
/// `coerce_numeric_cell` instead reads it as no value (drops that row, the
/// same as an absent one) — the same refusal bar.rs's own `CatOrder::Field`
/// doc comment documents. Under `Min` the sort value is compared as itself
/// (`utils.py::_js_min`): numbers numerically, strings by code point, a value
/// of the other kind passed over; keys of both kinds make Python's sort raise,
/// which `x_domain_order` answers with row order.
pub fn dimension_sort_domain(
    rows: &[serde_json::Map<String, Value>],
    field: &str,
    base_order: Vec<String>,
    sort: Option<&ChartSort>,
    agg: SortAggregate,
    family: &str,
) -> Result<Vec<String>, String> {
    let Some(sort) = sort else {
        return Ok(base_order);
    };
    let mut mins: std::collections::HashMap<String, Value> = std::collections::HashMap::new();
    let mut sums: std::collections::HashMap<String, f64> = std::collections::HashMap::new();
    for row in rows {
        let Some(raw) = row.get(&sort.by).filter(|v| !v.is_null()) else { continue };
        let cat = row_str(row, field);
        match agg {
            SortAggregate::Min => match mins.get_mut(&cat) {
                // `_js_min`: a value Python cannot compare against the min is passed over.
                Some(min) => {
                    if raw_x_order(raw, min).is_ok_and(std::cmp::Ordering::is_lt) {
                        *min = raw.clone();
                    }
                }
                None => {
                    mins.insert(cat, raw.clone());
                }
            },
            SortAggregate::Sum => {
                let val = row_num(row, &sort.by).filter(|v| v.is_finite()).ok_or_else(|| {
                    format!(
                        "{family}: sort by {:?} is not supported — Vega's `sum` over \
                         non-numeric values is not ported",
                        sort.by
                    )
                })?;
                *sums.entry(cat).or_insert(0.0) += val;
            }
        }
    }
    let keys: std::collections::HashMap<String, Value> = match agg {
        SortAggregate::Min => mins,
        SortAggregate::Sum => sums
            .into_iter()
            .map(|(cat, total)| {
                let total = serde_json::Number::from_f64(total)
                    .ok_or_else(|| format!("{family}: the sum of {:?} over {cat:?} is not finite", sort.by))?;
                Ok((cat, Value::Number(total)))
            })
            .collect::<Result<_, String>>()?,
    };
    let (mut ranked, unranked): (Vec<String>, Vec<String>) =
        base_order.iter().cloned().partition(|c| keys.contains_key(c));
    // A column of both kinds makes Python's sort raise: `x_domain_order` keeps row order.
    let numeric = ranked.iter().all(|c| matches!(keys[c], Value::Number(_) | Value::Bool(_)));
    let textual = ranked.iter().all(|c| keys[c].is_string());
    if !numeric && !textual {
        return Ok(base_order);
    }
    ranked.sort_by(|a, b| {
        let ord = raw_x_order(&keys[a], &keys[b]).expect("one kind of key is ordered");
        if sort.order == SortDir::Desc { ord.reverse() } else { ord }
    });
    ranked.extend(unranked);
    Ok(ranked)
}

#[derive(Deserialize, Clone)]
pub struct AxisStyle {
    pub grid: AxisGrid,
    #[serde(default)]
    pub line: Option<AxisLine>,
    pub ticks: AxisTicks,
    pub labels: AxisLabels,
    pub title: AxisTitle,
    pub position: Option<String>,
    #[serde(default)]
    pub scale: Value,
    /// Pre-baked tick positions from the Python resolver (may be empty).
    #[serde(default)]
    pub tick_values: Vec<f64>,
    pub domain_max: Option<f64>,
    pub domain_min: Option<f64>,
    /// Ruler overlay: when present, overrides labels.format for this axis.
    #[serde(default)]
    pub ruler: Option<RulerConfig>,
    /// Non-compacting sibling of `ruler` — set when the tick ladder does not
    /// abbreviate. Mutually exclusive with `ruler`.
    #[serde(default)]
    pub tick_label: Option<TickLabelConfig>,
    /// Temporal cadence field — unsupported in Rust renderer.
    #[serde(default)]
    pub time_unit: Option<Value>,
    /// Authored `axis_x.type` (`resolve_authored_x_type`, `type_inference.
    /// py`) — deserialize-only, read through `resolved_authored_type()`
    /// below, never directly: `"ordinal"` forces `resolve_cartesian_x_type`
    /// ordinal (and `pin_sorted_domain` sorts) on a shape this port's own
    /// categorical gate would otherwise treat as production-temporal and
    /// leave unsorted; `"temporal"` forces the OPPOSITE — `pin_sorted_
    /// domain` no-ops — on a shape this port's own gate would otherwise
    /// treat as categorical and sort.
    #[serde(default)]
    pub r#type: Option<String>,
    /// Fiscal-year-start month (1=Jan..12=Dec) for year/yearquarter bucket
    /// anchoring. Required: always present on the resolved axis (1 =
    /// calendar convention, the default for every axis except an authored
    /// x-axis override), the same way its non-defaulted neighbors
    /// (`grid`, `ticks`, `labels`, `position`) are — a fixture stale enough
    /// to lack it fails to deserialize loudly, matching every other
    /// always-baked theme value on this struct.
    pub fiscal_year_start_month: u8,
    /// Calendar unit (`year`, `yearquarter`, `yearmonth`, `yearweek`) to the
    /// strftime spec the board redefined that grain's `date_<grain>` name to
    /// (`ResolvedAxisStyle.grain_formats`); an unredefined grain is absent.
    #[serde(default)]
    pub grain_formats: std::collections::BTreeMap<String, String>,
    /// How production fills a synthesized (missing) time bucket's measures
    /// (`axis_x.fill`: `"null"`, `"zero"`, an interpolation); the band
    /// axis's bucket completion keys off it.
    #[serde(default)]
    pub fill: Option<String>,
    /// `false`/`null`, a bare `true`, or production's `ResolvedMirrorAxis`
    /// object (`{format, expr, ruler, tick_label}`) — read through
    /// [`AxisStyle::mirror_ghost`] (or its [`AxisStyle::mirror_requested`]
    /// form), never directly.
    #[serde(default)]
    pub mirror: Option<Value>,
    /// Whether this axis's channel is quantitative (numeric) rather than
    /// categorical — production's `ResolvedAxisStyle.is_quantitative`,
    /// baked once at resolve time from the same channel classification
    /// that decides format/label handling. Required, no default: always
    /// present on the resolved axis, the same way its non-defaulted
    /// neighbors (`grid`, `ticks`, `labels`, `position`) are.
    pub is_quantitative: bool,
    /// Whether this axis's floor is anchored at 0 — production's
    /// `ResolvedAxisStyle.zero_anchored`, resolve's own anchor DECISION
    /// (an explicit author pin, or unpinned data close enough to 0 for the
    /// axis to reach it on its own). Distinct from `is_zero_anchored()`
    /// above, which reads the AUTHORED `scale.continuous.zero: true`
    /// literal only — `zero_anchored` is the resolved outcome, `true` on
    /// strictly more axes (e.g. one that abstained on `zero` but still
    /// data-fit to 0). Required, no default, same rationale as
    /// `is_quantitative`.
    pub zero_anchored: bool,
}

/// The row a value-axis tick mark sits on: Vega's `tickRound` snaps the rung's
/// scale `position` to a whole pixel inside the axis group's crisp +0.5
/// translate, the same row the gridline takes. `origin` is the plot top in the
/// frame the mark is emitted in.
pub fn tick_row(origin: f64, position: f64) -> f64 {
    origin + position.round() + 0.5
}

/// A gridline or tick ink the resolve stage always fills; its absence refuses
/// by name rather than painting a guessed color.
pub fn required_ink<'a>(ink: Option<&'a str>, family: &str, field: &str) -> Result<&'a str, String> {
    ink.ok_or_else(|| format!("{family}: {field} absent (theme required)"))
}

/// The rows of a value axis's tick marks: one per rung inside the plot (a
/// pixel of slack), none when the ticks have no length. `origin` is the plot
/// top in the frame the marks are emitted in.
pub fn value_tick_rows(origin: f64, plot_h: f64, tick_len: f64, positions: impl IntoIterator<Item = f64>) -> Vec<f64> {
    if tick_len <= 0.0 {
        return Vec::new();
    }
    positions
        .into_iter()
        .map(|position| tick_row(origin, position))
        .filter(|&row| row >= origin - 1.0 && row <= origin + plot_h + 1.0)
        .collect()
}

/// One horizontal tick mark per row in `rows`, `len` long, running outward from
/// the axis `edge_x` (rightward when `outward` is positive, leftward when
/// negative), stroked with `ticks`' own color and width. Vega draws a mark per
/// rung on every axis whose `ticks` is true (`vega-parser/src/parsers/axis.js`).
pub fn emit_value_ticks(
    svg: &mut String,
    ticks: &AxisTicks,
    rows: impl IntoIterator<Item = f64>,
    edge_x: f64,
    outward: f64,
    len: f64,
) -> Result<(), String> {
    let (x1, x2) = if outward > 0.0 { (edge_x, edge_x + len) } else { (edge_x - len, edge_x) };
    let mut rows = rows.into_iter().peekable();
    if rows.peek().is_none() {
        return Ok(());
    }
    let color = required_ink(ticks.color.as_deref(), "axis", "ticks.color")?;
    let width = ticks.width.unwrap_or(1.0);
    for y in rows {
        let _ = std::fmt::Write::write_fmt(
            svg,
            format_args!(
                "<line x1=\"{x1:.2}\" y1=\"{y:.2}\" x2=\"{x2:.2}\" y2=\"{y:.2}\" \
                 stroke=\"{}\" stroke-width=\"{width}\"/>",
                xml_escape_attr(color)
            ),
        );
    }
    Ok(())
}

impl AxisStyle {
    /// The tick length this axis reserves and draws: `ticks.length` (default 6)
    /// while its ticks are visible, else 0. A zero length draws and reserves
    /// nothing, exactly like hidden ticks (Vega's `tickSize` is the mark's
    /// extent and the label offset, `axis.js:58-66`).
    pub fn tick_len(&self) -> f64 {
        if self.ticks.visible.unwrap_or(true) { self.ticks.length.unwrap_or(6.0) } else { 0.0 }
    }

    /// Whether the axis' domain line is authored visible (hidden by the theme).
    pub fn line_visible(&self) -> bool {
        self.line.as_ref().and_then(|l| l.visible) == Some(true)
    }

    /// The edge `axis_x.position` hangs the x axis from; Vega-Lite's default is the bottom.
    pub fn x_edge(&self) -> XEdge {
        match self.position.as_deref() {
            Some("top") => XEdge::Top,
            _ => XEdge::Bottom,
        }
    }

    /// Whether production's axis carries a `labelExpr` that reads `datum.value`
    /// (`inject_axis_numeral_expr`, `compose_axis_format`): a baked ruler or
    /// `tick_label`, an affix, a currency symbol, an SI notation, or one of the
    /// two natives with a Vega arm. Such an axis paints every tick itself, so a
    /// log axis keeps the labels d3's `tickFormat` would have blanked.
    pub fn composes_numeral_expr(&self) -> bool {
        if self.ruler.is_some() || self.tick_label.is_some() {
            return true;
        }
        let Some(slot) = &self.labels.format else { return false };
        let parts = d3rs::format::parse_parts(&slot.spec).ok();
        slot.has_affix_text()
            || matches!(slot.spec.as_str(), "bytes" | "duration")
            || parts.as_ref().is_some_and(|p| p.symbol)
            || (slot.notation.is_some() && parts.is_some_and(|p| p.type_ == 's'))
    }

    /// A quantitative tick of an axis whose emitter hands `labels.format` to
    /// Vega as `format` and composes no numeral expression (a vertical bar's
    /// continuous x): an axis with a baked ruler or `tick_label` ladder paints
    /// the bare spec (`axis_to_vl`), every other the zero-bare affixed digits
    /// (`compose_axis_format`).
    pub fn format_only_label(&self, slot: &FormatSlot, value: f64) -> Result<String, String> {
        if self.ruler.is_some() || self.tick_label.is_some() {
            crate::number_locale::d3_format(&slot.spec, value)
        } else {
            slot.paint_tick(value)
        }
    }

    /// Whether `style.axis_y.mirror` asks for a ghost axis that repaints the
    /// primary's labels; a ghost `format` refuses by name (see
    /// [`Self::mirror_ghost`] for the families that paint one).
    pub fn mirror_requested(&self, family: &str) -> Result<bool, String> {
        Ok(self.mirror_ghost(family)?.is_some())
    }

    /// The ghost axis `style.axis_y.mirror` asks for, `None` when there is none.
    ///
    /// Production bakes every truthy mirror into a `ResolvedMirrorAxis`
    /// (`resolved/_base.py`), so `mirror: true` arrives as
    /// `{format: null, expr: null, ruler, tick_label: null}` where `ruler` is
    /// built for the ghost's own edge (`axis_cascade.py:693-701`): a ruled
    /// primary gets an identical ghost ruler, while an unruled primary can still get a ruled
    /// ghost (a raw SI format or a non-tabular font). The ghost repaints the
    /// primary's labels unless it carries its own `format`, which only a
    /// family that `paints_format` accepts; an authored `expr`, the
    /// `tick_label` ladder that comes with a baked format, a ruled ghost
    /// format and a `ruler` that is not the primary's own are per-edge
    /// relabels this port has no model for. A bare `true` is the same
    /// request with nothing baked.
    pub fn mirror_ghost(&self, family: &str) -> Result<Option<MirrorGhost>, String> {
        let resolved = match self.mirror.as_ref() {
            None | Some(Value::Null) | Some(Value::Bool(false)) => return Ok(None),
            Some(Value::Bool(true)) => return Ok(Some(MirrorGhost { format: None, ruler: None })),
            Some(Value::Object(resolved)) => resolved,
            Some(other) => {
                return Err(format!(
                    "{family}: axis_y.mirror {other} is not a valid shape — expected true, false, null, \
                     or a resolved mirror object"
                ))
            }
        };
        if let Some(key) =
            resolved.keys().find(|k| !matches!(k.as_str(), "format" | "expr" | "ruler" | "tick_label"))
        {
            return Err(format!("{family}: axis_y.mirror carries {key:?}, which is not a ResolvedMirrorAxis field"));
        }
        let set = |key: &str| resolved.get(key).filter(|v| !v.is_null());
        for (key, why) in [
            ("expr", "an authored Vega expression"),
            ("tick_label", "a baked ladder for the ghost's own format"),
        ] {
            if set(key).is_some() {
                return Err(format!("{family}: axis_y.mirror.{key} ({why}) is not supported"));
            }
        }
        let ghost_ruler = match set("ruler") {
            None => None,
            Some(ruler) => Some(RulerConfig::deserialize(ruler).map_err(|e| {
                format!("{family}: axis_y.mirror.ruler is not a ResolvedRulerAxis: {e}")
            })?),
        };
        let Some(format) = set("format") else {
            return Ok(Some(MirrorGhost { format: None, ruler: ghost_ruler.filter(|g| Some(g) != self.ruler.as_ref()) }));
        };
        if ghost_ruler.is_some() {
            return Err(format!(
                "{family}: axis_y.mirror.format on a ruled axis is not supported (the ghost's ruler replaces \
                 its format, mirror_axis.py)"
            ));
        }
        let slot = FormatSlot::deserialize(format)
            .map_err(|e| format!("{family}: axis_y.mirror.format is not a ResolvedFormat: {e}"))?;
        Ok(Some(MirrorGhost { format: Some(slot), ruler: None }))
    }

    /// The transform of `scale.continuous.type`; Vega's defaults for the
    /// exponent (1) and the symlog constant (1) when unauthored.
    pub fn bend(&self) -> crate::vega_fit::Bend {
        use crate::vega_fit::Bend;
        let continuous = self.scale.get("continuous");
        let param = |kind: &str, key: &str| {
            continuous.and_then(|c| c.get(kind)).and_then(|p| p.get(key)).and_then(Value::as_f64).unwrap_or(1.0)
        };
        match continuous.and_then(|c| c.get("type")).and_then(Value::as_str) {
            Some("log") => Bend::Log,
            Some("pow") => Bend::Pow(param("pow", "exponent")),
            Some("sqrt") => Bend::Pow(0.5),
            Some("symlog") => Bend::Symlog(param("symlog", "constant")),
            _ => Bend::Linear,
        }
    }

    /// The authored `scale.values`, Vega's explicit tick positions.
    pub fn authored_scale_values(&self) -> Option<Vec<f64>> {
        self.scale.get("values")?.as_array().map(|a| a.iter().filter_map(Value::as_f64).collect())
    }

    /// The log scale's authored base, Vega's default 10 when unauthored.
    pub fn log_base(&self) -> f64 {
        self.scale
            .get("continuous")
            .and_then(|c| c.get("log"))
            .and_then(|l| l.get("base"))
            .and_then(Value::as_f64)
            .unwrap_or(10.0)
    }

    /// Returns true when the scale type is "log" (scale.continuous.type == "log").
    pub fn is_log_scale(&self) -> bool {
        self.scale
            .get("continuous")
            .and_then(|c| c.get("type"))
            .and_then(Value::as_str)
            == Some("log")
    }

    /// Production's `resolve_authored_x_type` (`type_inference.py:127-142`):
    /// the explicit `axis_x.type` when set to anything but `"auto"`, else
    /// `"temporal"` when `axis_x.scale.continuous.type: temporal` is
    /// authored as a second escape hatch for the same request — an author
    /// reaches for `scale.type`, not `type`, since that is the field VL
    /// itself calls "scale type". Only `"temporal"` participates in the
    /// scale escape hatch; there is no `scale.continuous.type: ordinal`
    /// equivalent (an author reaches for `type: ordinal` directly).
    pub fn resolved_authored_type(&self) -> Option<&str> {
        match self.r#type.as_deref() {
            None | Some("auto") => {
                let scale_temporal = self
                    .scale
                    .get("continuous")
                    .and_then(|c| c.get("type"))
                    .and_then(Value::as_str)
                    == Some("temporal");
                scale_temporal.then_some("temporal")
            }
            Some(t) => Some(t),
        }
    }

    /// True when the resolver baked either domain edge (`domain_min` /
    /// `domain_max`, production's `apply_domain_headroom_bounds`): Vega-Lite
    /// then drops `nice` from the compiled scale.
    pub fn has_baked_edge(&self) -> bool {
        self.domain_min.is_some() || self.domain_max.is_some()
    }

    /// Returns true when `scale.continuous.zero` is explicitly `false`.
    ///
    /// Narrower than production's `is_zero_anchored` inverse, which is also
    /// true for `None` and `"auto"` (`type_inference.py:952-961`) — this
    /// predicate is true only for an authored, literal `zero: false`. Correct
    /// for its one call site (`resolve_measure_domain`'s zoomed-axis gate);
    /// a second caller relying on it to cover `None`/`"auto"` would be wrong.
    pub fn is_zero_false(&self) -> bool {
        self.scale
            .get("continuous")
            .and_then(|c| c.get("zero"))
            .and_then(Value::as_bool)
            == Some(false)
    }

    /// Production's `is_zero_anchored` (`type_inference.py`): true only for
    /// a literal `scale.continuous.zero: true` — the one state whose axis
    /// pins its own `domainMin` instead of fitting the data's low edge.
    pub fn is_zero_anchored(&self) -> bool {
        self.scale
            .get("continuous")
            .and_then(|c| c.get("zero"))
            .and_then(Value::as_bool)
            == Some(true)
    }

    /// The authored pair as Vega's scale reads it: each end through JS
    /// `Number`, so a numeric string coerces and anything else is `NaN`
    /// (which leaves a scale with no ticks and every position at 0).
    pub fn coerced_continuous_domain(&self) -> Option<(f64, f64)> {
        let pair = self.scale.get("continuous").and_then(|c| c.get("domain")).and_then(Value::as_array)?;
        let [lo, hi] = pair.as_slice() else { return None };
        let end = |v: &Value| js_to_number(v).unwrap_or(f64::NAN);
        Some((end(lo), end(hi)))
    }

    /// The authored `scale.continuous.domain` `(lo, hi)`, or `None`.
    ///
    /// Production's single spelling of "did the author pin this axis's
    /// domain" (`authored_measure_domain`) — line/area's and scatter's zero
    /// rule both suppress on an authored domain excluding 0, and neither
    /// reads the baked `domain_min`/`domain_max` for that guard.
    pub fn authored_continuous_domain(&self) -> Option<(f64, f64)> {
        self.scale
            .get("continuous")
            .and_then(|c| c.get("domain"))
            .and_then(Value::as_array)
            .and_then(|a| match a.as_slice() {
                [lo, hi] => Some((lo.as_f64()?, hi.as_f64()?)),
                _ => None,
            })
    }
}

/// The `domainMin` a zero-anchored measure scale pins (`zero_anchor_pinned_floor`,
/// `type_inference.py`): its baked ladder's lowest rung, never an authored
/// `scale.values` (a statement about tick positions, not the domain). `None` pins
/// nothing: a literal 0 would sit at the bottom of an all-negative domain.
pub fn zero_anchor_pinned_floor(axis: &AxisStyle) -> Option<f64> {
    let authored_values = axis.scale.get("values").is_some_and(|v| !v.is_null());
    if authored_values { None } else { axis.tick_values.first().copied() }
}

/// Production's `effective_measure_domain` (`emitters/_cartesian.py`): the
/// `(lo, hi)` a measure axis actually renders, in the SAME branch order —
/// the one function `unity_rule_fires` and `x_threshold_fires` both call,
/// so the two gates cannot drift on where an axis starts and ends:
///
/// 1. **Authored domain** wins outright, regardless of `data_extent`.
/// 2. **Baked `domain_min`/`domain_max`** — the headroom-expanded edges,
///    read per-edge (resolve bakes only the top on a zero-anchored axis).
/// 3. **The zero-anchor floor**, for a low edge resolve left unbaked: an
///    axis whose AUTHORED `scale.continuous.zero: true` pin
///    (`is_zero_anchored`) reads the baked ladder's own first rung
///    (`tick_values.first()`) — but never when `scale.values` is set (an
///    authored tick list states positions, not a domain bound,
///    `zero_anchor_domain_floor`'s own provenance rule) — else
///    `0.0.min(data_lo)`, never a bare `0.0`: that literal is a floor only
///    while the data stays non-negative.
/// 4. **The data extent**, what Vega-Lite auto-fits an unpinned axis to.
///
/// On an unauthored x-axis branches 2 and 3 never engage (resolve bakes
/// neither `domain_min`/`domain_max` nor a zero-anchor floor for x today),
/// reducing this to "authored domain, else the data extent" there — but
/// that reduction is a CONSEQUENCE of what resolve doesn't bake, not
/// something this function special-cases; an x-side headroom/zero-anchor
/// bake added later is read correctly with no change here.
///
/// `data_extent` is `None` when the measure column carried no numeric
/// rows — mirrors production's `data_extent is None` early return, which
/// bails out even when `domain_max` IS baked (nothing to pair it with).
pub fn effective_measure_domain(
    axis: &AxisStyle,
    data_extent: Option<(f64, f64)>,
) -> Option<(f64, f64)> {
    if let Some((a, b)) = axis.authored_continuous_domain() {
        return Some((a.min(b), a.max(b)));
    }
    let (data_lo, data_hi) = data_extent?;
    let hi = axis.domain_max.unwrap_or(data_hi);
    let lo = if let Some(domain_min) = axis.domain_min {
        domain_min
    } else if axis.is_zero_anchored() {
        zero_anchor_pinned_floor(axis).unwrap_or_else(|| 0.0_f64.min(data_lo))
    } else {
        data_lo
    };
    Some((lo, hi))
}

/// Production's `_resolved_percent_format` (`baseline.py:119`): whether the
/// resolved axis label format is percent-shaped.
pub fn measure_is_percent(ax_y: &AxisStyle) -> bool {
    ax_y.labels.spec().is_some_and(|f| f.contains('%'))
}

/// Production's `_apply_unity` gate (`baseline.py`): whether a `datum: 1`
/// rule fires on a percent-formatted measure axis — `_y_carries_the_
/// measure` (`ax_y.is_quantitative`) folded in here, not left to the
/// caller, since production's own gate owns that check. `data_min`/`data_max`
/// are the measure column's raw extent (`f64::INFINITY`/`NEG_INFINITY` when
/// nothing parsed) — `effective_measure_domain`'s last-resort edge.
pub fn unity_rule_fires(ax_y: &AxisStyle, data_min: f64, data_max: f64) -> bool {
    if !ax_y.is_quantitative {
        return false;
    }
    if !(measure_is_percent(ax_y) && ax_y.grid.visible.unwrap_or(false) && ax_y.grid.threshold_visible()) {
        return false;
    }
    let data_extent = data_min.is_finite().then_some((data_min, data_max));
    match effective_measure_domain(ax_y, data_extent) {
        Some((lo, hi)) => lo <= 1.0 && 1.0 <= hi,
        None => false,
    }
}

/// Production's `BaselineFeature._apply_x_threshold` gate (`baseline.py`):
/// whether a `datum: 0` (`require_percent: false`) or `datum: 1`
/// (`require_percent: true`) rule fires on a quantitative x axis —
/// independent of the measure-axis zero/unity rules above, since a chart
/// can carry both a quantitative x AND a quantitative y (an ordinary x-y
/// scatter). `axis_x.is_quantitative` folded in here, mirroring
/// production's own gate. `require_percent` reads `ax_x`'s OWN resolved
/// label format (`_resolved_percent_format(axis_x)`). `data_min`/`data_max`
/// are the x column's raw extent — `effective_measure_domain`'s last-resort edge, same law
/// the unity gate above shares, never re-derived.
pub fn x_threshold_fires(
    ax_x: &AxisStyle,
    require_percent: bool,
    datum: f64,
    data_min: f64,
    data_max: f64,
) -> bool {
    if !ax_x.is_quantitative || ax_x.is_log_scale() {
        return false;
    }
    if require_percent && !ax_x.labels.spec().is_some_and(|f| f.contains('%')) {
        return false;
    }
    if !ax_x.grid.visible.unwrap_or(false) || !ax_x.grid.threshold_visible() {
        return false;
    }
    let data_extent = data_min.is_finite().then_some((data_min, data_max));
    match effective_measure_domain(ax_x, data_extent) {
        Some((lo, hi)) => lo <= datum && datum <= hi,
        None => false,
    }
}

/// The axis domain line (VL `domain`), hidden by the theme.
#[derive(Deserialize, Clone)]
pub struct AxisLine {
    pub visible: Option<bool>,
    pub width: Option<f64>,
    pub color: Option<String>,
}

#[derive(Deserialize, Clone)]
pub struct AxisGrid {
    pub visible: Option<bool>,
    pub opacity: Option<f64>,
    /// Authored gridline dash pattern (VL `gridDash`).
    #[serde(default)]
    pub dash: Option<Vec<f64>>,
    pub width: Option<f64>,
    pub color: Option<String>,
    /// Production's `ResolvedAxisGridStyle.threshold` (renamed from `zero`
    /// by #8343, generalized to every axis, not just the measure one)
    /// — the baseline/threshold reference-line style. `Option` for the same
    /// leniency as this struct's other fields; a production payload always
    /// bakes it.
    #[serde(default)]
    pub threshold: Option<AxisGridThreshold>,
    /// `None` draws no tear.
    #[serde(default)]
    pub torn_baseline: Option<TornBaseline>,
}

/// RJ Andrews' "tear up your baseline": on a line chart whose y domain
/// excludes zero, the edge nearest the off-screen zero is drawn as a wave
/// so it cannot be read as a floor at zero. Every other family, and the
/// x-axis, ignores it.
#[derive(Deserialize, Clone)]
pub struct TornBaseline {
    pub visible: bool,
    /// Crest height above the edge, in px.
    pub amplitude: f64,
    /// One full wave, in px.
    pub wavelength: f64,
    /// 0 is a sine, 1 a zigzag; between blends the two.
    pub sharpness: f64,
}

impl AxisGrid {
    /// Production's `ResolvedAxisGridThresholdStyle.visible` — the
    /// independent off-switch `_insert_zero_rule`/`_insert_top_rules`/
    /// `_apply_unity`/`_apply_x_threshold` all check (`baseline.py`): it
    /// silences the baseline rule without hiding this axis's other
    /// gridlines, which `grid.visible` alone could do before. Defaults to
    /// `true` when a threshold style is present but doesn't spell out
    /// `visible` (production's own baked theme default), `false` when no
    /// threshold style resolved onto this axis at all.
    pub fn threshold_visible(&self) -> bool {
        self.threshold.as_ref().is_some_and(|t| t.visible.unwrap_or(true))
    }
}

#[derive(Deserialize, Clone)]
pub struct AxisGridThreshold {
    pub color: Option<String>,
    pub width: Option<f64>,
    pub visible: Option<bool>,
}

#[derive(Deserialize, Clone)]
pub struct AxisTicks {
    pub visible: Option<bool>,
    pub color: Option<String>,
    pub width: Option<f64>,
    pub length: Option<f64>,
    pub count: Option<f64>,
    /// The resolved measure-axis `ticks.step`: Vega-Lite's `tickMinStep`,
    /// set where the axis's fixed-decimal format cannot tell closer ticks
    /// apart and no ladder is baked (`axis_cascade.py` `tick_step`,
    /// `bake_tick_ladder` in `vl_field_maps.py`). Read by the unbaked
    /// measure-axis ladders, through `cartesian::unbaked_ticks`, and, beside
    /// `time_unit`, as the authored calendar step of a temporal x axis.
    #[serde(default)]
    pub step: Option<f64>,
    /// The authored x-axis `ticks.time_unit`: a calendar-bucketing grain that
    /// names the d3 interval Vega draws ticks at (with `step`), production's
    /// `axis.tickCount: {interval, step}` (`type_inference.py:1026-1052`).
    /// Read by every family that draws a temporal x axis; a quantitative or
    /// band x ignores it, as production's paths that skip
    /// `build_cartesian_x_encoding` do.
    #[serde(default)]
    pub time_unit: Option<String>,
    /// Axis-group offset from the plot edge — production emits this as the
    /// VL `axis.offset` (1.0 on the themed category axis, absent on value
    /// axes), so it shifts the whole label gutter outward.
    #[serde(default)]
    pub offset: Option<f64>,
}

#[derive(Deserialize, Clone)]
pub struct AxisLabels {
    pub font: Font,
    pub padding: Option<f64>,
    /// Authored `labels.values`: the dates whose labels the axis keeps.
    #[serde(default)]
    pub values: Option<Vec<Value>>,
    #[serde(default)]
    pub format: Option<FormatSlot>,
    pub visible: Option<bool>,
    /// Authored label-width cap (VL `labelLimit`); `None` means Vega-Lite's
    /// own 180px default (`DEFAULT_VL_LABEL_LIMIT`) applies.
    #[serde(default)]
    pub max_width: Option<f64>,
    /// Minimum gap between adjacent labels; used by bar height derivation.
    #[serde(default)]
    pub min_gap: Option<f64>,
    /// Authored label anchor (VL `labelAlign`); `None` is Vega-Lite's
    /// away-from-plot default.
    #[serde(default)]
    pub align: Option<String>,
    /// Authored label angle (VL `labelAngle`); wins over the tilt ladder.
    #[serde(default)]
    pub angle: Option<f64>,
    /// The crowding remedies production's `resolve_axis_x_overlap` may
    /// apply; `None` disables both and keeps `angle`.
    #[serde(default)]
    pub overlap: Option<LabelOverlap>,
    /// The angles `overlap.tilt` walks, shallowest first (theme-baked).
    #[serde(default)]
    pub tilt_increments: Option<Vec<f64>>,
    /// Authored label cadence override (e.g. "yearmonth" on daily data).
    #[serde(default)]
    pub time_unit: Option<String>,
    /// Authored override of Vega's default edge-label flushing on a
    /// temporal x-axis (`type_inference.temporal_edge_labels_flushed`:
    /// `axis.labels.flush is not False`). Typed as the documented union
    /// (`bool | float | None`, theme/axis.py:418) — Vega's `labelFlush`
    /// also accepts a pixel threshold (`styling.md:770`), so this must not
    /// narrow to `bool` or a numeric `flush: 4` panics deserialization.
    /// `None` means "not authored" — the caller's own default (flushed for
    /// a continuous temporal axis) applies; only a literal `false` turns
    /// flushing off.
    #[serde(default)]
    pub flush: Option<Value>,
    /// The clock a sub-day axis reads (`24` or `12`; `None` reads the 12-hour one).
    #[serde(default)]
    pub clock: Option<i64>,
    /// Authored VL `labelExpr` (B5) — production compiles it from
    /// `axis_x.labels.expr` and rewrites every category label BEFORE it is
    /// measured and drawn. Evaluated by [`crate::labels_expr`].
    #[serde(default)]
    pub expr: Option<String>,
}

impl AxisLabels {
    /// Whether the labels stand upright, the one layout a family that paints no tilt has.
    pub fn upright(&self) -> bool {
        self.angle.unwrap_or(0.0) == 0.0
    }

    /// What Vega's label mark fixes for these labels, which a `labels.expr` reads off `item`.
    pub fn item_style(&self) -> Result<crate::labels_expr::view::ItemStyle, String> {
        use crate::labels_expr::Val;
        let styled_text = |s: String| crate::labels_expr::Text::new(s).map_err(|e| crate::labels_expr::describe(&e, "axis labels.font"));
        let font = &self.font;
        let weight = match font.weight.as_ref().ok_or("axis labels.font.weight absent (theme required)")? {
            Value::Number(n) => Val::Num(n.as_f64().ok_or("axis labels.font.weight is not a number")?),
            Value::String(w) => match w.parse() {
                Ok(n) => Val::Num(n),
                Err(_) => Val::string(w.clone()).map_err(|e| crate::labels_expr::describe(&e, "axis labels.font.weight"))?,
            },
            other => return Err(format!("axis labels.font.weight {other} is neither a number nor a string")),
        };
        Ok(crate::labels_expr::view::ItemStyle {
            font: styled_text(font.family.clone().ok_or("axis labels.font.family absent (theme required)")?)?,
            size: font.size.ok_or("axis labels.font.size absent (theme required)")?,
            fill: styled_text(font.color.clone().ok_or("axis labels.font.color absent (theme required)")?)?,
            weight,
            limit: self.max_width.unwrap_or(crate::vega_fit::DEFAULT_VL_LABEL_LIMIT),
        })
    }

    /// The authored `labels.align` production still sends to Vega: an authored
    /// `labels.expr` cannot be measured, so `measure_axis_to_vl` drops an own-side
    /// or centered align and the labels take Vega-Lite's away-from-plot default.
    pub fn align_kept(&self, own_side: &str) -> Option<&str> {
        match self.align.as_deref() {
            Some(a) if self.expr.is_some() && (a == own_side || a == "center") => None,
            a => a,
        }
    }

    /// A value tick's text: `label` (the axis's own formatted text) unless an
    /// authored `labels.expr` rewrites it, with `datum.value` the tick's number.
    /// The expr wins over the composed label, so an authored format keeps only its
    /// d3 digits as `datum.label` (`compose_axis_format`): its affix is dropped.
    pub fn apply_expr(&self, scale: &'static str, value: f64, label: String, index: usize, count: usize) -> Result<String, String> {
        let Some(src) = self.expr.as_deref() else { return Ok(label) };
        let label = match self.spec() {
            Some(spec) => crate::number_locale::d3_format(spec, value).map_err(|e| format!("labels.format {spec:?}: {e}"))?,
            None => label,
        };
        crate::labels_expr::speak_value(scale, src, value, &label, index, count)
    }

    /// The text Vega draws for an axis label: each line cut to the axis's `labelLimit` with an ellipsis.
    pub fn clip(&self, text: &str, fonts: &crate::text::FontBook) -> Result<String, String> {
        let family = self.font.family.as_deref().unwrap_or("sans-serif");
        let size = self.font.size_or(11.0);
        let limit = self.max_width.unwrap_or(crate::vega_fit::DEFAULT_VL_LABEL_LIMIT);
        text.split(crate::text::LINE_SEP)
            .map(|line| truncate_label(line, limit, family, size, fonts, true))
            .collect::<Result<Vec<_>, _>>()
            .map(|lines| lines.join(&crate::text::LINE_SEP.to_string()))
    }

    /// The d3 spec of the authored format, for a reader that paints digits
    /// alone: its affix is painted by [`format_y_label`] on a plain value
    /// axis; an authored `labels.expr` drops it.
    pub fn spec(&self) -> Option<&str> {
        self.format.as_ref().map(|f| f.spec.as_str())
    }
}

/// B6 — Vega's `labelLimit` truncation (vega-scenegraph's `truncate`): the
/// longest character prefix that FITS is picked by measuring
/// `prefix.trim_end() + "…"` (Vega ignores a candidate's trailing
/// whitespace when testing the cutoff) at or under `limit` — but the
/// EMITTED string keeps the prefix untrimmed, so a word-boundary limit
/// still draws the space before the ellipsis (`"Emerging …"`, never
/// `"Emerging…"`). The reserved gutter/ink a caller measures off the
/// returned string therefore agrees with what's drawn by construction.
/// Returns `text` unchanged when it already fits; the bare ellipsis when
/// nothing else does.
///
/// `shaped` picks the measurer per the "which measurer" law: `false`
/// (raw advance sums) for a width PRODUCTION bakes into the spec as a
/// number (an axis's own `labelLimit`, computed and baked ahead of
/// render — the two existing callers); `true` (rustybuzz/GPOS, what
/// vl-convert's own layout measures) for a width VEGA measures itself at
/// render time — a title/subtitle text mark, whose `limit` Vega enforces
/// natively regardless of what any Python pre-pass decided.
pub fn truncate_label(
    text: &str,
    limit: f64,
    family: &str,
    size: f64,
    fonts: &crate::text::FontBook,
    shaped: bool,
) -> Result<String, String> {
    let measure = |s: &str| {
        if shaped { fonts.measure_shaped(s, family, size) } else { fonts.measure(s, family, size) }
    };
    if measure(text)? <= limit {
        return Ok(text.to_string());
    }
    let chars: Vec<char> = text.chars().collect();
    let (mut lo, mut hi, mut best) = (0usize, chars.len(), None::<String>);
    while lo <= hi {
        let mid = (lo + hi) / 2;
        let prefix: String = chars[..mid].iter().collect();
        // Vega's own `truncate` selects the cutoff by measuring the prefix
        // with its trailing whitespace ignored, then draws the prefix
        // UNTRIMMED — at a word-boundary limit the emitted string still
        // carries the space before the ellipsis.
        let measure_cand = format!("{}\u{2026}", prefix.trim_end());
        if measure(&measure_cand)? <= limit {
            best = Some(format!("{prefix}\u{2026}"));
            lo = mid + 1;
        } else {
            if mid == 0 {
                break;
            }
            hi = mid - 1;
        }
    }
    Ok(best.unwrap_or_else(|| "\u{2026}".to_string()))
}

#[derive(Deserialize, Clone, Default)]
pub struct LabelOverlap {
    #[serde(default)]
    pub skip: bool,
    #[serde(default)]
    pub tilt: bool,
}

#[derive(Deserialize, Clone)]
pub struct AxisTitle {
    pub font: Font,
    pub padding: Option<f64>,
    pub visible: Option<bool>,
}

// ── Title (shared shape across families) ──────────────────────────────────

#[derive(Deserialize, Clone)]
pub struct TitleStyle {
    pub font: Font,
    /// Resolved title placement (`title_style.position`) — `offset` is the
    /// gap Vega leaves between the title ink and the view bounds (VL
    /// `title.offset`, themed at 10.0).
    #[serde(default)]
    pub position: Option<TitlePosition>,
    /// Resolved subtitle slot (`title_style.subtitle`).
    #[serde(default)]
    pub subtitle: Option<SubtitleStyle>,
    /// Overflow mode for the title text (`title_style.overflow`:
    /// clip / truncate / wrap / wrap-two; production defaults to
    /// wrap-two — title_overflow.py `DEFAULT_TITLE_OVERFLOW`).
    #[serde(default)]
    pub overflow: Option<String>,
}

/// The title style a family should actually read.
///
/// An OBJECT title is sized by the width of its own card, never by board
/// nesting — a 342px card titles at 11px/600 Inter where the un-tiered
/// `title_style.font` still reads 18px serif, and `_NARROW_MAX` switches
/// the FAMILY too (`resolve_title_font` returns the body family below it).
/// Production resolves all of that (`chart_title_spec`) and hands the answer
/// over in `style.title_font`; everything else — position, subtitle,
/// overflow — stays with `title_style`.
///
/// Returns `None` when the envelope carries no resolved font, so callers
/// fall back with `.or(r.title_style.as_ref())`.
pub fn effective_title_style(
    title_font: Option<&Font>,
    title_style: Option<&TitleStyle>,
) -> Option<TitleStyle> {
    Some(TitleStyle {
        font: title_font?.clone(),
        position: title_style.and_then(|ts| ts.position.clone()),
        subtitle: title_style.and_then(|ts| ts.subtitle.clone()),
        overflow: title_style.and_then(|ts| ts.overflow.clone()),
    })
}

/// The size `wrap_title_block`'s pre-pass measures at — production's
/// `apply_title_overflow_to_spec`: `style_font_size or title_font_size or
/// config_font_size or 18`. Every render call site passes no
/// `title_font_size` kwarg, and `config.title.fontSize` is baked from the
/// same `chart_style_context.title` object as `title_style` itself
/// (`vega_lite/mapping.py`'s title block reads `chart_style_context.title`
/// directly), so the precedence collapses to the UN-TIERED `title_style`'s
/// own `font.size`, never the width-resolved `style.title_font` a caller
/// paints with.
pub fn title_measure_font_size(title_style: Option<&TitleStyle>) -> f64 {
    title_style.and_then(|ts| ts.font.size).unwrap_or(18.0)
}

#[derive(Deserialize, Clone)]
pub struct SubtitleStyle {
    pub font: Font,
    /// Overflow mode for the subtitle (same vocabulary as the title's).
    #[serde(default)]
    pub overflow: Option<String>,
}

#[derive(Deserialize, Clone)]
pub struct TitlePosition {
    #[serde(default)]
    pub offset: Option<f64>,
}

/// `ResolvedChart.canvas`: the chart's opaque composited canvas, always the
/// 6-digit `#rrggbb` hex `rgb01_to_hex` emits (`compile/resolve/style/palette.py:
/// 1383` `ink_canvas`, `colors.py:441`). Anything else is a payload that was
/// never composited, and knockout ink read from it would be a guess.
pub(crate) fn de_canvas<'de, D>(d: D) -> Result<String, D::Error>
where
    D: serde::Deserializer<'de>,
{
    use serde::Deserialize;
    let canvas = Option::<String>::deserialize(d)?.ok_or_else(|| {
        serde::de::Error::custom("canvas is null, not an opaque #rrggbb color")
    })?;
    let is_hex = canvas.len() == 7
        && canvas.starts_with('#')
        && canvas[1..].bytes().all(|b| b.is_ascii_hexdigit());
    if is_hex {
        Ok(canvas)
    } else {
        Err(serde::de::Error::custom(format!(
            "canvas {canvas:?} is not an opaque #rrggbb color"
        )))
    }
}

/// Deserialize a title/subtitle: an EMPTY string is no title.
///
/// The envelope carries `""` for an unauthored title — production's title
/// block is then absent from the spec entirely (VL emits `"title": null`
/// for `text: ""`), so nothing paints and no band is reserved. Normalizing
/// here rather than at each use site is the point: the guard was
/// previously applied at two of the four bar/scatter title reads, and the
/// two that missed it reserved a phantom 28px band under an empty `<text>`
/// element on eight corpus cases.
///
/// EXACTLY empty, never whitespace-trimmed: production gates on Python
/// truthiness (`set_chart_title`), and `"   "` is truthy, so a
/// whitespace title survives into the spec and reserves its full band —
/// measured, scenegraph origin `[30, 19]` for `"   "` against `[30, 2]`
/// for `""`. Trimming here shifted such a chart 28px off the reference.
pub fn de_nonempty_string<'de, D>(d: D) -> Result<Option<String>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    use serde::Deserialize;
    Ok(Option::<String>::deserialize(d)?.filter(|s| !s.is_empty()))
}

/// Authored value labels (`style.marks.<mark>.labels` → the resolved
/// `labels` slice) — production's one `value_labels` feature emits these as
/// a text layer for bar, line, area and scatter alike, so the payload slice
/// is one shape whatever family reads it. The position VOCABULARY differs
/// per family (`BAR_POS_MAP_VERTICAL` vs `_LINE_POS_MAP`), and stays with
/// the family that draws it.
#[derive(Deserialize, Clone)]
pub struct MarkLabels {
    pub visible: bool,
    /// Label a different column than the measure.
    #[serde(default)]
    pub field: Option<String>,
    #[serde(default)]
    pub format: Option<FormatSlot>,
    #[serde(default)]
    pub dx: Option<f64>,
    #[serde(default)]
    pub dy: Option<f64>,
    #[serde(default)]
    pub font: Option<Font>,
    #[serde(default)]
    pub position: Option<String>,
    /// `false` unlabels the segments of a stacked bar; not ported, so a bar
    /// refuses it by name (`families/bar.rs::refuse_unmodeled_bar_marks`).
    #[serde(default)]
    pub stacked: Option<bool>,
}

impl MarkLabels {
    /// `labels_draw_text`: whether `labels.field` names a column holding a
    /// non-number, so every value prints verbatim. An unset field labels the
    /// numeric measure; a column with no value anywhere stays on the number path.
    pub fn field_is_text(&self, rows: &[serde_json::Map<String, Value>]) -> bool {
        self.field.as_deref().is_some_and(|field| {
            !rows.iter().filter_map(|row| row.get(field)).filter(|v| !v.is_null()).all(Value::is_number)
        })
    }

    /// `(text, blank)` of one row's label. `labels.field` prints that column,
    /// formatted when every value is a number, verbatim when any is text, blank
    /// where it is null (`_house_register_text_encoding`'s `condition`); an unset
    /// field prints the formatted measure `value`. The error carries no family prefix.
    pub fn field_text(
        &self,
        is_house: bool,
        field_is_text: bool,
        row: Option<&serde_json::Map<String, Value>>,
        value: f64,
    ) -> Result<(String, bool), String> {
        let Some(field) = self.field.as_deref() else {
            return Ok((format_mark_label(self.format.as_ref(), value, is_house)?, false));
        };
        match row.and_then(|row| row.get(field)).filter(|v| !v.is_null()) {
            None => Ok((String::new(), true)),
            Some(v) if field_is_text => Ok((crate::href::js_string(Some(v))?, false)),
            Some(v) => {
                let number = v.as_f64().ok_or_else(|| format!("labels.field {field:?} is not a number"))?;
                Ok((format_mark_label(self.format.as_ref(), number, is_house)?, false))
            }
        }
    }
}

/// The text a value label paints for one point — shared by each family's
/// emission and by the fit closure that has to measure the SAME text to
/// reserve its ink before the plot rect is known. No authored format → the
/// VL text encoding renders the raw quantitative value (Vega string
/// coercion); production backfills a format at resolve time
/// (`_label_format_fallback`), so a payload reaching here with
/// `format: null` is the rare unresolved shape, not the common path.
///
/// The error carries no family prefix — callers add their own.
pub fn format_mark_label(fmt: Option<&FormatSlot>, value: f64, is_house: bool) -> Result<String, String> {
    match fmt {
        Some(slot) => {
            let spec = slot.spec.as_str();
            // Production's `_house_register_expr` (`value_labels.py:335`) over
            // `sub_unit_guarded_vega_expr` (`numeral_expr.py:106`): an
            // SI-shaped format alias (`resolve_label_format` set `is_house`)
            // paints a sub-1 value through the SI spec's `r` sibling — d3's
            // milli/micro prefixes are not house suffixes — and below
            // `SUB_UNIT_SCIENTIFIC_FLOOR` (nonzero) through its `e` sibling,
            // which `d3rs` does not port and so refuses by name. From 1 up the
            // letter is spelled at the authored notation, else the narrative
            // register (`_label_notation`); a literal d3 spec, or any non-SI
            // format, paints the raw d3 output.
            if is_house && spec.ends_with('s') {
                if value.abs() < 1.0 {
                    let (digit, scientific) = d3rs::format::sub_unit_specs(spec)
                        .map_err(|e| format!("mark label format {spec:?}: {e}"))?;
                    let sub_unit = if value != 0.0 && value.abs() < d3rs::format::SUB_UNIT_SCIENTIFIC_FLOOR { scientific } else { digit };
                    return slot.paint_as(&sub_unit, None, value);
                }
                return slot.paint_as(spec, Some(slot.notation.as_deref().unwrap_or("narrative")), value);
            }
            slot.paint(value)
        }
        None => {
            if value.fract() == 0.0 {
                Ok(format!("{}", value as i64))
            } else {
                Ok(format!("{value}"))
            }
        }
    }
}

/// A title block after production's overflow processing: the drawn lines
/// plus the `TitleInk` the fit margins reserve for them.
pub struct TitleBlock {
    pub title_lines: Vec<String>,
    pub sub_lines: Vec<String>,
    /// `None` when there is no title — VL emits no title block, so nothing
    /// paints and no band is reserved.
    pub ink: Option<crate::vega_fit::TitleInk>,
    /// Whether production's pre-pass cut the title / subtitle
    /// (`prepare_title_text`'s `truncated`); Vega's native clip below it
    /// does not count.
    pub title_truncated: bool,
    pub subtitle_truncated: bool,
}

/// Production's `apply_title_overflow_to_spec`, run before the chart render:
/// `limit = int(width − pad_l − pad_r)`, the title wrapped/truncated per its
/// own `overflow` mode and the subtitle per its own (both default
/// `wrap-two`), each extra line advancing the title block by
/// `fontSize + 2` — Vega's default text lineHeight.
///
/// `cased_title` is the title with `font.case` already applied (production
/// applies the case at spec-build time; the subtitle never takes one).
/// Both wraps measure in Inter whatever family the text paints in: the two
/// production call sites pass no family, so `get_font_measurer(None)`
/// resolves InterVariable regardless.
///
/// `title_font_size` is the size the title PAINTS at (the width-resolved,
/// tiered `style.title_font.size`) — it drives the reserved `ink` and the
/// native `limit` clip pass, both of which must match what actually
/// renders. `title_measure_font_size` is production's own pre-pass size
/// (`title_overflow.py::apply_title_overflow_to_spec`'s `style_font_size or
/// title_font_size or config_font_size or 18` — the UN-TIERED
/// `title_style.font.size`, `title_measure_font_size` in this port): it
/// decides how many lines the wrap produces and, absent an authored
/// subtitle size, what the subtitle measures/paints at too — production's
/// subtitle fallback reads the same effective title size, never the
/// tiered paint size.
pub fn wrap_title_block(
    cased_title: Option<&str>,
    subtitle: Option<&str>,
    style: Option<&TitleStyle>,
    title_font_size: f64,
    title_measure_font_size: f64,
    avail_w: f64,
    fonts: &crate::text::FontBook,
) -> Result<TitleBlock, String> {
    const WRAP_MEASURE_FAMILY: &str = "Inter";
    // `compute_title_limit`: `int(width − l − r)`, and no processing at all
    // when that is non-positive.
    let limit = (avail_w.trunc() > 0.0).then(|| avail_w.trunc());
    let wrap = |text: &str, mode: Option<&str>, font_size: f64, limit: f64| {
        prepare_title_lines(text, mode, limit, font_size, WRAP_MEASURE_FAMILY, fonts)
    };
    let (wrap_lines, title_truncated) = match (cased_title, limit) {
        (None, _) => (vec![], false),
        (Some(text), None) => (vec![text.to_string()], false),
        (Some(text), Some(limit)) => wrap(
            text,
            style.and_then(|ts| ts.overflow.as_deref()),
            title_measure_font_size,
            limit,
        )?,
    };
    let sub_style = style.and_then(|ts| ts.subtitle.as_ref());
    // Production's `prepare_title_text` callers fall back to the effective
    // TITLE size when the subtitle slot carries none — the same fallback
    // `emit_subtitle_vega` paints with, so box and text agree. That
    // effective size is the pre-pass MEASURE size, never the tiered paint
    // size (`title_overflow.py`'s `subtitle_font_size = ... or
    // effective_title_font_size`).
    let sub_fs = sub_style.and_then(|f| f.font.size).unwrap_or(title_measure_font_size);
    let (wrap_sub_lines, subtitle_truncated) = match (wrap_lines.is_empty(), subtitle, limit) {
        (false, Some(sub), None) => (vec![sub.to_string()], false),
        (false, Some(sub), Some(limit)) => {
            wrap(sub, sub_style.and_then(|f| f.overflow.as_deref()), sub_fs, limit)?
        }
        _ => (vec![], false),
    };
    let family = style.and_then(|ts| ts.font.family.as_deref()).unwrap_or("serif");
    let sub_family = sub_style.and_then(|f| f.font.family.as_deref()).unwrap_or("sans-serif");
    let ink = if wrap_lines.is_empty() {
        None
    } else {
        let offset = style
            .and_then(|ts| ts.position.as_ref())
            .and_then(|pos| pos.offset)
            .unwrap_or(10.0);
        // An overflowing row reserves the `limit` the spec carries, exactly
        // — Vega bounds the mark at its limit, not at the ellipsized string
        // it drew (probe: limit 320, the truncated string measures 314
        // standalone, the laid-out title reserves 320.0). The wrap measures
        // in Inter and the ink in the row's own family, so a wider family
        // can keep one line and still overrun. Measured off `wrap_lines`
        // (pre-native-clip) — the reservation is unaffected by whether the
        // DRAWN text below gets shortened further; it was already `min`-ed
        // to `limit` here regardless.
        let cap = |w: f64| limit.map_or(w, |l| w.min(l));
        let mut width = 0.0_f64;
        for line in &wrap_lines {
            width = width.max(fonts.measure(line, family, title_font_size)?);
        }
        width = cap(width);
        if wrap_sub_lines.is_empty() {
            Some(crate::vega_fit::TitleInk::with_lines(
                title_font_size,
                wrap_lines.len(),
                offset,
                width,
            ))
        } else {
            let mut sub_w = 0.0_f64;
            for line in &wrap_sub_lines {
                sub_w = sub_w.max(fonts.measure(line, sub_family, sub_fs)?);
            }
            let sub_w = cap(sub_w);
            Some(crate::vega_fit::TitleInk::with_subtitle_lines(
                title_font_size,
                wrap_lines.len(),
                sub_fs,
                wrap_sub_lines.len(),
                offset,
                width,
                sub_w,
            ))
        }
    };
    // Vega draws every title/subtitle text mark with `limit` set and
    // enforces it NATIVELY at render time, in the row's own paint family —
    // a separate mechanism from the wrap pass above, which mirrors
    // production's own Python pre-pass (measured in Inter, mimicking
    // `prepare_title_text`'s own estimate). A row that fits under Inter's
    // estimate can still overflow in its own, usually wider, family; Vega
    // clips it with an ellipsis at render time regardless of what the
    // pre-pass decided. Probed: `dual-y-axis-lab/revenue_vs_conversion_
    // auto_split`'s compiled spec carries `limit: 568` with the FULL title
    // text (Python's own pre-pass measured ~550px in Inter and left it
    // alone), yet the reference SVG draws "...Only …" — ellipsized by Vega
    // itself. `truncate_label` already ports Vega's own `truncate` rule;
    // `shaped` picks the "Vega measures itself" branch of that law. This
    // runs AFTER `ink` (which reserves the wrap pass's own `limit`-capped
    // width regardless of what ends up drawn) — the DRAWN row can only get
    // shorter here, never wider, so the reservation above already covers it.
    let (title_lines, sub_lines) = match limit {
        None => (wrap_lines, wrap_sub_lines),
        Some(l) => {
            let clip = |lines: Vec<String>, fam: &str, fs: f64| -> Result<Vec<String>, String> {
                lines.into_iter().map(|line| truncate_label(&line, l, fam, fs, fonts, true)).collect()
            };
            (clip(wrap_lines, family, title_font_size)?, clip(wrap_sub_lines, sub_family, sub_fs)?)
        }
    };
    Ok(TitleBlock { title_lines, sub_lines, ink, title_truncated, subtitle_truncated })
}

/// `WARN-CHART-TITLE-TRUNCATED` for each of the title and subtitle the pre-pass
/// cut: production's `record_text_truncation(chart_id, "chart_title", text,
/// field)`, with `cased_title` and `subtitle` as `wrap_title_block` received
/// them. Each text pairs with whether the pre-pass cut it.
pub fn warn_title_truncations(
    chart_id: &str,
    (cased_title, title_truncated): (Option<&str>, bool),
    (subtitle, subtitle_truncated): (Option<&str>, bool),
) {
    for (cut, field, text) in [
        (title_truncated, "title", cased_title),
        (subtitle_truncated, "subtitle", subtitle),
    ] {
        if let (true, Some(text)) = (cut, text) {
            crate::diagnostic::warn(Diagnostic::new(
                &codes::WARN_CHART_TITLE_TRUNCATED,
                [
                    ("authored_field", json!(field)),
                    ("authored_text", json!(text)),
                    ("chart_id", json!(chart_id)),
                ],
            ));
        }
    }
}

/// How many distinct `field` values `rows` carry under Python's equality, as
/// `len({row[field] ...})` counts the bands of the raw query result.
pub fn distinct_values(rows: &[serde_json::Map<String, Value>], field: &str) -> usize {
    rows.iter()
        .filter(|row| row.contains_key(field))
        .map(|row| py_scalar_key(&row[field]))
        .collect::<std::collections::HashSet<_>>()
        .len()
}

/// `count_horizontal_bar_categories` (`_cartesian.py`): the distinct `str()` of
/// the category field over the rows that carry it non-null.
pub fn count_horizontal_bar_categories(rows: &[serde_json::Map<String, Value>], field: &str) -> usize {
    rows.iter()
        .filter(|row| row.get(field).is_some_and(|v| !v.is_null()))
        .map(|row| row_str(row, field))
        .collect::<std::collections::HashSet<_>>()
        .len()
}

/// The face a value label measures in for the crowd check: its own font, else
/// the theme's text-mark font, per field. Errors when the payload carries
/// neither, rather than measuring in a guessed face.
pub fn crowd_label_font(
    own: Option<&Font>,
    board: Option<&crate::BoardStyle>,
) -> Result<(String, f64), String> {
    let text_mark = board.map(|b| &b.text_mark_font);
    let family = own.and_then(|f| f.family.clone()).or_else(|| text_mark.map(|t| t.family.clone()));
    let size = own.and_then(|f| f.size).or_else(|| text_mark.map(|t| t.size));
    family.zip(size).ok_or_else(|| {
        "value labels: no labels.font, and the payload carries no board_style to name the text-mark face".into()
    })
}

/// `WARN-VALUE-LABELS-CROWD-WIDTH` (`value_labels_crowd_width.py`): each of
/// `distinct` marks gets `render_width / distinct` px, and the widest value
/// label (`widest`, measured at the font it paints in) is wider than that.
pub fn warn_value_labels_crowd(chart_id: &str, render_width: f64, distinct: usize, widest: f64) {
    if render_width <= 0.0 || distinct == 0 {
        return;
    }
    let slot_width = render_width / distinct as f64;
    if widest > slot_width {
        crate::diagnostic::warn(Diagnostic::new(
            &codes::WARN_VALUE_LABELS_CROWD_WIDTH,
            [
                ("chart_id", json!(chart_id)),
                ("label_width", json!(widest)),
                ("slot_width", json!(slot_width)),
            ],
        ));
    }
}

#[cfg(test)]
mod title_block_tests {
    use super::*;
    use crate::text::FontBook;

    fn fonts() -> FontBook {
        let dir = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        );
        FontBook::load_dir(dir).expect("test fonts load")
    }

    /// Production's `apply_title_overflow_to_spec` measures the pre-pass
    /// wrap at `title_style.font.size` (the UN-TIERED size) while the chart
    /// PAINTS the title at the width-resolved, tiered `style.title_font.size`
    /// — a nested card's title paints at 14px but the pre-pass still
    /// truncates as if it were 18px. `wrap_title_block` must take the two
    /// sizes separately: measuring at the paint size (the pre-fix bug) lets
    /// this title survive on one line where production truncates it.
    #[test]
    fn wrap_measures_at_the_untiered_title_style_size_not_the_paint_size() {
        let fonts = fonts();
        let text = "Stacked Area — Vertical Grid Behind the Mark";
        let paint_fs = 14.0;
        let measure_fs = 18.0;
        let limit = 350.0;
        assert!(
            fonts.measure(text, "Inter", paint_fs).expect("measures") < limit,
            "fixture must fit under Inter at the paint size"
        );
        assert!(
            fonts.measure(text, "Inter", measure_fs).expect("measures") > limit,
            "fixture must overflow under Inter at the measure size"
        );

        let style: TitleStyle = serde_json::from_value(serde_json::json!({
            "font": {"family": "Inter", "size": measure_fs},
            "overflow": "truncate"
        }))
        .expect("title style");

        let block =
            wrap_title_block(Some(text), None, Some(&style), paint_fs, measure_fs, limit, &fonts)
                .expect("title block");
        assert_eq!(block.title_lines.len(), 1, "fixture wrapped: {:?}", block.title_lines);
        assert_ne!(
            block.title_lines[0], text,
            "measured at the un-tiered 18px, the title must truncate"
        );
    }


    /// Vega bounds an over-long text mark at its `limit` itself — the limit
    /// production compiles onto the title (`compute_title_limit`: the padded
    /// card width, the same number this helper wraps against), not the
    /// ellipsized string it draws (probe: limit 320, drawn string 314,
    /// reserved 320.0). The wrap measures in Inter while the ink measures
    /// in the title's own family, so a family wider than Inter can keep one
    /// line and still measure past the limit — the ink contributes the
    /// limit, never the overflow.
    #[test]
    fn a_title_wider_than_its_limit_contributes_only_the_limit() {
        let fonts = fonts();
        let text = "Same Chart with the Feature OFF and a Side Legend Beside It";
        let style: TitleStyle = serde_json::from_value(serde_json::json!({
            "font": {"family": "'Source Code Pro', monospace", "size": 18.0}
        }))
        .expect("title style");
        let limit = 560.0;
        assert!(
            fonts.measure(text, "Inter", 18.0).expect("measures") < limit,
            "fixture must survive the wrap on one line"
        );
        let measured = fonts
            .measure(text, "'Source Code Pro', monospace", 18.0)
            .expect("measures");
        assert!(measured > limit, "fixture must overflow the limit: {measured}");

        let block = wrap_title_block(Some(text), None, Some(&style), 18.0, 18.0, limit, &fonts)
            .expect("title block");
        assert_eq!(block.title_lines.len(), 1, "fixture wrapped: {:?}", block.title_lines);
        assert_eq!(block.ink.expect("titled").width, limit);

        // The subtitle row carries the same limit, and `TitleInk` takes the
        // wider of the two — so an overflowing subtitle under a short title
        // must cap as well.
        let subtitled: TitleStyle = serde_json::from_value(serde_json::json!({
            "font": {"family": "'Source Code Pro', monospace", "size": 18.0},
            "subtitle": {"font": {"family": "'Source Code Pro', monospace", "size": 18.0}}
        }))
        .expect("title style");
        let block =
            wrap_title_block(Some("Short"), Some(text), Some(&subtitled), 18.0, 18.0, limit, &fonts)
                .expect("title block");
        assert_eq!(block.sub_lines.len(), 1, "fixture wrapped: {:?}", block.sub_lines);
        assert_eq!(block.ink.expect("titled").width, limit);
    }

    /// The wrap pass above (Inter-measured, mirroring production's own
    /// `prepare_title_text` pre-pass) decides this title needs no
    /// truncation — it fits on one line under Inter's estimate. But Vega
    /// draws EVERY title/subtitle text mark with `limit` set, and enforces
    /// it natively at render time using its OWN measurement (the row's own
    /// paint family) — a SEPARATE mechanism from the Python pre-pass, which
    /// clips with an ellipsis regardless of what the pre-pass decided.
    /// Probed on the real corpus row this fixture mirrors
    /// (`dual-y-axis-lab/revenue_vs_conversion_auto_split`): the compiled
    /// spec carries `limit: 568` with the FULL, untruncated title text
    /// (`prepare_title_text` measured ~550px in Inter and left it alone),
    /// yet the rendered reference SVG draws "...Only …" — ellipsized by
    /// Vega itself, not by Python. `common::truncate_label` already ports
    /// Vega's own `truncate` rule; this reuses it here.
    #[test]
    fn a_title_that_fits_under_inters_estimate_still_clips_in_its_own_wider_family() {
        let fonts = fonts();
        let text = "Same Chart with the Feature OFF and a Side Legend Beside It";
        let style: TitleStyle = serde_json::from_value(serde_json::json!({
            "font": {"family": "'Source Code Pro', monospace", "size": 18.0},
            "overflow": "truncate"
        }))
        .expect("title style");
        let limit = 560.0;
        assert!(
            fonts.measure(text, "Inter", 18.0).expect("measures") < limit,
            "fixture must survive the Inter-measured wrap pass on one line"
        );
        let family = "'Source Code Pro', monospace";
        let shaped = fonts.measure_shaped(text, family, 18.0).expect("measures");
        assert!(shaped > limit, "fixture must overflow the limit in its own family: {shaped}");

        let block = wrap_title_block(Some(text), None, Some(&style), 18.0, 18.0, limit, &fonts)
            .expect("title block");
        assert_eq!(block.title_lines.len(), 1, "still one row: {:?}", block.title_lines);
        assert_ne!(block.title_lines[0], text, "the drawn row is shortened, not the full string");
        assert!(
            block.title_lines[0].ends_with('\u{2026}'),
            "Vega's own limit clip ellipsizes the drawn row: {:?}",
            block.title_lines
        );
        let drawn_w = fonts.measure_shaped(&block.title_lines[0], family, 18.0).expect("measures");
        assert!(drawn_w <= limit, "the ellipsized row itself fits the limit: {drawn_w}");
        assert_eq!(block.ink.expect("titled").width, limit, "ink still reserves exactly the limit");
    }
}

#[cfg(test)]
mod truncate_label_tests {
    use super::*;
    use crate::text::FontBook;

    fn fonts() -> FontBook {
        let dir = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        );
        FontBook::load_dir(dir).expect("test fonts load")
    }

    const FAMILY: &str = "'Inter Variable', 'Noto Emoji', Inter, system-ui, sans-serif";

    #[test]
    fn fits_within_limit_is_unchanged() {
        let fonts = fonts();
        assert_eq!(truncate_label("Alpha", 180.0, FAMILY, 11.0, &fonts, false).unwrap(), "Alpha");
    }

    #[test]
    fn over_limit_truncates_to_the_reference_prefix() {
        // Oracle: bar_review's reference y label — "Emerging Product and
        // Innovation Bets" (199.4px) over the 180px labelLimit truncates
        // to "Emerging Product and Innovatio…" (176.3px); one more
        // character, "…Innovation…", measures 182.8px, over the limit.
        let fonts = fonts();
        let got = truncate_label(
            "Emerging Product and Innovation Bets",
            180.0,
            FAMILY,
            11.0,
            &fonts,
            false,
        )
        .unwrap();
        assert_eq!(got, "Emerging Product and Innovatio\u{2026}");
    }

    #[test]
    fn word_boundary_limit_keeps_the_space_before_the_ellipsis() {
        // Vega's `truncate` picks the cutoff by measuring the candidate
        // prefix with its trailing whitespace ignored, then draws the
        // prefix UNTRIMMED — so at a limit landing right after a word
        // boundary, the emitted string still carries that trailing space
        // ("Emerging …", not "Emerging…"). Probed against vl_convert over
        // "Emerging Product and Innovation Bets" at 11px Inter: limit 59.0
        // is the first word-boundary transition in the sweep.
        let fonts = fonts();
        let got = truncate_label(
            "Emerging Product and Innovation Bets",
            59.0,
            FAMILY,
            11.0,
            &fonts,
            false,
        )
        .unwrap();
        assert_eq!(got, "Emerging \u{2026}");
    }
}

/// Emit a wrapped title block where Vega's title layout put it: `(x, y)` is
/// the title group's top-left, each extra line advancing by the text mark's
/// default lineHeight, with the subtitle rows stacked under the LAST title
/// line.
pub fn emit_title_block_vega(
    out: &mut String,
    block: &TitleBlock,
    style: Option<&TitleStyle>,
    x: f64,
    y: f64,
    title_font_size: f64,
) {
    let advance = title_font_size + crate::vega_fit::TitleInk::LINE_ADVANCE_EXTRA;
    for (i, line) in block.title_lines.iter().enumerate() {
        // Production wraps every title line in ONE `role-title-text` group
        // (`_stamp_chart_title_kind`'s regex matches the group, not each
        // line), so `data-authored-kind="title"` appears exactly once
        // regardless of wrap -- stamp only the first line here too.
        emit_title_vega(out, line, style, x, y + i as f64 * advance, i == 0);
    }
    if !block.sub_lines.is_empty() {
        let last_title_y = y + (block.title_lines.len() - 1) as f64 * advance;
        emit_subtitle_vega(out, &block.sub_lines, style, x, last_title_y, title_font_size);
    }
}

/// Emit the standard chart title; returns the vertical band consumed (size + 10).
///
/// `x` is the left inset (`pad_l`); `pad_t` is the top padding — the baseline is derived
/// internally as `pad_t + baseline_shift("top", size)`, Vega's own top-baseline shift
/// (the same law `emit_title_vega` uses). Callers use the returned band for `plot_top`.
pub fn emit_title(
    out: &mut String,
    title: Option<&str>,
    style: Option<&TitleStyle>,
    x: f64,
    pad_t: f64,
) -> f64 {
    use std::fmt::Write;
    let Some(title) = title else { return 0.0 };
    let f = style.map(|t| &t.font);
    let family = f.and_then(|f| f.family.as_deref()).unwrap_or("serif");
    let size = f.and_then(|f| f.size).unwrap_or(18.0);
    let color = f.and_then(|f| f.color.as_deref()).unwrap_or("#1a1a1a");
    let weight = f.map(|f| f.weight_str()).unwrap_or_else(|| "600".into());
    let y_baseline = pad_t + crate::measure::baseline_shift("top", size);
    // Production applies title_style.font.case at render time
    // (translate.py -> spec_builders.set_chart_title -> text/case.py
    // apply_case); subtitle stays raw (case is never applied there).
    let font_case = f.and_then(|f| f.case.as_deref()).unwrap_or("none");
    // emit_title returns f64, not Result (family call sites don't propagate
    // one) — an unsupported case value (slug/camel; never emitted for a
    // title by production themes) is a payload bug, so it fails loudly
    // here rather than silently falling back to the raw title.
    let cased_title = display_text::case::apply_case(title, font_case)
        .unwrap_or_else(|e| panic!("emit_title: {e}"));
    let _ = write!(
        out,
        "<text x=\"{x:.1}\" y=\"{y_baseline:.1}\" font-family=\"{}\" font-size=\"{size}\" \
         font-weight=\"{weight}\" fill=\"{color}\"{}>{}</text>",
        crate::svg::vega_escape_attr(family),
        crate::contract::authored_kind_attr("title"),
        crate::svg::xml_escape(&cased_title), weight = xml_escape_attr(&weight), color = xml_escape_attr(color)
    );
    // VL allocates font_size + offset (10px) above the plot for the title.
    size + 10.0
}

/// Draw the chart title where Vega's title layout put it: `y` is the title
/// group's top (plot-coordinate `title_pos` made absolute), the text baseline
/// one top-baseline shift below it, anchored start at `x`.
/// Emit the subtitle line under a vega-positioned title: one title line +
/// SUBTITLE_PADDING below the title row, top-baseline, themed muted ink
/// unless the resolved subtitle font overrides it. Shared by every
/// vega_fit-wired family that draws `r.subtitle`.
pub fn emit_subtitle_vega(
    out: &mut String,
    subs: &[String],
    style: Option<&TitleStyle>,
    x: f64,
    title_row_y: f64,
    title_font_size: f64,
) {
    use std::fmt::Write;
    let sf = style.and_then(|ts| ts.subtitle.as_ref());
    // Same fallback the wrap/ink path uses (production's prepare_title_text
    // falls back to the effective title size) — box and text agree.
    let sub_fs = sf.and_then(|f| f.font.size).unwrap_or(title_font_size);
    let sub_family = sf.and_then(|f| f.font.family.as_deref()).unwrap_or("sans-serif");
    let sub_color = sf.and_then(|f| f.font.color.as_deref()).unwrap_or("#8A8C8F");
    let sub_weight = sf.map(|f| f.font.weight_str()).unwrap_or_else(|| "400".into());
    let sy = title_row_y
        + title_font_size
        + crate::constants::layout::VEGA_SUBTITLE_PADDING
        + crate::measure::baseline_shift("top", sub_fs);
    // Extra lines advance by the Vega text mark's default lineHeight
    // (fontSize + LINE_ADVANCE_EXTRA) — same term the fit margins reserve.
    for (i, sub) in subs.iter().enumerate() {
        let ly = sy + i as f64 * (sub_fs + crate::vega_fit::TitleInk::LINE_ADVANCE_EXTRA);
        // Same one-group-not-one-per-line law as the title above.
        let kind_attr =
            if i == 0 { crate::contract::authored_kind_attr("subtitle") } else { String::new() };
        let _ = write!(
            out,
            "<text x=\"{x:.2}\" y=\"{ly:.2}\" font-family=\"{}\" \
             font-size=\"{sub_fs}\" font-weight=\"{sub_weight}\" \
             fill=\"{sub_color}\"{kind_attr}>{}</text>",
            crate::svg::vega_escape_attr(sub_family),
            crate::svg::xml_escape(sub), sub_weight = xml_escape_attr(&sub_weight), sub_color = xml_escape_attr(sub_color)
        );
    }
}

pub fn emit_title_vega(
    out: &mut String,
    text: &str,
    style: Option<&TitleStyle>,
    x: f64,
    y: f64,
    stamp_kind: bool,
) {
    use std::fmt::Write;
    let f = style.map(|t| &t.font);
    let family = f.and_then(|f| f.family.as_deref()).unwrap_or("serif");
    let size = f.and_then(|f| f.size).unwrap_or(18.0);
    let color = f.and_then(|f| f.color.as_deref()).unwrap_or("#1a1a1a");
    let weight = f.map(|f| f.weight_str()).unwrap_or_else(|| "600".into());
    let baseline = y + crate::measure::baseline_shift("top", size);
    let kind_attr = if stamp_kind { crate::contract::authored_kind_attr("title") } else { String::new() };
    let _ = write!(
        out,
        "<text x=\"{x:.2}\" y=\"{baseline:.2}\" font-family=\"{}\" font-size=\"{size}\" \
         font-weight=\"{weight}\" fill=\"{color}\"{kind_attr}>{}</text>",
        crate::svg::vega_escape_attr(family),
        crate::svg::xml_escape(text), weight = xml_escape_attr(&weight), color = xml_escape_attr(color)
    );
}

/// Numeric value from a row column, tolerating strings.
pub fn row_num(row: &serde_json::Map<String, Value>, field: &str) -> Option<f64> {
    match row.get(field)? {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// The rows whose `field` holds a number, each with that number. A null or non-numeric measure
/// draws no mark and no label (Vega skips the datum), so every pass over a measure reads rows here.
pub fn valued_rows<'a>(
    rows: impl IntoIterator<Item = &'a serde_json::Map<String, Value>>,
    field: &str,
) -> Vec<(&'a serde_json::Map<String, Value>, f64)> {
    rows.into_iter().filter_map(|row| row_num(row, field).map(|val| (row, val))).collect()
}

/// Python's comparison of two raw values: numbers (bools included) with numbers,
/// strings with strings, any other pair has no order (`TypeError`).
pub fn raw_x_order(a: &Value, b: &Value) -> Result<std::cmp::Ordering, String> {
    // A bool is an int to Python.
    let number = |v: &Value| match v {
        Value::Number(n) => n.as_f64(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        _ => None,
    };
    let order = match (a, b) {
        (Value::String(a), Value::String(b)) => Some(a.cmp(b)),
        _ => number(a).zip(number(b)).and_then(|(a, b)| a.partial_cmp(&b)),
    };
    order.ok_or_else(|| format!("x values {a} and {b} have no order"))
}

/// Production's `last_nonnull_value_per_series` (`_cartesian.py`): each
/// series' value at its own greatest x, found by walking the rows in order and
/// replacing the held pair whenever a row's raw x is `>=` the held one (Python's
/// comparison of the raw values, so the later of two equal xs wins and an
/// unsorted string x compares lexicographically). A row with a null series, x or
/// y is skipped; an x pair of different types has no order and is refused.
pub fn last_nonnull_value_per_series(
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    y_field: &str,
    series_field: &str,
) -> Result<std::collections::HashMap<String, f64>, String> {
    let mut latest: std::collections::HashMap<String, (&Value, f64)> = std::collections::HashMap::new();
    for row in rows {
        let (Some(x), Some(y)) = (row.get(x_field).filter(|v| !v.is_null()), row_num(row, y_field)) else {
            continue;
        };
        if row.get(series_field).is_none_or(Value::is_null) {
            continue;
        }
        let series = row_str(row, series_field);
        let replaces = match latest.get(&series) {
            None => true,
            Some((held, _)) => raw_x_order(x, held).map_err(|e| format!("{e} (series {series:?})"))?.is_ge(),
        };
        if replaces {
            latest.insert(series, (x, y));
        }
    }
    Ok(latest.into_iter().map(|(series, (_, y))| (series, y)).collect())
}

/// Production's `_wide_endpoint_positions`: a column's value at its greatest
/// raw x, the first of equal xs winning. A row with a null x or y is skipped.
pub fn last_nonnull_value(
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    y_field: &str,
) -> Result<Option<f64>, String> {
    let mut latest: Option<(&Value, f64)> = None;
    for row in rows {
        let (Some(x), Some(y)) = (row.get(x_field).filter(|v| !v.is_null()), row_num(row, y_field)) else {
            continue;
        };
        let replaces = match latest {
            None => true,
            Some((held, _)) => raw_x_order(x, held)?.is_gt(),
        };
        if replaces {
            latest = Some((x, y));
        }
    }
    Ok(latest.map(|(_, y)| y))
}

/// Port of production's `utils.py::is_year_shaped` — value-only, no
/// column-name signal: every non-null `field` value is an int (a JSON
/// `Value::Number` with no fractional part), a 4-digit string matching
/// `^\d{4}$`, or an integral float (a warehouse NUMERIC/Decimal year comes
/// back as a JSON number that may still carry a `.0`) — each in
/// `[1700, 2100]`. A JSON `Value::Bool` never matches (`Value::Number`
/// can't confuse a bool with a number the way Python's `isinstance(v,
/// int)` can, so there is no separate bool exclusion to port). Empty input
/// (every row's `field` null or absent) is not year-shaped.
///
/// `time_unit_detect.normalize_labeled_temporal` rewrites a year-shaped x
/// to ISO dates BEFORE `resolve_cartesian_x_type` ever classifies it, so a
/// bare year resolves `temporal` in production even though it has no
/// date-like STRUCTURAL shape at all — `x_values_are_temporal_candidates`
/// alone does not catch it. This predicate is the missing precondition a
/// categorical-x gate (line/area's authored-sort gate) needs alongside it.
pub fn is_year_shaped(rows: &[serde_json::Map<String, Value>], field: &str) -> bool {
    const YEAR_MIN: f64 = 1700.0;
    const YEAR_MAX: f64 = 2100.0;
    let mut any = false;
    for row in rows {
        let v = match row.get(field) {
            Some(v) if !v.is_null() => v,
            _ => continue,
        };
        any = true;
        let ok = match v {
            Value::Number(n) => n
                .as_f64()
                .is_some_and(|f| f.fract() == 0.0 && (YEAR_MIN..=YEAR_MAX).contains(&f)),
            Value::String(s) => {
                s.len() == 4
                    && s.bytes().all(|b| b.is_ascii_digit())
                    && s.parse::<i32>().is_ok_and(|y| (1700..=2100).contains(&y))
            }
            _ => false,
        };
        if !ok {
            return false;
        }
    }
    any
}

#[cfg(test)]
mod is_year_shaped_tests {
    use super::*;

    fn rows(field: &str, values: &[Value]) -> Vec<serde_json::Map<String, Value>> {
        values
            .iter()
            .map(|v| {
                let mut m = serde_json::Map::new();
                m.insert(field.to_string(), v.clone());
                m
            })
            .collect()
    }

    /// `[1700, 2100]` is inclusive at both ends (`utils.py::is_year_shaped`'s
    /// own `YEAR_MIN <= y <= YEAR_MAX`), string or number alike.
    #[test]
    fn the_1700_2100_range_is_inclusive_at_both_ends() {
        let r = rows("year", &[Value::String("1700".into()), Value::String("2100".into())]);
        assert!(is_year_shaped(&r, "year"));
        let r = rows("year", &[serde_json::json!(1700), serde_json::json!(2100)]);
        assert!(is_year_shaped(&r, "year"));
    }

    /// One value just outside either bound fails the WHOLE domain, string or
    /// number alike — the gate is a per-value AND, not a range over the
    /// domain's extremes.
    #[test]
    fn a_value_one_outside_either_bound_fails_the_whole_domain() {
        let r = rows("year", &[Value::String("1699".into()), Value::String("2000".into())]);
        assert!(!is_year_shaped(&r, "year"));
        let r = rows("year", &[Value::String("2000".into()), Value::String("2101".into())]);
        assert!(!is_year_shaped(&r, "year"));
        let r = rows("year", &[serde_json::json!(1699), serde_json::json!(2000)]);
        assert!(!is_year_shaped(&r, "year"));
    }

    /// A non-year 4-digit number outside `[1700, 2100]` — e.g. a product
    /// code or a quantity — never counts as year-shaped, string or number.
    #[test]
    fn a_non_year_4_digit_number_is_not_year_shaped() {
        let r = rows("code", &[Value::String("1234".into()), Value::String("5678".into())]);
        assert!(!is_year_shaped(&r, "code"));
        let r = rows("code", &[serde_json::json!(1234), serde_json::json!(5678)]);
        assert!(!is_year_shaped(&r, "code"));
    }
}

/// A field name as display text: `display_text::slug_to_text`, then
/// `apply_case` per the resolved `font.case` (`title` keeps minor words
/// lowercase: "Avg Days to Complete").
pub fn slug_to_display(field: &str, font_case: &str) -> Result<String, String> {
    display_text::case::apply_case(&display_text::slug_to_text(field)?, font_case)
}

/// A chart's authored `field_labels`: column -> display text, painted verbatim.
pub type FieldLabels = std::collections::HashMap<String, String>;

/// `core/text/case.py::field_display_text`: the authored `field_labels` entry
/// verbatim, else `slug_to_display` (derived text takes the slot's case).
pub fn field_display_text(
    field: &str,
    field_labels: &FieldLabels,
    font_case: &str,
) -> Result<String, String> {
    match field_labels.get(field) {
        Some(label) => Ok(label.clone()),
        None => slug_to_display(field, font_case),
    }
}

/// An authored title's newlines read as spaces: only [`wrapped_axis_title`]
/// puts line breaks in a title.
pub fn collapse_title_newlines(text: &str) -> String {
    text.replace('\n', " ")
}

/// Axis title text: an authored `x_label`/`y_label` wins (production never
/// case-transforms it either, see `resolve_xy_titles`), else `field_title`
/// (the `field_labels` entry, else `slug_to_text`). A newline in authored text
/// reads as a space: only [`wrapped_axis_title`] puts line breaks in a title.
pub fn axis_title_text(
    field: &str,
    label: Option<&str>,
    field_labels: &FieldLabels,
) -> Result<String, String> {
    match label {
        Some(label) => Ok(collapse_title_newlines(label)),
        None => display_text::field_title(field, field_labels),
    }
}

/// `wrap_axis_title` (`emitters/_cartesian.py`): an axis title wraps to at most
/// two lines inside `axis_title_budget`, measured at the weight it paints.
/// `extent` is the chart's size along the title's direction and `panels` how
/// many facet panels share it. Returns the lines and whether authored text was
/// cut (a word wider than the budget, or a third line).
pub fn wrap_axis_title(
    text: &str,
    extent: f64,
    panels: f64,
    font: &Font,
    fonts: &crate::text::FontBook,
) -> Result<(Vec<String>, bool), String> {
    let budget = (extent - crate::families::bar::CHROME_PX / panels).trunc().max(1.0);
    let size = font.size_or(11.0);
    let measure = |t: &str| font.measure_weighted(fonts, t, size);
    // A word the wrapper would have to break is ellipsized first: a split word
    // is wrong output in a title.
    let authored: Vec<&str> = text.split_whitespace().collect();
    let words = authored
        .iter()
        .map(|w| truncate_with(&measure, w, budget, true))
        .collect::<Result<Vec<_>, _>>()?;
    let (lines, truncated) = wrap_text_with(&measure, &words.join(" "), budget, Some(2), true)?;
    if lines.is_empty() {
        return Ok((vec![text.to_string()], false));
    }
    Ok((lines, truncated || words != authored))
}

/// `WARN-AXIS-TITLE-TRUNCATED`: production's
/// `record_text_truncation(chart_id, "axis_title", text, authored_field)`.
pub fn warn_axis_title_truncated(chart_id: &str, authored_field: &str, text: &str) {
    crate::diagnostic::warn(Diagnostic::new(
        &codes::WARN_AXIS_TITLE_TRUNCATED,
        [
            ("authored_field", json!(authored_field)),
            ("authored_text", json!(text)),
            ("chart_id", json!(chart_id)),
        ],
    ));
}

/// A title's lines past the first, in pixels: Vega draws newline-separated text
/// as `<tspan>`s `lineHeight` apart, and each extra line grows the axis.
pub fn extra_title_lines(text: &str, font_size: f64) -> f64 {
    text.matches('\n').count() as f64 * crate::vega_fit::label_row_advance(font_size)
}

/// The axis title as it paints: `text` wrapped by [`wrap_axis_title`] and joined
/// by newlines, with `WARN-AXIS-TITLE-TRUNCATED` when authored text was cut.
/// `extent` is the chart size along the title's direction and `authored_field`
/// the YAML key the text came from; `panels` is how many facet panels share the extent. A hidden title still wraps and warns, as
/// production's `resolve_xy_titles` does.
pub fn wrapped_axis_title(
    chart_id: &str,
    authored_field: &str,
    text: &str,
    font: &Font,
    extent: f64,
    panels: f64,
    fonts: &crate::text::FontBook,
) -> Result<String, String> {
    if text.is_empty() {
        return Ok(String::new());
    }
    let (lines, truncated) = wrap_axis_title(text, extent, panels, font, fonts)?;
    if truncated {
        warn_axis_title_truncated(chart_id, authored_field, text);
    }
    Ok(lines.join("\n"))
}

/// The widest line of a (possibly wrapped) axis title as Vega measures it
/// (shaped), for the extent it reaches past the plot.
pub fn widest_title_line(title: &AxisTitle, text: &str, fonts: &crate::text::FontBook) -> Result<f64, String> {
    text.split('\n').try_fold(0.0_f64, |widest, line| {
        fonts
            .measure_shaped(line, title.font.family_or("sans-serif"), title.font.size_or(11.0))
            .map(|w| widest.max(w))
    })
}

/// Extra margin (px) an axis title band reserves beyond the tick-label band
/// it sits outside of — `0.0` when the title is hidden or has no text.
///
/// Matches the measured production constant `title.font.size + title.padding`
/// (golden cases `charts/bar-charts_4` `category_columns_styled`,
/// `charts/area-charts_0` `signup_area`, `guides/getting-started_0`
/// `documents_chart` — all three reserve exactly this band beyond their
/// tick-label row/gutter, confirmed via `vl_convert`-rendered SVG geometry:
/// bottom-axis title baseline sits at `label_baseline + label_font_size +
/// title.padding`, and a rotated side-axis title's near edge sits at
/// `label_edge - (title.padding + 2.0)` — the two font-size terms cancel
/// exactly whenever `title.font.size == label.font.size`, which every
/// measured case does).
pub fn axis_title_band(style: &AxisTitle, text: &str) -> f64 {
    if text.is_empty() || !style.visible.unwrap_or(true) {
        0.0
    } else {
        let size = style.font.size_or(11.0);
        size + style.padding.unwrap_or(10.0) + extra_title_lines(text, size)
    }
}

/// The pixel nudge a rotated (left/right) axis title sits past the tick
/// label's near edge, beyond `title.padding`: the `translate(0, -n)` in the
/// `rotate(...) translate(0,-n)` chain Vega emits after the title's own
/// placement, `n = round(0.21 * fontSize)` (the "bottom" baseline shift, the
/// same term `y_title_extra` reserves in the margin).
pub fn rotated_axis_title_nudge(font_size: f64) -> f64 {
    (0.21 * font_size).round()
}

/// A rotated (left/right) axis title's own additive reserve past the tick
/// label edge — Vega's real `axisTitleLayout`: `title.padding +
/// round(0.21·fontSize) + round(0.8·fontSize)`, rounding each term
/// independently rather than `axis_title_band`'s single `fontSize +
/// padding` sum (the two agree at the theme's default 11px title but
/// diverge at other sizes, since `0.21 + 0.8 != 1.0` before rounding).
/// `0.0` when the title is hidden or has no text. Shared by every arm
/// that reserves room for a rotated y-axis title past the label band, on
/// whichever edge the axis sits: `render_fit`'s ink union, `empty.rs`, and
/// both of `render_legacy`'s margin branches (the left one inside
/// `ceil(label_w + label_pad + y_title_extra)`).
pub fn y_title_extra(title: &AxisTitle, text: &str) -> f64 {
    if axis_title_band(title, text) <= 0.0 {
        return 0.0;
    }
    let fs = title.font.size_or(11.0);
    title.padding.unwrap_or(10.0) + (0.21 * fs).round() + (0.8 * fs).round() + extra_title_lines(text, fs)
}

/// The title Vega draws on an axis whose theme title is hidden but which
/// production forces a title onto (a dual axis restores both sides). The
/// theme's `axis.title` is `null` there, so none of its styling reaches the
/// axis and Vega's own defaults apply: 11px bold black text at 4px padding,
/// in the chart's font family.
pub fn vega_default_axis_title(family: Option<String>) -> AxisTitle {
    AxisTitle {
        font: Font {
            family,
            color: Some("#000".into()),
            size: Some(11.0),
            weight: Some(Value::String("bold".into())),
            ..Font::default()
        },
        padding: Some(4.0),
        visible: Some(true),
    }
}

/// A bottom axis title's additive reserve below the label ink — `axisLayout`
/// (`vega.js:20353-20408`) places the title at `s + padding` (`axisTitleLayout`,
/// `vega.js:20422-20432`, `s` the label bounds' `y2`) and unions its bounds:
/// `title.padding`, then the title's own height, `fontSize` plus the
/// `round(0.79·fs) − round(0.8·fs)` gap between the "top" and "middle" baseline
/// shifts. `0.0` when the title is hidden or has no text. The x-axis twin of
/// `y_title_extra`.
pub fn x_title_extra(title: &AxisTitle, text: &str) -> f64 {
    if axis_title_band(title, text) <= 0.0 {
        return 0.0;
    }
    let fs = title.font.size_or(11.0);
    title.padding.unwrap_or(10.0) + fs + (0.79 * fs).round() - (0.8 * fs).round() + extra_title_lines(text, fs)
}

/// Emit a bottom-anchored (horizontal) axis title's `<text>` at an
/// already-negotiated `title_y` — the shared emission (font resolution,
/// the `+0.5` crisp x every axis-group title inherits) both
/// `emit_axis_title_bottom`'s flat-label law below and a rotated
/// category axis's own Vega `axisTitleLayout` law (`scatter.rs`'s
/// nominal-x arm, `bar.rs`'s `BandXAxis::paint`) call into, so the `<text>`
/// is emitted once. No-op when `axis_title_band` would return `0.0` for
/// this `text`/`style` pair.
pub fn emit_axis_title_at(
    out: &mut String,
    text: &str,
    style: &AxisTitle,
    center_x: f64,
    title_y: f64,
    key: Option<crate::contract::LabelKey>,
) {
    use std::fmt::Write;
    if axis_title_band(style, text) == 0.0 {
        return;
    }
    let lines: Vec<&str> = text.split('\n').collect();
    let f = &style.font;
    let size = f.size_or(11.0);
    let family = f.family_or("sans-serif");
    let color = f.color.as_deref().unwrap_or("#8A8C8F");
    let weight = f.weight_str();
    // The title lives inside Vega's axis group, which the ticks and
    // gridlines already crisp-shift by +0.5 — the title inherits it too.
    let x = center_x + 0.5;
    crate::contract::axis_title_group_open(out, key);
    let _ = write!(
        out,
        "<text x=\"{x:.2}\" y=\"{title_y:.2}\" text-anchor=\"middle\" \
         font-family=\"{}\" font-size=\"{size}\" font-weight=\"{weight}\" fill=\"{color}\">",
        crate::svg::vega_escape_attr(family),
        weight = xml_escape_attr(&weight), color = xml_escape_attr(color)
    );
    write_title_lines(out, &lines, x, size);
    out.push_str("</text>");
    crate::contract::mark_group_close(out);
}

/// A title's `<text>` content: the line itself, or one `<tspan>` per line
/// stepping `lineHeight` along the text's own y (Vega's multi-line text).
pub fn write_title_lines(out: &mut String, lines: &[&str], x: f64, size: f64) {
    use std::fmt::Write;
    if let [line] = lines {
        out.push_str(&crate::svg::xml_escape(line));
        return;
    }
    for (i, line) in lines.iter().enumerate() {
        let step = if i == 0 { String::new() } else { format!(" x=\"{x:.2}\" dy=\"{}\"", crate::vega_fit::label_row_advance(size)) };
        let _ = write!(out, "<tspan{step}>{}</tspan>", crate::svg::xml_escape(line));
    }
}

/// Emit a bottom-anchored (horizontal) axis title, centered under the plot,
/// for a FLAT (untilted) category axis.
///
/// `label_baseline_y` is the same baseline the tick-label row already draws
/// at; `label_font_size` is that row's font size. The title's own baseline
/// sits one more "row" further out — see `axis_title_band`'s doc for the
/// measured derivation. This is production's own measured constant for a
/// flat label row, NOT a general reduction of Vega's `axisTitleLayout` (the
/// law `emit_axis_title_at`'s own rotated callers read directly): it
/// differs from `s + titlePadding` (`s` the label ink's true `bounds.y2`)
/// by `round(0.79·title_fs) − round(0.8·label_fs)` — zero at the label/title
/// sizes every corpus board and test here authors (11/11), nonzero in
/// general (e.g. −1px at 12/12). No-op when `axis_title_band` would return
/// `0.0` for this `text`/`style` pair (callers should still gate the margin
/// reservation on that same check).
pub fn emit_axis_title_bottom(
    out: &mut String,
    text: &str,
    style: &AxisTitle,
    center_x: f64,
    label_baseline_y: f64,
    label_font_size: f64,
    key: Option<crate::contract::LabelKey>,
) {
    let padding = style.padding.unwrap_or(10.0);
    let y = label_baseline_y + label_font_size + padding;
    emit_axis_title_at(out, text, style, center_x, y, key);
}

/// [`emit_axis_title_bottom`] for either edge: above a top axis the title baseline sits a label
/// height and its padding past the label row's own baseline, away from the plot.
pub fn emit_axis_title_flat(
    out: &mut String,
    text: &str,
    style: &AxisTitle,
    center_x: f64,
    label_baseline_y: f64,
    label_font_size: f64,
    key: Option<crate::contract::LabelKey>,
    edge: XEdge,
) {
    match edge {
        XEdge::Bottom => emit_axis_title_bottom(out, text, style, center_x, label_baseline_y, label_font_size, key),
        XEdge::Top => emit_axis_title_at(
            out,
            text,
            style,
            center_x,
            label_baseline_y - label_font_size - style.padding.unwrap_or(10.0),
            key,
        ),
    }
}

/// Emit a rotated (vertical) axis title for a left- or right-side axis,
/// centered on the plot's extent in that direction.
///
/// `label_edge_x` is the tick label band's outer edge (away from the plot) —
/// for a left axis, its leftmost pixel; for a right axis, its rightmost.
/// `sign` is `-1.0` for a left-side axis (rotate -90°, title sits further
/// left) and `1.0` for a right-side axis (rotate 90°, title sits further
/// right). No-op when `axis_title_band` would return `0.0`.
pub fn emit_axis_title_rotated(
    out: &mut String,
    text: &str,
    style: &AxisTitle,
    label_edge_x: f64,
    center_y: f64,
    sign: f64,
    key: Option<crate::contract::LabelKey>,
) {
    use std::fmt::Write;
    if axis_title_band(style, text) == 0.0 {
        return;
    }
    let lines: Vec<&str> = text.split('\n').collect();
    let f = &style.font;
    let size = f.size_or(11.0);
    let family = f.family_or("sans-serif");
    let color = f.color.as_deref().unwrap_or("#8A8C8F");
    let weight = f.weight_str();
    let padding = style.padding.unwrap_or(10.0);
    let angle = if sign < 0.0 { -90.0 } else { 90.0 };
    // Extra lines grow the title away from the plot; the last line keeps the
    // single-line baseline.
    let extra_lines = (lines.len() - 1) as f64 * crate::vega_fit::label_row_advance(size);
    let title_x = label_edge_x + sign * (padding + rotated_axis_title_nudge(size) + extra_lines);
    // The title lives inside Vega's axis group, which already crisp-shifts
    // by +0.5 in both coordinates — the title's y (its position ALONG the
    // axis) inherits it too, same as the bottom title's x above.
    let y = center_y + 0.5;
    crate::contract::axis_title_group_open(out, key);
    let _ = write!(
        out,
        "<text x=\"{title_x:.2}\" y=\"{y:.2}\" text-anchor=\"middle\" \
         transform=\"rotate({angle} {title_x:.2} {y:.2})\" \
         font-family=\"{}\" font-size=\"{size}\" font-weight=\"{weight}\" fill=\"{color}\">",
        crate::svg::vega_escape_attr(family),
        weight = xml_escape_attr(&weight), color = xml_escape_attr(color)
    );
    write_title_lines(out, &lines, title_x, size);
    out.push_str("</text>");
    crate::contract::mark_group_close(out);
}

/// Display string from a row column. This is production's Vega-drawn
/// convention (axis tick labels, strip cell text — anywhere the raw datum
/// reaches Vega, which stringifies with JS `toString`), not Python's
/// `str()`: a bool renders JSON-native `"true"`/`"false"`. The one place
/// production instead builds a label host-side with Python `str()` — wide
/// series naming — uses `wide_dimension_str` below, not this function; do
/// not special-case `Value::Bool` here, or every one of `row_str`'s other
/// ~29 call sites across bar/heatmap/line/pie/scatter/support_table_strip
/// would silently disagree with what Vega actually draws.
pub fn row_str(row: &serde_json::Map<String, Value>, field: &str) -> String {
    cell_str(row.get(field))
}

/// `row_str` of one cell.
pub fn cell_str(cell: Option<&Value>) -> String {
    row_cell_str(cell).into_owned()
}

/// `row_str` without copying a string cell.
pub fn row_str_ref<'a>(row: &'a serde_json::Map<String, Value>, field: &str) -> Cow<'a, str> {
    row_cell_str(row.get(field))
}

fn row_cell_str(cell: Option<&Value>) -> Cow<'_, str> {
    match cell {
        Some(Value::String(s)) => Cow::Borrowed(s),
        Some(Value::Null) | None => Cow::Borrowed(""),
        Some(other) => Cow::Owned(other.to_string()),
    }
}

/// JS `Number(string)` (ECMA-262 StringToNumber): `None` where it is `NaN`.
/// Blank is 0; `0x`/`0o`/`0b` prefixes read an unsigned integer; `Infinity`
/// is the only named value; decimals take no digit separators and no
/// trailing garbage.
pub fn js_string_to_number(s: &str) -> Option<f64> {
    let t = s.trim_matches(|c: char| c.is_whitespace() || c == '\u{feff}');
    if t.is_empty() {
        return Some(0.0);
    }
    let radix = match t.get(..2) {
        Some("0x" | "0X") => Some(16),
        Some("0o" | "0O") => Some(8),
        Some("0b" | "0B") => Some(2),
        _ => None,
    };
    if let Some(radix) = radix {
        let digits = &t[2..];
        if digits.is_empty() || !digits.chars().all(|c| c.is_digit(radix)) {
            return None;
        }
        return Some(digits.chars().fold(0.0, |acc, c| acc * f64::from(radix) + f64::from(c.to_digit(radix).unwrap_or(0))));
    }
    let (sign, body) = match t.as_bytes()[0] {
        b'-' => (-1.0, &t[1..]),
        b'+' => (1.0, &t[1..]),
        _ => (1.0, t),
    };
    if body == "Infinity" {
        return Some(sign * f64::INFINITY);
    }
    let (mantissa, exponent) = body.split_once(['e', 'E']).map_or((body, None), |(m, e)| (m, Some(e)));
    let (int, frac) = mantissa.split_once('.').map_or((mantissa, None), |(i, f)| (i, Some(f)));
    let all_digits = |part: &str| part.bytes().all(|b| b.is_ascii_digit());
    let mantissa_ok = all_digits(int) && frac.is_none_or(all_digits) && !(int.is_empty() && frac.is_none_or(str::is_empty));
    let exponent_ok = exponent.is_none_or(|e| {
        let digits = e.strip_prefix(['+', '-']).unwrap_or(e);
        !digits.is_empty() && all_digits(digits)
    });
    if !mantissa_ok || !exponent_ok {
        return None;
    }
    body.parse::<f64>().ok().map(|v| sign * v)
}

/// JS `Number(v)` of a JSON scalar; `None` where it is `NaN`.
pub fn js_to_number(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::String(s) => js_string_to_number(s),
        _ => None,
    }
}

/// JS `a < b` on two non-null JSON scalars: two strings compare by UTF-16
/// code unit, anything else as numbers (a numeric string coerces, a
/// non-numeric one is `NaN` and compares false both ways).
pub fn js_less(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::String(x), Value::String(y)) => x.encode_utf16().lt(y.encode_utf16()),
        _ => matches!((js_to_number(a), js_to_number(b)), (Some(x), Some(y)) if x < y),
    }
}

/// Vega's `ascending` comparator on two JSON scalars with JS `<`/`>`
/// coercion: numbers compare numerically (a numeric string coerces), strings
/// lexicographically, and a number against a non-numeric string is "equal".
fn js_ascending(a: &Value, b: &Value) -> i32 {
    match (a, b) {
        // `ascending` ranks a null before any other value.
        (Value::Null, Value::Null) => 0,
        (Value::Null, _) => -1,
        (_, Value::Null) => 1,
        _ if js_less(a, b) => -1,
        _ if js_less(b, a) => 1,
        _ => 0,
    }
}

/// An ordinal scale's `domain` (first-appearance order) sorted the way Vega
/// compiles `sort: {op: "min", field: sort.by, order}`: each category's key is
/// the JS `min` of the sort field over the `rows` it names (nulls skipped, the
/// first value kept unless a later one is less), and a category with no value
/// ranks below every other, first ascending and last descending.
pub fn min_ranked_domain(
    domain: Vec<String>,
    rows: &[&serde_json::Map<String, Value>],
    category_of: impl Fn(&serde_json::Map<String, Value>) -> String,
    sort: &ChartSort,
) -> Result<Vec<String>, String> {
    let keyed: Vec<(String, Value)> = domain
        .into_iter()
        .map(|category| {
            let key = rows
                .iter()
                .filter(|row| category_of(row) == category)
                .filter_map(|row| row.get(&sort.by).filter(|v| !v.is_null()))
                .fold(None::<&Value>, |min, v| min.filter(|m| !js_less(v, m)).or(Some(v)));
            (category, key.cloned().unwrap_or(Value::Null))
        })
        .collect();
    js_rank(&keyed, sort.order)
}

/// `items` ordered by their `key` the way Vega sorts a domain by an
/// aggregate: `Array.prototype.sort` with `ascending` (a null key first) or
/// its reverse. A key column of one type is a total order, so any stable
/// sort agrees; a mix of numbers and strings makes the comparator
/// non-transitive and the order is whatever V8's insertion sort produces.
pub fn js_rank<T: Clone>(items: &[(T, Value)], order: SortDir) -> Result<Vec<T>, String> {
    let sign = if order == SortDir::Desc { -1 } else { 1 };
    let cmp = |a: &(T, Value), b: &(T, Value)| sign * js_ascending(&a.1, &b.1);
    let numeric = items.iter().all(|(_, k)| matches!(k, Value::Null | Value::Number(_) | Value::Bool(_)));
    let textual = items.iter().all(|(_, k)| k.is_null() || k.is_string());
    let mut ranked: Vec<usize> = (0..items.len()).collect();
    if numeric || textual {
        ranked.sort_by(|&a, &b| cmp(&items[a], &items[b]).cmp(&0));
    } else {
        d3rs::js_sort::js_sort(&mut ranked, |a, b| f64::from(cmp(&items[a], &items[b])))?;
    }
    Ok(ranked.into_iter().map(|i| items[i].0.clone()).collect())
}

/// The order Vega's ordinal-scale `sort: true` produces for a domain of JSON
/// scalars: JavaScript `Array.prototype.sort` with `ascending`. A column that
/// mixes numbers and strings makes the comparator non-transitive, so the result
/// is whatever V8's insertion sort gives ([`js_rank`]).
pub fn vega_ascending_sort(values: Vec<Value>) -> Result<Vec<Value>, String> {
    let items: Vec<(Value, Value)> = values.into_iter().map(|v| (v.clone(), v)).collect();
    js_rank(&items, SortDir::Asc)
}

/// `_LINE_POS_MAP` and `_POINT_POS_MAP` (`features/value_labels.py`, identical but
/// for `middle`'s align): the (align, baseline, dx, dy) of a point value label at an authored `position`; `None` for an unknown one.
pub fn value_label_anchor(position: &str) -> Option<(&'static str, &'static str, f64, f64)> {
    Some(match position {
        "top" => ("center", "bottom", 0.0, -4.0),
        "bottom" => ("center", "top", 0.0, 4.0),
        "left" => ("right", "middle", -4.0, 0.0),
        "right" => ("left", "middle", 4.0, 0.0),
        "middle" => ("center", "middle", 0.0, 0.0),
        _ => return None,
    })
}

/// Where one value-label caption sits: `edge` is the fraction across its band
/// the caption anchors at (`None`: the datum's own position), then the text's
/// alignment, baseline and offset.
#[derive(Clone, Copy)]
pub struct CaptionSpot {
    pub edge: Option<f64>,
    pub align: &'static str,
    pub baseline: &'static str,
    pub dx: f64,
    pub dy: f64,
}

impl CaptionSpot {
    /// `top`'s anchor and offsets.
    pub fn top() -> CaptionSpot {
        let (align, baseline, dx, dy) = value_label_anchor("top").expect("top is a value-label position");
        CaptionSpot { edge: None, align, baseline, dx, dy }
    }

    /// The spot of `ml`'s captions: the authored position's anchor
    /// (`_POINT_POS_MAP`/`_LINE_POS_MAP`), an authored `dx`/`dy` replacing the
    /// anchor's own offset. On a band-width mark `band` says whether the
    /// caption's band is the domain's `(leading, trailing)` one: `left`/`right`
    /// anchor at their band's edge, and a caption whose band has no room on
    /// that side falls back to `top` at the band's center with `top`'s own
    /// offsets (`build_line_text_layers`, `_BAND_EDGE_FALLBACK`).
    pub fn of(ml: &MarkLabels, band: Option<(bool, bool)>) -> Result<CaptionSpot, String> {
        let position = ml.position.as_deref().unwrap_or("top");
        let (align, baseline, dx, dy) = value_label_anchor(position).ok_or_else(|| {
            format!("value labels position {position:?} is none of top, bottom, left, right or middle")
        })?;
        let anchored = CaptionSpot { edge: None, align, baseline, dx: ml.dx.unwrap_or(dx), dy: ml.dy.unwrap_or(dy) };
        let Some((leading, trailing)) = band else { return Ok(anchored) };
        let (blocked, edge) = match position {
            "left" => (leading, 0.0),
            "right" => (trailing, 1.0),
            _ => return Ok(anchored),
        };
        if blocked {
            return Ok(CaptionSpot::top());
        }
        Ok(CaptionSpot { edge: Some(edge), ..anchored })
    }
}

/// One point value label's `<text>`: `baseline_y` already carries the anchor's
/// dy and the baseline's own font shift; `align` is `vega_fit::text_ink`'s
/// vocabulary; `font_style` is `Font::style_attr`.
#[allow(clippy::too_many_arguments)]
pub fn write_value_label(
    svg: &mut String,
    x: f64,
    baseline_y: f64,
    align: &str,
    family: &str,
    size: f64,
    weight: &str,
    font_style: &str,
    color: &str,
    label_attr: &str,
    text: &str,
) {
    use std::fmt::Write as _;
    let text_anchor = match align {
        "left" => "start",
        "right" => "end",
        _ => "middle",
    };
    let _ = write!(
        svg,
        "<text x=\"{x:.2}\" y=\"{baseline_y:.2}\" text-anchor=\"{text_anchor}\" font-family=\"{}\" \
         font-size=\"{size}\" font-weight=\"{}\"{font_style} fill=\"{}\"{label_attr}>{}</text>",
        crate::svg::vega_escape_attr(family),
        crate::svg::xml_escape_attr(weight),
        crate::svg::xml_escape_attr(color),
        crate::svg::xml_escape(text),
    );
}

#[cfg(test)]
mod vega_ascending_tests {
    use super::*;
    use serde_json::json;

    fn sorted(v: Value) -> Vec<String> {
        let values = v.as_array().unwrap().clone();
        vega_ascending_sort(values).unwrap().iter().map(|x| x.to_string().trim_matches('"').to_string()).collect()
    }

    #[test]
    fn a_mixed_domain_sorts_as_v8_runs_it() {
        assert_eq!(sorted(json!([3, "b", 1, "a"])), ["3", "b", "1", "a"]);
        assert_eq!(sorted(json!([10, 2, "a", "B"])), ["2", "10", "B", "a"]);
        assert_eq!(sorted(json!(["d", "c", 2, 1, "a"])), ["c", "d", "1", "2", "a"]);
    }

    #[test]
    fn a_pure_domain_sorts_ascending() {
        assert_eq!(sorted(json!(["b", "a", "C"])), ["C", "a", "b"]);
    }
}

#[cfg(test)]
mod min_ranked_domain_tests {
    use super::*;
    use serde_json::json;

    fn rows(cells: &[(&str, Value)]) -> Vec<serde_json::Map<String, Value>> {
        cells
            .iter()
            .map(|(cat, by)| json!({"cat": cat, "by": by}).as_object().unwrap().clone())
            .collect()
    }

    fn ranked(cells: &[(&str, Value)], order: SortDir) -> Vec<String> {
        let rows = rows(cells);
        let refs: Vec<_> = rows.iter().collect();
        let domain = ["a", "b", "c", "d"].map(String::from).to_vec();
        let sort = ChartSort { by: "by".into(), order };
        min_ranked_domain(domain, &refs, |row| row_str(row, "cat"), &sort).unwrap()
    }

    /// JS `<` compares two strings by code unit, so a text sort key ranks
    /// lexicographically and its `min` is the first in that order.
    #[test]
    fn a_text_sort_key_ranks_by_its_lexicographic_min() {
        let cells = [("a", json!("z")), ("a", json!("m")), ("b", json!("b")), ("c", json!("a")), ("d", json!("B"))];
        assert_eq!(ranked(&cells, SortDir::Asc), ["d", "c", "b", "a"]);
        assert_eq!(ranked(&cells, SortDir::Desc), ["a", "b", "c", "d"]);
    }

    /// A category with no sort value ranks below every other: first ascending,
    /// last descending, ties keeping first-appearance order.
    #[test]
    fn a_category_with_no_value_leads_ascending_and_trails_descending() {
        let cells = [("a", json!(2)), ("b", Value::Null), ("c", json!(1)), ("c", json!(1)), ("d", json!(2))];
        assert_eq!(ranked(&cells, SortDir::Asc), ["b", "c", "a", "d"]);
        assert_eq!(ranked(&cells, SortDir::Desc), ["a", "d", "c", "b"]);
    }

    /// Numbers and bools coerce to numbers, a number against a non-numeric
    /// string compares false both ways, so the first value stays the `min`.
    #[test]
    fn a_value_js_cannot_order_against_the_min_leaves_it_in_place() {
        assert!(js_less(&json!(1), &json!("10")));
        assert!(js_less(&json!(false), &json!(true)));
        assert!(!js_less(&json!(1), &json!("x")) && !js_less(&json!("x"), &json!(1)));
        let cells = [("a", json!(5)), ("a", json!("x")), ("b", json!(7))];
        assert_eq!(ranked(&cells, SortDir::Asc), ["c", "d", "a", "b"]);
    }
}

#[cfg(test)]
mod axis_title_text_tests {
    use super::*;

    /// Vega draws a wrapped title's rows one `lineHeight` (font size plus 2)
    /// apart: 13px at the theme's 11px.
    #[test]
    fn a_wrapped_title_steps_by_vegas_line_height() {
        let style: AxisTitle = serde_json::from_value(serde_json::json!({
            "font": {"family": "sans-serif", "size": 11.0, "weight": "600"}, "padding": 10.0, "visible": true
        }))
        .unwrap();
        let mut out = String::new();
        emit_axis_title_at(&mut out, "first\nsecond", &style, 50.0, 30.0, None);
        assert!(out.contains("dy=\"13\""), "{out}");
        assert_eq!(extra_title_lines("first\nsecond", 11.0), 13.0);
    }

    #[test]
    fn an_authored_newline_reads_as_a_space() {
        let labels = FieldLabels::new();
        assert_eq!(axis_title_text("revenue", Some("Net\nrevenue"), &labels).unwrap(), "Net revenue");
    }
}

#[cfg(test)]
mod row_str_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn row_str_renders_a_bool_json_native_not_pythons_str() {
        let row = json!({"active": true, "inactive": false}).as_object().unwrap().clone();
        assert_eq!(row_str(&row, "active"), "true");
        assert_eq!(row_str(&row, "inactive"), "false");
    }
}

// ── Wide-measures fold ───────────────────────────────────────────────────────
//
// A wide (`y: [a, b]`) cartesian chart unfolds into one row per (source row,
// measure) — production's VL `fold` transform, mirrored host-side by
// `unfold_wide_rows` (`compile/resolve/chart/_wide_fields.py`) so
// non-render code can reason about the long rows the same way an authored
// `color:` chart's rows already are. The fold is the ONE substitution a
// wide-capable family needs: replace `payload.rows` with its unfolded form
// and the chart's `color` field with `WIDE_LABEL_FIELD`, once, before any
// other law runs — `y` is already `WIDE_VALUE_FIELD` by construction, since
// the resolver bakes it onto the payload's own `y` (`bake_wide_measures_kwargs`).

/// Field the VL fold transform emits for the folded numeric value — mirrors
/// `WIDE_VALUE_FIELD`. A wide payload's own `y` is already this constant
/// (baked by the resolver), so no family needs to set it itself.
pub const WIDE_VALUE_FIELD: &str = "__dbt_charts_wide_value__";
/// The key under which a row whose date x was floored to its time bucket keeps
/// the date it carried, which production's tooltip and link read.
pub const RAW_X: &str = "\u{1d}raw_x";

/// The raw measure name the fold keys each row by — mirrors `WIDE_KEY_FIELD`,
/// which a link's `{{ color }}` reads.
pub const WIDE_KEY_FIELD: &str = "__dbt_charts_wide_key__";
/// Human-readable series label the fold's color field carries — mirrors
/// `WIDE_LABEL_FIELD`. Substituted for the chart's own `color` field once,
/// at the top of each wide-capable family's render entry point.
pub const WIDE_LABEL_FIELD: &str = "__dbt_charts_wide_label__";
/// Separator between a `color:` dimension's value and the measure name in a
/// composite series label; mirrors `WIDE_SERIES_SEPARATOR`.
pub const WIDE_SERIES_SEPARATOR: &str = " - ";

/// Display string for a wide chart's `color:` dimension value — the ONE
/// place production builds a series label host-side with Python `str()`
/// (`unfold_wide_rows`, `_wide.py::_label_expression`'s
/// `json.dumps(str(value))`), rather than letting Vega draw the raw datum:
/// a bool renders `"True"`/`"False"`, disagreeing with `row_str`'s
/// JSON-native `"true"`/`"false"` deliberately — everywhere else in this
/// crate a bool cell reaches Vega's own `toString`, never Python's.
fn wide_dimension_str(row: &serde_json::Map<String, Value>, field: &str) -> String {
    match row.get(field) {
        Some(Value::Bool(b)) => if *b { "True" } else { "False" }.to_string(),
        _ => row_str(row, field),
    }
}

/// Pre-fold wide rows into long form — the Rust mirror of `unfold_wide_rows`.
/// A row whose `dimension` cell is null names no series (the whole row is
/// dropped); a row whose MEASURE cell is null contributes no row for that
/// measure alone. Every other original column rides along unchanged on each
/// emitted row (`row.clone()` first, fold fields added after), so a
/// per-row lookup against a folded row still sees the same source values.
pub fn unfold_wide_rows(
    rows: &[serde_json::Map<String, Value>],
    measures: &[String],
    dimension: Option<&str>,
) -> Vec<serde_json::Map<String, Value>> {
    let mut out = Vec::new();
    for row in rows {
        if let Some(dim) = dimension {
            if row.get(dim).map(Value::is_null).unwrap_or(true) {
                continue;
            }
        }
        for measure in measures {
            let Some(value) = row.get(measure) else { continue };
            if value.is_null() {
                continue;
            }
            let mut folded = row.clone();
            let label = match dimension {
                None => measure.clone(),
                Some(dim) => format!("{}{WIDE_SERIES_SEPARATOR}{measure}", wide_dimension_str(row, dim)),
            };
            folded.insert(WIDE_LABEL_FIELD.to_string(), Value::String(label));
            folded.insert(WIDE_KEY_FIELD.to_string(), Value::String(measure.clone()));
            folded.insert(WIDE_VALUE_FIELD.to_string(), value.clone());
            out.push(folded);
        }
    }
    out
}

/// Distinct non-null values of `dimension`, in first-seen order, deduped by
/// their `wide_dimension_str` display form — mirrors `wide_dimension_values`.
/// Every composite label (`unfold_wide_rows` above) already keys on
/// `wide_dimension_str`, so dedup can't desync from the string the label
/// itself carries.
pub fn wide_dimension_values(
    rows: &[serde_json::Map<String, Value>],
    dimension: &str,
) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for row in rows {
        if row.get(dimension).map(Value::is_null).unwrap_or(true) {
            continue;
        }
        let s = wide_dimension_str(row, dimension);
        if seen.insert(s.clone()) {
            out.push(s);
        }
    }
    out
}

/// Sorted color-scale domain of a wide chart, RAW identity (production's
/// `raw_wide_series_names`): the authored measures (from the resolved chart,
/// not the rows — a measure null on every row still owns a domain/palette
/// slot), crossed with every observed `dimension` value when one is authored.
pub fn wide_series_names(
    measures: &[String],
    dimension: Option<&str>,
    rows: &[serde_json::Map<String, Value>],
) -> Vec<String> {
    let mut names: Vec<String> = match dimension {
        None => measures.to_vec(),
        Some(dim) => wide_dimension_values(rows, dim)
            .iter()
            .flat_map(|v| measures.iter().map(move |m| format!("{v}{WIDE_SERIES_SEPARATOR}{m}")))
            .collect(),
    };
    names.sort();
    names
}

/// Vega's `center` stack offset: a category whose stack piles `total` of
/// magnitude floats to the middle of the tallest one, `max_total`.
pub fn center_offset(max_total: f64, total: f64) -> f64 {
    (max_total - total) / 2.0
}

/// Vega's `center` and `normalize` offsets pile `|v|` (`stackCenter`,
/// `stackNormalize` in `vega-transforms/Stack.js`): a negative value draws its
/// magnitude upward, never below the baseline as `zero` stacks it.
pub fn abs_total(values: impl IntoIterator<Item = f64>) -> f64 {
    values.into_iter().map(f64::abs).sum()
}

/// Vega's `stackZero` running sums for one category (`vega-transforms/Stack.js`):
/// a value below zero descends from 0, anything else (zero included) climbs
/// from `start`. A segment is `(start, end)`; the mark's `y` edge is `end`.
#[derive(Clone, Copy, Debug)]
pub struct StackZero {
    pos: f64,
    neg: f64,
}

impl StackZero {
    pub fn new(start: f64) -> Self {
        Self { pos: start, neg: 0.0 }
    }

    /// The next segment; a caller that normalizes passes the already
    /// normalized value for a non-negative one.
    pub fn push(&mut self, v: f64) -> (f64, f64) {
        let sum = if v < 0.0 { &mut self.neg } else { &mut self.pos };
        let start = *sum;
        *sum += v;
        (start, *sum)
    }

    /// The positive pile's height; the column total when nothing is negative.
    pub fn pos_total(&self) -> f64 {
        self.pos
    }

    pub fn neg_total(&self) -> f64 {
        self.neg
    }
}

/// Baseline-first series order of a stacked chart — port of
/// `core/utils.py::sorted_series_by_stack_order`. `value` (the default when
/// `stack_order` is unauthored): descending global sum of `y_field`, ties by
/// name; `alphabetical`: by name; `data`: first encounter of the series cell
/// in `rows`, a series never encountered ranking last. Any other string
/// refuses by name rather than ranking by a guessed rule.
pub fn sorted_series_by_stack_order(
    series: &[String],
    rows: &[serde_json::Map<String, Value>],
    series_field: &str,
    y_field: &str,
    stack_order: Option<&str>,
) -> Result<Vec<String>, String> {
    let mut ordered = series.to_vec();
    match stack_order {
        Some("alphabetical") => ordered.sort(),
        Some("data") => {
            let mut encounter: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
            for row in rows {
                if row.get(series_field).is_some_and(|v| !v.is_null()) {
                    let next = encounter.len();
                    encounter.entry(row_str(row, series_field)).or_insert(next);
                }
            }
            let unseen = encounter.len();
            ordered.sort_by(|a, b| {
                let rank = |s: &String| encounter.get(s).copied().unwrap_or(unseen);
                rank(a).cmp(&rank(b)).then_with(|| a.cmp(b))
            });
        }
        None | Some("value") => {
            let mut sums: std::collections::HashMap<String, f64> = std::collections::HashMap::new();
            for row in rows {
                if row.get(series_field).is_some_and(|v| !v.is_null()) {
                    if let Some(y) = row_num(row, y_field) {
                        *sums.entry(row_str(row, series_field)).or_insert(0.0) += y;
                    }
                }
            }
            let total = |s: &String| sums.get(s).copied().unwrap_or(0.0);
            ordered.sort_by(|a, b| total(b).total_cmp(&total(a)).then_with(|| a.cmp(b)));
        }
        Some(other) => {
            return Err(format!(
                "stack: style.stack_order={other:?} not supported — only \"value\", \"data\", or \"alphabetical\""
            ));
        }
    }
    Ok(ordered)
}

/// Y-axis title fallback for a folded multi-measure chart — mirrors
/// `wide_measures_title`: each measure's label (`wide_measure_labels_for`),
/// joined with ", ". An authored `y_label` always overrides this.
pub fn wide_measures_title(
    measures: &[String],
    field_labels: &FieldLabels,
) -> Result<String, String> {
    let labels = display_text::wide_measure_labels_for(measures, field_labels)?;
    Ok(measures
        .iter()
        .map(|m| labels[m].as_str())
        .collect::<Vec<_>>()
        .join(&display_text::language::tr("controls.list_join", None, &[])?))
}

/// `_wide_fields.py::humanize_wide_series_name`: a wide series' painted
/// label. A bare measure name maps to its label; a `<value><sep><measure>`
/// composite keeps the dimension value verbatim and labels the measure half;
/// anything else (not a wide series) is its own display text.
pub fn wide_series_display(name: &str, wide_labels: &std::collections::HashMap<String, String>) -> String {
    if let Some(label) = wide_labels.get(name) {
        return label.clone();
    }
    match name.rsplit_once(WIDE_SERIES_SEPARATOR) {
        Some((value, measure)) => match wide_labels.get(measure) {
            Some(label) => format!("{value}{WIDE_SERIES_SEPARATOR}{label}"),
            None => name.to_string(),
        },
        None => name.to_string(),
    }
}

#[cfg(test)]
mod wide_fields_tests {
    use super::*;
    use serde_json::json;

    fn row(v: serde_json::Value) -> serde_json::Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn unfold_drops_a_null_measure_cell() {
        let rows = vec![row(json!({"month": "Jan", "a": 1.0, "b": Value::Null}))];
        let measures = vec!["a".to_string(), "b".to_string()];
        let out = unfold_wide_rows(&rows, &measures, None);
        assert_eq!(out.len(), 1, "only measure a's row survives: {out:?}");
        assert_eq!(out[0][WIDE_LABEL_FIELD], json!("a"));
        assert_eq!(out[0][WIDE_VALUE_FIELD], json!(1.0));
    }

    #[test]
    fn unfold_drops_a_null_dimension_row_entirely() {
        let rows = vec![
            row(json!({"month": "Jan", "cat": Value::Null, "a": 1.0})),
            row(json!({"month": "Feb", "cat": "Tools", "a": 2.0})),
        ];
        let measures = vec!["a".to_string()];
        let out = unfold_wide_rows(&rows, &measures, Some("cat"));
        assert_eq!(out.len(), 1, "the null-dimension row names no series: {out:?}");
        assert_eq!(out[0]["month"], json!("Feb"));
    }

    #[test]
    fn unfold_composes_the_dimension_value_and_measure_into_the_label() {
        let rows = vec![row(json!({"month": "Jan", "cat": "Tools", "a": 1.0}))];
        let measures = vec!["a".to_string()];
        let out = unfold_wide_rows(&rows, &measures, Some("cat"));
        assert_eq!(out[0][WIDE_LABEL_FIELD], json!("Tools - a"));
    }

    /// A bool `color:` dimension composes Python's `str()` (`"True"`), not
    /// JSON `toString` (`"true"`) — production builds this label host-side,
    /// unlike every other bool cell in this crate, which Vega draws raw.
    #[test]
    fn unfold_composes_a_bool_dimension_value_as_pythons_str() {
        let rows = vec![row(json!({"month": "Jan", "active": true, "a": 1.0}))];
        let measures = vec!["a".to_string()];
        let out = unfold_wide_rows(&rows, &measures, Some("active"));
        assert_eq!(out[0][WIDE_LABEL_FIELD], json!("True - a"));
    }

    #[test]
    fn unfold_keeps_the_original_columns_on_every_folded_row() {
        let rows = vec![row(json!({"month": "Jan", "a": 1.0, "b": 2.0}))];
        let measures = vec!["a".to_string(), "b".to_string()];
        let out = unfold_wide_rows(&rows, &measures, None);
        assert_eq!(out.len(), 2);
        assert!(out.iter().all(|r| r["month"] == json!("Jan")));
    }

    #[test]
    fn wide_series_names_sorts_the_plain_measure_list() {
        let measures = vec!["revenue".to_string(), "profit".to_string()];
        assert_eq!(wide_series_names(&measures, None, &[]), vec!["profit", "revenue"]);
    }

    #[test]
    fn wide_series_names_sorts_the_dimension_x_measure_cross() {
        let rows = vec![
            row(json!({"cat": "Tools", "a": 1.0})),
            row(json!({"cat": "Electronics", "a": 2.0})),
        ];
        let measures = vec!["largest_order".to_string(), "avg_order_value".to_string()];
        assert_eq!(
            wide_series_names(&measures, Some("cat"), &rows),
            vec![
                "Electronics - avg_order_value",
                "Electronics - largest_order",
                "Tools - avg_order_value",
                "Tools - largest_order",
            ]
        );
    }

    #[test]
    fn wide_measures_title_joins_measure_labels() {
        let measures = vec!["revenue".to_string(), "fix-linked".to_string()];
        assert_eq!(wide_measures_title(&measures, &FieldLabels::new()).unwrap(), "Revenue, Fix linked");
        let labels = FieldLabels::from([("fix-linked".to_string(), "fix-linked".to_string())]);
        assert_eq!(wide_measures_title(&measures, &labels).unwrap(), "Revenue, fix-linked");
    }

    #[test]
    fn wide_series_display_labels_the_measure_half_only() {
        let labels = std::collections::HashMap::from([
            ("units_sold".to_string(), "units sold".to_string()),
        ]);
        assert_eq!(wide_series_display("units_sold", &labels), "units sold");
        assert_eq!(wide_series_display("EU - units_sold", &labels), "EU - units sold");
        assert_eq!(wide_series_display("EU", &labels), "EU");
    }
}

// Named engine format → d3 spec (`PREDEFINED_SPECS`). Shared by kpi.rs and
// table.rs — the only two families with a `format:` column that can name a
// predefined number format. The SI members carry an explicit `.3` because d3's
// `s` type defaults to six significant digits and `~` only trims trailing zeros:
// a bare `~s` renders 50752.9 as `50.7529 K`, six significant figures under a name
// that promises brevity. `compact` and `currency_compact` are the deliberate
// two-figure exception (`1.2M`, `45K`).
pub fn predefined_number_spec(name: &str) -> Option<&'static str> {
    d3rs::format::predefined_spec(name).map(|spec| spec.d3)
}

/// Python's `str.strip()` / `.lstrip()` / `.rstrip()`: the whitespace set is
/// `str.isspace`, not Rust's `trim`. `format_kpi_parts` merges an affix lane
/// with `.lstrip()` (prefix) and `.rstrip()` (suffix) under `keep_affix_gaps`
/// (`compile/format_value.py:379-382`) and with `.strip()` otherwise; the KPI
/// and the table share these.
pub(crate) fn py_strip(s: &str) -> &str {
    s.trim_matches(mdsvg::pygments_re::is_py_space)
}

pub(crate) fn py_lstrip(s: &str) -> &str {
    s.trim_start_matches(mdsvg::pygments_re::is_py_space)
}

pub(crate) fn py_rstrip(s: &str) -> &str {
    s.trim_end_matches(mdsvg::pygments_re::is_py_space)
}

/// The affix step of `format_kpi_parts` (`compile/format_value.py:379-390`):
/// the `(prefix, suffix)` lanes of a numeric cell from its authored and
/// d3-embedded pieces. Under `keep_affix_gaps` only the far edge of each lane
/// is stripped (`.lstrip()` / `.rstrip()`), so an authored gap against the
/// number survives; otherwise both edges go and the locale's own gap between
/// digits and its currency or percent sign is put back. The KPI and the table
/// share it.
pub(crate) fn merge_affix_lanes(
    explicit_prefix: &str,
    d3_prefix: &str,
    magnitude_suffix: &str,
    d3_suffix: &str,
    explicit_suffix: &str,
    keep_affix_gaps: bool,
) -> (String, String) {
    let prefix = format!("{explicit_prefix}{d3_prefix}");
    let suffix = format!("{magnitude_suffix}{d3_suffix}{explicit_suffix}");
    if keep_affix_gaps {
        return (py_lstrip(&prefix).to_string(), py_rstrip(&suffix).to_string());
    }
    let mut prefix = py_strip(&prefix).to_string();
    let mut suffix = py_strip(&suffix).to_string();
    if !py_strip(d3_prefix).is_empty() {
        prefix.push_str(&d3_prefix[py_rstrip(d3_prefix).len()..]);
    }
    if !py_strip(d3_suffix).is_empty() && py_strip(magnitude_suffix).is_empty() {
        suffix = format!("{}{suffix}", &d3_suffix[..d3_suffix.len() - py_lstrip(d3_suffix).len()]);
    }
    (prefix, suffix)
}

/// The affix step of a native formatter (`format_value.py:272-275`): the
/// authored prefix is `.lstrip()`ped under `keep_affix_gaps` (`.strip()`ped
/// otherwise) and the suffix is the unit followed by the `.rstrip()`ped
/// authored suffix.
pub(crate) fn native_affix_lanes(
    explicit_prefix: &str,
    unit: &str,
    explicit_suffix: &str,
    keep_affix_gaps: bool,
) -> (String, String) {
    let prefix = if keep_affix_gaps { py_lstrip(explicit_prefix) } else { py_strip(explicit_prefix) };
    (prefix.to_string(), format!("{unit}{}", py_rstrip(explicit_suffix)))
}

/// A format's fields as `format_kpi_parts` reads them (`resolve_format_parts`);
/// the default is no format.
#[derive(Default)]
pub(crate) struct FormatParts<'a> {
    pub spec: Option<&'a str>,
    /// The predefined name the spec was resolved from (`ResolvedFormat.raw`);
    /// the house rules (analytic notation, the sub-unit floor) key on it.
    pub raw: Option<&'a str>,
    pub prefix: &'a str,
    pub suffix: &'a str,
    pub notation: Option<&'a str>,
    pub placement: SignPlacement,
}

/// A table column's shared magnitude as one row sees it
/// (`ResolvedColumnSharedScale` and `is_anchor`). Production's authored
/// `notation` overrides the scale's register (`format_value.py:319-323`):
/// `FormatParts.notation` picks the word's register over the mode's.
pub(crate) struct ScaledColumn<'a> {
    pub exponent: i32,
    pub digit_spec: &'a str,
    /// `SuffixMode.REPEAT`: the narrative word on every nonzero row; else the
    /// analytic word on the anchor row alone.
    pub repeat: bool,
    pub is_anchor: bool,
}

/// `format_kpi_parts`' switches.
#[derive(Default)]
pub(crate) struct PartsOptions<'a> {
    /// d3's raw SI letter stays the magnitude suffix (`native=`).
    pub native: bool,
    pub keep_affix_gaps: bool,
    pub sign_in_prefix: bool,
    /// The column declares its unit once, so a `$` leads the digits whatever
    /// the locale's placement (`anchored=`).
    pub anchored: bool,
    pub shared_scale: Option<ScaledColumn<'a>>,
    /// The board's plain-digit sibling of each adaptive member (`currency`,
    /// `number`), resolved against its `style.formats`
    /// (`board_style.sub_unit_specs`); a member without an entry refuses. None
    /// reads the engine's predefined siblings.
    pub sub_unit_specs: Option<&'a std::collections::BTreeMap<String, String>>,
    /// The cell's own JSON number, for a caller whose production twin keeps an
    /// integer cell an `int` (the table; the KPI coerces to float first). A
    /// refusal quotes it as that type's `repr`.
    pub cell: Option<&'a serde_json::Number>,
}

/// `format_kpi_parts`' lanes, and the same pieces as `format_value` wraps
/// them (`format_d3(value, spec, prefix, suffix)`): the authored affixes raw,
/// outer whitespace included, around the number and its magnitude word, which
/// is what a width measurement reads.
pub(crate) struct KpiParts {
    pub prefix: String,
    pub number: String,
    pub suffix: String,
    pub measured_prefix: String,
    pub measured_suffix: String,
}

/// Format a numeric cell into its `(prefix, number, suffix)` lanes.
///
/// Mirrors `format_kpi_parts` (`compile/format_value.py:194-391`), the one
/// reading of the lane law for the KPI (`kpi.py:364-371`: no shared scale, no
/// anchor, the sign placed into the prefix) and the table's numeric lanes
/// (`table.py:2618-2626`: `keep_affix_gaps`, `anchored`, the column's shared
/// scale). The currency and percent affixes are the active locale's: d3 never
/// sees the `$`, so a suffix currency (`1.235 €`) and a percent with a gap
/// (`50,0 %`) land in the suffix lane with their gap.
pub(crate) fn format_kpi_parts(value: f64, f: &FormatParts, o: &PartsOptions) -> Result<KpiParts, Diagnostic> {
    let (explicit_prefix, explicit_suffix) = (f.prefix, f.suffix);
    // The spec is resolved; the house rules key on the predefined name it was
    // resolved from (`parts.raw` / `is_house`, format_value.py:255-256).
    let is_house = f.raw.is_some_and(|raw| predefined_number_spec(raw).is_some());
    let mut format_spec = f.spec.unwrap_or("").to_string();

    // A shared scale has baked the column's one magnitude, so a per-row
    // decision would second-guess that vote (format_value.py:258-262).
    if o.shared_scale.is_none() {
        if let Some((raw, fallback)) = f.raw.and_then(|raw| Some((raw, d3rs::format::sub_unit_fallback(raw)?))) {
            let sibling = match o.sub_unit_specs {
                Some(siblings) => siblings
                    .get(raw)
                    .map(String::as_str)
                    .ok_or_else(|| format!("payload carries no sub-unit spec for {raw:?}"))?,
                None => predefined_number_spec(fallback)
                    .expect("sub-unit fallback name resolves to a predefined spec"),
            };
            format_spec = d3rs::format::sub_unit_spec_for_value(&format_spec, sibling, value)?;
        }
    }

    // format_value.py:264-275: a native formatter knows its own unit; the
    // authored affix rides around number-and-unit.
    if let Some((number, unit)) = d3rs::native::predefined_native(&format_spec, value).map_err(Diagnostic::refused)? {
        let (prefix, number) = if o.sign_in_prefix {
            let (prefix, _, number) = sign_into_prefix(explicit_prefix, "", &number, f.placement);
            (prefix, number)
        } else {
            (explicit_prefix.to_string(), number)
        };
        let (prefix_lane, suffix_lane) = native_affix_lanes(&prefix, &unit, explicit_suffix, o.keep_affix_gaps);
        return Ok(KpiParts {
            prefix: prefix_lane,
            number,
            suffix: suffix_lane,
            measured_prefix: explicit_prefix.to_string(),
            measured_suffix: format!("{unit}{explicit_suffix}"),
        });
    }

    check_percent_range(value, o.cell, &format_spec)?;

    let locale = active_number_locale();
    let mut d3_prefix = String::new();
    // As `format_value` prints it, gap included; the lanes carry it stripped.
    let mut magnitude_word = String::new();
    let mut d3_suffix = String::new();
    let mut number_str;
    if format_spec.is_empty() {
        // format_value.py:363-372: no spec is exact digits, an integral value
        // below 1e15 through d3's `,d` (a negative zero is `0`) and every
        // other float through `,.2f`, under the board's locale; d3 supplies
        // the house minus.
        number_str = if value == value.trunc() && value.abs() < 1e15 {
            crate::number_locale::d3_format(",d", if value == 0.0 { 0.0 } else { value })?
        } else {
            crate::number_locale::d3_format(",.2f", value)?
        };
    } else {
        if format_spec.contains('$') {
            [d3_prefix, d3_suffix] =
                if o.anchored { [locale.currency_front(), String::new()] } else { locale.locale.currency.clone() };
        }
        let format_spec_clean = format_spec.replace('$', "");
        let effective_notation: Option<String> = f.notation.map(str::to_string).or_else(|| {
            if is_house {
                Some("analytic".to_string())
            } else {
                None
            }
        });
        if let Some(scale) = &o.shared_scale {
            // The column's one baked magnitude, never the value's own SI
            // letter (format_value.py:300-331).
            number_str = crate::number_locale::d3_format(scale.digit_spec, value / 10_f64.powi(scale.exponent))?;
            if value != 0.0 && (scale.repeat || scale.is_anchor) {
                let analytic = match f.notation {
                    None => !scale.repeat,
                    Some("analytic") => true,
                    Some("narrative") => false,
                    Some(other) => {
                        return Err(Diagnostic::refused(format!(
                            "format notation {other:?} is neither analytic nor narrative"
                        )))
                    }
                };
                magnitude_word = suffix_at_register(scale.exponent, analytic)?;
            }
        } else if format_spec_clean.ends_with('s') {
            let raw_formatted = crate::number_locale::d3_format(&format_spec_clean, value)?;
            let (num_part, si_char) = split_si_suffix(&raw_formatted);
            number_str = num_part.to_string();
            if !MAGNITUDE_CHARS.contains(&si_char) {
                // Not a recognized magnitude prefix (e.g. milli/micro) — stays
                // glued to the number string, matching production's loop that
                // only substitutes for a key it has a table entry for
                // (the locale's `magnitudes`). Checked before the
                // native/notation branch below: an unrecognized char must stay
                // glued regardless of notation, not just on the native path.
                number_str = raw_formatted;
            } else if o.native || effective_notation.is_none() {
                magnitude_word = si_char.to_string();
            } else {
                magnitude_word = printed_magnitude_word(si_char, effective_notation.as_deref().unwrap_or(""))?;
            }
        } else {
            number_str = crate::number_locale::d3_format(&format_spec_clean, value)?;
        }
        if format_spec.contains('%') {
            if let Some(stripped) = number_str.strip_suffix(locale.locale.percent.as_str()) {
                d3_suffix = locale.locale.percent.clone();
                number_str = stripped.to_string();
            }
        }
    }

    // The lane drops the word's leading gap: the painter carries it as
    // geometry (format_value.py's `.strip()`).
    let magnitude_suffix = py_strip(&magnitude_word);
    let measured_prefix = format!("{explicit_prefix}{d3_prefix}");
    let measured_suffix = format!("{magnitude_word}{d3_suffix}{explicit_suffix}");
    // format_value.py:374-391.
    let (explicit_prefix, d3_prefix, number) = if o.sign_in_prefix {
        sign_into_prefix(explicit_prefix, &d3_prefix, py_strip(&number_str), f.placement)
    } else {
        (explicit_prefix.to_string(), d3_prefix, py_strip(&number_str).to_string())
    };
    let (prefix, suffix) = merge_affix_lanes(
        &explicit_prefix,
        &d3_prefix,
        magnitude_suffix,
        &d3_suffix,
        explicit_suffix,
        o.keep_affix_gaps,
    );
    Ok(KpiParts { prefix, number, suffix, measured_prefix, measured_suffix })
}

// SI prefixes production splits out of a magnitude suffix (format_d3.py's
// _D3_TO_ANALYTIC/_D3_TO_NARRATIVE keys). Anything else stays glued to the
// number string, unstripped — matches the production loop, which only
// recognizes these characters.
const MAGNITUDE_CHARS: [&str; 8] = ["k", "M", "G", "T", "P", "E", "Z", "Y"];

/// `_check_percent_range` (`format_value.py:78-93`): a `%` spec that does not
/// open with `+` (the delta formats) is fed ratios, so a value of 10 or more
/// is a forgotten division by 100 and refuses. The refusal quotes the value
/// as production holds it (`{value!r}`): the cell's own number when the caller
/// kept it, else the float compared.
fn check_percent_range(
    value: f64,
    cell: Option<&serde_json::Number>,
    format_spec: &str,
) -> Result<(), Diagnostic> {
    let ratio_shaped = value.abs() >= 10.0;
    if !format_spec.ends_with('%') || format_spec.starts_with('+') || !ratio_shaped {
        return Ok(());
    }
    let quoted = cell.map_or_else(|| serde_json::json!(value), |n| serde_json::Value::Number(n.clone()));
    Err(Diagnostic::new(&codes::ERR_PERCENT_RANGE, [("format_spec", serde_json::json!(format_spec)), ("value", quoted)]))
}

/// `_sign_into_prefix` (`format_value.py:178-191`): the number's leading sign
/// (`split_leading_sign`, `text/format_d3.py:539-543`: `−`, `+` or `-`) moves
/// ahead of both prefixes, or, `after_prefix` with an authored prefix, sits
/// between it and d3's `$`, which joins the number lane. No prefix: untouched.
fn sign_into_prefix(
    explicit_prefix: &str,
    d3_prefix: &str,
    number: &str,
    placement: SignPlacement,
) -> (String, String, String) {
    if explicit_prefix.is_empty() && d3_prefix.is_empty() {
        return (String::new(), String::new(), number.to_string());
    }
    let (sign, digits) = match number.chars().next() {
        Some(c @ ('\u{2212}' | '+' | '-')) => number.split_at(c.len_utf8()),
        _ => ("", number),
    };
    if !explicit_prefix.is_empty() && placement == SignPlacement::AfterPrefix {
        return (explicit_prefix.to_string(), String::new(), format!("{sign}{d3_prefix}{digits}"));
    }
    (format!("{sign}{explicit_prefix}"), d3_prefix.to_string(), digits.to_string())
}

#[cfg(test)]
mod axis_title_tests {
    use super::*;

    fn title_style(size: f64, padding: f64, visible: Option<bool>) -> AxisTitle {
        serde_json::from_value(serde_json::json!({
            "font": {"size": size, "case": "title"},
            "padding": padding,
            "visible": visible,
        }))
        .unwrap()
    }

    #[test]
    fn axis_title_text_prefers_axis_label_then_field_label() {
        let labels = FieldLabels::from([("category".to_string(), "Product line".to_string())]);
        assert_eq!(axis_title_text("category", Some("Category Label"), &labels).unwrap(), "Category Label");
        assert_eq!(axis_title_text("category", None, &labels).unwrap(), "Product line");
        assert_eq!(axis_title_text("order_month", None, &FieldLabels::new()).unwrap(), "Order month");
    }

    #[test]
    fn field_display_text_cases_derived_text_but_not_authored_labels() {
        let labels = FieldLabels::from([("seg".to_string(), "customer SEG".to_string())]);
        assert_eq!(field_display_text("seg", &labels, "title").unwrap(), "customer SEG");
        assert_eq!(field_display_text("revenue_usd", &labels, "title").unwrap(), "Revenue ($)");
    }

    #[test]
    fn the_seam_decodes_decimal_and_non_finite_cells_and_keeps_their_text() {
        let mut rows = vec![serde_json::json!({
            "d": {"decimal": "2.50"},
            "n": {"non_finite": "nan", "text": "NaN"},
            "f": 1.5,
            "spark": [1, {"non_finite": "inf", "text": "inf"}, {"decimal": "3"}],
        })
        .as_object()
        .unwrap()
        .clone()];
        let text = decode_cells(&mut rows).unwrap();
        assert_eq!(
            Value::Object(rows[0].clone()),
            serde_json::json!({"d": 2.5, "n": null, "f": 1.5, "spark": [1, null, 3.0]})
        );
        assert_eq!(text[0]["d"], CellTag { text: "2.50".into(), non_finite: None });
        assert_eq!(text[0].len(), 2);
        assert!(text[0]["n"].non_finite.unwrap().is_nan() && text[0]["n"].text == "NaN");
        assert_eq!(rows[0]["d"], serde_json::json!(2.5));
        let mut bad = vec![serde_json::json!({"d": {"decimal": "not a number"}}).as_object().unwrap().clone()];
        assert!(decode_cells(&mut bad).is_err());
        let mut unknown = vec![serde_json::json!({"n": {"non_finite": "huge"}}).as_object().unwrap().clone()];
        assert!(decode_cells(&mut unknown).is_err());
    }

    #[test]
    fn value_ticks_run_outward_from_the_axis_edge_on_each_row() {
        let t = ticks(serde_json::json!({"visible": true, "color": "#112233", "width": 2.0}));
        let mut left = String::new();
        emit_value_ticks(&mut left, &t, [tick_row(10.0, 3.4)], 50.5, -1.0, 6.0).unwrap();
        assert_eq!(
            left,
            "<line x1=\"44.50\" y1=\"13.50\" x2=\"50.50\" y2=\"13.50\" \
             stroke=\"#112233\" stroke-width=\"2\"/>"
        );
        let mut right = String::new();
        emit_value_ticks(&mut right, &t, [13.5], 50.5, 1.0, 6.0).unwrap();
        assert!(right.contains("x1=\"50.50\"") && right.contains("x2=\"56.50\""));
    }

    #[test]
    fn a_tick_without_an_ink_refuses_by_name() {
        let t = ticks(serde_json::json!({"visible": true}));
        let err = emit_value_ticks(&mut String::new(), &t, [13.5], 50.5, 1.0, 6.0).unwrap_err();
        assert_eq!(err, "axis: ticks.color absent (theme required)");
    }

    fn ticks(json: serde_json::Value) -> AxisTicks {
        serde_json::from_value(json).unwrap()
    }

    #[test]
    fn axis_title_band_matches_measured_production_constant() {
        // charts/bar-charts_4 category_columns_styled / charts/area-charts_0
        // signup_area / guides/getting-started_0 documents_chart all resolve
        // title.font.size=11.0, title.padding=10.0 — measured band is exactly
        // 21.0 (deleting either term from the sum must fail this).
        let style = title_style(11.0, 10.0, Some(true));
        assert_eq!(axis_title_band(&style, "revenue"), 21.0);
    }

    #[test]
    fn a_restored_axis_title_reserves_fifteen_pixels_beside_its_axis() {
        // Vega's default title (11px, 4px padding): 4 + round(2.31) + round(8.8).
        let title = vega_default_axis_title(None);
        assert_eq!(y_title_extra(&title, "Conversion"), 15.0);
        assert_eq!(y_title_extra(&title, ""), 0.0);
    }

    #[test]
    fn axis_title_band_is_zero_when_hidden_or_textless() {
        let visible = title_style(11.0, 10.0, Some(true));
        assert_eq!(axis_title_band(&visible, ""), 0.0);
        let hidden = title_style(11.0, 10.0, Some(false));
        assert_eq!(axis_title_band(&hidden, "revenue"), 0.0);
    }

    #[test]
    fn emit_axis_title_bottom_places_baseline_at_measured_offset() {
        // Measured production: bar-charts_4's x-axis title baseline sits at
        // val tick label baseline (14.8, from val_pad=6 + val_font_size*0.8)
        // + label_font_size (11) + title.padding (10) = 35.8, within 0.2px of
        // the golden's 36.0 (see axis_title_band's doc for the residual).
        // The title lives inside Vega's axis group, which the ticks and
        // gridlines already crisp-shift by +0.5, so its x is `center_x +
        // 0.5`.
        let style = title_style(11.0, 10.0, Some(true));
        let mut out = String::new();
        emit_axis_title_bottom(&mut out, "revenue", &style, 239.0, 14.8, 11.0, None);
        assert!(out.contains("y=\"35.80\""), "expected baseline near 35.8: {out}");
        assert!(out.contains("x=\"239.50\""), "plot_x + 0.5 + pw/2: {out}");
        assert!(out.contains(">revenue<"));
    }

    /// The bottom title's x carries the axis group's +0.5 crisp translate
    /// (the same shift `tickRound`'d gridlines already take) — isolated
    /// here from the baseline math above so a regression that drops just
    /// the 0.5 term fails on its own.
    #[test]
    fn emit_axis_title_bottom_x_carries_the_axis_groups_crisp_translate() {
        let style = title_style(11.0, 10.0, Some(true));
        let mut out = String::new();
        emit_axis_title_bottom(&mut out, "revenue", &style, 100.0, 14.8, 11.0, None);
        assert!(out.contains("x=\"100.50\""), "center_x + 0.5: {out}");
    }

    #[test]
    fn emit_axis_title_rotated_places_title_past_the_label_edge() {
        // Measured production: bar-charts_4's category title x is exactly
        // 10.0px (title.padding) further from the plot than the label's own
        // left edge, plus the fixed 2.0px rotate nudge — deleting either term
        // collapses the title onto the label. The title's y (its position
        // ALONG the axis) carries the same +0.5 crisp translate as the x
        // does on a bottom title.
        let style = title_style(11.0, 10.0, Some(true));
        let mut out = String::new();
        emit_axis_title_rotated(&mut out, "category", &style, 16.0, 167.0, -1.0, None);
        assert!(out.contains("x=\"4.00\""), "expected 16 - (10 + 2): {out}");
        assert!(out.contains("rotate(-90 4.00 167.50)"), "plot_y + 0.5 + ph/2: {out}");
    }

    /// The rotated title's y carries the axis group's +0.5 crisp translate,
    /// isolated from the label-edge math above.
    #[test]
    fn emit_axis_title_rotated_y_carries_the_axis_groups_crisp_translate() {
        let style = title_style(11.0, 10.0, Some(true));
        let mut out = String::new();
        emit_axis_title_rotated(&mut out, "category", &style, 16.0, 100.0, -1.0, None);
        assert!(out.contains("y=\"100.50\""), "center_y + 0.5: {out}");
        assert!(out.contains("rotate(-90 4.00 100.50)"), "rotate pivot uses the shifted y too: {out}");
    }
}

#[cfg(test)]
mod format_y_label_tests {
    use super::*;

    /// `inject_axis_numeral_expr`'s tick_label arm, traced on
    /// `playground/number-format-affixes`: an anchored affix paints on the
    /// anchor tick alone and leads the sign ("US$−500" over bare "−400"); with
    /// `anchor_at_start: None` it repeats on every non-zero tick at the
    /// format's own placement ("−US$500"); a suffix rides the digits; the
    /// zero tick stays bare.
    #[test]
    fn a_tick_label_ladder_paints_its_affix_on_the_anchor_or_every_tick() {
        let tl = |anchor: Option<bool>| TickLabelConfig {
            format: ",.0f".into(),
            prefix: String::new(),
            anchor_at_start: anchor,
            decimal_pad_table: vec![],
            si_format: None,
            scientific_format: None,
        };
        let slot = |json: &str| serde_json::from_str::<FormatSlot>(json).unwrap();
        let usd = slot(r#"{"spec": ",.0f", "prefix": "US$"}"#);
        let chf = slot(r#"{"spec": ",.0f", "suffix": " CHF"}"#);
        let label = |tl: &TickLabelConfig, slot: &FormatSlot, v: f64, idx: usize| {
            format_y_label(v, None, Some(tl), Some(slot), idx, 4).unwrap()
        };
        let last = tl(Some(false));
        assert_eq!(label(&last, &usd, 1500.0, 3), "US$1,500");
        assert_eq!(label(&last, &usd, 1000.0, 2), "1,000");
        assert_eq!(label(&last, &usd, -500.0, 3), "US$\u{2212}500");
        assert_eq!(label(&last, &chf, 1500.0, 3), "1,500 CHF");
        assert_eq!(label(&last, &chf, 1000.0, 2), "1,000");
        let first = tl(Some(true));
        assert_eq!(label(&first, &usd, -1500.0, 0), "US$\u{2212}1,500");
        assert_eq!(label(&first, &usd, -500.0, 2), "\u{2212}500");
        let every = tl(None);
        assert_eq!(label(&every, &usd, 1500.0, 3), "US$1,500");
        assert_eq!(label(&every, &usd, 500.0, 1), "US$500");
        assert_eq!(label(&every, &usd, -500.0, 0), "\u{2212}US$500");
        assert_eq!(label(&every, &usd, 0.0, 1), "0");
        assert_eq!(label(&every, &chf, 500.0, 1), "500 CHF");
        // The baked currency symbol alone repeats the same way, the sign
        // leading it as d3's own symbol spec paints ("−$500").
        let dollar = TickLabelConfig { prefix: "$".into(), ..tl(None) };
        assert_eq!(format_y_label(-500.0, None, Some(&dollar), None, 1, 4).unwrap(), "\u{2212}$500");
        assert_eq!(format_y_label(0.0, None, Some(&dollar), None, 1, 4).unwrap(), "0");
    }

    // Measured against production's default (no ruler, no explicit format)
    // quantitative y-axis labelExpr: `format(datum.value, ",.0~f")` —
    // charts/line-graphs_0's revenue_trend_minimal renders ticks
    // 0/10000/20000/30000 as "0"/"10,000"/"20,000"/"30,000": thousands
    // grouping, no SI abbreviation, and always zero decimals regardless of
    // magnitude — a fixed spec, not a step-dependent precision choice.
    // The default ladder always arrives as a baked `tick_label` — resolve
    // never produces the no-ruler/no-tick_label/no-labels.format state from
    // a real payload (`_base.yaml:220-232` populates one or the other for
    // every quantitative axis).
    fn default_tick_label() -> TickLabelConfig {
        TickLabelConfig {
            format: ",.0~f".to_string(),
            prefix: String::new(),
            anchor_at_start: None,
            decimal_pad_table: vec![],
            si_format: None,
            scientific_format: None,
        }
    }

    #[test]
    fn no_ruler_uses_thousands_grouped_integer_format() {
        let tl = default_tick_label();
        assert_eq!(format_y_label(0.0, None, Some(&tl), None, 0, 4).unwrap(), "0");
        assert_eq!(format_y_label(10000.0, None, Some(&tl), None, 1, 4).unwrap(), "10,000");
        assert_eq!(format_y_label(20000.0, None, Some(&tl), None, 2, 4).unwrap(), "20,000");
        assert_eq!(format_y_label(30000.0, None, Some(&tl), None, 3, 4).unwrap(), "30,000");
    }

    #[test]
    fn no_ruler_rounds_fractional_values_to_zero_decimals() {
        // ",.0~f" always rounds to an integer — no decimal digits at any
        // magnitude (measured: production never emits a fractional default
        // tick label).
        let tl = default_tick_label();
        assert_eq!(format_y_label(1234.6, None, Some(&tl), None, 0, 1).unwrap(), "1,235");
        // d3rs::format renders the minus sign as U+2212 (MINUS SIGN), not the
        // ASCII hyphen — the real tick_label formatting path, unlike the
        // removed ASCII-hyphen fallback.
        assert_eq!(format_y_label(-1234.6, None, Some(&tl), None, 0, 1).unwrap(), "\u{2212}1,235");
    }

    #[test]
    fn no_ruler_groups_large_magnitudes_without_si_abbreviation() {
        // playground/xaxis-cadence-ladder's `half_bar` renders "100K" only
        // because it carries a baked ruler; the no-ruler default never
        // abbreviates, however large the value.
        let tl = default_tick_label();
        assert_eq!(format_y_label(1_240_000.0, None, Some(&tl), None, 0, 1).unwrap(), "1,240,000");
    }

    #[test]
    fn no_ruler_no_tick_label_no_labels_format_uses_the_plain_vl_default() {
        // Real, reachable state for scatter (measured: charts/point-charts_2's
        // team_response_dots resolves with none of the three baked) — not a
        // resolve-time bug, so this must format, not error.
        assert_eq!(format_y_label(30000.0, None, None, None, 0, 1).unwrap(), "30,000");
        // The discriminating half: ",.0~f" emits U+2212, not ASCII '-'.
        // Reverting this arm to an ASCII-hyphen formatter fails here —
        // the glyphs differ and so does the measured label advance.
        assert_eq!(
            format_y_label(-1234.9, None, None, None, 0, 1).unwrap(),
            "\u{2212}1,235"
        );
    }

    #[test]
    fn no_ruler_no_tick_label_falls_back_to_authored_labels_format() {
        // Measured against charts/line-graphs_2's revenue_formatted
        // (`axis_y.labels.format: currency_whole`): resolve bakes no `tick_label`
        // for an author-written non-SI format — production's VL spec reads
        // `labels.format` ("$,.0f") directly, with no labelExpr override.
        assert_eq!(format_y_label(30000.0, None, None, Some(&FormatSlot::from("$,.0f")), 0, 1).unwrap(), "$30,000");
    }

    #[test]
    fn tick_label_wins_over_authored_labels_format_when_both_present() {
        // Should not co-occur in real payloads (ResolvedTickLabel's bake
        // gate is mutually exclusive with an authored format), but tick_label
        // is the more specific/authoritative signal when it does.
        let tl = TickLabelConfig { format: ",.1f".to_string(), ..default_tick_label() };
        assert_eq!(
            format_y_label(30000.0, None, Some(&tl), Some(&FormatSlot::from("$,.0f")), 0, 1).unwrap(),
            "30,000.0"
        );
    }

    fn si_tick_label() -> TickLabelConfig {
        // playground/measured-label-alignment-estimated-lab's own
        // right_orient_numeric_unbaked resolves tick_label {format: ".3~r",
        // si_format: ".3~s", scientific_format: ".3~e"} — a ladder-less axis
        // (no baked tick_values, `axis_quantitative.ticks.count` cleared).
        TickLabelConfig {
            format: ".3~r".to_string(),
            prefix: String::new(),
            anchor_at_start: None,
            decimal_pad_table: vec![],
            si_format: Some(".3~s".to_string()),
            scientific_format: Some(".3~e".to_string()),
        }
    }

    #[test]
    fn si_format_compacts_at_or_above_unit_magnitude() {
        // Oracle: right_orient_numeric_unbaked's own reference ladder reads
        // "0" … "200k" … "1.6M", never the plain digit strings tl.format
        // alone would produce ("200000", "1600000").
        let tl = si_tick_label();
        assert_eq!(format_y_label(200_000.0, None, Some(&tl), None, 1, 9).unwrap(), "200k");
        assert_eq!(format_y_label(1_600_000.0, None, Some(&tl), None, 8, 9).unwrap(), "1.6M");
    }

    #[test]
    fn si_format_leaves_zero_on_the_plain_register() {
        // abs(0) < 1 routes to the sub-one branch, but datum.value !== 0
        // fails there too, so zero falls all the way through to plain
        // tl.format — never scientific's "0e+0".
        let tl = si_tick_label();
        assert_eq!(format_y_label(0.0, None, Some(&tl), None, 0, 9).unwrap(), "0");
    }

    #[test]
    fn si_format_below_one_uses_plain_format_above_the_scientific_floor() {
        let tl = si_tick_label();
        assert_eq!(format_y_label(0.5, None, Some(&tl), None, 0, 1).unwrap(), "0.5");
    }

    #[test]
    fn si_format_below_the_scientific_floor_takes_the_scientific_register() {
        let tl = si_tick_label();
        assert_eq!(format_y_label(1e-12, None, Some(&tl), None, 0, 1).unwrap(), "1e-12");
    }

    #[test]
    fn si_format_skips_the_prefix_and_decimal_pad_machinery() {
        // inject_axis_numeral_expr's own docstring: "Neither arm has a
        // prefix/decimal-pad device of its own" — a prefix/pad table set
        // alongside si_format is simply never consulted.
        let mut tl = si_tick_label();
        tl.prefix = "$".to_string();
        tl.anchor_at_start = Some(true);
        tl.decimal_pad_table = vec!["".into(), "X".into()];
        assert_eq!(format_y_label(200_000.0, None, Some(&tl), None, 0, 1).unwrap(), "200k");
    }

    fn currency_tick_label() -> TickLabelConfig {
        // playground/dundersign-commercial-finance's hero_bookings: resolve
        // bakes tick_label {format: ",.0~f", prefix: "$",
        // anchor_at_start: false} and inject_axis_numeral_expr compiles
        // `(datum.index === 1 ? format(datum.value, "$,.0~f") :
        // format(datum.value, ",.0~f"))`.
        TickLabelConfig {
            format: ",.0~f".to_string(),
            prefix: "$".to_string(),
            anchor_at_start: Some(false),
            decimal_pad_table: vec![],
            si_format: None,
            scientific_format: None,
        }
    }

    #[test]
    fn tick_label_prefix_lands_on_the_last_tick_when_anchor_at_end() {
        // Oracle: hero_bookings' rendered ladder — the top rung alone reads
        // "$8,000" (the anchor-only prefix gate; anchor_at_start=false picks
        // the last drawn tick, datum.index === 1 in the compiled expr).
        let tl = currency_tick_label();
        assert_eq!(format_y_label(8000.0, None, Some(&tl), None, 4, 5).unwrap(), "$8,000");
        assert_eq!(format_y_label(6000.0, None, Some(&tl), None, 3, 5).unwrap(), "6,000");
        assert_eq!(format_y_label(0.0, None, Some(&tl), None, 0, 5).unwrap(), "0");
    }

    #[test]
    fn tick_label_zero_valued_anchor_takes_no_prefix() {
        // The ruler branch's own `carries_prefix` gate is `value != 0.0 &&
        // (prefix_repeats || is_anchor)` (#8803) -- production's tick_label
        // sibling gates the same way (`vl_field_maps.py`'s
        // `prefix_present`, ALWAYS "datum.value !== 0 && ..." whether
        // `anchor_at_start` is `Some` or `None`). A zero-valued anchor tick
        // must paint the bare digits, never the symbol.
        let tl = currency_tick_label();
        assert_eq!(format_y_label(0.0, None, Some(&tl), None, 4, 5).unwrap(), "0");
    }

    #[test]
    fn tick_label_negative_anchor_puts_the_sign_before_the_symbol() {
        // with_symbol embeds the "$" INSIDE the d3 spec, so d3's
        // sign-before-symbol ordering holds: format("$,.0~f")(-500) renders
        // "−$500", never "$−500".
        let mut tl = currency_tick_label();
        tl.anchor_at_start = Some(true);
        assert_eq!(
            format_y_label(-500.0, None, Some(&tl), None, 0, 5).unwrap(),
            "\u{2212}$500"
        );
    }

    #[test]
    fn tick_label_decimal_pad_appends_the_missing_length_entry() {
        // _apply_decimal_pad: missing = length(format(v, untilded spec)) −
        // length(painted text); the compiled ternary chain matches exact
        // indices 0..len−2 and every other value takes the LAST entry.
        let tl = TickLabelConfig {
            format: ",.2~f".to_string(),
            decimal_pad_table: vec!["".into(), "A".into(), "BB".into(), "CCC".into()],
            ..default_tick_label()
        };
        // "2.51" trims nothing → missing 0 → entry "".
        assert_eq!(format_y_label(2.51, None, Some(&tl), None, 0, 3).unwrap(), "2.51");
        // "2.5" vs full "2.50" → missing 1 → entry "A".
        assert_eq!(format_y_label(2.5, None, Some(&tl), None, 1, 3).unwrap(), "2.5A");
        // "2" vs full "2.00" → missing 3 → past the exact tests → last "CCC".
        assert_eq!(format_y_label(2.0, None, Some(&tl), None, 2, 3).unwrap(), "2CCC");
    }

    fn currency_ruler() -> RulerConfig {
        // playground/chart-lab-import-review's line_multi_2_review: the
        // compiled labelExpr is `(datum.index === 1 ?
        // format(v/1000, "$,.1~f") : format(v/1000, ",.1~f")) +
        // (v !== 0 && datum.index === 1 ? " K" : "<pad>")`.
        RulerConfig {
            exponent: 3,
            mode: "anchor".to_string(),
            digit_spec: ",.1~f".to_string(),
            anchor_at_start: false,
            reservation: "\u{2004}\u{2004}\u{2008}\u{200b}".to_string(),
            prefix: "$".to_string(),
            prefix_repeats: false,
            register: "analytic".to_string(),
        }
    }

    #[test]
    fn anchor_tick_carries_the_currency_prefix() {
        // Oracle: line_multi_2_review's scenegraph — top tick "$180K",
        // the rest "100<pad>"…"160<pad>" with no prefix.
        let r = currency_ruler();
        assert_eq!(format_y_label(180000.0, Some(&r), None, None, 4, 5).unwrap(), "$180K");
        assert_eq!(
            format_y_label(100000.0, Some(&r), None, None, 0, 5).unwrap(),
            "100\u{2004}\u{2004}\u{2008}\u{200b}"
        );
    }

    fn affixed_slot(prefix: &str, suffix: &str) -> FormatSlot {
        FormatSlot { spec: ",.0f".to_string(), prefix: prefix.to_string(), suffix: suffix.to_string(), ..FormatSlot::default() }
    }

    /// `inject_axis_numeral_expr`'s ruler arm: an authored prefix leads the
    /// anchor's digits and an authored suffix trails the magnitude word, on the
    /// anchor tick alone.
    #[test]
    fn a_ruler_wears_an_authored_affix_on_its_anchor_tick() {
        let r = currency_ruler();
        let slot = affixed_slot("", " CHF");
        assert_eq!(format_y_label(180000.0, Some(&r), None, Some(&slot), 4, 5).unwrap(), "$180K CHF");
        assert_eq!(
            format_y_label(100000.0, Some(&r), None, Some(&slot), 0, 5).unwrap(),
            "100\u{2004}\u{2004}\u{2008}\u{200b}"
        );
        let slot = affixed_slot("EUR ", "");
        assert_eq!(format_y_label(180000.0, Some(&r), None, Some(&slot), 4, 5).unwrap(), "EUR $180K");
    }

    /// A ladder-less SI `tick_label` wraps each magnitude band's digits in the
    /// authored affix on every tick but zero, which stays the bare digits.
    #[test]
    fn a_ladderless_si_tick_label_wears_the_authored_affix() {
        let tl = TickLabelConfig {
            format: "$.3~r".to_string(),
            prefix: String::new(),
            anchor_at_start: None,
            decimal_pad_table: Vec::new(),
            si_format: Some("$.3~s".to_string()),
            scientific_format: Some("$.3~e".to_string()),
        };
        let slot = affixed_slot("", " x");
        let label = |v: f64| format_y_label(v, None, Some(&tl), Some(&slot), 1, 4).unwrap();
        assert_eq!(label(1500.0), "$1.5k x");
        assert_eq!(label(0.5), "$0.5 x");
        assert_eq!(label(0.0), "$0");
    }

    /// The decimal pad follows the fractional digits of the text actually
    /// painted (`_decimal_pad_missing_len_expr`), authored suffix included.
    #[test]
    fn a_decimal_pad_reads_the_affixed_text() {
        let tl = TickLabelConfig {
            format: ",.1~f".to_string(),
            prefix: String::new(),
            anchor_at_start: None,
            decimal_pad_table: ["P0", "P1", "P2", "P3"].map(String::from).to_vec(),
            si_format: None,
            scientific_format: None,
        };
        let slot = affixed_slot("", " x");
        assert_eq!(format_y_label(2.5, None, Some(&tl), Some(&slot), 1, 4).unwrap(), "2.5 xP1");
        assert_eq!(format_y_label(2.0, None, Some(&tl), Some(&slot), 1, 4).unwrap(), "2 xP3");
    }

    /// An axis keeps d3's log `tickFormat` blanking unless production composes
    /// a `labelExpr` over `datum.value`.
    #[test]
    fn only_a_numeral_labelexpr_defeats_the_log_blanking() {
        let axis = |format: serde_json::Value| -> AxisStyle {
            serde_json::from_value(serde_json::json!({
                "grid": {"visible": true, "threshold": null, "width": 1.5, "color": "#D0D3D9", "opacity": 1.0},
                "ticks": {"color": "#D0D3D9", "visible": false, "count": 6.0, "length": 6.0, "offset": 1.0},
                "labels": {
                    "font": {"family": "Inter", "color": "#626366", "size": 11.0, "weight": "400"},
                    "padding": 6.0, "min_gap": null, "format": format, "visible": true
                },
                "title": {"font": {"family": "Inter", "size": 11.0, "weight": "600", "color": "#8A8C8F"}, "padding": 10.0, "visible": false},
                "position": null, "mirror": null, "band_position": 0.5, "scale": {},
                "tick_values": [], "domain_max": null, "domain_min": null,
                "ruler": null, "tick_label": null,
                "time_unit": null, "fiscal_year_start_month": 1, "fill": null, "line": null,
                "is_quantitative": true, "zero_anchored": false
            }))
            .unwrap()
        };
        assert!(!axis(serde_json::json!({"spec": ".3~s"})).composes_numeral_expr());
        assert!(!axis(serde_json::json!({"spec": ",.0f"})).composes_numeral_expr());
        assert!(axis(serde_json::json!({"spec": ",.0f", "suffix": " pts"})).composes_numeral_expr());
        assert!(axis(serde_json::json!({"spec": "$,.0f"})).composes_numeral_expr());
        assert!(axis(serde_json::json!({"spec": ",.2s", "notation": "narrative"})).composes_numeral_expr());
        assert!(axis(serde_json::json!({"spec": "bytes"})).composes_numeral_expr());
    }

    #[test]
    fn negative_anchor_puts_the_sign_before_the_currency_symbol() {
        // d3's "$" spec flag places the symbol INSIDE the sign —
        // format("$,.1~f")(-1.5) is "−$1.5" — so an all-negative currency
        // ladder's anchor must not render "$−1.5".
        let mut r = currency_ruler();
        r.anchor_at_start = true;
        assert_eq!(
            format_y_label(-1500.0, Some(&r), None, None, 0, 5).unwrap(),
            "\u{2212}$1.5K"
        );
    }

    #[test]
    fn zero_valued_anchor_takes_reservation_not_unit_suffix() {
        // Both the labelExpr's suffix ternary AND its prefix ternary gate
        // on `datum.value !== 0` (`vl_field_maps.py`'s `suffix_present` and
        // `prefix_present`, the latter regardless of `prefix_repeats`,
        // #8803) — a zero anchor takes neither the "$" prefix nor the
        // analytic-word suffix, only the pad reservation.
        let r = currency_ruler();
        assert_eq!(
            format_y_label(0.0, Some(&r), None, None, 4, 5).unwrap(),
            "0\u{2004}\u{2004}\u{2008}\u{200b}"
        );
    }

    fn repeat_currency_ruler() -> RulerConfig {
        // playground/chart-lab-import-review's bar_review (B7): the resolved
        // payload's axis_y.ruler carries `mode: "repeat"`, `reserve: false`
        // (so `reservation` bakes to ""), a `$` prefix, and `prefix_repeats:
        // true` (#8803 -- "Plain-digit currency axis prefix ignores
        // column_forming on horizontal bar measure axis"): production's own
        // `prefix_present` (`vl_field_maps.py:722-726`) is `datum.value !==
        // 0` when `prefix_repeats`, independent of `mode`/`suffix_present` --
        // every non-zero tick carries "$", not just the anchor. Bar
        // semantics pin `ruler` to `axis_y` even when the measure axis
        // renders on a horizontal bar's visual x — this ladder is the one
        // `format_tick_labels` reads for both orientations.
        RulerConfig {
            exponent: 3,
            mode: "repeat".to_string(),
            digit_spec: ",.1~f".to_string(),
            anchor_at_start: false,
            reservation: String::new(),
            prefix: "$".to_string(),
            prefix_repeats: true,
            register: "narrative".to_string(),
        }
    }

    #[test]
    fn repeat_mode_prefix_repeats_on_every_non_zero_tick_when_prefix_repeats() {
        // Oracle: bar_review's reference x labels — `0, $50k, $100k, $150k,
        // $200k`.
        let r = repeat_currency_ruler();
        assert_eq!(format_y_label(0.0, Some(&r), None, None, 0, 5).unwrap(), "0");
        assert_eq!(format_y_label(50000.0, Some(&r), None, None, 1, 5).unwrap(), "$50k");
        assert_eq!(format_y_label(100000.0, Some(&r), None, None, 2, 5).unwrap(), "$100k");
        assert_eq!(format_y_label(150000.0, Some(&r), None, None, 3, 5).unwrap(), "$150k");
        assert_eq!(format_y_label(200000.0, Some(&r), None, None, 4, 5).unwrap(), "$200k");
    }

    /// An authored `notation: analytic` on a REPEAT ladder (`scale.py:149-151`
    /// bakes `register` from the notation first) spells the word on every
    /// non-zero tick in the analytic register, glued to the digits for en-US
    /// (`magnitude_words.py:40`).
    #[test]
    fn repeat_mode_speaks_the_baked_register() {
        let mut r = repeat_currency_ruler();
        r.register = "analytic".to_string();
        assert_eq!(format_y_label(0.0, Some(&r), None, None, 0, 5).unwrap(), "0");
        assert_eq!(format_y_label(50000.0, Some(&r), None, None, 1, 5).unwrap(), "$50K");
        assert_eq!(format_y_label(200000.0, Some(&r), None, None, 4, 5).unwrap(), "$200K");
    }

    #[test]
    fn an_unknown_register_refuses_by_name() {
        let mut r = currency_ruler();
        r.register = "spoken".to_string();
        let err = format_y_label(180000.0, Some(&r), None, None, 4, 5).unwrap_err();
        assert!(err.contains("register"), "{err}");
    }

    /// `prefix_repeats: false` (a column-forming ladder, per `scale.py`'s
    /// `prefix_repeats=not column_forming`) keeps the OLD anchor-only law:
    /// only the anchor tick — Vega's own normalized `datum.index` of `0`
    /// (`anchor_at_start`) or `1` (the LAST drawn tick, a 0-to-1 axis
    /// position, never a literal array index — `vl_field_maps.py`'s own
    /// comment on `anchor_index`) — carries "$".
    #[test]
    fn repeat_mode_prefix_stays_anchor_only_when_not_prefix_repeats() {
        let mut r = repeat_currency_ruler();
        r.prefix_repeats = false;
        assert_eq!(format_y_label(0.0, Some(&r), None, None, 0, 5).unwrap(), "0");
        assert_eq!(format_y_label(50000.0, Some(&r), None, None, 1, 5).unwrap(), "50k");
        assert_eq!(format_y_label(100000.0, Some(&r), None, None, 2, 5).unwrap(), "100k");
        assert_eq!(format_y_label(150000.0, Some(&r), None, None, 3, 5).unwrap(), "150k");
        assert_eq!(format_y_label(200000.0, Some(&r), None, None, 4, 5).unwrap(), "$200k");
    }
}

/// A color channel's legend/palette domain: the distinct values, sorted the
/// way Vega-Lite's nominal scale sorts an unpinned domain.
pub fn color_domain_of<'a>(
    rows: impl IntoIterator<Item = &'a serde_json::Map<String, Value>>,
    color_field: &str,
) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut domain: Vec<String> = rows
        .into_iter()
        .map(|row| row_str(row, color_field))
        .filter(|v| seen.insert(v.clone()))
        .collect();
    domain.sort();
    domain
}

/// `resolved_channels.<name>`: the resolver's style channel. `mode` is the
/// only field every family's rejection guard reads (`"conditional"` is
/// retired from every family's own paint path — table/kpi read
/// `conditional_formatting` directly instead, never through this struct);
/// `data_field` backs the series/gradient paint paths.
#[derive(Deserialize)]
pub struct StyleChannel {
    pub mode: String,
    pub data_field: Option<String>,
    /// `_flag_quantitative_color`: a series-mode color over numeric data paints
    /// a gradient, over anything else a nominal palette. Absent on a payload
    /// that predates the flag, which then says neither.
    #[serde(default)]
    pub quantitative_data: Option<bool>,
    /// A gradient-mode channel's scale (`ResolvedScaleTarget`).
    #[serde(default)]
    pub scale: Option<ScaleTarget>,
}

/// `ResolvedScaleTarget` (`resolve_hinge`/`compute_scale_domain`/
/// `interpolate_scale_color` all take this as `target`): the palette (a Vega
/// scheme name, or color stops: a named palette's already baked into
/// `resolved_stops`, or an inline list in `palette`), its authored bounds and
/// diverging pivot. A table cell paints no legend, so only a chart reads
/// `nice`.
#[derive(Deserialize, Clone)]
pub struct ScaleTarget {
    pub palette: Value,
    #[serde(default)]
    pub resolved_stops: Option<Vec<String>>,
    #[serde(default)]
    pub min: Option<f64>,
    #[serde(default)]
    pub max: Option<f64>,
    #[serde(default)]
    pub nice: bool,
    #[serde(default)]
    pub null_color: Option<String>,
    /// A number, or `"auto"`.
    #[serde(default)]
    pub hinge: Option<Value>,
    pub arm_mode: String,
}

/// Board-wide value→palette-slot binding for one categorical field —
/// mirrors production's `CategoryColorScale` (`compile/models/style/theme/
/// category_colors.py`), deserialized straight off the resolved chart's
/// `category_colors` list. `unseen_pins` is warning-only bookkeeping no
/// renderer reads, so it isn't modeled here.
#[derive(Deserialize)]
pub struct CategoryColorScale {
    pub field: String,
    pub slots: std::collections::HashMap<String, usize>,
    #[serde(default)]
    pub overrides: std::collections::HashMap<String, String>,
}

/// The board-wide scale for `field`, if this chart's own (already-narrowed)
/// `category_colors` list carries one — mirrors `category_scale_for`.
pub fn category_scale_for<'a>(
    category_colors: &'a [CategoryColorScale],
    field: &str,
) -> Option<&'a CategoryColorScale> {
    category_colors.iter().find(|s| s.field == field)
}

fn slot_for(scale: &CategoryColorScale, value: &str, palette_len: usize) -> Result<usize, String> {
    let slot = *scale.slots.get(value).ok_or_else(|| {
        format!(
            "`{value}` has no palette slot in the board's `{}` category-color scale.",
            scale.field
        )
    })?;
    if slot >= palette_len {
        return Err(format!(
            "`{value}` needs palette slot {slot} for the board's `{}` category-color \
             scale, but this chart's palette has only {palette_len} swatches.",
            scale.field
        ));
    }
    Ok(slot)
}

/// The FILL `value` paints under `scale`: its override, else its palette
/// slot — mirrors `color_at`.
pub fn category_color(scale: &CategoryColorScale, value: &str, palette: &[String]) -> Result<String, String> {
    if let Some(hex) = scale.overrides.get(value) {
        return Ok(hex.clone());
    }
    let slot = slot_for(scale, value, palette.len())?;
    Ok(palette[slot].clone())
}

/// The INK `value`'s fill pairs with: the palette slot's companion, except a
/// value pinned to a color outside the palette, which takes that pinned
/// color's own ink from `pin_companions` (`(pinned color, ink)` pairs,
/// `series_label.pin_companions`). A pin the pairs do not cover refuses.
pub fn category_ink(
    scale: &CategoryColorScale,
    value: &str,
    companions: &[String],
    pin_companions: &[(String, String)],
) -> Result<String, String> {
    if let Some(pinned) = scale.overrides.get(value) {
        return pin_companions
            .iter()
            .find_map(|(color, ink)| (color == pinned).then(|| ink.clone()))
            .ok_or_else(|| {
                format!("`{value}` is pinned to {pinned}, which has no entry in pin_companions.")
            });
    }
    let slot = slot_for(scale, value, companions.len())?;
    Ok(companions[slot].clone())
}

/// One value's fill, mirroring `spatial_color_scale`'s two branches: the
/// board scale's slot when `value`'s field is bound, else this chart's own
/// positional assignment over `palette_domain` (its chart-local order —
/// alphabetical, dash-cycle, whatever the family already builds). Every
/// family's series-fill lookup routes through this one function so the
/// precedence lives in exactly one place. A `value` absent from
/// `palette_domain` refuses — the same guarantee bar.rs's pre-#7167 "series
/// {s} not in palette domain" error made, since every wired caller's domain
/// is built from the same rows the value comes from. `palette` is a
/// [`Payload::series_palette`], which is never empty.
pub fn series_color(
    value: &str,
    palette_domain: &[String],
    palette: &[String],
    scale: Option<&CategoryColorScale>,
) -> Result<String, String> {
    if let Some(scale) = scale {
        return category_color(scale, value, palette);
    }
    let idx = palette_domain
        .iter()
        .position(|s| s == value)
        .ok_or_else(|| format!("internal error — {value:?} not in this chart's own color domain"))?;
    palette_slot(palette, idx).map(str::to_string)
}

/// Slot `idx` of a series palette, wrapping past its end as Vega's ordinal scale cycles a short range.
fn palette_slot(palette: &[String], idx: usize) -> Result<&str, String> {
    idx.checked_rem(palette.len())
        .map(|slot| palette[slot].as_str())
        .ok_or_else(|| "internal error — a series color read an empty palette; Payload::series_palette never hands one out".to_string())
}

/// The companion ink `value`'s fill pairs with, mirroring
/// `companion_color_for_fill`/`ink_at`'s pair: the board scale's slot when
/// bound (or the pinned color's own ink, see [`category_ink`]), else
/// this chart's own positional companion at the same index `series_color`
/// used for the fill — and, past the companion list's end, the FILL itself,
/// exactly as `companion_color_for_fill` returns `fill` when `index >=
/// len(companions)`.
pub fn series_ink(
    value: &str,
    palette_domain: &[String],
    palette: &[String],
    companions: &[String],
    pin_companions: &[(String, String)],
    scale: Option<&CategoryColorScale>,
) -> Result<String, String> {
    if let Some(scale) = scale {
        return category_ink(scale, value, companions, pin_companions);
    }
    let idx = palette_domain
        .iter()
        .position(|s| s == value)
        .ok_or_else(|| format!("internal error — {value:?} not in this chart's own color domain"))?;
    match companions.get(idx) {
        Some(ink) => Ok(ink.clone()),
        None => palette_slot(palette, idx).map(str::to_string),
    }
}

/// `companion_color_for_fill`: the companion ink occupying `fill`'s palette
/// slot, else the fill itself.
pub fn companion_for_fill(fill: &str, palette: &[String], companions: &[String]) -> String {
    palette
        .iter()
        .position(|c| c == fill)
        .and_then(|idx| companions.get(idx))
        .map_or_else(|| fill.to_string(), Clone::clone)
}

/// A non-stacked per_series cell's ink: the companion at the series' rank in
/// the sorted series list, wrapping past the list's end
/// (`support_table_attachment.py`'s `alpha_index`). No board scale is read.
pub fn alphabetical_companion_ink(
    value: &str,
    series_order: &[String],
    companions: &[String],
) -> Result<String, String> {
    if companions.is_empty() {
        return Err("internal error - empty dark companion palette".to_string());
    }
    let rank = series_order
        .iter()
        .filter(|s| s.as_str() < value)
        .count();
    Ok(companions[rank % companions.len()].clone())
}

#[cfg(test)]
mod category_color_tests {
    use super::*;
    use serde_json::json;

    fn scale(v: serde_json::Value) -> CategoryColorScale {
        serde_json::from_value(v).unwrap()
    }

    fn pal(hexes: &[&str]) -> Vec<String> {
        hexes.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn alphabetical_companion_ink_ranks_sorted_and_wraps() {
        let order: Vec<String> = ["b", "c", "a"].iter().map(|s| s.to_string()).collect();
        let comp = pal(&["#1", "#2"]);
        assert_eq!(alphabetical_companion_ink("a", &order, &comp).unwrap(), "#1");
        assert_eq!(alphabetical_companion_ink("b", &order, &comp).unwrap(), "#2");
        assert_eq!(alphabetical_companion_ink("c", &order, &comp).unwrap(), "#1");
    }

    /// A value's board slot wins over its chart-local (e.g. alphabetical)
    /// position — the whole point of the board-wide plan: "East" is second
    /// alphabetically but owns slot 3 on the board.
    #[test]
    fn board_slot_wins_over_chart_local_order() {
        let s = scale(json!({
            "field": "region",
            "slots": {"North": 0, "South": 1, "East": 3, "West": 2},
            "overrides": {}
        }));
        let palette = pal(&["#a", "#b", "#c", "#d"]);
        let domain = vec!["East".to_string(), "North".to_string()]; // chart-local order
        assert_eq!(
            series_color("East", &domain, &palette, Some(&s)).unwrap(),
            "#d"
        );
        assert_eq!(
            series_color("North", &domain, &palette, Some(&s)).unwrap(),
            "#a"
        );
    }

    /// An authored override wins outright, even over a value that also owns
    /// a slot for its companion ink — mirrors `color_at`.
    #[test]
    fn override_wins_over_slot() {
        let s = scale(json!({
            "field": "region",
            "slots": {"North": 0},
            "overrides": {"North": "#custom"}
        }));
        let palette = pal(&["#a", "#b"]);
        assert_eq!(category_color(&s, "North", &palette).unwrap(), "#custom");
    }

    /// A pinned value takes its pinned color's own ink, never the slot's
    /// companion; an unpinned value indexes its slot; a pin with no baked ink
    /// refuses.
    #[test]
    fn a_pinned_value_takes_its_pinned_colors_ink() {
        let s = scale(json!({
            "field": "region",
            "slots": {"North": 1, "South": 0},
            "overrides": {"North": "#custom"}
        }));
        let companions = pal(&["#dark0", "#dark1"]);
        let pins = vec![("#custom".to_string(), "#darkcustom".to_string())];
        assert_eq!(category_ink(&s, "North", &companions, &pins).unwrap(), "#darkcustom");
        assert_eq!(category_ink(&s, "South", &companions, &pins).unwrap(), "#dark0");
        let err = category_ink(&s, "North", &companions, &[]).unwrap_err();
        assert!(err.contains("North") && err.contains("#custom"), "{err}");
    }

    /// No board scale for the field: falls back to this chart's own
    /// positional assignment over `palette_domain` — mirrors
    /// `spatial_color_scale`'s `scale is None` branch.
    #[test]
    fn no_scale_falls_back_to_chart_local_position() {
        let palette = pal(&["#a", "#b", "#c"]);
        let domain = vec!["East".to_string(), "North".to_string()];
        assert_eq!(series_color("East", &domain, &palette, None).unwrap(), "#a");
        assert_eq!(series_color("North", &domain, &palette, None).unwrap(), "#b");
    }

    /// A value missing from the chart's own local domain (unbound case) is
    /// a real bug upstream — `series_color`/`series_ink` refuse rather than
    /// silently defaulting to index 0, the guarantee bar.rs's pre-#7167
    /// "series {s} not in palette domain" error made before this resolver
    /// existed.
    #[test]
    fn a_value_outside_the_local_domain_refuses_even_without_a_scale() {
        let palette = pal(&["#a", "#b"]);
        let domain = vec!["East".to_string()];
        let err = series_color("Ghost", &domain, &palette, None).unwrap_err();
        assert!(err.contains("Ghost"), "{err}");
        let err = series_ink("Ghost", &domain, &palette, &palette, &[], None).unwrap_err();
        assert!(err.contains("Ghost"), "{err}");
    }

    /// A value the board plan never saw is a real bug upstream — refuses,
    /// never guesses a slot.
    #[test]
    fn a_value_outside_the_plan_refuses() {
        let s = scale(json!({"field": "region", "slots": {"North": 0}, "overrides": {}}));
        let err = category_color(&s, "Ghost", &pal(&["#a"])).unwrap_err();
        assert!(err.contains("Ghost") && err.contains("no palette slot"), "{err}");
    }

    /// This chart's palette is shorter than the board plan needs — refuses
    /// rather than wrapping the slot onto a different swatch (the exact
    /// failure `_slot_for` guards).
    #[test]
    fn a_palette_too_short_for_the_planned_slot_refuses() {
        let s = scale(json!({"field": "region", "slots": {"North": 2}, "overrides": {}}));
        let err = category_color(&s, "North", &pal(&["#a", "#b"])).unwrap_err();
        assert!(err.contains("slot 2") && err.contains("only 2"), "{err}");
    }

    /// The companion ink follows the board slot, not the chart-local order.
    #[test]
    fn series_ink_follows_the_board_slot_not_the_local_order() {
        let s = scale(json!({"field": "region", "slots": {"East": 3}, "overrides": {}}));
        let companions = pal(&["#d0", "#d1", "#d2", "#d3"]);
        let domain = vec!["East".to_string()]; // local index 0, board slot 3
        assert_eq!(
            series_ink("East", &domain, &companions, &companions, &[], Some(&s)).unwrap(),
            "#d3"
        );
    }

    /// Unbound, past the companion list's end, the ink is the fill itself —
    /// `companion_color_for_fill` returns `fill` when `index >= len(companions)`.
    #[test]
    fn series_ink_past_the_companion_list_is_the_fill_itself() {
        let domain = pal(&["a", "b", "c"]);
        let palette = pal(&["#111", "#222", "#333"]);
        let companions = pal(&["#aaa"]);
        assert_eq!(series_ink("a", &domain, &palette, &companions, &[], None).unwrap(), "#aaa");
        assert_eq!(series_ink("c", &domain, &palette, &companions, &[], None).unwrap(), "#333");
    }
}

#[cfg(test)]
mod format_slot_tests {
    use super::*;
    use serde_json::json;

    fn slot(v: Value) -> FormatSlot {
        serde_json::from_value(v).unwrap()
    }

    fn slot_err(v: Value) -> String {
        serde_json::from_value::<FormatSlot>(v).unwrap_err().to_string()
    }

    #[test]
    fn a_bare_string_and_a_raw_carrying_object_read_as_the_same_spec() {
        assert_eq!(slot(json!(",.2~f")), FormatSlot::from(",.2~f"));
        let named = slot(json!({"spec": ".3~s", "raw": "number"}));
        assert_eq!((named.spec.as_str(), named.raw.as_deref()), (".3~s", Some("number")));
        assert!(!named.has_affix());
    }

    /// A ladder-less axis's SI arm takes the format's notation
    /// (`numeral_vega_expr(datum.value, tick_label.si_format, fmt.notation)`):
    /// the SI letter is respelled, a sub-unit tick and the no-slot case are not.
    #[test]
    fn the_si_arm_of_a_baked_tick_label_takes_the_slots_notation() {
        let tick_label: TickLabelConfig = serde_json::from_value(json!({
            "format": "$.2~r", "si_format": "$.2~s", "scientific_format": "$.2~e",
        }))
        .unwrap();
        let notated = slot(json!({"spec": "$.2~s", "notation": "analytic", "raw": "currency_compact"}));
        let paint = |value: f64, format: Option<&FormatSlot>| {
            format_y_label(value, None, Some(&tick_label), format, 0, 1).unwrap()
        };
        assert_eq!(paint(2000.0, None), "$2k");
        assert_eq!(paint(2000.0, Some(&notated)), "$2K");
        assert_eq!(paint(0.5, Some(&notated)), paint(0.5, None), "below 1 the notation never applies");
    }

    #[test]
    fn an_object_carries_every_resolved_format_field() {
        let full = slot(json!({
            "spec": ".3~s", "prefix": "CHF ", "suffix": " net", "notation": "analytic",
            "sign_placement": "before_prefix", "repeat": "every", "raw": "number",
        }));
        assert_eq!(
            full,
            FormatSlot {
                spec: ".3~s".into(),
                prefix: "CHF ".into(),
                suffix: " net".into(),
                notation: Some("analytic".into()),
                sign_placement: Some(SignPlacement::BeforePrefix),
                repeat: Some("every".into()),
                raw: Some("number".into()),
            }
        );
        // `FormatConfig` dumps unset fields as null.
        let nulls = slot(json!({
            "spec": "integer", "prefix": "CHF ", "suffix": null, "notation": null,
            "sign_placement": null, "repeat": null,
        }));
        assert_eq!((nulls.suffix.as_str(), nulls.notation, nulls.repeat), ("", None, None));
    }

    #[test]
    fn a_slot_is_house_only_when_its_raw_names_a_predefined_format() {
        let named = slot(json!({"spec": "$,.0f", "raw": "currency_whole"}));
        assert_eq!(named.spec, "$,.0f");
        assert!(named.is_house());
        assert!(!slot(json!({"spec": "$,.2f", "raw": "my_money"})).is_house());
        assert!(!slot(json!("$,.0f")).is_house());
    }

    /// `ResolvedFormat.spec` defaults to `""` and `_from_wire` reads a null as
    /// `""`: a KPI headline under the compact threshold arrives as
    /// `{prefix, notation}` with no spec (`finalize_kpi_value_format`). The
    /// parse accepts it; a painter that needs digits refuses it by name.
    #[test]
    fn a_spec_less_slot_parses_and_refuses_to_paint_its_affix() {
        for wire in [
            json!({"prefix": "US$", "notation": "narrative"}),
            json!({"spec": null, "prefix": "US$"}),
        ] {
            let parsed = slot(wire);
            assert_eq!((parsed.spec.as_str(), parsed.prefix.as_str()), ("", "US$"));
            assert!(parsed.paint(5.0).unwrap_err().contains("needs a d3 spec"));
        }
    }

    #[test]
    fn a_malformed_slot_refuses_by_name() {
        assert!(slot_err(json!(7)).starts_with("format must be a string or object"));
        assert!(slot_err(json!({"spec": ",.0f", "bogus": 1})).contains("unknown format key \"bogus\""));
        assert!(slot_err(json!({"spec": ",.0f", "prefix": 3})).contains("\"prefix\" must be a string"));
        assert!(slot_err(json!({"spec": ",.0f", "repeat": "twice"})).contains("neither anchor nor every"));
        assert!(slot_err(json!({"spec": ",.0f", "notation": "loud"})).contains("neither analytic nor narrative"));
        assert!(slot_err(json!({"spec": ",.0f", "sign_placement": "beside"})).contains("beside"));
    }

    /// Oracle: `quantitative_tick_labels((-1000, 0, 1000), fmt, None, None)`
    /// (`_measured_label_padding.py:109`) over the same `ResolvedFormat`s:
    /// the zero tick bare, a sign placed per the placement.
    #[test]
    fn a_plain_axis_tick_leaves_zero_bare_and_places_the_sign() {
        for (fmt, expected) in [
            (json!({"spec": ",.0f", "prefix": "US$"}), ["\u{2212}US$1,000", "0", "US$1,000"]),
            (json!({"spec": ",.0f", "prefix": "CHF "}), ["CHF \u{2212}1,000", "0", "CHF 1,000"]),
            (json!({"spec": ",.0f", "prefix": "US$", "sign_placement": "after_prefix"}), ["US$\u{2212}1,000", "0", "US$1,000"]),
            (json!({"spec": ",.0f", "prefix": "CHF ", "sign_placement": "before_prefix"}), ["\u{2212}CHF 1,000", "0", "CHF 1,000"]),
            (json!({"spec": ",.0f", "suffix": " CHF"}), ["\u{2212}1,000 CHF", "0", "1,000 CHF"]),
        ] {
            let slot = slot(fmt.clone());
            let ticks: Vec<String> = [-1000.0, 0.0, 1000.0].iter().map(|&v| slot.paint_tick(v).unwrap()).collect();
            assert_eq!(ticks, expected, "{fmt}");
        }
    }

    /// Oracle: `quantitative_tick_labels` over `ResolvedFormat(spec="$,.0f")`:
    /// the currency symbol in the spec leaves the zero tick like an affix does.
    #[test]
    fn a_plain_axis_zero_tick_drops_the_spec_currency_symbol() {
        let slot = slot(json!({"spec": "$,.0f"}));
        let ticks: Vec<String> = [-1000.0, 0.0, 1000.0].iter().map(|&v| slot.paint_tick(v).unwrap()).collect();
        assert_eq!(ticks, ["\u{2212}$1,000", "0", "$1,000"]);
    }

    /// Oracle: `sign_affixed_expr` (`vl_field_maps.py:208`) over the same
    /// `ResolvedFormat`s: the sign against the prefix, the suffix kept.
    #[test]
    fn a_signed_magnitude_places_its_sign_against_the_prefix() {
        let glued = slot(json!({"spec": ",.0f", "prefix": "US$", "suffix": "/mo"}));
        assert_eq!(glued.paint_signed('+', 500.0).unwrap(), "+US$500/mo");
        let after = slot(json!({"spec": ",.0f", "prefix": "US$", "sign_placement": "after_prefix"}));
        assert_eq!(after.paint_signed('\u{2212}', 500.0).unwrap(), "US$\u{2212}500");
        let bare = slot(json!({"spec": ",.0f", "suffix": " CHF"}));
        assert_eq!(bare.paint_signed('+', 500.0).unwrap(), "+500 CHF");
    }

    /// Oracle: `format_d3(value, spec, prefix, suffix, notation, placement)`
    /// (`text/format_d3.py`) over `ResolvedFormat`, run on the same literals;
    /// `ResolvedFormat.placement` supplies the default.
    #[test]
    fn a_prefix_leads_a_sign_by_default_only_when_glued() {
        let glued = slot(json!({"spec": ",.0f", "prefix": "US$", "raw": "integer"}));
        assert_eq!(glued.paint(1200.0).unwrap(), "US$1,200");
        assert_eq!(glued.paint(-500.0).unwrap(), "\u{2212}US$500");
        assert_eq!(glued.paint(0.0).unwrap(), "US$0");
        let spaced = slot(json!({"spec": ",.0f", "prefix": "CHF ", "raw": "integer"}));
        assert_eq!(spaced.paint(1200.0).unwrap(), "CHF 1,200");
        assert_eq!(spaced.paint(-500.0).unwrap(), "CHF \u{2212}500");
    }

    #[test]
    fn an_authored_sign_placement_wins_over_the_prefix_default() {
        let after = slot(json!({"spec": ",.0f", "prefix": "US$", "sign_placement": "after_prefix"}));
        assert_eq!(after.paint(-500.0).unwrap(), "US$\u{2212}500");
        let before = slot(json!({"spec": ",.0f", "prefix": "CHF ", "sign_placement": "before_prefix"}));
        assert_eq!(before.paint(-500.0).unwrap(), "\u{2212}CHF 500");
    }

    #[test]
    fn a_suffix_trails_the_digits() {
        let suffix = slot(json!({"spec": ",.0f", "suffix": " CHF", "raw": "integer"}));
        assert_eq!(suffix.paint(1200.0).unwrap(), "1,200 CHF");
        assert_eq!(suffix.paint(-500.0).unwrap(), "\u{2212}500 CHF");
        let pct = slot(json!({"spec": ".1%", "suffix": " YoY", "raw": ".1%"}));
        assert_eq!(pct.paint(0.123).unwrap(), "12.3% YoY");
        assert_eq!(pct.paint(-0.05).unwrap(), "\u{2212}5.0% YoY");
    }

    #[test]
    fn a_notation_respells_the_si_letter_and_only_an_si_spec() {
        let analytic = slot(json!({"spec": ".3~s", "prefix": "US$", "notation": "analytic"}));
        assert_eq!(analytic.paint(1_234_567.0).unwrap(), "US$1.23M");
        assert_eq!(analytic.paint(2500.0).unwrap(), "US$2.5K");
        let narrative = slot(json!({"spec": ".3~s", "prefix": "US$", "notation": "narrative"}));
        assert_eq!(narrative.paint(1_234_567.0).unwrap(), "US$1.23mn");
        assert_eq!(narrative.paint(2500.0).unwrap(), "US$2.5k");
        let bare = slot(json!({"spec": ".3~s", "notation": "analytic", "raw": ".3~s"}));
        assert_eq!(bare.paint(1_234_567.0).unwrap(), "1.23M");
        let fixed = slot(json!({"spec": ",.0f", "notation": "analytic"}));
        assert_eq!(fixed.paint(1_234_567.0).unwrap(), "1,234,567");
        // d3's milli letter has no house word and stays as d3 wrote it.
        assert_eq!(analytic.paint(0.67).unwrap(), "US$670m");
    }

    /// The letters past trillions (`_SI_PAST_TRILLIONS`, `format_d3.py:141`)
    /// are d3's own letter, glued to the digits at both registers. Oracle:
    /// `format_d3(v, ".3~s", "US$", "", n, "before_prefix")` for
    /// `n` in `analytic`, `narrative` gives `US$2.5P` (v = 2.5e15),
    /// `\u2212US$1.5E` (v = -1.5e18), `US$3Z` (3e21), `US$4Y` (4e24).
    #[test]
    fn the_letters_past_trillions_glue_to_the_digits_at_both_registers() {
        for notation in ["analytic", "narrative"] {
            let slot = slot(json!({"spec": ".3~s", "prefix": "US$", "notation": notation}));
            assert_eq!(slot.paint(2.5e15).unwrap(), "US$2.5P", "{notation}");
            assert_eq!(slot.paint(-1.5e18).unwrap(), "\u{2212}US$1.5E", "{notation}");
            assert_eq!(slot.paint(3e21).unwrap(), "US$3Z", "{notation}");
            assert_eq!(slot.paint(4e24).unwrap(), "US$4Y", "{notation}");
        }
        let house = slot(json!({"spec": ".3~s", "prefix": "US$", "raw": "number"}));
        assert_eq!(format_mark_label(Some(&house), 2.5e15, true).unwrap(), "US$2.5P");
    }

    #[test]
    fn a_literal_spec_without_notation_keeps_d3s_letters() {
        let literal = slot(json!({"spec": ".3~s", "prefix": "CHF ", "raw": ".3~s"}));
        assert_eq!(literal.paint(1_234_567.0).unwrap(), "CHF 1.23M");
        assert_eq!(literal.paint(2500.0).unwrap(), "CHF 2.5k");
        assert_eq!(literal.paint(0.67).unwrap(), "CHF 670m");
    }

    #[test]
    fn an_affixed_slot_with_no_spec_refuses_by_name() {
        let err = slot(json!({"spec": "", "prefix": "CHF "})).paint(5.0).unwrap_err();
        assert!(err.contains("needs a d3 spec"), "{err}");
    }

    /// Oracle: `painted_label_text` (`features/value_labels.py`).
    #[test]
    fn a_house_value_label_spells_its_register_inside_the_affix() {
        let label = |v: Value, value: f64| format_mark_label(Some(&slot(v)), value, true).unwrap();
        let chf = json!({"spec": ".3~s", "prefix": "CHF ", "raw": "number"});
        assert_eq!(label(chf.clone(), 1_234_567.0), "CHF 1.23mn");
        assert_eq!(label(chf.clone(), -1_234_567.0), "CHF \u{2212}1.23mn");
        assert_eq!(label(chf, 0.67), "CHF 0.67");
        let analytic = json!({"spec": ".3~s", "prefix": "US$", "notation": "analytic", "raw": "number"});
        assert_eq!(label(analytic.clone(), 2500.0), "US$2.5K");
        assert_eq!(label(analytic.clone(), 0.67), "US$0.67");
        assert_eq!(label(analytic, 0.0), "US$0");
        assert_eq!(label(json!({"spec": ".3~s", "raw": "number"}), 1_234_567.0), "1.23mn");
    }

    #[test]
    fn a_non_house_value_label_keeps_d3_unless_a_notation_is_authored() {
        let label = |v: Value, value: f64| format_mark_label(Some(&slot(v)), value, false).unwrap();
        assert_eq!(label(json!({"spec": ".3~s", "prefix": "US$", "raw": "number"}), 1_234_567.0), "US$1.23M");
        assert_eq!(
            label(json!({"spec": ".3~s", "notation": "analytic", "raw": ".3~s"}), 2500.0),
            "2.5K"
        );
        assert_eq!(label(json!({"spec": "$,.0f", "raw": "currency_whole"}), -500.0), "\u{2212}$500");
    }
}

#[cfg(test)]
mod estimated_gutter_tests {
    use super::*;
    use crate::text::FontBook;
    use serde_json::json;

    const FAMILY: &str = "'dbt Sans Tabular', 'Noto Emoji', Inter, system-ui, sans-serif";

    fn fonts() -> FontBook {
        let dir = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        );
        FontBook::load_dir(dir).expect("test fonts load")
    }

    fn axis(continuous: Value) -> AxisStyle {
        let fixture: Value = serde_json::from_str(include_str!("../../fixtures/line_single.json"))
            .expect("fixture parses");
        let mut axis: AxisStyle =
            Deserialize::deserialize(&fixture["resolved"]["style"]["axis_y"]).expect("axis_y");
        axis.scale = json!({ "continuous": continuous });
        axis
    }

    #[test]
    fn a_hidden_or_zero_length_tick_has_no_length() {
        let mut axis = axis(json!({}));
        for (visible, length, expected) in [
            (Some(true), None, 6.0),
            (Some(true), Some(9.0), 9.0),
            (Some(false), Some(9.0), 0.0),
            (Some(true), Some(0.0), 0.0),
        ] {
            axis.ticks.visible = visible;
            axis.ticks.length = length;
            assert_eq!(axis.tick_len(), expected, "{visible:?} {length:?}");
        }
    }

    fn gutter(axis: &AxisStyle, format: &str, lo: f64, hi: f64) -> Result<f64, String> {
        estimated_tick_label_gutter(&fonts(), FAMILY, 11.0, 180.0, None, Some(&FormatSlot::from(format)), axis, lo, hi)
    }

    /// Production bakes `labelPadding = 56.02978515625` for `revenue_log`
    /// (extent 18837..75739): the widest of d3's log candidates is
    /// `$400,000`, wider than the linear ladder's `$100,000`.
    #[test]
    fn a_log_axis_is_sized_from_the_log_candidates_not_the_linear_ladder() {
        let log = axis(json!({"type": "log"}));
        assert_eq!(gutter(&log, "$,.0f", 18837.0, 75739.0).unwrap(), 56.02978515625);
    }

    /// The candidates are the authored base's powers, not base ten's.
    #[test]
    fn a_log_axis_is_sized_from_the_authored_base() {
        let base_two = axis(json!({"type": "log", "log": {"base": 2.0}}));
        let base_ten = axis(json!({"type": "log"}));
        assert_ne!(
            gutter(&base_two, "$,.0f", 18837.0, 75739.0).unwrap(),
            gutter(&base_ten, "$,.0f", 18837.0, 75739.0).unwrap()
        );
    }

    #[test]
    fn a_log_axis_over_billions_widens_to_the_four_hundred_million_label() {
        let log = axis(json!({"type": "log"}));
        assert_eq!(gutter(&log, ".3~s", 990_000_000.0, 8_200_000_000.0).unwrap(), 34.92138671875);
    }

    /// An authored affix widens a ladder-less tick-label axis's gutter in every arm
    /// (`_measured_label_padding.estimated_quantitative_tick_labels`).
    #[test]
    fn an_authored_affix_widens_a_ladder_less_tick_label_gutter() {
        let tick_label: TickLabelConfig = serde_json::from_value(json!({
            "format": ",.0f", "si_format": ".3~s", "scientific_format": ".2e"
        }))
        .unwrap();
        let bare = FormatSlot::from(",.0f");
        let affixed = FormatSlot { prefix: "USD ".to_string(), ..FormatSlot::from(",.0f") };
        let linear = axis(json!({"type": null}));
        let measure = |slot: &FormatSlot| {
            estimated_tick_label_gutter(&fonts(), FAMILY, 11.0, 180.0, Some(&tick_label), Some(slot), &linear, 0.0, 75739.0).unwrap()
        };
        assert!(measure(&affixed) > measure(&bare) + 20.0, "{} vs {}", measure(&affixed), measure(&bare));
    }

    #[test]
    fn a_linear_axis_keeps_the_zero_extended_linear_ladder() {
        let linear = axis(json!({"type": null}));
        assert_eq!(gutter(&linear, "$,.0f", 18837.0, 75739.0).unwrap(), 53.39794921875);
    }

    #[test]
    fn a_log_axis_over_non_positive_data_is_refused() {
        let log = axis(json!({"type": "log"}));
        let err = gutter(&log, "$,.0f", 0.0, 75739.0).unwrap_err();
        assert!(err.contains("positive"), "{err}");
    }
}

/// `svg_utils.border_dash_attrs` (`svg_utils.py:353-366`): the one builder of a
/// dashed border's stroke attributes. Empty (solid, the default) when
/// `dash_array` is unset, gated on that field alone, never on the border's
/// width. Otherwise `stroke-dasharray`, then `stroke-linecap` and
/// `stroke-dashoffset` when set. Dashes and the offset print as `{:g}`
/// (`format_g`).
pub fn border_dash_pairs(dash_array: Option<&[f64]>, line_cap: Option<&str>, dash_offset: Option<f64>) -> Vec<(&'static str, String)> {
    let Some(dash_array) = dash_array else {
        return Vec::new();
    };
    let mut pairs = vec![(
        "stroke-dasharray",
        dash_array.iter().map(|v| d3rs::py_float::format_g(*v)).collect::<Vec<_>>().join(","),
    )];
    if let Some(line_cap) = line_cap {
        pairs.push(("stroke-linecap", line_cap.to_string()));
    }
    if let Some(dash_offset) = dash_offset {
        pairs.push(("stroke-dashoffset", d3rs::py_float::format_g(dash_offset)));
    }
    pairs
}

/// [`border_dash_pairs`] as attribute text for hand-written markup, each
/// pair with a leading space so the string follows a rect's `stroke-width`.
pub fn border_dash_attrs(dash_array: Option<&[f64]>, line_cap: Option<&str>, dash_offset: Option<f64>) -> String {
    border_dash_pairs(dash_array, line_cap, dash_offset)
        .into_iter()
        .map(|(name, value)| format!(r#" {name}="{}""#, crate::svg::xml_escape_attr(&value)))
        .collect()
}

#[cfg(test)]
mod measure_family_tests {
    use super::*;

    #[test]
    fn serif_measures_under_the_authored_family_not_the_web_alias() {
        assert_eq!(
            measure_family_stand_in("'Source Serif 4 Web', 'Source Serif 4', Georgia, serif"),
            "'Source Serif 4', Georgia, serif"
        );
        assert_eq!(measure_family_stand_in("Inter Variable, sans-serif"), "Inter");
    }
}

#[cfg(test)]
mod predefined_native_tests {
    use super::*;

    #[test]
    fn every_engine_native_name_has_a_formatter_and_no_spec() {
        for name in d3rs::predefined_formats::PREDEFINED_NATIVE {
            assert!(d3rs::native::predefined_native(name, 1.0).unwrap().is_some(), "{name}: no native arm");
            assert_eq!(predefined_number_spec(name), None, "{name} is native, not a d3 spec");
        }
    }

    #[test]
    fn a_spec_name_is_not_a_native() {
        for spec in d3rs::predefined_formats::PREDEFINED_SPECS {
            assert_eq!(d3rs::native::predefined_native(spec.name, 1.0), Ok(None), "{}", spec.name);
        }
    }
}

/// A value that reaches a live `style="color: ..."` declaration must be nothing
/// but one color. Python validates authored colors with `core/colors.py`
/// (`check_table_link_colors`, `BOX_SHADOW_PATTERN`); this is the attribute-safety backstop, so
/// `red; background-image:url(//evil)` can never open a declaration.
pub fn css_color(color: &str) -> Result<&str, Diagnostic> {
    let hex = color.strip_prefix('#').is_some_and(|h| {
        matches!(h.len(), 3 | 4 | 6 | 8) && h.bytes().all(|b| b.is_ascii_hexdigit())
    });
    let token = !color.is_empty()
        && color
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || " (),.%/+-".contains(c))
        && !color.to_ascii_lowercase().contains("url");
    if hex || token {
        return Ok(color);
    }
    Err(Diagnostic::new(
        &codes::ERR_CSS_COLOR_INVALID_AT_RENDER,
        [("value", json!(color)), ("detail", json!("not a single CSS color"))],
    ))
}

#[cfg(test)]
mod css_color_tests {
    use super::*;

    #[test]
    fn a_value_that_opens_a_declaration_is_not_a_color() {
        assert!(css_color("red; background-image:url(//evil)").is_err());
        assert!(css_color("#12").is_err());
        for ok in ["#123456", "#fff", "red", "rgb(1, 2, 3)", "hsl(120 50% 40%)"] {
            assert_eq!(css_color(ok).unwrap(), ok);
        }
    }
}

/// A cell's Python equality class: `1`, `1.0` and `True` are one key, an
/// integral double equals the int it names, and `None` is its own key. A list
/// or object cell (unhashable in Python) keys by its JSON text.
#[derive(Clone, PartialEq, Eq, Hash)]
pub enum PyKey {
    None,
    Int(i128),
    Float(u64),
    Str(String),
}

pub fn py_scalar_key(cell: &Value) -> PyKey {
    match cell {
        Value::Null => PyKey::None,
        Value::Bool(b) => PyKey::Int(i128::from(*b)),
        Value::String(s) => PyKey::Str(s.clone()),
        Value::Number(n) if n.is_f64() => {
            let x = n.as_f64().expect("an f64 number");
            // Every JSON int fits in i128, so an integral double past it
            // equals no int.
            if x.fract() == 0.0 && x.abs() < 2f64.powi(127) {
                PyKey::Int(x as i128)
            } else {
                PyKey::Float(x.to_bits())
            }
        }
        Value::Number(n) => PyKey::Int(n.as_i64().map_or_else(|| i128::from(n.as_u64().expect("a u64")), i128::from)),
        Value::Array(_) | Value::Object(_) => PyKey::Str(cell.to_string()),
    }
}

#[cfg(test)]
mod distinct_values_tests {
    use super::*;
    use serde_json::json;

    fn rows(values: Vec<Value>) -> Vec<serde_json::Map<String, Value>> {
        values.into_iter().map(|v| json!({"x": v}).as_object().unwrap().clone()).collect()
    }

    /// `1`, `1.0` and `True` are one Python key; `"1"`, `None` and `""` are three more.
    #[test]
    fn distinct_values_follow_pythons_equality() {
        let cells = rows(vec![json!(1), json!(1.0), json!(true), json!("1"), json!(null), json!("")]);
        assert_eq!(distinct_values(&cells, "x"), 4);
    }
}

#[cfg(test)]
mod axis_labels_expr_tests {
    use super::*;

    fn labels(align: Option<&str>, expr: Option<&str>) -> AxisLabels {
        serde_json::from_value(serde_json::json!({"font": {}, "align": align, "expr": expr})).unwrap()
    }

    #[test]
    fn a_value_tick_speaks_the_expression_over_its_own_text() {
        let spoken = labels(None, Some("datum.label + 'k' + (datum.value > 100 ? '!' : '')"));
        assert_eq!(spoken.apply_expr("x", 150.0, "150".into(), 0, 1).unwrap(), "150k!");
        assert_eq!(spoken.apply_expr("x", 50.0, "50".into(), 0, 1).unwrap(), "50k");
        assert_eq!(labels(None, None).apply_expr("x", 50.0, "50".into(), 0, 1).unwrap(), "50");
        assert!(labels(None, Some("nope(1)")).apply_expr("x", 1.0, "1".into(), 0, 1).is_err());
    }

    #[test]
    fn an_authored_expression_keeps_only_the_formats_digits_as_the_label() {
        let affixed: AxisLabels = serde_json::from_value(serde_json::json!({
            "font": {}, "expr": "datum.label + '!'", "format": {"spec": ",.0f", "prefix": "$", "suffix": "k"},
        }))
        .unwrap();
        assert_eq!(affixed.apply_expr("x", 5000.0, "$5,000k".into(), 0, 1).unwrap(), "5,000!");
    }

    #[test]
    fn an_authored_expression_drops_an_own_side_or_centered_align_only() {
        assert_eq!(labels(Some("right"), None).align_kept("right"), Some("right"));
        assert_eq!(labels(Some("right"), Some("datum.label")).align_kept("right"), None);
        assert_eq!(labels(Some("center"), Some("datum.label")).align_kept("right"), None);
        assert_eq!(labels(Some("left"), Some("datum.label")).align_kept("right"), Some("left"));
    }
}
