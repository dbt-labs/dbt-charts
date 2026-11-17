//! A KPI headline cut into the lanes it draws in: the number, and the magnitude word beside it.

use diagnostic::{diag, Diagnostic};

use super::locale::NumberLocale;
use super::paint::split_si_suffix;
use super::{ResolvedFormat, SignPlacement};

/// A KPI value cut into the lanes it draws in: the prefix (the sign and any currency symbol),
/// the number, and the suffix (the magnitude word, a percent sign, an authored suffix). The
/// headline paints each lane in its own face; the support row joins them into one string.
#[derive(Debug, Clone, PartialEq)]
pub struct Lanes {
    pub prefix: String,
    pub number: String,
    pub suffix: String,
}

/// The magnitude letters production substitutes a word for (`format_d3.py`'s
/// `_D3_TO_ANALYTIC` keys); any other letter stays glued to the digits.
const MAGNITUDE_LETTERS: [&str; 8] = ["k", "M", "G", "T", "P", "E", "Z", "Y"];

fn strip(s: &str) -> &str {
    s.trim_matches(d3rs::py_str::is_py_space)
}

fn lstrip(s: &str) -> &str {
    s.trim_start_matches(d3rs::py_str::is_py_space)
}

fn rstrip(s: &str) -> &str {
    s.trim_end_matches(d3rs::py_str::is_py_space)
}

/// A table column's one baked magnitude (`ScaledColumn`): its values divide by `10^exponent` and print through
/// the fixed-point `digit_spec`, the magnitude word set once in the suffix lane.
#[derive(Debug, Clone, PartialEq)]
pub struct ColumnScale {
    pub exponent: i32,
    pub digit_spec: String,
}

impl ResolvedFormat {
    /// [`Self::paint_lanes_in`] with no baked scale: each value prints at its own magnitude.
    pub fn paint_lanes(&self, locale: &NumberLocale, value: f64, native: bool, keep_affix_gaps: bool, anchored: bool, sign_in_prefix: bool) -> Result<Lanes, Diagnostic> {
        self.paint_lanes_in(locale, value, (native, keep_affix_gaps, anchored, sign_in_prefix), None)
    }

