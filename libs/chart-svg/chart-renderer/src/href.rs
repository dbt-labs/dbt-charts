//! Chart-mark `link` templates (`ResolvedChart.link`): the per-mark href law
//! production's `features/click_interactivity.py` compiles into a Vega
//! calculate expression, shared by every family that paints `<a href>` around
//! its marks. Vega evaluates that expression per datum, so each substitution
//! below is JS `'' + datum[field]` followed by `_vega_encode_field`'s chain.

use mdsvg::pygments_re::is_py_space;
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// `board_links.LinkContext`: where the host serves boards, so an authored
/// board path becomes the URL its host answers.
#[derive(Deserialize, Serialize, Clone)]
pub struct LinkContext {
    pub root: String,
    pub current_board_slug: String,
    pub origin: String,
}

const PASSTHROUGH_PREFIXES: &[&str] =
    &["http://", "https://", "mailto:", "#", "?", "command:", "cursor://", "vscode://", "file://"];
const BOARD_SUFFIXES: &[&str] = &[".yml", ".yaml", ".md", ".markdown"];

/// Python's `posixpath.normpath` for a relative path.
fn normpath(path: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/') {
        match part {
            "" | "." => {}
            ".." if parts.last().is_some_and(|last| *last != "..") => {
                parts.pop();
            }
            other => parts.push(other),
        }
    }
    if parts.is_empty() { ".".to_string() } else { parts.join("/") }
}

/// `board_links.resolve_href`: external URLs, `mailto:`, fragments and
/// query-only links pass through; a board path maps to `{origin}{root}/{slug}`
/// (a root-relative one from the author namespace, a relative one against the
/// linking board's directory), authored `.yml`/`.md` sugar dropped.
pub(crate) fn resolve_href(href: &str, ctx: &LinkContext) -> String {
    if PASSTHROUGH_PREFIXES.iter().any(|p| href.starts_with(p)) {
        return href.to_string();
    }
    let (rest, fragment) = href.split_once('#').unwrap_or((href, ""));
    let (path, query) = rest.split_once('?').unwrap_or((rest, ""));
    let mut resolved = if let Some(root_relative) = path.strip_prefix('/') {
        root_relative.trim_start_matches('/').to_string()
    } else {
        let board_dir = ctx.current_board_slug.rsplit_once('/').map_or("", |(dir, _)| dir);
        let joined = if board_dir.is_empty() { normpath(path) } else { normpath(&format!("{board_dir}/{path}")) };
        if joined.starts_with("..") { joined.trim_start_matches(['.', '/']).to_string() } else { joined }
    };
    if let Some(stem) = BOARD_SUFFIXES.iter().find_map(|suffix| resolved.strip_suffix(suffix)) {
        resolved = stem.to_string();
    }
    let slug = resolved.trim_end_matches('/');
    let mut url = if !slug.is_empty() {
        format!("{}/{slug}", ctx.root)
    } else if !ctx.root.is_empty() {
        format!("{}/", ctx.root)
    } else {
        "/".to_string()
    };
    if !query.is_empty() {
        url = format!("{url}?{query}");
    }
    if !ctx.origin.is_empty() {
        url = format!("{}{url}", ctx.origin);
    }
    if fragment.is_empty() { url } else { format!("{url}#{fragment}") }
}

/// `_vega_encode_field`'s replace chain (`click_interactivity.py:67-94`): the
/// ASCII delimiters that corrupt a `?col=value` query string, percent-encoded
/// with `%` first so no later `%XX` output is re-escaped. Non-ASCII passes
/// through — browsers encode it on navigation.
pub(crate) fn vega_encode_field(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '%' => out.push_str("%25"),
            ' ' => out.push_str("%20"),
            '&' => out.push_str("%26"),
            '=' => out.push_str("%3D"),
            '+' => out.push_str("%2B"),
            '#' => out.push_str("%23"),
            '?' => out.push_str("%3F"),
            '/' => out.push_str("%2F"),
            other => out.push(other),
        }
    }
    out
}

