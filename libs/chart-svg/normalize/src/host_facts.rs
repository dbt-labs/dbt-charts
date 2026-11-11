//! The facts the host's checks (`dct validate`) read off a compiled board, as versioned JSON
//! (D-35's shape): every query, the columns each chart's channels name, the charts that bind by
//! position, the authored links and the warnings each chart suppresses. They come from the folded
//! patch, so no run, data or variable values are needed.
//!
//! A query is keyed as Python's registry keys the ones the author wrote (`name`, or
//! `<file>#<name>`); a query normalize hoisted keeps its position key (`@charts.<pos>.query`) and
//! is `authored: false`. A chart is keyed by its public id.

use crate::chart_checks::Patch;
use crate::compile::Structure;
use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored};
use board_model::chart::{ChartAuthored, ChartQuery, LayerQuery};
use board_model::ids::QueryId;
use board_model::query::QueryAuthored;
use indexmap::IndexMap;
use model_core::{Spell, Tri};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;

/// The wire version; a reader refuses another.
const VERSION: u32 = 1;

/// A field always on the wire, null when absent.
#[cfg(feature = "schema")]
fn nullable_text(_: &mut schemars::SchemaGenerator) -> schemars::Schema {
    schemars::json_schema!({"type": ["string", "null"]})
}

/// The wire document; `dbt_charts.core.compile.host_facts.HostFacts` mirrors it field for field.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct HostFacts {
    pub version: u32,
    pub queries: IndexMap<String, HostQuery>,
    pub column_refs: IndexMap<String, Vec<ColumnRef>>,
    pub unbound_charts: Vec<UnboundChart>,
    pub links: Vec<(String, String, bool)>,
    pub warnings_ignore: IndexMap<String, Vec<String>>,
}

/// `(query, channel label, column)`.
pub type ColumnRef = (String, String, String);

#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct HostQuery {
    pub authored: bool,
    #[serde(rename = "type")]
    pub kind: QueryKind,
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub source: Option<String>,
    pub ignore: Vec<String>,
    #[serde(flatten)]
    pub sql: Option<SqlFields>,
}

/// What a SQL query carries beyond the common fields, as authored.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct SqlFields {
    pub sql: String,
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub setup_sql: Option<String>,
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub dialect: Option<String>,
}

#[derive(Serialize, Clone, Copy)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
#[serde(rename_all = "lowercase")]
pub enum QueryKind {
    Sql,
    Values,
    Schema,
}

#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct UnboundChart {
    pub chart: String,
    #[serde(rename = "type")]
    pub family: String,
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub query: Option<String>,
}

/// Python's `_FIELD_CHANNEL_KEYS` minus `geo` and `sort`, in the order its refs are listed.
const CHANNELS: [&str; 13] = [
    "background",
    "color",
    "latitude",
    "longitude",
    "lookup",
    "pattern",
    "shape",
    "size",
    "theta",
    "value",
    "x",
    "y",
    "y_start",
];

/// Per family, the channels it binds by position and the fields that also name a column
/// (Python's `CHANNELS` and `PINNED_FIELDS`); a chart naming none of them is unbound.
fn positional(family: &str) -> Option<(&'static [&'static str], &'static [&'static str])> {
    const SERIES: &[&str] = &["layers", "multiples", "tooltip"];
    Some(match family {
        "bar" => (
            &["x", "y", "color", "pattern"],
            &["y_start", "layers", "multiples", "tooltip"],
        ),
        "line" => (
            &["x", "y", "color"],
            &["connect", "layers", "multiples", "tooltip"],
        ),
        "area" => (&["x", "y", "color", "pattern"], SERIES),
        "scatter" => (&["x", "y", "color", "size", "shape"], SERIES),
        "heatmap" => (&["x", "y", "color", "pattern"], &["multiples"]),
        "pie" | "donut" => (&["theta", "color", "pattern"], &["tooltip"]),
        "kpi" => (&["value"], &[]),
        "histogram" => (&["x"], &[]),
        _ => return None,
    })
}

fn strings(v: Option<&Value>) -> Vec<String> {
    let items = v
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default();
    items
        .iter()
        .filter_map(Value::as_str)
        .map(String::from)
        .collect()
}

fn text(v: &Value) -> Option<&str> {
    v.as_str()
}

/// An empty name or list names nothing, like an absent key (Python's `_is_named`).
fn named(v: Option<&Value>) -> bool {
    !matches!(v, None | Some(Value::Null)) && v != Some(&json!("")) && v != Some(&json!([]))
}

