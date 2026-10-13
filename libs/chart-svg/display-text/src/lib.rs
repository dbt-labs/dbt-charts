//! Display text derived from a column name: `core/utils.py::slug_to_text`,
//! `core/text/case.py::field_title`, and `_wide_fields.py::
//! resolve_wide_measure_labels`. No letter-case transform lives here; callers
//! that owe a slot's `font.case` apply it to the derived text only.

use std::collections::HashMap;

const UNIT_SUFFIXES: &[(&str, &str)] = &[
    ("usd", "($)"),
    ("eur", "(€)"),
    ("gbp", "(£)"),
    ("pct", "(%)"),
    ("percent", "(%)"),
    ("ratio", "(ratio)"),
    ("cnt", "(Count)"),
    ("num", "(#)"),
    ("qty", "(Qty)"),
];

const ABBREVIATIONS: &[(&str, &str)] = &[
    ("yoy", "YoY"),
    ("mom", "MoM"),
    ("qoq", "QoQ"),
    ("avg", "Avg"),
    ("arr", "ARR"),
    ("mrr", "MRR"),
    ("nrr", "NRR"),
    ("ltv", "LTV"),
    ("cac", "CAC"),
    ("roi", "ROI"),
    ("roas", "ROAS"),
    ("csat", "CSAT"),
    ("nps", "NPS"),
    ("enps", "eNPS"),
    ("api", "API"),
    ("id", "ID"),
    ("url", "URL"),
    ("sql", "SQL"),
    ("hr", "HR"),
    ("ip", "IP"),
];

fn lookup(table: &[(&str, &'static str)], key: &str) -> Option<&'static str> {
    table.iter().find(|(k, _)| *k == key).map(|(_, v)| *v)
}

/// `core/utils.py::slug_to_text`: split on `_`, `-` and space, expand a unit
/// suffix and abbreviations, lowercase single-case tokens, keep mixed-case
/// tokens (`MTok`, `pgSQL`) as spelled.
pub fn slug_to_text(slug: &str) -> String {
    let mut tokens: Vec<&str> = slug
        .split(['_', '-', ' '])
        .filter(|t| !t.is_empty())
        .collect();

    let mut suffix = None;
    if tokens.len() > 1 {
        suffix = lookup(UNIT_SUFFIXES, &tokens[tokens.len() - 1].to_lowercase());
        if suffix.is_some() {
            tokens.pop();
        }
    }

    let words: Vec<String> = tokens
        .iter()
        .map(|t| {
            let lower = t.to_lowercase();
            match lookup(ABBREVIATIONS, &lower) {
                Some(abbr) => abbr.to_string(),
                None if *t != lower && *t != t.to_uppercase() => t.to_string(),
                None => lower,
            }
        })
        .collect();

    let text = words.join(" ");
    match suffix {
        Some(s) => format!("{text} {s}").trim().to_string(),
        None => text,
    }
}

/// `core/text/case.py::field_title`: the chart's authored `field_labels`
/// entry verbatim, else `slug_to_text(field)`.
pub fn field_title(field: &str, field_labels: &HashMap<String, String>) -> String {
    field_labels
        .get(field)
        .cloned()
        .unwrap_or_else(|| slug_to_text(field))
}

/// `_wide_fields.py::resolve_wide_measure_labels`'s label half: each
/// measure's `field_title`, with colliding derived labels falling back to the
/// raw measure name, iterated to a fixed point. An authored label never falls
/// back (resolve rejects an authored collision before any reader runs).
pub fn wide_measure_labels_for(
    measures: &[String],
    field_labels: &HashMap<String, String>,
) -> HashMap<String, String> {
    let mut labels: HashMap<String, String> = measures
        .iter()
        .map(|m| (m.clone(), field_title(m, field_labels)))
        .collect();
    loop {
        let mut by_label: HashMap<&str, Vec<&String>> = HashMap::new();
        for m in measures {
            let bucket = by_label.entry(labels[m].as_str()).or_default();
            if !bucket.contains(&m) {
                bucket.push(m);
            }
        }
        let fallback: Vec<String> = by_label
            .values()
            .filter(|group| group.len() > 1)
            .flat_map(|group| group.iter())
            .filter(|m| !field_labels.contains_key(m.as_str()) && labels[**m] != ***m)
            .map(|m| (*m).clone())
            .collect();
        if fallback.is_empty() {
            return labels;
        }
        for m in fallback {
            labels.insert(m.clone(), m);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn labels(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    #[test]
    fn slug_to_text_matches_production() {
        for (slug, expected) in [
            ("revenue_usd", "revenue ($)"),
            ("yoy_growth_pct", "YoY growth (%)"),
            ("avg_deal_size", "Avg deal size"),
            ("revenue-by-month", "revenue by month"),
            ("fix-linked", "fix linked"),
            ("usd", "usd"),
            ("", ""),
        ] {
            assert_eq!(slug_to_text(slug), expected, "{slug}");
        }
    }

    #[test]
    fn slug_to_text_keeps_mixed_case_tokens() {
        for (slug, expected) in [
            ("$/MTok in / out", "$/MTok in / out"),
            ("Cache-read $/MTok", "Cache read $/MTok"),
            ("pgSQL_hackers", "pgSQL hackers"),
            ("Order_Month", "Order Month"),
        ] {
            assert_eq!(slug_to_text(slug), expected, "{slug}");
        }
    }

    #[test]
    fn slug_to_text_folds_single_case_tokens() {
        for (slug, expected) in [
            ("REVENUE_USD", "revenue ($)"),
            ("YOY_GROWTH_PCT", "YoY growth (%)"),
            ("Api_Id", "API ID"),
        ] {
            assert_eq!(slug_to_text(slug), expected, "{slug}");
        }
    }

    #[test]
    fn field_title_prefers_the_authored_label_verbatim() {
        let l = labels(&[("fix-linked", "fix-linked")]);
        assert_eq!(field_title("fix-linked", &l), "fix-linked");
        assert_eq!(field_title("order_month", &l), "order month");
    }

    #[test]
    fn wide_labels_terminate_on_a_duplicate_measure() {
        let measures = vec!["revenue".to_string(), "revenue".to_string()];
        let out = wide_measure_labels_for(&measures, &HashMap::new());
        assert_eq!(out.len(), 1);
        assert_eq!(out["revenue"], "revenue");
    }

    #[test]
    fn wide_labels_fall_back_only_for_derived_collisions() {
        let measures = vec!["all".to_string(), "fix-linked".to_string()];
        let out = wide_measure_labels_for(&measures, &labels(&[("all", "fix linked")]));
        assert_eq!(out["all"], "fix linked");
        assert_eq!(out["fix-linked"], "fix-linked");

        let derived = vec!["orders_cnt".to_string(), "orders (Count)".to_string()];
        let out = wide_measure_labels_for(&derived, &HashMap::new());
        assert_eq!(out["orders_cnt"], "orders_cnt");
        assert_eq!(out["orders (Count)"], "orders (Count)");
    }
}
