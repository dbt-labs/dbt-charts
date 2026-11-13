//! Python's compile-time format validation (`compile/validate/formats.py`): every authored
//! format slot, the board's `style.formats` aliases and each query's `formats:`, with the same
//! codes, params and first-error order. A slot is a field named `format`, found by walking the
//! set leaves of the board style and of each chart.

use crate::compile::CompileError;
use board_model::chart::ChartAuthored;
use board_model::chart::ChartQuery;
use board_model::query::QueryAuthored;
use board_model::style::style::Style;
use d3rs::format_spec::{
    self, is_engine_name, is_number_name, is_time_name, ParseError, GRAIN_NAMES, TEXT_NAMES,
};
use diagnostic::{diag, py_str_repr, Diagnostic};
use indexmap::IndexMap;
use model_core::{Leaf, LeafValues, Resolved, ToPatch, Tri};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

type Patch = <board_model::board::Board as Leaf>::Patch;

/// `PYTHON_ONLY_NATIVE_NAMES`: native names Vega has no arm for.
const PYTHON_ONLY_NATIVE: [&str; 3] = [
    "percent_number",
    "percent_number_delta",
    "percentage_points_delta",
];
/// The d3 type letters the engine draws, sorted as Python lists them.
const AUTO: &str = "auto";
const ENGINE_NUMBER_TYPES: &str = "%defgprs";

/// Field names whose subtree may carry a strftime spec: the slots that render a date.
const TIME_CAPABLE: [&str; 9] = [
    "axis",
    "axis_x",
    "axis_y",
    "axis_band",
    "axis_quantitative",
    "mirror",
    "columns",
    "column_defaults",
    "timestamp",
];
/// The slots an axis paints, the only ones rejecting an affix beside a date spec.
const AXIS_FIELDS: [&str; 6] = [
    "axis",
    "axis_x",
    "axis_y",
    "axis_band",
    "axis_quantitative",
    "mirror",
];
/// Field names whose child format slots Vega paints.
const VEGA_PAINTED: [&str; 11] = [
    "axis",
    "axis_x",
    "axis_y",
    "axis_band",
    "axis_quantitative",
    "mirror",
    "labels",
    "total_label",
    "total",
    "tooltip",
    "support_table",
];

fn is_grain(name: &str) -> bool {
    GRAIN_NAMES.contains(&name)
}

fn is_text_name(name: &str) -> bool {
    TEXT_NAMES.contains(&name)
}

/// Every engine name that survives `keep`, sorted.
fn engine_names(keep: impl Fn(&str) -> bool) -> BTreeSet<String> {
    let specs = d3rs::predefined_formats::PREDEFINED_SPECS
        .iter()
        .map(|s| s.name);
    let times = d3rs::predefined_formats::PREDEFINED_TIME_SPECS
        .iter()
        .map(|(n, _)| *n);
    specs
        .chain(d3rs::predefined_formats::PREDEFINED_NATIVE.iter().copied())
        .chain(times)
        .chain(GRAIN_NAMES)
        .chain(TEXT_NAMES)
        .filter(|n| keep(n))
        .map(str::to_string)
        .collect()
}

fn list(names: impl IntoIterator<Item = impl Into<Value>>) -> Value {
    Value::Array(names.into_iter().map(Into::into).collect())
}

/// A format as authored: a bare name or spec, or the `FormatConfig` mapping.
#[derive(Debug, Clone, Default, PartialEq)]
struct Fmt {
    spec: Option<String>,
    prefix: Option<String>,
    suffix: Option<String>,
    notation: Option<String>,
    sign_placement: Option<String>,
    repeat: Option<String>,
}

impl Fmt {
    fn named(spec: &str) -> Self {
        Self {
            spec: Some(spec.to_string()),
            ..Self::default()
        }
    }

