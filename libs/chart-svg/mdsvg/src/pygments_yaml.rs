//! `pygments.lexers.data.YamlLexer` (Pygments 2.21.0), rule for rule.
//!
//! An `ExtendedRegexLexer` (`pygments/lexer.py:777-846`): a state stack, one
//! ordered rule list per state, and callbacks that read and write a
//! `YamlLexerContext` (`data.py:19-27`). Each rule below carries its Python
//! regex verbatim, so it audits against `data.py:167-428` line by line.
//!
//! Python's `re` semantics come from [`crate::pygments_re`].

use std::collections::HashMap;

use once_cell::sync::Lazy;

use crate::pygments_lexer::{transition, Span, Tok, ERROR, WHITESPACE};
use crate::pygments_lexers::Next;
use crate::pygments_re::{is_py_space, Flags, Pattern};

const TEXT: Tok = "Token.Text";
const WHITESPACE_ERROR: Tok = "Token.Text.Whitespace.Error";
const COMMENT_SINGLE: Tok = "Token.Comment.Single";
const NAME_TAG: Tok = "Token.Name.Tag";
const NAME_NAMESPACE: Tok = "Token.Name.Namespace";
const NAME_LABEL: Tok = "Token.Name.Label";
const NAME_VARIABLE: Tok = "Token.Name.Variable";
const NAME_CONSTANT: Tok = "Token.Name.Constant";
const NUMBER: Tok = "Token.Literal.Number";
const KEYWORD_TYPE: Tok = "Token.Keyword.Type";
const PUNCTUATION: Tok = "Token.Punctuation";
const PUNCTUATION_INDICATOR: Tok = "Token.Punctuation.Indicator";
const STRING: Tok = "Token.Literal.String";
const STRING_ESCAPE: Tok = "Token.Literal.String.Escape";
const SCALAR_PLAIN: Tok = "Token.Literal.Scalar.Plain";

/// The `YamlLexerContext` fields (`data.py:19-27`) plus the lexer position
/// and state stack (`LexerContext`, `lexer.py:762-774`).
struct Ctx {
    pos: usize,
    stack: Vec<&'static str>,
    indent_stack: Vec<i64>,
    indent: i64,
    next_indent: i64,
    block_scalar_indent: Option<i64>,
}

/// The callback factories at `data.py:43-165`, one variant each.
#[derive(Clone, Copy)]
enum Cb {
    Something(Tok),
    ResetIndent(Tok),
    SaveIndent(bool),
    SetIndent(Tok, bool),
    SetBlockScalarIndent(Tok),
    ParseBlockScalarEmptyLine,
    ParseBlockScalarIndent(Tok),
    ParsePlainScalarIndent(Tok),
}

/// One `bygroups` argument (`lexer.py:385-408`).
#[derive(Clone, Copy)]
enum Group {
    Tok(Tok),
    Cb(Cb),
}

#[derive(Clone, Copy)]
enum Act {
    Tok(Tok),
    Groups(&'static [Group]),
    Cb(Cb),
}

struct Rule {
    pattern: Pattern,
    act: Act,
    next: Next,
}

/// `data.py:408`, `(?::(?!\s)|[^\s:])+`: the one rule with a lookahead inside
/// a repetition, which `(P)(?:X)` cannot express. A `:` continues the run
/// unless whitespace follows it; end of text counts as no whitespace.
fn plain_block_run(text: &str, pos: usize) -> Option<usize> {
    let mut end = pos;
    let mut chars = text[pos..].chars().peekable();
    while let Some(c) = chars.next() {
        let continues = if c == ':' {
            chars.peek().is_none_or(|&n| !is_py_space(n))
        } else {
            !is_py_space(c)
        };
        if !continues {
            break;
        }
        end += c.len_utf8();
    }
    (end > pos).then_some(end)
}

/// `lexer.py:674`: YAML compiles with the `RegexLexer` default, MULTILINE.
const FLAGS: Flags = Flags {
    ignore_case: false,
    multiline: true,
    dot_all: false,
};

fn rule(py: &str, act: Act, next: Next) -> Rule {
    Rule {
        pattern: Pattern::new(py, FLAGS),
        act,
        next,
    }
}

fn push(states: &'static [&'static str]) -> Next {
    Next::Push(states)
}

