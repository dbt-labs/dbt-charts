//! The variables band — transliteration of `render/variables_layout.py`
//! (`lay_out_variables`/`traits_of`/ornament sizing), `render/variables_
//! resolve.py` (`resolve_controls`/`resolve_option_values`), `render/
//! variable_input_refinement.py` (`refine_input_type_from_data`), and
//! `render/variables_strip.py` (`render_variables_strip_svg` and its draw
//! helpers).
//!
//! SEAM: this module has no executor. A query-backed `options.query` (or
//! bare `query:`) reads its rows from `BoardPayload.variable_datasets`
//! (built once by `board_sweep.py::_variable_datasets`, the same way chart
//! datasets are supplied) — a variable name absent from that map is treated
//! exactly like Python's own `executor=None` short-circuit (`resolve_option_
//! values` returns `[]`), which is also how the SIZING pass (no query
//! results available yet) is expected to behave.
//!
//! `Variable.enabled` is `crate::conditions::eval_bool_condition`, the same
//! law a layout item's `visible` reads. An unlabeled control's
//! inferred display name runs through `display_text::case::apply_case` — the same
//! law production's `text/case.py::apply_case` applies to both a chart
//! title's and a variable label's `FontStyle.case`, so this module owns no
//! second copy of it.

use std::collections::{HashMap, HashSet};

use d3rs::py_float::float_repr;
use serde::Deserialize;
use serde_json::Value;

use crate::board::{px, translate_group};
use display_text::case::apply_case;
use crate::conditions::eval_bool_condition;
use diagnostic::py_str;
use ::bind::time_locale::active_time_locale;
use crate::svg::num_full;
use print::{xml_escape, xml_escape_attr};

fn pxi(v: f64) -> String {
    num_full(px(v))
}

// ---------------------------------------------------------------------
// Deserialized theme/authoring shapes
// ---------------------------------------------------------------------

/// `compile/models/primitives.py::FontStyle`, the slice `_draw_control`/
/// `lay_out_variables` actually read (family/color/weight/size/case).
#[derive(Deserialize, Clone, Default)]
pub struct FontStyleDoc {
    pub family: Option<String>,
    pub color: Option<String>,
    pub size: Option<f64>,
    pub weight: Option<Value>,
    pub case: Option<String>,
}

#[derive(Deserialize, Clone)]
struct LabelValuePlaceholderDoc {
    font: FontStyleDoc,
}

#[derive(Deserialize, Clone)]
struct InputWidthsDoc {
    text: f64,
    number: f64,
    range: f64,
    slider_value_min: f64,
    checkbox: f64,
    daterange: f64,
}

#[derive(Deserialize, Clone)]
struct RangeDefaultsDoc {
    default_min: f64,
    default_max: f64,
    default_step: f64,
}

// `primitives.py::SpacingValues`'s `top`/`bottom` are unread here — only
// `_input_width`/`_field_rect`'s own `.left`/`.right` reach the strip.
#[derive(Deserialize, Clone, Copy, Default)]
struct SpacingValuesDoc {
    right: f64,
    left: f64,
}

#[derive(Deserialize, Clone, Copy)]
struct CornerStyleDoc {
    radius: f64,
}

#[derive(Deserialize, Clone)]
struct InputStyleDoc {
    height: f64,
    border: CornerStyleDoc,
    background: String,
    /// A theme's solid fill for a button row's selected segment; absent draws a tint.
    #[serde(default)]
    selected_background: Option<String>,
    selected_color: String,
    selected_tint: f64,
    selected_weight: Value,
    padding: SpacingValuesDoc,
    widths: InputWidthsDoc,
    range: RangeDefaultsDoc,
}

#[derive(Deserialize, Clone)]
struct PanelBorderDoc {
    color: String,
}

/// `compile/models/style/theme/variables.py::VariablesStyle`.
#[derive(Deserialize, Clone)]
pub struct VariablesStyleDoc {
    pub position: String,
    title_inline_band_bottom_pad: f64,
    gap: f64,
    title_inline_title_max_width: f64,
    font: FontStyleDoc,
    label: LabelValuePlaceholderDoc,
    value: LabelValuePlaceholderDoc,
    placeholder: LabelValuePlaceholderDoc,
    container_height: f64,
    border: PanelBorderDoc,
    control_gap: f64,
    input: InputStyleDoc,
    presets: Vec<String>,
    presets_display: String,
    overflow: String,
}

impl VariablesStyleDoc {
    /// `style.variables.gap`: also the space above a nested board's band.
    pub fn gap(&self) -> f64 {
        self.gap
    }

    /// `variables_style.label.font.case` — the letter-case transform an
    /// unlabeled control's inferred display name runs through
    /// (`resolve_controls`'s own `inferred_display_name` call).
    pub fn label_font_case(&self) -> Option<&str> {
        self.label.font.case.as_deref()
    }

    /// `variables_style.font.color` — an inactive tab's own text fill
    /// (`layouts.py::render_tabs_layout`).
    pub fn font_color(&self) -> Option<&str> {
        self.font.color.as_deref()
    }
}

/// `compile/models/variable/authored.py::VariableOptions`.
#[derive(Deserialize, Clone, Default)]
struct VariableOptionsDoc {
    #[serde(rename = "static", default)]
    static_values: Option<Vec<Value>>,
    #[serde(default)]
    query: Option<String>,
}

/// `compile/models/variable/authored.py::Variable`, the render-relevant
/// slice. `query`/`options.query` are always plain strings by the time this
/// deserializes — `Variable.get_option_query`'s own docstring: inline
/// `AuthoredQuery` instances are promoted to synthetic query names at
/// compile time, before render ever sees this shape.
#[derive(Deserialize, Clone)]
pub struct VariableDoc {
    #[serde(default = "default_input")]
    pub input: String,
    #[serde(default)]
    pub input_auto_detected: bool,
    #[serde(default)]
    pub label: Option<String>,
    #[serde(default)]
    pub default: Option<Value>,
    #[serde(default)]
    pub placeholder: Option<String>,
    #[serde(default)]
    pub required: Option<bool>,
    pub visible: bool,
    #[serde(default)]
    pub enabled: Value,
    #[serde(default)]
    pub query: Option<String>,
    #[serde(default)]
    options: Option<VariableOptionsDoc>,
    #[serde(default)]
    min: Option<f64>,
    #[serde(default)]
    max: Option<f64>,
    #[serde(default)]
    step: Option<f64>,
    #[serde(default)]
    pub defined_in_other_file: bool,
    #[serde(default)]
    presets: Option<Vec<String>>,
}

fn default_input() -> String {
    "auto".to_string()
}

impl VariableDoc {
    /// Whether the options are written in the board (`options.static`), as
    /// opposed to read from a query the sizing pass cannot run.
    fn has_static_options(&self) -> bool {
        self.options
            .as_ref()
            .and_then(|o| o.static_values.as_ref())
            .is_some_and(|values| !values.is_empty())
    }

    /// `Variable.get_option_query`.
    fn option_query(&self) -> Option<&str> {
        if let Some(opts) = &self.options {
            if let Some(q) = &opts.query {
                return Some(q.as_str());
            }
        }
        self.query.as_deref()
    }
}

/// Reads a JSON object as an ordered `Vec<(String, T)>`, preserving document
/// order (`serde_json`'s `preserve_order` feature keeps a `Map`'s own
/// iteration order; a plain `HashMap<String, T>` would not) — `resolve_
/// controls`' own contract is document order, since a host binds the i-th
/// control to the i-th box.
pub fn deserialize_ordered<'de, D, T>(deserializer: D) -> Result<Vec<(String, T)>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: serde::de::DeserializeOwned,
{
    let map: serde_json::Map<String, Value> = serde::Deserialize::deserialize(deserializer)?;
    map.into_iter()
        .map(|(k, v)| {
            serde_json::from_value::<T>(v)
                .map(|t| (k, t))
                .map_err(serde::de::Error::custom)
        })
        .collect()
}

// ---------------------------------------------------------------------
// variable_input_refinement.py
// ---------------------------------------------------------------------

struct RefinedType {
    input_type: String,
    slider_min: Option<f64>,
    slider_max: Option<f64>,
    slider_step: Option<f64>,
}

/// `variable_input_refinement.py::refine_input_type_from_data`; the data-aware law is
/// `board_model::variable::refine_options`.
fn refine_input_type_from_data(
    current_input: &str,
    input_auto_detected: bool,
    option_values: &[String],
) -> RefinedType {
    let refined = input_auto_detected
        .then(|| ::board_model::variable::refine_options(option_values))
        .flatten();
    let slider = refined.and_then(|r| r.slider);
    RefinedType {
        input_type: refined.map_or(current_input, |r| r.input.as_str()).to_string(),
        slider_min: slider.map(|s| s.0),
        slider_max: slider.map(|s| s.1),
        slider_step: slider.map(|s| s.2),
    }
}

/// A full `YYYY-MM-DD` date, the one shape a control variable carries.
fn full_iso_date(s: &str) -> Option<(i32, u32, u32)> {
    if s.len() != 10 {
        return None;
    }
    ::bind::time_axis::parse_iso_date(s).map(|(y, m, d)| (y, m.into(), d.into()))
}

#[cfg(test)]
mod refinement_tests {
    use super::*;

    #[test]
    fn slider_refines_from_integer_options() {
        let opts = vec!["10".to_string(), "20".to_string(), "15".to_string()];
        let r = refine_input_type_from_data("select", true, &opts);
        assert_eq!(r.input_type, "slider");
        assert_eq!(r.slider_min, Some(10.0));
        assert_eq!(r.slider_max, Some(20.0));
        assert_eq!(r.slider_step, Some(1.0));
    }

    #[test]
    fn checkbox_refines_from_boolean_pair() {
        let opts = vec!["true".to_string(), "false".to_string()];
        let r = refine_input_type_from_data("select", true, &opts);
        assert_eq!(r.input_type, "checkbox");
    }

    #[test]
    fn datepicker_refines_from_iso_dates() {
        let opts = vec!["2024-01-01".to_string(), "2024-02-01".to_string()];
        let r = refine_input_type_from_data("select", true, &opts);
        assert_eq!(r.input_type, "datepicker");
    }

    #[test]
    fn explicit_author_type_is_never_refined() {
        let opts = vec!["10".to_string(), "20".to_string()];
        let r = refine_input_type_from_data("select", false, &opts);
        assert_eq!(r.input_type, "select");
    }
}

// ---------------------------------------------------------------------
// variables_layout.py
// ---------------------------------------------------------------------

pub const WRAP_EPSILON: f64 = 0.5;

/// `variables_layout.py::MIN_CONTROLS_FOR_RESET`.
const MIN_CONTROLS_FOR_RESET: usize = 2;

struct InputTraits {
    sizing: &'static str,
    ornament: &'static str,
    unset: &'static str,
    /// What the buttons form draws as buttons: `options`, `presets`, or `none`.
    segments: &'static str,
}

/// `variables_layout.py::_INPUT_TRAITS` / `traits_of` — a complete mapping
/// over `VariableInputType`; an unrecognized string refuses rather than
/// silently sizing as text with no ornament.
fn traits_of(input_type: &str) -> Result<InputTraits, String> {
    Ok(match input_type {
        "auto" | "input" | "text" | "textarea" => InputTraits {
            sizing: "text",
            ornament: "none",
            unset: "none",
            segments: "none",
        },
        "select" => InputTraits {
            sizing: "content",
            ornament: "arrow",
            unset: "chooser",
            segments: "none",
        },
        "radio" => InputTraits {
            sizing: "content",
            ornament: "arrow",
            unset: "chooser",
            segments: "options",
        },
        "multiselect" => InputTraits {
            sizing: "summary",
            ornament: "arrow",
            unset: "chooser",
            segments: "none",
        },
        "date" | "datepicker" => InputTraits {
            sizing: "content",
            ornament: "calendar",
            unset: "date",
            segments: "none",
        },
        "daterange" => InputTraits {
            sizing: "daterange",
            ornament: "calendar",
            unset: "range",
            segments: "presets",
        },
        "number" => InputTraits {
            sizing: "number",
            ornament: "none",
            unset: "none",
            segments: "none",
        },
        "slider" | "range" => InputTraits {
            sizing: "slider",
            ornament: "none",
            unset: "none",
            segments: "none",
        },
        "checkbox" => InputTraits {
            sizing: "checkbox",
            ornament: "none",
            unset: "none",
            segments: "none",
        },
        other => return Err(format!("board variable input type not modeled: {other}")),
    })
}

#[derive(Clone)]
pub struct ControlSpec {
    pub name: String,
    pub input: String,
    pub label: String,
    pub value: String,
    /// The button row's segment texts; empty for a control drawn as a field.
    pub segment_labels: Vec<String>,
    /// Whether the buttons form draws the control's label (a timespan button row does not).
    pub segments_labeled: bool,
}

#[derive(Clone)]
pub struct ControlBox {
    pub name: String,
    pub input: String,
    pub label: String,
    pub value: String,
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub label_width: f64,
    pub labeled: bool,
    /// Each row's segment widths; empty means the control is a field.
    pub segment_rows: Vec<Vec<f64>>,
    /// A button row wider than its slot that fell back to the field.
    pub overflowed: bool,
    pub value_width: f64,
    /// The width the buttons want on one row, whether or not they got it.
    pub buttons_width: f64,
}

/// `variables_layout.py::ResetBox`.
#[derive(Clone)]
pub struct ResetBox {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

pub struct VariablesLayout {
    pub boxes: Vec<ControlBox>,
    pub height: f64,
    pub rows: i64,
    pub reset: Option<ResetBox>,
}

impl VariablesLayout {
    /// Controls plus the reset item, as `(y, right edge)`.
    fn placed_extents(&self) -> impl Iterator<Item = (f64, f64)> + '_ {
        let reset = self.reset.iter().map(|r| (r.y, r.x + r.width));
        self.boxes.iter().map(|b| (b.y, b.x + b.width)).chain(reset)
    }
}

/// `variables_layout.py::ornament_width`.
fn ornament_width(input_type: &str, font_size: f64) -> Result<f64, String> {
    Ok(match traits_of(input_type)?.ornament {
        "arrow" => font_size,
        "calendar" => font_size * 0.85,
        _ => 0.0,
    })
}

/// `variables_layout.py::ornament_gap`.
fn ornament_gap(input_type: &str, font_size: f64) -> Result<f64, String> {
    Ok(if ornament_width(input_type, font_size)? > 0.0 {
        font_size * 0.5
    } else {
        0.0
    })
}

