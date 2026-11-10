//! The warnings Python's `merged_patch` collects while it folds, over the layers the fold returns:
//! a layer that replaces an inherited layout or tag list, a `meta.yml` or `extends:` file that
//! authors a heading margin, and a board's own file that restates a theme or a cascaded value.

use crate::extends::Patch;
use board_model::board::layout::LayoutAuthored;
use board_model::board::theme::built_in_theme;
use diagnostic::{diag, Diagnostic, ModelPath};
use model_core::{LeafValues, SetLeaves};
use project::ProjectPath;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

/// A finding of the fold, and the file it names when it is not the board's own.
#[derive(Debug, Clone, PartialEq)]
pub struct MergeWarning {
    pub file: Option<ProjectPath>,
    pub diagnostic: Diagnostic,
}

impl MergeWarning {
    /// The text Python shows: a finding in another file leads with that file.
    pub fn message(&self) -> String {
        let message = self.diagnostic.message().unwrap_or_default();
        match &self.file {
            Some(file) => format!("{file}: {message}"),
            None => message,
        }
    }
}

struct Held<T> {
    source: String,
    count: Option<usize>,
    value: T,
}

fn layout_name(l: &LayoutAuthored) -> (&'static str, Option<usize>) {
    match l {
        LayoutAuthored::Rows(v) => ("rows", Some(v.len())),
        LayoutAuthored::Cols(v) => ("cols", Some(v.len())),
        LayoutAuthored::Grid(_) => ("grid", None),
        LayoutAuthored::Tabs(_) => ("tabs", None),
    }
}

fn replaced(
    field: &str,
    name: &str,
    held_source: &str,
    count: Option<usize>,
    source: &str,
) -> MergeWarning {
    let inherited = match count {
        Some(n) => format!("the {n} {name}"),
        None => format!("the {name}"),
    };
    let replacer = if source.is_empty() {
        "here".to_string()
    } else {
        format!("in {source}")
    };
    let diagnostic = diag::warn_inherited_layout_replaced(field, replacer, inherited, held_source);
    MergeWarning {
        file: None,
        diagnostic: if source.is_empty() {
            diagnostic.at_path(ModelPath(vec![field.to_string()]))
        } else {
            diagnostic
        },
    }
}

/// One warning per layer that replaces a non-empty layout or tag list. `layers` run low to high;
/// the board's own layer has the empty source. A list set empty has nothing to lose, and
/// restating an equal value loses nothing.
fn replaced_layouts<'a>(layers: impl Iterator<Item = (&'a str, &'a Patch)>) -> Vec<MergeWarning> {
    let mut tags: Option<Held<&'a Vec<String>>> = None;
    let mut layout: Option<Held<&'a LayoutAuthored>> = None;
    let mut out = Vec::new();
    for (source, patch) in layers {
        if let Some(new) = &patch.tags {
            if let Some(h) = tags.take() {
                if h.count != Some(0) && h.value != new {
                    out.push(replaced("tags", "tags", &h.source, h.count, source));
                }
            }
            tags = Some(Held {
                source: source.to_string(),
                count: Some(new.len()),
                value: new,
            });
        }
        if let Some(new) = &patch.body.layout {
            let (field, count) = layout_name(new);
            if let Some(h) = layout.take() {
                let (name, _) = layout_name(h.value);
                if h.count != Some(0) && (name != field || h.value != new) {
                    out.push(replaced(field, name, &h.source, h.count, source));
                }
            }
            layout = Some(Held {
                source: source.to_string(),
                count,
                value: new,
            });
        }
    }
    out
}

const MARGIN_TOP: &str = "it sets the space above H1 and H2 only; H3 to H6 take the paragraph gap.";
const MARGIN_BOTTOM: &str = "it sets the space below H2 to H6 only; H1 takes the paragraph gap, and board and section titles take their gap below from style.text.paragraph.";

/// WARN-HEADING-MARGIN-REDEFINED for each file that authors a `style.text.heading` margin.
fn heading_margins(layers: &[(ProjectPath, Patch)]) -> Vec<MergeWarning> {
    let mut out = Vec::new();
    for (file, patch) in layers {
        let set = set_paths(patch);
        for (key, meaning) in [("margin_top", MARGIN_TOP), ("margin_bottom", MARGIN_BOTTOM)] {
            if set.contains(&format!("style.text.heading.{key}")) {
                out.push(MergeWarning {
                    file: Some(file.clone()),
                    diagnostic: diag::warn_heading_margin_redefined(key, meaning),
                });
            }
        }
    }
    out
}

fn set_paths(patch: &Patch) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    patch.set_leaves("", &mut |p| {
        out.insert(p.to_string());
    });
    out
}

/// The layout and heading-margin warnings of a fold: the layers low to high, then the board's own.
pub(crate) fn of_fold(layers: &[(ProjectPath, Patch)], own: &Patch) -> Vec<MergeWarning> {
    let all = layers
        .iter()
        .map(|(f, p)| (f.as_str(), p))
        .chain([("", own)]);
    let mut out = replaced_layouts(all);
    out.extend(heading_margins(layers));
    out
}

/// A board's own cascaded fields: what a restatement is judged on.
pub(crate) fn cascaded(own: &Patch) -> Patch {
    let mut narrow = Patch {
        regional: own.regional.clone(),
        ..Default::default()
    };
    narrow.body.style = own.body.style.clone();
    narrow.body.source = own.body.source.clone();
    narrow
}

