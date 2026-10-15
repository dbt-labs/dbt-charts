//! chart-renderer — spike library: dbt charts `Resolved*Chart` payloads → SVG,
//! no Vega-Lite, no JS runtime. One module per chart family; families own
//! their payload structs and deserialize the raw `resolved` JSON themselves.

pub mod board;
pub mod case;
pub mod codes;
pub mod common;
pub mod conditions;
pub mod constants;
pub mod contract;
pub mod diagnostic;
pub mod embed_fonts;
pub mod families;
pub mod font_normalize;
pub mod frame;
pub mod href;
pub mod label_template;
pub mod number_locale;
pub mod prose;
pub mod sizing;
pub mod step_band;
pub mod svg;
pub mod text;
pub mod time_locale;
pub mod title;
pub mod title_shift;
pub mod titlecase_first_char;
pub mod variables;
pub mod vega_fit;
pub mod xml;

use std::borrow::Cow;
use std::collections::HashMap;

use diagnostic::{Diagnostic, RenderOutput};
use serde::Deserialize;
use serde_json::Value;

pub use title_shift::TitleShift;

/// Four-sided outer margin. A VL family reads it as an inward inset into
/// the canvas before any chart chrome; kpi/table read it the other way —
/// how far their own card-fill rect reaches back OUT over the padding
/// their render box was shrunk by (`common::card_box`). spark_bar is also
/// a padding-shrunk SVG family but has no card-fill rect and reads this
/// field for neither purpose.
#[derive(Deserialize, Clone, Copy)]
pub struct Padding {
    pub left: f64,
    pub right: f64,
    pub top: f64,
    pub bottom: f64,
}

/// Semantic tone palette (`Style.tones` — board level, KpiTonesStyle):
/// theme-provided hexes for the KPI support row (and, in production, table
/// conditional glyphs). Mirrors production's `resolve_tone_color`
/// (render/utils.py): a direct name → field lookup.
#[derive(Deserialize, Clone)]
pub struct TonesStyle {
    pub positive: String,
    pub negative: String,
    pub warning: String,
    pub info: String,
}

impl TonesStyle {
    pub fn resolve(&self, tone: &str) -> Option<&str> {
        match tone {
            "positive" => Some(&self.positive),
            "negative" => Some(&self.negative),
            "warning" => Some(&self.warning),
            "info" => Some(&self.info),
            _ => None,
        }
    }
}

#[derive(Deserialize, Clone)]
pub struct TextMarkFont {
    pub family: String,
    pub size: f64,
}