/// JS `'' + value` for a datum field. A missing key is `undefined`; a number
/// prints in JS decimal notation (`197199.0` is `197199`), so one outside
/// `[1e-6, 1e21)`, where JS switches to exponent form, refuses by name, as
/// does an array or object.
pub(crate) fn js_string(value: Option<&Value>) -> Result<String, String> {
    match value {
        None => Ok("undefined".into()),
        Some(Value::Null) => Ok("null".into()),
        Some(Value::Bool(b)) => Ok(b.to_string()),
        Some(Value::String(s)) => Ok(s.clone()),
        Some(Value::Number(n)) => {
            let f = n.as_f64().ok_or_else(|| format!("link value {n}: not an f64"))?;
            if f == 0.0 {
                Ok("0".into())
            } else if (1e-6..1e21).contains(&f.abs()) {
                Ok(f.to_string())
            } else {
                Err(format!("link value {n}: JS prints it in exponent notation, not supported"))
            }
        }
        Some(other) => Err(format!("link value {other}: not a scalar")),
    }
}

/// `_HREF_PLACEHOLDER` (`\{\{\s*(x|y|color|theta)\s*\}\}`) anchored at the start
/// of `s`, over `channels`: the channel and the match's byte length.
fn placeholder_at<'c>(s: &str, channels: &[&'c str]) -> Option<(&'c str, usize)> {
    let inner = s.strip_prefix("{{")?.trim_start_matches(is_py_space);
    let channel = channels.iter().copied().find(|c| inner.starts_with(c))?;
    let tail = inner[channel.len()..].trim_start_matches(is_py_space).strip_prefix("}}")?;
    Some((channel, s.len() - tail.len()))
}

/// The Vega-Lite families' placeholder set.
const VEGA_LINK_CHANNELS: &[&str] = &["x", "y", "color", "theta"];

/// `_build_href_calc_expr`'s template law: every `_HREF_PLACEHOLDER` match is
/// replaced by `channel_value(channel)` percent-encoded; every other byte,
/// including a `{{` that opens no match and any non-channel `{{ name }}`, is
/// literal. `re.split` retries a failed opener one character later, so
/// `{{ {{ x }}` still substitutes its inner placeholder.
pub(crate) fn resolve_channel_href(
    template: &str,
    channel_value: impl FnMut(&str) -> Result<String, String>,
) -> Result<String, String> {
    resolve_channel_href_in(template, VEGA_LINK_CHANNELS, channel_value)
}

/// `resolve_channel_href` over a family's own placeholder set.
pub(crate) fn resolve_channel_href_in(
    template: &str,
    channels: &[&str],
    mut channel_value: impl FnMut(&str) -> Result<String, String>,
) -> Result<String, String> {
    let mut out = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(open) = rest.find("{{") {
        match placeholder_at(&rest[open..], channels) {
            Some((channel, len)) => {
                out.push_str(&rest[..open]);
                out.push_str(&vega_encode_field(&channel_value(channel)?));
                rest = &rest[open + len..];
            }
            None => {
                out.push_str(&rest[..=open]);
                rest = &rest[open + 1..];
            }
        }
    }
    out.push_str(rest);
    Ok(out)
}

/// `links.py::link_scheme`: the scheme a browser navigates `href` with, `None`
/// when relative. WHATWG parsing strips leading C0 controls and spaces
/// (U+0000-U+0020) and removes tab, LF and CR anywhere before the scheme is
/// read, and folds case (`links.py:34`, `_SCHEME_RE` `[a-z][a-z0-9+.\-]*(?=:)`).
pub(crate) fn link_scheme(href: &str) -> Option<String> {
    let normalized: String = href
        .trim_start_matches(|c: char| c <= ' ')
        .chars()
        .filter(|c| !matches!(c, '\t' | '\n' | '\r'))
        .collect::<String>()
        .to_lowercase();
    let mut chars = normalized.char_indices();
    if !chars.next().is_some_and(|(_, c)| c.is_ascii_lowercase()) {
        return None;
    }
    let end = chars
        .find(|&(_, c)| {
            !(c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '+' | '.' | '-'))
        })
        .map_or(normalized.len(), |(i, _)| i);
    normalized[end..]
        .starts_with(':')
        .then(|| normalized[..end].to_string())
}