/// `variables_layout.py::_text_width` — always measured against Inter
/// Variable (`get_font_measurer()`'s own no-family default,
/// `fonts.py::get_font_path(None)`), independent of the board's own theme
/// family: a layout-width measurer, not a paint-time one.
fn text_width(text: &str, font_size: f64, fonts_dir: &str) -> f64 {
    if text.is_empty() {
        return 0.0;
    }
    let path = format!("{fonts_dir}/InterVariable.ttf");
    let measurer = measure::fonts::cached_measurer(&path, 0, None);
    measurer.measure(text, font_size)
}

/// `fonts.py::get_font_path`'s own family resolution, restricted to the
/// two-way split it actually makes: any `Source Serif` spelling gets the
/// vendored Source Serif 4 cut, everything else (including a family we do
/// not vendor at all) falls back to Inter Variable.
fn measure_font_path(fonts_dir: &str, family: &str) -> String {
    let primary = family
        .split(',')
        .next()
        .unwrap_or("")
        .trim()
        .trim_matches(|c| c == '\'' || c == '"');
    if primary.starts_with("Source Serif") {
        format!("{fonts_dir}/SourceSerif4Variable.ttf")
    } else {
        format!("{fonts_dir}/InterVariable.ttf")
    }
}

/// `font_measure.py::centered_baseline_offset`.
fn centered_baseline_offset(family: &str, font_size: f64, fonts_dir: &str) -> f64 {
    let path = measure_font_path(fonts_dir, family);
    measure::centered_baseline_offset(&measure::fonts::cached_measurer(&path, 0, None), font_size)
}

/// `variables_layout.py::_input_width`.
fn input_width(
    spec: &ControlSpec,
    vs: &VariablesStyleDoc,
    font_size: f64,
    fonts_dir: &str,
) -> Result<f64, String> {
    let widths = &vs.input.widths;
    let padding = &vs.input.padding;
    let sizing = traits_of(&spec.input)?.sizing;
    if sizing == "checkbox" {
        return Ok(widths.checkbox);
    }
    if sizing == "summary" {
        return Ok(widths.text);
    }
    if sizing == "slider" {
        return Ok(widths.range
            + vs.control_gap
            + text_width(&spec.value, font_size, fonts_dir).max(widths.slider_value_min));
    }
    let boxed =
        text_width(&spec.value, font_size, fonts_dir).max(font_size) + padding.left + padding.right;
    Ok(match sizing {
        "content" => boxed,
        "number" => widths.number.max(boxed),
        "daterange" => widths.daterange.max(boxed),
        _ => widths.text.max(boxed),
    })
}

/// `variables_layout.py::_wrap_segments`.
fn wrap_segments(widths: &[f64], room: f64) -> Vec<Vec<f64>> {
    let mut rows: Vec<Vec<f64>> = vec![Vec::new()];
    let mut used = 0.0_f64;
    for &width in widths {
        if !rows.last().expect("one row open").is_empty() && used + width > room + WRAP_EPSILON {
            rows.push(Vec::new());
            used = 0.0;
        }
        rows.last_mut().expect("one row open").push(width);
        used += width;
    }
    rows
}

/// `variables_layout.py::_drawn_value` — a `summary` field's text cut to its
/// text slot by the engine's truncation law.
fn drawn_value(
    spec: &ControlSpec,
    vs: &VariablesStyleDoc,
    font_size: f64,
    fonts_dir: &str,
) -> Result<String, String> {
    if traits_of(&spec.input)?.sizing != "summary" {
        return Ok(spec.value.clone());
    }
    let slot = vs.input.widths.text - vs.input.padding.left - vs.input.padding.right;
    crate::common::truncate_label_with(&spec.value, slot, |s| {
        Ok(text_width(s, font_size, fonts_dir))
    })
}

/// `variables_layout.py::lay_out_variables`.
pub fn lay_out_variables(
    specs: &[ControlSpec],
    width: f64,
    vs: &VariablesStyleDoc,
    fonts_dir: &str,
) -> Result<VariablesLayout, String> {
    if specs.is_empty() {
        return Ok(VariablesLayout {
            boxes: Vec::new(),
            height: 0.0,
            rows: 0,
            reset: None,
        });
    }
    let gap = vs.gap;
    let row_height = vs.container_height;
    let available = width.max(1.0);
    let font_size = vs
        .font
        .size
        .ok_or("style.variables.font.size must be configured")?;

    let padding = vs.input.padding.left + vs.input.padding.right;
    let mut boxes: Vec<ControlBox> = Vec::with_capacity(specs.len());
    let mut widths = Vec::with_capacity(specs.len() + 1);
    let mut spans = Vec::with_capacity(specs.len() + 1);
    for spec in specs {
        let segment_widths: Vec<f64> = spec
            .segment_labels
            .iter()
            .map(|text| text_width(text, font_size, fonts_dir) + padding)
            .collect();
        let field_label_width = text_width(&field_label(&spec.label)?, font_size, fonts_dir);
        let mut labeled = spec.segments_labeled;
        let mut label_width = if labeled { field_label_width } else { 0.0 };
        let lead = if labeled { label_width + vs.control_gap } else { 0.0 };
        let value_width = text_width(&spec.value, font_size, fonts_dir).max(font_size) + padding;
        let mut segment_rows: Vec<Vec<f64>> = Vec::new();
        let mut overflowed = false;
        let mut single_row = 0.0_f64;
        if !segment_widths.is_empty() {
            single_row = lead + segment_widths.iter().sum::<f64>().max(value_width);
            if single_row <= available + WRAP_EPSILON {
                segment_rows = vec![segment_widths.clone()];
            } else if vs.overflow == "wrap" {
                segment_rows = wrap_segments(&segment_widths, (available - lead).max(1.0));
            } else {
                overflowed = true;
            }
        }
        let button_rows = segment_rows.len().max(1);
        let (control_width, box_height) = if !segment_rows.is_empty() {
            let widest = segment_rows
                .iter()
                .map(|r| r.iter().sum::<f64>())
                .fold(0.0_f64, f64::max);
            (
                lead + widest.max(value_width),
                button_rows as f64 * row_height + (button_rows - 1) as f64 * gap,
            )
        } else {
            label_width = field_label_width;
            labeled = true;
            (
                label_width
                    + vs.control_gap
                    + input_width(spec, vs, font_size, fonts_dir)?
                    + ornament_gap(&spec.input, font_size)?
                    + ornament_width(&spec.input, font_size)?,
                row_height,
            )
        };
        boxes.push(ControlBox {
            name: spec.name.clone(),
            input: spec.input.clone(),
            label: spec.label.clone(),
            value: drawn_value(spec, vs, font_size, fonts_dir)?,
            x: 0.0,
            y: 0.0,
            width: control_width,
            height: box_height,
            label_width,
            labeled,
            segment_rows,
            overflowed,
            value_width,
            buttons_width: single_row,
        });
        widths.push(control_width);
        spans.push(button_rows as i64);
    }
    if specs.len() >= MIN_CONTROLS_FOR_RESET {
        widths.push(
            text_width(&word("controls.reset")?, font_size, fonts_dir)
                + vs.input.padding.left
                + vs.input.padding.right,
        );
        spans.push(1);
    }
    let places = flow(&widths, &spans, gap, balanced_limit(&widths, &spans, gap, available));
    for (b, &(x, row)) in boxes.iter_mut().zip(&places) {
        b.x = x;
        b.y = row as f64 * (row_height + gap);
    }
    let (last_x, last_row) = places[places.len() - 1];
    let reset = (widths.len() > specs.len()).then(|| ResetBox {
        x: last_x,
        y: last_row as f64 * (row_height + gap),
        width: widths[widths.len() - 1],
        height: row_height,
    });
    let rows = last_row + spans[spans.len() - 1];
    Ok(VariablesLayout {
        boxes,
        height: rows as f64 * row_height + (rows - 1) as f64 * gap,
        rows,
        reset,
    })
}

/// `variables_layout.py::_flow`: each item's `(x, row)`, rows no wider than `limit`. The first
/// item of a row is placed wherever it lands: an overrun is visible, a dropped filter is not. An
/// item spanning several rows (wrapped buttons) owns them: whatever follows starts below it.
fn flow(widths: &[f64], spans: &[i64], gap: f64, limit: f64) -> Vec<(f64, i64)> {
    let mut places = Vec::with_capacity(widths.len());
    let (mut x, mut row) = (0.0_f64, 0_i64);
    for (&w, &span) in widths.iter().zip(spans) {
        if !places.is_empty() && x + w > limit + WRAP_EPSILON {
            row += 1;
            x = 0.0;
        }
        places.push((x, row));
        x += w + gap;
        if span > 1 {
            row += span - 1;
            x = limit + gap;
        }
    }
    places
}

/// `variables_layout.py::_balanced_limit`: the narrowest run width that packs into as few rows
/// as `available` does.
fn balanced_limit(widths: &[f64], spans: &[i64], gap: f64, available: f64) -> f64 {
    let rows_at =
        |limit: f64| flow(widths, spans, gap, limit).last().map_or(0, |p| p.1) + spans[spans.len() - 1];
    let rows = rows_at(available);
    if rows == 1 {
        return available;
    }
    let mut runs = Vec::new();
    for start in 0..widths.len() {
        let mut x = 0.0_f64;
        for &w in &widths[start..] {
            if x + w > available + WRAP_EPSILON {
                break;
            }
            runs.push(x + w);
            x += w + gap;
        }
    }
    runs.sort_by(f64::total_cmp);
    runs.into_iter()
        .find(|&limit| rows_at(limit) == rows)
        .unwrap_or(available)
}

#[cfg(test)]
mod layout_tests {
    use super::*;

    fn multiselect(value: &str) -> ControlSpec {
        ControlSpec {
            name: "r".into(),
            input: "multiselect".into(),
            label: "Region".into(),
            value: value.into(),
            segment_labels: Vec::new(),
            segments_labeled: true,
        }
    }

    #[test]
    fn a_multiselect_holds_one_width_whatever_is_selected() {
        let vs = fixture_style();
        let widths: Vec<f64> = ["All", "North", "(5) Latin America and the Caribbean"]
            .iter()
            .map(|v| {
                lay_out_variables(&[multiselect(v)], 2000.0, &vs, &fonts_dir()).unwrap().boxes[0]
                    .width
            })
            .collect();
        assert!(widths.iter().all(|w| *w == widths[0]), "{widths:?}");
    }

    #[test]
    fn a_multiselect_value_too_long_for_its_field_ends_in_an_ellipsis() {
        let vs = fixture_style();
        let long = "Latin America and the Caribbean, Sub-Saharan Africa";
        let layout = lay_out_variables(&[multiselect(long)], 2000.0, &vs, &fonts_dir()).unwrap();
        let value = &layout.boxes[0].value;
        let slot = vs.input.widths.text - vs.input.padding.left - vs.input.padding.right;
        assert!(value.ends_with('\u{2026}'), "{value}");
        assert!(long.starts_with(value.trim_end_matches('\u{2026}')), "{value}");
        assert!(text_width(value, 11.0, &fonts_dir()) <= slot, "{value}");
    }

    #[test]
    fn a_multiselect_value_that_fits_is_drawn_whole() {
        let vs = fixture_style();
        let layout =
            lay_out_variables(&[multiselect("(2) North")], 2000.0, &vs, &fonts_dir()).unwrap();
        assert_eq!(layout.boxes[0].value, "(2) North");
    }

    fn fixture_style() -> VariablesStyleDoc {
        serde_json::from_value(serde_json::json!({
            "position": "top",
            "title_inline_band_bottom_pad": 8.0,
            "gap": 10.0,
            "title_inline_title_max_width": 0.0,
            "font": {"size": 11.0, "family": "Inter", "color": "#000", "weight": "400", "case": "none"},
            "label": {"font": {"size": 11.0, "family": "Inter", "color": "#000", "weight": "400", "case": "none"}},
            "value": {"font": {"size": 11.0, "family": "Inter", "color": "#000", "weight": "400", "case": "none"}},
            "placeholder": {"font": {"size": 11.0, "family": "Inter", "color": "#999", "weight": "400", "case": "none"}},
            "container_height": 32.0,
            "border": {"color": "#ccc"},
            "control_gap": 8.0,
            "presets": ["today", "last_5_days", "year_to_date"],
            "presets_display": "menu",
            "overflow": "menu",
            "input": {
                "height": 28.0,
                "border": {"radius": 4.0},
                "background": "#fff",
                "selected_color": "#111", "selected_tint": 0.12, "selected_weight": 500,
                "padding": {"top": 6.0, "right": 10.0, "bottom": 6.0, "left": 10.0},
                "widths": {"text": 140.0, "number": 100.0, "range": 120.0, "slider_value_min": 30.0, "checkbox": 16.0, "daterange": 160.0},
                "range": {"default_min": 0.0, "default_max": 100.0, "default_step": 1.0},
            },
        }))
        .expect("fixture style deserializes")
    }

