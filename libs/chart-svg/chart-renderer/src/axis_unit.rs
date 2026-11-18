//! `WARN-TITLE-UNIT-MISMATCH`: a title or subtitle that names a unit must not contradict a
//! compacted (K, M, B, T) value axis. A title that names no unit never warns, nor does a
//! symbol-only axis (`$166,300`, `40%`). The renderer only reads the title; it never edits it.
//!
//! The axis unit is read off the painted tick labels (what the reader sees), not re-derived from
//! the format: strip the digits and separators and what remains is the unit.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use diagnostic::diag;
use regex::Regex;
use serde_json::Value;

const SYMBOLS: [char; 5] = ['$', '€', '£', '¥', '%'];
/// The tiers a title can name. d3's `G` (giga) is the house `B`.
const TIERS: [char; 5] = ['K', 'M', 'B', 'T', 'G'];

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Unit {
    symbol: Option<char>,
    tier: Option<char>,
}

impl Unit {
    fn label(self) -> String {
        self.symbol.into_iter().chain(self.tier).collect()
    }
}

#[derive(Default)]
struct Named {
    symbols: BTreeSet<char>,
    tiers: BTreeSet<char>,
}

static SYMBOL_TIER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"([$€£¥])\s?([KMBTkmbt])?(?:[^A-Za-z0-9&]|$)").unwrap());
// A bare tier letter only counts as a trailing annotation (`, K`, `(M)`, `in B`), never as a
// word or an initial in the middle of a title.
static TRAILING_TIER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?:,\s*|\(|\bin\s+)([KMBT])\)?\s*$").unwrap());
static WORDS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:(thousand|million|billion|trillion)s?|(dollar|euro)s?|(percent(?:age)?))\b(-|\s+[0-9])?").unwrap()
});

fn named(text: &str) -> Named {
    let mut named = Named::default();
    for caps in SYMBOL_TIER.captures_iter(text) {
        named.symbols.insert(caps[1].chars().next().unwrap());
        if let Some(tier) = caps.get(2) {
            named.tiers.insert(tier.as_str().to_ascii_uppercase().chars().next().unwrap());
        }
    }
    if let Some(caps) = TRAILING_TIER.captures(text) {
        named.tiers.insert(caps[1].chars().next().unwrap());
    }
    // A `%` after a digit is a quantity in running text, not a unit declaration.
    let bytes = text.as_bytes();
    if text.match_indices('%').any(|(i, _)| i == 0 || !bytes[i - 1].is_ascii_digit()) {
        named.symbols.insert('%');
    }
    // `million-dollar deals` and `Euro 2024` are not unit declarations.
    let in_compound = |caps: &regex::Captures| {
        caps.get(4).is_some() || text[..caps.get(0).unwrap().start()].ends_with('-')
    };
    for caps in WORDS.captures_iter(text).filter(|caps| !in_compound(caps)) {
        if let Some(tier) = caps.get(1) {
            named.tiers.insert(match tier.as_str().to_ascii_lowercase().as_str() {
                "thousand" => 'K',
                "million" => 'M',
                "billion" => 'B',
                _ => 'T',
            });
        } else if let Some(currency) = caps.get(2) {
            named.symbols.insert(if currency.as_str().eq_ignore_ascii_case("dollar") { '$' } else { '€' });
        } else {
            named.symbols.insert('%');
        }
    }
    named
}

impl Named {
    #[cfg(test)]
    fn is_empty(&self) -> bool {
        self.symbols.is_empty() && self.tiers.is_empty()
    }

    fn label(&self) -> String {
        self.symbols.iter().chain(&self.tiers).collect()
    }

    /// Whether this text names a tier or symbol that none of the chart's axes carries. A
    /// symbol is only held against axes that carry one.
    fn contradicts(&self, units: &BTreeSet<Unit>) -> bool {
        let axis_symbols: BTreeSet<char> = units.iter().filter_map(|u| u.symbol).collect();
        self.tiers.iter().any(|&tier| !units.iter().any(|u| u.tier == Some(tier)))
            || (!axis_symbols.is_empty() && self.symbols.iter().any(|s| !axis_symbols.contains(s)))
    }
}

