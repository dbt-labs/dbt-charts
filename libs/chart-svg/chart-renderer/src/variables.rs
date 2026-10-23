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

use serde::Deserialize;
use serde_json::Value;

use crate::board::{px, translate_group};
use display_text::case::apply_case;
use crate::conditions::eval_bool_condition;
use crate::diagnostic::py_str;
use crate::time_locale::active_time_locale;
use crate::svg::{num_full};
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
}

impl VariablesStyleDoc {
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
}

fn default_input() -> String {
    "auto".to_string()
}

impl VariableDoc {
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

const SLIDER_MAX_DISTINCT: usize = 200;
const SLIDER_MAX_RANGE_STEPS: f64 = 1000.0;

struct RefinedType {
    input_type: String,
    slider_min: Option<f64>,
    slider_max: Option<f64>,
    slider_step: Option<f64>,
}

/// `variable_input_refinement.py::refine_input_type_from_data`.
fn refine_input_type_from_data(
    current_input: &str,
    input_auto_detected: bool,
    option_values: &[String],
) -> RefinedType {
    let unchanged = || RefinedType {
        input_type: current_input.to_string(),
        slider_min: None,
        slider_max: None,
        slider_step: None,
    };
    if !input_auto_detected || option_values.is_empty() {
        return unchanged();
    }
    if looks_like_dates(option_values) {
        return RefinedType {
            input_type: "datepicker".to_string(),
            slider_min: None,
            slider_max: None,
            slider_step: None,
        };
    }
    if looks_like_booleans(option_values) {
        return RefinedType {
            input_type: "checkbox".to_string(),
            slider_min: None,
            slider_max: None,
            slider_step: None,
        };
    }
    if option_values.len() <= SLIDER_MAX_DISTINCT {
        if let Some(parsed) = try_parse_numbers(option_values) {
            let min_val = parsed.iter().cloned().fold(f64::INFINITY, f64::min);
            let max_val = parsed.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
            let val_range = max_val - min_val;
            if val_range > 0.0 {
                let all_ints = parsed.iter().all(|v| v.fract() == 0.0);
                let step = if all_ints {
                    1.0
                } else {
                    (val_range / 100.0 * 1e6).round() / 1e6
                };
                if val_range / step <= SLIDER_MAX_RANGE_STEPS {
                    return RefinedType {
                        input_type: "slider".to_string(),
                        slider_min: Some(min_val),
                        slider_max: Some(max_val),
                        slider_step: Some(step),
                    };
                }
            }
        }
    }
    unchanged()
}

fn looks_like_dates(values: &[String]) -> bool {
    if values.len() < 2 {
        return false;
    }
    let matches = values.iter().filter(|v| is_iso_date_prefix(v)).count();
    matches as f64 / values.len() as f64 >= 0.8
}

fn is_iso_date_prefix(v: &str) -> bool {
    if v.len() < 10 || !v.is_char_boundary(10) {
        return false;
    }
    if parse_iso_date(&v[..10]).is_none() {
        return false;
    }
    let bytes = v.as_bytes();
    if bytes.len() > 10 {
        let next = bytes[10];
        if next != b'T' && next != b' ' && next != b'\t' {
            return false;
        }
    }
    true
}

fn is_leap_year(y: i32) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
}

fn parse_iso_date(s: &str) -> Option<(i32, u32, u32)> {
    let bytes = s.as_bytes();
    if bytes.len() != 10 || bytes[4] != b'-' || bytes[7] != b'-' {
        return None;
    }
    let y: i32 = s[0..4].parse().ok()?;
    let m: u32 = s[5..7].parse().ok()?;
    let d: u32 = s[8..10].parse().ok()?;
    let days_in_month = match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if is_leap_year(y) {
                29
            } else {
                28
            }
        }
        _ => return None,
    };
    if d < 1 || d > days_in_month {
        return None;
    }
    Some((y, m, d))
}

