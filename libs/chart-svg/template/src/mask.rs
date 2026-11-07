//! Fenced markdown code is not Jinja. Masking blanks it to spaces of the same
//! byte length, so a diagnostic's span still indexes the author's text.

/// Every closed fenced block blanked; an unclosed fence stays as written.
pub fn mask_fenced_code(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut at = 0;
    for block in fenced_blocks(text) {
        out.push_str(&text[at..block.start]);
        blank_into(&mut out, &text[block.clone()]);
        at = block.end;
    }
    out.push_str(&text[at..]);
    out
}

/// The byte range of every closed fenced block, from its opening line through its
/// closing line and that line's newline. An unclosed fence is no block.
pub fn fenced_blocks(text: &str) -> Vec<std::ops::Range<usize>> {
    let mut blocks = Vec::new();
    let mut open: Option<(char, usize, usize)> = None;
    let mut at = 0;
    for line in text.split_inclusive('\n') {
        let start = at;
        at += line.len();
        match open {
            Some((ch, width, from)) => {
                if closes(line, ch, width) {
                    blocks.push(from..at);
                    open = None;
                }
            }
            None => open = opens(line).map(|(ch, width)| (ch, width, start)),
        }
    }
    blocks
}

fn blank_into(out: &mut String, fence: &str) {
    for c in fence.chars() {
        if c == '\n' {
            out.push('\n');
        } else {
            out.extend(std::iter::repeat_n(' ', c.len_utf8()));
        }
    }
}

/// `^\s*(`{3,}|~{3,})[^\r\n]*$`: the fence character and run width.
fn opens(line: &str) -> Option<(char, usize)> {
    let body = line.strip_suffix('\n').unwrap_or(line).trim_start();
    let ch = body.chars().next().filter(|c| matches!(c, '`' | '~'))?;
    let rest = body.trim_start_matches(ch);
    let width = body.len() - rest.len();
    (width >= 3 && !rest.contains(['\r', '\n'])).then_some((ch, width))
}

/// `^\s*{ch}{width,}\s*$`
fn closes(line: &str, ch: char, width: usize) -> bool {
    let body = line.trim();
    body.chars().count() >= width && body.chars().all(|c| c == ch)
}

#[cfg(test)]
mod tests {
    use super::mask_fenced_code;

    #[test]
    fn blanking_keeps_byte_length_and_newlines_for_every_fence_shape() {
        for text in [
            "é\n```\n{{ é }}\n```\nz",
            "~~~~\r\n{{ a }}\r\n~~~~\r\n{{ b }}",
            "```\n{{ unclosed",
        ] {
            let masked = mask_fenced_code(text);
            assert_eq!(masked.len(), text.len());
            assert_eq!(masked.matches('\n').count(), text.matches('\n').count());
        }
        assert_eq!(
            mask_fenced_code("```\n{{ a }}\n```\n{{ b }}").trim(),
            "{{ b }}"
        );
        assert!(mask_fenced_code("```\n{{ unclosed").contains("{{ unclosed"));
    }
}
