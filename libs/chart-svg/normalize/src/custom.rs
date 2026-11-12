//! Custom chart templates: the file a `template:` names (or the inline `svg:`), its syntax, and the
//! chart's channels and options against the header the template declares.
//!
//! Python's pydantic words a malformed header's reason; the engine words its own, so the code is the
//! contract there, and the reason is exact for every other refusal.

use crate::compile::CompileError;
use crate::files::{ProjectFiles, ReadError};
use board_model::chart::custom::OptionValue;
use board_model::chart::{ChartAuthored, Columns};
use board_model::ids::ChartId;
use diagnostic::{diag, py_repr, py_str_repr};
use indexmap::IndexMap;
use model_core::Tri;
use project::ProjectPath;
use serde_json::Value as Json;
use std::collections::BTreeMap;
use template::{parse, Kind};

const CHANNELS: [&str; 7] = ["x", "y", "color", "size", "shape", "theta", "value"];
const OPTION_TYPES: [&str; 7] = [
    "column", "columns", "string", "number", "integer", "boolean", "color",
];
const INLINE: &str = "inline svg:";

fn refuse(
    kind: fn(String, String) -> diagnostic::Diagnostic,
    id: &str,
    why: String,
) -> CompileError {
    CompileError::Invalid(vec![kind(id.to_string(), why)])
}

/// Loads each custom chart's template and checks its fields; the first chart that fails is the
/// refusal, as Python compiles charts in order.
pub fn check(
    charts: &indexmap::IndexMap<String, Option<ChartAuthored>>,
    public_ids: &BTreeMap<ChartId, String>,
    files: &dyn ProjectFiles,
) -> Result<(), CompileError> {
    for (id, chart) in charts {
        let Some(ChartAuthored::Custom(c)) = chart else {
            continue;
        };
        let id = public_ids
            .iter()
            .find(|(k, _)| k.as_str() == id)
            .map(|(_, public)| public)
            .ok_or_else(|| {
                CompileError::Invalid(vec![diag::err_internal(format!(
                    "no public id for the chart {id}"
                ))])
            })?;
        let template_fail = |why: String| refuse(diag::err_custom_template, id, why);
        let (name, source) = match (&c.template, &c.svg) {
            (Tri::Value(t), _) => {
                let rel = format!("templates/{}.svg.j2", t.as_str());
                let path = ProjectPath::try_from(rel.as_str())
                    .map_err(|e| template_fail(e.to_string()))?;
                let Some(source) = files.read(&path)? else {
                    let names = available(files)?;
                    let avail = if names.is_empty() {
                        "templates/ has no templates".to_string()
                    } else {
                        format!("available: {}", names.join(", "))
                    };
                    return Err(template_fail(format!(
                        "template file {rel} not found in the project; {avail}"
                    )));
                };
                (t.as_str().to_string(), source)
            }
            (_, Tri::Value(svg)) => (INLINE.to_string(), svg.clone()),
            _ => continue,
        };
        let info = parse(&source, Kind::Svg).map_err(|errs| {
            let why = errs.first().map_or_else(String::new, |d| d.message.clone());
            template_fail(format!("{name}: {why}"))
        })?;
        let header = Header::read(info.header.as_deref())
            .map_err(|why| template_fail(format!("{name}: {why}")))?;
        header
            .validate(c)
            .map_err(|why| refuse(diag::err_custom_fields, id, why))?;
    }
    Ok(())
}

/// `available_template_names`: the sorted names directly under `templates/`.
fn available(files: &dyn ProjectFiles) -> Result<Vec<String>, ReadError> {
    let mut names: Vec<String> = files
        .list("templates")?
        .iter()
        .filter_map(|p| {
            p.as_str()
                .strip_prefix("templates/")?
                .strip_suffix(".svg.j2")
                .map(str::to_string)
        })
        .collect();
    names.sort();
    Ok(names)
}

struct Channel {
    multiple: bool,
    required: bool,
}

struct Opt {
    kind: String,
    required: bool,
}

#[derive(Default)]
struct Header {
    channels: IndexMap<String, Channel>,
    options: IndexMap<String, Opt>,
}

