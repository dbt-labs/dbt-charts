//! `format_d3` (`text/format_d3.py:572`): a value printed through a resolved format
//! under the board's number locale. The d3 digits, an SI letter respelled at the
//! format's notation, then the affix with the sign placed against the prefix. Every
//! painter takes the locale it prints in.
//!
//! It is the bind painter, the renderer's `FormatSlot` until the cutover:
//! `ResolvedFormat::paint`, `paint_tick`, `paint_signed` and `paint_anchored` print the
//! d3 digits, the SI letter in the locale's words, and the affix and sign placement
//! under an explicit `NumberLocale`.

use diagnostic::Diagnostic;

use super::locale::NumberLocale;
use super::{Notation, ResolvedFormat, SignPlacement};

impl ResolvedFormat {
    /// `ResolvedFormat.has_affix`.
    pub fn has_affix(&self) -> bool {
        !self.prefix.is_empty() || !self.suffix.is_empty() || self.notation.is_some()
    }

    /// `ResolvedFormat.placement`: the authored placement, else the prefix's default.
    pub fn placement(&self) -> SignPlacement {
        self.sign_placement.unwrap_or_else(|| default_placement(&self.prefix))
    }

    /// `format_d3(value, spec, prefix, suffix, notation, placement)`.
    pub fn paint(&self, locale: &NumberLocale, value: f64) -> Result<String, Diagnostic> {
        self.paint_as(locale, &self.spec, self.notation, value)
    }

    /// [`Self::paint`] with the digits' spec and notation overridden: the house
    /// register's sub-unit and narrative variants of one format.
    pub fn paint_as(
        &self,
        locale: &NumberLocale,
        spec: &str,
        notation: Option<Notation>,
        value: f64,
    ) -> Result<String, Diagnostic> {
        let digits = self.digits(locale, spec, notation, value)?;
        let (sign, body) = split_sign(&digits);
        Ok(self.wear(self.placement(), sign, body))
    }

    /// `sign_affixed_expr` (`vl_field_maps.py:208`): a magnitude with `sign` placed
    /// against the prefix (a ranged bar's signed difference).
    pub fn paint_signed(&self, locale: &NumberLocale, sign: char, magnitude: f64) -> Result<String, Diagnostic> {
        let digits = self.digits(locale, &self.spec, self.notation, magnitude)?;
        Ok(self.wear(self.placement(), &sign.to_string(), &digits))
    }

    /// A plain value-axis tick (`compose_axis_format`, `vl_field_maps.py:217`): the zero
    /// tick bare, without the affix or the spec's own currency symbol; every other tick
    /// is [`Self::paint`].
    pub fn paint_tick(&self, locale: &NumberLocale, value: f64) -> Result<String, Diagnostic> {
        if value != 0.0 {
            return self.paint(locale, value);
        }
        let bare = match self.spec.as_str() {
            "bytes" | "duration" => self.spec.clone(),
            spec => d3rs::format::without_symbol(spec).map_err(Diagnostic::refused)?,
        };
        self.digits(locale, &bare, None, value)
    }

    /// A rung of a ladder production does not bake, printed as Vega's axis prints it: `rung` is
    /// the tick's index and the ladder's length (`None` for a gutter estimate, which dresses every
    /// candidate), `extent_hi` the top of the axis field's raw extent. A house SI format paints
    /// each tick by its magnitude (`sub_unit_guarded_format`): the SI spec from 1 up, else the
    /// plain significant-digit sibling, or the exponential one below 1e-10, and its anchored affix
    /// wears the last tick, or the first when no value is above 0 (`ladderless_tick_label`). Any
    /// other format with an anchored affix wears the last nonzero tick alone
    /// (`affix_tick_label`); without one, every tick prints [`Self::paint_tick`].
    pub fn paint_unbaked_rung(
        &self,
        locale: &NumberLocale,
        value: f64,
        rung: Option<(usize, usize)>,
        extent_hi: f64,
    ) -> Result<String, Diagnostic> {
        let affixed = !self.prefix.is_empty() || !self.suffix.is_empty();
        let anchored = self.repeat == Some(super::Repeat::Anchor) && affixed;
        if !self.is_house_si() {
            return match rung {
                Some((index, count)) if anchored => {
                    if index + 1 == count && value != 0.0 {
                        self.paint_anchored(locale, &self.spec, value, true)
                    } else {
                        self.bare_digits(locale, &self.spec, value)
                    }
                }
                _ => self.paint_tick(locale, value),
            };
        }
        let (digit, scientific) = d3rs::format::sub_unit_specs(&self.spec).map_err(Diagnostic::refused)?;
        let band = if value.abs() >= 1.0 {
            &self.spec
        } else if value != 0.0 && value.abs() < d3rs::format::SUB_UNIT_SCIENTIFIC_FLOOR {
            &scientific
        } else {
            &digit
        };
        let wears = match rung {
            Some((index, count)) if anchored => index == if extent_hi <= 0.0 { 0 } else { count - 1 },
            _ => true,
        };
        if wears && affixed && value != 0.0 {
            return self.paint_anchored(locale, band, value, anchored);
        }
        self.bare_digits(locale, band, value)
    }

