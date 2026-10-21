//! Custom family (`type: custom`): a minijinja SVG template draws the chart
//! body from its query rows; the shared chart frame (`frame.rs`) draws the
//! root `<svg>`, background, title and subtitle and hands the template the
//! body box inside the padding; the chart-root `link` is an `<a>` around the
//! body.
//!
//! The template is user code and its output lands inline in a page that may
//! be shared, so the output is checked against an SVG allowlist and refused
//! whole on the first violation; nothing is stripped. What ships is the
//! checked tree serialized again, never the template's own string: XML and
//! HTML disagree about comments, processing instructions and attribute-name
//! case, so a string that passes an XML check can still carry live markup to
//! a browser.
//!
//! minijinja has no attribute reflection to escape from (see `compile/src/
//! template/environment.rs`), so a template reaches exactly the names this
//! module puts in its context: `rows`, `columns`, `channels`, `options`,
//! `width`, `height`, `uid`, `style`, `variables`, `math`, the helpers
//! `text_width`, `color_for`, `href`, `raise_error`, and the `format`
//! filter. Every name is a reviewed decision; the `custom-chart` product
//! skill and `apps/docs/docs/charts/custom.md` document the contract.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, OnceLock};

use minijinja::value::{Rest, Value, ValueKind};
use minijinja::{context, AutoEscape, Environment, Error, ErrorKind, UndefinedBehavior};
use regex::Regex;
use serde::Deserialize;
use serde_json::{json, Map, Value as Json};

use crate::codes;
use crate::common::{Font, TitleStyle};
use crate::frame::{ChartFrame, FrameTitle};
use crate::diagnostic::Diagnostic;
use crate::href;
use crate::svg::{num, vega_escape_attr, xml_escape, xml_escape_attr};
use crate::text::FontBook;
use crate::Payload;

const SVG_NS: &str = "http://www.w3.org/2000/svg";
const XLINK_NS: &str = "http://www.w3.org/1999/xlink";

/// The channels a custom chart's `link:` may name as `{{ channel }}`.
const LINK_CHANNELS: &[&str] = &["x", "y", "color", "size", "shape", "theta", "value"];

/// Elements nested deeper than this fail the chart: the serializer recurses
/// once per level, and a template can nest without bound.
const MAX_DEPTH: usize = 64;

/// Attributes a browser reads as a color (the hover runtime pastes `fill` and
/// `stroke` into its tooltip markup), so their values are held to a color
/// grammar: a name, hex, a color function, `none`, `currentColor`, `url(#id)`.
const PAINT_ATTRIBUTES: &[&str] = &[
    "fill",
    "stroke",
    "stop-color",
    "flood-color",
    "lighting-color",
    "color",
];

/// Instruction budget for one template render: a runaway loop fails the
/// chart instead of holding a render worker.
const FUEL: u64 = 20_000_000;

/// Elements a template may emit. `a` carries only an `http`, `https`,
/// `mailto` or board-path href (`checked_attribute`); `script`,
/// `foreignObject`, `style`, `image` and the animation elements are out.
const ALLOWED_ELEMENTS: &[&str] = &[
    "a",
    "g",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "path",
    "text",
    "tspan",
    "textPath",
    "defs",
    "linearGradient",
    "radialGradient",
    "stop",
    "pattern",
    "clipPath",
    "mask",
    "marker",
    "symbol",
    "use",
    "title",
    "desc",
];

#[derive(Deserialize)]
struct Resolved {
    id: String,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    link: Option<String>,
    link_open: href::LinkOpen,
    template_name: String,
    template_source: String,
    channels: Map<String, Json>,
    options: Map<String, Json>,
    column_options: Vec<String>,
    category_pins: BTreeMap<String, BTreeMap<String, String>>,
    palette: Vec<String>,
    background: String,
    style: CustomStyle,
    title_style: TitleStyle,
}

#[derive(Deserialize)]
struct CustomStyle {
    title_font: Font,
    /// The board's merged style tree as plain data — the template's `style`.
    tree: Json,
}

fn diagnostic(code: &crate::diagnostic::Code, chart_id: &str, reason: String) -> Diagnostic {
    Diagnostic::new(
        code,
        [("chart_id", json!(chart_id)), ("reason", json!(reason))],
    )
}

// ── Template context ────────────────────────────────────────────────────────

fn invalid(message: impl Into<String>) -> Error {
    Error::new(ErrorKind::InvalidOperation, message.into())
}

