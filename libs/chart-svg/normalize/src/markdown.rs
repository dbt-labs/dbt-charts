//! A markdown board read into the board's [`Node`], as `compile_file`'s markdown branch reads it.

use diagnostic::{diag, Diagnostic};
use model_core::Tri;
use project::ProjectPath;
use yaml::Node;

/// Whether `path` is read as markdown (`ProjectPath.is_markdown`).
pub(crate) fn is_markdown(path: &ProjectPath) -> bool {
    let p = path.as_str().to_ascii_lowercase();
    p.ends_with(".md") || p.ends_with(".markdown")
}

/// The board node of the markdown file `text` at `path`. `named` is whether the user named this
/// file, which lets a body with a chart fence stand without a `board:` key.
pub(crate) fn read(text: &str, path: &ProjectPath, named: bool) -> Result<Node, Vec<Diagnostic>> {
    let in_boards = path.as_str().split('/').any(|part| part == "charts");
    if !yaml::markdown::is_markdown_board_content(text, in_boards, named) {
        return Err(vec![diag::err_markdown_not_board()]);
    }
    yaml::markdown::parse_markdown_board(text)
        .map(|board| board.board)
        .map_err(|errors| errors.into_iter().map(parse_error).collect())
}

/// A refusal the front-end words as a validation error is Python's `ValueError`, which is
/// `ERR-MARKDOWN-PARSE`; a YAML syntax error keeps its own code.
fn parse_error(d: Diagnostic) -> Diagnostic {
    match (d.code, d.params.get("pydantic_msg")) {
        ("ERR-VALIDATION-FIELD", Some(serde_json::Value::String(msg))) => {
            diag::err_markdown_parse(msg.as_str())
        }
        _ => d,
    }
}

/// `style.text.column.max_number: 1` for a leaf the merged board leaves unset: the lowest-priority
/// style layer, beneath `meta.yml`, `extends:` and the board's own style (`with_document_style`).
pub(crate) fn with_document_style(patch: &mut crate::extends::Patch) {
    let mut style = match std::mem::take(&mut patch.body.style) {
        Tri::Value(style) => style,
        Tri::Unset => Box::default(),
        other => {
            patch.body.style = other;
            return;
        }
    };
    if matches!(style.text.column.max_number, Tri::Unset) {
        style.text.column.max_number = Tri::Value(1);
    }
    patch.body.style = Tri::Value(style);
}

/// Warnings less the ones a markdown body has no way to act on (`_without_markdown_section_headings`):
/// chart embeds split a body into rows, and markdown has no per-row `title:`, so a heading after an
/// embed is document structure. Only one opening the body of an untitled board can move, to the
/// front matter's `board.title`.
pub(crate) fn without_section_heading_warnings(diagnostics: &mut Vec<Diagnostic>, untitled: bool) {
    diagnostics.retain(|d| {
        let at = d.path.as_ref().map(ToString::to_string);
        d.code != "WARN-BODY-HEADING-NO-TITLE"
            || at.as_deref() == Some("text")
            || (untitled && at.as_deref() == Some("rows.0.text"))
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use diagnostic::ModelPath;

    fn heading(at: &[&str]) -> Diagnostic {
        diag::warn_body_heading_no_title("H")
            .at_path(ModelPath(at.iter().map(|s| s.to_string()).collect()))
    }

    #[test]
    fn only_a_heading_that_can_move_to_the_title_survives() {
        let all = || {
            vec![
                heading(&["text"]),
                heading(&["rows", "0", "text"]),
                heading(&["rows", "2", "text"]),
            ]
        };
        let mut titled = all();
        without_section_heading_warnings(&mut titled, false);
        assert_eq!(titled.len(), 1);
        let mut untitled = all();
        without_section_heading_warnings(&mut untitled, true);
        assert_eq!(untitled.len(), 2);
    }
}