/// `svg_utils.checked_href` (`svg_utils.py:80-95`): `href` unchanged when it has
/// no scheme or an `http`, `https` or `mailto` one (`links.py:24`), else the
/// render-time `ERR-LINK-SCHEME-UNSAFE-AT-RENDER` refusal. Every href sink
/// re-validates here, whatever compile-time validation already saw.
pub(crate) fn checked_href(href: &str) -> Result<&str, String> {
    match link_scheme(href) {
        Some(scheme) if !matches!(scheme.as_str(), "http" | "https" | "mailto") => Err(format!(
            "ERR-LINK-SCHEME-UNSAFE-AT-RENDER: rendered link {href:?} uses scheme {scheme:?}; \
             allowed: board paths (/, ./, ?, #), http, https, mailto"
        )),
        _ => Ok(href),
    }
}

/// `checked_href` with the registered diagnostic: the refusal a table's cell,
/// row and header links raise, naming the href and its scheme.
pub(crate) fn checked_href_diagnostic(href: &str) -> Result<&str, crate::diagnostic::Diagnostic> {
    checked_href(href).map_err(|_| {
        crate::diagnostic::Diagnostic::new(
            &crate::codes::ERR_LINK_SCHEME_UNSAFE_AT_RENDER,
            [
                ("href", serde_json::json!(href)),
                ("scheme", serde_json::json!(link_scheme(href).unwrap_or_default())),
            ],
        )
    })
}

/// Vega's href mark structure: the `<a>` sits inside the `.role-mark` group and
/// wraps only the datum's first drawn element — a stroke-only antialiasing
/// overlay that follows is the same datum, never a second linked mark.
pub(crate) fn anchor_open(href: Option<&str>) -> String {
    href.map_or_else(String::new, |h| format!("<a href=\"{}\">", crate::svg::xml_escape_attr(h)))
}