    fn fonts_dir() -> String {
        concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        )
        .to_string()
    }

    #[test]
    fn empty_specs_lay_out_to_zero_height() {
        let vs = fixture_style();
        let layout = lay_out_variables(&[], 400.0, &vs, &fonts_dir()).unwrap();
        assert_eq!(layout.rows, 0);
        assert_eq!(layout.height, 0.0);
        assert!(layout.boxes.is_empty());
    }

    #[test]
    fn a_wrapped_strip_balances_its_rows() {
        let vs = fixture_style();
        let specs: Vec<ControlSpec> = (0..5)
            .map(|i| ControlSpec {
                name: format!("v{i}"),
                input: "text".into(),
                label: format!("Var {i}"),
                value: String::new(),
                segment_labels: Vec::new(),
                segments_labeled: true,
            })
            .collect();
        let one_row = lay_out_variables(&specs, 10_000.0, &vs, &fonts_dir()).unwrap();
        let reset = one_row.reset.expect("five controls reserve a reset");
        let layout =
            lay_out_variables(&specs, reset.x + reset.width - 1.0, &vs, &fonts_dir()).unwrap();
        assert_eq!(layout.rows, 2);
        assert_eq!(layout.boxes.iter().filter(|b| b.y == 0.0).count(), 3);
        assert!(layout.reset.expect("still reserved").y > 0.0);
    }

    #[test]
    fn a_wide_control_wraps_to_a_second_row() {
        let vs = fixture_style();
        let specs = vec![
            ControlSpec {
                name: "a".into(),
                input: "text".into(),
                label: "A".into(),
                value: "value".into(),
                segment_labels: Vec::new(),
                segments_labeled: true,
            },
            ControlSpec {
                name: "b".into(),
                input: "text".into(),
                label: "B".into(),
                value: "value".into(),
                segment_labels: Vec::new(),
                segments_labeled: true,
            },
        ];
        // A narrow strip forces the second control, then the reset, onto their own rows.
        let layout = lay_out_variables(&specs, 50.0, &vs, &fonts_dir()).unwrap();
        assert_eq!(layout.rows, 3);
        assert_eq!(layout.boxes[0].y, 0.0);
        assert_eq!(layout.boxes[1].y, vs.container_height + vs.gap);
        let reset = layout.reset.expect("two controls reserve a reset");
        assert_eq!(reset.x, 0.0);
        assert_eq!(reset.y, 2.0 * (vs.container_height + vs.gap));
    }

    #[test]
    fn a_lone_control_reserves_no_reset() {
        let vs = fixture_style();
        let specs = vec![ControlSpec {
            name: "a".into(),
            input: "text".into(),
            label: "A".into(),
            value: "".into(),
            segment_labels: Vec::new(),
            segments_labeled: true,
        }];
        let layout = lay_out_variables(&specs, 400.0, &vs, &fonts_dir()).unwrap();
        assert!(layout.reset.is_none());
    }

    fn assert_close(actual: f64, expected: f64) {
        assert!((actual - expected).abs() < 1e-9, "{actual} != {expected}");
    }

    fn title_column(title: &str, region: &str) -> f64 {
        let (style, fonts) = crate::title::title_style_and_fonts();
        let control: VariableDoc =
            serde_json::from_value(serde_json::json!({"input": "select", "visible": true}))
                .unwrap();
        let values: serde_json::Map<String, Value> =
            serde_json::from_value(serde_json::json!({"region": region})).unwrap();
        let (title_w, _vars_w) = resolve_title_variables_inline_widths(
            900.0,
            &style,
            &fonts,
            Some("title"),
            &[("region", &control)],
            title,
            &values,
            &fixture_style(),
        )
        .unwrap();
        title_w
    }

    // Expected widths are production's `markdown_style.title_max_content_width`
    // on the same titles (default theme, level 1, `style.title.font.case:
    // title`) plus the band's 8px pad, from the `ladder/variables` 09 and 10
    // rungs.
    #[test]
    fn the_title_column_is_the_width_mdsvg_asks_for_not_a_shaped_width() {
        assert_close(title_column("AVATAR To Wave Typography", "APAC"), 345.37 + 8.0);
    }

    #[test]
    fn the_title_column_measures_the_resolved_jinja_title() {
        assert_close(title_column("Revenue in {{ region }}", "APAC"), 199.96 + 8.0);
        let eu = title_column("Revenue in {{ region }}", "EU");
        assert!(eu < title_column("Revenue in {{ region }}", "APAC"));
    }
}

// ---------------------------------------------------------------------
// variables_resolve.py
// ---------------------------------------------------------------------

/// `variables_resolve.py::Segment`: one button of a row.
pub struct Segment {
    pub value: String,
    pub label: String,
    pub selected: bool,
    /// None when the button does nothing: the selected button of a required
    /// variable, or any button of a disabled control.
    pub href: Option<String>,
}

pub struct ResolvedControl {
    pub name: String,
    pub input: String,
    pub label: String,
    pub display_value: String,
    pub current: Value,
    pub option_values: Vec<String>,
    pub checked: bool,
    pub enabled: bool,
    pub slider_min: f64,
    pub slider_max: f64,
    pub slider_step: f64,
    pub can_unset: bool,
    /// `var_def.placeholder`, raw — `read_only_unset_label`'s own source,
    /// unconditional on input type or the current value.
    pub placeholder: Option<String>,
    /// `resolve_controls`'s own `ResolvedControl.placeholder` (NOT this
    /// struct's `placeholder` above, which is `var_def.placeholder` raw):
    /// the authored hint, only Some on a `FREE_ENTRY_INPUTS` control whose
    /// current value is empty — the one fact that decides whether
    /// `_draw_text_field` shows it in the muted placeholder color.
    pub hint: Option<String>,
    /// `var_def.min`/`.max`/`.step`, raw — `_draw_control`'s own `number`
    /// input bounds, distinct from `slider_min`/`slider_max`/`slider_step`
    /// above (those are always-defaulted; these are None unless authored).
    pub authored_min: Option<f64>,
    pub authored_max: Option<f64>,
    pub authored_step: Option<f64>,
    pub defined_in_other_file: bool,
    /// The buttons of the buttons form; empty when the control draws as a field.
    pub segments: Vec<Segment>,
    /// The preset a daterange commits; `current` then holds its dates.
    pub preset: Option<String>,
}

impl ResolvedControl {
    fn spec(&self) -> ControlSpec {
        ControlSpec {
            name: self.name.clone(),
            input: self.input.clone(),
            label: self.label.clone(),
            value: self.display_value.clone(),
            segment_labels: self.segments.iter().map(|s| s.label.clone()).collect(),
            segments_labeled: self.input != "daterange",
        }
    }
}

/// A daterange preset name parsed by `compile/template/daterange.py`'s grammar,
/// holding what the button row's labels read.
struct Preset {
    kind: &'static str,
    unit: &'static str,
    n: u64,
    fiscal: bool,
}

const PRESET_UNITS: [&str; 5] = ["day", "week", "month", "quarter", "year"];

fn preset_unit(text: &str) -> Option<&'static str> {
    PRESET_UNITS.iter().copied().find(|unit| *unit == text)
}

/// `parse_daterange_preset` for the shapes a label needs.
fn parse_preset(name: &str) -> Result<Preset, String> {
    let bad = || format!("`{name}` is not a daterange preset");
    match name {
        "today" => return Ok(Preset { kind: "today", unit: "day", n: 1, fiscal: false }),
        "yesterday" => return Ok(Preset { kind: "yesterday", unit: "day", n: 1, fiscal: false }),
        _ => {}
    }
    for kind in ["before_previous", "previous", "last"] {
        if let Some(rest) = name.strip_prefix(&format!("{kind}_")) {
            let (n, rest) = rest.split_once('_').ok_or_else(bad)?;
            if n.starts_with('0') || n.is_empty() || !n.bytes().all(|b| b.is_ascii_digit()) {
                return Err(bad());
            }
            let n: u64 = n.parse().map_err(|_| bad())?;
            let (fiscal, unit) = match rest.strip_prefix("fiscal_") {
                Some(unit) => (true, unit),
                None => (false, rest),
            };
            let unit = preset_unit(unit.strip_suffix('s').unwrap_or(unit)).ok_or_else(bad)?;
            if fiscal && !matches!(unit, "quarter" | "year") {
                return Err(bad());
            }
            return Ok(Preset { kind, unit, n, fiscal });
        }
    }
    let (kind, rest) = if let Some(rest) = name.strip_prefix("this_") {
        ("this", rest)
    } else if let Some(rest) = name.strip_suffix("_to_date") {
        ("to_date", rest)
    } else {
        return Err(bad());
    };
    let (fiscal, unit) = match rest.strip_prefix("fiscal_") {
        Some(unit) => (true, unit),
        None => (false, rest),
    };
    let unit = preset_unit(unit).ok_or_else(bad)?;
    if fiscal && !matches!(unit, "quarter" | "year") {
        return Err(bad());
    }
    Ok(Preset { kind, unit, n: 1, fiscal })
}

/// `controls.py::preset_label`.
fn preset_label(preset: &Preset) -> Result<String, String> {
    let unit = if preset.fiscal {
        format!("fiscal_{}", preset.unit)
    } else {
        preset.unit.to_string()
    };
    match preset.kind {
        "today" | "yesterday" => word(&format!("controls.{}", preset.kind)),
        "this" => word(&format!("controls.this_{unit}")),
        "to_date" => word(&format!("controls.{unit}_to_date")),
        kind => {
            let duration =
                display_text::language::tr(&format!("duration.{unit}"), Some(preset.n), &[])?;
            display_text::language::tr(
                &format!("controls.preset_{kind}"),
                None,
                &[("duration", duration.as_str())],
            )
        }
    }
}

/// `controls.py::compact_preset_label`: ticker notation (1D 5D 3M YTD 12M), and
/// the long label for a form with no such spelling.
fn compact_preset_label(preset: &Preset) -> Result<String, String> {
    if preset.fiscal {
        return preset_label(preset);
    }
    let letter = match preset.unit {
        "day" => "D",
        "week" => "W",
        "month" => "M",
        "quarter" => "Q",
        _ => "Y",
    };
    match preset.kind {
        "today" => Ok("1D".to_string()),
        "last" if preset.n > 1 || preset.unit == "day" => Ok(format!("{}{letter}", preset.n)),
        // `last_1_<unit>` is calendar-aligned, so its dates are the unit to date.
        "last" | "to_date" => Ok(format!("{letter}TD")),
        _ => preset_label(preset),
    }
}

/// `board_variables.py::variable_link`: a `?` link that sets one variable and
/// keeps every other as it is, never host-injected `context` variables; a
/// preset daterange links its preset id. An empty `value` unsets `name`.
pub fn variable_link(
    values: &serde_json::Map<String, Value>,
    presets: &serde_json::Map<String, Value>,
    context: &[String],
    name: &str,
    value: &str,
) -> Result<String, String> {
    let mut params: Vec<(String, String)> = Vec::new();
    for (key, current) in values {
        if current.is_null() || context.contains(key) {
            continue;
        }
        let text = match presets.get(key) {
            Some(preset) => py_str(preset),
            None => committed_value(current)?,
        };
        params.push((key.clone(), text));
    }
    match params.iter_mut().find(|(key, _)| key == name) {
        Some(param) => param.1 = value.to_string(),
        None => params.push((name.to_string(), value.to_string())),
    }
    let pairs: Vec<String> = params
        .iter()
        .map(|(key, value)| {
            format!("{}={}", crate::href::quote_plus(key), crate::href::quote_plus(value))
        })
        .collect();
    Ok(format!("?{}", pairs.join("&")))
}

/// `variables_resolve.py::_segments`.
#[allow(clippy::too_many_arguments)]
fn build_segments(
    name: &str,
    input_type: &str,
    option_values: &[String],
    presets: &[String],
    current: &Value,
    can_unset: bool,
    linkable: bool,
    current_values: &serde_json::Map<String, Value>,
    committed_presets: &serde_json::Map<String, Value>,
    context_variables: &[String],
    show_presets: bool,
    static_options: bool,
) -> Result<Vec<Segment>, String> {
    let (entries, chosen): (Vec<(String, String)>, Option<String>) =
        match traits_of(input_type)?.segments {
            "options" if !static_options => return Ok(Vec::new()),
            "options" => (
                option_values.iter().map(|v| (v.clone(), v.clone())).collect(),
                (!value_is_unset(current)).then(|| py_str(current)),
            ),
            "presets" if show_presets => {
                let mut entries = Vec::new();
                for preset in presets {
                    entries.push((preset.clone(), compact_preset_label(&parse_preset(preset)?)?));
                }
                let chosen = committed_presets.get(name).map(py_str);
                (entries, chosen)
            }
            _ => return Ok(Vec::new()),
        };
    let mut segments = Vec::with_capacity(entries.len());
    for (value, label) in entries {
        let selected = chosen.as_deref() == Some(value.as_str());
        let href = if !linkable || (selected && !can_unset) {
            None
        } else {
            Some(variable_link(
                current_values,
                committed_presets,
                context_variables,
                name,
                if selected { "" } else { &value },
            )?)
        };
        segments.push(Segment { value, label, selected, href });
    }
    Ok(segments)
}

fn field_label(label: &str) -> Result<String, String> {
    display_text::language::tr("controls.field_label", None, &[("label", label)])
}

fn word(id: &str) -> Result<String, String> {
    display_text::language::tr(id, None, &[])
}

fn dated(id: &str, date: &str) -> Result<String, String> {
    display_text::language::tr(id, None, &[("date", date)])
}

/// `variables_resolve.py::resolve_query_options`'s column-priority
/// extraction (`value` > `label` > `name` > `id` > the row's first key).
fn extract_option_values(rows: &[serde_json::Map<String, Value>]) -> Vec<String> {
    let Some(first_row) = rows.first() else {
        return Vec::new();
    };
    let value_key = ["value", "label", "name", "id"]
        .iter()
        .find(|k| first_row.contains_key(**k))
        .map(|k| k.to_string())
        .or_else(|| first_row.keys().next().cloned());
    let Some(value_key) = value_key else {
        return Vec::new();
    };
    rows.iter()
        .filter_map(|row| row.get(&value_key))
        .filter(|v| !v.is_null())
        .map(py_str)
        .collect()
}

fn dedupe(values: Vec<String>) -> Vec<String> {
    let mut seen = HashSet::new();
    values
        .into_iter()
        .filter(|v| seen.insert(v.clone()))
        .collect()
}

/// `variables_resolve.py::resolve_option_values`. `datasets` is `None` for
/// the SIZING pass (mirroring `executor=None`), `Some` for render.
fn resolve_option_values(
    var_def: &VariableDoc,
    name: &str,
    datasets: Option<&HashMap<String, Vec<serde_json::Map<String, Value>>>>,
) -> Vec<String> {
    if let Some(opts) = &var_def.options {
        if let Some(static_values) = &opts.static_values {
            return dedupe(static_values.iter().map(py_str).collect());
        }
    }
    if var_def.option_query().is_none() {
        return Vec::new();
    }
    let Some(datasets) = datasets else {
        return Vec::new();
    };
    let rows = datasets.get(name).map(Vec::as_slice).unwrap_or(&[]);
    dedupe(extract_option_values(rows))
}

/// `variables_resolve.py::_slider_bounds`.
fn slider_bounds(
    var_def: &VariableDoc,
    refined: &RefinedType,
    vs: &VariablesStyleDoc,
) -> (f64, f64, f64) {
    if let Some(min) = refined.slider_min {
        return (
            min,
            refined
                .slider_max
                .expect("slider_max set alongside slider_min"),
            refined
                .slider_step
                .expect("slider_step set alongside slider_min"),
        );
    }
    (
        var_def.min.unwrap_or(vs.input.range.default_min),
        var_def.max.unwrap_or(vs.input.range.default_max),
        var_def.step.unwrap_or(vs.input.range.default_step),
    )
}

