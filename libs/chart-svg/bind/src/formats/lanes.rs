//! A KPI headline cut into the lanes it draws in: the number, and the magnitude word beside it.

use diagnostic::{refuse, Diagnostic};

use super::locale::NumberLocale;
use super::paint::split_si_suffix;
use super::ResolvedFormat;

/// A KPI headline's two lanes: the number, and the magnitude word that rides beside it in the
/// affix face. The sign stays in the number lane, as there is no prefix for it to move to.
#[derive(Debug, Clone, PartialEq)]
pub struct Lanes {
    pub number: String,
    pub suffix: String,
}

impl ResolvedFormat {
    /// The lanes `format_kpi_parts` (`chart-renderer/src/common.rs`) cuts a value into, for a format
    /// with no affix of its own and no unit: no spec is the exact digits (`format_value.py`: an
    /// integer below 1e15 through `,d`, any other number through `,.2~f`), an SI spec splits at its
    /// respelled magnitude word, and every other digit spec is one number. A native member, a
    /// currency or percent spec, an affix, or an SI spec with no notation to spell its word in is
    /// refused, since their lanes are not cut here.
    pub fn paint_lanes(&self, locale: &NumberLocale, value: f64) -> Result<Lanes, Diagnostic> {
        let spec = self.spec.as_str();
        let d3 = |spec: &str, value: f64| d3rs::format::format(&locale.locale, spec, value).map_err(Diagnostic::refused);
        if !self.prefix.is_empty() || !self.suffix.is_empty() {
            return Err(Diagnostic::refused(refuse!("a KPI value format with a prefix or suffix is not supported")));
        }
        if spec.is_empty() {
            let exact = if value == value.trunc() && value.abs() < 1e15 {
                d3(",d", if value == 0.0 { 0.0 } else { value })?
            } else {
                d3(d3rs::format::SUB_UNIT_NUMBER_SPEC, value)?
            };
            return Ok(Lanes { number: exact, suffix: String::new() });
        }
        if d3rs::native::predefined_native(spec, value).map_err(Diagnostic::refused)?.is_some() || spec.contains(['$', '%']) {
            return Err(Diagnostic::refused(refuse!("a native, currency or percent KPI value format is not supported")));
        }
        if d3rs::format::parse_parts(spec).map_err(Diagnostic::refused)?.type_ != 's' {
            return Ok(Lanes { number: d3(spec, value)?, suffix: String::new() });
        }
        let Some(notation) = self.notation else {
            return Err(Diagnostic::refused(refuse!("an SI KPI value format with no notation is not supported")));
        };
        let raw = d3(spec, value)?;
        let (_, letter) = split_si_suffix(&raw);
        if locale.magnitude_word(letter, "analytic").map_err(Diagnostic::refused)?.is_none() {
            return Ok(Lanes { number: raw, suffix: String::new() });
        }
        let digits = self.digits(locale, spec, Some(notation), value)?;
        let (number, word) = split_si_suffix(&digits);
        Ok(Lanes { number: number.trim_matches(d3rs::py_str::is_py_space).to_string(), suffix: word.trim_matches(d3rs::py_str::is_py_space).to_string() })
    }

}