/// Python's `str(value)` for the values a template passes a helper.
fn py_str(value: &Value) -> String {
    match value.kind() {
        ValueKind::None | ValueKind::Undefined => "None".to_string(),
        ValueKind::Bool => {
            if value.is_true() {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        _ => value.to_string(),
    }
}

/// A finite or infinite number, never a bool (Python's `_is_number`).
fn number(value: &Value) -> Option<f64> {
    if value.kind() != ValueKind::Number {
        return None;
    }
    f64::try_from(value.clone()).ok()
}

/// A whole font weight from any value the style tree carries: 400, "600", "bold".
fn font_weight(weight: Option<&Value>) -> Result<f32, Error> {
    let Some(weight) = weight else {
        return Ok(400.0);
    };
    let axis = match weight.kind() {
        ValueKind::String => {
            let text = weight.as_str().expect("string kind");
            // A numeric string is read exactly: "600.5" is no whole weight.
            text.trim().parse::<f64>().ok().or_else(|| {
                mdsvg::fonts::css_weight_axis(text)
                    .ok()
                    .flatten()
                    .map(f64::from)
            })
        }
        ValueKind::Number => number(weight),
        _ => None,
    };
    match axis {
        Some(axis) if axis.fract() == 0.0 && (1.0..=1000.0).contains(&axis) => Ok(axis as f32),
        _ => Err(invalid(format!(
            "text_width(): weight must be a whole number from 1 to 1000 like 400, or a CSS \
             keyword like bold, got {weight:?}"
        ))),
    }
}

/// Python's `math`, radians, as a plain namespace: functions and floats only,
/// so a template cannot traverse anything from them.
fn math_namespace() -> Value {
    fn float(name: &'static str, f: fn(f64) -> f64) -> Value {
        Value::from_function(move |x: f64| -> Result<Value, Error> {
            let y = f(x);
            if y.is_nan() && !x.is_nan() {
                return Err(invalid(format!("math.{name}(): math domain error")));
            }
            Ok(Value::from(y))
        })
    }
    fn integer(name: &'static str, f: fn(f64) -> f64) -> Value {
        Value::from_function(move |x: f64| -> Result<Value, Error> {
            if !x.is_finite() {
                return Err(invalid(format!(
                    "math.{name}(): cannot convert {x} to an integer"
                )));
            }
            Ok(Value::from(f(x) as i64))
        })
    }
    let mut m: BTreeMap<&str, Value> = BTreeMap::new();
    m.insert("sin", float("sin", f64::sin));
    m.insert("cos", float("cos", f64::cos));
    m.insert("tan", float("tan", f64::tan));
    m.insert("asin", float("asin", f64::asin));
    m.insert("acos", float("acos", f64::acos));
    m.insert("sqrt", float("sqrt", f64::sqrt));
    m.insert("exp", float("exp", f64::exp));
    m.insert("floor", integer("floor", f64::floor));
    m.insert("ceil", integer("ceil", f64::ceil));
    m.insert(
        "atan2",
        Value::from_function(|y: f64, x: f64| -> Result<Value, Error> {
            Ok(Value::from(y.atan2(x)))
        }),
    );
    m.insert(
        "log",
        Value::from_function(|x: f64, base: Option<f64>| -> Result<Value, Error> {
            if x <= 0.0 {
                return Err(invalid("math.log(): math domain error"));
            }
            Ok(Value::from(match base {
                None => x.ln(),
                Some(b) => x.ln() / b.ln(),
            }))
        }),
    );
    m.insert("pi", Value::from(std::f64::consts::PI));
    m.insert("tau", Value::from(std::f64::consts::TAU));
    Value::from(m)
}

/// `labels_env.format_filter`: `{{ value | format('$,.0f') }}` through
/// d3-format. `None` prints the null marker; a string passes through.
fn format_filter(value: Value, spec: String) -> Result<String, Error> {
    match value.kind() {
        ValueKind::None | ValueKind::Undefined => Ok(super::table::NULL_DISPLAY.to_string()),
        ValueKind::String => Ok(value.as_str().expect("string kind").to_string()),
        ValueKind::Number => {
            let n = number(&value).expect("number kind");
            if spec.is_empty() {
                return Ok(value.to_string());
            }
            crate::number_locale::d3_format(&spec, n)
                .map_err(invalid)
                .map(Into::into)
        }
        kind => Err(invalid(format!(
            "format: cannot format a {kind} with {spec:?}"
        ))),
    }
}

fn environment() -> Environment<'static> {
    let mut env = Environment::new();
    env.set_undefined_behavior(UndefinedBehavior::Strict);
    env.set_auto_escape_callback(|_| AutoEscape::Html);
    env.set_fuel(Some(FUEL));
    env.add_filter("format", format_filter);
    env
}

/// Compile-time syntax gate, in the engine that renders the template.
pub fn check_template(source: &str) -> Result<(), String> {
    environment()
        .template_from_str(source)
        .map(drop)
        .map_err(|e| {
            let line = e
                .line()
                .map(|l| format!(" on line {l}"))
                .unwrap_or_default();
            format!(
                "template syntax error{line}: {}",
                e.detail().unwrap_or("invalid syntax")
            )
        })
}

/// Channel and column-option values must name returned columns. No rows
/// means no columns to check against, and nothing to draw.
fn check_columns(r: &Resolved, columns: &[String]) -> Result<(), String> {
    if columns.is_empty() {
        return Ok(());
    }
    let named = r
        .channels
        .iter()
        .map(|(name, value)| (format!("channel '{name}'"), value))
        .chain(
            r.column_options
                .iter()
                .map(|name| (format!("option '{name}'"), &r.options[name])),
        );
    for (label, value) in named {
        let wanted: Vec<&str> = match value {
            Json::Null => continue,
            Json::String(s) => vec![s.as_str()],
            Json::Array(items) => items.iter().filter_map(Json::as_str).collect(),
            other => return Err(format!("{label} must name column(s); {other} is not")),
        };
        if let Some(missing) = wanted
            .iter()
            .find(|column| !columns.iter().any(|c| c == *column))
        {
            let available = columns
                .iter()
                .map(|c| format!("'{c}'"))
                .collect::<Vec<_>>()
                .join(", ");
            return Err(format!(
                "{label} names column '{missing}', but the query returned [{available}]"
            ));
        }
    }
    Ok(())
}

/// The chart's `link` for one row: every `{{ channel }}` placeholder replaced
/// by the row's value in the column that channel binds, then resolved against
/// the host like every other chart link.
fn row_href(
    template: &str,
    channels: &Map<String, Json>,
    row: &Json,
    link_context: Option<&href::LinkContext>,
) -> Result<String, String> {
    let resolved = href::resolve_channel_href_in(template, LINK_CHANNELS, |channel| {
        let column = match channels.get(channel) {
            Some(Json::String(column)) => column.as_str(),
            Some(Json::Array(_)) => {
                return Err(format!(
                    "href(): link placeholder {{{{ {channel} }}}} names a channel bound to several columns"
                ))
            }
            _ => {
                return Err(format!(
                    "href(): link placeholder {{{{ {channel} }}}} names a channel the chart does not set"
                ))
            }
        };
        href::js_string(row.get(column))
    })?;
    if resolved.contains("{{") {
        return Err(unknown_placeholder("href()", template));
    }
    let resolved = match link_context {
        Some(ctx) => href::resolve_href(&resolved, ctx),
        None => resolved,
    };
    href::checked_href(&resolved).map(str::to_string)
}

fn unknown_placeholder(site: &str, template: &str) -> String {
    format!(
        "{site}: link {template:?} keeps a {{{{ ... }}}} that names no channel; a placeholder names \
         one of {}",
        LINK_CHANNELS.join(", ")
    )
}

/// The chart-root `link` wrapped around the whole body when it names no
/// channel: a per-row link is the template's to draw with `href(row)`, and a
/// `{{ ... }}` that names no channel refuses rather than shipping as text.
/// A per-row link opens only as a page.
fn chart_anchor(r: &Resolved, payload: &Payload) -> Result<Option<String>, Diagnostic> {
    let Some(link) = r.link.as_deref() else {
        return Ok(None);
    };
    if link.contains("{{") {
        let mut named = false;
        href::resolve_channel_href_in(link, LINK_CHANNELS, |_| {
            named = true;
            Ok(String::new())
        })?;
        if !named {
            return Err(diagnostic(
                &codes::ERR_CUSTOM_RENDER,
                &r.id,
                unknown_placeholder("link", link),
            ));
        }
        if let Some(open) = r.link_open.open_attr() {
            return Err(diagnostic(
                &codes::ERR_CUSTOM_RENDER,
                &r.id,
                format!(
                    "link.open: {open} needs a link that names no channel; a per-row \
                     link is the template's own <a>, which always navigates the page"
                ),
            ));
        }
        return Ok(None);
    }
    let resolved = match &payload.link_context {
        Some(ctx) => href::resolve_href(link, ctx),
        None => link.to_string(),
    };
    href::checked_href_diagnostic(&resolved).map(|h| Some(h.to_string()))
}

fn render_template(r: &Resolved, job: &Job, fonts: &FontBook) -> Result<String, String> {
    let columns: Vec<String> = job
        .rows
        .first()
        .map(|row| row.keys().cloned().collect())
        .unwrap_or_default();
    check_columns(r, &columns)?;
    let budget = job.budget;
    let (width, height) = (job.width, job.height);
    let font_path = fonts.font_path(&job.family)?;

    let text_width = Value::from_function(
        move |text: Value, size: Value, rest: Rest<Value>| -> Result<Value, Error> {
            let Some(text) = text.as_str() else {
                return Err(invalid(format!(
                    "text_width(): text must be a string, got {text:?}"
                )));
            };
            let px = number(&size)
                .filter(|s| s.is_finite() && *s > 0.0)
                .ok_or_else(|| {
                    invalid(format!(
                        "text_width(): size must be a positive number, got {size:?}"
                    ))
                })?;
            if rest.len() > 1 {
                return Err(invalid(
                    "text_width() takes text, size and an optional weight",
                ));
            }
            let axis = font_weight(rest.first())?;
            let measurer = mdsvg::fonts::cached_measurer(&font_path, 0, Some(axis));
            Ok(Value::from(measurer.measure(text, px)))
        },
    );

    let color_for = {
        let pins = r.category_pins.clone();
        let palette = r.palette.clone();
        let assigned: Arc<Mutex<Vec<(String, String)>>> = Arc::default();
        Value::from_function(
            move |value: Value, rest: Rest<Value>| -> Result<Value, Error> {
                if rest.len() > 1 {
                    return Err(invalid("color_for() takes a value and an optional column"));
                }
                let column = match rest.first() {
                    None => None,
                    Some(c) if c.is_none() => None,
                    Some(c) => Some(c.as_str().map(str::to_string).ok_or_else(|| {
                        invalid(format!(
                            "color_for(): column must be a string or none, got {c:?}"
                        ))
                    })?),
                };
                let key = py_str(&value);
                if let Some(hex) = column.and_then(|c| pins.get(&c)).and_then(|p| p.get(&key)) {
                    return Ok(Value::from(hex.clone()));
                }
                let mut assigned = assigned.lock().expect("color_for mutex poisoned");
                if let Some((_, hex)) = assigned.iter().find(|(k, _)| *k == key) {
                    return Ok(Value::from(hex.clone()));
                }
                if assigned.len() == palette.len() {
                    return Err(invalid(format!(
                        "color_for ran out of palette colors ({}) at value {key:?}; pin the extra \
                     values under style.charts.category_colors",
                        palette.len()
                    )));
                }
                let hex = palette[assigned.len()].clone();
                assigned.push((key, hex.clone()));
                Ok(Value::from(hex))
            },
        )
    };

    let href_fn = {
        let link = r.link.clone();
        let channels = r.channels.clone();
        let link_context = job.link_context.clone();
        Value::from_function(move |row: Value| -> Result<Value, Error> {
            let Some(template) = link.as_deref() else {
                return Err(invalid(
                    "href(): this chart has no link; set `link:` on the chart",
                ));
            };
            if row.kind() != ValueKind::Map {
                return Err(invalid(format!(
                    "href(): row must be a row mapping, got {row:?}"
                )));
            }
            let row: Json = serde_json::to_value(&row).map_err(|e| invalid(e.to_string()))?;
            row_href(template, &channels, &row, link_context.as_ref())
                .map(Value::from)
                .map_err(invalid)
        })
    };

    let abort: Arc<Mutex<Option<String>>> = Arc::default();
    let raise_error = {
        let abort = abort.clone();
        Value::from_function(move |message: Value| -> Result<Value, Error> {
            let message = py_str(&message);
            *abort.lock().expect("raise_error mutex poisoned") = Some(message.clone());
            Err(invalid(message))
        })
    };

    static UID: OnceLock<Regex> = OnceLock::new();
    let uid = UID
        .get_or_init(|| Regex::new(r"[^A-Za-z0-9_-]").expect("static regex"))
        .replace_all(&r.id, "-");
    let ctx = context! {
        rows => Value::from_serialize(&job.rows),
        columns => columns,
        channels => Value::from_serialize(&r.channels),
        options => Value::from_serialize(&r.options),
        width => width,
        height => height,
        uid => format!("custom-{uid}"),
        style => Value::from_serialize(&r.style.tree),
        variables => Value::from_serialize(&job.variables),
        math => math_namespace(),
        text_width => text_width,
        color_for => color_for,
        href => href_fn,
        raise_error => raise_error,
    };
    let env = environment();
    let mut out = CappedOutput {
        buf: Vec::new(),
        cap: usize::try_from(budget).unwrap_or(usize::MAX),
        exceeded: false,
    };
    let template = env
        .template_from_str(&r.template_source)
        .map_err(|e| template_error(r, &abort, &e))?;
    // A per-row link is drawn only where the template calls `href`: a
    // template that never names it (a comment does not count) would drop
    // the link quietly. Read off the parsed template, so a spaced or
    // aliased call counts and a query with no rows still renders.
    if r.link.as_deref().is_some_and(|l| l.contains("{{"))
        && !template.undeclared_variables(true).contains("href")
    {
        return Err("link names a channel, but the template never calls href(row)".to_string());
    }
    let result = template.render_captured_to(ctx, &mut out).map(drop);
    match result {
        Ok(_) => String::from_utf8(out.buf).map_err(|e| format!("template output is not UTF-8: {e}")),
        Err(_) if out.exceeded => Err(format!(
            "template output exceeded the {budget} bytes left of the board's \
             execution.max_template_output_bytes; draw less, or raise it in dbt_charts.yml"
        )),
        Err(e) => Err(template_error(r, &abort, &e)),
    }
}

/// The template's output sink: refuses the write that would cross the
/// budget, so the render stops there instead of holding the whole string.
struct CappedOutput {
    buf: Vec<u8>,
    cap: usize,
    exceeded: bool,
}

impl std::io::Write for CappedOutput {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if self.buf.len() + bytes.len() > self.cap {
            self.exceeded = true;
            return Err(std::io::Error::other("template output budget exceeded"));
        }
        self.buf.extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

/// The chart's reason for a failed render: `raise_error()`'s own message, the
/// instruction budget, or the engine's error with the template line.
fn template_error(r: &Resolved, abort: &Mutex<Option<String>>, error: &Error) -> String {
    if let Some(message) = abort.lock().expect("raise_error mutex poisoned").take() {
        return message;
    }
    if error.kind() == ErrorKind::OutOfFuel {
        return format!("template exceeded the engine's instruction budget ({FUEL} operations)");
    }
    let line = error
        .line()
        .map(|l| format!(", line {l}"))
        .unwrap_or_default();
    format!(
        "{}: {} (template {}{line})",
        error.kind(),
        error.detail().unwrap_or(""),
        r.template_name
    )
}

// ── SVG allowlist ───────────────────────────────────────────────────────────

fn markup_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)(?P<comment><!--)|(?P<pi><\?)|(?P<cdata><!\[CDATA\[)|<!\s*(?P<declaration>DOCTYPE|ENTITY)")
            .expect("static regex")
    })
}