    fn from_json(value: &Value) -> Option<Self> {
        let text = |key: &str| value.get(key).and_then(Value::as_str).map(str::to_string);
        match value {
            Value::String(s) => Some(Self::named(s)),
            Value::Object(_) => Some(Self {
                spec: text("spec"),
                prefix: text("prefix"),
                suffix: text("suffix"),
                notation: text("notation"),
                sign_placement: text("sign_placement"),
                repeat: text("repeat"),
            }),
            _ => None,
        }
    }

    fn from_config(config: &board_model::primitives::FormatConfig) -> Self {
        Self {
            spec: config.spec.clone(),
            prefix: config.prefix.clone(),
            suffix: config.suffix.clone(),
            notation: config.notation.map(|n| n.as_str().to_string()),
            sign_placement: config.sign_placement.map(|n| n.as_str().to_string()),
            repeat: config.repeat.map(|n| n.as_str().to_string()),
        }
    }

    fn spec(&self) -> &str {
        self.spec.as_deref().unwrap_or_default()
    }

    fn has_affix(&self) -> bool {
        self.prefix.as_deref().is_some_and(|p| !p.is_empty())
            || self.suffix.as_deref().is_some_and(|s| !s.is_empty())
            || self.notation.is_some()
    }
}

type Aliases = IndexMap<String, Fmt>;

/// `grain_is_redefined`: following `name` through `aliases` ends at something other than an
/// engine grain name.
fn grain_is_redefined(name: &str, aliases: &Aliases) -> bool {
    let (mut seen, mut target) = (BTreeSet::new(), Some(name));
    while let Some(t) = target.filter(|t| aliases.contains_key(*t) && !seen.contains(*t)) {
        seen.insert(t);
        target = aliases[t].spec.as_deref();
    }
    target.is_some_and(|t| !t.is_empty() && !is_grain(t))
}

/// The register an alias chain sets, as `resolve_format_parts` reads it.
fn chain_notation(value: &Fmt, aliases: &Aliases) -> Option<String> {
    let mut seen = BTreeSet::new();
    let mut current = value.spec.as_deref()?;
    value.notation.clone().or_else(|| loop {
        if !seen.insert(current) {
            return None;
        }
        let target = aliases.get(current)?;
        if target.notation.is_some() {
            return target.notation.clone();
        }
        current = target.spec.as_deref().filter(|s| *s != current)?;
    })
}

fn affix_time(path: &str, spec: &str) -> Diagnostic {
    diag::err_format_affix_time_unsupported(path, spec)
}

fn check_time_affix(value: &Fmt, path: &str, spec: &str) -> Result<(), Diagnostic> {
    match value.has_affix() {
        true => Err(affix_time(path, spec)),
        false => Ok(()),
    }
}

fn invalid(
    kind: &str,
    spec: &str,
    path: &str,
    explanation: String,
    available: Option<&BTreeSet<String>>,
) -> Diagnostic {
    let mut d = diag::err_format_invalid(kind, spec, path, explanation);
    if let Some(names) = available {
        d = d.with_sink_param("available", list(names.iter().cloned()));
    }
    d
}

fn number_explanation(spec: &str, error: &ParseError) -> String {
    if spec.contains(['[', '"', '#', ';']) || spec.contains(",,") || spec.contains("0.0") {
        return "spreadsheet number codes are not supported. For thousands, millions and billions write \
                `compact` (money: `currency_compact`); otherwise use a d3-format spec such as `,.0f`."
            .to_string();
    }
    let reason = match error {
        ParseError::Precision { .. } => "precision requires at least one digit after '.'".to_string(),
        ParseError::Type { found, .. } => format!(
            "invalid type {}; the engine draws the types f, %, s, d, r, g, p, e, or none for the default",
            py_str_repr(&found.to_string())
        ),
        ParseError::Trailing { rest, .. } => format!("unexpected characters {} after type", py_str_repr(rest)),
    };
    format!(
        "it is not an engine-predefined format name, not a key in `style.formats`, and is not a valid \
         d3-format spec ({reason} at position {}).",
        error.position()
    )
}

