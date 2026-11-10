//! `chart/authored/_support_table.py`: the mini data-grid attached to a bar, line or area chart.
//!
//! A `support_table:` is the entries themselves or `{entries: [...]}`; an entry is a bare column
//! name, `{source: ..}`, `{aggregate: .., source: ..}` or `{per_series: ..}`, told apart by which
//! of `aggregate` / `per_series` it holds. Both shorthands are accepted here, once.
//!
//! The cross-entry rules are the one hand-written part ([`shape`]): a duplicate row has no
//! `constraint` kind, which tests a field's value or a list's items by pattern, never two items
//! against each other.

use crate::facet::DisplayText;
use crate::primitives::FormatSpec;
use crate::string_enum;
use model_core::{Cx, Entry, FromNode, Kind, Leaf, Node, ReadScalar, Spell};
use model_derive::Model;
use std::collections::HashSet;

string_enum!(
    /// Aggregate operation of a support-table row.
    SupportTableAggregateOp {
        Sum = "sum",
        Avg = "avg",
        Min = "min",
        Max = "max",
        Median = "median",
        Count = "count",
        CountDistinct = "count_distinct",
    }
);

/// A row that reads a column's raw per-x value; a bare string is `{source: name}`.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "source")]
pub struct SupportTableSource {
    /// Query column the row reads from (per-x raw value).
    pub source: String,
    /// D3 format string or format config object. Inherits the chart measure format when omitted and source matches chart.y.
    #[model(absent = "the chart measure format when source matches chart.y")]
    pub format: Option<FormatSpec>,
    /// Left-stub row label.
    #[model(absent = "the column name")]
    pub label: Option<DisplayText>,
}

/// A row that reads an aggregate of a column grouped by x.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SupportTableAggregate {
    /// Aggregate operation applied per x-group. Exact names only: no aliases.
    pub aggregate: SupportTableAggregateOp,
    /// Query column being aggregated. Always required alongside `aggregate:`.
    pub source: String,
    /// D3 format string or format config object for the aggregated value.
    #[model(absent = "the column's own format")]
    pub format: Option<FormatSpec>,
    /// Column header label override for this row.
    #[model(absent = "the column name")]
    pub label: Option<DisplayText>,
}

/// An entry that expands into one row per `color:` series.
///
/// With `by_measure: true` it expands into a single row reading the named y field directly,
/// without a color channel: the mode for multi-y charts (`y: [revenue, margin]`) where each
/// measure is its own series.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SupportTablePerSeries {
    /// Query column the row reads from (per-x, per-series value).
    pub per_series: String,
    /// When true, expand one row reading the named measure field directly (no color: groupby). Required for multi-y charts where each measure is its own y-field rather than a color-encoded series.
    #[model(fill = false)]
    pub by_measure: bool,
    /// Row label for a by_measure row; unset on one, the per_series column name is used. Also the header of a horizontal grouped bar's single per_series column. Not allowed on a non-by_measure strip row or stacked-bar column.
    #[model(absent = "the per_series column name")]
    pub label: Option<DisplayText>,
    /// D3 format string or format config object.
    #[model(absent = "the column's own format")]
    pub format: Option<FormatSpec>,
}

/// One support-table row, told apart by its keys.
#[derive(Debug, Clone, PartialEq)]
pub enum SupportTableEntry {
    Source(<SupportTableSource as Leaf>::Whole),
    Aggregate(<SupportTableAggregate as Leaf>::Whole),
    PerSeries(<SupportTablePerSeries as Leaf>::Whole),
}

impl ReadScalar for SupportTableEntry {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if let Kind::Map(m) = &node.kind {
            if m.contains_key("aggregate") {
                return <SupportTableAggregate as FromNode>::whole(node, cx).map(Self::Aggregate);
            }
            if m.contains_key("per_series") {
                return <SupportTablePerSeries as FromNode>::whole(node, cx).map(Self::PerSeries);
            }
        }
        <SupportTableSource as FromNode>::whole(node, cx).map(Self::Source)
    }
}
impl Spell for SupportTableEntry {
    fn spell(&self) -> serde_json::Value {
        match self {
            Self::Source(s) => {
                let spelled = s.spell();
                match spelled.get("source") {
                    Some(source) if spelled.as_object().is_some_and(|m| m.len() == 1) => {
                        source.clone()
                    }
                    _ => spelled,
                }
            }
            Self::Aggregate(a) => a.spell(),
            Self::PerSeries(p) => p.spell(),
        }
    }
}

