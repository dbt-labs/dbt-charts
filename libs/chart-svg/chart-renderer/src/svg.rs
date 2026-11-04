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

/// The port of `svg_utils.format_svg_numeric`: integers bare, else the
/// float's own full (non-truncated) representation. `board.rs` uses this at
/// every call site production routes through `format_svg_numeric` (the root
/// svg's `width=`/`height=`, the footer/timestamp positions): a measured value
/// like the footer timestamp's `x`, placed from an unsnapped attribution width,
/// keeps its full precision.
pub fn num_full(v: f64) -> String {
    if v == v.trunc() && v.abs() < 1e12 {
        format!("{}", v as i64)
    } else {
        format!("{v}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
