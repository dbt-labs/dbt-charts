//! The 0.10.0 -> 0.11.0 boundary.
//!
//! Scaffold steps are addressed by number (`dbt-grays.muted` -> `dbt-grays.8`,
//! `chrome.heading` -> `chrome[10]`) and five stray aliases retire straight to
//! their final spelling. Neither is schema-visible, so the respell is declared
//! as identity-path moves and its value map registered in `retired_tokens` for
//! the pre-check. `style.muted` becomes the secondary font's color, with the
//! same respell applied on the way (a rewrite, since the field is gone from
//! every model). `style.frame.width` becomes the root `width`, `style.row.role`
//! on a table the chart-root `row_role`; the rest are deletions with notices.
//!
//! Reach: a board nested in a board is opaque to the field walk past the first
//! level, so the respell follows `sub_board_prefixes` instead.

use std::sync::Arc;

use serde_json::Value as Json;

use crate::decl::{
    path, Declarations, Deletion, Grammars, Mapped, Move, Rewritten, ScalarKey, ValueMap,
};

const COLOR_TOKEN_FIELD_TAILS: &[&str] = &[
    "color",
    "fill",
    "stroke",
    "null_color",
    "neutral_color",
    "glyph_color",
    "static",
    "positive",
    "negative",
    "warning",
    "info",
    "color_active",
    "color_inactive",
    "color_disabled",
    "focus_color",
    "drop_line_color",
    "accent",
    "muted",
    "background",
];
const PALETTE_NAME_FIELD_TAILS: &[&str] = &["palette", "single_series_palette"];
const CATEGORY_PIN: &[&str] = &["style", "charts", "category_colors", "*", "values", "*"];
const ROLES_AND_PALETTES: &[&[&str]] = &[&["style", "palettes", "*"], &["style", "roles", "*"]];

/// A retired scaffold job name to the numbered step at the same index of the
/// unchanged light-to-dark ladder.
const SCAFFOLD_STOP_BY_ROLE: &[(&str, u8)] = &[
    ("canvas", 1),
    ("surface-subtle", 2),
    ("grid", 3),
    ("border", 4),
    ("grid-emphasis", 5),
    ("disabled", 6),
    ("subtitle", 7),
    ("muted", 8),
    ("inactive", 9),
    ("heading", 10),
    ("ink", 11),
    ("void", 12),
];
/// The stray aliases, respelled to the step each sat nearest.
const SCAFFOLD_ALIAS_STOPS: &[(&str, u8)] = &[
    ("separator", 4),
    ("fill-muted", 3),
    ("spark-empty", 5),
    ("placeholder", 8),
];
const ACCENT_SUCCESSOR: &str = "info.solid-hover";
const SCAFFOLDS: &[&str] = &["dbt-grays", "dbt-creams"];

const GEO_CHART_TYPES: &[&str] = &["map", "geoshape", "point_map", "bubble_map"];

const PROJECTION_REASON: &str = "The theme projection was never read. Set `projection:` on the chart, or use a geo_source, which carries its own.";
const SPARK_LABEL_REASON: &str = "Table spark bars no longer print a value label. Add a value column beside the bar to show the number.";
const NESTED_FRAME_WIDTH_REASON: &str = "A nested board's `style.frame.width` never took effect: set its `width:` to place it in the parent. Only the root board has a width of its own.";
const RECEDE_REASON: &str = "Hover recede now targets a contrast ratio against the canvas. Set `style.charts.hover_emphasis.recede_contrast` instead.";
const QUERY_TARGET_REASON: &str = "A query's `target:` never chose its connection. Declare a `type: dbt_profile` source with `target:` in dbt_charts.yml and name it with `source:`.";
const ROW_ROLE_REASON: &str =
    "A table's role column is data, not style. Set `row_role: <column>` on each table chart.";

