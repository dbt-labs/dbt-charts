//! Two Python checks with no home in the passes before them: `check_dialect_composition`
//! (`compile/template/jinja.py`, the dialect half) and the layer half of
//! `validate/chart_color_tokens.py`.

use crate::compile::{public_id, CompileError, Structure};
use board_model::chart::layer::CartesianLayerAuthored as Layer;
use board_model::chart::ChartAuthored;
use board_model::query::QueryAuthored;
use board_model::style::charts::ChartsStyle;
use board_model::style::style::Style;
use diagnostic::{diag, Diagnostic};
use model_core::{CascadeError, Leaf, Resolved, Tri};
use std::collections::BTreeMap;

type Patch = <board_model::board::Board as Leaf>::Patch;
type StylePatch = <Style as Leaf>::Patch;

/// The names `{{ queries.NAME }}` embeds in `sql`, exactly as Python's `_QUERY_REF_RE` reads them:
/// the bare reference, not `queries.NAME.cache`, a column read or `setup_sql`.
fn embedded(sql: &str) -> Vec<&str> {
    let mut found = Vec::new();
    let mut rest = sql;
    while let Some(at) = rest.find("{{") {
        rest = rest.get(at + 2..).unwrap_or_default();
        let Some(end) = rest.find("}}") else { break };
        let inner = rest.get(..end).unwrap_or_default().trim();
        if let Some(name) = inner.strip_prefix("queries.") {
            if !name.is_empty() && name.chars().all(|c| c.is_alphanumeric() || c == '_') {
                found.push(name);
            }
        }
    }
    found
}

/// Every `{{ queries.X }}` embedding must share its host's `dialect:`, an unset one counting as a
/// value. Python raises at the first violation.
pub fn dialect_mixed(patch: &Patch) -> Result<(), CompileError> {
    let queries = &patch.body.queries;
    for (name, query) in queries {
        let Some(QueryAuthored::Sql(host)) = query else {
            continue;
        };
        let Some(sql) = host.sql.as_deref() else {
            continue;
        };
        for dep_name in embedded(sql) {
            let Some(Some(QueryAuthored::Sql(dep))) = queries.get(dep_name) else {
                continue;
            };
            let (dialect, dep_dialect) = (label(&host.dialect), label(&dep.dialect));
            if dialect != dep_dialect {
                return Err(CompileError::Invalid(vec![diag::err_query_dialect_mixed(
                    name.as_str(),
                    dialect,
                    dep_name,
                    dep_dialect,
                )]));
            }
        }
    }
    Ok(())
}

fn label(dialect: &Tri<board_model::query::QueryDialect>) -> &'static str {
    match dialect {
        Tri::Value(d) => d.as_str(),
        Tri::Unset | Tri::Null => "unset",
    }
}

/// One `ERR-COLOR-TOKEN` per placed cartesian chart whose layer style names a color no palette
/// resolves, against the chart's own scope (`placed`). Python stops a chart at its first bad token.
pub fn layer_color_tokens(
    structure: &Structure,
    placed: &BTreeMap<String, Resolved<Style>>,
) -> Result<Vec<Diagnostic>, CompileError> {
    let mut found = Vec::new();
    for (pos, name) in &structure.layout.charts {
        let (Some(Some(chart)), Some(scope)) = (
            structure.patch.body.charts.get(name),
            placed.get(pos.as_str()),
        ) else {
            continue;
        };
        for layer in layers(chart) {
            let Err(CascadeError::Refused(refused)) = scope.resolved_layer(&layer) else {
                continue;
            };
            let reason = refused.first().map(|r| r.why.clone()).unwrap_or_default();
            found.push(diag::err_color_token(
                format!("chart '{}'", public_id(&structure.index, name)?),
                reason,
            ));
            break;
        }
    }
    Ok(found)
}

/// Each layer's `style:` as a board-style layer: its `marks` under its own family's slot.
fn layers(chart: &ChartAuthored) -> Vec<StylePatch> {
    let authored = match chart {
        ChartAuthored::Bar(c) => &c.layers,
        ChartAuthored::Line(c) => &c.layers,
        ChartAuthored::Area(c) => &c.layers,
        ChartAuthored::Scatter(c) => &c.layers,
        _ => return Vec::new(),
    };
    let mut out = Vec::new();
    for layer in authored.iter().flatten() {
        let mut charts = <ChartsStyle as Leaf>::Patch::default();
        match layer {
            Layer::Bar(l) => charts.bar.marks = marks(&l.style, |s| s.marks.clone()),
            Layer::Line(l) => charts.line.marks = marks(&l.style, |s| s.marks.clone()),
            Layer::Area(l) => charts.area.marks = marks(&l.style, |s| s.marks.clone()),
            Layer::Scatter(l) => charts.scatter.marks = marks(&l.style, |s| s.marks.clone()),
        }
        out.push(StylePatch {
            charts: Box::new(charts),
            ..Default::default()
        });
    }
    out
}

fn marks<S, M: Default>(slot: &Tri<S>, take: impl Fn(&S) -> M) -> M {
    style(slot).map_or_else(M::default, take)
}

fn style<T>(slot: &Tri<T>) -> Option<&T> {
    match slot {
        Tri::Value(v) => Some(v),
        Tri::Unset | Tri::Null => None,
    }
}
