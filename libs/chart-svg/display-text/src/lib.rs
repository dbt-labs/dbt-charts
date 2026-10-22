//! Display text derived from a column name (`core/text/humanize.py::
//! slug_to_text`, `core/text/case.py::field_title`, `_wide_fields.py::
//! resolve_wide_measure_labels`), the language catalogs, and the letter-case
//! transform (`case`) both engines apply to a slot's `font.case`.

pub mod case;
pub mod language;
pub mod titlecase_first_char;

use std::collections::HashMap;

/// A unit suffix or abbreviation: a symbol that reads the same in every
/// language, or a catalog message id the board language spells.
enum Display {
    Symbol(&'static str),
    Word(&'static str),
}

const UNIT_SUFFIXES: &[(&str, Display)] = &[
    ("usd", Display::Symbol("($)")),
    ("eur", Display::Symbol("(€)")),
    ("gbp", Display::Symbol("(£)")),
    ("pct", Display::Symbol("(%)")),
    ("percent", Display::Symbol("(%)")),
    ("ratio", Display::Word("humanize.ratio")),
    ("cnt", Display::Word("humanize.count")),
    ("num", Display::Symbol("(#)")),
    ("qty", Display::Word("humanize.qty")),
    ("lbs", Display::Symbol("(lb)")),
    ("kg", Display::Symbol("(kg)")),
    ("km", Display::Symbol("(km)")),
    ("mi", Display::Symbol("(mi)")),
    ("mph", Display::Symbol("(mph)")),
    ("secs", Display::Symbol("(s)")),
    ("seconds", Display::Symbol("(s)")),
    ("mins", Display::Symbol("(min)")),
    ("hrs", Display::Symbol("(h)")),
    ("ms", Display::Symbol("(ms)")),
];

/// The parenthesized unit symbols of [`UNIT_SUFFIXES`] whose letters title case
/// must leave alone: `(kg)` is a symbol, not a word to capitalize.
pub(crate) const CASED_UNIT_SYMBOLS: &[&str] =
    &["(lb)", "(kg)", "(km)", "(mi)", "(mph)", "(s)", "(min)", "(h)", "(ms)"];

const ABBREVIATIONS: &[(&str, Display)] = &[
    ("yoy", Display::Word("humanize.yoy")),
    ("mom", Display::Word("humanize.mom")),
    ("qoq", Display::Word("humanize.qoq")),
    ("avg", Display::Word("humanize.avg")),
    ("arr", Display::Symbol("ARR")),
    ("mrr", Display::Symbol("MRR")),
    ("nrr", Display::Symbol("NRR")),
    ("ltv", Display::Symbol("LTV")),
    ("cac", Display::Symbol("CAC")),
    ("roi", Display::Symbol("ROI")),
    ("roas", Display::Symbol("ROAS")),
    ("csat", Display::Symbol("CSAT")),
    ("nps", Display::Symbol("NPS")),
    ("enps", Display::Symbol("eNPS")),
    ("api", Display::Symbol("API")),
    ("id", Display::Symbol("ID")),
    ("url", Display::Symbol("URL")),
    ("sql", Display::Symbol("SQL")),
    ("hr", Display::Symbol("HR")),
    ("mpg", Display::Symbol("MPG")),
    ("ip", Display::Symbol("IP")),
    ("gdp", Display::Symbol("GDP")),
    ("kpi", Display::Symbol("KPI")),
    ("sku", Display::Symbol("SKU")),
    ("cpu", Display::Symbol("CPU")),
    ("ebitda", Display::Symbol("EBITDA")),
    ("ios", Display::Symbol("iOS")),
];

fn lookup(table: &[(&str, Display)], key: &str) -> Result<Option<String>, String> {
    match table.iter().find(|(k, _)| *k == key) {
        None => Ok(None),
        Some((_, Display::Symbol(text))) => Ok(Some((*text).to_string())),
        Some((_, Display::Word(id))) => language::tr(id, None, &[]).map(Some),
    }
}

/// `core/text/humanize.py::slug_to_text`, sentence-cased: split on `_`, `-` and space, expand a unit
/// suffix and abbreviations, lowercase single-case tokens, keep mixed-case
/// tokens (`MTok`, `pgSQL`) as spelled.
pub fn slug_to_text(slug: &str) -> Result<String, String> {
    let mut tokens: Vec<&str> = slug
        .split(['_', '-', ' '])
        .filter(|t| !t.is_empty())
        .collect();

    let mut suffix = None;
    if tokens.len() > 1 {
        suffix = lookup(UNIT_SUFFIXES, &tokens[tokens.len() - 1].to_lowercase())?;
        if suffix.is_some() {
            tokens.pop();
            if tokens.last().is_some_and(|t| t.eq_ignore_ascii_case("in")) {
                tokens.pop(); // weight_in_lbs reads "Weight (lb)"
            }
        }
    }

    let mut words = Vec::with_capacity(tokens.len());
    for t in &tokens {
        let lower = t.to_lowercase();
        words.push(match lookup(ABBREVIATIONS, &lower)? {
            Some(abbr) => abbr,
            None if *t != lower && *t != t.to_uppercase() => t.to_string(),
            None => lower,
        });
    }

    let text = words.join(" ");
    let text = match suffix {
        Some(s) => format!("{text} {s}").trim().to_string(),
        None => text,
    };
    // A first word spelled with a capital (`iPhone`, `YoY`) is already as intended.
    let first_word = text.split(' ').next().unwrap_or("");
    Ok(if first_word != first_word.to_lowercase() {
        text
    } else {
        case::sentence_case(&text)
    })
}

/// `core/text/case.py::inline_derived_label`: a derived label read mid-sentence.
pub fn inline_derived_label(label: &str) -> Result<String, String> {
    let first = label.split(' ').next().unwrap_or("");
    let mut chars = first.chars();
    // Python's `str.islower()`: at least one cased character, none uppercase.
    let plain = chars.next().is_some_and(char::is_uppercase)
        && chars.as_str().chars().any(char::is_lowercase)
        && !chars.as_str().chars().any(char::is_uppercase);
    if plain && language::text(&language::active_language(), "case.title_style")? == "en_small_words" {
        let mut c = label.chars();
        let head = c.next().unwrap().to_lowercase().collect::<String>();
        return Ok(head + c.as_str());
    }
    Ok(label.to_string())
}

/// `core/text/case.py::field_title`: the chart's authored `field_labels`
/// entry verbatim, else `slug_to_text(field)`.
pub fn field_title(
    field: &str,
    field_labels: &HashMap<String, String>,
) -> Result<String, String> {
    match field_labels.get(field) {
        Some(label) => Ok(label.clone()),
        None => slug_to_text(field),
    }
}

/// `_wide_fields.py::resolve_wide_measure_labels`'s label half: each
/// measure's `field_title`, with colliding derived labels falling back to the
/// raw measure name, iterated to a fixed point. An authored label never falls
/// back (resolve rejects an authored collision before any reader runs).
pub fn wide_measure_labels_for(
    measures: &[String],
    field_labels: &HashMap<String, String>,
) -> Result<HashMap<String, String>, String> {
    let mut labels = HashMap::new();
    for m in measures {
        labels.insert(m.clone(), field_title(m, field_labels)?);
    }
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
            return Ok(labels);
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
            ("revenue_usd", "Revenue ($)"),
            ("yoy_growth_pct", "YoY growth (%)"),
            ("avg_deal_size", "Avg deal size"),
            ("revenue-by-month", "Revenue by month"),
            ("fix-linked", "Fix linked"),
            ("usd", "Usd"),
            ("weight_in_lbs", "Weight (lb)"),
            ("avg_mpg", "Avg MPG"),
            ("gdp_per_capita", "GDP per capita"),
            ("ios_users", "iOS users"),
            ("total_kpi_count", "Total KPI count"),
            ("cpu_hrs", "CPU (h)"),
            ("p95_latency_ms", "P95 latency (ms)"),
            ("", ""),
        ] {
            assert_eq!(slug_to_text(slug).unwrap(), expected, "{slug}");
        }
    }