/// Every retired scaffold spelling mapped to its replacement.
fn retired_scaffold_token_map() -> ValueMap {
    let mut map = ValueMap::new();
    let mut put = |from: String, to: String| {
        map.insert(ScalarKey::Str(from), Mapped::Str(to));
    };
    for (alias, stop) in SCAFFOLD_ALIAS_STOPS {
        for scaffold in SCAFFOLDS {
            put(format!("{scaffold}.{alias}"), format!("{scaffold}.{stop}"));
        }
        put(format!("chrome.{alias}"), format!("chrome[{stop}]"));
    }
    for scaffold in SCAFFOLDS.iter().chain(&["chrome"]) {
        put(format!("{scaffold}.accent"), ACCENT_SUCCESSOR.into());
    }
    for (role, stop) in SCAFFOLD_STOP_BY_ROLE {
        for scaffold in SCAFFOLDS {
            put(format!("{scaffold}.{role}"), format!("{scaffold}.{stop}"));
        }
        put(format!("chrome.{role}"), format!("chrome[{stop}]"));
    }
    // Current spellings mapped to themselves: the value map is total over its
    // field's domain, and the value gate has something to prove itself against.
    for current in [
        "dbt-grays.5",
        "dbt-creams.12",
        "chrome[2]",
        "chrome.white",
        ACCENT_SUCCESSOR,
    ] {
        put(current.into(), current.into());
    }
    map
}

const VARIANT_SUCCESSORS: &[(&str, &str)] = &[
    ("dark", "dark"),
    ("light", "light"),
    ("ghost", "pale"),
    ("ink", "deep"),
];
const RETIRED_FAMILIES: &[&str] = &["vivid-10", "editorial-10"];

fn is_digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}

fn is_alias(s: &str) -> bool {
    let mut chars = s.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphabetic())
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

/// The 0.9.0 third-segment spelling that replaced a retired companion token,
/// or `None` for anything outside that closed set of shapes.
pub fn respell_retired_variant(token: &str) -> Option<String> {
    let split = |rest: &'_ str| {
        VARIANT_SUCCESSORS.iter().find_map(|(suffix, variant)| {
            rest.strip_prefix(suffix).map(|r| (r.to_string(), *variant))
        })
    };
    if let Some((tail, v)) = token.strip_prefix("category_").and_then(split) {
        return if tail.is_empty() {
            Some(format!("category.{v}"))
        } else if let Some(slot) = tail.strip_prefix('[').and_then(|t| t.strip_suffix(']')) {
            is_digits(slot).then(|| format!("category[{slot}].{v}"))
        } else {
            let alias = tail.strip_prefix('.')?;
            is_alias(alias).then(|| format!("category.{alias}.{v}"))
        };
    }
    for family in RETIRED_FAMILIES {
        let Some((tail, v)) = token
            .strip_prefix(family)
            .and_then(|r| r.strip_prefix('-'))
            .and_then(split)
        else {
            continue;
        };
        if tail.is_empty() {
            return Some(format!("{family}.{v}"));
        }
        if let Some(slot) = tail.strip_prefix('.') {
            return is_digits(slot).then(|| format!("{family}.{slot}.{v}"));
        }
        let shorthand = tail
            .strip_prefix(':')
            .map(|t| t.strip_suffix("_r").unwrap_or(t));
        return match shorthand {
            Some(slot) if is_digits(slot) => Some(format!("{family}{tail}.{v}")),
            _ => (tail == "_r").then(|| format!("{family}_r.{v}")),
        };
    }
    None
}

fn respell_secondary_ink(map: &ValueMap, value: &Json) -> Rewritten {
    let Json::String(s) = value else {
        return Rewritten::Value(value.clone());
    };
    let token = respell_retired_variant(s).unwrap_or_else(|| s.clone());
    Rewritten::Value(match map.get(&ScalarKey::Str(token.clone())) {
        Some(Mapped::Str(to)) => Json::String(to.clone()),
        _ => Json::String(token),
    })
}