/// `(query, label, column)` for each channel of `source` (a chart or a layer), labels led by `prefix`.
fn channel_refs(source: &Value, prefix: &str, query: &str, out: &mut Vec<ColumnRef>) {
    for field in CHANNELS {
        match source.get(field) {
            Some(Value::String(column)) => {
                out.push((query.into(), format!("{prefix}{field}"), column.clone()))
            }
            Some(Value::Array(items)) => {
                out.extend(items.iter().enumerate().filter_map(|(i, item)| {
                    text(item).map(|column| {
                        (query.into(), format!("{prefix}{field}[{i}]"), column.into())
                    })
                }))
            }
            _ => {}
        }
    }
}

fn column_refs(
    family: &str,
    chart: &Value,
    own: Option<&str>,
    layers: &[Option<String>],
) -> Vec<ColumnRef> {
    let mut out = Vec::new();
    if let Some(own) = own {
        channel_refs(chart, "", own, &mut out);
        if let Some(Value::Array(columns)) = chart.get("columns").filter(|_| family == "table") {
            out.extend(columns.iter().enumerate().filter_map(|(i, c)| {
                text(c).map(|column| (own.into(), format!("columns[{i}]"), column.into()))
            }));
        }
        if matches!(
            family,
            "bar" | "histogram" | "line" | "area" | "scatter" | "pie" | "donut"
        ) {
            if let Some(Value::Array(entries)) = chart.get("tooltip") {
                out.extend(entries.iter().enumerate().filter_map(|(i, e)| {
                    let column = text(e).or_else(|| e.get("column").and_then(text))?;
                    Some((own.into(), format!("tooltip[{i}]"), column.into()))
                }));
            }
        }
    }
    if matches!(family, "bar" | "histogram" | "line" | "area" | "scatter") {
        if let Some(Value::Array(items)) = chart.get("layers") {
            for (i, layer) in items.iter().enumerate() {
                let query = layers.get(i).and_then(Option::as_deref).or(own);
                if let Some(query) = query {
                    channel_refs(layer, &format!("layers[{i}]."), query, &mut out);
                }
            }
        }
    }
    out
}

/// The authored link slots below `node`: a table column's `link` and `header_link`, the footer's
/// `link`, and the chart's own (`parent == "@chart"`), each a URL or a `{url}` block. Yields
/// `(field_path, url, exempt_from_anchoring)`; a column's link and a table chart's own resolve
/// per row at render and are re-checked there.
fn links(node: &Value, path: &str, parent: &str, grand: &str, table: bool, out: &mut Vec<Slot>) {
    let Value::Object(map) = node else { return };
    for (key, value) in map {
        let at = format!("{path}.{key}");
        let in_column = grand == "columns" && matches!(key.as_str(), "link" | "header_link");
        let slot = in_column || (key == "link" && matches!(parent, "footer" | "@chart"));
        let exempt = in_column || (parent == "@chart" && table);
        match (slot, value) {
            (true, Value::String(url)) => out.push((at, url.clone(), exempt)),
            (true, Value::Object(block)) => {
                if let Some(url) = block.get("url").and_then(text) {
                    // Python normalizes a chart's link to its URL, so the path stays `.link`.
                    let at = if parent == "@chart" {
                        at
                    } else {
                        format!("{at}.url")
                    };
                    out.push((at, url.to_string(), exempt));
                }
            }
            _ => links(value, &at, key, parent, table, out),
        }
    }
}

/// Every nested board's and tab's own `style:` links, a board's nested ones before its own, in
/// layout order; each path starts at `style` whatever the depth, as Python's walk names them.
fn nested_links(layout: &LayoutAuthored, out: &mut Vec<Slot>) {
    fn scope(style: &Tri<Box<impl Spell>>, inner: Option<&LayoutAuthored>, out: &mut Vec<Slot>) {
        if let Some(inner) = inner {
            nested_links(inner, out);
        }
        if let Tri::Value(style) = style {
            links(&style.spell(), "style", "", "", false, out);
        }
    }
    match layout {
        LayoutAuthored::Rows(items) | LayoutAuthored::Cols(items) => {
            for item in items {
                if let LayoutItemAuthored::Board(b) = item {
                    scope(&b.body.style, b.body.layout.as_ref(), out);
                }
            }
        }
        LayoutAuthored::Grid(g) => {
            for item in g
                .items
                .iter()
                .flatten()
                .filter_map(|cell| cell.item.as_ref())
            {
                if let LayoutItemAuthored::Board(b) = item {
                    scope(&b.body.style, b.body.layout.as_ref(), out);
                }
            }
        }
        LayoutAuthored::Tabs(t) => {
            for tab in t.items.iter().flatten() {
                scope(&tab.style, tab.layout.as_ref(), out);
            }
        }
    }
}

