//! Tiny SVG emission helpers shared by all families. Escaping is `print`'s.

/// Return `name` when it can follow `data-var-`/`data-dbt-` as an attribute
/// name, mirroring production's `svg_utils.attr_name`: a name with whitespace,
/// quotes, `=`, `<`, `>`, `/`, or a control character splits or closes the
/// tag, and no rewrite of it is safe.
pub fn attr_name(name: &str) -> Result<&str, String> {
    let unsafe_char = |c: char| {
        c.is_whitespace() || matches!(c, '"' | '\'' | '=' | '<' | '>' | '/' | '\x00'..='\x1f' | '\x7f')
    };
    if name.is_empty() || name.chars().any(unsafe_char) {
        return Err(format!(
            "ERR-INPUT-INVALID: not a valid SVG attribute name: {name:?}"
        ));
    }
    Ok(name)
}

/// Trim a float for SVG attributes: integers bare, else up to 2 decimals.
pub fn num(v: f64) -> String {
    if v == v.trunc() && v.abs() < 1e12 {
        format!("{}", v as i64)
    } else {
        let mut s = format!("{v:.2}");
        s.truncate(s.trim_end_matches('0').trim_end_matches('.').len());
        s
    }
}

/// The port of `svg_utils.format_svg_numeric`: integers bare, else the
/// float's own full (non-truncated) representation. `board.rs` uses this at
/// every call site production routes through `format_svg_numeric` (the root
/// svg's `width=`/`height=`, the footer/timestamp positions) — never `num()`,
/// whose 2-decimal cap loses real precision on a measured value like the
/// footer timestamp's `x` (`board.rs`), placed from an unsnapped attribution
/// width. The attribution runs themselves are snapped to whole pixels and
/// would survive `num()`; the timestamp is the value on this path that
/// does not. Chart families use `num()` because their own emitted values
/// are already rounded at the source; these board-level values are not.
///
/// Also the writer of a Vega text mark's anchor, which `vega-scenegraph`
/// concatenates into `translate(x,y)` as the number's own JavaScript string;
/// the two agree on every value a layout produces.
pub fn num_full(v: f64) -> String {
    if v == v.trunc() && v.abs() < 1e12 {
        format!("{}", v as i64)
    } else {
        format!("{v}")
    }
}

/// Python's own bare `f"{value}"` float formatting, which is
/// `d3rs::py_float::float_repr` under the name `laws-board.md` documents:
/// unlike `format_svg_numeric`, a whole number keeps its `.0` (`372.0`, never
/// `372`). `board.rs` uses this
/// at every call site that interpolates a raw float WITHOUT going through
/// `format_svg_numeric` or `px()` first — `boards.py`'s `_bg_rect` width/
/// height, the root svg's `viewBox`, and `_wrap_rendered_chart_svg`'s
/// `data-chart-width`/`data-chart-height`. A site that runs its float through
/// `px()` first (an `int` in Python) needs neither formatter — `num_full`'s
/// integer branch already reproduces bare `int` interpolation.
pub fn py_float_str(v: f64) -> String {
    d3rs::py_float::float_repr(v)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn py_float_str_keeps_the_decimal_python_never_drops() {
        assert_eq!(py_float_str(372.0), "372.0");
        assert_eq!(py_float_str(0.0), "0.0");
        assert_eq!(py_float_str(179.7), "179.7");
        assert_eq!(py_float_str(-16.0), "-16.0");
        assert_eq!(py_float_str(12.0), "12.0");
        assert_eq!(py_float_str(12.5), "12.5");
    }

    #[test]
    fn num_full_still_drops_it_format_svg_numeric_style() {
        assert_eq!(num_full(372.0), "372");
        assert_eq!(num_full(0.06657894736842104), "0.06657894736842104");
    }

    #[test]
    fn attr_name_refuses_names_that_split_or_close_the_tag() {
        assert_eq!(attr_name("region"), Ok("region"));
        assert_eq!(attr_name("café"), Ok("café"));
        for name in ["", "a b", "a=\"b\"", "a<b", "a/b", "a\tb", "a'b"] {
            let err = attr_name(name).unwrap_err();
            assert!(err.starts_with("ERR-INPUT-INVALID"), "{name:?}: {err}");
        }
    }
}