fn looks_like_booleans(values: &[String]) -> bool {
    if values.len() != 2 {
        return false;
    }
    let pair: HashSet<String> = values.iter().map(|v| v.trim().to_lowercase()).collect();
    let candidates: [[&str; 2]; 3] = [["true", "false"], ["yes", "no"], ["0", "1"]];
    candidates.iter().any(|c| {
        let candidate: HashSet<String> = c.iter().map(|s| s.to_string()).collect();
        candidate == pair
    })
}

fn try_parse_numbers(values: &[String]) -> Option<Vec<f64>> {
    let mut out = Vec::with_capacity(values.len());
    for v in values {
        let n: f64 = v.trim().parse().ok()?;
        if !n.is_finite() {
            return None;
        }
        out.push(n);
    }
    Some(out)
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

struct InputTraits {
    sizing: &'static str,
    ornament: &'static str,
    unset: &'static str,
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
        },
        "select" | "multiselect" | "radio" => InputTraits {
            sizing: "content",
            ornament: "arrow",
            unset: "chooser",
        },
        "date" | "datepicker" => InputTraits {
            sizing: "content",
            ornament: "calendar",
            unset: "date",
        },
        "daterange" => InputTraits {
            sizing: "daterange",
            ornament: "calendar",
            unset: "range",
        },
        "number" => InputTraits {
            sizing: "number",
            ornament: "none",
            unset: "none",
        },
        "slider" | "range" => InputTraits {
            sizing: "slider",
            ornament: "none",
            unset: "none",
        },
        "checkbox" => InputTraits {
            sizing: "checkbox",
            ornament: "none",
            unset: "none",
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
}

pub struct VariablesLayout {
    pub boxes: Vec<ControlBox>,
    pub height: f64,
    pub rows: i64,
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
    let measurer = mdsvg::fonts::cached_measurer(&path, 0, None);
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
    measure::centered_baseline_offset(&mdsvg::fonts::cached_measurer(&path, 0, None), font_size)
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
        });
    }
    let gap = vs.gap;
    let row_height = vs.container_height;
    let available = width.max(1.0);
    let font_size = vs
        .font
        .size
        .ok_or("style.variables.font.size must be configured")?;

    let mut boxes: Vec<ControlBox> = Vec::new();
    let mut x = 0.0_f64;
    let mut row: i64 = 0;
    for spec in specs {
        let label_width = text_width(&field_label(&spec.label)?, font_size, fonts_dir);
        let control_width = label_width
            + vs.control_gap
            + input_width(spec, vs, font_size, fonts_dir)?
            + ornament_gap(&spec.input, font_size)?
            + ornament_width(&spec.input, font_size)?;
        if !boxes.is_empty() && x + control_width > available + WRAP_EPSILON {
            row += 1;
            x = 0.0;
        }
        boxes.push(ControlBox {
            name: spec.name.clone(),
            input: spec.input.clone(),
            label: spec.label.clone(),
            value: spec.value.clone(),
            x,
            y: row as f64 * (row_height + gap),
            width: control_width,
            height: row_height,
            label_width,
        });
        x += control_width + gap;
    }
    let rows = row + 1;
    Ok(VariablesLayout {
        boxes,
        height: rows as f64 * row_height + (rows - 1) as f64 * gap,
        rows,
    })
}

