//! Tiny SVG emission helpers shared by all families.

use std::borrow::Cow;

/// `s` with each character `escape` maps replaced by its reference, in one
/// pass; borrowed when nothing needs escaping.
fn escape_with(s: &str, escape: impl Fn(char) -> Option<&'static str>) -> Cow<'_, str> {
    let Some(first) = s.find(|c| escape(c).is_some()) else {
        return Cow::Borrowed(s);
    };
    let mut out = String::with_capacity(s.len() + 16);
    out.push_str(&s[..first]);
    for c in s[first..].chars() {
        match escape(c) {
            Some(reference) => out.push_str(reference),
            None => out.push(c),
        }
    }
    Cow::Owned(out)
}

fn xml_reference(c: char) -> Option<&'static str> {
    match c {
        '&' => Some("&amp;"),
        '<' => Some("&lt;"),
        '>' => Some("&gt;"),
        _ => None,
    }
}

pub fn xml_escape(s: &str) -> Cow<'_, str> {
    escape_with(s, xml_reference)
}

/// Text content escaped the way production's bare `html.escape(text)` does:
/// `xml_escape` plus both quote characters, and no whitespace references — a
/// text node keeps its literal newline, an attribute value does not.
pub fn html_escape(s: &str) -> Cow<'_, str> {
    escape_with(s, |c| match c {
        '"' => Some("&quot;"),
        '\'' => Some("&#x27;"),
        _ => xml_reference(c),
    })
}

/// The one attribute-value escaper — `mdsvg::utils::escape_xml`, production's
/// `svg_utils.escape_attr` (`html.escape(s, quote=True)`, then newline, CR,
/// and tab as numeric character references). Every authored string that
/// lands in an attribute value goes through it; text content never needs the
/// quotes escaped, so it uses `xml_escape`.
pub use mdsvg::utils::escape_xml as xml_escape_attr;

/// An attribute value as Vega's SVG serializer writes it (vega-scenegraph
/// `src/util/markup.js`, `attrText`): `&`, `<`, `>`, and `"` escaped, tab,
/// newline, and CR as hex references, `'` left bare. A Vega-drawn chart body's
/// `font-family` keeps its single-quoted stack byte-for-byte; every other
/// attribute value, and every Python-drawn one, uses `xml_escape_attr`.
pub fn vega_escape_attr(s: &str) -> Cow<'_, str> {
    escape_with(s, |c| match c {
        '"' => Some("&quot;"),
        '\t' => Some("&#x9;"),
        '\n' => Some("&#xA;"),
        '\r' => Some("&#xD;"),
        _ => xml_reference(c),
    })
}

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
    fn attribute_escape_is_production_escape_attr() {
        assert_eq!(
            xml_escape_attr("x\" onmouseover=\"alert(1)"),
            "x&quot; onmouseover=&quot;alert(1)"
        );
        assert_eq!(xml_escape_attr("'A' <b> & c"), "&#x27;A&#x27; &lt;b&gt; &amp; c");
        assert_eq!(xml_escape_attr("a\nb\rc\td"), "a&#10;b&#13;c&#9;d");
    }

    #[test]
    fn vega_attribute_escape_is_vega_scenegraph_attr_text() {
        assert_eq!(
            vega_escape_attr("'A' \"b\" <c> & d\te\nf\rg"),
            "'A' &quot;b&quot; &lt;c&gt; &amp; d&#x9;e&#xA;f&#xD;g"
        );
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
