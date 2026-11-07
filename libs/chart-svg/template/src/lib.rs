//! The shared template crate: one minijinja environment definition for all
//! user text (`environment`) and parse-only analysis. Compile parses Jinja and
//! never renders it; rendering stays where the values are.

mod deps;
pub mod environment;
pub mod mask;

use std::collections::BTreeSet;
use std::ops::Range;

use minijinja::machinery::{parse as parse_stmts, parse_expr};
use serde::Serialize;

pub use environment::Kind;

/// A syntax finding. `span` is a byte range into the source as written (for a
/// `where:` clause, into the whole clause, braces included).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Diagnostic {
    pub message: String,
    pub line: Option<usize>,
    pub span: Option<Range<usize>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FilterCall {
    pub function: String,
    /// The column when it is a string literal.
    pub column: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct Listing {
    pub function: String,
    pub pattern: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TemplateInfo {
    /// Names read but never assigned, minus the runtime helpers and dbt
    /// builtins that are only ever called.
    pub variables: BTreeSet<String>,
    /// Query names read as `queries.NAME` / `queries['NAME']`.
    pub queries: BTreeSet<String>,
    /// `queries` is used in a form other than a literal-name read.
    pub queries_nonliteral: bool,
    pub filter_calls: Vec<FilterCall>,
    /// A filter helper is used other than by calling it by name (aliased,
    /// passed, or bound), so `filter_calls` may be incomplete.
    pub filter_nonliteral: bool,
    pub listings: BTreeSet<Listing>,
    /// A listing function is used other than as a call with one string literal.
    pub listings_nonliteral: bool,
    /// The custom chart header's YAML text (`Kind::Svg` only).
    pub header: Option<String>,
    /// A construct the dependency walk does not model; the dependency fields
    /// above are then not to be trusted.
    pub unanalyzed: Option<&'static str>,
    /// Some expression does arithmetic (`+ - * / // % **`).
    pub arithmetic: bool,
    /// Some expression orders two values (`< <= > >=`).
    pub ordering: bool,
    /// Some expression joins values into text with `~`.
    pub concat: bool,
}

fn diagnostic(e: &minijinja::Error, offset: usize, source: &str) -> Diagnostic {
    // minijinja reports end-of-input errors one byte past the end.
    let span = e
        .range()
        .map(|r| (r.start + offset).min(source.len())..(r.end + offset).min(source.len()));
    // A `where:` clause is parsed without its braces, so minijinja's own line
    // is relative to the clause; the span indexes the whole source.
    let line = match &span {
        Some(r) => source.get(..r.start).map(|p| p.matches('\n').count() + 1),
        None => e.line(),
    };
    Diagnostic {
        message: e.detail().map_or_else(|| e.to_string(), str::to_string),
        line,
        span,
    }
}

fn plain(message: &str) -> Vec<Diagnostic> {
    vec![Diagnostic {
        message: message.to_string(),
        line: None,
        span: None,
    }]
}

/// Parses `source` as `kind` and reports its dependencies, or every syntax
/// diagnostic. Never renders.
pub fn parse(source: &str, kind: Kind) -> Result<TemplateInfo, Vec<Diagnostic>> {
    let header = if kind == Kind::Svg {
        header(source)?
    } else {
        None
    };
    let d = if kind == Kind::Where {
        let (clause, offset) = where_clause(source)?;
        let expr = parse_expr(clause).map_err(|e| vec![diagnostic(&e, offset, source)])?;
        deps::analyze_expr(&expr)
    } else {
        let syntax = environment::syntax(kind).map_err(|e| vec![diagnostic(&e, 0, source)])?;
        let masked;
        let text = if kind == Kind::Markdown {
            masked = mask::mask_fenced_code(source);
            masked.as_str()
        } else {
            source
        };
        let root = parse_stmts(text, "<template>", syntax, Default::default())
            .map_err(|e| vec![diagnostic(&e, 0, source)])?;
        if let Some(d) = jinja2_refusal(&root, text) {
            return Err(vec![d]);
        }
        deps::analyze(&root)
    };
    Ok(TemplateInfo {
        variables: d.variables,
        queries: d.queries,
        queries_nonliteral: d.queries_nonliteral,
        filter_calls: d.filter_calls,
        filter_nonliteral: d.filter_nonliteral,
        listings: d.listings,
        listings_nonliteral: d.listings_nonliteral,
        header,
        unanalyzed: d.unanalyzed,
        arithmetic: d.arithmetic,
        ordering: d.ordering,
        concat: d.concat,
    })
}

/// `{#---\n<yaml>\n---#}` leading a custom chart template.
fn header(source: &str) -> Result<Option<String>, Vec<Diagnostic>> {
    let Some(rest) = source.trim_start().strip_prefix("{#---") else {
        return Ok(None);
    };
    let body = rest
        .strip_prefix("\r\n")
        .or_else(|| rest.strip_prefix('\n'));
    let close = body.and_then(|b| {
        let end = b.find("\n---#}")?;
        Some(b.get(..end)?.strip_suffix('\r').unwrap_or(b.get(..end)?))
    });
    match close {
        Some(yaml) => Ok(Some(yaml.to_string())),
        None => Err(plain(
            "template header must open with `{#---` on its own line and close with `---#}` on its own line",
        )),
    }
}

/// A `where:` clause is `x` or `{{ x }}`; mixed forms are refused. Returns the
/// bare expression and its byte offset in the source.
fn where_clause(source: &str) -> Result<(&str, usize), Vec<Diagnostic>> {
    let lead = source.len() - source.trim_start().len();
    let s = source.trim();
    let Some(inner) = s.strip_prefix("{{").and_then(|r| r.strip_suffix("}}")) else {
        return Ok((s, lead));
    };
    if inner.contains("{{") || inner.contains("}}") {
        return Err(plain(
            "where: expression contains nested {{ ... }}; use a bare expression",
        ));
    }
    let skipped = inner.len() - inner.trim_start().len();
    Ok((inner.trim(), lead + 2 + skipped))
}

/// Constructs minijinja parses but jinja2's plain environment refuses: the
/// shared environment refuses them too, so the two agree on what is a template.
fn jinja2_refusal(root: &minijinja::machinery::ast::Stmt<'_>, text: &str) -> Option<Diagnostic> {
    use minijinja::machinery::ast::{Expr, Stmt};
    fn find(stmt: &Stmt<'_>) -> Option<(&'static str, Range<usize>)> {
        let kids: &[Stmt<'_>] = match stmt {
            Stmt::Do(d) => {
                return Some((
                    "unknown tag 'do' (jinja2 has no do-extension here)",
                    span_of(d),
                ))
            }
            Stmt::Set(s) => {
                return match &s.target {
                    Expr::GetAttr(g) if !matches!(g.expr, Expr::Var(_)) => Some((
                        "a set target may name one attribute at most (`ns.attr`)",
                        span_of(s),
                    )),
                    _ => None,
                }
            }
            Stmt::Template(t) => &t.children,
            Stmt::ForLoop(f) => return f.body.iter().chain(&f.else_body).find_map(find),
            Stmt::IfCond(i) => return i.true_body.iter().chain(&i.false_body).find_map(find),
            Stmt::WithBlock(w) => &w.body,
            Stmt::SetBlock(s) => &s.body,
            Stmt::AutoEscape(a) => &a.body,
            Stmt::FilterBlock(f) => &f.body,
            Stmt::Block(b) => &b.body,
            Stmt::Macro(m) => &m.body,
            Stmt::CallBlock(c) => &c.macro_decl.body,
            _ => return None,
        };
        kids.iter().find_map(find)
    }
    let (message, span) = find(root)?;
    let line = text.get(..span.start).map(|p| p.matches('\n').count() + 1);
    Some(Diagnostic {
        message: message.to_string(),
        line,
        span: Some(span),
    })
}

fn span_of<T>(node: &minijinja::machinery::ast::Spanned<T>) -> Range<usize> {
    let s = node.span();
    s.start_offset as usize..s.end_offset as usize
}
