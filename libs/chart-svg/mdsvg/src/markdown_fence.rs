//! Markdown fenced code blocks, which board text keeps out of Jinja
//! (`compile/template/markdown_fence.py`): the one masker compile's
//! template scans and the renderer's prose share.

use std::collections::HashMap;

use crate::pygments_re::is_py_space;

/// `resolve_outside_fences`: `resolve` sees the text with every closed fence
/// replaced by a token, and each token is put back afterwards.
pub fn resolve_outside_fences(
    text: &str,
    resolve: impl FnOnce(&str) -> Result<String, String>,
) -> Result<String, String> {
    let (masked, fences) = mask_fenced_code(text);
    let mut resolved = resolve(&masked)?;
    for (token, fence) in &fences {
        resolved = resolved.replace(token.as_str(), fence);
    }
    Ok(resolved)
}

/// `mask_fenced_code`: every closed fenced block becomes
/// `__DCT_MARKDOWN_FENCE_<n>__`, returned beside its original lines; an
/// unclosed fence stays as written.
pub fn mask_fenced_code(text: &str) -> (String, HashMap<String, String>) {
    let mut fences = HashMap::new();
    let mut result = String::with_capacity(text.len());
    let mut open: Option<(&str, String)> = None;
    for line in splitlines_keepends(text) {
        if let Some((marker, fence_lines)) = &mut open {
            fence_lines.push_str(line);
            if is_fence_close(line, marker) {
                let token = format!("__DCT_MARKDOWN_FENCE_{}__", fences.len());
                result.push_str(&token);
                fences.insert(token, std::mem::take(fence_lines));
                open = None;
            }
            continue;
        }
        if let Some(marker) = fence_marker(line) {
            open = Some((marker, line.to_string()));
            continue;
        }
        result.push_str(line);
    }
    if let Some((_, fence_lines)) = open {
        result.push_str(&fence_lines);
    }
    (result, fences)
}

/// `_MARKDOWN_FENCE_RE`, `^\s*(`{3,}|~{3,})([^\r\n]*)$`: the opening marker
/// of a fence line. Python's `$` forgives one final `\n` only, so a line that
/// ends in `\r\n` or a bare `\r` opens nothing.
fn fence_marker(line: &str) -> Option<&str> {
    let body = line.strip_suffix('\n').unwrap_or(line);
    let trimmed = body.trim_start_matches(is_py_space);
    let marker_char = trimmed.chars().next().filter(|c| matches!(c, '`' | '~'))?;
    let run = trimmed.len() - trimmed.trim_start_matches(marker_char).len();
    (run >= 3 && !trimmed[run..].contains(['\r', '\n'])).then(|| &trimmed[..run])
}

/// `_is_markdown_fence_close`, `^\s*{c}{min,}\s*$`: a line of nothing but
/// at least `opening_marker`'s count of its own character.
fn is_fence_close(line: &str, opening_marker: &str) -> bool {
    let body = line.strip_suffix('\n').unwrap_or(line);
    let closing = body.trim_matches(is_py_space);
    let marker_char = opening_marker
        .chars()
        .next()
        .expect("a marker is non-empty");
    !closing.is_empty()
        && closing.chars().count() >= opening_marker.chars().count()
        && closing.chars().all(|c| c == marker_char)
}

/// `str.splitlines(keepends=True)`: `\r\n` is one ending, and every other
/// separator Python splits on is one too.
fn splitlines_keepends(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((i, ch)) = chars.next() {
        let end = match ch {
            '\r' => match chars.peek() {
                Some(&(j, '\n')) => {
                    chars.next();
                    j + 1
                }
                _ => i + 1,
            },
            '\n' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}'
            | '\u{2029}' => i + ch.len_utf8(),
            _ => continue,
        };
        lines.push(&text[start..end]);
        start = end;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_closed_fence_is_masked_and_its_neighbors_kept() {
        let text = "a {{ queries.x }}\n```\n{{ queries.y }}\n```\nb\n";
        let (masked, fences) = mask_fenced_code(text);
        assert_eq!(masked, "a {{ queries.x }}\n__DCT_MARKDOWN_FENCE_0__b\n");
        assert_eq!(
            fences["__DCT_MARKDOWN_FENCE_0__"],
            "```\n{{ queries.y }}\n```\n"
        );
    }

    #[test]
    fn an_unclosed_fence_stays_as_written() {
        let text = "```\nstill {{ open }}\n";
        let (masked, fences) = mask_fenced_code(text);
        assert_eq!(masked, text);
        assert!(fences.is_empty());
    }

    #[test]
    fn a_fence_closes_only_on_a_line_of_its_own_marker_at_least_as_long() {
        let text = "~~~~\n~~~\n```\n~~~~~ \nafter\n";
        assert_eq!(mask_fenced_code(text).0, "__DCT_MARKDOWN_FENCE_0__after\n");
        assert!(is_fence_close("  ````  \n", "```"));
        assert!(!is_fence_close("``` python\n", "```"));
        assert!(!is_fence_close("``\n", "```"));
    }

    #[test]
    fn markers_are_three_or_more_backticks_or_tildes() {
        assert_eq!(fence_marker("```python\n"), Some("```"));
        assert_eq!(fence_marker("  ~~~~\n"), Some("~~~~"));
        assert_eq!(fence_marker("plain text\n"), None);
        assert_eq!(fence_marker("``\n"), None);
    }

    #[test]
    fn a_crlf_opening_fence_opens_nothing_but_a_crlf_line_can_close() {
        let text = "```\r\nbody\r\n```\r\n";
        assert_eq!(mask_fenced_code(text).0, text);
        assert!(is_fence_close("```\r\n", "```"));
    }

    #[test]
    fn lines_split_as_python_does() {
        assert_eq!(
            splitlines_keepends("a\r\nb\rc\u{2028}d"),
            ["a\r\n", "b\r", "c\u{2028}", "d"]
        );
    }

    #[test]
    fn resolution_never_sees_a_fence() {
        let text = "{{ x }}\n```\n{{ x }}\n```\n";
        let out = resolve_outside_fences(text, |masked| Ok(masked.replace("{{ x }}", "hi")));
        assert_eq!(out.unwrap(), "hi\n```\n{{ x }}\n```\n");
    }
}
