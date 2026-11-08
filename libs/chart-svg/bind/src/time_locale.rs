//! The time vocabulary of the board being drawn: a d3 `timeFormatLocale`
//! definition, production's `regional.locale.time` (`active_time_locale()` in
//! `core/text/format_d3.py`).
//!
//! [`with_time_locale`] opens the scope for one render; [`active_time_locale`]
//! refuses when none is open.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use d3rs::time_format::Locale;

thread_local! {
    static ACTIVE: RefCell<Option<Rc<Locale>>> = const { RefCell::new(None) };
    static WEEK_START: Cell<u8> = const { Cell::new(0) };
}

struct Restore(Option<Rc<Locale>>);

impl Drop for Restore {
    fn drop(&mut self) {
        ACTIVE.with(|active| *active.borrow_mut() = self.0.take());
    }
}

/// Run `render` with `locale` as the active time locale.
pub fn with_time_locale<T>(locale: &Locale, render: impl FnOnce() -> T) -> T {
    let previous = ACTIVE.with(|active| active.replace(Some(Rc::new(locale.clone()))));
    let _restore = Restore(previous);
    render()
}

/// Run `render` with `week_start` (0 = Sunday) as the board's first day of the
/// week.
pub fn with_week_start<T>(week_start: u8, render: impl FnOnce() -> T) -> T {
    let previous = WEEK_START.with(|active| active.replace(week_start));
    struct Reset(u8);
    impl Drop for Reset {
        fn drop(&mut self) {
            WEEK_START.with(|active| active.set(self.0));
        }
    }
    let _reset = Reset(previous);
    render()
}

/// The board's first day of the week, 0 = Sunday; Sunday outside a scope.
pub fn active_week_start() -> u8 {
    WEEK_START.with(Cell::get)
}

/// The active time locale.
pub fn active_time_locale() -> Result<Rc<Locale>, String> {
    ACTIVE
        .with(|active| active.borrow().clone())
        .ok_or_else(|| {
            "a month, day or period name needs the board's time locale, and the payload's \
         board_style carries no `time_locale`"
                .to_string()
        })
}

/// The board language's word for each quarter of a year ("Q1" .. "Q4"): size-free text the time
/// axis places.
pub fn quarter_words() -> Result<[String; 4], String> {
    let word = |n: u8| display_text::language::tr("time.quarter", None, &[("n", &n.to_string())]);
    Ok([word(1)?, word(2)?, word(3)?, word(4)?])
}

/// The board language's words for 00:00 and 12:00: size-free text a clock axis places.
pub fn clock_words() -> Result<[String; 2], String> {
    let word = |id: &str| display_text::language::tr(id, None, &[]);
    Ok([word("axis.midnight")?, word("axis.noon")?])
}

#[cfg(any(test, feature = "test-support"))]
pub fn en_us() -> Locale {
    serde_json::from_str(display_text::locale::time_locale("en-US").unwrap())
        .expect("en-US is a valid time locale")
}

/// Holds en-US as the active locale until dropped (tests that format names).
#[cfg(any(test, feature = "test-support"))]
pub fn en_us_scope() -> impl Drop {
    Restore(ACTIVE.with(|active| active.replace(Some(Rc::new(en_us())))))
}

/// en-US in the payload's wire shape (test and harness payload builders).
pub fn en_us_wire() -> serde_json::Value {
    serde_json::from_str(display_text::locale::time_locale("en-US").unwrap()).expect("valid JSON")
}

#[cfg(any(test, feature = "test-support"))]
pub fn de_de() -> Locale {
    serde_json::from_str(display_text::locale::time_locale("de-DE").unwrap())
        .expect("de-DE is a valid time locale")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_clock_words_are_the_board_languages() {
        assert_eq!(clock_words().unwrap(), ["Midnight", "Noon"]);
        let german: display_text::language::Language = serde_json::from_str("\"de\"").unwrap();
        let words = display_text::language::with_language(german, || clock_words().unwrap());
        assert_eq!(words, ["Mitternacht", "Mittag"]);
    }

    #[test]
    fn a_locale_with_the_wrong_number_of_months_fails_to_parse() {
        let mut wire: serde_json::Value =
            serde_json::from_str(display_text::locale::time_locale("en-US").unwrap()).unwrap();
        wire["months"].as_array_mut().unwrap().pop();
        assert!(serde_json::from_value::<Locale>(wire).is_err());
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
