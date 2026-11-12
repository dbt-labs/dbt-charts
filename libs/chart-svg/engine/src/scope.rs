//! The language, time locale, number locale and time zone a board's words, numbers and dates print in,
//! opened for the whole render.
//!
//! All four come from the board's `regional:`, never from the host: a board with none prints in en-US and
//! UTC, the schema's own fills. A value the engine cannot honor (a defined locale, a currency) is refused by
//! name, not rendered in the default. The thread-locals the readers in `bind` and `layout` consult are only the
//! transport.

use bind::formats::locale::{with_number_locale, NumberLocale};
use board_model::regional::{NumberLocaleChoice, Regional, TimeLocaleChoice};
use d3rs::time_format::Locale;
use diagnostic::{diag, refuse, Diagnostic};
use display_text::language::{language_of_time_locale, with_language, Language};
use jiff::tz::TimeZone;

pub(crate) struct Scope {
    language: Language,
    locale: Locale,
    number: NumberLocale,
    zone: TimeZone,
}

/// The IANA zone `regional` states; a board with none is UTC.
pub(crate) fn zone_name(regional: Option<&Regional>) -> &str {
    regional.map_or("UTC", |r| r.time_zone.as_str())
}

/// The tag of the time locale `regional` names; a board with none is en-US.
pub(crate) fn time_tag(regional: Option<&Regional>) -> Result<&str, Diagnostic> {
    match regional.and_then(|r| r.locale.as_ref()).map(|l| &l.time) {
        None => Ok("en-US"),
        Some(TimeLocaleChoice::Name(name)) => Ok(name.as_str()),
        Some(TimeLocaleChoice::Definition(_)) => {
            Err(Diagnostic::refused(refuse!("a defined time locale is not supported")))
        }
    }
}

fn number_tag(regional: Option<&Regional>) -> Result<&str, Diagnostic> {
    match regional.and_then(|r| r.locale.as_ref()).map(|l| &l.number) {
        None => Ok("en-US"),
        Some(NumberLocaleChoice::Name(name)) => Ok(name.as_str()),
        Some(NumberLocaleChoice::Definition(_)) => {
            Err(Diagnostic::refused(refuse!("a defined number locale is not supported")))
        }
    }
}

/// The scope a board's `regional:` names.
pub(crate) fn of(regional: Option<&Regional>) -> Result<Scope, Diagnostic> {
    if regional.is_some_and(|r| r.currency.is_some()) {
        return Err(Diagnostic::refused(refuse!("a regional currency is not supported")));
    }
    let tag = time_tag(regional)?;
    let language = regional.and_then(|r| r.language.as_ref()).map(|l| l.as_str());
    let code = language.unwrap_or_else(|| language_of_time_locale(Some(tag)));
    let definition = display_text::locale::time_locale(tag).map_err(diag::err_internal)?;
    Ok(Scope {
        language: Language::new(code).map_err(diag::err_internal)?,
        locale: serde_json::from_str(definition).map_err(|e| diag::err_internal(e.to_string()))?,
        number: NumberLocale::named(number_tag(regional)?).map_err(diag::err_internal)?,
        zone: bind::time_zone::named(zone_name(regional)).map_err(|e| diag::err_internal(e.to_string()))?,
    })
}

impl Scope {
    pub(crate) fn within<T>(&self, render: impl FnOnce() -> T) -> T {
        with_language(self.language, || {
            bind::time_locale::with_time_locale(&self.locale, || {
                with_number_locale(&self.number, || bind::time_zone::with_time_zone(&self.zone, render))
            })
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use board_model::fixtures as fixture;
    use serde_json::json;

    fn regional(edit: impl FnOnce(&mut serde_json::Value)) -> Regional {
        let mut value = json!({"time_zone": "UTC", "fiscal_year_start_month": 1, "locale": {"number": "en-US", "time": "en-US"}});
        edit(&mut value);
        fixture::build(&value).unwrap()
    }

    fn read(scope: &Scope) -> (String, i64) {
        scope.within(|| (bind::formats::locale::d3_format(",.1f", 1234.5).unwrap(), bind::time_zone::wall_ms(0).unwrap()))
    }

    #[test]
    fn the_stated_number_locale_and_zone_are_the_ones_readers_see() {
        let stated = regional(|r| {
            r["locale"]["number"] = json!("de-DE");
            r["time_zone"] = json!("America/New_York");
        });
        assert_eq!(read(&of(Some(&stated)).unwrap()), ("1.234,5".to_string(), -5 * 3_600_000));
    }

    #[test]
    fn a_board_with_no_regional_states_en_us_and_utc() {
        assert_eq!(read(&of(None).unwrap()), ("1,234.5".to_string(), 0));
    }

    #[test]
    fn a_value_the_engine_cannot_honor_is_refused_by_name() {
        let currency = regional(|r| r["currency"] = json!("EUR"));
        assert!(of(Some(&currency)).err().unwrap().to_string().contains("a regional currency is not supported"));
        let defined = regional(|r| r["locale"]["number"] = json!({"decimal": ",", "thousands": ".", "grouping": [3], "currency": ["", " EUR"], "percent": "%", "minus": "-"}));
        assert!(of(Some(&defined)).err().unwrap().to_string().contains("a defined number locale is not supported"));
    }
}