/// Board-level `ResolvedStyle` tokens shared across families — the muted
/// ink and the tone palette. Production hands the whole `ResolvedStyle` to
/// every renderer alongside the chart; this envelope carries only the
/// slices a family actually reads. Optional on the wire because pre-slice
/// fixtures omit it, but a family whose production renderer asserts a
/// token (spark_bar: `muted`, the title case) refuses its absence by name
/// rather than painting a stand-in.
#[derive(Deserialize, Clone)]
pub struct BoardStyle {
    pub muted: Option<String>,
    pub tones: Option<TonesStyle>,
    /// Board-level `style.title.font.case` — the title case transform the
    /// chart/table/spark renderers all share (spark_bar.py:434-448).
    #[serde(default)]
    pub title_case: Option<String>,
    /// `chart_defaults.view.continuous_height` — the VL default view height
    /// (`config.view.continuousHeight`), the effective height of any child
    /// spec production renders with `height=None` (the attached-table pie's
    /// wheel). Optional in the envelope; the paths that need it error when
    /// it is absent rather than assuming a theme value.
    #[serde(default)]
    pub continuous_height: Option<f64>,
    /// The theme's VL `config.font` — the family Vega-Lite compiles into the
    /// `guide-title` and `guide-label` styles (`config.ts:505-512`), which a
    /// legend with no legend config (a shape channel's own) draws in. Optional
    /// in the envelope; a chart that needs it errors when it is absent rather
    /// than borrowing another slot's family.
    #[serde(default)]
    pub font: Option<String>,
    /// The board's own text color (`ResolvedStyle.font.color`): a linked table
    /// cell takes it. Optional in the envelope; a table with a link errors
    /// when it is absent.
    #[serde(default)]
    pub font_color: Option<String>,
    /// The theme's text-mark font (`chart_defaults.marks.text.font`): the face a
    /// value label measures in when it authors none.
    pub text_mark_font: TextMarkFont,
    /// `chart_defaults.dark_companion_palette`: the board's label ink per palette
    /// stop, which a grouped per_series column ranks its series into. Optional in
    /// the envelope; that column errors when it is absent.
    #[serde(default)]
    pub dark_companion_palette: Option<Vec<String>>,
    /// The root board's width, which `WARN-TABLE-CRAMPED`'s suggested width is
    /// sized against. Optional in the envelope; a cramped table renders without
    /// the suggested width when it is absent.
    #[serde(default)]
    pub board_width: Option<f64>,
    /// The board's `style.formats` alias table: a format name the board
    /// redefined (`date_short: "%Y-%m-%d"`) resolves through it. Null: no table.
    #[serde(default)]
    pub formats: Option<serde_json::Map<String, Value>>,
    /// The plain-digit spec each adaptive member (`currency`, `number`) swaps to
    /// below 1, resolved against the board's `style.formats` and currency
    /// (`sub_unit_spec`). Optional in the envelope; a KPI errors when it is absent.
    #[serde(default)]
    pub sub_unit_specs: Option<std::collections::BTreeMap<String, String>>,
    /// Height (px) of a paginated table's control bar, owned by the sizer
    /// (`TABLE_PAGINATION_CONTROL_HEIGHT`) so both sides reserve the same band.
    /// Optional in the envelope; a table errors when it is absent.
    #[serde(default)]
    pub pagination_control_height: Option<f64>,
    /// `chart_rendering.support_table.column_block_share_warn_ratio`: the share
    /// of its card a support_table column block may claim before it warns.
    #[serde(default)]
    pub column_block_share_warn_ratio: Option<f64>,
    /// `SPARK_BAR_TITLE_HEIGHT_PX` / `SPARK_BAR_TITLE_BASELINE_Y_PX`.
    /// Optional in the envelope; a titled spark_bar errors when it is absent.
    #[serde(default)]
    pub spark_bar_title: Option<SparkBarTitle>,
    /// `ResolvedStyle.placeholder`; a placeholder chart errors when it is absent.
    #[serde(default)]
    pub placeholder: Option<PlaceholderStyle>,
    /// The board's resolved `regional.format_locale` (`HouseLocale`): the
    /// vocabulary every number on the board prints in. Required — a payload
    /// without it names the field rather than printing en-US.
    pub format_locale: number_locale::NumberLocale,
    /// The board's resolved `regional.locale.time`: the month, day and period
    /// names (and the `%c %x %X` patterns) a date prints in. Required, like
    /// `format_locale`.
    pub time_locale: time_locale::TimeLocale,
}

impl BoardStyle {
    /// Run `render` in this board's number and time locales.
    pub fn scoped<T>(&self, render: impl FnOnce() -> T) -> T {
        number_locale::with_number_locale(&self.format_locale, || {
            time_locale::with_time_locale(&self.time_locale, render)
        })
    }
}

/// A spark_bar's title block: its height, and the first baseline within it.
#[derive(Deserialize, Clone, Copy)]
pub struct SparkBarTitle {
    pub height: f64,
    pub baseline_y: f64,
}

/// How a placeholder chart is faded and what its overlay says.
#[derive(Deserialize, Clone)]
pub struct PlaceholderStyle {
    pub opacity: f64,
    pub overlay: PlaceholderOverlay,
}

#[derive(Deserialize, Clone)]
pub struct PlaceholderOverlay {
    pub text: String,
    pub background: String,
    pub font: PlaceholderFont,
}

#[derive(Deserialize, Clone)]
pub struct PlaceholderFont {
    pub size: f64,
    /// `FontStyle.weight`: a CSS keyword or a number, printed as authored.
    pub weight: Value,
    pub color: String,
    /// The board's `ResolvedStyle.font.family`, the family production's
    /// Vega-family overlay paints in. Optional in the envelope; a pie
    /// placeholder errors when it is absent.
    #[serde(default)]
    pub family: Option<String>,
}