fn checked_from_current(current: &Value) -> bool {
    match current {
        Value::String(s) => matches!(s.trim().to_lowercase().as_str(), "true" | "yes" | "1"),
        Value::Bool(b) => *b,
        Value::Null => false,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `compile/template/variables.py::coerce_multiselect`. `UNSET_MULTISELECT`
/// (an empty tuple) is folded into the `Value::Null` arm — the caller
/// substitutes it for `None` before calling in Python; here `None` maps
/// straight to the same empty result.
fn coerce_multiselect(value: &Value) -> Vec<Value> {
    match value {
        Value::Null => Vec::new(),
        Value::Array(a) => a.clone(),
        Value::String(s) if s.trim().is_empty() => Vec::new(),
        other => vec![other.clone()],
    }
}

fn read_only_unset_label(placeholder: Option<&str>, default_id: &str) -> Result<String, String> {
    match placeholder {
        Some(p) if !p.is_empty() => Ok(p.to_string()),
        _ => word(default_id),
    }
}

fn value_is_unset(v: &Value) -> bool {
    matches!(v, Value::Null) || matches!(v, Value::String(s) if s.is_empty())
}

/// `variables_resolve.py::FREE_ENTRY_INPUTS`: the inputs a user types free
/// text into — they show a placeholder while empty, and `number` carries
/// authored bounds.
fn is_free_entry_input(input: &str) -> bool {
    matches!(input, "text" | "input" | "textarea" | "number")
}

/// `compile/template/variables.py::variable_value_is_absent`.
fn value_is_absent(v: &Value) -> bool {
    match v {
        Value::Null => true,
        Value::String(s) => s.trim().is_empty(),
        Value::Array(a) => a.is_empty(),
        _ => false,
    }
}

fn daterange_is_unset(v: &Value) -> bool {
    if value_is_absent(v) {
        return true;
    }
    let Value::Array(a) = v else { return false };
    if a.len() != 2 {
        return false;
    }
    value_is_unset(&a[0]) && value_is_unset(&a[1])
}

/// `variables_resolve.py::_month`: the board time locale's short month name.
fn month((_, m, _): (i32, u32, u32)) -> Result<String, String> {
    Ok(active_time_locale()?.short_months[(m - 1) as usize].clone())
}

fn fmt(date: (i32, u32, u32)) -> Result<String, String> {
    display_text::language::tr(
        "controls.date",
        None,
        &[("day", &date.2.to_string()), ("month", &month(date)?), ("year", &date.0.to_string())],
    )
}

fn fmt_no_year(date: (i32, u32, u32)) -> Result<String, String> {
    display_text::language::tr(
        "controls.date_no_year",
        None,
        &[("day", &date.2.to_string()), ("month", &month(date)?)],
    )
}

fn range(start: &str, end: &str) -> Result<String, String> {
    display_text::language::tr("controls.range", None, &[("start", start), ("end", end)])
}

/// `variables_resolve.py::format_date_label`.
fn format_date_label(iso: &str) -> Result<String, String> {
    full_iso_date(iso).map_or_else(|| Ok(iso.to_string()), fmt)
}

/// `variables_resolve.py::format_daterange_label`.
fn format_daterange_label(start_iso: &str, end_iso: &str) -> Result<String, String> {
    let (Some(s), Some(e)) = (full_iso_date(start_iso), full_iso_date(end_iso)) else {
        return range(start_iso, end_iso);
    };
    if s == e {
        return fmt(s);
    }
    if s.0 == e.0 && s.1 == e.1 {
        return display_text::language::tr(
            "controls.range_same_month",
            None,
            &[
                ("start_day", &s.2.to_string()),
                ("end_day", &e.2.to_string()),
                ("month", &month(s)?),
                ("year", &s.0.to_string()),
            ],
        );
    }
    if s.0 == e.0 {
        return range(&fmt_no_year(s)?, &fmt(e)?);
    }
    range(&fmt(s)?, &fmt(e)?)
}

/// `variables_resolve.py::format_variable_display_value`.
fn format_variable_display_value(
    var_def: &VariableDoc,
    value: &Value,
    input_type: &str,
) -> Result<String, String> {
    match input_type {
        "select" | "radio" => {
            if value_is_unset(value) {
                return read_only_unset_label(
                    var_def.placeholder.as_deref(),
                    "controls.all",
                );
            }
            if matches!(value, Value::Array(_)) {
                return Err(format!(
                    "{input_type} variable expects a scalar value, got a list: {value:?}"
                ));
            }
            Ok(py_str(value))
        }
        "multiselect" => {
            let selected = coerce_multiselect(value);
            if selected.is_empty() {
                return read_only_unset_label(
                    var_def.placeholder.as_deref(),
                    "controls.all",
                );
            }
            if selected.len() == 1 {
                return Ok(py_str(&selected[0]));
            }
            let first = py_str(&selected[0]);
            let n = selected.len().to_string();
            display_text::language::tr(
                "controls.selection_summary",
                None,
                &[("first", &first), ("n", &n)],
            )
        }
        "daterange" => {
            if daterange_is_unset(value) {
                return read_only_unset_label(
                    var_def.placeholder.as_deref(),
                    "controls.all_dates",
                );
            }
            let Value::Array(a) = value else {
                return Err("daterange variable expects a 2-element list".to_string());
            };
            if a.len() != 2 {
                return Err(format!(
                    "daterange variable expects exactly 2 elements, got {}",
                    a.len()
                ));
            }
            if value_is_unset(&a[0]) {
                return dated("controls.until", &format_date_label(&py_str(&a[1]))?);
            }
            if value_is_unset(&a[1]) {
                return dated("controls.from", &format_date_label(&py_str(&a[0]))?);
            }
            format_daterange_label(&py_str(&a[0]), &py_str(&a[1]))
        }
        "checkbox" => {
            let checked = match value {
                Value::Null => false,
                Value::String(s) => {
                    matches!(s.trim().to_lowercase().as_str(), "true" | "yes" | "1")
                }
                other => checked_from_current(other),
            };
            word(if checked { "controls.yes" } else { "controls.no" })
        }
        "date" | "datepicker" => {
            if value_is_unset(value) {
                return read_only_unset_label(
                    var_def.placeholder.as_deref(),
                    "controls.any_date",
                );
            }
            format_date_label(&py_str(value))
        }
        "text" | "input" | "textarea" | "number" | "slider" | "range" => {
            if value_is_unset(value) {
                return Ok(var_def.placeholder.clone().unwrap_or_default());
            }
            Ok(py_str(value))
        }
        "auto" => Err("variable display requires a resolved input type, not 'auto'".to_string()),
        other => Err(format!(
            "unsupported variable input type for display: {other}"
        )),
    }
}

/// `variables_strip.py::_committed_value` (:307-323): a list is
/// `json.dumps(list)`, anything else `str()`.
fn committed_value(current: &Value) -> Result<String, String> {
    Ok(match current {
        Value::Null => String::new(),
        Value::Array(_) => {
            let mut out = String::new();
            py_json_dumps(current, &mut out)?;
            out
        }
        other => py_str(other),
    })
}

/// `json.dumps(value)` with its defaults: `", "` and `": "` separators and
/// `ensure_ascii` (every non-ASCII code point a `\uXXXX` escape, astral ones as
/// a surrogate pair). A float spelled in scientific notation by `repr`
/// (`abs >= 1e16` or `< 1e-4`) and an object member (whose key order is the
/// insertion order of a Python dict) are not modeled.
fn py_json_dumps(value: &Value, out: &mut String) -> Result<(), String> {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => match (n.as_i64(), n.as_u64(), n.as_f64()) {
            (Some(i), _, _) => out.push_str(&i.to_string()),
            (_, Some(u), _) => out.push_str(&u.to_string()),
            (_, _, Some(f)) if f == 0.0 || (1e-4..1e16).contains(&f.abs()) => {
                out.push_str(&float_repr(f))
            }
            _ => {
                return Err(format!(
                    "variable value {n} needs Python's scientific-notation float repr, \
                     which is not supported"
                ))
            }
        },
        Value::String(s) => {
            out.push('"');
            for c in s.chars() {
                match c {
                    '"' => out.push_str("\\\""),
                    '\\' => out.push_str("\\\\"),
                    '\n' => out.push_str("\\n"),
                    '\r' => out.push_str("\\r"),
                    '\t' => out.push_str("\\t"),
                    '\u{8}' => out.push_str("\\b"),
                    '\u{c}' => out.push_str("\\f"),
                    ' '..='~' => out.push(c),
                    _ => {
                        let mut units = [0u16; 2];
                        for unit in c.encode_utf16(&mut units) {
                            out.push_str(&format!("\\u{unit:04x}"));
                        }
                    }
                }
            }
            out.push('"');
        }
        Value::Array(members) => {
            out.push('[');
            for (i, member) in members.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                py_json_dumps(member, out)?;
            }
            out.push(']');
        }
        Value::Object(_) => {
            return Err("variable value holds an object member, which is not supported".into())
        }
    }
    Ok(())
}

/// `variables_resolve.py::resolve_controls`. `committed_presets` is
/// `current_daterange_presets()`: the sizing pass has none to give. `datasets` mirrors Python's
/// `executor: Option<...>` — `None` for the sizing pass, `Some` for render.
/// `label_font_case` is `variables_style.label.font.case`, resolved once by
/// the caller (the same value every unlabeled control's inferred display
/// name reads).
pub fn resolve_controls(
    variable_defs: &[(&str, &VariableDoc)],
    current_values: &serde_json::Map<String, Value>,
    datasets: Option<&HashMap<String, Vec<serde_json::Map<String, Value>>>>,
    vs: &VariablesStyleDoc,
    label_font_case: &str,
    committed_presets: &serde_json::Map<String, Value>,
    context_variables: &[String],
) -> Result<Vec<ResolvedControl>, String> {
    let mut controls = Vec::new();
    for (name, var_def) in variable_defs {
        let name = *name;
        let var_def = *var_def;
        if !var_def.visible {
            continue;
        }
        let option_values = resolve_option_values(var_def, name, datasets);
        let refined = refine_input_type_from_data(
            &var_def.input,
            var_def.input_auto_detected,
            &option_values,
        );
        let (low, high, step) = slider_bounds(var_def, &refined, vs);
        let mut current = current_values
            .get(name)
            .cloned()
            .unwrap_or_else(|| var_def.default.clone().unwrap_or(Value::Null));
        if matches!(refined.input_type.as_str(), "slider" | "range") && current.is_null() {
            current = Value::from(low);
        } else if refined.input_type == "multiselect" {
            current = Value::Array(coerce_multiselect(&current));
        }
        let label = match &var_def.label {
            Some(l) => l.clone(),
            None => apply_case(&display_text::slug_to_text(name)?, label_font_case)?,
        };
        let display_value = format_variable_display_value(var_def, &current, &refined.input_type)?;
        // `variables_resolve.py::resolve_controls`'s own `placeholder=`: the
        // authored hint, only carried when the control is free-entry AND
        // its current value is empty — the one fact `_draw_text_field`
        // reads to decide whether to paint the shown text muted.
        let hint = if is_free_entry_input(&refined.input_type) && value_is_unset(&current) {
            var_def.placeholder.clone()
        } else {
            None
        };
        let enabled = datasets.is_none()
            || eval_bool_condition(
                &var_def.enabled,
                true,
                current_values,
                "board variable enabled",
            )?;
        let can_unset = !var_def.required.unwrap_or(false);
        let presets = var_def.presets.as_deref().unwrap_or(&vs.presets);
        let segments = build_segments(
            name,
            &refined.input_type,
            &option_values,
            presets,
            &current,
            can_unset,
            enabled,
            current_values,
            committed_presets,
            context_variables,
            vs.presets_display == "buttons",
            var_def.has_static_options(),
        )?;
        let refined_input = refined.input_type.clone();
        controls.push(ResolvedControl {
            name: name.to_string(),
            input: refined.input_type,
            label,
            display_value,
            checked: checked_from_current(&current),
            current,
            option_values,
            // `_is_enabled` (variables_resolve.py:193-208): the executor-less
            // sizing pass never reads `enabled` — it moves no box.
            enabled,
            slider_min: low,
            slider_max: high,
            slider_step: step,
            can_unset,
            placeholder: var_def.placeholder.clone(),
            hint,
            authored_min: var_def.min,
            authored_max: var_def.max,
            authored_step: var_def.step,
            defined_in_other_file: var_def.defined_in_other_file,
            segments,
            preset: committed_presets
                .get(name)
                .filter(|_| refined_input == "daterange")
                .map(py_str),
        });
    }
    Ok(controls)
}

// ---------------------------------------------------------------------
// sizing.py: variable-controls height, the title-inline-band decision
// ---------------------------------------------------------------------

/// `sizing.py::_variables_layout`, always the `executor=None` shape — the
/// sizing pass has no query results, so a query-backed `options.query`
/// widget refinement is invisible here, matching Python's own gap (values
/// still measure the board is being drawn for; only the widget kind can't
/// refine).
fn variables_layout_for_sizing(
    variable_defs: &[(&str, &VariableDoc)],
    width: f64,
    variable_values: &serde_json::Map<String, Value>,
    vs: &VariablesStyleDoc,
    fonts_dir: &str,
) -> Result<VariablesLayout, String> {
    let label_case = vs
        .label
        .font
        .case
        .as_deref()
        .ok_or("style.variables.label.font.case must be resolved")?;
    let controls = resolve_controls(
        variable_defs,
        variable_values,
        None,
        vs,
        label_case,
        &serde_json::Map::new(),
        &[],
    )?;
    let specs: Vec<ControlSpec> = controls.iter().map(ResolvedControl::spec).collect();
    lay_out_variables(&specs, width, vs, fonts_dir)
}

/// `sizing.py::compute_variable_controls_height`.
pub fn compute_variable_controls_height(
    variable_defs: &[(&str, &VariableDoc)],
    width: f64,
    variable_values: &serde_json::Map<String, Value>,
    vs: &VariablesStyleDoc,
    fonts_dir: &str,
) -> Result<f64, String> {
    Ok(variables_layout_for_sizing(variable_defs, width, variable_values, vs, fonts_dir)?.height)
}

const TITLE_INLINE_VARIABLE_MAX_ROWS: i64 = 2;
const MIN_TITLE_INLINE_TITLE_COLUMN_PX: f64 = 48.0;
const TITLE_INLINE_TITLE_MAX_INNER_RATIO: f64 = 0.8;

/// `sizing.py::resolve_title_variables_inline_widths`.
#[allow(clippy::too_many_arguments)]
pub fn resolve_title_variables_inline_widths(
    inner: f64,
    title_style: &mdsvg::style::Style,
    title_fonts: &measure::fonts::FontSlots,
    title_case: Option<&str>,
    visible_variables: &[(&str, &VariableDoc)],
    title: &str,
    variable_values: &serde_json::Map<String, Value>,
    vs: &VariablesStyleDoc,
) -> Result<(f64, f64), String> {
    let col_gap = vs.gap;
    if visible_variables.is_empty() {
        return Ok((inner.max(1.0), 0.0));
    }
    let natural = if title.is_empty() {
        0.0
    } else {
        crate::title::title_max_content_width(
            title,
            variable_values,
            title_case,
            title_style.clone(),
            title_fonts.clone(),
        )?
    };
    let natural_with_pad = if natural > 0.0 {
        natural + 8.0
    } else {
        MIN_TITLE_INLINE_TITLE_COLUMN_PX
    };
    let mut title_w = natural_with_pad.min(inner * TITLE_INLINE_TITLE_MAX_INNER_RATIO);
    title_w = title_w.max(MIN_TITLE_INLINE_TITLE_COLUMN_PX);
    let cap = vs.title_inline_title_max_width;
    if cap > 0.0 {
        title_w = title_w.min(cap);
    }
    let vars_w = (inner - title_w - col_gap).max(1.0);
    Ok((title_w, vars_w))
}