pub(crate) fn anchor_close(href: Option<&str>) -> &'static str {
    if href.is_some() { "</a>" } else { "" }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Each of the eight characters maps to its own escape, `%` is escaped
    /// first (a later `%XX` is never re-escaped), and non-ASCII passes.
    #[test]
    fn the_encoder_escapes_exactly_the_eight_delimiters() {
        for (raw, escaped) in [
            ("%", "%25"),
            (" ", "%20"),
            ("&", "%26"),
            ("=", "%3D"),
            ("+", "%2B"),
            ("#", "%23"),
            ("?", "%3F"),
            ("/", "%2F"),
        ] {
            assert_eq!(vega_encode_field(&format!("a{raw}b")), format!("a{escaped}b"));
        }
        assert_eq!(vega_encode_field("%20"), "%2520");
        assert_eq!(vega_encode_field("café-ü_.~:;,!*'()[]@$"), "café-ü_.~:;,!*'()[]@$");
    }

    /// `checked_href` refuses exactly the schemes a browser would navigate
    /// that are not http, https or mailto, however the scheme is disguised.
    #[test]
    fn checked_href_reads_the_scheme_the_way_a_browser_does() {
        for bad in [
            "javascript:alert(1)",
            "JAVASCRIPT:alert(1)",
            " \u{1}\tjava\nscript:x",
            "ja\rva\tscript:x",
            "data:text/html,x",
            "a+b.c-d:x",
        ] {
            assert!(checked_href(bad).is_err(), "{bad:?}");
        }
        for good in [
            "https://x.io",
            "HTTP://x.io",
            " mailto:a@b.c",
            "/a:b",
            "a/b:c",
            "?q=a:b",
            "#a:b",
            "",
            "1a:b",
            ":x",
            "rel/path",
        ] {
            assert_eq!(checked_href(good), Ok(good), "{good:?}");
        }
    }

    #[test]
    fn js_coercion_matches_string_concatenation() {
        for (value, expected) in [
            (None, "undefined"),
            (Some(json!(null)), "null"),
            (Some(json!(true)), "true"),
            (Some(json!("a b")), "a b"),
            (Some(json!(197199.0)), "197199"),
            (Some(json!(-0.0)), "0"),
            (Some(json!(0.5)), "0.5"),
            (Some(json!(12)), "12"),
            (Some(json!(1e-6)), "0.000001"),
            (Some(json!(1e20)), "100000000000000000000"),
        ] {
            assert_eq!(js_string(value.as_ref()).unwrap(), expected, "{value:?}");
        }
    }

    #[test]
    fn js_coercion_refuses_what_it_does_not_model() {
        for value in [json!(1e21), json!(1e-7), json!([1]), json!({"a": 1})] {
            assert!(js_string(Some(&value)).unwrap_err().starts_with("link value"), "{value}");
        }
    }

    fn resolve(template: &str) -> String {
        resolve_channel_href(template, |channel| Ok(format!("<{channel}>"))).unwrap()
    }

    /// `\s*` on both sides of the channel takes Python's whitespace set,
    /// including U+001F and U+00A0.
    #[test]
    fn a_placeholder_tolerates_any_python_whitespace() {
        assert_eq!(resolve("{{x}}{{ y }}{{\tcolor\n}}{{\u{1f}theta\u{a0}}}"), "<x><y><color><theta>");
    }

    #[test]
    fn a_span_that_matches_no_channel_is_literal() {
        for template in ["{{ X }}", "{{ xy }}", "{{ category }}", "{{ x", "{{", "}}", "{ x }", "{{ x } }"] {
            assert_eq!(resolve(template), template);
        }
    }

    #[test]
    fn a_failed_opener_never_hides_a_later_match() {
        assert_eq!(resolve("a={{ {{ x }}"), "a={{ <x>");
        assert_eq!(resolve("{{{ x }}}"), "{<x>}");
        assert_eq!(resolve("{{ x }}&w={{"), "<x>&w={{");
        assert_eq!(resolve("{{ x }}{{ y }}"), "<x><y>");
    }

    #[test]
    fn a_substituted_value_is_encoded_and_a_refusal_propagates() {
        assert_eq!(
            resolve_channel_href("?a={{ x }}", |_| Ok("A&B C".into())).unwrap(),
            "?a=A%26B%20C"
        );
        assert_eq!(resolve_channel_href("?a={{ y }}", |c| Err(c.into())).unwrap_err(), "y");
    }
}

#[cfg(test)]
mod resolve_href_tests {
    use super::*;

    fn ctx(root: &str, slug: &str, origin: &str) -> LinkContext {
        LinkContext { root: root.into(), current_board_slug: slug.into(), origin: origin.into() }
    }

    #[test]
    fn external_query_and_fragment_links_pass_through() {
        let c = ctx("/boards", "a/b", "");
        for href in ["https://x.io/p", "mailto:a@b.c", "#top", "?status=open"] {
            assert_eq!(resolve_href(href, &c), href);
        }
    }

    #[test]
    fn a_root_relative_path_maps_under_the_host_root_with_sugar_dropped() {
        let c = ctx("/org/proj/d", "a/b", "https://dbtcharts.com");
        assert_eq!(
            resolve_href("/zendesk/tickets/list.yml?status=open#t", &c),
            "https://dbtcharts.com/org/proj/d/zendesk/tickets/list?status=open#t"
        );
    }

    #[test]
    fn a_relative_path_resolves_against_the_linking_boards_directory() {
        let c = ctx("", "zendesk/tickets/list", "");
        assert_eq!(resolve_href("../orders/detail.md", &c), "/zendesk/orders/detail");
        assert_eq!(resolve_href("../../../../x", &c), "/x");
    }
}
