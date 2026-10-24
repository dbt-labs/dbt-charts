//! `core/font_measure.py`'s reservation composition: the invisible space
//! characters whose summed advance matches a suffix's, so a cell without the
//! suffix still lines its digits up with one that carries it.
//! `compose_decimal_units` feeds `d3rs::numeral_pad::build_decimal_pad_table`.

use std::collections::HashMap;

use crate::fonts::FontMeasurer;

/// `font_measure.py::RESERVATION_GUARD` — U+200B ZERO WIDTH SPACE, appended
/// after a trailing suffix-field reservation so a serializer treats the run
/// as interior rather than trailing whitespace to trim. Written as the
/// escape sequence, not the raw character, for the same reason the Python
/// source is: it stays visible in a diff.
pub const RESERVATION_GUARD: char = '\u{200b}';

/// `font_measure.py::_RESERVATION_SPACE_CANDIDATES`, in the same search
/// order: EN, EM, THREE-PER-EM, FOUR-PER-EM, SIX-PER-EM, FIGURE,
/// PUNCTUATION, THIN, HAIR, MEDIUM MATHEMATICAL space.
const RESERVATION_SPACE_CANDIDATES: &[char] = &[
    '\u{2002}', '\u{2003}', '\u{2004}', '\u{2005}', '\u{2006}', '\u{2007}', '\u{2008}', '\u{2009}',
    '\u{200a}', '\u{205f}',
];

/// `font_measure.py::_MAX_RESERVATION_COMPOSITION_LENGTH`.
const MAX_RESERVATION_COMPOSITION_LENGTH: usize = 3;

/// `font_measure.py::_SUBPIXEL_RESIDUAL_TOLERANCE_EM`.
const SUBPIXEL_RESIDUAL_TOLERANCE_EM: f64 = 0.05;