/// Where a slot sits: what the field names above it say about who paints it.
#[derive(Clone, Copy, Default)]
struct Slot {
    time_capable: bool,
    vega_painted: bool,
    reject_time_affix: bool,
}

/// `_validate_spec`: the first reason `value` resolves to an unusable spec.
fn validate_spec(value: &Fmt, aliases: &Aliases, path: &str, slot: Slot) -> Result<(), Diagnostic> {
    let Slot {
        time_capable,
        vega_painted,
        reject_time_affix,
    } = slot;
    if value.prefix.as_deref().is_some_and(|p| !p.is_empty())
        && value.repeat.as_deref() == Some("anchor")
        && value.sign_placement.as_deref() == Some("before_prefix")
    {
        return Err(diag::err_format_sign_before_anchored_prefix(path));
    }
    let spec = value.spec();
    if spec.is_empty() {
        return Ok(());
    }
    if is_engine_name(spec) {
        if is_grain(spec) && !grain_is_redefined(spec, aliases) {
            return Err(diag::err_format_grain_name_not_redefined(
                spec,
                path,
                format_spec::grain_strftime(spec),
            ));
        }
        if vega_painted && PYTHON_ONLY_NATIVE.contains(&spec) {
            let available = engine_names(|n| {
                !PYTHON_ONLY_NATIVE.contains(&n) && !is_grain(n) && !is_text_name(n)
            });
            return Err(diag::err_format_native_in_vega_slot(
                spec,
                path,
                list(available),
            ));
        }
        if vega_painted && is_text_name(spec) {
            return Err(diag::err_format_text_in_vega_slot(spec, path));
        }
        if reject_time_affix && is_time_name(spec) {
            check_time_affix(value, path, spec)?;
        }
        return Ok(());
    }
    if let Some(aliased) = aliases.get(spec) {
        let aliased_spec = aliased.spec();
        let parts = |from: &Fmt| Fmt {
            spec: None,
            prefix: from.prefix.clone().filter(|p| !p.is_empty()),
            suffix: from.suffix.clone().filter(|s| !s.is_empty()),
            notation: chain_notation(value, aliases),
            ..Fmt::default()
        };
        // An explicit affix wins over the alias's (`get_format_prefix_suffix`).
        let held = if value.prefix.as_deref().is_some_and(|p| !p.is_empty())
            || value.suffix.as_deref().is_some_and(|s| !s.is_empty())
        {
            parts(value)
        } else {
            parts(aliased)
        };
        if is_engine_name(aliased_spec) {
            let resolved = Fmt {
                spec: Some(aliased_spec.to_string()),
                ..held
            };
            return validate_spec(&resolved, aliases, path, slot);
        }
        if !aliased_spec.is_empty()
            && reject_time_affix
            && format_spec::is_time_format(aliased_spec)
        {
            let effective = if held.has_affix() { &held } else { value };
            check_time_affix(effective, path, spec)?;
        }
        if time_capable
            && vega_painted
            && !aliased_spec.is_empty()
            && format_spec::is_time_format(aliased_spec)
        {
            time_directives(aliased_spec, path)?;
        }
        return Ok(());
    }
    if time_capable && format_spec::is_time_format(spec) {
        if let Some(bad) = format_spec::find_unsupported_directive(spec) {
            let accepted = if vega_painted {
                format_spec::VEGA_SAFE_DIRECTIVES
            } else {
                format_spec::STRFTIME_DIRECTIVES
            };
            let mut letters: Vec<char> = accepted.chars().collect();
            letters.sort_unstable();
            let accepted = letters
                .iter()
                .map(|d| format!("%{d}"))
                .collect::<Vec<_>>()
                .join(", ");
            let explanation = format!(
                "%{bad} is not a strftime directive the engine implements. Accepted directives: {accepted}."
            );
            return Err(invalid("date/time", spec, path, explanation, None));
        }
        if reject_time_affix {
            check_time_affix(value, path, spec)?;
        }
        if vega_painted {
            time_directives(spec, path)?;
        }
        return Ok(());
    }
    let pool = || {
        let mut names = engine_names(|_| true);
        names.extend(aliases.keys().cloned());
        names
    };
    let kind = match format_spec::parse(spec) {
        Err(error) => {
            let explanation = number_explanation(spec, &error);
            return Err(invalid("number", spec, path, explanation, Some(&pool())));
        }
        Ok(kind) => kind,
    };
    match kind {
        Some(letter) if !ENGINE_NUMBER_TYPES.contains(letter) => {
            let types = ENGINE_NUMBER_TYPES
                .chars()
                .map(String::from)
                .collect::<Vec<_>>()
                .join(", ");
            let explanation = format!(
                "type {} is not one the engine draws. Use one of {types}, or omit the type.",
                py_str_repr(&letter.to_string())
            );
            Err(invalid("number", spec, path, explanation, Some(&pool())))
        }
        _ => Ok(()),
    }
}

