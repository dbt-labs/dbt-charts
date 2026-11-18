//! Where the host serves boards, so an authored board path becomes the URL its host answers
//! (`board_links.resolve_href`).

use board_model::board::Board;
use board_model::chart::ChartLink;
use board_model::primitives::{LinkOpen, LinkSpec};
use diagnostic::{diag, Diagnostic};
use model_core::Tri;
use serde::Deserialize;

use crate::bound::Href;
use crate::run::RunResult;

#[derive(Debug, Clone, PartialEq, Deserialize)]
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

impl LinkContext {
    /// The shape `board_links.LinkContext` enforces: a root that starts with `/` and does not end
    /// with one (or is empty), an origin with no trailing `/`.
    pub fn check(&self) -> Result<(), String> {
        if !(self.root.is_empty() || self.root.starts_with('/') && !self.root.ends_with('/')) {
            return Err(format!("root {:?} must be empty or start with `/` and not end with one", self.root));
        }
        if self.origin.ends_with('/') {
            return Err(format!("origin {:?} must not end with `/`", self.origin));
        }
        Ok(())
    }

    /// External URLs, `mailto:`, fragments and query-only links pass through; a board path maps
    /// to `{origin}{root}/{slug}` (a root-relative one from the author namespace, a relative one
    /// against the linking board's directory), authored `.yml`/`.md` sugar dropped.
    pub fn resolve(&self, href: &str) -> String {
        if PASSTHROUGH_PREFIXES.iter().any(|p| href.starts_with(p)) {
            return href.to_string();
        }
        let (rest, fragment) = href.split_once('#').unwrap_or((href, ""));
        let (path, query) = rest.split_once('?').unwrap_or((rest, ""));
        let mut resolved = if let Some(root_relative) = path.strip_prefix('/') {
            root_relative.trim_start_matches('/').to_string()
        } else {
            let board_dir = self.current_board_slug.rsplit_once('/').map_or("", |(dir, _)| dir);
            let joined = if board_dir.is_empty() { normpath(path) } else { normpath(&format!("{board_dir}/{path}")) };
            if joined.starts_with("..") { joined.trim_start_matches(['.', '/']).to_string() } else { joined }
        };
        if let Some(stem) = BOARD_SUFFIXES.iter().find_map(|suffix| resolved.strip_suffix(suffix)) {
            resolved = stem.to_string();
        }
        let slug = resolved.trim_end_matches('/');
        let mut url = if !slug.is_empty() {
            format!("{}/{slug}", self.root)
        } else if !self.root.is_empty() {
            format!("{}/", self.root)
        } else {
            "/".to_string()
        };
        if !query.is_empty() {
            url = format!("{url}?{query}");
        }
        if !self.origin.is_empty() {
            url = format!("{}{url}", self.origin);
        }
        if fragment.is_empty() { url } else { format!("{url}#{fragment}") }
    }
}

/// The link the host resolved for the chart `public_id` at run time (`RunResult::automatic_links`),
/// as its template; `None` when it has none.
pub fn automatic<'a>(run: &'a RunResult, public_id: &str) -> Option<&'a str> {
    run.automatic_links.get(public_id).map(String::as_str)
}

/// A chart's `link`: its template and how it opens, the href left to the family to expand
/// (a KPI's is its href as authored, a table's or pie's carries the row's placeholders). A chart that
/// authors no `link:` takes the host's `automatic` one (`automatic`), so every family reads the pair the
/// same way; `link: false` (`ChartLink::Off`) turns both off.
pub fn chart_link<'a>(link: Option<&'a ChartLink>, automatic: Option<&'a str>, board: &Board) -> Result<Option<(&'a str, LinkOpen)>, Diagnostic> {
    match link {
        None => Ok(automatic.map(|template| (template, link_open(None, template, board)))),
        Some(ChartLink::Off) => Ok(None),
        Some(ChartLink::On(spec)) => spec_link(spec, board).map(Some),
    }
}