fn url_call_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)url\(").expect("static regex"))
}

fn local_url_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r##"(?i)url\(\s*(?:#[^)'"\s]+|'#[^)'"\s]+'|"#[^)'"\s]+")\s*\)"##)
            .expect("static regex")
    })
}

/// CSS functions besides `url()` that fetch: `image-set()` and its prefixed
/// form, `cross-fade()`, `image()`, `element()`, `src()`.
fn css_fetch_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)(-webkit-image-set|image-set|cross-fade|image|element|src)\s*\(")
            .expect("static regex")
    })
}

/// A color as CSS writes one: a keyword, `#hex`, a color function, or
/// `url(#id)` (checked separately). No quotes, angle brackets, `&` or `;`.
fn paint_value_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9#.,%()/\s+-]*$").expect("static regex"))
}

fn javascript_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)j\s*a\s*v\s*a\s*s\s*c\s*r\s*i\s*p\s*t\s*:").expect("static regex")
    })
}

/// `content` re-serialized from its checked tree, or the first violation.
/// `content` is what goes inside the root `<svg>`: any number of siblings.
pub(crate) fn checked_svg_content(content: &str) -> Result<String, String> {
    if let Some(markup) = markup_re().captures(content) {
        if let Some(declaration) = markup.name("declaration") {
            return Err(format!(
                "<!{} is not allowed",
                declaration.as_str().to_uppercase()
            ));
        }
        let kind = if markup.name("comment").is_some() {
            "a comment"
        } else if markup.name("pi").is_some() {
            "a processing instruction"
        } else {
            "a CDATA section"
        };
        return Err(format!("{kind} ({:?}) is not allowed", &markup[0]));
    }
    if let Some(depth) = nesting_past(content, MAX_DEPTH) {
        return Err(format!(
            "elements nested {depth} deep; the limit is {MAX_DEPTH} levels"
        ));
    }
    let wrapped = format!("<root xmlns=\"{SVG_NS}\" xmlns:xlink=\"{XLINK_NS}\">{content}</root>");
    let doc = roxmltree::Document::parse(&wrapped)
        .map_err(|e| format!("output is not well-formed XML: {e}"))?;
    serialize_children(doc.root_element(), 0)
}