/// `_raise_if_unsupported_time_directives`.
fn time_directives(spec: &str, path: &str) -> Result<(), Diagnostic> {
    let bad = format_spec::vega_unsupported_directives(spec);
    if bad.is_empty() {
        return Ok(());
    }
    let named = |letters: &mut dyn Iterator<Item = char>| {
        letters
            .map(|d| format!("%{d}"))
            .collect::<Vec<_>>()
            .join(", ")
    };
    let mut safe: Vec<char> = format_spec::VEGA_SAFE_DIRECTIVES.chars().collect();
    safe.sort_unstable();
    Err(diag::err_format_time_directive_unsupported(
        spec,
        path,
        named(&mut bad.into_iter()),
        named(&mut safe.into_iter()),
    ))
}

/// `_check_redefinition_kind`: `style.formats.<engine name>` keeps the engine name's kind.
fn check_redefinition_kind(name: &str, target: &Fmt) -> Result<(), Diagnostic> {
    if !is_engine_name(name) {
        return Ok(());
    }
    let path = format!("style.formats.{name}");
    if is_text_name(name) {
        return Err(diag::err_format_text_redefined(path, name));
    }
    let time = is_time_name(name);
    let spec = target.spec();
    if spec.is_empty() {
        let explanation = "redefining an engine name needs a `spec`; an affix alone would leave every slot that reads it with no spec.";
        return Err(diag::err_format_invalid(
            if time { "date/time" } else { "number" },
            name,
            path,
            explanation,
        )
        .with_sink_param("available", list(Vec::<String>::new())));
    }
    let spec_is_time = is_time_name(spec) && is_engine_name(spec)
        || (!is_engine_name(spec) && format_spec::is_time_format(spec));
    if spec_is_time != time {
        let (kind, hatch) = match time {
            true => ("time", "A strftime spec (e.g. '%b %Y') or a `style.formats` alias of your own works here too."),
            false => ("number", "A raw d3 number spec (e.g. ',.0f') or a `style.formats` alias of your own works here too."),
        };
        let available = engine_names(|n| {
            (if time {
                is_time_name(n)
            } else {
                is_number_name(n)
            }) && !PYTHON_ONLY_NATIVE.contains(&n)
                && !is_grain(n)
        });
        return Err(diag::err_format_kind_mismatch(
            spec,
            path,
            kind,
            list(available),
            hatch,
        ));
    }
    Ok(())
}

/// `_check_reserved_alias`: an alias named `auto` would be ignored on an axis.
fn check_reserved_alias(name: &str) -> Result<(), Diagnostic> {
    match name == AUTO {
        true => Err(diag::err_format_invalid(
            "number",
            name,
            format!("style.formats.{name}"),
            "`auto` is reserved: an axis's `labels.format: auto` means the engine's default. Name the alias something else.",
        )),
        false => Ok(()),
    }
}