/// `data.py:167-428`. `include(x)` is a splice at that position, so each
/// included state is a function returning its rules.
fn descriptors() -> Vec<Rule> {
    vec![
        // a full-form tag
        rule(
            r"!<[\w#;/?:@&=+$,.!~*\'()\[\]%-]+>",
            Act::Tok(KEYWORD_TYPE),
            Next::Stay,
        ),
        // a tag in the form '!', '!suffix' or '!handle!suffix'
        rule(
            r"!(?:[\w-]+!)?[\w#;/?:@&=+$,.!~*\'()\[\]%-]*",
            Act::Tok(KEYWORD_TYPE),
            Next::Stay,
        ),
        // an anchor
        rule(r"&[\w-]+", Act::Tok(NAME_LABEL), Next::Stay),
        // an alias
        rule(r"\*[\w-]+", Act::Tok(NAME_VARIABLE), Next::Stay),
    ]
}

fn block_nodes() -> Vec<Rule> {
    vec![
        // implicit key
        rule(
            r":(?=[ ]|$)",
            Act::Cb(Cb::SetIndent(PUNCTUATION_INDICATOR, true)),
            Next::Stay,
        ),
        // literal and folded scalars
        rule(
            r"[|>]",
            Act::Tok(PUNCTUATION_INDICATOR),
            push(&["block-scalar-content", "block-scalar-header"]),
        ),
    ]
}