fn keys(map: &serde_json::Map<String, Json>, allowed: &[&str], what: &str) -> Result<(), String> {
    match map.keys().find(|k| !allowed.contains(&k.as_str())) {
        Some(k) => Err(format!(
            "invalid template header: {what} has unknown key {}",
            py_str_repr(k)
        )),
        None => Ok(()),
    }
}

fn check_value(label: &str, kind: &str, value: &Json, place: &str) -> Result<(), String> {
    let non_empty = |s: &Json| s.as_str().is_some_and(|s| !s.is_empty());
    let ok = match kind {
        "column" => non_empty(value),
        "columns" => value
            .as_array()
            .is_some_and(|a| !a.is_empty() && a.iter().all(non_empty)),
        "string" => value.is_string(),
        "number" => value.is_number(),
        "integer" => value.is_i64() || value.is_u64(),
        "boolean" => value.is_boolean(),
        _ => value
            .as_str()
            .is_some_and(|c| color::is_color_token(c) || color::is_sanitizable_color(c)),
    };
    if ok {
        Ok(())
    } else {
        Err(format!(
            "{label} expects {kind}; {place} {} is not",
            repr(value)
        ))
    }
}

/// Python's `repr`, lists included.
fn repr(v: &Json) -> String {
    match v {
        Json::Array(a) => format!("[{}]", a.iter().map(repr).collect::<Vec<_>>().join(", ")),
        other => py_repr(other),
    }
}

impl Header {
    fn read(text: Option<&str>) -> Result<Self, String> {
        let Some(text) = text else {
            return Ok(Self::default());
        };
        let node =
            yaml::parse_any(text).map_err(|_| "invalid template header: not YAML".to_string())?;
        let raw = node.to_json();
        let map = match &raw {
            Json::Null => return Ok(Self::default()),
            Json::Object(m) => m,
            _ => return Err("invalid template header: must be a mapping".into()),
        };
        keys(map, &["description", "channels", "options"], "the header")?;
        let mut header = Self::default();
        let section = |name: &str| match map.get(name) {
            None | Some(Json::Null) => Ok(serde_json::Map::new()),
            Some(Json::Object(m)) => Ok(m.clone()),
            Some(_) => Err(format!(
                "invalid template header: `{name}` must be a mapping"
            )),
        };
        for (name, spec) in section("channels")? {
            if !CHANNELS.contains(&name.as_str()) {
                return Err(format!(
                    "invalid template header: unknown channel {}",
                    py_str_repr(&name)
                ));
            }
            let spec = match &spec {
                Json::Null => serde_json::Map::new(),
                Json::Object(m) => m.clone(),
                _ => return Err("invalid template header: a channel must be a mapping".into()),
            };
            if spec.contains_key("required") {
                return Err("invalid template header: `required` is not a key; omit `default` to require it".into());
            }
            keys(&spec, &["description", "multiple", "default"], "a channel")?;
            if spec.get("default").is_some_and(|d| !d.is_null()) {
                return Err(
                    "invalid template header: a channel's only `default` is null (optional)".into(),
                );
            }
            let multiple = match spec.get("multiple") {
                None => false,
                Some(Json::Bool(b)) => *b,
                Some(_) => {
                    return Err("invalid template header: `multiple` must be a boolean".into())
                }
            };
            let required = !spec.contains_key("default");
            header.channels.insert(name, Channel { multiple, required });
        }
        for (name, spec) in section("options")? {
            let Json::Object(spec) = &spec else {
                return Err(
                    "invalid template header: an option must be a mapping with a `type:`".into(),
                );
            };
            if spec.contains_key("required") {
                return Err("invalid template header: `required` is not a key; omit `default` to require it".into());
            }
            keys(spec, &["type", "description", "default"], "an option")?;
            let kind = match spec.get("type") {
                Some(Json::String(t)) if OPTION_TYPES.contains(&t.as_str()) => t.clone(),
                _ => {
                    return Err(format!(
                        "invalid template header: option {} needs a valid `type`",
                        py_str_repr(&name)
                    ))
                }
            };
            let default = spec.get("default").cloned().unwrap_or(Json::Null);
            let required = !spec.contains_key("default");
            if !required && !default.is_null() {
                check_value(
                    &format!("option {}", py_str_repr(&name)),
                    &kind,
                    &default,
                    "default",
                )?;
            }
            header.options.insert(name, Opt { kind, required });
        }
        Ok(header)
    }