/// `itertools.combinations_with_replacement(items, r)`: every non-decreasing
/// index tuple of length `r` over `items`, in the same lexicographic order
/// Python's own generator yields.
fn combinations_with_replacement(items: &[char], r: usize) -> Vec<Vec<char>> {
    if items.is_empty() || r == 0 {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut idx = vec![0usize; r];
    loop {
        out.push(idx.iter().map(|&i| items[i]).collect());
        let mut pos = r;
        let advanced = loop {
            if pos == 0 {
                break false;
            }
            pos -= 1;
            if idx[pos] + 1 < items.len() {
                idx[pos] += 1;
                for j in (pos + 1)..r {
                    idx[j] = idx[pos];
                }
                break true;
            }
        };
        if !advanced {
            return out;
        }
    }
}

/// `_search_space_combo`: the combination of up to
/// `MAX_RESERVATION_COMPOSITION_LENGTH` characters from `available` whose
/// summed advance is closest to `target`. Deterministic tie-break: fewer
/// characters win first (the outer length loop, strict `<` on error), then
/// `available`'s own order (the lexicographic combination order the nested
/// loop above produces) — no separate tie-break step, same as production.
fn search_space_combo(
    target: f64,
    available: &[char],
    widths: &HashMap<char, f64>,
) -> (Vec<char>, f64) {
    let mut best: Vec<char> = Vec::new();
    let mut best_error = f64::INFINITY;
    for length in 1..=MAX_RESERVATION_COMPOSITION_LENGTH {
        for combo in combinations_with_replacement(available, length) {
            let error = (combo.iter().map(|c| widths[c]).sum::<f64>() - target).abs();
            if error < best_error {
                best_error = error;
                best = combo;
            }
        }
    }
    (best, best_error)
}

/// The pure composition step: run the search over already-measured
/// candidate widths and apply the sub-pixel tolerance — factored out of
/// `compose_suffix_reservation` so the refusal branch is unit-testable with
/// synthetic widths, since no vendored font's real metrics happen to miss
/// tolerance (`compose_suffix_reservation`'s own doc records the worst
/// observed case, well under it).
pub(crate) fn compose_from_widths(
    target: f64,
    suffix: &str,
    font_family: &str,
    available: &[char],
    widths: &HashMap<char, f64>,
) -> Result<String, String> {
    let (mut best, best_error) = search_space_combo(target, available, widths);

    if best_error > SUBPIXEL_RESIDUAL_TOLERANCE_EM {
        return Err(format!(
            "{font_family:?} cannot compose a sub-pixel suffix-field reservation for \
             {suffix:?}: the best achievable combination from its available space \
             characters is {best_error:.4}em off target (tolerance \
             {SUBPIXEL_RESIDUAL_TOLERANCE_EM}em) — a font-registry defect, not something \
             a caller can work around"
        ));
    }

    best.sort_unstable();
    let mut out: String = best.into_iter().collect();
    out.push(RESERVATION_GUARD);
    Ok(out)
}

/// `compose_suffix_reservation`: the trailing padding a non-suffix tick
/// reserves, so its digits still line up with a tick that carries `suffix`,
/// measured against `measurer`, the face `font_family` names.
///
/// `Ok("")` for an empty suffix. `Err` when the typeface cannot compose a
/// sub-pixel match: a font-registry defect, refused by name rather than
/// degraded around.
pub fn compose_suffix_reservation(
    measurer: &FontMeasurer,
    suffix: &str,
    font_family: &str,
) -> Result<String, String> {
    if suffix.is_empty() {
        return Ok(String::new());
    }
    let available: Vec<char> = RESERVATION_SPACE_CANDIDATES
        .iter()
        .copied()
        .filter(|&c| measurer.has_glyph(c))
        .collect();
    let widths: HashMap<char, f64> = available
        .iter()
        .map(|&c| (c, measurer.measure(&c.to_string(), 1.0)))
        .collect();
    let target = measurer.measure(suffix, 1.0);
    compose_from_widths(target, suffix, font_family, &available, &widths)
}

/// `compose_decimal_units`: the digit and dot padding units
/// `d3rs::numeral_pad::build_decimal_pad_table` composes from, measured from
/// the actual font glyphs.
pub fn compose_decimal_units(
    measurer: &FontMeasurer,
    font_family: &str,
) -> Result<(String, String), String> {
    Ok((
        compose_suffix_reservation(measurer, "0", font_family)?,
        compose_suffix_reservation(measurer, ".", font_family)?,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn combinations_with_replacement_matches_itertools_order() {
        let items = ['a', 'b', 'c'];
        let combos = combinations_with_replacement(&items, 2);
        assert_eq!(
            combos,
            vec![
                vec!['a', 'a'],
                vec!['a', 'b'],
                vec!['a', 'c'],
                vec!['b', 'b'],
                vec!['b', 'c'],
                vec!['c', 'c'],
            ]
        );
    }

    #[test]
    fn search_space_combo_prefers_fewer_characters_on_a_tie() {
        let available = ['a', 'b'];
        let mut widths = HashMap::new();
        widths.insert('a', 1.0);
        widths.insert('b', 0.5);
        // target 1.0 is matched exactly by "a" alone (length 1) and by "b b"
        // (length 2, unavailable at length 1): the length-ascending outer
        // loop must return the length-1 winner.
        let (best, error) = search_space_combo(1.0, &available, &widths);
        assert_eq!(best, vec!['a']);
        assert_eq!(error, 0.0);
    }

    #[test]
    fn a_target_no_combination_reaches_within_tolerance_refuses_by_name() {
        // Every candidate measures 0.0, so `best_error` is pinned at the
        // target itself, far past `SUBPIXEL_RESIDUAL_TOLERANCE_EM`. Synthetic
        // widths: no vendored typeface's real metrics happen to miss tolerance.
        let available = ['a', 'b'];
        let mut widths = HashMap::new();
        widths.insert('a', 0.0);
        widths.insert('b', 0.0);
        let err =
            compose_from_widths(10.0, "suffix", "Test Family", &available, &widths).unwrap_err();
        assert!(
            err.contains("cannot compose a sub-pixel suffix-field reservation"),
            "{err}"
        );
        assert!(err.contains("Test Family"), "{err}");
        assert!(err.contains("\"suffix\""), "{err}");
    }
}
