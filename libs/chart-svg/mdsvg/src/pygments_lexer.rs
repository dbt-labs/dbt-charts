//! `pygments.lexer.RegexLexer.get_tokens_unprocessed` (Pygments 2.21.0,
//! `lexer.py:700-760`) over the rule tables in `pygments_lexers.rs`, for the
//! lexers whose rules are plain regexes: SQL, Python and Bash.
//!
//! Each rule is one row of the lexer's processed `_tokens`, regex verbatim;
//! [`crate::pygments_re`] gives it Python's `re.match(text, pos)` meaning.
//! The YAML lexer is an `ExtendedRegexLexer` with callbacks and lives in
//! `pygments_yaml.rs`; it shares the state-stack transition below.

use std::collections::HashMap;

use once_cell::sync::Lazy;

use crate::pygments_lexers::{self as tables, Act, Grp, LexerDef, Next, Rule};
use crate::pygments_re::{is_py_word, Flags, Pattern};
use d3rs::py_str::is_py_space;

/// A token type as its dotted `str()` path under `Token`
/// (`pygments/token.py`): `Number = Literal.Number`, `Whitespace =
/// Text.Whitespace`.
pub type Tok = &'static str;

/// `(token type, start byte, end byte)` of a token's text in the input.
pub type Span = (Tok, usize, usize);

pub const WHITESPACE: Tok = "Token.Text.Whitespace";
pub const ERROR: Tok = "Token.Error";

/// `lexer.py:720-743`: a tuple pushes; `'#pop:n'` pops `n`, or everything
/// above `root` when the stack is not deeper than `n`.
pub fn transition(stack: &mut Vec<&'static str>, next: &Next) {
    match *next {
        Next::Stay => {}
        Next::Push(states) => stack.extend_from_slice(states),
        Next::Pop(n) if n >= stack.len() => stack.truncate(1),
        Next::Pop(n) => stack.truncate(stack.len() - n),
    }
}

/// `shell.py:68`, `<<-?\s*(\'?)\\?(\w+)[\w\W]+?\2`: a backreference to the
/// word, which the `regex` crate lacks. The rule's match is the first
/// `(quote, backslash, word)` choice in `re`'s order (greedy `'?`, `\\?`,
/// `\w+`) whose word recurs after at least one more character. A shorter
/// `\s*` or an absent `-?` leaves a character that no later element accepts
/// (`\s` and `-` are not word characters), so neither backtracks usefully.
const BASH_HEREDOC: &str = r"<<-?\s*(\'?)\\?(\w+)[\w\W]+?\2";

fn bash_heredoc(text: &str, pos: usize) -> Option<usize> {
    let s = &text[pos..];
    let mut i = s.len() - s.strip_prefix("<<")?.len();
    if s[i..].starts_with('-') {
        i += 1;
    }
    i += s[i..]
        .find(|c: char| !is_py_space(c))
        .unwrap_or(s.len() - i);
    for quote in if s[i..].starts_with('\'') {
        &[1, 0][..]
    } else {
        &[0]
    } {
        let j = i + quote;
        for backslash in if s[j..].starts_with('\\') {
            &[1, 0][..]
        } else {
            &[0]
        } {
            let k = j + backslash;
            let run = s[k..].find(|c: char| !is_py_word(c)).unwrap_or(s.len() - k);
            for word_end in s[k..k + run]
                .char_indices()
                .map(|(at, c)| k + at + c.len_utf8())
                .rev()
            {
                let word = &s[k..word_end];
                let mut from = word_end;
                let Some(first) = s[from..].chars().next() else {
                    continue;
                };
                from += first.len_utf8();
                loop {
                    if s[from..].starts_with(word) {
                        return Some(pos + from + word.len());
                    }
                    let Some(c) = s[from..].chars().next() else {
                        break;
                    };
                    from += c.len_utf8();
                }
            }
        }
    }
    None
}

struct Compiled {
    rules: HashMap<&'static str, Vec<(Pattern, &'static Rule)>>,
}