impl PlaceholderStyle {
    /// `placeholder.py::add_placeholder_overlay`: a `width` x `height` wash and
    /// the overlay text centered on it, in `family`, cased by `case`.
    pub fn overlay(
        &self,
        xml: &mut xml::Xml,
        width: f64,
        height: f64,
        family: &str,
        case: Option<&str>,
    ) -> Result<(), Diagnostic> {
        use crate::svg::py_float_str;
        use crate::xml::Attrs;
        let overlay = &self.overlay;
        let text = match case {
            Some(case) if case != "none" => crate::case::apply_case(&overlay.text, case)?,
            _ => overlay.text.clone(),
        };
        // `escape_attr` prints a keyword as written, an int bare, a float with its `.0`.
        let weight = match &overlay.font.weight {
            Value::String(s) => s.clone(),
            Value::Number(n) if n.is_i64() || n.is_u64() => n.to_string(),
            Value::Number(n) => py_float_str(n.as_f64().ok_or("placeholder font weight is not a number")?),
            other => return Err(format!("placeholder font weight {other} is neither a keyword nor a number").into()),
        };
        xml.open("g", Attrs::new().set("class", "placeholder-overlay"));
        xml.empty(
            "rect",
            Attrs::new()
                .set("class", "dbt-pointer-inert")
                .set("x", "0")
                .set("y", "0")
                .set("width", py_float_str(width))
                .set("height", py_float_str(height))
                .set("fill", &overlay.background),
        );
        xml.leaf(
            "text",
            Attrs::new()
                .set("class", "dbt-pointer-inert")
                .set("x", py_float_str(width / 2.0))
                .set("y", py_float_str(height / 2.0))
                .set("text-anchor", "middle")
                .set("dominant-baseline", "middle")
                .set("font-family", family)
                .set("font-size", py_float_str(overlay.font.size))
                .set("font-weight", weight)
                .set("fill", &overlay.font.color),
            &text,
        );
        xml.close("g");
        Ok(())
    }
}

/// The fields every table render requires of `board_style`, filled in for a
/// unit test unless it names them.
#[cfg(test)]
fn with_required_board_fields(mut style: Value) -> Value {
    let fields = style.as_object_mut().expect("board_style is an object");
    fields.entry("format_locale").or_insert_with(number_locale::en_us_wire);
    fields.entry("time_locale").or_insert_with(time_locale::en_us_wire);
    fields.entry("pagination_control_height").or_insert(serde_json::json!(28.0));
    fields.entry("text_mark_font").or_insert_with(|| {
        serde_json::json!({"family": "'Inter Variable', 'Noto Emoji', Inter, system-ui, sans-serif", "size": 11.0})
    });
    fields.entry("column_block_share_warn_ratio").or_insert(serde_json::json!(0.4));
    fields
        .entry("sub_unit_specs")
        .or_insert_with(|| serde_json::json!({"currency": "$,.2f", "number": ",.2f"}));
    style
}

/// A unit-test board style as the Python seam would send it.
#[cfg(test)]
pub(crate) fn test_board_style(style: Value) -> Option<BoardStyle> {
    Some(serde_json::from_value(with_required_board_fields(style)).expect("board style parses"))
}

