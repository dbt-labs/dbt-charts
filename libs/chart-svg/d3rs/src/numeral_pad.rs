//! The string laws of `core/text/numeral_scale.py`'s decimal alignment: the pad
//! table a column of mixed fractional depths is aligned with and the gate that
//! decides whether a column has a shared decimal position at all. The unit
//! strings the table is built from are measured from a font
//! (`measure::reservation`).

use std::collections::HashSet;

/// `decimal_reservation_pad`: the trailing padding that makes a trimmed
/// decimal value as wide as its untrimmed sibling. `missing_len` is
/// `len(full_text) - len(trimmed_text)` for the same spec with/without
/// `~`; `has_dot` is whether the decimal point itself survived trimming.
fn decimal_reservation_pad(
    missing_len: usize,
    has_dot: bool,
    digit_unit: &str,
    dot_unit: &str,
) -> String {
    if missing_len == 0 {
        return String::new();
    }
    let digit_pad_count = missing_len - usize::from(!has_dot);
    format!(
        "{}{}",
        digit_unit.repeat(digit_pad_count),
        if has_dot { "" } else { dot_unit }
    )
}

/// `build_decimal_pad_table`: pre-built pad table for a fixed-point format
/// at `precision`. Index `i` (0..=precision+1) is the trailing pad for
/// `missing_len == i`; index `precision + 1` is the whole-number case
/// (fractional part AND dot both trimmed).
pub fn build_decimal_pad_table(precision: usize, digit_unit: &str, dot_unit: &str) -> Vec<String> {
    (0..=precision + 1)
        .map(|i| decimal_reservation_pad(i, i < precision + 1, digit_unit, dot_unit))
        .collect()
}

/// `decimal_pad_table_for`'s gate: the precision a pad table is built at for a
/// literal d3 spec, which is a trim-enabled fixed-point type with decimals
/// (declared precision, or 6 when absent). `None` for every other spec, a
/// native name or anything d3 cannot parse.
pub fn pad_table_precision(spec: &str) -> Option<usize> {
    let parts = crate::format::parse_parts(spec).ok()?;
    if !parts.trim || parts.type_ != 'f' {
        return None;
    }
    Some(parts.precision.unwrap_or(6)).filter(|&precision| precision > 0)
}

/// `fractional_digit_count` (`numeral_scale.py:431`): the digit characters
/// after the locale's `decimal` mark, so a trailing non-digit (an accounting
/// `)`, a magnitude suffix letter) never inflates the depth.
pub fn fractional_digit_count(num_str: &str, decimal: &str) -> usize {
    num_str
        .split_once(decimal)
        .map_or(0, |(_, fraction)| fraction.chars().filter(|c| c.is_numeric()).count())
}

/// `_unscaled_decimal_pad`'s decision over a row's own printed cells: the
/// precision to pad to, the deepest fractional depth printed, when every
/// finite non-zero cell shares one printed unit and the depths differ.
pub fn unscaled_pad_precision(cells: &[(f64, String)], decimal: &str) -> Option<usize> {
    if !column_shares_one_printed_unit(cells) {
        return None;
    }
    let depths: HashSet<usize> = cells.iter().map(|(_, text)| fractional_digit_count(text, decimal)).collect();
    depths.iter().copied().max().filter(|_| depths.len() > 1)
}

/// `_printed_si_suffix`: the trailing SI magnitude-suffix letters a d3
/// `s`-type spec printed, or `""` for a value printed with none. A closing
/// accounting `)` is stripped first.
fn printed_si_suffix(formatted: &str) -> String {
    let text = formatted.strip_suffix(')').unwrap_or(formatted);
    let end = text
        .char_indices()
        .rev()
        .find(|(_, c)| !c.is_alphabetic())
        .map_or(0, |(i, c)| i + c.len_utf8());
    text[end..].to_string()
}

/// `column_shares_one_printed_unit`: true when every non-zero, finite
/// value's actual printed SI suffix agrees, the gate a no-shared-tier
/// decimal-pad bake must pass before it means anything to pad cells
/// together at all. `cells` pairs each value with its own formatted text.
pub fn column_shares_one_printed_unit(cells: &[(f64, String)]) -> bool {
    let suffixes: HashSet<String> = cells
        .iter()
        .filter(|(v, _)| *v != 0.0 && v.is_finite())
        .map(|(_, text)| printed_si_suffix(text))
        .collect();
    suffixes.len() <= 1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_table_has_precision_plus_two_entries() {
        let table = build_decimal_pad_table(2, "D", "P");
        assert_eq!(table, ["", "D", "DD", "DDP"].map(String::from));
    }

    #[test]
    fn only_a_trim_fixed_point_spec_with_decimals_pads() {
        assert_eq!(pad_table_precision(",.2~f"), Some(2));
        assert_eq!(pad_table_precision(",~f"), Some(6));
        for spec in [",.2f", ",.0~f", ".3~s", "not a spec"] {
            assert_eq!(pad_table_precision(spec), None, "{spec}");
        }
    }

    #[test]
    fn depth_counts_digits_after_the_decimal_mark_only() {
        assert_eq!(fractional_digit_count("(2.5)", "."), 1);
        assert_eq!(fractional_digit_count("4.00M", "."), 2);
        assert_eq!(fractional_digit_count("2,5", ","), 1);
        assert_eq!(fractional_digit_count("25", "."), 0);
    }

    #[test]
    fn an_unscaled_row_pads_to_its_deepest_cell_only_when_the_depths_differ_under_one_unit() {
        let cell = |v: f64, t: &str| (v, t.to_string());
        assert_eq!(unscaled_pad_precision(&[cell(15e3, "15k"), cell(3.5e3, "3.5k")], "."), Some(1));
        assert_eq!(unscaled_pad_precision(&[cell(15e3, "15k"), cell(5e3, "5k")], "."), None);
        assert_eq!(unscaled_pad_precision(&[cell(12.1e3, "12.1k"), cell(900.0, "900")], "."), None);
    }

    #[test]
    fn a_column_with_two_printed_units_shares_none() {
        let cells = [(12_100.0, "12.1k".to_string()), (900.0, "900".to_string())];
        assert!(!column_shares_one_printed_unit(&cells));
        let same = [(12_100.0, "12.1k".to_string()), (5_000.0, "5k".to_string())];
        assert!(column_shares_one_printed_unit(&same));
    }
}