/// Width and packing edge of a variables band on its own line:
/// `sizing.py::standalone_variables_band`.
pub fn standalone_variables_band(
    title: &str,
    content_width: f64,
    card_padding: f64,
    vs: &VariablesStyleDoc,
) -> (f64, &'static str) {
    if title.is_empty() && vs.position == "title-inline" {
        return ((content_width - 2.0 * card_padding).max(0.0), "end");
    }
    ((content_width - card_padding).max(0.0), "start")
}

/// `sizing.py::should_use_title_inline_band`.
#[allow(clippy::too_many_arguments)]
pub fn should_use_title_inline_band(
    title: &str,
    visible_variables: &[(&str, &VariableDoc)],
    content_width: f64,
    title_style: Option<&mdsvg::style::Style>,
    title_fonts: Option<&measure::fonts::FontSlots>,
    title_case: Option<&str>,
    vs: &VariablesStyleDoc,
    card_padding: f64,
    variable_values: &serde_json::Map<String, Value>,
    fonts_dir: &str,
) -> Result<bool, String> {
    if vs.position != "title-inline" || title.is_empty() || visible_variables.is_empty() {
        return Ok(false);
    }
    let title_style = title_style.ok_or("board has a title but payload carries no title_style")?;
    let title_fonts = title_fonts.ok_or("board has a title but payload carries no title_fonts")?;
    let inner = (content_width - 2.0 * card_padding).max(1.0);
    let (_title_w, vars_w) = resolve_title_variables_inline_widths(
        inner,
        title_style,
        title_fonts,
        title_case,
        visible_variables,
        title,
        variable_values,
        vs,
    )?;
    let layout =
        variables_layout_for_sizing(visible_variables, vars_w, variable_values, vs, fonts_dir)?;
    if layout.rows > TITLE_INLINE_VARIABLE_MAX_ROWS {
        return Ok(false);
    }
    let widest = layout
        .placed_extents()
        .map(|(_, end)| end)
        .fold(0.0_f64, f64::max);
    Ok(widest <= vars_w + WRAP_EPSILON)
}

/// `sizing.py::compute_title_variables_inline_baseline_layout`.
pub fn compute_title_variables_inline_baseline_layout(
    title_h: f64,
    vars_h: f64,
    title_baseline: f64,
    label_font_size: f64,
    label_font_family: &str,
    pad: f64,
    fonts_dir: &str,
) -> (f64, f64, f64) {
    let vars_baseline =
        vars_h / 2.0 + centered_baseline_offset(label_font_family, label_font_size, fonts_dir);
    let target = title_baseline.max(vars_baseline);
    let title_dy = target - title_baseline;
    let vars_dy = target - vars_baseline;
    let band_h = (title_dy + title_h).max(vars_dy + vars_h) + pad;
    (title_dy, vars_dy, band_h)
}

/// `sizing.py::compute_title_variables_inline_band_height`.
#[allow(clippy::too_many_arguments)]
pub fn compute_title_variables_inline_band_height(
    title: &str,
    visible_variables: &[(&str, &VariableDoc)],
    content_width: f64,
    variable_values: &serde_json::Map<String, Value>,
    card_padding: f64,
    title_min_height: f64,
    title_style: mdsvg::style::Style,
    title_fonts: measure::fonts::FontSlots,
    title_case: Option<&str>,
    vs: &VariablesStyleDoc,
    fonts_dir: &str,
) -> Result<f64, String> {
    if title.is_empty() {
        return Ok(0.0);
    }
    let inner = (content_width - 2.0 * card_padding).max(1.0);
    let (_title_w, vars_w) = resolve_title_variables_inline_widths(
        inner,
        &title_style,
        &title_fonts,
        title_case,
        visible_variables,
        title,
        variable_values,
        vs,
    )?;
    let title_h = crate::title::get_title_height(
        title,
        variable_values,
        title_case,
        inner,
        title_style.clone(),
        title_fonts.clone(),
    )?
    .max(title_min_height);
    if visible_variables.is_empty() {
        return Ok(title_h);
    }
    let var_h = compute_variable_controls_height(
        visible_variables,
        vars_w,
        variable_values,
        vs,
        fonts_dir,
    )?;
    let label_size = vs
        .font
        .size
        .ok_or("style.variables.font.size must be configured")?;
    let label_family = vs
        .font
        .family
        .as_deref()
        .ok_or("style.variables.font.family must be configured")?;
    let title_baseline = crate::title::title_baseline_offset(
        title,
        variable_values,
        title_case,
        inner,
        title_style,
        title_fonts,
    )?;
    let (_title_dy, _vars_dy, band_h) = compute_title_variables_inline_baseline_layout(
        title_h,
        var_h,
        title_baseline,
        label_size,
        label_family,
        vs.title_inline_band_bottom_pad,
        fonts_dir,
    );
    Ok(band_h)
}

/// `boards.py::_render_title_variables_inline_band`: one horizontal band,
/// title (left column) + variable controls (right column, `align: "end"`),
/// baseline-aligned. `title_authored_attrs` empty is the nested-board
/// shape (its own combined header handle already covers the band; only the
/// title sub-group's `kind` leaf is tagged); non-empty is the root's own
/// standalone `title` handle.
#[allow(clippy::too_many_arguments)]
pub fn render_title_variables_inline_band(
    title: &str,
    visible_variables: &[(&str, &VariableDoc)],
    variable_values: &serde_json::Map<String, Value>,
    layout_content_width: f64,
    card_pad: f64,
    title_style: mdsvg::style::Style,
    title_fonts: measure::fonts::FontSlots,
    title_case: Option<&str>,
    title_color: Option<&str>,
    title_min_height: f64,
    title_authored_attrs: &str,
    variables_path: &str,
    overridden: &[String],
    datasets: Option<&HashMap<String, Vec<serde_json::Map<String, Value>>>>,
    committed_presets: &serde_json::Map<String, Value>,
    context_variables: &[String],
    vs: &VariablesStyleDoc,
    fonts_dir: &str,
    painted_italics: &crate::embed_fonts::PaintedItalics,
) -> Result<(String, f64), String> {
    let inner = (layout_content_width - 2.0 * card_pad).max(1.0);
    let (title_w, vars_w) = resolve_title_variables_inline_widths(
        inner,
        &title_style,
        &title_fonts,
        title_case,
        visible_variables,
        title,
        variable_values,
        vs,
    )?;
    let col_gap = vs.gap;

    let mut title_svg_raw = crate::title::render_title_svg(
        title,
        variable_values,
        title_case,
        inner,
        title_style.clone(),
        title_fonts.clone(),
        Some(painted_italics),
    )?;
    if let Some(color) = title_color {
        title_svg_raw = crate::title::paint_title_svg_fill(&title_svg_raw, color);
    }
    let title_inner = crate::board::extract_svg_inner_content(&title_svg_raw)?;
    let measured_title_h = crate::title::get_title_height(
        title,
        variable_values,
        title_case,
        inner,
        title_style.clone(),
        title_fonts.clone(),
    )?;
    let title_h = measured_title_h.max(title_min_height);
    let inline_title_line_box = crate::title::title_line_box(
        title,
        variable_values,
        title_case,
        inner,
        title_style.clone(),
        title_fonts.clone(),
    )?;

    let label_case = vs
        .label
        .font
        .case
        .as_deref()
        .ok_or("style.variables.label.font.case must be resolved")?;
    let controls = resolve_controls(
        visible_variables,
        variable_values,
        datasets,
        vs,
        label_case,
        committed_presets,
        context_variables,
    )?;
    let (vars_svg, vars_h) =
        render_variables_strip_svg(
            &controls,
            vars_w,
            vs,
            "end",
            variables_path,
            overridden,
            fonts_dir,
        )?;

    let label_size = vs
        .font
        .size
        .ok_or("style.variables.font.size must be configured")?;
    let label_family = vs
        .font
        .family
        .as_deref()
        .ok_or("style.variables.font.family must be configured")?;
    let title_baseline = crate::title::title_baseline_offset(
        title,
        variable_values,
        title_case,
        inner,
        title_style,
        title_fonts,
    )?;
    let (title_dy, vars_dy, band_h) = compute_title_variables_inline_baseline_layout(
        title_h,
        vars_h,
        title_baseline,
        label_size,
        label_family,
        vs.title_inline_band_bottom_pad,
        fonts_dir,
    );

    let title_group = if !title_authored_attrs.is_empty() {
        crate::title::tagged_authoring_block(
            title_inner,
            title_authored_attrs,
            card_pad,
            title_dy,
            title_w,
            title_h,
            card_pad,
            card_pad.min(col_gap),
            0.0,
            Some(inline_title_line_box),
        )
    } else {
        format!(
            r#"<g transform="translate({}, {})"{}>{}</g>"#,
            pxi(card_pad),
            pxi(title_dy),
            crate::contract::authored_kind_attr("title"),
            title_inner
        )
    };
    let vars_group = translate_group(
        &pxi(card_pad + title_w + col_gap),
        &pxi(vars_dy),
        &vars_svg,
        "",
    );
    let band = format!("{title_group}{vars_group}");
    Ok((band, band_h))
}

#[cfg(test)]
mod resolve_tests {
    use super::*;

    #[test]
    fn a_multiselect_with_several_values_shows_the_first_and_a_count() {
        let doc: VariableDoc = serde_json::from_value(serde_json::json!({"visible": true})).unwrap();
        assert_eq!(
            format_variable_display_value(
                &doc,
                &serde_json::json!(["South", "North", "East"]),
                "multiselect"
            )
            .unwrap(),
            "(3) South"
        );
        assert_eq!(
            format_variable_display_value(&doc, &serde_json::json!(["North"]), "multiselect")
                .unwrap(),
            "North"
        );
    }

    #[test]
    fn a_set_date_displays_as_day_month_year_not_raw_iso() {
        let _locale = ::bind::time_locale::en_us_scope();
        let doc: VariableDoc = serde_json::from_value(serde_json::json!({"visible": true})).unwrap();
        assert_eq!(
            format_variable_display_value(&doc, &serde_json::json!("2026-09-21"), "date").unwrap(),
            "21 Sep 2026"
        );
    }

    #[test]
    fn a_daterange_with_one_open_end_names_the_other() {
        let _locale = ::bind::time_locale::en_us_scope();
        let doc: VariableDoc =
            serde_json::from_value(serde_json::json!({"visible": true, "placeholder": "Any time"}))
                .unwrap();
        let label = |v: Value| format_variable_display_value(&doc, &v, "daterange").unwrap();
        assert_eq!(label(serde_json::json!(["2025-10-01", null])), "From 1 Oct 2025");
        assert_eq!(label(serde_json::json!(["2025-10-01", ""])), "From 1 Oct 2025");
        assert_eq!(label(serde_json::json!([null, "2026-10-04"])), "Until 4 Oct 2026");
        assert_eq!(label(serde_json::json!([null, null])), "Any time");
        assert_eq!(label(serde_json::json!(["", ""])), "Any time");
    }

    #[test]
    fn slug_to_text_expands_abbreviations_and_unit_suffixes() {
        assert_eq!(display_text::slug_to_text("revenue_usd").unwrap(), "Revenue ($)");
        assert_eq!(display_text::slug_to_text("yoy_growth_pct").unwrap(), "YoY growth (%)");
        assert_eq!(display_text::slug_to_text("avg_deal_size").unwrap(), "Avg deal size");
    }

    /// Every pair here is `(slug_to_text(name).unwrap(), apply_case(..., "title"))`
    /// from the real `titlecase` library — captured live, not hand-derived.
    #[test]
    fn slug_to_text_output_matches_the_real_titlecase_library_under_apply_case() {
        let cases: &[(&str, &str)] = &[
            ("region", "Region"),
            ("customer segment", "Customer Segment"),
            ("min pages", "Min Pages"),
            ("revenue ($)", "Revenue ($)"),
            ("YoY growth (%)", "YoY Growth (%)"),
            ("Avg deal size", "Avg Deal Size"),
            ("is active", "Is Active"),
            ("cost and revenue", "Cost and Revenue"),
            ("the region", "The Region"),
            ("a and b", "A and B"),
            ("customer ID", "Customer ID"),
            ("API key", "API Key"),
            ("time to value", "Time to Value"),
            ("churn rate (%)", "Churn Rate (%)"),
            ("up to date", "Up to Date"),
            ("per seat pricing", "Per Seat Pricing"),
            ("value out of range", "Value out of Range"),
        ];
        for (input, expected) in cases {
            assert_eq!(
                apply_case(input, "title").unwrap(),
                *expected,
                "input {input:?}"
            );
        }
    }

    /// A variable's default label is `apply_case(slug_to_text(name).unwrap(), ...)`
    /// (the one call site above) — `apply_case` must be `case::apply_case`,
    /// the same law production uses for chart titles, not a second,
    /// restricted copy. Expected values captured live from
    /// `dbt_charts.core.text.humanize.slug_to_text` + `dbt_charts.core.text.case.apply_case`.
    #[test]
    fn label_case_matches_production_case_law_for_inline_period_and_name_prefix_tokens() {
        let cases: &[(&str, &str)] = &[
            ("rev.usd", "Rev.usd"),
            ("o'brien", "O'Brien"),
            ("mcdonald", "McDonald"),
            ("cpu/memory", "Cpu/Memory"),
        ];
        for (name, expected) in cases {
            let actual = apply_case(&display_text::slug_to_text(name).unwrap(), "title").unwrap();
            assert_eq!(&actual, expected, "label for {name:?}");
        }
    }

    #[test]
    fn py_str_capitalizes_python_bools() {
        assert_eq!(py_str(&Value::Bool(true)), "True");
        assert_eq!(py_str(&Value::Bool(false)), "False");
    }

    #[test]
    fn committed_list_is_json_dumps_with_its_default_separators() {
        let list = serde_json::json!(["a", "b", 3, 2.5, true, null]);
        assert_eq!(
            committed_value(&list).unwrap(),
            r#"["a", "b", 3, 2.5, true, null]"#
        );
        assert_eq!(committed_value(&serde_json::json!([])).unwrap(), "[]");
        assert_eq!(
            committed_value(&serde_json::json!([["a", 1], ["b"]])).unwrap(),
            r#"[["a", 1], ["b"]]"#
        );
    }