    fn validate(&self, c: &board_model::chart::custom::CustomChartAuthored) -> Result<(), String> {
        let authored: [(&str, &Tri<Columns>); 7] = [
            ("x", &c.x),
            ("y", &c.y),
            ("color", &c.color),
            ("size", &c.size),
            ("shape", &c.shape),
            ("theta", &c.theta),
            ("value", &c.value),
        ];
        let mut set: Vec<&str> = authored
            .iter()
            .filter(|(_, v)| matches!(v, Tri::Value(_)))
            .map(|(n, _)| *n)
            .collect();
        set.sort_unstable();
        if set.iter().any(|n| !self.channels.contains_key(*n)) {
            let undeclared: Vec<&str> = set
                .into_iter()
                .filter(|n| !self.channels.contains_key(*n))
                .collect();
            let mut declared: Vec<&String> = self.channels.keys().collect();
            declared.sort();
            return Err(format!(
                "the chart sets channel(s) {}, but the template declares channels {}",
                list(&undeclared),
                list(&declared)
            ));
        }
        for (name, spec) in &self.channels {
            let value = authored.iter().find(|(n, _)| n == name).map(|(_, v)| v);
            let Some(Tri::Value(value)) = value else {
                if spec.required {
                    return Err(format!("missing required channel {}", py_str_repr(name)));
                }
                continue;
            };
            if matches!(value, Columns::Many(_)) && !spec.multiple {
                return Err(format!(
                    "channel {} takes one column; a list needs `multiple: true` in the template header",
                    py_str_repr(name)
                ));
            }
            let blank = value.is_blank()
                || match value {
                    Columns::One(_) => false,
                    Columns::Many(m) => m.iter().any(|c| c.as_str().is_empty()),
                };
            if blank {
                return Err(format!(
                    "channel {} must name column(s); {} is not",
                    py_str_repr(name),
                    columns_repr(value)
                ));
            }
        }
        let authored_options: Vec<(&str, Json)> = c
            .options
            .iter()
            .map(|(k, v)| (k.as_str(), v.as_ref().map_or(Json::Null, json)))
            .collect();
        if self.options.is_empty() && !authored_options.is_empty() {
            let mut names: Vec<&str> = authored_options.iter().map(|(k, _)| *k).collect();
            names.sort_unstable();
            return Err(format!(
                "the template declares no options, but options: names {}",
                list(&names)
            ));
        }
        let mut unknown: Vec<&str> = authored_options
            .iter()
            .map(|(k, _)| *k)
            .filter(|k| !self.options.contains_key(*k))
            .collect();
        unknown.sort_unstable();
        if !unknown.is_empty() {
            let mut declared: Vec<&String> = self.options.keys().collect();
            declared.sort();
            return Err(format!(
                "unknown option(s) {}; the template declares {}",
                list(&unknown),
                list(&declared)
            ));
        }
        for (name, spec) in &self.options {
            match authored_options.iter().find(|(k, _)| k == name) {
                Some((_, v)) => check_value(
                    &format!("option {}", py_str_repr(name)),
                    &spec.kind,
                    v,
                    "value",
                )?,
                None if spec.required => {
                    return Err(format!(
                        "missing required option {} ({})",
                        py_str_repr(name),
                        spec.kind
                    ))
                }
                None => {}
            }
        }
        Ok(())
    }
}

fn json(v: &OptionValue) -> Json {
    match v {
        OptionValue::Null => Json::Null,
        OptionValue::Bool(b) => Json::Bool(*b),
        OptionValue::Number(n) => Json::Number(n.clone()),
        OptionValue::Text(s) => Json::String(s.clone()),
        OptionValue::List(l) => Json::Array(l.iter().cloned().map(Json::String).collect()),
    }
}

fn list<T: AsRef<str>>(names: &[T]) -> String {
    format!(
        "[{}]",
        names
            .iter()
            .map(|n| py_str_repr(n.as_ref()))
            .collect::<Vec<_>>()
            .join(", ")
    )
}

fn columns_repr(c: &Columns) -> String {
    match c {
        Columns::One(c) => py_str_repr(c.as_str()),
        Columns::Many(m) => list(&m.iter().map(|c| c.as_str()).collect::<Vec<_>>()),
    }
}