/// The first nesting depth past `limit` that a scan of the tags finds, before
/// the parser ever sees the text. roxmltree recurses per level, so a deep
/// enough document overflows the stack of the process that parses it: this
/// scan is the law, not a fast path. Every tag that is not a closing or
/// self-closing one counts as a level, whatever its name starts with, and a
/// tag ends at the first `>` outside a quoted attribute value. Comments,
/// CDATA, processing instructions and declarations are refused before it
/// runs, so no other `<` or `>` can hide a tag.
fn nesting_past(content: &str, limit: usize) -> Option<usize> {
    let mut depth: usize = 0;
    let mut rest = content;
    while let Some(open) = rest.find('<') {
        rest = &rest[open + 1..];
        let mut quote = None;
        let Some(end) = rest.find(|c: char| match quote {
            Some(q) => {
                if c == q {
                    quote = None;
                }
                false
            }
            None if c == '"' || c == '\'' => {
                quote = Some(c);
                false
            }
            None => c == '>',
        }) else {
            break;
        };
        let tag = &rest[..end];
        rest = &rest[end + 1..];
        if tag.starts_with('/') {
            depth = depth.saturating_sub(1);
        } else if !tag.ends_with('/') {
            depth += 1;
            if depth > limit {
                return Some(depth);
            }
        }
    }
    None
}

fn serialize_children(parent: roxmltree::Node<'_, '_>, depth: usize) -> Result<String, String> {
    let mut out = String::new();
    for child in parent.children() {
        if child.is_element() {
            out.push_str(&serialize(child, depth + 1)?);
        } else if child.is_text() {
            out.push_str(&xml_escape(child.text().unwrap_or("")));
        } else {
            return Err("a comment or processing instruction is not allowed".to_string());
        }
    }
    Ok(out)
}

fn serialize(element: roxmltree::Node<'_, '_>, depth: usize) -> Result<String, String> {
    let name = element.tag_name().name();
    if element.tag_name().namespace() != Some(SVG_NS) || !ALLOWED_ELEMENTS.contains(&name) {
        return Err(format!("<{name}> is not an allowed element"));
    }
    if depth > MAX_DEPTH {
        return Err(format!("<{name}> is nested deeper than {MAX_DEPTH} levels"));
    }
    let mut attrs = String::new();
    let mut seen: Vec<&str> = Vec::new();
    for attribute in element.attributes() {
        let out_name = checked_attribute(
            name,
            attribute.namespace(),
            attribute.name(),
            attribute.value(),
        )?;
        let out_name = crate::svg::attr_name(out_name)?;
        if seen.contains(&out_name) {
            return Err(format!("attribute {out_name:?} appears twice on <{name}>"));
        }
        seen.push(out_name);
        attrs.push_str(&format!(
            " {out_name}=\"{}\"",
            xml_escape_attr(attribute.value())
        ));
    }
    let inner = serialize_children(element, depth)?;
    Ok(if inner.is_empty() {
        format!("<{name}{attrs}/>")
    } else {
        format!("<{name}{attrs}>{inner}</{name}>")
    })
}

/// The attribute's output name, or the violation that refuses it.
fn checked_attribute<'a>(
    element: &str,
    namespace: Option<&str>,
    local: &'a str,
    value: &str,
) -> Result<&'a str, String> {
    let site = format!("{local:?} on <{element}>");
    // HTML lowercases attribute names, so every name check is case-blind.
    let lowered = local.to_lowercase();
    match namespace {
        None | Some("") => {}
        Some(XLINK_NS) if lowered == "href" => {}
        Some(_) => return Err(format!("namespaced attribute {site} is not allowed")),
    }
    if lowered.starts_with("on") {
        return Err(format!("event attribute {site} is not allowed"));
    }
    // The board's own markup is what the host runtimes read: a role, an
    // aria-label the hover runtime parses, a `data-dbt-*` hook, a `role-*` or
    // `dbt-*` class. A template may not speak as the board.
    if lowered == "role" || lowered.starts_with("aria-") || lowered.starts_with("data-") {
        return Err(format!(
            "attribute {site} is reserved for the board's own markup"
        ));
    }
    if lowered == "class"
        && value
            .split_whitespace()
            .any(|token| token.starts_with("role-") || token.starts_with("dbt-"))
    {
        return Err(format!(
            "class {value:?} on <{element}> uses a prefix reserved for the board's own markup (role-, dbt-)"
        ));
    }
    if lowered == "href" {
        if element == "a" {
            href::checked_href(value).map_err(|_| {
                format!(
                    "href {value:?} on <a> must be a board path or an http, https or mailto URL"
                )
            })?;
        } else if !value.starts_with('#') {
            return Err(format!(
                "href {value:?} on <{element}> must be a #id reference"
            ));
        }
    }
    if javascript_re().is_match(value) {
        return Err(format!("javascript: in {site} is not allowed"));
    }
    if value.contains('\\') {
        return Err(format!("a backslash (CSS escape) in {site} is not allowed"));
    }
    if let Some(fetch) = css_fetch_re().captures(value) {
        return Err(format!("{}() in {site} is not allowed", &fetch[1]));
    }
    if url_call_re().find_iter(value).count() != local_url_re().find_iter(value).count() {
        return Err(format!("url() in {site} must be url(#id)"));
    }
    if PAINT_ATTRIBUTES.contains(&lowered.as_str()) && !paint_value_re().is_match(value) {
        return Err(format!(
            "paint attribute {site} must be a color, none, currentColor or url(#id), got {value:?}"
        ));
    }
    Ok(if lowered == "href" { "href" } else { local })
}