/// `_check_alias_cycle`: `name` leads into a circle of aliases.
fn check_alias_cycle(name: &str, aliases: &Aliases) -> Result<(), Diagnostic> {
    let mut chain = vec![name];
    let mut spec = name;
    while let Some(target) = aliases.get(spec) {
        let Some(following) = target.spec.as_deref().filter(|f| *f != spec) else {
            return Ok(());
        };
        if chain.contains(&following) {
            chain.push(following);
            return Err(diag::err_format_alias_cycle(name, chain.join(" -> ")));
        }
        chain.push(following);
        spec = following;
    }
    Ok(())
}

/// Whether *key* names one of *returned*, ignoring letter case as a result-column reference does.
fn names_a_column(key: &str, returned: &BTreeSet<&str>) -> bool {
    let folded = key.to_lowercase();
    returned.iter().any(|column| column.to_lowercase() == folded)
}

/// One query's `formats:`, as `_validate_query_formats` reads it. `returned` is the column set of
/// a values query whose columns are literal.
fn check_query_formats(
    query: &str,
    formats: &[(&str, &str)],
    aliases: &Aliases,
    returned: &BTreeSet<&str>,
) -> Result<(), Diagnostic> {
    for (column, name) in formats {
        let path = format!("queries.{query}.formats.{column}");
        if is_grain(name) && !grain_is_redefined(name, aliases) {
            return Err(diag::err_format_grain_name_not_redefined(
                *name,
                path,
                format_spec::grain_strftime(name),
            ));
        }
        if is_engine_name(name) || aliases.contains_key(*name) {
            continue;
        }
        if format_spec::parse(name).is_ok() || format_spec::is_time_format(name) {
            return Err(diag::err_query_format_spec(query, *column, *name));
        }
        let mut available = engine_names(|n| !is_grain(n));
        available.extend(aliases.keys().cloned());
        return Err(diag::err_query_format_unknown(
            query,
            *column,
            *name,
            list(available),
        ));
    }
    if let Some((column, _)) = formats
        .iter()
        .find(|(c, _)| !returned.is_empty() && !names_a_column(c, returned))
    {
        let columns = returned.iter().copied().collect::<Vec<_>>().join(", ");
        return Err(diag::err_query_format_column_missing(
            query, *column, columns,
        ));
    }
    Ok(())
}

/// A format slot found in a tree, by the path under it.
struct Found {
    path: String,
    fmt: Fmt,
    slot: Slot,
}

/// The path's field names: a segment under `columns` or `formats` is a map key (a column or an
/// alias the author named), which must not widen or narrow what its siblings accept.
fn fields<'a>(segments: &[&'a str]) -> Vec<&'a str> {
    let mut keyed = false;
    let mut out = Vec::new();
    for segment in segments {
        if !keyed {
            out.push(*segment);
        }
        keyed = !keyed && ["columns", "formats"].contains(segment);
    }
    out
}

fn slot_of(fields: &[&str]) -> Slot {
    let any = |names: &[&str]| fields.iter().any(|s| names.contains(s));
    Slot {
        time_capable: any(&TIME_CAPABLE),
        vega_painted: any(&VEGA_PAINTED),
        reject_time_affix: any(&AXIS_FIELDS),
    }
}

fn visit(path: &str, value: &Value, out: &mut Vec<Found>) {
    let segments: Vec<&str> = path.split('.').collect();
    let fields = fields(&segments);
    // An alias the author named `format` is a table entry, not a slot.
    if fields.last() == Some(&"format") {
        if let Some(fmt) = Fmt::from_json(value) {
            return out.push(Found {
                path: path.to_string(),
                fmt,
                slot: slot_of(&fields),
            });
        }
    }
    match value {
        Value::Object(map) => {
            for (key, v) in map.iter().filter(|(k, _)| *k != "query") {
                visit(&format!("{path}.{key}"), v, out);
            }
        }
        Value::Array(items) => {
            for (i, v) in items.iter().enumerate() {
                visit(&format!("{path}.{i}"), v, out);
            }
        }
        _ => {}
    }
}