/// A unit-test payload as the Python seam would send it.
#[cfg(test)]
pub(crate) fn test_payload(mut payload: Value) -> Payload {
    let fields = payload.as_object_mut().expect("a payload is an object");
    let style = fields.remove("board_style").unwrap_or_else(|| serde_json::json!({}));
    fields.insert("board_style".into(), with_required_board_fields(style));
    serde_json::from_value(payload).expect("payload deserializes")
}
/// The envelope the Python harness dumps: the family's Resolved*Chart
/// model_dump plus the raw query rows and the render box.
///
/// Canvas is always exactly `width × height`. All chrome (title, axes, rail)
/// is inset within `padding` on all four sides; treat missing padding as zeros.
///
/// `Clone` exists for the wide-measures fold (`common::unfold_wide_rows`'s
/// two callers, `line.rs`/`bar.rs`): each rebinds its local `payload` to a
/// clone whose `rows` are the unfolded long rows. That deep-clones
/// `resolved` too, which is wasted work (both callers deserialize `r` from
/// `payload.resolved.clone()` before the fold and never read `.resolved`
/// again), but threading `rows` through as a bare extra parameter instead
/// would mean adding it to every remaining `payload.rows`/`&payload`
/// reference in `render_with_layout`/`render_horizontal`/`render_vertical` (each
/// thousands of lines, and `payload.width`/`.height`/`.background`/
/// `.padding` stay read off the shared `Payload` throughout) — a much
/// larger diff than the fold itself. Revisit if a third family joins it.
#[derive(Deserialize, Clone)]
#[serde(remote = "Self")]
pub struct Payload {
    pub resolved: Value,
    pub rows: Vec<serde_json::Map<String, Value>>,
    pub width: f64,
    pub height: f64,
    pub background: Option<String>,
    /// Directory of the shipped dbt charts TTFs, for real text measurement.
    pub fonts_dir: Option<String>,
    pub padding: Option<Padding>,
    pub board_style: Option<BoardStyle>,
    /// The board's variable values for this render: what a table reads its
    /// `{chart_id}_page` from (`board_variables.current_board_variables()`).
    #[serde(default)]
    pub variables: serde_json::Map<String, Value>,
    /// The board's declared variable names: a table cell in a column of the
    /// same name links to `?col=value` (`auto_link.get_filter_variables_context`).
    #[serde(default)]
    pub filter_variables: Vec<String>,
    /// The host acts on `data-dbt-page-var` (`dct serve`, Cloud): a paginated
    /// table draws only the page its variable names, and its controls commit
    /// that variable. Off, an export pre-renders every page
    /// (`controls.controls_are_interactive`).
    #[serde(default)]
    pub interactive_controls: bool,
    /// Where the host serves boards: a table's cell and row links are rewritten
    /// through it (`board_links.resolve_href`). Absent outside a board render.
    #[serde(default)]
    pub link_context: Option<href::LinkContext>,
    /// Every dataset production's own `build_chart_datasets` produced,
    /// keyed by query name — verbatim, no reshaping. `rows` is always
    /// `datasets[chart.query_name]`; a `chart.layers` entry whose own
    /// `query_name` differs from the chart's reads its rows from here (see
    /// `_overlay.py::_resolve_layer_rows`). Absent on any payload predating
    /// this field (every hand-built test fixture, `harness.py`'s minimal
    /// specs) — only a family's own layers arm reads it, and only when a
    /// layer authors a differing `query_name`.
    #[serde(default)]
    pub datasets: Option<HashMap<String, Vec<serde_json::Map<String, Value>>>>,
    /// Set by `sizing::render_first_vega_height`'s own probe render —
    /// production's ONE "static estimate, before any layout decision"
    /// measurement call (`layout_sizing.py`'s `item.height == 0` branch) —
    /// AND by `board.rs::render_chart_item` for a cols-row chart `sizing::
    /// calculate_cols_boxes` force-realigned away from its own pristine
    /// natural, and for a chart alone in a nested `rows` board that a parent
    /// cols row forced (`sizing::forced_fresh_renders`): production gives
    /// that a FRESH render too (`render_cache`
    /// misses on the new height, `layout_sizing.py::_align_cols_heights`),
    /// not a reuse of the first probe's cached SVG. Every OTHER board
    /// paint renders at its own already-decided slot height (a real cache
    /// hit in production) and leaves this at its `false` default. Gates
    /// line/area's rail-concat pane-shrink law (`docs/laws-line-area.md`'s
    /// rail-concat height section): that correction reads the WIDE,
    /// pre-shrink title/subtitle wrap to decide how far the pane may
    /// shrink, which can disagree with the narrow, real wrap that actually
    /// paints — a real, one-line residual production's own single-pass
    /// correction leaves uncorrected. Re-deriving that residual on a
    /// render that's already at the grown height would double it; this
    /// flag is the one signal this port has for "this render gets a fresh
    /// probe-and-shrink pass" without threading production's own render
    /// cache. `#[serde(default)]` because every payload the Python harness
    /// sends (`board_sweep.py`, `golden_sweep.py`) predates this field and
    /// never sets it — `false` is the correct read of an absent key, not a
    /// silent guess.
    #[serde(default)]
    pub measuring: bool,
    /// The layout item's row title shift (`LayoutItem.title_shift`): zero on
    /// every render outside a `cols` row whose titles disagree — the model's own
    /// default (`NO_TITLE_SHIFT`, `compile/models/board/resolved.py:159`).
    #[serde(default)]
    pub title_shift: TitleShift,
    /// The chart draws placeholder data: spark_bar fades its content and lays
    /// the "add data" overlay over it.
    #[serde(default)]
    pub is_placeholder: bool,
    /// The slot height an ancestor's literal `height:` assigned this chart
    /// (`authored_chart_heights`); absent when no ancestor authored one. The
    /// horizontal bar's category floor is judged against it.
    #[serde(default)]
    pub authored_height: Option<f64>,
    /// `execution.max_template_output_bytes`: the most SVG a custom chart's
    /// template may emit for one render. Only the custom family reads it.
    #[serde(default)]
    pub max_template_output_bytes: Option<u64>,
    /// The `str()` text of each cell the wire tagged (a Decimal, a non-finite
    /// value) in `rows`, by row and column; filled when the payload is parsed.
    #[serde(skip)]
    pub cell_text: common::CellText,
}