impl Compiled {
    fn new(def: &'static LexerDef) -> Compiled {
        let flags = Flags::from_bits(def.flags);
        let rules = def
            .states
            .iter()
            .map(|state| {
                let rules = state
                    .rules
                    .iter()
                    .map(|rule| {
                        let pattern = if rule.pattern == BASH_HEREDOC {
                            Pattern::custom(bash_heredoc)
                        } else {
                            Pattern::new(rule.pattern, flags)
                        };
                        (pattern, rule)
                    })
                    .collect();
                (state.name, rules)
            })
            .collect();
        Compiled { rules }
    }

    /// The loop of `lexer.py:700-760` over `text`, its spans appended to
    /// `out` shifted by `offset`. `using(this)` re-enters it on a group's
    /// text: that lexing starts at `root` and sees only the group.
    fn run(&self, text: &str, offset: usize, out: &mut Vec<Span>) {
        let (mut pos, mut stack) = (0, vec!["root"]);
        'lex: loop {
            let state = *stack.last().expect("state stack is never empty");
            for (pattern, rule) in &self.rules[state] {
                let Some(hit) = pattern.find(text, pos) else {
                    continue;
                };
                match &rule.act {
                    Act::None => {}
                    Act::Tok(tok) => out.push((tok, offset + pos, offset + hit.end)),
                    Act::Groups(groups) => {
                        for (i, group) in groups.iter().enumerate() {
                            let Some((start, end)) = hit.group(i + 1) else {
                                continue;
                            };
                            match group {
                                Grp::Skip => {}
                                Grp::Tok(tok) if start != end => {
                                    out.push((tok, offset + start, offset + end));
                                }
                                Grp::Tok(_) => {}
                                Grp::This => {
                                    self.run(&text[start..end], offset + start, out);
                                }
                            }
                        }
                    }
                }
                pos = hit.end;
                transition(&mut stack, &rule.next);
                continue 'lex;
            }
            let Some(c) = text[pos..].chars().next() else {
                return;
            };
            if c == '\n' {
                stack = vec!["root"];
                out.push((WHITESPACE, offset + pos, offset + pos + 1));
            } else {
                out.push((ERROR, offset + pos, offset + pos + c.len_utf8()));
            }
            pos += c.len_utf8();
        }
    }
}

/// A ported `RegexLexer`: compiled on first use.
pub struct Lexer(Lazy<Compiled>);

impl Lexer {
    /// `get_tokens_unprocessed` over already-normalized text.
    pub fn tokenize(&self, text: &str) -> Vec<Span> {
        let mut out = Vec::new();
        self.0.run(text, 0, &mut out);
        out
    }
}

static SQL: Lexer = Lexer(Lazy::new(|| Compiled::new(&tables::SQL)));
static PYTHON: Lexer = Lexer(Lazy::new(|| Compiled::new(&tables::PYTHON)));
static BASH: Lexer = Lexer(Lazy::new(|| Compiled::new(&tables::BASH)));

/// The ported lexer for a Pygments lexer class name.
pub fn lexer(class: &str) -> Option<&'static Lexer> {
    match class {
        "SqlLexer" => Some(&SQL),
        "PythonLexer" => Some(&PYTHON),
        "BashLexer" => Some(&BASH),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_heredoc_pattern_is_the_one_in_the_table() {
        assert!(tables::BASH
            .states
            .iter()
            .flat_map(|s| s.rules)
            .any(|r| r.pattern == BASH_HEREDOC));
    }

    #[test]
    fn heredoc_takes_the_first_choice_in_res_order() {
        let end = |t: &str| bash_heredoc(t, 0);
        assert_eq!(end("<<EOF\nx\nEOF\n"), Some(11));
        assert_eq!(end("<<-'EOF'\nx\nEOF"), Some(14));
        assert_eq!(end("<<  \\EOF\nx\nEOF"), Some(14));
        assert_eq!(end("<<EOF\nx"), None);
        assert_eq!(end("<<EOFEOF"), Some(7));
        assert_eq!(end("<<EOF"), None);
        assert_eq!(end("<<ab\n ab"), Some(8));
        assert_eq!(end("<<abc\n ab"), Some(9));
        assert_eq!(end("<<\u{e9}a\n\u{e9}a\u{e9}"), Some(9));
        assert_eq!(end("<< 'a b"), None);
    }
}