/// The text a restatement message shows for a leaf, as Python's `restated_diagnostic` makes it:
/// a string as is, a whole float as the integer, anything else as `json.dumps` (a float as `repr`).
fn message_text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) if n.is_f64() && n.as_f64().is_some_and(|f| f.fract() == 0.0) => {
            format!("{:.0}", n.as_f64().unwrap_or_default())
        }
        _ => dumps(v),
    }
}

/// `json.dumps`: `", "` and `": "` separators, non-ASCII escaped.
fn dumps(v: &Value) -> String {
    match v {
        Value::String(s) => {
            let mut out = String::new();
            for c in serde_json::to_string(s).unwrap_or_default().chars() {
                if c.is_ascii() {
                    out.push(c);
                } else {
                    for unit in c.encode_utf16(&mut [0; 2]) {
                        out.push_str(&format!("\\u{unit:04x}"));
                    }
                }
            }
            out
        }
        Value::Array(items) => {
            let items: Vec<String> = items.iter().map(dumps).collect();
            format!("[{}]", items.join(", "))
        }
        Value::Object(map) => {
            let items: Vec<String> = map
                .iter()
                .map(|(k, v)| format!("{}: {}", dumps(&Value::String(k.clone())), dumps(v)))
                .collect();
            format!("{{{}}}", items.join(", "))
        }
        Value::Number(n) if n.is_f64() => {
            d3rs::py_float::float_repr(n.as_f64().unwrap_or_default())
        }
        _ => v.to_string(),
    }
}

/// Why the restated warnings cannot be computed: the theme in force is not a built-in one.
#[derive(Debug, Clone, PartialEq)]
pub struct RestatedError(pub String);

impl std::fmt::Display for RestatedError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RestatedError {}

fn value_paths(patch: &Patch) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    patch.leaf_values("", &mut |p, _| {
        out.insert(p.to_string());
    });
    out
}

fn leaves(patch: &Patch) -> Vec<(String, Value)> {
    let mut all = Vec::new();
    patch.leaf_values("", &mut |p, v| all.push((p.to_string(), v.spell())));
    // A model's own path is reported beside its leaves: only a path nothing extends is a leaf.
    let paths: BTreeSet<&str> = all.iter().map(|(p, _)| p.as_str()).collect();
    all.iter()
        .filter(|(p, _)| {
            let below = format!("{p}.");
            !paths.iter().any(|q| q.starts_with(&below))
        })
        .cloned()
        .collect()
}

/// WARN-RESTATED-STYLE for the board's own file: its theme and its cascaded keys that the layers
/// below it already supply. `entries` are its own `extends:`; `themes` and `meta_themes` the
/// themes the whole fold, and the metas alone, met.
pub(crate) fn restated(
    own: &Patch,
    entries: &[String],
    layers: &[(ProjectPath, Patch)],
    themes: &[String],
    meta_themes: usize,
    default_theme: &str,
) -> Result<Vec<MergeWarning>, RestatedError> {
    let below = meta_themes
        .checked_sub(1)
        .and_then(|i| themes.get(i))
        .map_or(default_theme, String::as_str);
    let mut out = Vec::new();
    if entries.len() == 1
        && themes.get(meta_themes..) == Some(entries)
        && entries.first().map(String::as_str) == Some(below)
    {
        let source = format!(
            "the {}theme `{below}`",
            if meta_themes > 0 {
                "meta.yml "
            } else {
                "default "
            }
        );
        out.push(MergeWarning {
            file: None,
            diagnostic: diag::warn_restated_style("extends", below, source)
                .at_path(ModelPath(vec!["extends".to_string()])),
        });
    }
    let in_force = themes.last().map_or(below, String::as_str);
    let theme = built_in_theme(in_force).map_err(|e| RestatedError(e.to_string()))?;
    let theme_patch = Patch {
        body: theme.clone(),
        ..Default::default()
    };
    let mut stack: Vec<(String, &Patch)> =
        vec![(format!("the default in theme `{in_force}`"), &theme_patch)];
    stack.extend(layers.iter().map(|(f, p)| (format!("set in `{f}`"), p)));
    let mut lower = Patch::default();
    for (_, patch) in &stack {
        lower.merge((*patch).clone());
    }
    let held: BTreeMap<String, Value> = leaves(&lower).into_iter().collect();
    let sets: Vec<BTreeSet<String>> = stack.iter().map(|(_, p)| value_paths(p)).collect();
    for (path, value) in leaves(&cascaded(own)) {
        // A font slot that names another font drops what it inherits: its key is no restatement.
        if path.ends_with(".extends") || held.get(&path) != Some(&value) {
            continue;
        }
        // A value no layer sets is a model default, not an inherited one.
        if let Some((label, _)) = stack
            .iter()
            .zip(&sets)
            .rev()
            .find_map(|(layer, set)| set.contains(&path).then_some(layer))
        {
            let value = message_text(&value);
            let at = ModelPath(path.split('.').map(str::to_string).collect());
            out.push(MergeWarning {
                file: None,
                diagnostic: diag::warn_restated_style(path, value, label.clone()).at_path(at),
            });
        }
    }
    Ok(out)
}
