//! Where the host serves boards, so an authored board path becomes the URL its host answers
//! (`board_links.resolve_href`).

use serde::Deserialize;

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

#[cfg(test)]
mod tests {
    use super::*;

    fn at(root: &str, slug: &str, origin: &str) -> LinkContext {
        LinkContext { root: root.into(), current_board_slug: slug.into(), origin: origin.into() }
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
