//! The vendored families that guarantee tabular digits.

/// `core/fonts.py::font_is_tabular`'s families with `tabular=True` -- a small, finite,
/// engine-owned set (`FONT_REGISTRY`'s vendored font faces).
const TABULAR_FAMILIES: &[&str] = &[
    "dbt Sans Tabular",
    "dbt Sans Tabular Medium",
    "dbt Sans Tabular SemiBold",
    "dbt Serif Oldstyle Tabular",
];

/// `core/fonts.py::font_is_tabular`: the PRIMARY (first-listed) family in a CSS font stack,
/// quote/whitespace-stripped, checked against the tabular registry -- correct regardless of what
/// a later stack entry (an emoji fallback) happens to be.
pub fn family_is_tabular(family_stack: &str) -> bool {
    let primary = family_stack.split(',').next().unwrap_or("").trim().trim_matches(['\'', '"']);
    !primary.is_empty() && TABULAR_FAMILIES.contains(&primary)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_primary_stack_entry_decides() {
        assert!(family_is_tabular("'dbt Sans Tabular', Inter, sans-serif"));
        assert!(family_is_tabular("dbt Serif Oldstyle Tabular"));
        assert!(!family_is_tabular("'Inter Variable', Inter, sans-serif"));
        assert!(!family_is_tabular("Inter, 'dbt Sans Tabular'"));
        assert!(!family_is_tabular(""));
    }

    #[test]
    fn an_emoji_fallback_after_the_primary_changes_nothing() {
        assert!(family_is_tabular("'dbt Sans Tabular', 'Noto Emoji', Inter, system-ui, sans-serif"));
    }
}