    #[test]
    fn committed_list_escapes_like_json_dumps_ensure_ascii() {
        let list = serde_json::json!(["Café", "say \"hi\"", "a\\b", "l1\nl2\t", "\u{1}", "\u{7f}", "😀"]);
        assert_eq!(
            committed_value(&list).unwrap(),
            r#"["Caf\u00e9", "say \"hi\"", "a\\b", "l1\nl2\t", "\u0001", "\u007f", "\ud83d\ude00"]"#
        );
    }

    #[test]
    fn committed_list_floats_use_python_repr() {
        let list = serde_json::json!([1.0, 0.1, 1e15, 100000.5]);
        assert_eq!(committed_value(&list).unwrap(), "[1.0, 0.1, 1000000000000000.0, 100000.5]");
    }

    #[test]
    fn committed_list_refuses_what_python_repr_spells_in_scientific_notation() {
        for v in [serde_json::json!([1e16]), serde_json::json!([0.00001])] {
            let err = committed_value(&v).unwrap_err();
            assert!(err.contains("scientific"), "{err}");
        }
    }

    #[test]
    fn committed_list_refuses_an_object_member() {
        let err = committed_value(&serde_json::json!([{"a": 1}])).unwrap_err();
        assert!(err.contains("object"), "{err}");
    }

    #[test]
    fn committed_scalar_is_python_str() {
        assert_eq!(committed_value(&serde_json::json!("x")).unwrap(), "x");
        assert_eq!(committed_value(&serde_json::json!(true)).unwrap(), "True");
        assert_eq!(committed_value(&Value::Null).unwrap(), "");
    }

    #[test]
    fn py_str_keeps_integer_json_numbers_bare() {
        assert_eq!(py_str(&serde_json::json!(1200)), "1200");
        assert_eq!(py_str(&serde_json::json!(1200.5)), "1200.5");
    }

    #[test]
    fn format_daterange_label_same_month_collapses_the_range() {
        let _locale = ::bind::time_locale::en_us_scope();
        assert_eq!(
            format_daterange_label("2024-03-05", "2024-03-12").unwrap(),
            "5\u{2013}12 Mar 2024"
        );
    }

    #[test]
    fn format_daterange_label_cross_year_spells_both_dates() {
        let _locale = ::bind::time_locale::en_us_scope();
        assert_eq!(
            format_daterange_label("2023-12-30", "2024-01-02").unwrap(),
            "30 Dec 2023 \u{2013} 2 Jan 2024"
        );
    }

    #[test]
    fn format_date_label_spells_the_date_and_passes_a_non_iso_string_through() {
        let _locale = ::bind::time_locale::en_us_scope();
        assert_eq!(format_date_label("2026-09-21").unwrap(), "21 Sep 2026");
        assert_eq!(format_date_label("last week").unwrap(), "last week");
    }

    #[test]
    fn date_labels_take_their_month_names_from_the_board_time_locale() {
        ::bind::time_locale::with_time_locale(&::bind::time_locale::de_de(), || {
            assert_eq!(format_date_label("2024-03-05").unwrap(), "5 Mrz 2024");
            assert_eq!(
                format_daterange_label("2024-03-05", "2024-03-12").unwrap(),
                "5\u{2013}12 Mrz 2024"
            );
        });
    }

    #[test]
    fn a_date_label_without_a_time_locale_refuses() {
        assert!(format_date_label("2024-03-05").unwrap_err().contains("time_locale"));
    }

    #[test]
    fn a_date_pill_is_unsettable_like_a_daterange_and_a_chooser() {
        for input in ["date", "datepicker"] {
            assert_eq!(traits_of(input).unwrap().unset, "date");
        }
        assert_eq!(traits_of("daterange").unwrap().unset, "range");
        assert_eq!(traits_of("select").unwrap().unset, "chooser");
        assert_eq!(traits_of("text").unwrap().unset, "none");
    }
}

// ---------------------------------------------------------------------
// variables_strip.py
// ---------------------------------------------------------------------

/// `variables_strip.py::_font_size`.
fn font_size(vs: &VariablesStyleDoc) -> Result<f64, String> {
    vs.font
        .size
        .ok_or("style.variables.font.size must be resolved".to_string())
}

fn font_weight_as_css(w: &Value) -> String {
    match w {
        Value::String(s) => s.clone(),
        Value::Number(n) => {
            if let Some(f) = n.as_f64() {
                if f.fract() == 0.0 {
                    (f as i64).to_string()
                } else {
                    f.to_string()
                }
            } else {
                n.to_string()
            }
        }
        _ => String::new(),
    }
}

/// `variables_strip.py::_row_offsets`.
fn row_offsets(layout: &VariablesLayout, width: f64, align: &str) -> HashMap<u64, f64> {
    if align != "end" {
        return layout
            .placed_extents()
            .map(|(y, _)| (y.to_bits(), 0.0))
            .collect();
    }
    let mut ends: HashMap<u64, f64> = HashMap::new();
    for (y, end) in layout.placed_extents() {
        ends.insert(y.to_bits(), end);
    }
    ends.into_iter()
        .map(|(y, end)| (y, (width - end).max(0.0)))
        .collect()
}

/// `variables_strip.py::_field_rect`.
fn field_rect(x: f64, y: f64, width: f64, height: f64, vs: &VariablesStyleDoc) -> String {
    let input_style = &vs.input;
    format!(
        r#"<rect data-dbt-field="input" x="{}" y="{}" width="{}" height="{}" rx="{}" fill="{}" stroke="none"/>"#,
        pxi(x),
        pxi(y),
        pxi(width),
        pxi(height),
        pxi(input_style.border.radius),
        xml_escape_attr(&input_style.background)
    )
}