#[cfg(test)]
mod layout_tests {
    use super::*;

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
            "input": {
                "height": 28.0,
                "border": {"radius": 4.0},
                "background": "#fff",
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
    fn a_wide_control_wraps_to_a_second_row() {
        let vs = fixture_style();
        let specs = vec![
            ControlSpec {
                name: "a".into(),
                input: "text".into(),
                label: "A".into(),
                value: "value".into(),
            },
            ControlSpec {
                name: "b".into(),
                input: "text".into(),
                label: "B".into(),
                value: "value".into(),
            },
        ];
        // A narrow strip forces the second control onto its own row.
        let layout = lay_out_variables(&specs, 50.0, &vs, &fonts_dir()).unwrap();
        assert_eq!(layout.rows, 2);
        assert_eq!(layout.boxes[0].y, 0.0);
        assert_eq!(layout.boxes[1].y, vs.container_height + vs.gap);
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
}

impl ResolvedControl {
    fn spec(&self) -> ControlSpec {
        ControlSpec {
            name: self.name.clone(),
            input: self.input.clone(),
            label: self.label.clone(),
            value: self.display_value.clone(),
        }
    }
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
    parse_iso_date(iso).map_or_else(|| Ok(iso.to_string()), fmt)
}

/// `variables_resolve.py::format_daterange_label`.
fn format_daterange_label(start_iso: &str, end_iso: &str) -> Result<String, String> {
    let (Some(s), Some(e)) = (parse_iso_date(start_iso), parse_iso_date(end_iso)) else {
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
            Ok(selected.iter().map(py_str).collect::<Vec<_>>().join(&word("controls.list_join")?))
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
                out.push_str(&crate::svg::py_float_str(f))
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

/// `variables_resolve.py::resolve_controls`. `datasets` mirrors Python's
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
            enabled: datasets.is_none()
                || eval_bool_condition(
                    &var_def.enabled,
                    true,
                    current_values,
                    "board variable enabled",
                )?,
            slider_min: low,
            slider_max: high,
            slider_step: step,
            can_unset: !var_def.required.unwrap_or(false),
            placeholder: var_def.placeholder.clone(),
            hint,
            authored_min: var_def.min,
            authored_max: var_def.max,
            authored_step: var_def.step,
            defined_in_other_file: var_def.defined_in_other_file,
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
    let controls = resolve_controls(variable_defs, variable_values, None, vs, label_case)?;
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
    title_fonts: &mdsvg::fonts::FontSlots,
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

/// `sizing.py::should_use_title_inline_band`.
#[allow(clippy::too_many_arguments)]
pub fn should_use_title_inline_band(
    title: &str,
    visible_variables: &[(&str, &VariableDoc)],
    content_width: f64,
    title_style: Option<&mdsvg::style::Style>,
    title_fonts: Option<&mdsvg::fonts::FontSlots>,
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
        .boxes
        .iter()
        .map(|b| b.x + b.width)
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
    title_fonts: mdsvg::fonts::FontSlots,
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
    title_fonts: mdsvg::fonts::FontSlots,
    title_case: Option<&str>,
    title_color: Option<&str>,
    title_min_height: f64,
    title_authored_attrs: &str,
    variables_path: &str,
    datasets: Option<&HashMap<String, Vec<serde_json::Map<String, Value>>>>,
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
    let inline_title_line_box =
        crate::title::title_line_box(title_style.clone(), title_fonts.clone(), measured_title_h)?;

    let label_case = vs
        .label
        .font
        .case
        .as_deref()
        .ok_or("style.variables.label.font.case must be resolved")?;
    let controls = resolve_controls(visible_variables, variable_values, datasets, vs, label_case)?;
    let (vars_svg, vars_h) =
        render_variables_strip_svg(&controls, vars_w, vs, "end", variables_path, fonts_dir)?;

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
    fn a_set_date_displays_as_day_month_year_not_raw_iso() {
        let _locale = crate::time_locale::en_us_scope();
        let doc: VariableDoc = serde_json::from_value(serde_json::json!({"visible": true})).unwrap();
        assert_eq!(
            format_variable_display_value(&doc, &serde_json::json!("2026-09-21"), "date").unwrap(),
            "21 Sep 2026"
        );
    }

    #[test]
    fn a_daterange_with_one_open_end_names_the_other() {
        let _locale = crate::time_locale::en_us_scope();
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
        let _locale = crate::time_locale::en_us_scope();
        assert_eq!(
            format_daterange_label("2024-03-05", "2024-03-12").unwrap(),
            "5\u{2013}12 Mar 2024"
        );
    }

    #[test]
    fn format_daterange_label_cross_year_spells_both_dates() {
        let _locale = crate::time_locale::en_us_scope();
        assert_eq!(
            format_daterange_label("2023-12-30", "2024-01-02").unwrap(),
            "30 Dec 2023 \u{2013} 2 Jan 2024"
        );
    }

    #[test]
    fn format_date_label_spells_the_date_and_passes_a_non_iso_string_through() {
        let _locale = crate::time_locale::en_us_scope();
        assert_eq!(format_date_label("2026-09-21").unwrap(), "21 Sep 2026");
        assert_eq!(format_date_label("last week").unwrap(), "last week");
    }

    #[test]
    fn date_labels_take_their_month_names_from_the_board_time_locale() {
        crate::time_locale::with_time_locale(&crate::time_locale::de_de(), || {
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
        return layout.boxes.iter().map(|b| (b.y.to_bits(), 0.0)).collect();
    }
    let mut ends: HashMap<u64, f64> = HashMap::new();
    for b in &layout.boxes {
        ends.insert(b.y.to_bits(), b.x + b.width);
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
) -> Result<Vec<String>, String> {
    let fs = font_size(vs)?;
    let value_font = &vs.value.font;
    let family = value_font.family.as_deref().unwrap_or_default();
    let text_x = x + vs.input.padding.left;
    let baseline = y + height / 2.0 + centered_baseline_offset(family, fs, fonts_dir);
    let color = if hint {
        vs.placeholder.font.color.as_deref().unwrap_or_default()
    } else {
        value_font.color.as_deref().unwrap_or_default()
    };

    let mut parts = vec![
        field_rect(x, y, width, height, vs),
        format!(
            r#"<text x="{}" y="{}" font-size="{}" font-family="{}" fill="{}">{}</text>"#,
            pxi(text_x),
            pxi(baseline),
            pxi(fs),
            xml_escape_attr(family),
            xml_escape_attr(color),
            xml_escape(&box_.value)
        ),
    ];
    match traits_of(&box_.input)?.ornament {
        "arrow" => parts.push(draw_arrow(x + width, y + height / 2.0, fs, vs)?),
        "calendar" => parts.push(draw_calendar(x + width, y + height / 2.0, fs, vs)?),
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
        crate::svg::py_float_str(value)
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
            r#"<text x="{}" y="{}" font-size="{}" font-family="{}" fill="{}">{}</text>"#,
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
) -> Result<Vec<String>, String> {
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
        ),
    }
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
    let field_x = x + box_.label_width + vs.control_gap;
    let field_width = (box_.width - box_.label_width - vs.control_gap).max(0.0);
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

    let mut parts = vec![format!(
        r#"<text{label_kind} x="{}" y="{}" font-size="{}" font-weight="{weight}" font-family="{}" fill="{}">{}</text>"#,
        pxi(x),
        pxi(baseline),
        pxi(fs),
        xml_escape_attr(family),
        xml_escape_attr(label_font.color.as_deref().unwrap_or_default()),
        xml_escape(&field_label(&box_.label)?), weight = xml_escape_attr(&weight)
    )];
    parts.extend(draw_field(
        control,
        box_,
        field_x,
        field_y,
        field_width,
        field_height,
        vs,
        fonts_dir,
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
        xml_escape_attr(&committed_value(&control.current)?)
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

    Ok(format!(
        r#"<g data-dbt-variable="{}"{handle} data-dbt-input="{}"{checked_attr}{disabled}{value_attr}{bounds}{unset}{options} data-dbt-x="{}" data-dbt-y="{}" data-dbt-width="{}" data-dbt-height="{}">{}</g>"#,
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
    fonts_dir: &str,
) -> Result<(String, f64), String> {
    if controls.is_empty() {
        return Ok((String::new(), 0.0));
    }
    let specs: Vec<ControlSpec> = controls.iter().map(ResolvedControl::spec).collect();
    let layout = lay_out_variables(&specs, width, vs, fonts_dir)?;
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
        )?);
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