impl Payload {
    /// The id of the chart this payload renders, for the diagnostics it raises.
    pub(crate) fn chart_id(&self) -> Result<&str, String> {
        self.resolved
            .get("id")
            .and_then(Value::as_str)
            .ok_or_else(|| "payload.resolved.id missing".to_string())
    }
}

/// The one place the wire's cell markers are decoded (`common::decode_cells`):
/// every family reads plain numbers from `rows` and `datasets`.
impl<'de> Deserialize<'de> for Payload {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let mut payload = Payload::deserialize(deserializer)?;
        payload.cell_text = common::decode_cells(&mut payload.rows).map_err(serde::de::Error::custom)?;
        for rows in payload.datasets.iter_mut().flat_map(HashMap::values_mut) {
            common::decode_cells(rows).map_err(serde::de::Error::custom)?;
        }
        Ok(payload)
    }
}

pub fn render(payload: &Payload, fonts: &text::FontBook) -> Result<String, Diagnostic> {
    render_sized(payload, fonts).map(|(svg, _)| svg)
}

/// `render`, plus the natural height (see `RenderOutput`).
///
/// A payload that carries a `board_style` prints in that board's number
/// locale; one without it renders inside whatever scope the caller opened
/// (a chart drawn by the board composer) or, with none, en-US (production's
/// `DEFAULT_REGIONAL`: a style resolved without a board). The time locale has
/// no such default: a date name formatted outside any scope refuses.
pub fn render_sized(
    payload: &Payload,
    fonts: &text::FontBook,
) -> Result<(String, Option<f64>), Diagnostic> {
    match &payload.board_style {
        Some(style) => style.scoped(|| render_in_scope(payload, fonts)),
        None => render_in_scope(payload, fonts),
    }
}

fn render_in_scope(
    payload: &Payload,
    fonts: &text::FontBook,
) -> Result<(String, Option<f64>), Diagnostic> {
    let chart_type = payload
        .resolved
        .get("chart_type")
        .and_then(Value::as_str)
        .ok_or("payload.resolved.chart_type missing")?;
    // A chart-root `link` (`ResolvedChart.link`) is painted by bar and pie (a
    // per-datum `<a>`), table (a per-row band), kpi (the value's `<a>`) and
    // custom (the body's `<a>`, or the template's own via `href(row)`); every
    // other family would draw its marks with no href, so it refuses by name
    // instead.
    let has_link = payload
        .resolved
        .get("link")
        .and_then(Value::as_str)
        .is_some_and(|l| !l.is_empty());
    if has_link && !matches!(chart_type, "bar" | "pie" | "table" | "kpi" | "custom") {
        return Err(format!(
            "{chart_type}: chart-root link not supported — only bar, pie, table, kpi and custom"
        )
        .into());
    }
    families::legend::refuse_unmodeled_slide(chart_type, &payload.resolved)?;
    let shifted = title_shift::for_family(payload, chart_type)?;
    let payload: &Payload = &shifted;
    // A query that returned no rows resolves with every field that samples the
    // data unset (`orientation`, `stacked_domain_max`, `columns`, ...), so the
    // family models below cannot deserialize it: the empty result is decided
    // here, ahead of any of them. A callout has no query (`query_name` is
    // `None`), so no rows is its normal shape.
    // A custom template decides its own empty drawing: it gets `rows` as is.
    if chart_type == "custom" {
        return families::custom::render(payload, fonts).map(|svg| (svg, None));
    }
    if payload.rows.is_empty() && chart_type != "callout" {
        return families::empty::render(chart_type, payload, fonts).map(|svg| (svg, None));
    }
    families::cartesian_errors::check(payload, chart_type)?;
    // A Vega-family root is `class="marks"`, the host of the hover markers.
    if chart_type == "callout" {
        let (svg, natural_height) = families::callout::render(payload, fonts)?;
        return Ok((svg, Some(natural_height)));
    }
    let svg = match chart_type {
        "pie" => families::pie::render(payload, fonts).and_then(contract::stamp_view_root)?,
        "bar" => {
            families::bar::production_errors(payload)?;
            families::bar::render(payload, fonts).and_then(|svg| Ok(contract::stamp_view_root(svg)?))?
        }
        "line" => families::line::render(payload, fonts).and_then(|svg| Ok(contract::stamp_view_root(svg)?))?,
        "area" => families::area::render(payload, fonts).and_then(|svg| Ok(contract::stamp_view_root(svg)?))?,
        "scatter" => families::scatter::render(payload, fonts).and_then(|svg| Ok(contract::stamp_view_root(svg)?))?,
        "heatmap" => families::heatmap::render(payload, fonts).and_then(|svg| Ok(contract::stamp_view_root(svg)?))?,
        "geoshape" => families::geoshape::render(payload, fonts)?,
        "histogram" => families::histogram::render(payload, fonts).and_then(|svg| Ok(contract::stamp_view_root(svg)?))?,
        "kpi" => families::kpi::render(payload, fonts)?,
        "table" => families::table::render(payload, fonts)?,
        "spark_bar" => families::spark_bar::render(payload, fonts)?,
        "sankey" => families::sankey::render(payload, fonts).and_then(contract::stamp_view_root)?,
        other => return Err(format!("chart family not implemented: {other}").into()),
    };
    Ok((svg, None))
}