/// Every format slot among the set leaves of `patch`, in field order, each once.
fn slots_of(patch: &impl LeafValues) -> Vec<Found> {
    let mut leaves: Vec<(String, Value)> = Vec::new();
    patch.leaf_values("", &mut |p, v| leaves.push((p.to_string(), v.spell())));
    let mut out = Vec::new();
    for (path, value) in leaves
        .iter()
        .filter(|(p, _)| p != "query" && !p.starts_with("query."))
    {
        visit(path, value, &mut out);
    }
    let mut seen = BTreeSet::new();
    out.retain(|f| seen.insert(f.path.clone()));
    out
}

fn chart_slots(chart: &ChartAuthored) -> Vec<Found> {
    use ChartAuthored as C;
    macro_rules! walk {
        ($c:expr) => {
            slots_of(&$c.clone().to_patch())
        };
    }
    match chart {
        C::Scatter(c) => walk!(c),
        C::Bar(c) | C::Histogram(c) | C::Funnel(c) => walk!(c),
        C::Line(c) => walk!(c),
        C::Area(c) => walk!(c),
        C::Callout(c) => walk!(c),
        C::Heatmap(c) => walk!(c),
        C::Kpi(c) => walk!(c),
        C::Pie(c) | C::Donut(c) => walk!(c),
        C::SparkBar(c) => walk!(c),
        C::Table(c) => walk!(c),
        C::Geoshape(c) | C::Map(c) => walk!(c),
        C::PointMap(c) | C::BubbleMap(c) => walk!(c),
        C::Custom(_) => Vec::new(),
    }
}

fn aliases_of(scope: &Resolved<Style>) -> Aliases {
    scope
        .total
        .formats
        .iter()
        .map(|(k, v)| (k.clone(), Fmt::from_config(v)))
        .collect()
}

/// A board's alias table must be free of circles.
fn alias_cycles(aliases: &Aliases) -> Result<(), Diagnostic> {
    aliases
        .keys()
        .try_for_each(|alias| check_alias_cycle(alias, aliases))
}

/// Each alias is a usable name, keeps an engine name's kind and has a target that resolves.
fn alias_targets(aliases: &Aliases) -> Result<(), Diagnostic> {
    for (alias, target) in aliases {
        check_reserved_alias(alias)?;
        check_redefinition_kind(alias, target)?;
        // Only a grain name resolves through the table; any other target is an engine name or a d3 spec.
        let table = if is_grain(target.spec()) {
            aliases
        } else {
            &Aliases::new()
        };
        let slot = Slot {
            time_capable: true,
            vega_painted: is_engine_name(alias),
            reject_time_affix: true,
        };
        validate_spec(target, table, &format!("style.formats.{alias}"), slot)?;
    }
    Ok(())
}

/// One query's `formats:`, with the columns a values query returns.
fn query_formats(name: &str, query: &QueryAuthored, aliases: &Aliases) -> Result<(), Diagnostic> {
    let (formats, returned) = match query {
        QueryAuthored::Sql(q) => (&q.base.formats, BTreeSet::new()),
        QueryAuthored::Schema(q) => (&q.base.formats, BTreeSet::new()),
        QueryAuthored::Values(q) => {
            let returned = match &q.columns {
                Tri::Value(columns) if !columns.is_empty() => {
                    columns.iter().map(String::as_str).collect()
                }
                _ => q
                    .rows
                    .iter()
                    .flatten()
                    .flat_map(|r| r.0.keys().map(String::as_str))
                    .collect(),
            };
            (&q.base.formats, returned)
        }
    };
    let declared: Vec<(&str, &str)> = formats
        .iter()
        .filter_map(|(c, n)| Some((c.as_str(), n.as_ref()?.as_str())))
        .collect();
    check_query_formats(name, &declared, aliases, &returned)
}

/// The key of the hoisted query a chart declares inline (a callout has none).
fn inline_query(chart: &ChartAuthored) -> Option<String> {
    let mut chart = chart.clone();
    match chart.query_mut()? {
        Tri::Value(ChartQuery::Hoisted(id)) if id.name().is_inline() => {
            Some(id.name().as_str().to_string())
        }
        _ => None,
    }
}

