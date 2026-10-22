//! The number vocabulary of the board being drawn: production's
//! `HouseLocale` (`core/text/format_d3.py`), a d3 `formatLocale` definition
//! plus the words its magnitudes print in each register.
//!
//! Production reads it from the `number_locale_scope` context variable; every
//! `d3_format` under it speaks the board's `regional` locale. The renderer
//! does the same: [`with_number_locale`] opens the scope for one render and
//! [`d3_format`] is the single formatting entry every family goes through.
//! Outside any scope the locale is en-US, production's `HOUSE_EN_US`.

use std::cell::RefCell;
use std::rc::Rc;

use d3rs::format::{Grouping, Locale};
use serde::{Deserialize, Serialize};

/// `DEFAULT_REGIONAL.format_locale` as the payload carries it, generated from
/// production (`libs/chart-svg/tests/test_format_locale.py` pins it).
pub const EN_US_WIRE: &str = include_str!("../data/format_locale_en_us.json");

/// A d3 locale and the words its magnitudes print: thousand, million,
/// billion, trillion, in the analytic and the narrative register. A word
/// carries its own gap (the no-break space, or none).
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(try_from = "Wire", into = "Wire")]
pub struct NumberLocale {
    pub locale: Locale,
    analytic: [String; 4],
    narrative: [String; 4],
}

/// `HouseLocale` as `chart_svg_dev._sweep.board_style_envelope` writes it:
/// d3's locale definition (`locale_definition`) plus the two word registers.
/// `thousands` and `grouping` travel together or not at all.
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Wire {
    decimal: String,
    thousands: Option<String>,
    grouping: Option<Vec<usize>>,
    currency: [String; 2],
    numerals: Option<Vec<String>>,
    percent: String,
    minus: String,
    nan: String,
    analytic: [String; 4],
    narrative: [String; 4],
}

impl TryFrom<Wire> for NumberLocale {
    type Error = String;

    fn try_from(wire: Wire) -> Result<Self, String> {
        let grouping = match (wire.thousands, wire.grouping) {
            (None, None) => None,
            (Some(separator), Some(sizes)) => {
                if sizes.is_empty() || sizes.contains(&0) {
                    return Err(format!("format_locale grouping {sizes:?} must be non-empty positive sizes"));
                }
                Some(Grouping { sizes, separator })
            }
            _ => return Err("format_locale `thousands` and `grouping` must be given together".to_string()),
        };
        let numerals = wire
            .numerals
            .map(|digits| {
                <[String; 10]>::try_from(digits)
                    .map_err(|d| format!("format_locale numerals must list the 10 digits, got {}", d.len()))
            })
            .transpose()?;
        Ok(NumberLocale {
            locale: Locale {
                decimal: wire.decimal,
                grouping,
                currency: wire.currency,
                numerals,
                percent: wire.percent,
                minus: wire.minus,
                nan: wire.nan,
            },
            analytic: wire.analytic,
            narrative: wire.narrative,
        })
    }
}

impl From<NumberLocale> for Wire {
    fn from(locale: NumberLocale) -> Wire {
        let Locale { decimal, grouping, currency, numerals, percent, minus, nan } = locale.locale;
        let (thousands, grouping) = match grouping {
            Some(g) => (Some(g.separator), Some(g.sizes)),
            None => (None, None),
        };
        Wire {
            decimal,
            thousands,
            grouping,
            currency,
            numerals: numerals.map(Vec::from),
            percent,
            minus,
            nan,
            analytic: locale.analytic,
            narrative: locale.narrative,
        }
    }
}

impl NumberLocale {
    /// `HOUSE_EN_US`: what a format outside any board's scope speaks, read
    /// from the definition production resolved (`DEFAULT_REGIONAL`).
    pub fn en_us() -> NumberLocale {
        serde_json::from_str(EN_US_WIRE).expect("data/format_locale_en_us.json is a valid format_locale")
    }

    /// `currency_suffix(spec)` (`format_d3.py:218`): the text the locale's
    /// currency places after the digits of `spec`.
    pub fn currency_suffix(&self, spec: &str) -> &str {
        if spec.contains('$') {
            &self.locale.currency[1]
        } else {
            ""
        }
    }