/// The unit a value axis's tick labels declare, or `None` for plain numbers. A tick label's
/// residue past its digits and separators is its unit; a letter that is no tier (an exponent, a
/// localized word, d3's lowercase `m` for milli) leaves the unit unreadable, so the axis is not
/// checked. A ladder mixing tiers (`500K`, `1M`) declares its largest, as the axis does on its top tick.
fn tick_unit<'a>(labels: impl Iterator<Item = &'a str>) -> Option<Unit> {
    let mut unit = Unit { symbol: None, tier: None };
    for label in labels {
        for c in label.chars().filter(|c| !c.is_ascii_digit() && !c.is_whitespace()) {
            match c {
                '.' | ',' | '-' | '−' | '+' | '\u{200b}' => {}
                c if SYMBOLS.contains(&c) => unit.symbol = Some(c),
                'm' => return None,
                c if TIERS.contains(&c.to_ascii_uppercase()) => {
                    let tier = if c.eq_ignore_ascii_case(&'G') { 'B' } else { c.to_ascii_uppercase() };
                    let rank = |t: char| ['K', 'M', 'B', 'T'].iter().position(|&x| x == t);
                    if unit.tier.is_none_or(|top| rank(tier) > rank(top)) {
                        unit.tier = Some(tier);
                    }
                }
                _ => return None,
            }
        }
    }
    (unit.symbol.is_some() || unit.tier.is_some()).then_some(unit)
}

/// The units of the chart's quantitative axes, read off its painted SVG.
fn axis_units(svg: &str) -> Result<BTreeSet<Unit>, String> {
    let doc = roxmltree::Document::parse(svg).map_err(|e| format!("chart svg does not parse: {e}"))?;
    let has_class = |node: roxmltree::Node, class: &str| {
        node.attribute("class").is_some_and(|c| c.split_whitespace().any(|c| c == class))
    };
    let mut units = BTreeSet::new();
    for axis in doc.descendants().filter(|n| n.attribute("data-dct-role").is_some_and(|r| r.ends_with("-axis"))) {
        let caption = axis.attribute("aria-label").unwrap_or_default();
        if !["linear scale", "log scale", "sqrt scale"].iter().any(|scale| caption.contains(scale)) {
            continue;
        }
        let ticks = axis
            .descendants()
            .filter(|n| n.has_tag_name("text") && !n.ancestors().any(|a| has_class(a, "role-axis-title")))
            .map(|n| n.descendants().filter_map(|t| t.text()).collect::<String>());
        let labels: Vec<String> = ticks.collect();
        units.extend(tick_unit(labels.iter().map(String::as_str)));
    }
    Ok(units)
}