    /// The rungs Vega draws on an axis production does not bake: d3's ticks from `start` to
    /// `stop` at Vega's count for `target` (capped by `min_step`), each printed by
    /// [`Self::paint_unbaked_rung`].
    pub fn unbaked_rungs(
        &self,
        locale: &NumberLocale,
        (start, stop): (f64, f64),
        target: usize,
        min_step: Option<f64>,
        extent_hi: f64,
    ) -> Result<Vec<(f64, String)>, Diagnostic> {
        let count = d3rs::ticks::vega_tick_count(start, stop, target, min_step);
        let ticks = d3rs::ticks::d3_ticks(start, stop, count);
        let total = ticks.len();
        ticks
            .into_iter()
            .enumerate()
            .map(|(index, value)| Ok((value, self.paint_unbaked_rung(locale, value, Some((index, total)), extent_hi)?)))
            .collect()
    }

    /// A ladder tick's affixed digits (`_affixed_digits_expr`): `spec` at this format's
    /// notation inside its affix. An anchored affix (one tick per ladder) leads the sign,
    /// so the anchor's digits line up with the bare ticks beside it
    /// (`painted_sign_placement`).
    pub fn paint_anchored(
        &self,
        locale: &NumberLocale,
        spec: &str,
        value: f64,
        anchored: bool,
    ) -> Result<String, Diagnostic> {
        let digits = self.digits(locale, spec, self.notation, value)?;
        let (sign, body) = split_sign(&digits);
        let placement = if anchored { SignPlacement::AfterPrefix } else { self.placement() };
        Ok(self.wear(placement, sign, body))
    }

    /// A ladder tick's bare digits at this format's notation
    /// (`numeral_vega_expr(datum.value, tick_label.format, fmt.notation)`).
    pub fn bare_digits(&self, locale: &NumberLocale, spec: &str, value: f64) -> Result<String, Diagnostic> {
        self.digits(locale, spec, self.notation, value)
    }

    /// The d3 digits of `value` under `locale`, an SI letter respelled at `notation`.
    pub(super) fn digits(
        &self,
        locale: &NumberLocale,
        spec: &str,
        notation: Option<Notation>,
        value: f64,
    ) -> Result<String, Diagnostic> {
        if spec.is_empty() && (self.has_affix() || notation.is_some()) {
            return Err(Diagnostic::refused(
                "format spec is empty — an affixed or notated format needs a d3 spec",
            ));
        }
        let d3 = |spec: &str, value: f64| d3rs::format::format(&locale.locale, spec, value);
        // The two natives with a Vega arm (`VEGA_ARMS`); `bytes` speaks the board's
        // locale, `duration` has no digits a locale changes.
        if spec == "bytes" {
            let (number, unit) = d3rs::native::bytes(value, d3).map_err(Diagnostic::refused)?;
            return Ok(format!("{number}{unit}"));
        }
        if spec == "duration" {
            let (number, unit) = d3rs::native::predefined_native(spec, value)
                .map_err(Diagnostic::refused)?
                .ok_or_else(|| Diagnostic::refused("duration is not an engine native"))?;
            return Ok(format!("{number}{unit}"));
        }
        let digits = d3(spec, value).map_err(|e| Diagnostic::refused(format!("format {spec:?} on {value}: {e}")))?;
        if d3rs::format::parse_parts(spec).map_err(Diagnostic::refused)?.type_ != 's' {
            return Ok(digits);
        }
        let body = digits.strip_suffix(locale.currency_suffix(spec)).unwrap_or(&digits);
        let (number, letter) = split_si_suffix(body);
        // d3's raw `G` reads as gigabytes: an unnotated SI format spells billions in
        // the analytic register, as the ruler does.
        let notation = match notation {
            Some(notation) => notation,
            None if letter == "G" => Notation::Analytic,
            None => return Ok(digits),
        };
        Ok(match locale.magnitude_word(letter, notation.as_str()).map_err(Diagnostic::refused)? {
            Some(word) => format!("{number}{word}{}", &digits[body.len()..]),
            None => digits,
        })
    }

    /// `affix_signed` (`text/format_d3.py:546`): `body` inside the prefix and suffix,
    /// `sign` placed against the prefix.
    fn wear(&self, placement: SignPlacement, sign: &str, body: &str) -> String {
        if self.prefix.is_empty() {
            return format!("{sign}{body}{}", self.suffix);
        }
        match placement {
            SignPlacement::AfterPrefix => format!("{}{sign}{body}{}", self.prefix, self.suffix),
            SignPlacement::BeforePrefix => format!("{sign}{}{body}{}", self.prefix, self.suffix),
        }
    }
}

/// `default_sign_placement` (`format_d3.py:60`): a prefix ending in whitespace is a
/// word or code (`CHF −500`), anything else a glued symbol (`−US$500`).
fn default_placement(prefix: &str) -> SignPlacement {
    if prefix.chars().next_back().is_some_and(d3rs::py_str::is_py_space) {
        SignPlacement::AfterPrefix
    } else {
        SignPlacement::BeforePrefix
    }
}

/// d3's leading sign and the digits after it.
fn split_sign(digits: &str) -> (&str, &str) {
    match digits.chars().next() {
        Some(c @ ('\u{2212}' | '+' | '-')) => digits.split_at(c.len_utf8()),
        _ => ("", digits),
    }
}

/// A d3 SI string split after its last digit: the number and the SI letter (`12.3M`).
pub(super) fn split_si_suffix(s: &str) -> (&str, &str) {
    match s.char_indices().rev().find(|(_, c)| c.is_ascii_digit()) {
        Some((i, _)) => s.split_at(i + 1),
        None => (s, ""),
    }
}