    /// `currency_front` (`format_d3.py:209`): the locale's currency ahead of
    /// the digits; a word keeps a gap (`kr 12`).
    pub(crate) fn currency_front(&self) -> String {
        let [before, after] = &self.locale.currency;
        if !before.trim().is_empty() {
            return before.clone();
        }
        let symbol = after.trim();
        match symbol.chars().next_back() {
            Some(last) if last.is_alphabetic() => format!("{symbol}\u{a0}"),
            _ => symbol.to_string(),
        }
    }

    /// `composed_currency(symbol, repeats)` (`format_d3.py:223`): the
    /// `(lead, trail)` a `$` composes outside d3 around digits ending in a
    /// magnitude word. Both empty where the locale's currency leads (d3 places
    /// it); else trailing where it repeats (`500k €`) and leading where
    /// declared once.
    pub fn composed_currency(&self, symbol: &str, repeats: bool) -> (String, String) {
        if symbol != "$" || !self.locale.currency[0].trim().is_empty() {
            return (String::new(), String::new());
        }
        if repeats {
            (String::new(), self.locale.currency[1].clone())
        } else {
            (self.currency_front(), String::new())
        }
    }

    /// `magnitude_suffixes()` (`compile/format_value.py:104`): the suffixes a
    /// numeric lane splits out as a magnitude, bare: d3's raw SI letters and
    /// both registers' words.
    pub fn magnitude_suffixes(&self) -> impl Iterator<Item = &str> {
        ["k", "M", "G", "T", "P", "E", "Z", "Y"]
            .into_iter()
            .chain(self.analytic.iter().chain(&self.narrative).map(|word| word.trim()))
    }

    /// `HouseLocale.magnitudes(register)[letter]` (`format_d3.py:146-158`):
    /// the word for d3's SI letter (`k M G T` take the register's word,
    /// `P E Z Y` d3's own letter at both registers, whatever the locale), or
    /// `None` for any other letter (milli, micro, ...), which d3 prints as is.
    pub fn magnitude_word(&self, letter: &str, notation: &str) -> Result<Option<String>, String> {
        let words = match notation {
            "analytic" => &self.analytic,
            "narrative" => &self.narrative,
            other => return Err(format!("format notation {other:?} is neither analytic nor narrative")),
        };
        Ok(match letter {
            "k" => Some(words[0].clone()),
            "M" => Some(words[1].clone()),
            "G" => Some(words[2].clone()),
            "T" => Some(words[3].clone()),
            "P" | "E" | "Z" | "Y" => Some(letter.to_string()),
            _ => None,
        })
    }
}

thread_local! {
    static ACTIVE: RefCell<Rc<NumberLocale>> = RefCell::new(Rc::new(NumberLocale::en_us()));
}

/// Restores the enclosing scope's locale when the render ends, panic or not.
struct Restore(Rc<NumberLocale>);

impl Drop for Restore {
    fn drop(&mut self) {
        ACTIVE.with(|active| *active.borrow_mut() = Rc::clone(&self.0));
    }
}

/// Run `render` with `locale` as the active number locale
/// (`number_locale_scope`).
pub fn with_number_locale<T>(locale: &NumberLocale, render: impl FnOnce() -> T) -> T {
    let previous = ACTIVE.with(|active| active.replace(Rc::new(locale.clone())));
    let _restore = Restore(previous);
    render()
}

/// `active_format_locale()`.
pub fn active_number_locale() -> Rc<NumberLocale> {
    ACTIVE.with(|active| Rc::clone(&active.borrow()))
}

/// `d3_format(spec)(value)`: `d3rs`'s formatter under the active locale.
pub fn d3_format(spec: &str, value: f64) -> Result<String, String> {
    d3rs::format::format(&active_number_locale().locale, spec, value)
}

/// The value a [`d3_format`] result (`".0f"`, `".1~f"`: no grouping) reads as,
/// under the active locale's numerals, decimal mark and minus sign.
pub fn parse_printed(text: &str) -> Result<f64, String> {
    let locale = &active_number_locale().locale;
    let digits: String = text
        .chars()
        .map(|c| match &locale.numerals {
            Some(numerals) => numerals
                .iter()
                .position(|n| n.chars().eq([c]))
                .map_or(c, |digit| char::from(b'0' + digit as u8)),
            None => c,
        })
        .collect();
    digits
        .replace(&locale.minus, "-")
        .replace(&locale.decimal, ".")
        .parse()
        .map_err(|e| format!("number {text:?} is not a printed decimal: {e}"))
}

