//! The one law that fits text to a width and a line limit.

use std::cell::RefCell;

use d3rs::py_str::is_py_space;

use crate::fonts::{
    measure_text_precise, FontMeasurer, pieces_to_text, split_token_precise, truncate_by, wrap_measured_pieces, WrapPiece,
};

/// Text fitted to a width: the lines to draw and what the fit cost.
#[derive(Debug, Clone, PartialEq)]
pub struct FittedText {
    /// The lines drawn; the last ends in an ellipsis when `cut`.
    pub lines: Vec<String>,
    /// How many lines the text takes with no limit.
    pub natural_lines: usize,
    /// The limit dropped text.
    pub cut: bool,
}

/// `text` wrapped to `width` by `measure` and held to `max_lines` (`None` draws every line).
///
/// Whitespace collapses to single spaces. A word wider than the line breaks after its last
/// seam character that fits, else after its longest fitting prefix. A line's width is the sum
/// of its pieces' measured widths and the spaces between them, never a re-measure of the joined
/// text. Past `max_lines`, the last line holds the rest of the text cut to `prefix + "…"`, or to
/// its first character when not even that fits. The first error `measure` returns is the error.
///
/// # Panics
/// When `max_lines` is `Some(0)`: a line limit is at least one line.
pub fn fit_lines<E>(
    text: &str,
    width: f64,
    measure: &dyn Fn(&str) -> Result<f64, E>,
    max_lines: Option<usize>,
) -> Result<FittedText, E> {
    assert!(max_lines != Some(0), "fit_lines: max_lines must be at least 1");
    let failed = RefCell::new(None);
    let measured = |piece: &str| match measure(piece) {
        Ok(w) => w,
        Err(e) => {
            failed.borrow_mut().get_or_insert(e);
            0.0
        }
    };
    let fitted = fit_infallible(text, width, &measured, max_lines);
    match failed.into_inner() {
        Some(e) => Err(e),
        None => Ok(fitted),
    }
}

/// [`fit_lines`] against a font measurer at `size`.
pub fn fit_with(text: &str, width: f64, size: f64, measurer: &FontMeasurer, max_lines: Option<usize>) -> FittedText {
    fit_infallible(text, width, &|t| measure_text_precise(t, size, measurer), max_lines)
}

fn fit_infallible(text: &str, width: f64, measure: &dyn Fn(&str) -> f64, max_lines: Option<usize>) -> FittedText {
    let text = d3rs::py_str::normalize_whitespace(text.trim_matches(is_py_space));
    if text.is_empty() {
        return FittedText { lines: Vec::new(), natural_lines: 0, cut: false };
    }
    let space_width = measure(" ");
    let mut pieces: Vec<WrapPiece> = Vec::new();
    for (word_index, word) in text.split(' ').enumerate() {
        for (chunk_index, chunk) in split_token_precise(word, width, measure).into_iter().enumerate() {
            let spaced = word_index > 0 && chunk_index == 0;
            pieces.push(WrapPiece {
                width: measure(&chunk),
                text: chunk,
                separator: if spaced { " " } else { "" }.to_string(),
                separator_width: if spaced { space_width } else { 0.0 },
                meta: None,
                glued: false,
            });
        }
    }
    let lines = wrap_measured_pieces(&pieces, width, false);
    let natural_lines = lines.len();
    match max_lines {
        Some(cap) if natural_lines > cap => {
            let tail: Vec<WrapPiece> = lines[cap - 1..].iter().flatten().cloned().collect();
            let mut drawn: Vec<String> = lines[..cap - 1].iter().map(|l| pieces_to_text(l)).collect();
            drawn.push(
                truncate_by(&pieces_to_text(&tail), width, true, |t| Ok::<_, std::convert::Infallible>(measure(t)))
                    .unwrap_or_else(|never| match never {}),
            );
            FittedText { lines: drawn, natural_lines, cut: true }
        }
        _ => FittedText { lines: lines.iter().map(|l| pieces_to_text(l)).collect(), natural_lines, cut: false },
    }
}

