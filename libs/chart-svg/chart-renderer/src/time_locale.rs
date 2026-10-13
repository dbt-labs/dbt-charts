//! The time vocabulary of the board being drawn: a d3 `timeFormatLocale`
//! definition, production's `regional.locale.time` (`active_time_locale()` in
//! `core/text/format_d3.py`).
//!
//! [`with_time_locale`] opens the scope for one render; [`active_time_locale`]
//! refuses when none is open.

use std::cell::RefCell;
use std::rc::Rc;

use serde::Deserialize;

#[derive(Clone, Debug, PartialEq, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TimeLocale {
    pub date_time: String,
    pub date: String,
    pub time: String,
    pub periods: [String; 2],
    /// Sunday first, as d3 lists them.
    pub days: [String; 7],
    pub short_days: [String; 7],
    pub months: [String; 12],
    pub short_months: [String; 12],
}

thread_local! {
    static ACTIVE: RefCell<Option<Rc<TimeLocale>>> = const { RefCell::new(None) };
}

struct Restore(Option<Rc<TimeLocale>>);

impl Drop for Restore {
    fn drop(&mut self) {
        ACTIVE.with(|active| *active.borrow_mut() = self.0.take());
    }
}

/// Run `render` with `locale` as the active time locale.
pub fn with_time_locale<T>(locale: &TimeLocale, render: impl FnOnce() -> T) -> T {
    let previous = ACTIVE.with(|active| active.replace(Some(Rc::new(locale.clone()))));
    let _restore = Restore(previous);
    render()
}

/// The active time locale.
pub fn active_time_locale() -> Result<Rc<TimeLocale>, String> {
    ACTIVE
        .with(|active| active.borrow().clone())
        .ok_or_else(|| {
            "a month, day or period name needs the board's time locale, and the payload's \
         board_style carries no `time_locale`"
                .to_string()
        })
}

#[cfg(test)]
pub(crate) fn en_us() -> TimeLocale {
    serde_json::from_str(include_str!("../data/time_locale_en_us.json"))
        .expect("data/time_locale_en_us.json is a valid time locale")
}

/// Holds en-US as the active locale until dropped (tests that format names).
#[cfg(test)]
pub(crate) fn en_us_scope() -> impl Drop {
    Restore(ACTIVE.with(|active| active.replace(Some(Rc::new(en_us())))))
}

/// en-US in the payload's wire shape (test and harness payload builders).
pub fn en_us_wire() -> serde_json::Value {
    serde_json::from_str(include_str!("../data/time_locale_en_us.json")).expect("valid JSON")
}

#[cfg(test)]
pub(crate) fn de_de() -> TimeLocale {
    serde_json::from_str(include_str!("../data/time_locale_de_de.json"))
        .expect("data/time_locale_de_de.json is a valid time locale")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_locale_with_the_wrong_number_of_months_fails_to_parse() {
        let mut wire: serde_json::Value =
            serde_json::from_str(include_str!("../data/time_locale_en_us.json")).unwrap();
        wire["months"].as_array_mut().unwrap().pop();
        assert!(serde_json::from_value::<TimeLocale>(wire).is_err());
    }

    #[test]
    fn no_scope_refuses_and_the_scope_ends_with_the_render() {
        assert!(active_time_locale().unwrap_err().contains("time_locale"));
        with_time_locale(&de_de(), || {
            assert_eq!(active_time_locale().unwrap().months[2], "März");
        });
        assert!(active_time_locale().is_err());
    }
}