union_leaf!(
    SupportTableEntry,
    ::model_core::TypeExpr::Either(
        &::model_core::TypeExpr::Ref(
            "SupportTableSource",
            <SupportTableSource as ::model_core::Model>::info
        ),
        &::model_core::TypeExpr::Either(
            &::model_core::TypeExpr::Ref(
                "SupportTableAggregate",
                <SupportTableAggregate as ::model_core::Model>::info
            ),
            &::model_core::TypeExpr::Ref(
                "SupportTablePerSeries",
                <SupportTablePerSeries as ::model_core::Model>::info
            ),
        ),
    )
);

/// The container of a chart's `support_table:` block.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(requires(entries, nonempty(entries))))]
pub struct SupportTable {
    /// List of support-table entries (source, aggregate, or per-series rows). A source entry may be written as a bare column name (`revenue` in place of `{source: revenue}`), the same scalar-listable spelling `y:` uses.
    pub entries: Vec<SupportTableEntry>,
}

/// A `support_table:` value: the block, or the bare entry list.
#[derive(Debug, Clone, PartialEq)]
pub struct SupportTableSpec(pub <SupportTable as Leaf>::Whole);

impl ReadScalar for SupportTableSpec {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if matches!(&node.kind, Kind::Seq(items) if items.is_empty()) {
            cx.invalid(
                node.span,
                "List should have at least 1 item after validation, not 0",
            );
            return None;
        }
        if matches!(node.kind, Kind::Seq(_)) {
            let map = [(
                "entries".to_string(),
                Entry::string_key(node.span, "entries", node.clone()),
            )]
            .into_iter()
            .collect();
            let wrapped = Node {
                span: node.span,
                kind: Kind::Map(map),
            };
            return <SupportTable as FromNode>::whole(&wrapped, cx).map(Self);
        }
        <SupportTable as FromNode>::whole(node, cx).map(Self)
    }
}
impl Spell for SupportTableSpec {
    fn spell(&self) -> serde_json::Value {
        self.entries().iter().map(Spell::spell).collect()
    }
}

union_leaf!(
    SupportTableSpec,
    ::model_core::TypeExpr::Either(
        &::model_core::TypeExpr::Ref("SupportTable", <SupportTable as ::model_core::Model>::info),
        &::model_core::TypeExpr::Keyed(
            &::model_core::Keywords::at_least_items(1),
            &::model_core::TypeExpr::List(&::model_core::TypeExpr::Either(
                &::model_core::TypeExpr::Ref(
                    "SupportTableSource",
                    <SupportTableSource as ::model_core::Model>::info
                ),
                &::model_core::TypeExpr::Either(
                    &::model_core::TypeExpr::Ref(
                        "SupportTableAggregate",
                        <SupportTableAggregate as ::model_core::Model>::info
                    ),
                    &::model_core::TypeExpr::Either(
                        &::model_core::TypeExpr::Ref(
                            "SupportTablePerSeries",
                            <SupportTablePerSeries as ::model_core::Model>::info
                        ),
                        &::model_core::TypeExpr::Scalar(::model_core::Scalar::Str),
                    ),
                ),
            )),
        ),
    )
);

impl SupportTableSpec {
    /// The entries, in authored order.
    pub fn entries(&self) -> &[SupportTableEntry] {
        self.0.entries.as_deref().unwrap_or_default()
    }

    /// Data-free validation of the entry list: no row appears twice
    /// (`validate_support_table_shape`). Messages are Python's `ValueError` texts.
    pub fn shape(&self) -> Vec<String> {
        let mut seen: HashSet<(String, Option<String>, bool)> = HashSet::new();
        let mut out = Vec::new();
        for entry in self.entries() {
            let (key, label) = match entry {
                SupportTableEntry::PerSeries(e) => {
                    let Some(name) = e.per_series.clone() else {
                        continue;
                    };
                    let label = format!(
                        "chart.support_table has duplicate per_series entries for {{per_series: {}}}. Remove the duplicate.",
                        diagnostic::py_repr(&serde_json::Value::String(name.clone()))
                    );
                    ((name, None, true), label)
                }
                SupportTableEntry::Aggregate(e) => {
                    let (Some(agg), Some(source)) = (e.aggregate, e.source.clone()) else {
                        continue;
                    };
                    let agg = agg.as_str().to_string();
                    let what = format!("{{aggregate: {agg}, source: {source}}}");
                    ((source, Some(agg), false), duplicate(&what))
                }
                SupportTableEntry::Source(e) => {
                    let Some(source) = e.source.clone() else {
                        continue;
                    };
                    let what = format!("{{source: {source}}}");
                    ((source, None, false), duplicate(&what))
                }
            };
            if !seen.insert(key) {
                out.push(label);
            }
        }
        out
    }
}

fn duplicate(what: &str) -> String {
    format!(
        "chart.support_table has duplicate entries for {what}. Remove the duplicate or differentiate by aggregate operation."
    )
}