/// Warn, once per distinct compacted axis unit, when the chart's title or subtitle names a unit
/// no axis carries. A title that names nothing has nothing to contradict.
pub(crate) fn check(resolved: &Value, svg: &str) -> Result<(), String> {
    let authored: Vec<&str> = ["title", "subtitle"]
        .iter()
        .filter_map(|key| resolved.get(key).and_then(Value::as_str))
        .filter(|text| !text.is_empty())
        .collect();
    if authored.is_empty() {
        return Ok(());
    }
    let texts: Vec<Named> = authored.into_iter().map(named).collect();
    let chart_id = resolved.get("id").and_then(Value::as_str).ok_or("payload.resolved.id missing")?;
    let units = axis_units(svg)?;
    for &unit in &units {
        if unit.tier.is_none() {
            continue;
        }
        if let Some(wrong) = texts.iter().find(|text| text.contradicts(&units)) {
            diagnostic::warn(diag::warn_title_unit_mismatch(chart_id, unit.label(), wrong.label()));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unit(labels: &[&str]) -> Option<Unit> {
        tick_unit(labels.iter().copied())
    }

    #[test]
    fn ticks_declare_their_unit_by_what_follows_the_digits() {
        let u = |symbol, tier| Some(Unit { symbol, tier });
        assert_eq!(unit(&["0", "100K", "$400K"]), u(Some('$'), Some('K')));
        assert_eq!(unit(&["0", "100k", "200k"]), u(None, Some('K')));
        assert_eq!(unit(&["−$1.5M", "0"]), u(Some('$'), Some('M')));
        assert_eq!(unit(&["20%", "40%"]), u(Some('%'), None));
        assert_eq!(unit(&["300   \u{200b}", "400"]), None);
        assert_eq!(unit(&["1e+6"]), None);
        assert_eq!(unit(&["5 Mio."]), None);
    }

    #[test]
    fn a_symbol_only_axis_has_no_tier_to_check() {
        assert_eq!(unit(&["$166,300", "$0"]), Some(Unit { symbol: Some('$'), tier: None }));
        assert_eq!(unit(&["40%"]).unwrap().tier, None);
    }

    #[test]
    fn a_mixed_tick_ladder_takes_its_largest_tier() {
        let u = |tier| Some(Unit { symbol: Some('$'), tier: Some(tier) });
        assert_eq!(unit(&["$500K", "$1M", "$800K"]), u('M'));
        assert_eq!(unit(&["$1M", "$500K"]), u('M'));
        // d3's lowercase `m` is milli, not the house million.
        assert_eq!(unit(&["500m", "1"]), None);
    }

    fn axis_svg(labels: &[&str]) -> String {
        let texts: String = labels.iter().map(|l| format!("<text>{l}</text>")).collect();
        format!(r#"<svg><g data-dct-role="y-axis" aria-label="linear scale">{texts}</g></svg>"#)
    }

    #[test]
    fn an_axis_repeating_its_unit_on_every_tick_still_declares_it() {
        let k = Unit { symbol: None, tier: Some('K') };
        let units = axis_units(&axis_svg(&["0", "500k", "1,000k", "1,500k"])).unwrap();
        assert_eq!(units.into_iter().collect::<Vec<_>>(), [k]);
        assert!(axis_units(&axis_svg(&["0", "0.5m", "1m"])).unwrap().is_empty());
    }

    #[test]
    fn an_anchored_axis_still_names_its_unit() {
        let units = axis_units(&axis_svg(&["0", "0.5", "1", "1.5M"])).unwrap();
        assert_eq!(units.into_iter().collect::<Vec<_>>(), [Unit { symbol: None, tier: Some('M') }]);
    }

    fn warnings(title: &str, labels: &[&str]) -> usize {
        let resolved = serde_json::json!({"id": "c", "title": title});
        let (checked, warnings) = diagnostic::collect(|| check(&resolved, &axis_svg(labels)));
        checked.unwrap();
        warnings.len()
    }

    #[test]
    fn only_a_wrong_unit_warns_never_a_missing_one() {
        let ticks = ["0", "0.5", "1", "1.5M"];
        assert_eq!(warnings("Pipeline by quarter", &ticks), 0);
        assert_eq!(warnings("Pipeline by quarter, $M", &ticks), 0);
        assert_eq!(warnings("Pipeline by quarter, $K", &ticks), 1);
    }

    #[test]
    fn a_percent_attached_to_a_number_is_not_a_unit() {
        let n = named("Revenue in thousands from top 10% of accounts");
        assert_eq!(n.label(), "K");
        assert_eq!(named("Win rate (%)").label(), "%");
    }

    fn contradicted(title: &str, units: &[Unit]) -> bool {
        let units: BTreeSet<Unit> = units.iter().copied().collect();
        named(title).contradicts(&units)
    }

    #[test]
    fn a_title_naming_each_axis_unit_of_a_two_unit_chart_contradicts_nothing() {
        let units = [
            Unit { symbol: Some('%'), tier: None },
            Unit { symbol: Some('$'), tier: Some('K') },
        ];
        assert!(!contradicted("Revenue ($K) vs win rate (%)", &units));
        assert!(contradicted("Revenue ($M) vs win rate (%)", &units));
    }

    #[test]
    fn a_unit_matching_no_axis_contradicts() {
        assert!(contradicted("Win rate, $M deals", &[Unit { symbol: Some('%'), tier: None }]));
    }

    #[test]
    fn titles_name_compact_and_spelled_out_units() {
        let n = named("Pipeline by quarter, $K");
        assert_eq!((n.label().as_str(), n.is_empty()), ("$K", false));
        assert_eq!(named("In $ millions").label(), "$M");
        assert_eq!(named("Thousands of dollars").label(), "$K");
        assert_eq!(named("Win rate, %").label(), "%");
        assert_eq!(named("Share (percent)").label(), "%");
        assert_eq!(named("Sales in K").label(), "K");
    }

    #[test]
    fn ordinary_title_words_name_no_unit() {
        for title in ["Plan A vs B", "Deals above $5M target", "Region A, B and C", "Q3 pipeline", "Million-dollar deals", "Euro 2024"] {
            assert!(named(title).is_empty(), "{title}");
        }
    }
}
