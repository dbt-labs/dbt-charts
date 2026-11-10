//! The id a user sees for a chart, Python's `generate_inline_chart_id` (`normalize/charts.py`):
//! an inline chart's authored display text (a KPI's `label`, every other family's `title`),
//! slugged and made unique among the charts of the board it sits in, else the item id of its
//! place (`row0`, `col1`, `grid2`). A chart authored under a name keeps it.
//!
//! The set a slug is made unique against is one board's: that board's declared chart names plus
//! the inline ids already given in its layout. A nested board, an imported board and a tab pane
//! each start their own, so two boards may hand out the same id.

use std::collections::BTreeSet;

/// Python's `_title_to_slug`: lowercase, spaces and underscores to hyphens, anything but
/// `[a-z0-9-]` dropped, hyphen runs collapsed and trimmed; `chart` when nothing is left.
pub fn title_slug(title: &str) -> String {
    let mut slug = String::new();
    for c in title.to_lowercase().chars() {
        let c = if c == ' ' || c == '_' { '-' } else { c };
        if c == '-' && (slug.is_empty() || slug.ends_with('-')) {
            continue;
        }
        if c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-' {
            slug.push(c);
        }
    }
    let slug = slug.trim_end_matches('-');
    if slug.is_empty() {
        "chart".to_string()
    } else {
        slug.to_string()
    }
}

/// The public id of an inline chart whose display text is `text` (`None` or empty reads as
/// unwritten: whitespace is text), placed at `fallback`, made unique against `used` (which it
/// extends).
pub fn inline_id(text: Option<&str>, fallback: &str, used: &mut BTreeSet<String>) -> String {
    let base = match text {
        Some(t) if !t.is_empty() => title_slug(t),
        _ => fallback.to_string(),
    };
    let mut id = base.clone();
    let mut counter = 2;
    while used.contains(&id) {
        id = format!("{base}-{counter}");
        counter += 1;
    }
    used.insert(id.clone());
    id
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ids(items: &[(Option<&str>, &str)], declared: &[&str]) -> Vec<String> {
        let mut used: BTreeSet<String> = declared.iter().map(|s| s.to_string()).collect();
        items
            .iter()
            .map(|(t, f)| inline_id(*t, f, &mut used))
            .collect()
    }

    #[test]
    fn a_title_is_slugged() {
        assert_eq!(title_slug("My First Chart"), "my-first-chart");
        assert_eq!(title_slug("Revenue_by  Region!"), "revenue-by-region");
        assert_eq!(title_slug("--a---b--"), "a-b");
        assert_eq!(title_slug("Q3 (2026) / EMEA"), "q3-2026-emea");
    }

    #[test]
    fn a_title_with_nothing_to_keep_is_chart() {
        assert_eq!(title_slug("   "), "chart");
        assert_eq!(title_slug("!!!"), "chart");
        assert_eq!(title_slug("日本語"), "chart");
        assert_eq!(title_slug("-_-"), "chart");
    }

    #[test]
    fn unicode_is_dropped_after_lowercasing() {
        // U+212A KELVIN SIGN lowercases to ASCII k; U+0130 to i + a combining dot.
        assert_eq!(title_slug("\u{212A}pi"), "kpi");
        assert_eq!(title_slug("\u{130}x"), "ix");
        assert_eq!(title_slug("Café Ünï"), "caf-n");
        assert_eq!(title_slug("a\tb\nc"), "abc");
    }

    #[test]
    fn duplicates_count_from_two_in_order() {
        let got = ids(
            &[
                (Some("Sales"), "row0"),
                (Some("sales"), "row1"),
                (Some("SALES"), "row2"),
            ],
            &[],
        );
        assert_eq!(got, ["sales", "sales-2", "sales-3"]);
    }

    #[test]
    fn a_counted_id_that_is_taken_is_skipped() {
        let got = ids(
            &[
                (Some("A"), "row0"),
                (Some("A 2"), "row1"),
                (Some("A"), "row2"),
            ],
            &[],
        );
        // "a", then "a-2" is the slug of "A 2" and takes the next free count after it.
        assert_eq!(got, ["a", "a-2", "a-3"]);
        let got = ids(&[(Some("A"), "row0"), (Some("A"), "row1")], &["a", "a-2"]);
        assert_eq!(got, ["a-3", "a-4"]);
    }

    #[test]
    fn a_missing_or_empty_title_takes_the_item_id() {
        let got = ids(&[(None, "row0"), (Some(""), "col1"), (None, "grid2")], &[]);
        assert_eq!(got, ["row0", "col1", "grid2"]);
    }

    #[test]
    fn a_blank_title_is_text_and_slugs_to_chart() {
        let got = ids(&[(Some(" "), "row0"), (Some("?"), "row1")], &[]);
        assert_eq!(got, ["chart", "chart-2"]);
    }

    #[test]
    fn the_item_id_is_deduplicated_like_a_slug() {
        let got = ids(&[(None, "row0"), (Some("row0"), "row1")], &["row0"]);
        assert_eq!(got, ["row0-2", "row0-3"]);
    }

    #[test]
    fn a_declared_name_is_taken() {
        let got = ids(&[(Some("Revenue"), "row0")], &["revenue"]);
        assert_eq!(got, ["revenue-2"]);
    }
}