fn is_table(payload: &Payload) -> bool {
    payload.resolved.get("chart_type").and_then(Value::as_str) == Some("table")
}

/// A table's height with nothing bounding it: the one measure core sizes a
/// table slot from (`measure_table_height`). Warnings of the probe render are
/// dropped.
pub fn table_natural_height(payload: &Payload, fonts: &text::FontBook) -> Result<f64, Diagnostic> {
    let mut probe = payload.clone();
    probe.height = 0.0;
    let svg = diagnostic::collect(|| render(&probe, fonts)).0?;
    Ok(board::extract_svg_height(&svg)?)
}

/// `render_fitted`, plus a table's natural height for the callers that read
/// it (the binding and the CLI); the board composer renders without it.
pub fn render_measured(payload: &Payload, fonts: &text::FontBook) -> Result<RenderOutput, Diagnostic> {
    let mut output = render_fitted(payload, fonts)?;
    if is_table(payload) {
        output.natural_height = Some(if payload.height > 0.0 {
            table_natural_height(payload, fonts)?
        } else {
            board::extract_svg_height(&output.svg)?
        });
    }
    Ok(output)
}

pub fn render_output(payload: &Payload, fonts: &text::FontBook) -> Result<RenderOutput, Diagnostic> {
    let (sized, warnings) = diagnostic::collect(|| render_sized(payload, fonts));
    let (svg, natural_height) = sized?;
    Ok(RenderOutput { svg, warnings, natural_height })
}

/// `render`, corrected for a support_table attachment's own width overhead
/// (`layout_sizing.py::_make_data_aware_height_provider`'s `provider()`,
/// `render_ctx.support_table_corrected_widths`).
/// A support_table chart switches its spec to `autosize: pad`
/// (`support_table_attachment.py:2050`) — spec.width becomes the INNER plot
/// rect, and Vega adds axis-label/legend chrome beyond it, so a naive
/// `render()` call's outer canvas can overshoot `payload.width`. Production
/// compensates by measuring the first render's overshoot and re-rendering at
/// a pre-shrunk spec.width so the corrected outer SVG fits the caller's slot.
/// Returns the corrected SVG and, when a correction actually fired, the
/// width-corrected `Payload` (so a caller chaining a further correction on
/// top — `render_fitted`'s own height half — starts from the right width).
/// Whether `payload` can carry either correction at all: support_table's
/// modeled port arms are bar/line/area only (docs/laws-support-table.md —
/// scatter/heatmap/pie never attach one on the authored surface's own
/// family-scoped fields either, and every OTHER family — table, kpi,
/// spark_bar — has no `support_table` field to attach one to). Shared by
/// both corrections below: a chart outside this gate must never re-render
/// at a corrected width OR height, or a family with its OWN unrelated
/// height-fitting law (`table.rs`'s own pagination re-flow chief among
/// them) sees its slot height silently shrunk by this gate's math.
pub(crate) fn is_support_table_arm(payload: &Payload) -> Result<bool, String> {
    let chart_type = payload
        .resolved
        .get("chart_type")
        .and_then(Value::as_str)
        .ok_or("payload.resolved.chart_type missing")?;
    if !matches!(chart_type, "bar" | "line" | "area") {
        return Ok(false);
    }
    Ok(payload.resolved.get("support_table").is_some_and(|v| !v.is_null()))
}