/// A `link` value as authored, a bare URL or a `{url, open}` block: its template and how it opens.
pub fn spec_link<'a>(spec: &'a LinkSpec, board: &Board) -> Result<(&'a str, LinkOpen), Diagnostic> {
    let (template, own) = match spec {
        LinkSpec::Url(url) => (url.as_str(), None),
        LinkSpec::Block(block) => {
            let Some(url) = block.url.as_deref() else {
                return Err(diag::err_internal("a `link` block reaches bind with no `url`"));
            };
            (url, match block.open { Tri::Value(open) => Some(open), Tri::Unset | Tri::Null => None })
        }
    };
    Ok((template, link_open(own, template, board)))
}

/// `split_link`: the link's own `open`, else a query-only `?var=` template updates the board in
/// place as a page, else the board's `links.open` (a page when the board sets none). It reads the
/// authored template, never an expanded href.
pub fn link_open(own: Option<LinkOpen>, template: &str, board: &Board) -> LinkOpen {
    match own {
        Some(open) => open,
        None if template.starts_with('?') => LinkOpen::Page,
        None => board.links.as_ref().map_or(LinkOpen::Page, |links| links.open),
    }
}

/// An expanded href as the host serves it: resolved in the host's link context when it has one,
/// drawn as authored when it has none (a render with no public URL), its scheme checked.
pub fn resolve(raw: &str, run: &RunResult) -> Result<Href, Diagnostic> {
    match &run.env.link_context {
        Some(context) => Href::new(&context.resolve(raw)),
        None => Href::new(raw),
    }
}

/// Python's `urllib.parse.quote_plus` with the default (empty) `safe` set: the RFC 3986 unreserved
/// bytes pass, a space becomes `+`, every other byte percent-encodes.
pub fn quote_plus(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for byte in text.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => out.push(char::from(byte)),
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(root: &str, slug: &str, origin: &str) -> LinkContext {
        LinkContext { root: root.into(), current_board_slug: slug.into(), origin: origin.into() }
    }

    #[test]
    fn quote_plus_encodes_as_python_does() {
        assert_eq!(quote_plus("a b&c=d/é~_.-"), "a+b%26c%3Dd%2F%C3%A9~_.-");
    }

    #[test]
    fn external_and_query_only_links_pass_through() {
        let ctx = at("/b", "sales/overview", "");
        for href in ["https://x.com/a", "mailto:a@b.c", "#top", "?q=1"] {
            assert_eq!(ctx.resolve(href), href);
        }
    }

    #[test]
    fn a_relative_path_resolves_against_the_linking_boards_directory() {
        let ctx = at("", "zendesk/tickets/list", "");
        assert_eq!(ctx.resolve("../orders/detail.md"), "/zendesk/orders/detail");
        assert_eq!(ctx.resolve("../../../../x"), "/x");
    }

    #[test]
    fn a_context_with_a_malformed_root_or_origin_is_rejected() {
        assert!(at("/b", "a", "https://h.io").check().is_ok());
        assert!(at("b", "a", "").check().is_err());
        assert!(at("/b/", "a", "").check().is_err());
        assert!(at("", "a", "https://h.io/").check().is_err());
    }

    #[test]
    fn a_board_path_lands_under_the_root_and_the_origin() {
        assert_eq!(at("/b", "sales/overview", "").resolve("/ops/health.yml"), "/b/ops/health");
        assert_eq!(at("/b", "sales/overview", "https://h.io").resolve("detail?id=3#x"), "https://h.io/b/sales/detail?id=3#x");
        assert_eq!(at("", "a", "").resolve("/"), "/");
    }
}

/// `_HREF_PLACEHOLDER`'s channels (`\{\{\s*(x|y|color|theta)\s*\}\}`): what a Vega-family mark's link
/// fills from its row.
pub const MARK_CHANNELS: &[&str] = &["x", "y", "color", "theta"];