/// `(field_path, url, exempt_from_anchoring)`.
pub(crate) type Slot = (String, String, bool);

/// Every authored link slot of the board, in Python's order (`validate/links.py`): nested boards'
/// and tabs' styles, the root's style, then each chart (`public` names it as users see it). One
/// list serves the host's link-target check and compile's scheme check.
pub(crate) fn link_slots(patch: &Patch, public: &dyn Fn(&str) -> String) -> Vec<Slot> {
    let body = &patch.body;
    let mut out = Vec::new();
    if let Some(layout) = &body.layout {
        nested_links(layout, &mut out);
    }
    if let Tri::Value(style) = &body.style {
        links(&style.spell(), "style", "", "", false, &mut out);
    }
    for (name, chart) in &body.charts {
        let Some(chart) = chart else { continue };
        links(
            &chart.spell(),
            &format!("charts.{}", public(name)),
            "@chart",
            "",
            chart.family() == "table",
            &mut out,
        );
    }
    out
}

impl Structure {
    /// The host facts; serialized, the wire JSON the Python reader parses.
    pub fn host_facts(&self) -> HostFacts {
        let body = &self.patch.body;
        let mut queries = IndexMap::new();
        for (key, query) in &body.queries {
            let Some(query) = query else { continue };
            let spelled = query.spell();
            let kind = match query {
                QueryAuthored::Sql(_) => QueryKind::Sql,
                QueryAuthored::Values(_) => QueryKind::Values,
                QueryAuthored::Schema(_) => QueryKind::Schema,
            };
            let text = |name: &str| spelled.get(name).and_then(Value::as_str).map(String::from);
            queries.insert(
                key.clone(),
                HostQuery {
                    authored: !key.starts_with('@'),
                    kind,
                    source: text("source"),
                    ignore: strings(spelled.get("ignore")),
                    sql: matches!(kind, QueryKind::Sql).then(|| SqlFields {
                        sql: text("sql").unwrap_or_default(),
                        setup_sql: text("setup_sql"),
                        dialect: text("dialect"),
                    }),
                },
            );
        }
        let key_of = |id: &QueryId| {
            let printed = id.to_string();
            match body.queries.contains_key(&printed) {
                true => printed,
                false => id.name().as_str().to_string(),
            }
        };

        let public_ids: BTreeMap<&str, &str> = self
            .index
            .public_ids
            .iter()
            .map(|(id, public)| (id.as_ref(), public.as_str()))
            .collect();
        let (mut refs, mut unbound, mut ignored) = (IndexMap::new(), Vec::new(), IndexMap::new());
        for (name, chart) in &body.charts {
            let Some(chart) = chart else { continue };
            let public = public_ids
                .get(name.as_str())
                .copied()
                .unwrap_or(name.as_str());
            let spelled = chart.spell();
            let family = chart.family();
            let mut owned: ChartAuthored = chart.clone();
            let own = owned.query_mut().and_then(|q| match q {
                Tri::Value(ChartQuery::Hoisted(id)) => Some(key_of(id)),
                _ => None,
            });
            let layers: Vec<Option<String>> = owned
                .layer_queries_mut()
                .into_iter()
                .map(|q| match q {
                    Tri::Value(LayerQuery::Hoisted(id)) => Some(key_of(id)),
                    _ => None,
                })
                .collect();
            let found = column_refs(family, &spelled, own.as_deref(), &layers);
            if !found.is_empty() {
                refs.insert(public.to_string(), found);
            }
            if let Some((channels, pinned)) = positional(family) {
                if !channels
                    .iter()
                    .chain(pinned)
                    .any(|f| named(spelled.get(*f)))
                {
                    unbound.push(UnboundChart {
                        chart: public.to_string(),
                        family: family.to_string(),
                        query: own,
                    });
                }
            }
            if let Some(codes) = spelled.get("warnings_ignore").filter(|c| named(Some(c))) {
                ignored.insert(public.to_string(), strings(Some(codes)));
            }
        }
        HostFacts {
            version: VERSION,
            queries,
            column_refs: refs,
            unbound_charts: unbound,
            links: link_slots(&self.patch, &|name| {
                public_ids.get(name).copied().unwrap_or(name).to_string()
            }),
            warnings_ignore: ignored,
        }
    }
}