fn render_width_fitted(
    payload: &Payload,
    fonts: &text::FontBook,
) -> Result<(String, Option<Payload>), Diagnostic> {
    let svg = render(payload, fonts)?;
    if !is_support_table_arm(payload)? {
        return Ok((svg, None));
    }
    let chart_type = payload
        .resolved
        .get("chart_type")
        .and_then(Value::as_str)
        .expect("is_support_table_arm already required chart_type");
    let chart_id = payload.resolved.get("id").and_then(Value::as_str).unwrap_or("<unknown>");
    let actual_width = board::extract_svg_width(&svg)?;
    let orientation = payload.resolved.get("orientation").and_then(Value::as_str);
    let corrected = sizing::support_table_width_correction(
        chart_id,
        chart_type,
        orientation,
        payload.width,
        actual_width,
        constants::layout::SUPPORT_TABLE_PLOT_WIDTH_FLOOR_RATIO,
    )?;
    match corrected {
        None => Ok((svg, None)),
        Some(shrunk_width) => {
            let mut corrected_payload = payload.clone();
            corrected_payload.width = shrunk_width;
            let svg = render(&corrected_payload, fonts)
                .map_err(|e| e.in_context(&format!("chart {chart_id} refused during width-correction re-render")))?;
            Ok((svg, Some(corrected_payload)))
        }
    }
}

/// Every caller that hands a chart-family `Payload` to a REAL layout slot —
/// the per-chart sweep's own binary entry point (`main.rs`) and the board
/// composer's per-item render (`board::render_chart_item`) — must call this
/// instead of bare `render`, or a support_table chart's canvas silently
/// grows past the box the sizing/oracle math already agreed on. `render`
/// itself stays a plain one-shot dispatcher: this wrapper's own corrected
/// re-renders call it directly.
///
/// The sizing pass's render-first height measurement
/// (`sizing::render_first_vega_height`) does NOT call this — it calls
/// `render_width_fitted` directly. Production's own render-first sizing
/// pass (`layout_sizing.py::_make_data_aware_height_provider`) applies the SAME width correction
/// inline (its box height is measured off that corrected render), but its
/// height correction (`_correct_support_table_height`, called only from
/// `_align_cols_heights` and `_fix_slot_heights_in_tree`) runs strictly later, on the FINAL render
/// at the already-decided slot height — never during the box-sizing pass
/// itself. Applying it here too would size the box off the CORRECTED
/// (shrunk) height where production sizes it off the uncorrected one.
pub fn render_fitted(payload: &Payload, fonts: &text::FontBook) -> Result<RenderOutput, Diagnostic> {
    render_fitted_natural(payload, payload, fonts)
}

/// `render_fitted` for a slot rendered first from `natural`, the payload at
/// the chart's sizing-time height. Production keeps that cached render while
/// the box still holds its height (`abs(natural_h - item.height) < 1`) and
/// corrects it against the box only if the canvas overshoots
/// (`_correct_support_table_height`); a box moved away from it re-renders
/// at the box height (`_fix_slot_heights_in_tree`), which is `slot` itself.
pub fn render_fitted_natural(
    natural: &Payload,
    slot: &Payload,
    fonts: &text::FontBook,
) -> Result<RenderOutput, Diagnostic> {
    if !is_support_table_arm(slot)? {
        return render_output(slot, fonts);
    }
    let (svg, _, rendered) = render_fitted_with_payload(natural, slot, fonts)?;
    // Production's warning pass renders the spec at the layout width, before any
    // support_table correction, so the warnings are that first render's; its
    // own refusal, if any, is the corrected render's to raise.
    let (_, warnings) = diagnostic::collect(|| render_sized(rendered, fonts));
    Ok(RenderOutput { svg, warnings, natural_height: None })
}

/// `render_fitted`, also handing back the payload of the render that produced
/// the SVG — the width/height-corrected one on a support_table chart, the
/// caller's own otherwise — so `render_layout` describes that render, and the
/// payload (`natural` or `slot`) the first render was made from.
fn render_fitted_with_payload<'a>(
    natural: &'a Payload,
    slot: &'a Payload,
    fonts: &text::FontBook,
) -> Result<(String, Cow<'a, Payload>, &'a Payload), Diagnostic> {
    let (svg, width_corrected_payload) = render_width_fitted(natural, fonts)?;
    if !is_support_table_arm(natural)? {
        return Ok((svg, Cow::Borrowed(natural), natural));
    }
    let chart_id = natural.resolved.get("id").and_then(Value::as_str).unwrap_or("<unknown>");
    // layout_sizing.py's `_correct_support_table_height` (the title
    // offset is a closed-form `plot_top` term, not part of this correction —
    // `support_table_strip::support_table_title_offset_correction`): the
    // SAME autosize:pad overshoot the width correction above measures on
    // the horizontal axis also overshoots vertically, so measure the
    // (possibly width-corrected) render's actual height and shrink again if
    // it overshoots the slot.
    let actual_height = board::extract_svg_height(&svg)?;
    if natural.height != slot.height {
        let chart_type = natural
            .resolved
            .get("chart_type")
            .and_then(Value::as_str)
            .expect("is_support_table_arm already required chart_type");
        let natural_box = sizing::clamp_render_first_height(chart_type, actual_height, natural.height);
        if sizing::height_moved(natural_box, slot.height) {
            return render_fitted_with_payload(slot, slot, fonts);
        }
    }
    match sizing::support_table_height_correction(slot.height, actual_height) {
        None => Ok((svg, width_corrected_payload.map_or(Cow::Borrowed(natural), Cow::Owned), natural)),
        Some(shrunk_height) => {
            let mut corrected_payload = width_corrected_payload.unwrap_or_else(|| natural.clone());
            corrected_payload.height = shrunk_height;
            let svg = render(&corrected_payload, fonts)
                .map_err(|e| e.in_context(&format!("chart {chart_id} refused during height-correction re-render")))?;
            Ok((svg, Cow::Owned(corrected_payload), natural))
        }
    }
}