/// `_build_href_calc_expr`'s template law: each placeholder naming one of `MARK_CHANNELS` becomes
/// `value(channel)` percent-encoded (`_vega_encode_field`); every other byte, a `{{` that opens no
/// placeholder and any other `{{ name }}` included, stays literal. `re.split` retries a failed
/// opener one character later, so `{{ {{ x }}` still fills its inner placeholder.
pub fn expand_channels(template: &str, mut value: impl FnMut(&str) -> Result<String, Diagnostic>) -> Result<String, Diagnostic> {
    let mut out = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(open) = rest.find("{{") {
        match placeholder_at(&rest[open..]) {
            Some((channel, len)) => {
                out.push_str(&rest[..open]);
                out.push_str(&vega_encode_field(&value(channel)?));
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

/// A placeholder naming one of `MARK_CHANNELS` at the start of `s`: the channel and the match's length.
fn placeholder_at(s: &str) -> Option<(&'static str, usize)> {
    let inner = s.strip_prefix("{{")?.trim_start_matches(d3rs::py_str::is_py_space);
    let channel = MARK_CHANNELS.iter().copied().find(|c| inner.starts_with(c))?;
    let tail = inner[channel.len()..].trim_start_matches(d3rs::py_str::is_py_space).strip_prefix("}}")?;
    Some((channel, s.len() - tail.len()))
}

/// `_vega_encode_field`'s replace chain (`click_interactivity.py`): the ASCII delimiters that corrupt
/// a `?col=value` query string, `%` first so no later `%XX` is escaped again. Non-ASCII passes
/// through; a browser encodes it on navigation.
fn vega_encode_field(s: &str) -> String {
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

/// JS `'' + datum[field]`: a missing key is `undefined`, a number prints as JS `String(number)`.
pub fn js_string(value: Option<&serde_json::Value>) -> Result<String, Diagnostic> {
    use serde_json::Value;
    match value {
        None => Ok("undefined".into()),
        Some(Value::Null) => Ok("null".into()),
        Some(Value::Bool(b)) => Ok(b.to_string()),
        Some(Value::String(s)) => Ok(s.clone()),
        Some(Value::Number(n)) => Ok(d3rs::js_math::js_number_string(n.as_f64().ok_or_else(|| diag::err_internal(format!("link value {n}: not an f64")))?)),
        Some(other) => Err(diag::err_internal(format!("link value {other}: not a scalar"))),
    }
}

/// `selected::setter`: `?name={{ channel }}` and nothing else, the one link shape that sets a board
/// variable from the mark a reader clicks.
pub fn sets_variable(template: &str) -> bool {
    let Some((name, rest)) = template.strip_prefix('?').and_then(|t| t.split_once('=')) else { return false };
    let channel = rest.strip_prefix("{{").and_then(|r| r.strip_suffix("}}")).map(str::trim);
    !name.is_empty() && !name.contains(['=', '&', '#', '{', '}']) && channel.is_some_and(|c| MARK_CHANNELS.contains(&c))
}

#[cfg(test)]
mod channel_tests {
    use super::*;

    fn expand(template: &str) -> String {
        expand_channels(template, |channel| Ok(format!("<{channel} & co>"))).unwrap()
    }

    #[test]
    fn a_failed_opener_is_retried_one_character_later() {
        assert_eq!(expand("/a/{{ {{ color }}"), "/a/{{ <color%20%26%20co>");
    }

    #[test]
    fn a_placeholder_naming_no_channel_stays_literal() {
        assert_eq!(expand("/a/{{ label }}?v={{theta}}"), "/a/{{ label }}?v=<theta%20%26%20co>");
        assert_eq!(expand("/a/{{ label }}"), "/a/{{ label }}");
    }

    #[test]
    fn only_a_lone_query_placeholder_sets_a_variable() {
        assert!(sets_variable("?fruit={{ color }}"));
        assert!(!sets_variable("?fruit={{ color }}&b=1"));
        assert!(!sets_variable("?fruit={{ label }}"));
        assert!(!sets_variable("/f?fruit={{ color }}"));
        assert!(!sets_variable("?={{ color }}"));
    }
}
