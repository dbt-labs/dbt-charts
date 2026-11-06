//! d3's number (`formatLocale`) and time (`timeFormatLocale`) locale
//! definitions, vendored byte for byte from the pinned d3-format and
//! d3-time-format releases into `locale/{number,time}/`
//! (`locale_gen/gen_locales.mjs`), and the house facts d3 does not carry: a
//! number locale's magnitude words (`locale/magnitude_words.json`) and a time
//! locale's first weekday (`locale/first_day.json`, CLDR's, written by
//! `chart_svg_dev/gen_cldr_first_day.py`). The only copy: Python reads them
//! through `_engine`.

use std::collections::BTreeMap;
use std::sync::OnceLock;

use serde::Deserialize;

include!(concat!(env!("OUT_DIR"), "/locales.rs"));

/// Thousand, million, billion, trillion.
pub type MagnitudeWords = [String; 4];

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Registers {
    pub analytic: MagnitudeWords,
    pub narrative: MagnitudeWords,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MagnitudeTable {
    #[serde(rename = "doc")]
    _doc: String,
    region: BTreeMap<String, Registers>,
    language: BTreeMap<String, Registers>,
}

fn magnitude_table() -> &'static MagnitudeTable {
    static TABLE: OnceLock<MagnitudeTable> = OnceLock::new();
    TABLE.get_or_init(|| {
        serde_json::from_str(include_str!("../locale/magnitude_words.json"))
            .unwrap_or_else(|e| panic!("locale/magnitude_words.json: {e}"))
    })
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct FirstDayTable {
    #[serde(rename = "source")]
    _source: String,
    first_day: BTreeMap<String, String>,
}

fn first_day_table() -> &'static FirstDayTable {
    static TABLE: OnceLock<FirstDayTable> = OnceLock::new();
    TABLE.get_or_init(|| {
        serde_json::from_str(include_str!("../locale/first_day.json"))
            .unwrap_or_else(|e| panic!("locale/first_day.json: {e}"))
    })
}

const WEEKDAYS: [&str; 7] = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

fn definition(
    kind: &str,
    tags: &[&str],
    sources: &[&'static str],
    tag: &str,
) -> Result<&'static str, String> {
    match tags.iter().position(|t| *t == tag) {
        Some(i) => Ok(sources[i]),
        None => Err(format!(
            "unknown {kind} locale '{tag}'; supported: {}",
            tags.join(", ")
        )),
    }
}

/// The d3-format locale definition `tag` names, as d3 ships its JSON.
pub fn number_locale(tag: &str) -> Result<&'static str, String> {
    definition("number", &NUMBER_LOCALES, &NUMBER_LOCALES_SOURCES, tag)
}

/// The d3-time-format locale definition `tag` names, as d3 ships its JSON.
pub fn time_locale(tag: &str) -> Result<&'static str, String> {
    definition("time", &TIME_LOCALES, &TIME_LOCALES_SOURCES, tag)
}

/// The words number locale `tag`'s magnitudes print: its region row, else its
/// language row.
pub fn magnitude_words(tag: &str) -> Result<&'static Registers, String> {
    number_locale(tag)?;
    let table = magnitude_table();
    let language = tag.split('-').next().unwrap_or(tag);
    table
        .region
        .get(tag)
        .or_else(|| table.language.get(language))
        .ok_or_else(|| format!("number locale '{tag}' has no magnitude words"))
}

/// The index (0 Sunday) of the first weekday CLDR gives time locale `tag`'s
/// region.
pub fn week_start(tag: &str) -> Result<usize, String> {
    time_locale(tag)?;
    let region = tag.split_once('-').map_or("", |(_, region)| region);
    let day = first_day_table()
        .first_day
        .get(region)
        .ok_or_else(|| format!("time locale '{tag}' has no CLDR first weekday"))?;
    WEEKDAYS
        .iter()
        .position(|d| d == day)
        .ok_or_else(|| format!("locale/first_day.json: '{day}' is not a weekday"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_vendored_locale_is_json_and_an_unknown_tag_errors() {
        for tag in NUMBER_LOCALES {
            let v: serde_json::Value = serde_json::from_str(number_locale(tag).unwrap()).unwrap();
            assert!(v["decimal"].is_string(), "{tag}");
        }
        for tag in TIME_LOCALES {
            let v: serde_json::Value = serde_json::from_str(time_locale(tag).unwrap()).unwrap();
            assert_eq!(v["months"].as_array().unwrap().len(), 12, "{tag}");
        }
        assert!(number_locale("xx-XX").unwrap_err().contains("en-US"));
        assert!(time_locale("pt-PT").is_err());
    }

    #[test]
    fn every_number_locale_has_its_words() {
        for tag in NUMBER_LOCALES {
            let words = magnitude_words(tag).unwrap();
            for w in words.analytic.iter().chain(&words.narrative) {
                assert!(!w.trim().is_empty(), "{tag}");
            }
        }
        assert_eq!(
            magnitude_words("en-US").unwrap().analytic,
            ["K", "M", "B", "T"]
        );
        assert!(magnitude_words("xx-XX").is_err());
    }

    #[test]
    fn a_region_row_differs_from_its_language_row() {
        let table = magnitude_table();
        for (tag, words) in &table.region {
            assert!(NUMBER_LOCALES.contains(&tag.as_str()), "{tag}");
            let language = &table.language[tag.split('-').next().unwrap()];
            assert!(
                words.analytic != language.analytic || words.narrative != language.narrative,
                "{tag}"
            );
        }
    }

    #[test]
    fn every_time_locale_starts_its_week_where_cldr_says() {
        for tag in TIME_LOCALES {
            week_start(tag).unwrap();
        }
        for (tag, start) in [("en-US", 0), ("de-DE", 1), ("fa-IR", 6), ("en-GB", 1)] {
            assert_eq!(week_start(tag).unwrap(), start, "{tag}");
        }
        assert!(week_start("pt-PT").is_err());
    }
}