/// The plot rect the render `render_fitted` writes settled on, as JSON —
/// line and area, the families that hand their `PlotRect` out of the render.
/// Re-runs `render_fitted`'s chain to learn its final payload, then renders
/// that payload once more for the rect; the SVG itself is not returned.
/// A support_table chart on line's legacy splice refuses: the splice
/// composes its strip after the negotiation, so no rect is known to
/// describe the written SVG.
pub fn render_layout(payload: &Payload, fonts: &text::FontBook) -> Result<Value, Diagnostic> {
    match &payload.board_style {
        Some(style) => style.scoped(|| layout_in_scope(payload, fonts)),
        None => layout_in_scope(payload, fonts),
    }
}

fn layout_in_scope(payload: &Payload, fonts: &text::FontBook) -> Result<Value, Diagnostic> {
    let (_, fitted, _) = render_fitted_with_payload(payload, payload, fonts)?;
    let chart_type = fitted
        .resolved
        .get("chart_type")
        .and_then(Value::as_str)
        .ok_or("payload.resolved.chart_type missing")?;
    let draw_areas = match chart_type {
        "line" => false,
        "area" => true,
        other => {
            return Err(format!(
                "layout dump: {other} does not hand its plot rect out of its render — only \
                 line and area do"
            ).into())
        }
    };
    if !fitted.resolved["multiples"].is_null() {
        return Err("layout dump: multiples has no single plot rect, a facet composite has many".into());
    }
    let (_, plot) = families::line::render_with_layout(&fitted, fonts, draw_areas)?;
    if is_support_table_arm(&fitted)? && plot.composed_rows.is_none() {
        return Err("layout dump: this support_table chart took line's legacy splice, which \
                    composes its strip after the negotiation — not modeled"
            .into());
    }
    Ok(serde_json::to_value(plot).map_err(|e| format!("layout dump: {e}"))?)
}

#[cfg(test)]
mod link_gate_tests {
    use super::*;

    /// A family that paints no href refuses a chart-root `link` by name
    /// rather than draw its marks unlinked.
    #[test]
    fn a_family_with_no_link_arm_refuses_a_chart_root_link() {
        for family in [
            "line", "area", "scatter", "heatmap", "histogram", "spark_bar", "callout", "geoshape",
        ] {
            let payload: Payload = serde_json::from_value(serde_json::json!({
                "resolved": {"chart_type": family, "link": "/d/?x={{ x }}"},
                "rows": [], "width": 400.0, "height": 300.0,
            }))
            .unwrap();
            let err = render(&payload, &text::FontBook::empty()).unwrap_err().to_string();
            assert!(err.contains("chart-root link"), "{family}: {err}");
        }
    }

    /// The families that paint a link pass the gate: their own (here,
    /// deliberately malformed) payload error surfaces, never the refusal.
    #[test]
    fn bar_pie_table_and_kpi_pass_the_link_gate() {
        for family in ["bar", "pie", "table", "kpi"] {
            let payload: Payload = serde_json::from_value(serde_json::json!({
                "resolved": {"chart_type": family, "link": "/d/?x={{ x }}"},
                "rows": [], "width": 400.0, "height": 300.0,
            }))
            .unwrap();
            let err = render(&payload, &text::FontBook::empty()).unwrap_err().to_string();
            assert!(!err.contains("chart-root link"), "{family}: {err}");
        }
    }
}