/// The en-US definition as a payload carries it, for a `board_style` a test
/// hand-builds.
pub fn en_us_wire() -> serde_json::Value {
    serde_json::from_str(EN_US_WIRE).expect("data/format_locale_en_us.json is valid JSON")
}

/// en-US with the given magnitude words, for a test that pins how a word's own
/// gap reaches a measurement.
#[cfg(test)]
pub(crate) fn en_us_with_words(analytic: [&str; 4], narrative: [&str; 4]) -> NumberLocale {
    let mut wire = en_us_wire();
    wire["analytic"] = serde_json::json!(analytic);
    wire["narrative"] = serde_json::json!(narrative);
    serde_json::from_value(wire).expect("locale parses")
}

/// `styling_6`'s de-DE locale as production resolved it, for a test that runs
/// a family under a non-en-US board.
#[cfg(test)]
pub(crate) fn de_de() -> NumberLocale {
    let fixture: serde_json::Value =
        serde_json::from_str(include_str!("../../fixtures/regional/styling_6_revenue_bar.json"))
            .expect("fixture json parses");
    Deserialize::deserialize(&fixture["payload"]["board_style"]["format_locale"])
        .expect("fixture locale parses")
}

/// `fr-FR` as production resolved it (percent `\u{202f}%`, currency after the
/// digits), for a test that runs a family under a non-en-US board.
#[cfg(test)]
pub(crate) fn fr_fr() -> NumberLocale {
    let fixtures: serde_json::Value =
        serde_json::from_str(include_str!("../../fixtures/regional/table_lanes.json")).expect("fixture json parses");
    Deserialize::deserialize(&fixtures["fr-FR/lanes_all"]["payload"]["board_style"]["format_locale"])
        .expect("fixture locale parses")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn wire_with(edit: impl FnOnce(&mut serde_json::Value)) -> Result<NumberLocale, serde_json::Error> {
        let mut wire: serde_json::Value = serde_json::from_str(EN_US_WIRE).unwrap();
        edit(&mut wire);
        serde_json::from_value(wire)
    }

    #[test]
    fn the_house_en_us_is_d3_s_default_locale() {
        assert_eq!(NumberLocale::en_us().locale, Locale::en_us());
    }

    #[test]
    fn a_locale_missing_a_field_names_it() {
        let err = wire_with(|w| {
            w.as_object_mut().unwrap().remove("decimal");
        })
        .unwrap_err()
        .to_string();
        assert!(err.contains("decimal"), "{err}");
    }

    #[test]
    fn thousands_without_grouping_refuses() {
        let err = wire_with(|w| {
            w.as_object_mut().unwrap().remove("grouping");
        })
        .unwrap_err()
        .to_string();
        assert!(err.contains("`thousands` and `grouping`"), "{err}");
    }

    #[test]
    fn grouping_sizes_must_be_positive() {
        let err = wire_with(|w| w["grouping"] = json!([3, 0])).unwrap_err().to_string();
        assert!(err.contains("grouping"), "{err}");
    }

    #[test]
    fn magnitude_words_follow_the_register() {
        let de = wire_with(|w| {
            w["analytic"] = json!(["\u{a0}k", "\u{a0}M", "\u{a0}Mrd", "\u{a0}Bio"]);
            w["narrative"] = json!(["\u{a0}Tsd.", "\u{a0}Mio.", "\u{a0}Mrd.", "\u{a0}Bio."]);
        })
        .unwrap();
        assert_eq!(de.magnitude_word("k", "narrative").unwrap().as_deref(), Some("\u{a0}Tsd."));
        assert_eq!(de.magnitude_word("G", "analytic").unwrap().as_deref(), Some("\u{a0}Mrd"));
        assert_eq!(de.magnitude_word("P", "narrative").unwrap().as_deref(), Some("P"));
        assert_eq!(de.magnitude_word("m", "narrative").unwrap(), None);
        assert!(de.magnitude_word("k", "loud").is_err());
    }

    #[test]
    fn the_scope_restores_the_enclosing_locale() {
        let de = wire_with(|w| w["decimal"] = json!(",")).unwrap();
        assert_eq!(d3_format(".1f", 1.5).unwrap(), "1.5");
        with_number_locale(&de, || assert_eq!(d3_format(".1f", 1.5).unwrap(), "1,5"));
        assert_eq!(d3_format(".1f", 1.5).unwrap(), "1.5");
    }
}
