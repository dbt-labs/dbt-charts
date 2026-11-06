//! The 0.6.0 -> 0.7.0 boundary: dead `style.page` and `style.color` removed,
//! `conditional_formatting` retired from eleven chart families (kept on `table`
//! and `kpi`), `axis*.grid.zero` -> `grid.threshold`, and pie/donut
//! `total.format` -> `style.total.value.format`.
//!
//! `style.color` is a bare tail: the live-grammar gate fires it only where the
//! current grammar stopped declaring it, which is a board's own style block.

use crate::decl::{Declarations, Deletion, Grammars};

const THRESHOLD_RENAMES: &[(&[&str], &[&str])] = &[(&["grid", "threshold"], &["grid", "zero"])];
const TOTAL_FORMAT_RENAMES: &[(&[&str], &[&str])] =
    &[(&["style", "total", "value", "format"], &["total", "format"])];

/// One entry per authored `type:` literal, not per model class.
const CONDITIONAL_FORMATTING_RETIRED: &[&str] = &[
    "bar",
    "histogram",
    "line",
    "area",
    "scatter",
    "pie",
    "donut",
    "map",
    "geoshape",
    "point_map",
    "bubble_map",
];

const PAGE_REASON: &str = "The page canvas this board explicitly requested is gone; the standalone-HTML export, dct serve, and Cloud's HTML download now use this board's own style.background instead, which is what page.background already equaled on every shipped theme, a no-op unless this board's page.background diverged from its own background.";

pub fn declarations(g: &Grammars) -> Declarations {
    let mut page = Deletion::new(g.source, g.target, &["style", "page"]);
    page.reason = Some(PAGE_REASON.into());
    let mut deletions = vec![page, Deletion::new(g.source, g.target, &["style", "color"])];
    for chart_type in CONDITIONAL_FORMATTING_RETIRED {
        let mut d = Deletion::new(g.source, g.target, &["conditional_formatting"]);
        d.reason = Some(format!(
            "{chart_type} charts no longer support rule-driven styling (conditional_formatting). Use table or kpi for conditional formatting."
        ));
        d.chart_type = Some((*chart_type).to_string());
        deletions.push(d);
    }
    let renames: Vec<_> = THRESHOLD_RENAMES
        .iter()
        .chain(TOTAL_FORMAT_RENAMES)
        .copied()
        .collect();
    Declarations {
        moves: g.suffix_rename_moves(&renames, None, None),
        deletions,
        ..Declarations::default()
    }
}