    #[test]
    fn inline_derived_label_matches_pythons_islower() {
        for (label, expected) in [
            ("Revenue", "revenue"),
            ("Revenue2024 by month", "revenue2024 by month"),
            ("Q", "Q"),
            ("API ID", "API ID"),
            ("YoY growth", "YoY growth"),
        ] {
            assert_eq!(inline_derived_label(label).unwrap(), expected, "{label}");
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
            assert_eq!(slug_to_text(slug).unwrap(), expected, "{slug}");
        }
    }

    #[test]
    fn slug_to_text_folds_single_case_tokens() {
        for (slug, expected) in [
            ("REVENUE_USD", "Revenue ($)"),
            ("YOY_GROWTH_PCT", "YoY growth (%)"),
            ("Api_Id", "API ID"),
        ] {
            assert_eq!(slug_to_text(slug).unwrap(), expected, "{slug}");
        }
    }

    #[test]
    fn field_title_prefers_the_authored_label_verbatim() {
        let l = labels(&[("fix-linked", "fix-linked")]);
        assert_eq!(field_title("fix-linked", &l).unwrap(), "fix-linked");
        assert_eq!(field_title("order_month", &l).unwrap(), "Order month");
    }

    #[test]
    fn wide_labels_terminate_on_a_duplicate_measure() {
        let measures = vec!["revenue".to_string(), "revenue".to_string()];
        let out = wide_measure_labels_for(&measures, &HashMap::new()).unwrap();
        assert_eq!(out.len(), 1);
        assert_eq!(out["revenue"], "Revenue");
    }

    #[test]
    fn wide_labels_fall_back_only_for_derived_collisions() {
        let measures = vec!["all".to_string(), "fix-linked".to_string()];
        let out = wide_measure_labels_for(&measures, &labels(&[("all", "Fix linked")])).unwrap();
        assert_eq!(out["all"], "Fix linked");
        assert_eq!(out["fix-linked"], "fix-linked");

        let derived = vec!["orders_cnt".to_string(), "orders (Count)".to_string()];
        let out = wide_measure_labels_for(&derived, &HashMap::new()).unwrap();
        assert_eq!(out["orders_cnt"], "orders_cnt");
        assert_eq!(out["orders (Count)"], "orders (Count)");
    }

    #[test]
    fn humanizer_words_follow_the_board_language() {
        let de = language::Language::new("de").unwrap();
        let out = language::with_language(de, || slug_to_text("avg_orders_cnt")).unwrap();
        assert_eq!(out, "Ø orders (Anzahl)");
    }
}