/// `variables_strip.py::_draw_reset`.
fn draw_reset(
    reset: &ResetBox,
    dx: f64,
    vs: &VariablesStyleDoc,
    enabled: bool,
    fonts_dir: &str,
) -> Result<String, String> {
    let fs = font_size(vs)?;
    let family = vs.value.font.family.as_deref().unwrap_or_default();
    let x = reset.x + dx;
    let ink = if enabled { &vs.value.font.color } else { &vs.placeholder.font.color };
    let disabled = if enabled { "" } else { r#" data-dbt-enabled="false""# };
    let field_y = reset.y + (reset.height - vs.input.height) / 2.0;
    let baseline = reset.y + reset.height / 2.0 + centered_baseline_offset(family, fs, fonts_dir);
    Ok(format!(
        r#"<g data-dbt-reset="true"{disabled} opacity="0"><rect x="{}" y="{}" width="{}" height="{}" rx="{}" fill="{}" stroke="none"/><text x="{}" y="{}" font-size="{}" font-family="{}" fill="{}">{}</text></g>"#,
        pxi(x),
        pxi(field_y),
        pxi(reset.width),
        pxi(vs.input.height),
        pxi(vs.input.border.radius),
        xml_escape_attr(&vs.input.background),
        pxi(x + vs.input.padding.left),
        pxi(baseline),
        pxi(fs),
        xml_escape_attr(family),
        xml_escape_attr(ink.as_deref().unwrap_or_default()),
        xml_escape(&word("controls.reset")?)
    ))
}

/// `variables_strip.py::_draw_arrow`.
fn draw_arrow(
    right: f64,
    mid: f64,
    font_size: f64,
    vs: &VariablesStyleDoc,
) -> Result<String, String> {
    let size = ornament_width("select", font_size)?;
    let x = right - vs.input.padding.right - size;
    let drop = size * 0.32;
    Ok(format!(
        r#"<path data-dbt-ornament="arrow" opacity="0" d="M{} {} L{} {} L{} {}" fill="none" stroke="{}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>"#,
        pxi(x),
        pxi(mid - drop / 2.0),
        pxi(x + size / 2.0),
        pxi(mid + drop / 2.0),
        pxi(x + size),
        pxi(mid - drop / 2.0),
        xml_escape_attr(vs.placeholder.font.color.as_deref().unwrap_or_default())
    ))
}

/// `variables_strip.py::_draw_calendar`.
fn draw_calendar(
    right: f64,
    mid: f64,
    font_size: f64,
    vs: &VariablesStyleDoc,
) -> Result<String, String> {
    let size = ornament_width("date", font_size)?;
    let x = right - vs.input.padding.right - size;
    let top = mid - size / 2.0;
    Ok(format!(
        r#"<g data-dbt-ornament="calendar" opacity="0" fill="none" stroke="{}" stroke-width="1.25"><rect x="{}" y="{}" width="{}" height="{}" rx="{}"/><line x1="{}" y1="{}" x2="{}" y2="{}"/></g>"#,
        xml_escape_attr(vs.placeholder.font.color.as_deref().unwrap_or_default()),
        pxi(x),
        pxi(top + size * 0.12),
        pxi(size),
        pxi(size * 0.88),
        pxi(size * 0.15),
        pxi(x),
        pxi(top + size * 0.4),
        pxi(x + size),
        pxi(top + size * 0.4)
    ))
}

/// `variables_strip.py::_draw_text_field`. `hint` is true when the shown
/// text is the authored placeholder standing in for an empty value — the
/// one thing that decides whether it paints in the muted placeholder color.
fn draw_text_field(
    box_: &ControlBox,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    vs: &VariablesStyleDoc,
    fonts_dir: &str,
    hint: bool,
    ornamented: bool,
    text_at: Option<f64>,
) -> Result<Vec<String>, String> {
    let fs = font_size(vs)?;
    let value_font = &vs.value.font;
    let family = value_font.family.as_deref().unwrap_or_default();
    let text_x = text_at.unwrap_or(x + vs.input.padding.left);
    let baseline = y + height / 2.0 + centered_baseline_offset(family, fs, fonts_dir);
    let color = if hint {
        vs.placeholder.font.color.as_deref().unwrap_or_default()
    } else {
        value_font.color.as_deref().unwrap_or_default()
    };

    let mut parts = if text_at.is_some() { Vec::new() } else { vec![field_rect(x, y, width, height, vs)] };
    parts.push(
        format!(
            r#"<text x="{}" y="{}" font-size="{}" font-family="{}" fill="{}">{}</text>"#,
            pxi(text_x),
            pxi(baseline),
            pxi(fs),
            xml_escape_attr(family),
            xml_escape_attr(color),
            xml_escape(&box_.value)
        ),
    );
    match traits_of(&box_.input)?.ornament {
        "arrow" if ornamented => parts.push(draw_arrow(x + width, y + height / 2.0, fs, vs)?),
        "calendar" if ornamented => {
            parts.push(draw_calendar(x + width, y + height / 2.0, fs, vs)?)
        }
        _ => {}
    }
    Ok(parts)
}

/// `variables_strip.py::_draw_checkbox`.
fn draw_checkbox(checked: bool, x: f64, y: f64, vs: &VariablesStyleDoc) -> Vec<String> {
    let size = vs.input.widths.checkbox;
    let top = y + (vs.input.height - size) / 2.0;
    let mut parts = vec![format!(
        r#"<rect data-dbt-field="checkbox" data-dbt-ornament="checkbox" x="{}" y="{}" width="{}" height="{}" rx="{}" fill="{}" stroke="{}" stroke-width="1"/>"#,
        pxi(x),
        pxi(top),
        pxi(size),
        pxi(size),
        pxi(vs.input.border.radius),
        xml_escape_attr(&vs.input.background),
        xml_escape_attr(&vs.border.color)
    )];
    if checked {
        parts.push(format!(
            r#"<path d="M{} {} L{} {} L{} {}" fill="none" stroke="{}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>"#,
            pxi(x + size * 0.25),
            pxi(top + size * 0.5),
            pxi(x + size * 0.45),
            pxi(top + size * 0.7),
            pxi(x + size * 0.75),
            pxi(top + size * 0.3),
            xml_escape_attr(vs.value.font.color.as_deref().unwrap_or_default())
        ));
    }
    parts
}

/// `variables_strip.py::_number_attr`: a bound as the runtime must read it
/// back — exact, never `:g`'s six significant digits, which turned an
/// authored 1234567 into 1.23457e+06. Used only for `data-dbt-min`/`-max`/
/// `-step`/`-placeholder`'s numeric-attribute siblings (`_draw_control`'s
/// own comment: deliberately NOT through `px()`, since these are the
/// authored bounds a runtime maps a pointer against, not a
/// rasterization-crisp coordinate).
fn number_attr(value: f64) -> String {
    if value.fract() == 0.0 {
        format!("{}", value as i64)
    } else {
        float_repr(value)
    }
}

/// `variables_strip.py::_slider_fraction`.
fn slider_fraction(current: &Value, slider_min: f64, slider_max: f64) -> f64 {
    let value = match current {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    };
    let Some(value) = value else { return 0.0 };
    if slider_max <= slider_min {
        return 0.0;
    }
    let span = slider_max - slider_min;
    ((value - slider_min) / span).clamp(0.0, 1.0)
}

/// `variables_strip.py::_draw_slider`.
#[allow(clippy::too_many_arguments)]
fn draw_slider(
    control: &ResolvedControl,
    box_: &ControlBox,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    vs: &VariablesStyleDoc,
    fonts_dir: &str,
) -> Result<Vec<String>, String> {
    let _ = width;
    let fs = font_size(vs)?;
    let value_font = &vs.value.font;
    let family = value_font.family.as_deref().unwrap_or_default();
    let track_width = vs.input.widths.range;
    let mid = y + height / 2.0;
    let thumb =
        x + track_width * slider_fraction(&control.current, control.slider_min, control.slider_max);

    Ok(vec![
        format!(
            r#"<rect data-dbt-field="slider" data-dbt-ornament="slider" x="{}" y="{}" width="{}" height="4" rx="2" fill="{}" stroke="none"/>"#,
            pxi(x),
            pxi(mid - 2.0),
            pxi(track_width),
            xml_escape_attr(&vs.input.background)
        ),
        format!(
            r#"<circle data-dbt-ornament="slider-thumb" cx="{}" cy="{}" r="6" fill="{}"/>"#,
            pxi(thumb),
            pxi(mid),
            xml_escape_attr(value_font.color.as_deref().unwrap_or_default())
        ),
        format!(
            r#"<text data-dbt-ornament="slider-value" x="{}" y="{}" font-size="{}" font-family="{}" fill="{}">{}</text>"#,
            pxi(x + track_width + vs.control_gap),
            pxi(mid + centered_baseline_offset(family, fs, fonts_dir)),
            pxi(fs),
            xml_escape_attr(family),
            xml_escape_attr(value_font.color.as_deref().unwrap_or_default()),
            xml_escape(&box_.value)
        ),
    ])
}

/// `variables_strip.py::_draw_field`.
#[allow(clippy::too_many_arguments)]
fn draw_field(
    control: &ResolvedControl,
    box_: &ControlBox,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    vs: &VariablesStyleDoc,
    fonts_dir: &str,
    align: &str,
) -> Result<Vec<String>, String> {
    if !box_.segment_rows.is_empty() {
        return draw_buttons(control, box_, x, y, width, vs, fonts_dir, align);
    }
    match traits_of(&box_.input)?.sizing {
        "checkbox" => Ok(draw_checkbox(control.checked, x, y, vs)),
        "slider" => draw_slider(control, box_, x, y, width, height, vs, fonts_dir),
        _ => draw_text_field(
            box_,
            x,
            y,
            width,
            height,
            vs,
            fonts_dir,
            control.hint.is_some(),
            true,
            None,
        ),
    }
}

/// `variables_strip.py::_corner`.
fn corner(x: f64, y: f64, radius: f64) -> String {
    if radius == 0.0 {
        String::new()
    } else {
        format!("A{} {} 0 0 1 {} {}", pxi(radius), pxi(radius), pxi(x), pxi(y))
    }
}

/// `variables_strip.py::_segment_path`.
fn segment_path(x: f64, y: f64, w: f64, h: f64, radius: f64, left: bool, right: bool) -> String {
    let rl = if left { radius } else { 0.0 };
    let rr = if right { radius } else { 0.0 };
    format!(
        "M{} {} L{} {} {} L{} {} {} L{} {} {} L{} {} {} Z",
        pxi(x + rl), pxi(y),
        pxi(x + w - rr), pxi(y),
        corner(x + w, y + rr, rr),
        pxi(x + w), pxi(y + h - rr),
        corner(x + w - rr, y + h, rr),
        pxi(x + rl), pxi(y + h),
        corner(x, y + h - rl, rl),
        pxi(x), pxi(y + rl),
        corner(x + rl, y, rl),
    )
}

/// `variables_strip.py::_draw_buttons`: the button row (hidden at rest) and the
/// selected value standing in for it in the same box.
fn draw_buttons(
    control: &ResolvedControl,
    box_: &ControlBox,
    x: f64,
    y: f64,
    width: f64,
    vs: &VariablesStyleDoc,
    fonts_dir: &str,
    align: &str,
) -> Result<Vec<String>, String> {
    let fs = font_size(vs)?;
    let value_font = &vs.value.font;
    let family = value_font.family.as_deref().unwrap_or_default();
    let field_height = vs.input.height;
    let radius = vs.input.border.radius;
    let pad_y = (vs.container_height - field_height) / 2.0;
    let pitch = vs.container_height + vs.gap;
    let label_ink = vs.label.font.color.as_deref().unwrap_or_default();
    let selected_ink = vs.input.selected_color.as_str();
    let selected_weight = font_weight_as_css(&vs.input.selected_weight);
    let resting_weight = font_weight_as_css(
        value_font.weight.as_ref().ok_or("style.variables.value.font.weight must be resolved")?,
    );

    let rest_x = if align == "end" { x + width - box_.value_width } else { x };
    let run_on = (box_.labeled && align != "end")
        .then(|| x - vs.control_gap + text_width(" ", fs, fonts_dir));
    let at_rest = draw_text_field(
        box_, rest_x, y, box_.value_width, field_height, vs, fonts_dir, false, false, run_on,
    )?;
    let mut drawn: Vec<String> = Vec::new();
    let mut segments = control.segments.iter();
    for (index, widths) in box_.segment_rows.iter().enumerate() {
        let row_y = box_.y + index as f64 * pitch + pad_y;
        let row_width: f64 = widths.iter().sum();
        drawn.push(format!(
            r#"<rect data-dbt-field="buttons" x="{}" y="{}" width="{}" height="{}" rx="{}" fill="{}" stroke="none"/>"#,
            pxi(x), pxi(row_y), pxi(row_width), pxi(field_height), pxi(radius),
            xml_escape_attr(&vs.input.background)
        ));
        let baseline = row_y + field_height / 2.0 + centered_baseline_offset(family, fs, fonts_dir);
        let mut left = x;
        for (position, &width) in widths.iter().enumerate() {
            let segment = segments.next().ok_or("button row has fewer segments than its layout")?;
            let (first, last) = (position == 0, position == widths.len() - 1);
            let (weight, ink, fill) = if segment.selected {
                (
                    selected_weight.as_str(),
                    selected_ink,
                    match &vs.input.selected_background {
                        Some(fill) => format!(
                            r#"<path d="{}" fill="{}"/>"#,
                            segment_path(left, row_y, width, field_height, radius, first, last),
                            xml_escape_attr(fill)
                        ),
                        None => format!(
                            r#"<path d="{}" fill="{}" fill-opacity="{}"/>"#,
                            segment_path(left, row_y, width, field_height, radius, first, last),
                            xml_escape_attr(label_ink),
                            float_repr(vs.input.selected_tint)
                        ),
                    },
                )
            } else {
                (resting_weight.as_str(), label_ink, String::new())
            };
            let divider = if first {
                String::new()
            } else {
                format!(
                    r#"<line data-dbt-field="divider" x1="{}" y1="{}" x2="{}" y2="{}" stroke="none"/>"#,
                    pxi(left), pxi(row_y), pxi(left), pxi(row_y + field_height)
                )
            };
            let inverse = if segment.selected && vs.input.selected_background.is_some() {
                r#" data-dbt-inverse="true""#
            } else {
                ""
            };
            let href = match &segment.href {
                Some(href) => format!(r#" href="{}""#, xml_escape_attr(href)),
                None => String::new(),
            };
            drawn.push(format!(
                r#"<a data-dbt-segment="{}"{href}{inverse} role="radio" aria-checked="{}"><rect x="{}" y="{}" width="{}" height="{}" fill="transparent"/>{fill}{divider}<rect data-dbt-segment-ring="true" x="{}" y="{}" width="{}" height="{}" rx="{}" fill="none" stroke="none"/><text x="{}" y="{}" text-anchor="middle" font-size="{}" font-weight="{weight}" font-family="{}" fill="{}">{}</text></a>"#,
                xml_escape_attr(&segment.value),
                if segment.selected { "true" } else { "false" },
                pxi(left), pxi(row_y), pxi(width), pxi(field_height),
                pxi(left + 2.0), pxi(row_y + 2.0), pxi(width - 4.0), pxi(field_height - 4.0),
                pxi((radius - 2.0).max(0.0)),
                pxi(left + width / 2.0), pxi(baseline), pxi(fs),
                xml_escape_attr(family),
                xml_escape_attr(ink),
                xml_escape(&segment.label)
            ));
            left += width;
        }
    }
    Ok(vec![
        format!(
            r#"<g data-dbt-buttons-value="true" class="dbt-pointer-inert">{}</g>"#,
            at_rest.concat()
        ),
        format!(r#"<g data-dbt-buttons="true" opacity="0">{}</g>"#, drawn.concat()),
    ])
}

/// `variables_strip.py::_committed_value` — public re-export point for
/// `_draw_control`'s own `data-dbt-value` attribute.
fn draw_control(
    control: &ResolvedControl,
    box_: &ControlBox,
    dx: f64,
    vs: &VariablesStyleDoc,
    variables_path: &str,
    fonts_dir: &str,
    align: &str,
) -> Result<String, String> {
    let fs = font_size(vs)?;
    let label_font = &vs.label.font;
    let family = label_font.family.as_deref().unwrap_or_default();
    let weight = label_font
        .weight
        .as_ref()
        .map(font_weight_as_css)
        .unwrap_or_default();

    let x = box_.x + dx;
    let lead = if box_.labeled { box_.label_width + vs.control_gap } else { 0.0 };
    let field_x = x + lead;
    let field_width = (box_.width - lead).max(0.0);
    let field_height = vs.input.height;
    let field_y = box_.y + (box_.height - field_height) / 2.0;
    let baseline = box_.y + box_.height / 2.0 + centered_baseline_offset(family, fs, fonts_dir);

    let authored = !variables_path.is_empty() && !control.defined_in_other_file;
    let handle = if authored {
        crate::title::authored_attrs(&format!("{variables_path}.{}", box_.name), "variable")
    } else {
        String::new()
    };
    let label_kind = if authored {
        crate::contract::authored_kind_attr("label")
    } else {
        String::new()
    };

    let mut parts = if box_.labeled {
        vec![format!(
            r#"<text{label_kind} x="{}" y="{}" font-size="{}" font-weight="{weight}" font-family="{}" fill="{}">{}</text>"#,
            pxi(x),
            pxi(baseline),
            pxi(fs),
            xml_escape_attr(family),
            xml_escape_attr(label_font.color.as_deref().unwrap_or_default()),
            xml_escape(&field_label(&box_.label)?), weight = xml_escape_attr(&weight)
        )]
    } else {
        Vec::new()
    };
    parts.extend(draw_field(
        control,
        box_,
        field_x,
        field_y,
        field_width,
        field_height,
        vs,
        fonts_dir,
        align,
    )?);

    let disabled = if !control.enabled {
        r#" data-dbt-enabled="false" opacity="0.55""#.to_string()
    } else {
        String::new()
    };
    let checked_attr = if box_.input == "checkbox" {
        format!(
            r#" data-dbt-checked="{}""#,
            if control.checked { "true" } else { "false" }
        )
    } else {
        String::new()
    };
    let value_attr = format!(
        r#" data-dbt-value="{}""#,
        xml_escape_attr(&match (&control.preset, &control.current) {
            (_, Value::Null) => String::new(),
            (Some(preset), _) => preset.clone(),
            (None, current) => committed_value(current)?,
        })
    );
    let mut bounds = if traits_of(&box_.input)?.sizing == "slider" {
        format!(
            r#" data-dbt-min="{}" data-dbt-max="{}" data-dbt-step="{}""#,
            number_attr(control.slider_min),
            number_attr(control.slider_max),
            number_attr(control.slider_step)
        )
    } else if box_.input == "number" {
        // Only what the author set: a number typed into a field is bounded
        // by nothing unless the board says so, never by the theme's slider
        // range.
        [
            ("min", control.authored_min),
            ("max", control.authored_max),
            ("step", control.authored_step),
        ]
        .into_iter()
        .filter_map(|(attr, value)| {
            value.map(|v| format!(r#" data-dbt-{attr}="{}""#, number_attr(v)))
        })
        .collect()
    } else {
        String::new()
    };
    // The hint the lifted native input shows while empty — published
    // whenever one is authored, not only while the drawing shows it, since
    // the field can be emptied on the page.
    if is_free_entry_input(&box_.input) {
        if let Some(placeholder) = &control.placeholder {
            bounds.push_str(&format!(
                r#" data-dbt-placeholder="{}""#,
                xml_escape_attr(placeholder)
            ));
        }
    }
    let unset_style = traits_of(&box_.input)?.unset;
    let mut unset = String::new();
    if unset_style != "none" {
        let default_label = match unset_style {
            "chooser" => "controls.all",
            "date" => "controls.any_date",
            _ => "controls.all_dates",
        };
        unset = format!(
            r#" data-dbt-can-unset="{}""#,
            if control.can_unset { "true" } else { "false" }
        );
        if control.can_unset {
            let label = read_only_unset_label(control.placeholder.as_deref(), default_label)?;
            unset.push_str(&format!(
                r#" data-dbt-unset-label="{}""#,
                xml_escape_attr(&label)
            ));
        }
    }
    // `controls_are_interactive()` gates a live-serve-only concern
    // (`render/controls.py`) this composer never reaches — the board sweep
    // always renders the static-export path, so `options` never draws here,
    // matching the harness's own render mode rather than a static export's.
    let options = String::new();

    let form = if box_.segment_rows.is_empty() { "" } else { r#" data-dbt-form="buttons""# };
    Ok(format!(
        r#"<g data-dbt-variable="{}"{handle} data-dbt-input="{}"{form}{checked_attr}{disabled}{value_attr}{bounds}{unset}{options} data-dbt-x="{}" data-dbt-y="{}" data-dbt-width="{}" data-dbt-height="{}">{}</g>"#,
        xml_escape_attr(&box_.name),
        xml_escape_attr(&box_.input),
        pxi(x),
        pxi(box_.y),
        pxi(box_.width),
        pxi(box_.height),
        parts.concat()
    ))
}

/// `variables_strip.py::render_variables_strip_svg`.
pub fn render_variables_strip_svg(
    controls: &[ResolvedControl],
    width: f64,
    vs: &VariablesStyleDoc,
    align: &str,
    variables_path: &str,
    overridden: &[String],
    fonts_dir: &str,
) -> Result<(String, f64), String> {
    if controls.is_empty() {
        return Ok((String::new(), 0.0));
    }
    let specs: Vec<ControlSpec> = controls.iter().map(ResolvedControl::spec).collect();
    let layout = lay_out_variables(&specs, width, vs, fonts_dir)?;
    for box_ in layout.boxes.iter().filter(|b| b.overflowed) {
        let path = if variables_path.is_empty() {
            Value::Null
        } else {
            Value::from(format!("{variables_path}.{}", box_.name))
        };
        diagnostic::warn(
            diagnostic::Diagnostic::new(
                &diagnostic::codes::WARN_VARIABLE_BUTTONS_OVERFLOW,
                [
                    ("available", Value::from(width)),
                    ("needed", Value::from(box_.buttons_width)),
                    ("variable", Value::from(box_.name.clone())),
                ],
            )
            .with_sink_param("path", path),
        );
    }
    let offsets = row_offsets(&layout, width, align);

    let mut drawn = Vec::with_capacity(controls.len());
    for (control, box_) in controls.iter().zip(layout.boxes.iter()) {
        let dx = *offsets.get(&box_.y.to_bits()).unwrap_or(&0.0);
        drawn.push(draw_control(
            control,
            box_,
            dx,
            vs,
            variables_path,
            fonts_dir,
            align,
        )?);
    }

    if let Some(reset) = &layout.reset {
        let dx = *offsets.get(&reset.y.to_bits()).unwrap_or(&0.0);
        // A chart's legend view is board state a Reset clears, though no control draws it.
        let enabled = controls.iter().any(|c| overridden.contains(&c.name))
            || overridden.iter().any(|name| name.starts_with(crate::legend_view::PARAM_PREFIX));
        drawn.push(draw_reset(reset, dx, vs, enabled, fonts_dir)?);
    }

    let anchor = format!(
        r#"<rect data-dbt-variables-box="true" data-dbt-align="{align}" class="dbt-pointer-inert" x="0" y="0" width="{}" height="{}" fill="none"/>"#,
        pxi(width),
        pxi(layout.height), align = xml_escape_attr(align)
    );
    Ok((
        format!(
            r#"<g data-dbt-variables="true" style="--dbt-variable-field-border: {}">{anchor}<g data-dbt-variables-static="true">{}</g></g>"#,
            xml_escape_attr(&vs.border.color),
            drawn.concat()
        ),
        layout.height,
    ))
}

#[cfg(test)]
mod strip_tests {
    use super::*;

    #[test]
    fn number_attr_prints_a_whole_value_without_a_decimal_point() {
        assert_eq!(number_attr(10.0), "10");
        assert_eq!(number_attr(-3.0), "-3");
        assert_eq!(number_attr(0.0), "0");
    }

    /// Python's `:g` format turned an authored 1234567 into "1.23457e+06" —
    /// the bug `_number_attr` exists to fix. An exact integer stays exact.
    #[test]
    fn number_attr_never_switches_a_large_integer_to_scientific_notation() {
        assert_eq!(number_attr(1_234_567.0), "1234567");
    }

    #[test]
    fn number_attr_prints_a_fractional_value_with_its_decimal() {
        assert_eq!(number_attr(0.1), "0.1");
        assert_eq!(number_attr(179.7), "179.7");
    }

    #[test]
    fn free_entry_inputs_match_pythons_frozenset() {
        for input in ["text", "input", "textarea", "number"] {
            assert!(is_free_entry_input(input), "{input} should be free-entry");
        }
        for input in [
            "select",
            "multiselect",
            "checkbox",
            "slider",
            "date",
            "daterange",
        ] {
            assert!(
                !is_free_entry_input(input),
                "{input} should not be free-entry"
            );
        }
    }
}

#[cfg(test)]
mod buttons_form_tests {
    use super::*;

    fn style(overflow: &str, presets_display: &str) -> VariablesStyleDoc {
        let font = serde_json::json!({"size": 11.0, "family": "Inter", "color": "#555", "weight": "400", "case": "none"});
        serde_json::from_value(serde_json::json!({
            "position": "top",
            "title_inline_band_bottom_pad": 8.0,
            "gap": 10.0,
            "title_inline_title_max_width": 0.0,
            "font": font,
            "label": {"font": font},
            "value": {"font": font},
            "placeholder": {"font": font},
            "container_height": 36.0,
            "border": {"color": "#ccc"},
            "control_gap": 8.0,
            "presets": ["today", "last_5_days", "last_1_months", "year_to_date", "previous_3_months"],
            "presets_display": presets_display,
            "overflow": overflow,
            "input": {
                "height": 32.0,
                "border": {"radius": 4.0},
                "background": "#fff",
                "selected_color": "#111", "selected_tint": 0.12, "selected_weight": 500,
                "padding": {"top": 4.0, "right": 8.0, "bottom": 4.0, "left": 8.0},
                "widths": {"text": 140.0, "number": 100.0, "range": 120.0, "slider_value_min": 30.0, "checkbox": 16.0, "daterange": 160.0},
                "range": {"default_min": 0.0, "default_max": 100.0, "default_step": 1.0},
            },
        }))
        .expect("style deserializes")
    }

    fn fonts_dir() -> String {
        concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        )
        .to_string()
    }

    fn draw(
        defs: serde_json::Value,
        values: serde_json::Value,
        width: f64,
        vs: &VariablesStyleDoc,
    ) -> (String, Vec<diagnostic::Diagnostic>) {
        let docs: Vec<(String, VariableDoc)> =
            deserialize_ordered(defs).expect("variables deserialize");
        let refs: Vec<(&str, &VariableDoc)> = docs.iter().map(|(n, d)| (n.as_str(), d)).collect();
        let values = values.as_object().expect("an object").clone();
        diagnostic::collect(|| {
            let controls = resolve_controls(&refs, &values, None, vs, "none", &serde_json::Map::new(), &[]).unwrap();
            render_variables_strip_svg(&controls, width, vs, "start", "variables", &[], &fonts_dir())
                .unwrap()
                .0
        })
    }

    fn group_by() -> serde_json::Value {
        serde_json::json!({"group_by": {
            "input": "radio", "visible": true,
            "options": {"static": ["Region", "Sub Region", "Manager"]}
        }})
    }

    #[test]
    fn radio_draws_one_link_segment_per_option_and_marks_the_selected() {
        let (svg, warnings) = draw(
            group_by(),
            serde_json::json!({"group_by": "Sub Region"}),
            1000.0,
            &style("menu", "menu"),
        );
        assert!(warnings.is_empty());
        assert!(svg.contains(r#"data-dbt-form="buttons""#));
        assert_eq!(svg.matches("data-dbt-segment=").count(), 3);
        assert!(svg.contains(r#"data-dbt-segment="Region" href="?group_by=Region""#));
        // The selected segment of an optional variable links to the unset state.
        assert!(svg.contains(r#"data-dbt-segment="Sub Region" href="?group_by=""#));
        assert_eq!(svg.matches(r#"aria-checked="true""#).count(), 1);
        assert!(svg.contains(r#"<g data-dbt-buttons="true" opacity="0">"#));
    }

    #[test]
    fn a_required_selected_segment_has_no_link() {
        let defs = serde_json::json!({"group_by": {
            "input": "radio", "visible": true, "required": true,
            "options": {"static": ["A", "B"]}
        }});
        let (svg, _) = draw(defs, serde_json::json!({"group_by": "A"}), 1000.0, &style("menu", "menu"));
        assert!(svg.contains(r#"data-dbt-segment="A" role="radio""#));
        assert!(svg.contains(r#"data-dbt-segment="B" href="?group_by=B""#));
    }

    #[test]
    fn a_buttons_wider_than_its_slot_draws_the_field_and_warns() {
        let (svg, warnings) = draw(group_by(), serde_json::json!({}), 120.0, &style("menu", "menu"));
        assert!(!svg.contains("data-dbt-segment="));
        assert!(!svg.contains("data-dbt-form"));
        assert_eq!(warnings.len(), 1);
        assert_eq!(warnings[0].code, "WARN-VARIABLE-BUTTONS-OVERFLOW");
    }

    #[test]
    fn wrap_flows_the_buttons_onto_further_rows_without_a_warning() {
        let (svg, warnings) = draw(group_by(), serde_json::json!({}), 200.0, &style("wrap", "menu"));
        assert!(warnings.is_empty());
        assert_eq!(svg.matches("data-dbt-segment=").count(), 3);
        assert!(svg.matches(r#"data-dbt-field="buttons""#).count() > 1);
    }

    #[test]
    fn preset_buttons_uses_ticker_labels_and_keeps_long_ones_where_none_exists() {
        let defs = serde_json::json!({"span": {"input": "daterange", "visible": true}});
        let (svg, _) = draw(defs, serde_json::json!({}), 1000.0, &style("menu", "buttons"));
        for label in [">1D<", ">5D<", ">MTD<", ">YTD<"] {
            assert!(svg.contains(label), "{label}");
        }
        assert!(svg.contains(">Previous 3 months<"));
        assert!(svg.contains(r#"href="?span=last_5_days""#));
    }

    fn style_with_selected(background: Option<&str>, color: &str) -> VariablesStyleDoc {
        let mut vs = style("menu", "menu");
        vs.input.selected_background = background.map(str::to_string);
        vs.input.selected_color = color.to_string();
        vs
    }

    #[test]
    fn a_theme_selected_fill_and_text_replace_the_tint() {
        let vs = style_with_selected(Some("#111111"), "#fefefe");
        let (svg, _) = draw(group_by(), serde_json::json!({"group_by": "Region"}), 1000.0, &vs);
        assert!(svg.contains(r##"<path d="M"##) && svg.contains(r##"fill="#111111"/>"##));
        assert!(!svg.contains("fill-opacity"));
        assert!(svg.contains(r##"fill="#fefefe">Region</text>"##));
        assert!(svg.contains(r#"data-dbt-inverse="true""#));
    }

    #[test]
    fn without_a_theme_fill_the_selected_segment_is_a_tint() {
        let (svg, _) = draw(group_by(), serde_json::json!({"group_by": "Region"}), 1000.0, &style("menu", "menu"));
        assert!(svg.contains("fill-opacity"));
        assert!(!svg.contains("data-dbt-inverse"));
    }

    #[test]
    fn a_right_aligned_buttons_shows_its_value_at_the_buttons_right_edge() {
        let vs = style("menu", "menu");
        let docs: Vec<(String, VariableDoc)> = deserialize_ordered(group_by()).unwrap();
        let refs: Vec<(&str, &VariableDoc)> = docs.iter().map(|(n, d)| (n.as_str(), d)).collect();
        let values = serde_json::json!({"group_by": "Region"}).as_object().unwrap().clone();
        let controls = resolve_controls(&refs, &values, None, &vs, "none", &serde_json::Map::new(), &[]).unwrap();
        let (svg, _) =
            render_variables_strip_svg(&controls, 1000.0, &vs, "end", "variables", &[], &fonts_dir())
                .unwrap();
        let attr = |tag: &str, name: &str| -> f64 {
            let from = svg.find(tag).expect(tag);
            let rest = &svg[from..];
            let at = rest.find(&format!(r#" {name}=""#)).unwrap() + name.len() + 3;
            rest[at..].split('"').next().unwrap().parse().unwrap()
        };
        let buttons_right = attr(r#"data-dbt-field="buttons""#, "x") + attr(r#"data-dbt-field="buttons""#, "width");
        let value_at = svg.find("data-dbt-buttons-value").unwrap();
        let value_rect = &svg[value_at..];
        let rect_from = value_rect.find(r#"data-dbt-field="input""#).unwrap();
        let rect = &value_rect[rect_from..];
        let num = |name: &str| -> f64 {
            let at = rect.find(&format!(r#" {name}=""#)).unwrap() + name.len() + 3;
            rect[at..].split('"').next().unwrap().parse().unwrap()
        };
        assert_eq!(num("x") + num("width"), buttons_right);
    }

    #[test]
    fn a_left_aligned_buttons_value_follows_its_label_by_one_word_space() {
        let vs = style("menu", "menu");
        let (svg, _) = draw(group_by(), serde_json::json!({"group_by": "Region"}), 1000.0, &vs);
        let xs: Vec<f64> = svg
            .split("<text ")
            .skip(1)
            .map(|t| {
                let at = t.find(r#"x=""#).unwrap() + 3;
                t[at..].split('"').next().unwrap().parse().unwrap()
            })
            .collect();
        let expected = text_width("Group by:", 11.0, &fonts_dir()) + text_width(" ", 11.0, &fonts_dir());
        assert!((xs[1] - xs[0] - expected).abs() < 0.6, "{xs:?} vs {expected}");
    }

    #[test]
    fn a_radio_with_query_options_keeps_the_field() {
        let defs = serde_json::json!({"g": {
            "input": "radio", "visible": true, "options": {"query": "opts"}
        }});
        let vs = style("menu", "menu");
        let docs: Vec<(String, VariableDoc)> = deserialize_ordered(defs).unwrap();
        let refs: Vec<(&str, &VariableDoc)> = docs.iter().map(|(n, d)| (n.as_str(), d)).collect();
        let mut datasets = HashMap::new();
        datasets.insert(
            "g".to_string(),
            vec![serde_json::json!({"value": "A"}).as_object().unwrap().clone()],
        );
        let controls = resolve_controls(
            &refs, &serde_json::Map::new(), Some(&datasets), &vs, "none", &serde_json::Map::new(), &[],
        )
        .unwrap();
        assert_eq!(controls[0].option_values, vec!["A".to_string()]);
        assert!(controls[0].segments.is_empty());
    }

    #[test]
    fn the_overflow_warning_names_where_the_variable_is_written() {
        let (_, warnings) = draw(group_by(), serde_json::json!({}), 120.0, &style("menu", "menu"));
        assert_eq!(warnings[0].params["path"], serde_json::json!("variables.group_by"));
    }

    #[test]
    fn a_committed_preset_selects_its_segment_and_links_by_name() {
        let _locale = ::bind::time_locale::en_us_scope();
        let defs = serde_json::json!({"span": {"input": "daterange", "visible": true}});
        let vs = style("menu", "buttons");
        let docs: Vec<(String, VariableDoc)> = deserialize_ordered(defs).unwrap();
        let refs: Vec<(&str, &VariableDoc)> = docs.iter().map(|(n, d)| (n.as_str(), d)).collect();
        let values = serde_json::json!({"span": ["2026-04-20", "2026-10-20"]})
            .as_object().unwrap().clone();
        let presets = serde_json::json!({"span": "last_5_days"}).as_object().unwrap().clone();
        let controls = resolve_controls(&refs, &values, None, &vs, "none", &presets, &[]).unwrap();
        let (svg, _) =
            render_variables_strip_svg(&controls, 1000.0, &vs, "start", "variables", &[], &fonts_dir())
                .unwrap();
        assert_eq!(svg.matches(r#"aria-checked="true""#).count(), 1);
        assert!(svg.contains(r#"data-dbt-segment="last_5_days" href="?span=""#));
        assert!(svg.contains(r#"data-dbt-segment="today" href="?span=today""#));
        assert!(!svg.contains("2026-04"));
    }

    #[test]
    fn an_unresolved_preset_value_is_refused_by_name() {
        let defs = serde_json::json!({"span": {"input": "daterange", "visible": true}});
        let vs = style("menu", "buttons");
        let docs: Vec<(String, VariableDoc)> = deserialize_ordered(defs).unwrap();
        let refs: Vec<(&str, &VariableDoc)> = docs.iter().map(|(n, d)| (n.as_str(), d)).collect();
        let values = serde_json::json!({"span": "last_7_days"}).as_object().unwrap().clone();
        let result = resolve_controls(&refs, &values, None, &vs, "none", &serde_json::Map::new(), &[]);
        assert!(result.is_err());
    }

    #[test]
    fn the_preset_grammar_matches_pythons() {
        for bad in ["last_0_days", "last_007_days", "last_+3_days", "last_3_fiscal_days", "this_fiscal_week", "fiscal_week_to_date"] {
            assert!(parse_preset(bad).is_err(), "{bad}");
        }
        for good in ["last_3_days", "last_2_fiscal_quarters", "this_fiscal_year", "fiscal_year_to_date", "year_to_date", "previous_1_months"] {
            assert!(parse_preset(good).is_ok(), "{good}");
        }
    }

    #[test]
    fn one_link_law_encodes_a_multiselect_the_way_python_commits_it() {
        let values = serde_json::json!({"m": ["a", "b"], "x": 1}).as_object().unwrap().clone();
        let link = variable_link(&values, &serde_json::Map::new(), &[], "x", "2").unwrap();
        assert_eq!(link, "?m=%5B%22a%22%2C+%22b%22%5D&x=2");
    }

    #[test]
    fn menu_display_keeps_a_daterange_a_field() {
        let defs = serde_json::json!({"span": {"input": "daterange", "visible": true}});
        let (svg, _) = draw(defs, serde_json::json!({}), 1000.0, &style("menu", "menu"));
        assert!(!svg.contains("data-dbt-segment="));
    }
}