/// Vega's `labelLimit` truncation (vega-scenegraph's `truncate`): `text` whole when it measures
/// within `limit`, else its longest character prefix whose right-trimmed text plus `…` fits, the
/// bare `…` when none does.
///
/// Not [`fit_lines`] at one line: a label's characters stay as authored (a no-break space is not
/// collapsed to a space) and a label too narrow for a character reads `…`, where wrapped text
/// keeps its first character.
pub fn truncate_label_by<E>(
    text: &str,
    limit: f64,
    measure: impl Fn(&str) -> Result<f64, E>,
) -> Result<String, E> {
    if measure(text)? <= limit {
        return Ok(text.to_string());
    }
    let chars: Vec<char> = text.chars().collect();
    let (mut lo, mut hi, mut best) = (0usize, chars.len(), None::<String>);
    while lo <= hi {
        let mid = (lo + hi) / 2;
        let prefix: String = chars[..mid].iter().collect();
        let candidate = format!("{}\u{2026}", prefix.trim_end());
        if measure(&candidate)? <= limit {
            best = Some(candidate);
            lo = mid + 1;
        } else if mid == 0 {
            break;
        } else {
            hi = mid - 1;
        }
    }
    Ok(best.unwrap_or_else(|| "\u{2026}".to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::convert::Infallible;

    /// Every character is 10 px wide.
    fn fit(text: &str, width: f64, max_lines: Option<usize>) -> FittedText {
        let measure = |t: &str| Ok::<_, Infallible>(t.chars().count() as f64 * 10.0);
        fit_lines(text, width, &measure, max_lines).unwrap()
    }

    #[test]
    fn a_line_limit_caps_the_lines_and_ends_the_last_in_an_ellipsis() {
        let f = fit("alpha beta gamma delta", 100.0, Some(2));
        assert_eq!(f.lines, ["alpha beta", "gamma del…"]);
        assert!(f.cut);
        assert_eq!(f.natural_lines, 3);
    }

    #[test]
    fn no_limit_draws_every_line_and_cuts_nothing() {
        let f = fit("alpha beta gamma delta", 100.0, None);
        assert_eq!(f.lines, ["alpha beta", "gamma", "delta"]);
        assert!(!f.cut);
        assert_eq!(f.natural_lines, 3);
    }

    #[test]
    fn text_that_fits_the_limit_is_not_cut() {
        let f = fit("alpha beta", 100.0, Some(1));
        assert_eq!((f.lines, f.natural_lines, f.cut), (vec!["alpha beta".to_string()], 1, false));
    }

    #[test]
    fn one_line_cuts_the_whole_text_to_the_width() {
        let f = fit("alpha beta gamma", 100.0, Some(1));
        assert_eq!(f.lines, ["alpha bet…"]);
        assert!(f.cut);
    }

    #[test]
    fn whitespace_collapses_and_empty_text_has_no_lines() {
        assert_eq!(fit("  a \n  b ", 100.0, None).lines, ["a b"]);
        let empty = fit(" \t", 100.0, Some(2));
        assert_eq!((empty.lines, empty.natural_lines, empty.cut), (vec![], 0, false));
    }

    #[test]
    fn a_word_wider_than_the_line_breaks_at_a_seam_then_anywhere() {
        assert_eq!(fit("path/to/file", 70.0, None).lines, ["path/", "to/file"]);
        assert_eq!(fit("abcdefghij", 40.0, None).lines, ["abcd", "efgh", "ij"]);
    }

    #[test]
    fn a_measure_error_is_the_error() {
        let measure = |_: &str| Err::<f64, _>("no such face");
        assert_eq!(fit_lines("a b", 10.0, &measure, None), Err("no such face"));
    }

    #[test]
    #[should_panic(expected = "at least 1")]
    fn a_limit_of_zero_lines_is_refused() {
        fit("a", 10.0, Some(0));
    }

    fn label(text: &str, limit: f64) -> String {
        let measure = |t: &str| Ok::<_, Infallible>(t.chars().count() as f64 * 10.0);
        truncate_label_by(text, limit, measure).unwrap()
    }

    #[test]
    fn a_label_keeps_its_characters_whole_when_it_fits_and_cuts_to_an_ellipsis_when_not() {
        assert_eq!(label("1\u{a0}B", 100.0), "1\u{a0}B");
        assert_eq!(label("alpha beta", 70.0), "alpha…");
    }

    #[test]
    fn a_label_with_no_room_for_a_character_is_the_bare_ellipsis() {
        assert_eq!(label("alpha", 5.0), "\u{2026}");
        assert_eq!(fit("alpha", 5.0, Some(1)).lines, ["a"]);
    }
}