fn refuse(d: Diagnostic) -> CompileError {
    CompileError::Invalid(vec![d])
}

/// A position's sort key in layout post-order (`@rows[2].cols[0]`): indexes by value, a board
/// after the boards below it.
fn post_order(position: &str) -> Vec<(u8, u64, String)> {
    let mut key: Vec<_> = position
        .split(['.', '[', ']'])
        .filter(|part| !part.is_empty())
        .map(|part| match part.parse::<u64>() {
            Ok(n) => (0, n, String::new()),
            Err(_) => (0, 0, part.to_string()),
        })
        .collect();
    key.push((1, 0, String::new()));
    key
}

/// Python's `validate_board_format_specs`, over each nested board's own scope and then the root's:
/// a scope's alias cycles, named queries, alias targets, authored style, then its
/// charts, each with its inline query. `public` names a chart as users see it; `owner` is the
/// nested scope that holds a chart. A named query is judged against every scope's names: the
/// hoist no longer says which board declared it.
pub fn format_checks(
    patch: &Patch,
    root: &Resolved<Style>,
    nested: &BTreeMap<String, Resolved<Style>>,
    owner: &dyn Fn(&str) -> Option<String>,
    authored: &BTreeMap<String, <Style as Leaf>::Patch>,
    query_boards: &BTreeMap<String, String>,
    public: &dyn Fn(&str) -> Result<String, CompileError>,
) -> Result<(), CompileError> {
    let root_aliases = aliases_of(root);
    let tables: BTreeMap<&str, Aliases> = nested
        .iter()
        .map(|(k, s)| (k.as_str(), aliases_of(s)))
        .collect();
    let inline: BTreeMap<&str, String> = patch
        .body
        .charts
        .iter()
        .filter_map(|(k, c)| Some((k.as_str(), inline_query(c.as_ref()?)?)))
        .collect();
    let mut known = root_aliases.clone();
    tables.values().for_each(|t| known.extend(t.clone()));
    let named_queries = || {
        let inlined: BTreeSet<&String> = inline.values().collect();
        patch
            .body
            .queries
            .iter()
            .filter(move |(k, _)| !inlined.contains(k))
    };
    // A nested board is checked before the board that holds it, in layout order.
    let mut order: Vec<&String> = nested.keys().collect();
    order.sort_by_key(|key| post_order(key));
    let scopes = order
        .into_iter()
        .map(|key| Some(key.as_str()))
        .chain([None]);
    for scope_key in scopes {
        let aliases = scope_key
            .and_then(|key| tables.get(key))
            .unwrap_or(&root_aliases);
        alias_cycles(aliases).map_err(refuse)?;
        for (key, query) in named_queries() {
            let Some(query) = query else { continue };
            if query_boards.get(key).map(String::as_str) == scope_key {
                query_formats(key, query, &known).map_err(refuse)?;
            }
        }
        alias_targets(aliases).map_err(refuse)?;
        let own = match scope_key {
            Some(key) => authored.get(key).map(slots_of),
            None => match &patch.body.style {
                Tri::Value(style) => Some(slots_of(&**style)),
                _ => None,
            },
        };
        for f in own.into_iter().flatten() {
            validate_spec(&f.fmt, aliases, &format!("style.{}", f.path), f.slot).map_err(refuse)?;
        }
        for (key, chart) in &patch.body.charts {
            let Some(chart) = chart else { continue };
            if owner(key).as_deref() != scope_key {
                continue;
            }
            let id = public(key)?;
            let hoisted = inline
                .get(key.as_str())
                .and_then(|q| patch.body.queries.get(q));
            if let Some(Some(query)) = hoisted {
                query_formats(&id, query, aliases).map_err(refuse)?;
            }
            for f in chart_slots(chart) {
                validate_spec(&f.fmt, aliases, &format!("charts.{id}.{}", f.path), f.slot)
                    .map_err(refuse)?;
            }
        }
    }
    Ok(())
}
