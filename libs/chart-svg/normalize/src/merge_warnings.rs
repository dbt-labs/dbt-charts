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

/// The leaves of `patch` with their spelled values, a leaf being a path nothing extends.
pub(crate) fn leaves(patch: &impl model_core::LeafValues) -> Vec<(String, Value)> {
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

/// What a built-in theme sets: its leaves and every path it sets.
struct ThemeLeaves {
    held: BTreeMap<String, Value>,
    set: BTreeSet<String>,
}

/// A built-in theme's leaves, read once per process: the theme is a static, and walking it is the
/// whole cost of the check.
fn theme_leaves(name: &str) -> Result<std::sync::Arc<ThemeLeaves>, RestatedError> {
    static CACHE: std::sync::Mutex<BTreeMap<String, std::sync::Arc<ThemeLeaves>>> =
        std::sync::Mutex::new(BTreeMap::new());
    let mut cache = CACHE
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if let Some(found) = cache.get(name) {
        return Ok(found.clone());
    }
    let body = built_in_theme(name).map_err(|e| RestatedError(e.to_string()))?;
    let theme = Patch {
        body: body.clone(),
        ..Default::default()
    };
    let found = std::sync::Arc::new(ThemeLeaves {
        held: leaves(&theme).into_iter().collect(),
        set: value_paths(&theme),
    });
    cache.insert(name.to_string(), found.clone());
    Ok(found)
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
                .with_sink_param(
                    "why",
                    if meta_themes > 0 {
                        "meta.yml theme"
                    } else {
                        "default theme"
                    }
                    .into(),
                )
                .at_path(ModelPath(vec!["extends".to_string()])),
        });
    }
    // A board that authors no cascaded key has nothing to judge; the theme is not read.
    let own_leaves = leaves(&cascaded(own));
    if own_leaves.is_empty() {
        return Ok(out);
    }
    let in_force = themes.last().map_or(below, String::as_str);
    let theme = theme_leaves(in_force)?;
    // The layers' own leaves are few; the theme's are enumerated once per process. A path is
    // looked up top layer first, then the theme: this equals the fold of the layers only while
    // no layer nulls or replaces whole a slot the theme populates (no shipped theme fills one).
    let layer_leaves: Vec<BTreeMap<String, Value>> = layers
        .iter()
        .map(|(_, p)| leaves(p).into_iter().collect())
        .collect();
    let mut layer_sets: Option<Vec<BTreeSet<String>>> = None;
    for (path, value) in own_leaves {
        // A font slot that names another font drops what it inherits: its key is no restatement.
        if path.ends_with(".extends") {
            continue;
        }
        let held = layer_leaves
            .iter()
            .rev()
            .find_map(|l| l.get(&path))
            .or_else(|| theme.held.get(&path));
        if held != Some(&value) {
            continue;
        }
        // A value no layer sets is a model default, not an inherited one.
        let sets =
            layer_sets.get_or_insert_with(|| layers.iter().map(|(_, p)| value_paths(p)).collect());
        let mut why = "inherited";
        let label = layers
            .iter()
            .zip(sets.iter())
            .rev()
            .find_map(|((file, _), set)| set.contains(&path).then(|| format!("set in `{file}`")))
            .or_else(|| {
                theme.set.contains(&path).then(|| {
                    why = "theme default";
                    format!("the default in theme `{in_force}`")
                })
            });
        if let Some(label) = label {
            let value = diagnostic::restated_text(&value);
            let at = ModelPath(path.split('.').map(str::to_string).collect());
            out.push(MergeWarning {
                file: None,
                diagnostic: diag::warn_restated_style(path, value, label)
                    .with_sink_param("why", why.into())
                    .at_path(at),
            });
        }
    }
    Ok(out)
}