fn flow_nodes() -> Vec<Rule> {
    vec![
        rule(
            r"\[",
            Act::Tok(PUNCTUATION_INDICATOR),
            push(&["flow-sequence"]),
        ),
        rule(
            r"\{",
            Act::Tok(PUNCTUATION_INDICATOR),
            push(&["flow-mapping"]),
        ),
        rule(r"\'", Act::Tok(STRING), push(&["single-quoted-scalar"])),
        rule(r#"\""#, Act::Tok(STRING), push(&["double-quoted-scalar"])),
    ]
}

fn flow_collection() -> Vec<Rule> {
    let mut rules = vec![
        rule(r"[ ]+", Act::Tok(WHITESPACE), Next::Stay),
        rule(r"\n+", Act::Tok(WHITESPACE), Next::Stay),
        rule(r"#[^\n]*", Act::Tok(COMMENT_SINGLE), Next::Stay),
        rule(r"[?:,]", Act::Tok(PUNCTUATION_INDICATOR), Next::Stay),
    ];
    rules.extend(descriptors());
    rules.extend(flow_nodes());
    rules.push(rule(
        r#"(?=[^\s?:,\[\]{}#&*!|>\'"%@`])"#,
        Act::Cb(Cb::Something(NAME_VARIABLE)),
        push(&["plain-scalar-in-flow-context"]),
    ));
    rules
}

fn quoted_scalar_whitespaces() -> Vec<Rule> {
    vec![
        // leading and trailing whitespaces are ignored
        rule(r"^[ ]+", Act::Tok(WHITESPACE), Next::Stay),
        rule(r"[ ]+$", Act::Tok(WHITESPACE), Next::Stay),
        // line breaks are ignored
        rule(r"\n+", Act::Tok(WHITESPACE), Next::Stay),
        // other whitespaces are a part of the value
        rule(r"[ \xa0]+", Act::Tok(NAME_VARIABLE), Next::Stay),
    ]
}

fn build() -> HashMap<&'static str, Vec<Rule>> {
    use Act::{Cb as C, Groups, Tok as T};
    let mut s: HashMap<&'static str, Vec<Rule>> = HashMap::new();

    // 'root' (data.py:169)
    s.insert(
        "root",
        vec![
            rule(r"[ ]+(?=#|$)", T(WHITESPACE), Next::Stay),
            rule(r"\n+", T(WHITESPACE), Next::Stay),
            rule(r"#[^\n]*", T(COMMENT_SINGLE), Next::Stay),
            rule(
                r"^%YAML(?=[ ]|$)",
                C(Cb::ResetIndent(NAME_TAG)),
                push(&["yaml-directive"]),
            ),
            rule(
                r"^%TAG(?=[ ]|$)",
                C(Cb::ResetIndent(NAME_TAG)),
                push(&["tag-directive"]),
            ),
            rule(
                r"^(?:---|\.\.\.)(?=[ ]|$)",
                C(Cb::ResetIndent(NAME_NAMESPACE)),
                push(&["block-line"]),
            ),
            rule(
                r"[ ]*(?!\s|$)",
                C(Cb::SaveIndent(true)),
                push(&["block-line", "indentation"]),
            ),
        ],
    );

    // 'ignored-line' (data.py:189)
    s.insert(
        "ignored-line",
        vec![
            rule(r"[ ]+(?=#|$)", T(WHITESPACE), Next::Stay),
            rule(r"#[^\n]*", T(COMMENT_SINGLE), Next::Stay),
            rule(r"\n", T(WHITESPACE), Next::Pop(2)),
        ],
    );

    // 'yaml-directive' (data.py:199)
    s.insert(
        "yaml-directive",
        vec![rule(
            r"([ ]+)([0-9]+\.[0-9]+)",
            Groups(&[Group::Tok(WHITESPACE), Group::Tok(NUMBER)]),
            push(&["ignored-line"]),
        )],
    );

    // 'tag-directive' (data.py:206)
    s.insert(
        "tag-directive",
        vec![rule(
            r"([ ]+)(!|![\w-]*!)([ ]+)(!|!?[\w;/?:@&=+$,.!~*\'()\[\]%-]+)",
            Groups(&[
                Group::Tok(WHITESPACE),
                Group::Tok(KEYWORD_TYPE),
                Group::Tok(WHITESPACE),
                Group::Tok(KEYWORD_TYPE),
            ]),
            push(&["ignored-line"]),
        )],
    );

    // 'indentation' (data.py:215)
    s.insert(
        "indentation",
        vec![
            rule(r"[ ]*$", C(Cb::Something(WHITESPACE)), Next::Pop(2)),
            rule(
                r"[ ]+(?=[?:-](?:[ ]|$))",
                C(Cb::SaveIndent(false)),
                Next::Stay,
            ),
            rule(
                r"[?:-](?=[ ]|$)",
                C(Cb::SetIndent(PUNCTUATION_INDICATOR, false)),
                Next::Stay,
            ),
            rule(r"[ ]*", C(Cb::SaveIndent(false)), Next::Pop(1)),
        ],
    );

    // 'block-line' (data.py:227)
    let mut block_line = vec![
        rule(r"[ ]*(?=#|$)", C(Cb::Something(WHITESPACE)), Next::Pop(1)),
        rule(r"[ ]+", T(WHITESPACE), Next::Stay),
        rule(
            r#"([^#,?\[\]{}"'\n]+)(:)(?=[ ]|$)"#,
            Groups(&[
                Group::Tok(NAME_TAG),
                Group::Cb(Cb::SetIndent(PUNCTUATION, true)),
            ]),
            Next::Stay,
        ),
    ];
    block_line.extend(descriptors());
    block_line.extend(block_nodes());
    block_line.extend(flow_nodes());
    block_line.push(rule(
        r#"(?=[^\s?:,\[\]{}#&*!|>\'"%@`-]|[?:-]\S)"#,
        C(Cb::Something(NAME_VARIABLE)),
        push(&["plain-scalar-in-block-context"]),
    ));
    s.insert("block-line", block_line);

    // 'flow-collection' (data.py:282), 'flow-sequence' (302), 'flow-mapping' (310)
    let mut flow_sequence = flow_collection();
    flow_sequence.push(rule(r"\]", T(PUNCTUATION_INDICATOR), Next::Pop(1)));
    s.insert("flow-sequence", flow_sequence);
    let mut flow_mapping = vec![rule(
        r#"([^,:?\[\]{}"'\n]+)(:)(?=[ ]|$)"#,
        Groups(&[Group::Tok(NAME_TAG), Group::Tok(PUNCTUATION)]),
        Next::Stay,
    )];
    flow_mapping.extend(flow_collection());
    flow_mapping.push(rule(r"\}", T(PUNCTUATION_INDICATOR), Next::Pop(1)));
    s.insert("flow-mapping", flow_mapping);

    // 'block-scalar-content' (data.py:321)
    s.insert(
        "block-scalar-content",
        vec![
            rule(r"\n", T(WHITESPACE), Next::Stay),
            rule(r"^[ ]+$", C(Cb::ParseBlockScalarEmptyLine), Next::Stay),
            rule(
                r"^[ ]*",
                C(Cb::ParseBlockScalarIndent(WHITESPACE)),
                Next::Stay,
            ),
            rule(r"[\S\t ]+", T(NAME_CONSTANT), Next::Stay),
        ],
    );

    // 'block-scalar-header' (data.py:334)
    s.insert(
        "block-scalar-header",
        vec![
            rule(
                r"([1-9])?[+-]?(?=[ ]|$)",
                C(Cb::SetBlockScalarIndent(PUNCTUATION_INDICATOR)),
                push(&["ignored-line"]),
            ),
            rule(
                r"[+-]?([1-9])?(?=[ ]|$)",
                C(Cb::SetBlockScalarIndent(PUNCTUATION_INDICATOR)),
                push(&["ignored-line"]),
            ),
        ],
    );

    // 'single-quoted-scalar' (data.py:357)
    let mut single = quoted_scalar_whitespaces();
    single.extend([
        rule(r"\'\'", T(STRING_ESCAPE), Next::Stay),
        rule(r"[^\s\']+", T(STRING), Next::Stay),
        rule(r"\'", T(STRING), Next::Pop(1)),
    ]);
    s.insert("single-quoted-scalar", single);

    // 'double-quoted-scalar' (data.py:369)
    let mut double = quoted_scalar_whitespaces();
    double.extend([
        rule(r#"\\[0abt\tn\nvfre "\\N_LP]"#, T(STRING), Next::Stay),
        rule(
            r"\\(?:x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8})",
            T(STRING_ESCAPE),
            Next::Stay,
        ),
        rule(r#"[^\s"\\]+"#, T(STRING), Next::Stay),
        rule(r#"""#, T(STRING), Next::Pop(1)),
    ]);
    s.insert("double-quoted-scalar", double);

    // 'plain-scalar-in-block-context-new-line' (data.py:384)
    s.insert(
        "plain-scalar-in-block-context-new-line",
        vec![
            rule(r"^[ ]+$", T(WHITESPACE), Next::Stay),
            rule(r"\n+", T(WHITESPACE), Next::Stay),
            rule(
                r"^(?=---|\.\.\.)",
                C(Cb::Something(NAME_NAMESPACE)),
                Next::Pop(3),
            ),
            rule(
                r"^[ ]*",
                C(Cb::ParsePlainScalarIndent(WHITESPACE)),
                Next::Pop(1),
            ),
        ],
    );

    // 'plain-scalar-in-block-context' (data.py:396)
    s.insert(
        "plain-scalar-in-block-context",
        vec![
            rule(
                r"[ ]*(?=:[ ]|:$)",
                C(Cb::Something(WHITESPACE)),
                Next::Pop(1),
            ),
            rule(r"[ ]+(?=#)", T(WHITESPACE), Next::Pop(1)),
            rule(r"[ ]+$", T(WHITESPACE), Next::Stay),
            rule(
                r"\n+",
                T(WHITESPACE),
                push(&["plain-scalar-in-block-context-new-line"]),
            ),
            rule(r"[ \xa0]+", T(SCALAR_PLAIN), Next::Stay),
            Rule {
                pattern: Pattern::custom(plain_block_run),
                act: T(SCALAR_PLAIN),
                next: Next::Stay,
            },
        ],
    );

    // 'plain-scalar-in-flow-context' (data.py:412)
    s.insert(
        "plain-scalar-in-flow-context",
        vec![
            rule(
                r"[ ]*(?=[,:?\[\]{}])",
                C(Cb::Something(WHITESPACE)),
                Next::Pop(1),
            ),
            rule(r"[ ]+(?=#)", T(WHITESPACE), Next::Pop(1)),
            rule(r"^[ ]+", T(WHITESPACE), Next::Stay),
            rule(r"[ ]+$", T(WHITESPACE), Next::Stay),
            rule(r"\n+", T(WHITESPACE), Next::Stay),
            rule(r"[ \xa0]+", T(NAME_VARIABLE), Next::Stay),
            rule(r"[^\s,:?\[\]{}]+", T(NAME_VARIABLE), Next::Stay),
        ],
    );

    s
}

static STATES: Lazy<HashMap<&'static str, Vec<Rule>>> = Lazy::new(build);

/// Python slice bounds on a `len`-byte run: a negative index counts from the
/// end, so `text[-1:]` is the last character.
fn py_index(len: i64, i: i64) -> usize {
    (if i < 0 { (len + i).max(0) } else { i.min(len) }) as usize
}

impl Cb {
    /// Run the callback on the rule's match `[start, end)`; `digit` is the
    /// pattern's first own group. A callback that returns without setting
    /// `cx.pos` leaves the position where it was, as in Python.
    fn run(self, cx: &mut Ctx, out: &mut Vec<Span>, start: usize, end: usize, digit: Option<&str>) {
        let len = (end - start) as i64;
        match self {
            Cb::Something(tok) => {
                if start != end {
                    out.push((tok, start, end));
                    cx.pos = end;
                }
            }
            Cb::ResetIndent(tok) => {
                cx.indent_stack.clear();
                cx.indent = -1;
                cx.next_indent = 0;
                cx.block_scalar_indent = None;
                out.push((tok, start, end));
                cx.pos = end;
            }
            Cb::SaveIndent(is_start) => {
                let mut cut = end;
                if is_start {
                    cx.next_indent = len;
                    if cx.next_indent < cx.indent {
                        while cx.next_indent < cx.indent {
                            cx.indent = cx.indent_stack.pop().expect("indent stack underflow");
                        }
                        if cx.next_indent > cx.indent {
                            cut = start + py_index(len, cx.indent);
                        }
                    }
                } else {
                    cx.next_indent += len;
                }
                if cut > start {
                    out.push((WHITESPACE, start, cut));
                }
                if cut < end {
                    out.push((WHITESPACE_ERROR, cut, end));
                }
                cx.pos = end;
            }
            Cb::SetIndent(tok, implicit) => {
                if cx.indent < cx.next_indent {
                    cx.indent_stack.push(cx.indent);
                    cx.indent = cx.next_indent;
                }
                if !implicit {
                    cx.next_indent += len;
                }
                out.push((tok, start, end));
                cx.pos = end;
            }
            Cb::SetBlockScalarIndent(tok) => {
                cx.block_scalar_indent = None;
                if start == end {
                    return;
                }
                if let Some(d) = digit {
                    let n: i64 = d.parse().expect("indentation indicator is [1-9]");
                    cx.block_scalar_indent = Some(cx.indent.max(0) + n);
                }
                out.push((tok, start, end));
                cx.pos = end;
            }
            Cb::ParseBlockScalarEmptyLine => {
                match cx.block_scalar_indent {
                    Some(bsi) if len > bsi => {
                        let cut = start + bsi as usize;
                        out.push((WHITESPACE, start, cut));
                        out.push((NAME_CONSTANT, cut, end));
                    }
                    _ => {
                        if start != end {
                            out.push((WHITESPACE, start, end));
                        }
                    }
                }
                cx.pos = end;
            }
            Cb::ParseBlockScalarIndent(tok) => {
                match cx.block_scalar_indent {
                    None if len <= cx.indent.max(0) => return cx.leave(2),
                    None => cx.block_scalar_indent = Some(len),
                    Some(bsi) if len < bsi => return cx.leave(2),
                    Some(_) => {}
                }
                if start != end {
                    out.push((tok, start, end));
                    cx.pos = end;
                }
            }
            Cb::ParsePlainScalarIndent(tok) => {
                if len <= cx.indent {
                    return cx.leave(2);
                }
                if start != end {
                    out.push((tok, start, end));
                    cx.pos = end;
                }
            }
        }
    }
}

impl Ctx {
    /// `context.stack.pop()` `n` times.
    fn leave(&mut self, n: usize) {
        for _ in 0..n {
            self.stack.pop().expect("state stack underflow");
        }
    }
}

/// `YamlLexer.get_tokens_unprocessed` over already-normalized text: the
/// `ExtendedRegexLexer` loop, `lexer.py:782-846`.
pub fn tokenize(text: &str) -> Vec<Span> {
    let mut cx = Ctx {
        pos: 0,
        stack: vec!["root"],
        indent_stack: Vec::new(),
        indent: -1,
        next_indent: 0,
        block_scalar_indent: None,
    };
    let mut out: Vec<Span> = Vec::new();
    'lex: loop {
        let state = *cx.stack.last().expect("state stack is never empty");
        for rule in &STATES[state] {
            let Some(hit) = rule.pattern.find(text, cx.pos) else {
                continue;
            };
            let (start, end) = (cx.pos, hit.end);
            match rule.act {
                Act::Tok(tok) => {
                    out.push((tok, start, end));
                    cx.pos = end;
                }
                Act::Cb(cb) => cb.run(&mut cx, &mut out, start, end, hit.group_str(1)),
                Act::Groups(groups) => {
                    for (i, group) in groups.iter().enumerate() {
                        let Some((gs, ge)) = hit.group(i + 1) else {
                            continue;
                        };
                        match group {
                            Group::Tok(tok) if gs != ge => out.push((tok, gs, ge)),
                            Group::Tok(_) => {}
                            Group::Cb(cb) => cb.run(&mut cx, &mut out, gs, ge, None),
                        }
                    }
                    cx.pos = end;
                }
            }
            transition(&mut cx.stack, &rule.next);
            continue 'lex;
        }
        if cx.pos >= text.len() {
            break;
        }
        let c = text[cx.pos..]
            .chars()
            .next()
            .expect("pos is inside the text");
        if c == '\n' {
            cx.stack = vec!["root"];
            out.push((TEXT, cx.pos, cx.pos + 1));
        } else {
            out.push((ERROR, cx.pos, cx.pos + c.len_utf8()));
        }
        cx.pos += c.len_utf8();
    }
    out
}