// ── Render ──────────────────────────────────────────────────────────────────

/// Everything one template render reads beyond the resolved chart.
struct Job {
    rows: Vec<Map<String, Json>>,
    variables: Map<String, Json>,
    link_context: Option<href::LinkContext>,
    /// The board's font family list, which `text_width()` measures with.
    family: String,
    width: f64,
    height: f64,
    /// What the board's output budget has left for this chart.
    budget: u64,
}

/// The fonts the resolved custom chart always carries (title, subtitle and the
/// body text the wrapper paints): a missing slot would fall back to a default.
fn require_font<'a>(font: &'a Font, what: &str) -> Result<(&'a str, f64, &'a str), String> {
    let absent = |field: &str| format!("custom: {what}.{field} absent");
    let size = font.size.ok_or_else(|| absent("size"))?;
    let family = font.family.as_deref().ok_or_else(|| absent("family"))?;
    let color = font.color.as_deref().ok_or_else(|| absent("color"))?;
    Ok((family, size, color))
}

/// The chart in the shared frame: root `<svg>`, background and title band are
/// the frame's, fitted with no ink; the template's checked output fills the
/// body box.
pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    let r: Resolved = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("custom: resolved payload: {e}"))?;
    require_font(&r.style.title_font, "style.title_font")?;
    if r.subtitle.is_some() {
        let subtitle = r.title_style.subtitle.as_ref().map(|s| &s.font);
        require_font(subtitle.ok_or("custom: title_style.subtitle absent")?, "title_style.subtitle.font")?;
    }
    let body_font: Font = serde_json::from_value(r.style.tree["font"].clone())
        .map_err(|e| format!("custom: style.font: {e}"))?;
    let (body_family, body_size, body_color) = require_font(&body_font, "style.font")?;
    let frame = ChartFrame::of(payload, &r.background);
    let title = FrameTitle::new(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        Some(&r.style.title_font),
        Some(&r.title_style),
        frame.avail_w(),
        fonts,
    )?;
    // Too small a box is the chart's sizing, not an engine gap.
    let m = frame.fit(&title, |_, _, _| {});
    if m.plot_w <= 0.0 || m.plot_h <= 0.0 {
        let reason = format!("a chart box {}×{} too small for its content", frame.w, frame.h);
        return Err(diagnostic(&codes::ERR_CUSTOM_RENDER, &r.id, reason));
    }
    let origin = frame.origin(&m);
    let anchor = chart_anchor(&r, payload)?;
    let family = payload
        .board_style
        .as_ref()
        .and_then(|b| b.font.clone())
        .ok_or("custom: board_style.font absent")?;
    let job = Job {
        rows: payload.rows.clone(),
        variables: payload.variables.clone(),
        link_context: payload.link_context.clone(),
        family,
        width: m.plot_w,
        height: m.plot_h,
        budget: payload
            .max_template_output_bytes
            .ok_or("custom: payload.max_template_output_bytes missing")?,
    };
    let content = render_template(&r, &job, fonts)
        .map_err(|reason| diagnostic(&codes::ERR_CUSTOM_RENDER, &r.id, reason))?;
    let content = checked_svg_content(&content)
        .map_err(|reason| diagnostic(&codes::ERR_CUSTOM_SVG, &r.id, reason))?;
    // The id is the box itself: equal ids always name equal shapes.
    let clip_id = format!("custom-clip-{}-{}", num(m.plot_w), num(m.plot_h));
    let mut svg = String::new();
    frame.open(&mut svg);
    svg.push_str(&format!(
        "<defs><clipPath id=\"{clip_id}\"><rect width=\"{}\" height=\"{}\"/></clipPath></defs>",
        num(m.plot_w),
        num(m.plot_h),
    ));
    title.paint(&mut svg, m.title_pos, origin);
    svg.push_str(&format!(
        "{}<g transform=\"translate({}, {})\" clip-path=\"url(#{clip_id})\" font-family=\"{}\" font-size=\"{}\" fill=\"{}\">{content}</g>{}</svg>",
        href::anchor_open(r.link_open.anchor(anchor.as_deref())),
        num(origin.0),
        num(origin.1),
        vega_escape_attr(body_family),
        num(body_size),
        xml_escape_attr(body_color),
        href::anchor_close(r.link_open.anchor(anchor.as_deref())),
    ));
    Ok(svg)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    const FAMILY: &str = "'Inter Variable', Inter, system-ui, sans-serif";
    const BODY_PAINT: &str =
        "font-family=\"'Inter Variable', Inter, system-ui, sans-serif\" font-size=\"12\" fill=\"#222222\"";

    const CLIP: &str = "clip-path=\"url(#custom-clip-300-176)\"";

    fn payload(svg: &str, patch: impl FnOnce(&mut Json)) -> Payload {
        let mut resolved = json!({
            "link_open": "page",
            "id": "c",
            "title": "Quota",
            "subtitle": null,
            "link": null,
            "template_name": "inline svg:",
            "template_source": svg,
            "channels": {"x": "rep"},
            "options": {"fill": "#ff0000"},
            "column_options": [],
            "category_pins": {"rep": {"Ana": "#123456"}},
            "palette": ["#111111", "#222222"],
            "style": {
                "title_font": {"size": 14.0, "weight": "600", "family": FAMILY, "color": "#222222", "case": "none"},
                "tree": {"font": {"family": FAMILY, "size": 12.0, "color": "#222222"}, "muted": "#888888"},
            },
            "title_style": {
                "font": {"size": 14.0, "weight": "600", "family": FAMILY, "color": "#222222", "case": "none"},
                "subtitle": {"font": {"size": 11.0, "color": "#666666", "family": FAMILY}},
                "position": {"offset": null},
            },
            "background": "#ffffff",
        });
        patch(&mut resolved);
        serde_json::from_value(json!({
            "resolved": resolved,
            "rows": [{"rep": "Ana", "n": 1.5}, {"rep": "Ben", "n": 2}],
            "width": 300.0,
            "height": 200.0,
            "background": null,
            "fonts_dir": format!("{}/../../../src/dbt_charts/core/render/fonts", env!("CARGO_MANIFEST_DIR")),
            "padding": null,
            "board_style": { "text_mark_font": {"family": "sans-serif", "size": 11.0},
                "font": FAMILY,
                "format_locale": crate::number_locale::en_us_wire(),
                "time_locale": crate::time_locale::en_us_wire(),
            },
            "variables": {"region": "North"},
            "link_context": null,
            "max_template_output_bytes": 1_000_000,
        }))
        .expect("payload parses")
    }

    #[test]
    fn renders_rows_below_the_title() {
        let svg = render(
            &payload("{% for r in rows %}<text y=\"{{ loop.index * 10 }}\">{{ r.rep }}: {{ r.n }}</text>{% endfor %}", |_| {}),
            &test_fonts(),
        )
        .unwrap();
        assert!(
            svg.starts_with(
                "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"300\" height=\"200\""
            ),
            "{svg}"
        );
        assert!(
            svg.contains("data-authored-kind=\"title\">Quota</text>"),
            "{svg}"
        );
        // Vega's title band: 14px title + the 10px default offset.
        assert!(svg.contains(&format!("<g transform=\"translate(0, 24)\" {CLIP} {BODY_PAINT}><text y=\"10\">Ana: 1.5</text><text y=\"20\">Ben: 2</text></g>")), "{svg}");
    }

    #[test]
    fn the_wrapper_clips_the_template_to_its_drawing_box() {
        let svg = render(&payload("<rect y=\"-30\" width=\"5\" height=\"5\"/>", |_| {}), &test_fonts()).unwrap();
        assert!(
            svg.contains("<defs><clipPath id=\"custom-clip-300-176\"><rect width=\"300\" height=\"176\"/></clipPath></defs>"),
            "{svg}"
        );
        assert!(
            svg.contains(&format!("<g transform=\"translate(0, 24)\" {CLIP} {BODY_PAINT}>")),
            "{svg}"
        );
    }

    #[test]
    fn the_wrapper_paints_the_board_text_defaults() {
        let svg = render(&payload("<text>t</text>", |_| {}), &test_fonts()).unwrap();
        assert!(
            svg.contains(&format!(
                "<g transform=\"translate(0, 24)\" {CLIP} {BODY_PAINT}><text>t</text></g>"
            )),
            "{svg}"
        );
    }

    #[test]
    fn a_style_font_without_a_color_is_refused() {
        let p = payload("<rect/>", |r| r["style"]["tree"]["font"]["color"] = Json::Null);
        let err = render(&p, &test_fonts()).unwrap_err();
        assert!(format!("{err:?}").contains("custom: style.font.color absent"), "{err:?}");
    }

    #[test]
    fn a_box_too_small_for_the_title_is_the_charts_error() {
        let mut p = payload("<rect/>", |_| {});
        p.height = 10.0;
        let err = render(&p, &test_fonts()).unwrap_err();
        assert_eq!(err.code, "ERR-CUSTOM-RENDER", "{err:?}");
    }

    #[test]
    fn a_title_font_without_a_size_is_refused() {
        let p = payload("<rect/>", |r| r["style"]["title_font"]["size"] = Json::Null);
        let err = render(&p, &test_fonts()).unwrap_err();
        assert!(format!("{err:?}").contains("custom: style.title_font.size absent"), "{err:?}");
    }

    #[test]
    fn an_untitled_chart_hands_the_template_the_whole_height() {
        let svg = render(
            &payload("<rect height=\"{{ height }}\"/>", |r| {
                r["title"] = Json::Null
            }),
            &test_fonts(),
        )
        .unwrap();
        assert!(
            svg.contains(&format!("<g transform=\"translate(0, 0)\" clip-path=\"url(#custom-clip-300-200)\" {BODY_PAINT}><rect height=\"200.0\"/></g>")),
            "{svg}"
        );
    }

    #[test]
    fn helpers_and_namespaces_are_in_scope() {
        let template = "<text>{{ options.fill }} {{ channels.x }} {{ columns | join(',') }} {{ uid }} \
                        {{ style.muted }} {{ variables.region }} {{ math.floor(2.7) }} {{ math.pi | round(2) }} \
                        {{ color_for('Ana', 'rep') }} {{ color_for('x') }} {{ color_for('y') }} {{ 1234.5 | format(',.0f') }}</text>";
        let svg = render(&payload(template, |_| {}), &test_fonts()).unwrap();
        assert!(
            svg.contains("<text>#ff0000 rep rep,n custom-c #888888 North 2 3.14 #123456 #111111 #222222 1,235</text>"),
            "{svg}"
        );
    }

    #[test]
    fn text_width_measures_with_the_board_font() {
        let svg = render(&payload("<text>{{ text_width('Hello', 12) > 20 }} {{ text_width('Hello', 12, 'bold') > text_width('Hello', 12) }}</text>", |_| {}), &test_fonts()).unwrap();
        assert!(svg.contains("<text>True True</text>"), "{svg}");
        let err = render(
            &payload("{{ text_width('Hello', 12, 'heavy-ish') }}", |_| {}),
            &test_fonts(),
        )
        .unwrap_err();
        assert_eq!(err.code, "ERR-CUSTOM-RENDER");
        assert!(
            err.params["reason"]
                .as_str()
                .unwrap()
                .contains("text_width(): weight must be"),
            "{err:?}"
        );
    }

    #[test]
    fn color_for_refuses_past_the_palette() {
        let err = render(
            &payload(
                "{{ color_for('a') }}{{ color_for('b') }}{{ color_for('c') }}",
                |_| {},
            ),
            &test_fonts(),
        )
        .unwrap_err();
        assert!(
            err.params["reason"]
                .as_str()
                .unwrap()
                .contains("ran out of palette colors (2)"),
            "{err:?}"
        );
    }

    #[test]
    fn raise_error_is_the_charts_reason_verbatim() {
        let err = render(
            &payload("{{ raise_error('needs two rows') }}", |_| {}),
            &test_fonts(),
        )
        .unwrap_err();
        assert_eq!(err.code, "ERR-CUSTOM-RENDER");
        assert_eq!(err.params["reason"], json!("needs two rows"));
    }

    #[test]
    fn an_undefined_name_names_the_template_line() {
        let err = render(&payload("<g>\n{{ nope }}</g>", |_| {}), &test_fonts()).unwrap_err();
        let reason = err.params["reason"].as_str().unwrap();
        assert!(
            reason.contains("undefined") && reason.contains("template inline svg:, line 2"),
            "{reason}"
        );
    }

    #[test]
    fn a_missing_channel_column_lists_the_available_ones() {
        let err = render(
            &payload("<g/>", |r| r["channels"]["x"] = json!("salesperson")),
            &test_fonts(),
        )
        .unwrap_err();
        let reason = err.params["reason"].as_str().unwrap();
        assert!(
            reason.contains("channel 'x' names column 'salesperson'")
                && reason.contains("['rep', 'n']"),
            "{reason}"
        );
    }

    #[test]
    fn row_values_are_escaped() {
        let p = payload("<text>{{ rows[0].rep }}</text>", |_| {});
        let mut p = p;
        p.rows[0].insert("rep".into(), json!("<b>&"));
        let svg = render(&p, &test_fonts()).unwrap();
        assert!(svg.contains("<text>&lt;b&gt;&amp;</text>"), "{svg}");
    }

    #[test]
    fn disallowed_svg_is_err_custom_svg() {
        let err = render(&payload("<script>1</script>", |_| {}), &test_fonts()).unwrap_err();
        assert_eq!(err.code, "ERR-CUSTOM-SVG");
        assert_eq!(
            err.params["reason"],
            json!("<script> is not an allowed element")
        );
    }

    #[test]
    fn the_output_budget_fails_the_chart() {
        let mut p = payload("{% for i in range(1000) %}<g/>{% endfor %}", |_| {});
        p.max_template_output_bytes = Some(100);
        let err = render(&p, &test_fonts()).unwrap_err();
        assert!(
            err.params["reason"]
                .as_str()
                .unwrap()
                .contains("exceeded the 100 bytes"),
            "{err:?}"
        );
    }

    #[test]
    fn a_runaway_loop_hits_the_instruction_budget() {
        let template =
            "{% for i in range(100000) %}{% for j in range(100000) %}{% endfor %}{% endfor %}";
        let err = render(&payload(template, |_| {}), &test_fonts()).unwrap_err();
        assert_eq!(err.code, "ERR-CUSTOM-RENDER");
        assert!(
            err.params["reason"]
                .as_str()
                .unwrap()
                .contains("instruction budget"),
            "{err:?}"
        );
    }

    #[test]
    fn the_output_cap_stops_the_render_as_it_crosses() {
        let mut p = payload("{% for i in range(100000) %}<g/>{% endfor %}", |_| {});
        p.max_template_output_bytes = Some(1_000);
        let err = render(&p, &test_fonts()).unwrap_err();
        assert_eq!(err.code, "ERR-CUSTOM-RENDER");
        assert!(
            err.params["reason"]
                .as_str()
                .unwrap()
                .contains("exceeded the 1000 bytes"),
            "{err:?}"
        );
    }

    #[test]
    fn deep_nesting_is_refused_not_recursed() {
        let template =
            "{% for i in range(5000) %}<g>{% endfor %}{% for i in range(5000) %}</g>{% endfor %}";
        let err = render(&payload(template, |_| {}), &test_fonts()).unwrap_err();
        assert_eq!(err.code, "ERR-CUSTOM-SVG");
        assert!(
            err.params["reason"]
                .as_str()
                .unwrap()
                .contains("the limit is 64 levels"),
            "{err:?}"
        );
    }

    #[test]
    fn a_row_link_fills_every_custom_channel_and_refuses_an_unknown_placeholder() {
        let template = "{% for r in rows %}<a href=\"{{ href(r) }}\"><rect/></a>{% endfor %}";
        let svg = render(
            &payload(template, |r| {
                r["channels"] = json!({"value": "n"});
                r["link"] = json!("https://x.test/v/{{ value }}");
            }),
            &test_fonts(),
        )
        .unwrap();
        assert!(
            svg.contains("<a href=\"https://x.test/v/1.5\">") && svg.contains("/v/2\">"),
            "{svg}"
        );

        let err = render(
            &payload(template, |r| {
                r["link"] = json!("https://x.test/{{ stage }}")
            }),
            &test_fonts(),
        )
        .unwrap_err();
        assert_eq!(err.code, "ERR-CUSTOM-RENDER");
        assert!(
            err.params["reason"]
                .as_str()
                .unwrap()
                .contains("names no channel"),
            "{err:?}"
        );
    }

    #[test]
    fn a_channel_link_the_template_never_draws_is_refused() {
        let err = render(
            &payload("<rect/>{# href(row) #}", |r| {
                r["channels"] = json!({"value": "n"});
                r["link"] = json!("https://x.test/v/{{ value }}");
            }),
            &test_fonts(),
        )
        .unwrap_err();
        assert_eq!(err.code, "ERR-CUSTOM-RENDER");
        assert!(
            err.params["reason"]
                .as_str()
                .unwrap()
                .contains("never calls href(row)"),
            "{err:?}"
        );
    }

    #[test]
    fn a_row_link_template_with_no_rows_renders_empty() {
        let mut p = payload(
            "{% for r in rows %}<a href=\"{{ href(r) }}\"><rect/></a>{% endfor %}",
            |r| {
                r["channels"] = json!({"value": "n"});
                r["link"] = json!("https://x.test/v/{{ value }}");
            },
        );
        p.rows.clear();
        let svg = render(&p, &test_fonts()).unwrap();
        assert!(!svg.contains("<a "), "{svg}");
    }

    #[test]
    fn a_channel_link_drawn_through_a_spaced_or_aliased_href_call_is_kept() {
        for template in [
            "<a href=\"{{ href (rows[0]) }}\"><rect/></a>",
            "{% set h = href %}<a href=\"{{ h(rows[0]) }}\"><rect/></a>",
        ] {
            let svg = render(
                &payload(template, |r| {
                    r["channels"] = json!({"value": "n"});
                    r["link"] = json!("https://x.test/v/{{ value }}");
                }),
                &test_fonts(),
            )
            .unwrap();
            assert!(svg.contains("<a href=\"https://x.test/v/1.5\">"), "{svg}");
        }
    }

    #[test]
    fn a_link_naming_no_channel_is_diagnosed_before_the_template_runs() {
        let err = render(
            &payload("<rect/>", |r| r["link"] = json!("https://x.test/{{ stage }}")),
            &test_fonts(),
        )
        .unwrap_err();
        assert!(
            err.params["reason"]
                .as_str()
                .unwrap()
                .contains("names no channel"),
            "{err:?}"
        );
    }

    #[test]
    fn an_unsafe_chart_link_raises_the_link_scheme_code() {
        let err = render(
            &payload("<rect/>", |r| r["link"] = json!("javascript:alert(1)")),
            &test_fonts(),
        )
        .unwrap_err();
        assert_eq!(err.code, "ERR-LINK-SCHEME-UNSAFE-AT-RENDER");
    }

    #[test]
    fn a_link_mode_stamps_only_a_modal_whole_chart_anchor() {
        let render_with = |mode: &str| {
            render(
                &payload("<rect/>", |r| {
                    r["link"] = json!("https://x.test/all");
                    r["link_open"] = json!(mode);
                }),
                &test_fonts(),
            )
        };
        let page = render_with("page").unwrap();
        assert!(page.contains("<a href=\"https://x.test/all\"><g"), "{page}");
        assert!(!page.contains("data-dbt-link-open"), "{page}");
        let modal = render_with("modal").unwrap();
        assert!(modal.contains("<a href=\"https://x.test/all\" data-dbt-link-open=\"modal\"><g"), "{modal}");
        assert!(render_with("popup").is_err());
    }

    /// A per-row link is the template's own `<a>`, which carries no mode: a
    /// non-page mode on it is refused rather than silently navigating.
    #[test]
    fn a_row_link_with_a_non_page_mode_is_refused() {
        let template = "{% for r in rows %}<a href=\"{{ href(r) }}\"><rect/></a>{% endfor %}";
        let err = render(
            &payload(template, |r| {
                r["link"] = json!("/reps/{{ x }}");
                r["link_open"] = json!("modal");
            }),
            &test_fonts(),
        )
        .unwrap_err();
        let reason = err.params["reason"].as_str().unwrap();
        assert!(reason.contains("link.open: modal"), "{reason}");
    }

    #[test]
    fn a_static_link_wraps_the_body_and_a_row_link_is_the_templates() {
        let svg = render(
            &payload("<rect/>", |r| r["link"] = json!("https://x.test/all")),
            &test_fonts(),
        )
        .unwrap();
        assert!(
            svg.contains("</text><a href=\"https://x.test/all\"><g transform"),
            "{svg}"
        );
        assert!(svg.ends_with("</g></a></svg>"), "{svg}");

        let template = "{% for r in rows %}<a href=\"{{ href(r) }}\"><rect/></a>{% endfor %}";
        let svg = render(
            &payload(template, |r| {
                r["link"] = json!("https://x.test/reps/{{ x }}?q=1")
            }),
            &test_fonts(),
        )
        .unwrap();
        assert!(!svg.contains("<a href=\"https://x.test/reps/{{"), "{svg}");
        assert!(svg.contains("<a href=\"https://x.test/reps/Ana?q=1\"><rect/></a><a href=\"https://x.test/reps/Ben?q=1\">"), "{svg}");
        assert!(
            svg.contains(&format!("<g transform=\"translate(0, 24)\" {CLIP} {BODY_PAINT}><a href")),
            "no whole-chart anchor for a per-row link: {svg}"
        );
    }

    #[test]
    fn href_without_a_link_is_the_templates_error() {
        let err = render(&payload("{{ href(rows[0]) }}", |_| {}), &test_fonts()).unwrap_err();
        assert!(
            err.params["reason"]
                .as_str()
                .unwrap()
                .contains("href(): this chart has no link"),
            "{err:?}"
        );
    }

    #[test]
    fn a_row_title_shift_moves_title_and_body() {
        let mut p = payload("<rect/>", |r| r["title_style"]["position"]["offset"] = json!(10.0));
        p.padding = Some(crate::Padding { left: 0.0, right: 0.0, top: 0.0, bottom: 0.0 });
        p.title_shift = crate::title_shift::TitleShift {
            title_dy: 2.0,
            body_dy: 5.0,
        };
        let shifted = crate::title_shift::for_family(&p, "custom").unwrap();
        let svg = render(&shifted, &test_fonts()).unwrap();
        assert!(
            svg.contains("y=\"13.00\"") && svg.contains("translate(0, 29)"),
            "{svg}"
        );
    }

    #[test]
    fn check_template_reports_the_line() {
        assert!(check_template("{% if x %}a{% endif %}").is_ok());
        let err = check_template("<g>\n{% if %}").unwrap_err();
        assert!(err.starts_with("template syntax error on line 2:"), "{err}");
    }

    // ── allowlist ──

    #[test]
    fn allowed_content_passes_and_is_reserialized() {
        for content in [
            "<g><rect x=\"1\" fill=\"url(#grad)\"/></g>",
            "<defs><linearGradient id=\"g\"><stop offset=\"0\"/></linearGradient></defs>",
            "<use href=\"#sym\"/>",
            "<use xlink:href=\"#sym\"/>",
            "<text font-family=\"a, 'b c'\">t &amp; u</text>",
            "<a href=\"https://x.test/a\"><rect/></a>",
            "<a href=\"/boards/detail?x=1\"><rect/></a>",
            "<g class=\"series\"><rect fill=\"rgb(1, 2, 3)\" stroke=\"currentColor\" stop-color=\"#abc\"/></g>",
            "<rect fill=\"url(#grad)\" stroke=\"none\"/>",
        ] {
            checked_svg_content(content).unwrap_or_else(|e| panic!("{content}: {e}"));
        }
        assert_eq!(
            checked_svg_content("<use xlink:href=\"#s\" ></use>").unwrap(),
            "<use href=\"#s\"/>"
        );
        assert_eq!(
            checked_svg_content("<text a=\"&lt;&quot;\">x &gt; y</text>").unwrap(),
            "<text a=\"&lt;&quot;\">x &gt; y</text>"
        );
    }

    #[test]
    fn violations_name_the_construct() {
        for (content, needle) in [
            ("<script>x</script>", "<script> is not an allowed element"),
            (
                "<foreignObject/>",
                "<foreignObject> is not an allowed element",
            ),
            ("<style>x</style>", "<style> is not an allowed element"),
            ("<image href=\"#x\"/>", "<image> is not an allowed element"),
            ("<animate/>", "<animate> is not an allowed element"),
            ("<rect onclick=\"x()\"/>", "event attribute"),
            ("<rect ONLOAD=\"x()\"/>", "event attribute"),
            ("<use href=\"https://x.test\"/>", "must be a #id reference"),
            (
                "<a href=\"javascript:alert(1)\"><rect/></a>",
                "must be a board path or an http, https or mailto URL",
            ),
            (
                "<a href=\"data:text/html,x\"><rect/></a>",
                "must be a board path",
            ),
            ("<rect style=\"fill:url(https://x)\"/>", "url() in"),
            (
                "<rect style=\"background:image-set('x')\"/>",
                "image-set() in",
            ),
            ("<rect style=\"fill:\\75rl(#x)\"/>", "backslash"),
            ("<rect fill=\"j a v a s c r i p t:x\"/>", "javascript: in"),
            ("<!-- c --><rect/>", "a comment"),
            ("<?pi x?><rect/>", "a processing instruction"),
            ("<![CDATA[x]]>", "a CDATA section"),
            ("<!DOCTYPE svg><rect/>", "<!DOCTYPE is not allowed"),
            ("<!ENTITY x 'y'>", "<!ENTITY is not allowed"),
            ("<rect xml:space=\"preserve\"/>", "namespaced attribute"),
            ("<rect aria-label=\"x\"/>", "reserved for the board"),
            ("<rect data-dbt-page-var=\"p\"/>", "reserved for the board"),
            ("<g role=\"img\"/>", "reserved for the board"),
            ("<g class=\"role-mark\"/>", "reserved for the board"),
            ("<g class=\"a dbt-page-target\"/>", "reserved for the board"),
            (
                "<rect fill='red;&quot;&gt;&lt;img src=x onerror=alert(1)&gt;'/>",
                "paint attribute",
            ),
            ("<rect stroke=\"red&quot;\"/>", "paint attribute"),
            ("<use href=\"#a\" xlink:href=\"#b\"/>", "appears twice"),
            ("<rect", "not well-formed XML"),
            (
                "<svg:rect xmlns:svg=\"http://other\"/>",
                "<rect> is not an allowed element",
            ),
        ] {
            let err = checked_svg_content(content).expect_err(content);
            assert!(err.contains(needle), "{content}: {err}");
        }
    }
}