    /// `format_kpi_parts` (`compile/format_value.py`): a value's lanes under the `(native, keep_affix_gaps, anchored,
    /// sign_in_prefix)` switches. No spec is the exact digits (an integer below 1e15 through `,d`, any other number
    /// through `,.2~f`); an SI spec splits at its magnitude word (respelled at the format's notation, the analytic
    /// register for a house name; `native` keeps d3's letter); d3 never sees a `$` or a `%`: the locale's own symbol
    /// and gap land in the prefix or suffix lane. `keep_affix_gaps` strips only the far edge of each lane, so an
    /// authored gap against the number survives (the support row); otherwise both edges are stripped and the
    /// locale's gap between digits and a symbol is put back. `anchored`: the column declares its unit once, so a `$`
    /// currency leads; otherwise the locale places it before or after the digits. A native member (`bytes`,
    /// `duration`, `percent_number`) bypasses d3 and prints its own number and unit. `sign_in_prefix` moves a
    /// leading sign into the prefix lane (the KPI does, `kpi.py`; the table keeps it on the digits). `scale`, when a
    /// table column has baked one, replaces the value's own SI tier (`shared_scale`).
    pub fn paint_lanes_in(&self, locale: &NumberLocale, value: f64, (native, keep_affix_gaps, anchored, sign_in_prefix): (bool, bool, bool, bool), scale: Option<&ColumnScale>) -> Result<Lanes, Diagnostic> {
        // An engine SI member never prints an SI prefix below 1: per value it takes its plain sibling.
        // A baked scale has voted the column's one magnitude, so a per-value decision would second-guess it.
        let voted = if scale.is_some() { self.spec.clone() } else { super::sub_unit_spec_for_value(self.raw.as_deref(), &self.spec, value, super::Formats::new(None))? };
        let spec = voted.as_str();
        // `PREDEFINED_NATIVE`: the formatter knows its own unit, and the authored affix rides
        // around the number and the unit.
        if let Some((number, unit)) = d3rs::native::predefined_native(spec, value).map_err(Diagnostic::refused)? {
            // Only the KPI paints a native member; a table refuses one ahead of its lanes.
            if !sign_in_prefix {
                return Err(diag::err_internal(format!("the native format {spec} with the sign kept on the digits")));
            }
            let (explicit_prefix, _, number) = sign_into_prefix(self.prefix.as_str(), "", &number, self.placement());
            let prefix = if keep_affix_gaps { lstrip(&explicit_prefix) } else { strip(&explicit_prefix) }.to_string();
            return Ok(Lanes { prefix, number, suffix: format!("{unit}{}", rstrip(self.suffix.as_str())) });
        }
        if spec.ends_with('%') && !spec.starts_with('+') && value.abs() >= 10.0 {
            return Err(diag::err_percent_range(value, spec));
        }
        let d3 = |spec: &str, value: f64| d3rs::format::format(&locale.locale, spec, value).map_err(Diagnostic::refused);
        let (mut d3_prefix, mut d3_suffix) = (String::new(), String::new());
        let mut magnitude_word = String::new();
        let mut number;
        if spec.is_empty() {
            number = if value == value.trunc() && value.abs() < 1e15 {
                d3(",d", if value == 0.0 { 0.0 } else { value })?
            } else {
                d3(d3rs::format::SUB_UNIT_NUMBER_SPEC, value)?
            };
        } else {
            if spec.contains('$') {
                [d3_prefix, d3_suffix] = if anchored { [locale.currency_front(), String::new()] } else { locale.locale.currency.clone() };
            }
            let clean = spec.replace('$', "");
            if let Some(scale) = scale {
                number = d3(&scale.digit_spec, value / 10_f64.powi(scale.exponent))?;
                if value != 0.0 && !number.chars().any(|c| ('1'..='9').contains(&c)) {
                    // Too small for the column's decimals: the grid's smallest step as a bound, never a bare zero.
                    let decimals = d3rs::format::parse_parts(&scale.digit_spec).map_err(Diagnostic::refused)?.precision.ok_or_else(|| diag::err_internal(format!("the baked digit spec {} has no precision", scale.digit_spec)))?;
                    let step = 10_f64.powi(-(decimals as i32)).copysign(value);
                    let bound = if value > 0.0 { "<" } else { ">" };
                    number = format!("{bound}{}", d3(&scale.digit_spec, step)?);
                }
                if value != 0.0 {
                    let letter = match scale.exponent {
                        3 => "k",
                        6 => "M",
                        9 => "G",
                        12 => "T",
                        other => return Err(diag::err_internal(format!("the magnitude exponent {other} is not a shared-scale tier"))),
                    };
                    let notation = self.notation.unwrap_or(super::Notation::Analytic);
                    magnitude_word = locale.magnitude_word(letter, notation.as_str()).map_err(Diagnostic::refused)?.unwrap_or_else(|| letter.to_string());
                }
            } else if clean.ends_with('s') {
                let raw = d3(&clean, value)?;
                let (digits, letter) = split_si_suffix(&raw);
                let notation = self.notation.or(self.is_house().then_some(super::Notation::Analytic));
                number = digits.to_string();
                match notation {
                    _ if !MAGNITUDE_LETTERS.contains(&letter) => number = raw,
                    Some(notation) if !native => {
                        magnitude_word = locale.magnitude_word(letter, notation.as_str()).map_err(Diagnostic::refused)?.unwrap_or_else(|| letter.to_string());
                    }
                    _ => magnitude_word = letter.to_string(),
                }
            } else {
                number = d3(&clean, value)?;
            }
            if spec.contains('%') {
                if let Some(stripped) = number.strip_suffix(locale.locale.percent.as_str()) {
                    d3_suffix = locale.locale.percent.clone();
                    number = stripped.to_string();
                }
            }
        }
        let (explicit_prefix, explicit_suffix) = (self.prefix.as_str(), self.suffix.as_str());
        let magnitude = strip(&magnitude_word);
        let number = strip(&number);
        let (explicit_prefix, d3_prefix, number) = if sign_in_prefix {
            sign_into_prefix(explicit_prefix, &d3_prefix, number, self.placement())
        } else {
            (explicit_prefix.to_string(), d3_prefix, number.to_string())
        };
        let prefix = format!("{explicit_prefix}{d3_prefix}");
        let suffix = format!("{magnitude}{d3_suffix}{explicit_suffix}");
        if keep_affix_gaps {
            return Ok(Lanes { prefix: lstrip(&prefix).to_string(), number, suffix: rstrip(&suffix).to_string() });
        }
        let mut prefix = strip(&prefix).to_string();
        let mut suffix = strip(&suffix).to_string();
        if !strip(&d3_prefix).is_empty() {
            prefix.push_str(&d3_prefix[rstrip(&d3_prefix).len()..]);
        }
        if !strip(&d3_suffix).is_empty() && magnitude.is_empty() {
            suffix = format!("{}{suffix}", &d3_suffix[..d3_suffix.len() - lstrip(&d3_suffix).len()]);
        }
        Ok(Lanes { prefix, number, suffix })
    }
}

/// `_sign_into_prefix` (`format_value.py:178-191`): the number's leading sign moves ahead of both
/// prefixes, or, `after_prefix` with an authored prefix, sits between it and d3's currency symbol,
/// which joins the number lane. No prefix: untouched.
fn sign_into_prefix(explicit_prefix: &str, d3_prefix: &str, number: &str, placement: SignPlacement) -> (String, String, String) {
    if explicit_prefix.is_empty() && d3_prefix.is_empty() {
        return (String::new(), String::new(), number.to_string());
    }
    let (sign, digits) = match number.chars().next() {
        Some(c @ ('\u{2212}' | '+' | '-')) => number.split_at(c.len_utf8()),
        _ => ("", number),
    };
    if !explicit_prefix.is_empty() && placement == SignPlacement::AfterPrefix {
        return (explicit_prefix.to_string(), String::new(), format!("{sign}{d3_prefix}{digits}"));
    }
    (format!("{sign}{explicit_prefix}"), d3_prefix.to_string(), digits.to_string())
}
