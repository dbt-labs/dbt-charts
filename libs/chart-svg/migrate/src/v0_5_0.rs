//! The 0.4.0 -> 0.5.0 boundary: removed style keys, `style.board` -> `style.frame`,
//! `allow_html` -> `html_policy`, `text.column.number` -> `max_number`, the KPI
//! `style.tone` relocation, and the chart-level `style.scale.continuous.zero`
//! relocation.

use std::sync::Arc;

use crate::decl::{
    bool_key, path, ConditionalMove, Declarations, Deletion, Grammars, Mapped, ValueMap,
};

const DELETED_TAILS: &[&[&str]] = &[
    &["legend", "interactive_legend"],
    &["kpi", "legend"],
    &["table", "legend"],
    &["bar", "tooltip"],
    &["line", "tooltip"],
    &["area", "tooltip"],
    &["scatter", "tooltip"],
    &["heatmap", "tooltip"],
    &["histogram", "tooltip"],
    &["pie", "tooltip"],
    &["geoshape", "tooltip"],
    &["point_map", "tooltip"],
    &["kpi", "tooltip"],
    &["table", "tooltip"],
    &["style", "tooltip"],
    // Three segments: a two-segment tail would delete a chart *named* animation_duration.
    &["style", "charts", "animation_duration"],
    &["area", "animation_duration"],
    &["bar", "animation_duration"],
    &["geoshape", "animation_duration"],
    &["heatmap", "animation_duration"],
    &["histogram", "animation_duration"],
    &["kpi", "animation_duration"],
    &["line", "animation_duration"],
    &["pie", "animation_duration"],
    &["point_map", "animation_duration"],
    &["scatter", "animation_duration"],
    &["table", "animation_duration"],
    &["style", "animation_duration"],
    &["marks", "square"],
    &["marks", "tick"],
    &["marks", "trail"],
    &["style", "variables", "padding"],
    &["style", "variables", "container_padding"],
    &["style", "variables", "popover_rail_background"],
    &["style", "variables", "value", "numeric_variant"],
];

const FRAME_RENAMES: &[(&[&str], &[&str])] = &[(&["frame"], &["board"])];
const HTML_POLICY_RENAMES: &[(&[&str], &[&str])] = &[(&["html_policy"], &["allow_html"])];
/// The authored value passes through; the meaning softened from a fixed count to a ceiling.
const TEXT_COLUMN_NUMBER_RENAMES: &[(&[&str], &[&str])] = &[(&["max_number"], &["number"])];

const SCALE_DROP: &str = " Add style.axis_y.scale.continuous.zero: <true|false> manually.";

fn scale_zero(g: &Grammars, chart_type: &'static str, label: &str) -> ConditionalMove {
    ConditionalMove {
        source: g.source,
        target: g.target,
        chart_type,
        old_tail: path(&["style", "scale", "continuous", "zero"]),
        new_tail: path(&["style", "axis_y", "scale", "continuous", "zero"]),
        sibling_tail: path(&["style", "axis_y"]),
        drop_warning: format!(
            "{label} chart {{chart!r}}: style.scale.continuous.zero was dropped (not migrated) because the chart has no style.axis_y: block.{SCALE_DROP}"
        ),
    }
}

pub fn declarations(g: &Grammars) -> Declarations {
    let html_policy: Arc<ValueMap> = Arc::new(
        [
            (bool_key(true), Mapped::Str("trusted-raw".into())),
            (bool_key(false), Mapped::Str("none".into())),
        ]
        .into_iter()
        .collect(),
    );
    let mut moves = g.suffix_rename_moves(FRAME_RENAMES, None, None);
    moves.extend(g.suffix_rename_moves(HTML_POLICY_RENAMES, Some(html_policy), None));
    moves.extend(g.suffix_rename_moves(TEXT_COLUMN_NUMBER_RENAMES, None, None));
    let conditional_moves = vec![
        ConditionalMove {
            source: g.source,
            target: g.target,
            chart_type: "kpi",
            old_tail: path(&["style", "tone"]),
            new_tail: path(&["support", "tone"]),
            sibling_tail: path(&["support"]),
            drop_warning: "KPI chart {chart!r}: style.tone was dropped (not migrated) because the chart has no support: block. Add a support: block with a value: column reference, then set support.tone manually.".into(),
        },
        scale_zero(g, "bar", "Bar"),
        scale_zero(g, "line", "Line"),
        scale_zero(g, "area", "Area"),
    ];
    Declarations {
        moves,
        deletions: DELETED_TAILS
            .iter()
            .map(|t| Deletion::new(g.source, g.target, t))
            .collect(),
        conditional_moves,
        ..Declarations::default()
    }
}