fn deletions(g: &Grammars) -> Vec<Deletion> {
    let del = |parts: &[&str], reason: &str, chart_type: Option<&str>| {
        let mut d = Deletion::new(g.source, g.target, parts);
        d.reason = Some(reason.into());
        d.chart_type = chart_type.map(str::to_string);
        d
    };
    let mut out: Vec<Deletion> = ["geoshape", "point_map"]
        .iter()
        .map(|f| {
            del(
                &["style", "charts", f, "projection"],
                PROJECTION_REASON,
                None,
            )
        })
        .collect();
    out.extend(
        GEO_CHART_TYPES
            .iter()
            .map(|t| del(&["style", "projection"], PROJECTION_REASON, Some(t))),
    );
    // Tails are relative to the node whose schema position declares them.
    out.extend(
        [
            &["value_visible"][..],
            &["value_suffix"],
            &["spark", "bar", "label"],
        ]
        .iter()
        .map(|p| del(p, SPARK_LABEL_REASON, None)),
    );
    out.push(del(
        &["style", "charts", "hover_emphasis", "dimmed_opacity"],
        RECEDE_REASON,
        None,
    ));
    out.push(del(
        &["style", "charts", "table", "row", "role"],
        ROW_ROLE_REASON,
        None,
    ));
    // Runs after the root move has taken the root's own, so it only reaches
    // the keys of inline nested boards.
    out.push(del(
        &["style", "frame", "width"],
        NESTED_FRAME_WIDTH_REASON,
        None,
    ));
    out.push(del(&["target"], QUERY_TARGET_REASON, None));
    out
}

pub fn declarations(g: &Grammars) -> Declarations {
    let value_map = Arc::new(retired_scaffold_token_map());
    let nesting = g.nesting_prefixes();
    let mut secondary_ink = Move::new(
        g.source,
        g.target,
        path(&["style", "muted"]),
        path(&["style", "fonts", "secondary", "color"]),
    );
    let rewrite_map = value_map.clone();
    secondary_ink.rewrite = Some(Arc::new(move |v| respell_secondary_ink(&rewrite_map, v)));
    // A tab item is its own model, not a board, so the nesting walk never steps
    // into one; following it as a prefix reaches its own `style:`.
    secondary_ink.sub_board_prefixes = Arc::new(
        nesting
            .iter()
            .cloned()
            .chain([path(&["tabs", "items", "*"])])
            .collect(),
    );
    let root_width = Move::new(
        g.source,
        g.target,
        path(&["style", "frame", "width"]),
        path(&["width"]),
    );
    let mut moves = g.suffix_rename_moves(
        &[(&["row_role"], &["style", "row", "role"])],
        None,
        Some("table"),
    );
    moves.push(root_width);
    let mut respell = g.token_respell_moves(
        &value_map,
        COLOR_TOKEN_FIELD_TAILS,
        PALETTE_NAME_FIELD_TAILS,
        ROLES_AND_PALETTES,
    );
    respell.push(g.identity(&path(CATEGORY_PIN), &value_map));
    for m in &mut respell {
        m.sub_board_prefixes = nesting.clone();
    }
    moves.extend(respell);
    moves.push(secondary_ink);
    Declarations {
        moves,
        deletions: deletions(g),
        retired_tokens: vec![value_map],
        ..Declarations::default()
    }
}

#[cfg(test)]
mod tests {
    use super::respell_retired_variant;

    #[test]
    fn retired_variant_shapes_respell_and_nothing_else_does() {
        for (old, new) in [
            ("category_ink[3]", Some("category[3].deep")),
            ("editorial-10-ghost:4_r", Some("editorial-10:4_r.pale")),
            ("category_light", Some("category.light")),
            ("vivid-10-ghost.3", Some("vivid-10.3.pale")),
            ("vivid-10-ink", Some("vivid-10.deep")),
            ("category_dark.blue.dark", None),
        ] {
            assert_eq!(respell_retired_variant(old).as_deref(), new, "{old}");
        }
    }
}
