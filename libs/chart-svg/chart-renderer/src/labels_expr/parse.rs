//! The lexer and parser of Vega's expression language (vega-expression, an Esprima subset): ECMAScript literals,
//! member access, calls to the function table, and every operator but assignment, `++`/`--`, `typeof`/`void`/
//! `delete` and the comma operator. Everything Vega's compiler rejects is [`EvalError::Unknown`] here.

use super::funcs;
use super::jsre;
use super::val::{EvalError, Res, Val};

#[derive(Debug, Clone, PartialEq)]
enum Tok {
    /// The value and the literal's source text.
    Num(f64, String),
    Str(String),
    Regex(String, String),
    Ident(String),
    Punct(&'static str),
    End,
}

const PUNCT: [&str; 46] = [
    ">>>", "===", "!==", "<<", ">>", "&&", "||", "==", "!=", "<=", ">=", "++", "--", "+=", "-=", "*=", "/=", "%=", "&=",
    "|=", "^=", "=>", "{", "}", "(", ")", "[", "]", ".", ",", ";", ":", "?", "~", "!", "%", "&", "*", "+", "-", "/", "<",
    "=", ">", "^", "|",
];

const KEYWORDS: [&str; 33] = [
    "break", "case", "catch", "class", "continue", "debugger", "default", "delete", "do", "else", "enum", "export",
    "extends", "finally", "for", "function", "import", "in", "instanceof", "new", "return", "super", "switch", "this",
    "throw", "try", "typeof", "var", "void", "while", "with", "const", "let",
];

fn unknown<T>(what: impl Into<String>) -> Res<T> {
    Err(EvalError::Unknown(what.into()))
}

fn is_id_start(c: char) -> bool {
    c == '$' || c == '_' || c.is_alphabetic()
}

fn is_id_part(c: char) -> bool {
    is_id_start(c) || c.is_alphanumeric() || c == '\u{200c}' || c == '\u{200d}' || matches!(c, '\u{300}'..='\u{36f}')
}

struct Lexer {
    chars: Vec<char>,
    at: usize,
}

impl Lexer {
    fn skip_space(&mut self) -> Res<()> {
        while let Some(&c) = self.chars.get(self.at) {
            if super::val::is_js_space(c) {
                self.at += 1;
            } else {
                break;
            }
        }
        Ok(())
    }

    fn hex_run(&mut self, n: usize) -> Option<u32> {
        let s: String = self.chars.get(self.at..self.at + n)?.iter().collect();
        if !s.chars().all(|c| c.is_ascii_hexdigit()) {
            return None;
        }
        self.at += n;
        u32::from_str_radix(&s, 16).ok()
    }

    fn string(&mut self, quote: char) -> Res<String> {
        let mut out = String::new();
        self.at += 1;
        loop {
            let Some(&c) = self.chars.get(self.at) else { return unknown("Unexpected token ILLEGAL") };
            self.at += 1;
            match c {
                q if q == quote => return Ok(out),
                '\n' | '\r' | '\u{2028}' | '\u{2029}' if c == '\n' || c == '\r' => return unknown("Unexpected token ILLEGAL"),
                '\\' => {
                    let Some(&e) = self.chars.get(self.at) else { return unknown("Unexpected token ILLEGAL") };
                    self.at += 1;
                    match e {
                        'n' => out.push('\n'),
                        'r' => out.push('\r'),
                        't' => out.push('\t'),
                        'b' => out.push('\u{8}'),
                        'f' => out.push('\u{c}'),
                        'v' => out.push('\u{b}'),
                        'x' => {
                            let v = self.hex_run(2).ok_or_else(|| EvalError::Unknown("Invalid hexadecimal escape sequence".into()))?;
                            out.push(char::from_u32(v).unwrap_or('\u{fffd}'));
                        }
                        'u' => {
                            let code = if self.chars.get(self.at) == Some(&'{') {
                                self.at += 1;
                                let start = self.at;
                                while self.chars.get(self.at).is_some_and(char::is_ascii_hexdigit) {
                                    self.at += 1;
                                }
                                let digits: String = self.chars[start..self.at].iter().collect();
                                if self.chars.get(self.at) != Some(&'}') || digits.is_empty() {
                                    return unknown("Invalid Unicode escape sequence");
                                }
                                self.at += 1;
                                u32::from_str_radix(&digits, 16).map_err(|_| EvalError::Unknown("Invalid Unicode escape sequence".into()))?
                            } else {
                                self.hex_run(4).ok_or_else(|| EvalError::Unknown("Invalid Unicode escape sequence".into()))?
                            };
                            if (0xD800..0xDC00).contains(&code) && self.chars.get(self.at) == Some(&'\\') && self.chars.get(self.at + 1) == Some(&'u') {
                                let save = self.at;
                                self.at += 2;
                                match self.hex_run(4) {
                                    Some(lo) if (0xDC00..0xE000).contains(&lo) => {
                                        out.push(char::from_u32(0x10000 + ((code - 0xD800) << 10) + (lo - 0xDC00)).unwrap_or('\u{fffd}'));
                                        continue;
                                    }
                                    _ => self.at = save,
                                }
                            }
                            out.push(char::from_u32(code).unwrap_or('\u{fffd}'));
                        }
                        '0' if !self.chars.get(self.at).is_some_and(char::is_ascii_digit) => out.push('\0'),
                        '0'..='7' => return unknown("Octal literals are not allowed in strict mode."),
                        '\r' => {
                            if self.chars.get(self.at) == Some(&'\n') {
                                self.at += 1;
                            }
                        }
                        '\n' | '\u{2028}' | '\u{2029}' => {}
                        other => out.push(other),
                    }
                }
                other => out.push(other),
            }
        }
    }

    fn number(&mut self) -> Res<(f64, String)> {
        let start = self.at;
        let c = self.chars[self.at];
        let next = self.chars.get(self.at + 1).copied();
        let value = if c == '0' && matches!(next, Some('x' | 'X')) {
            let radix = 16;
            self.at += 2;
            let ds = self.at;
            while self.chars.get(self.at).is_some_and(|c| c.is_digit(radix)) {
                self.at += 1;
            }
            if ds == self.at {
                return unknown("Unexpected token ILLEGAL");
            }
            self.chars[ds..self.at].iter().fold(0.0, |acc, c| acc * f64::from(radix) + f64::from(c.to_digit(radix).unwrap_or(0)))
        } else {
            if c == '0' && next.is_some_and(|d| d.is_ascii_digit()) {
                return unknown("Octal literals are not allowed in strict mode.");
            }
            while self.chars.get(self.at).is_some_and(char::is_ascii_digit) {
                self.at += 1;
            }
            if self.chars.get(self.at) == Some(&'.') {
                self.at += 1;
                while self.chars.get(self.at).is_some_and(char::is_ascii_digit) {
                    self.at += 1;
                }
            }
            if matches!(self.chars.get(self.at), Some('e' | 'E')) {
                self.at += 1;
                if matches!(self.chars.get(self.at), Some('+' | '-')) {
                    self.at += 1;
                }
                let ds = self.at;
                while self.chars.get(self.at).is_some_and(char::is_ascii_digit) {
                    self.at += 1;
                }
                if ds == self.at {
                    return unknown("Unexpected token ILLEGAL");
                }
            }
            let text: String = self.chars[start..self.at].iter().collect();
            text.parse::<f64>().map_err(|_| EvalError::Unknown("Unexpected token ILLEGAL".into()))?
        };
        if self.chars.get(self.at).is_some_and(|c| is_id_start(*c) || c.is_ascii_digit()) {
            return unknown("Unexpected token ILLEGAL");
        }
        Ok((value, self.chars[start..self.at].iter().collect()))
    }

    fn regex(&mut self) -> Res<Tok> {
        self.at += 1;
        let mut source = String::new();
        let mut in_class = false;
        loop {
            let Some(&c) = self.chars.get(self.at) else { return unknown("Invalid regular expression: missing /") };
            self.at += 1;
            match c {
                '\n' | '\r' | '\u{2028}' | '\u{2029}' => return unknown("Invalid regular expression: missing /"),
                '\\' => {
                    let Some(&e) = self.chars.get(self.at) else { return unknown("Invalid regular expression: missing /") };
                    self.at += 1;
                    source.push('\\');
                    source.push(e);
                }
                '[' => {
                    in_class = true;
                    source.push(c);
                }
                ']' => {
                    in_class = false;
                    source.push(c);
                }
                '/' if !in_class => break,
                other => source.push(other),
            }
        }
        let start = self.at;
        while self.chars.get(self.at).is_some_and(|c| is_id_part(*c)) {
            self.at += 1;
        }
        let flags: String = self.chars[start..self.at].iter().collect();
        if flags.chars().any(|f| !"gimuy".contains(f)) {
            return unknown("Invalid regular expression flags");
        }
        jsre::compile(&source, &flags).map_err(|e| e.into_eval(EvalError::Unknown))?;
        Ok(Tok::Regex(source, flags))
    }

    fn next(&mut self, regex_ok: bool) -> Res<Tok> {
        self.skip_space()?;
        let Some(&c) = self.chars.get(self.at) else { return Ok(Tok::End) };
        if c.is_ascii_digit() || (c == '.' && self.chars.get(self.at + 1).is_some_and(char::is_ascii_digit)) {
            let (n, raw) = self.number()?;
            Ok(Tok::Num(n, raw))
        } else if c == '\'' || c == '"' {
            let s = self.string(c)?;
            Ok(Tok::Str(s))
        } else if is_id_start(c) {
            let start = self.at;
            while self.chars.get(self.at).is_some_and(|c| is_id_part(*c)) {
                self.at += 1;
            }
            Ok(Tok::Ident(self.chars[start..self.at].iter().collect()))
        } else if c == '/' && regex_ok {
            self.regex()
        } else {
            let rest: String = self.chars[self.at..(self.at + 3).min(self.chars.len())].iter().collect();
            let p = PUNCT.iter().find(|p| rest.starts_with(**p)).ok_or_else(|| EvalError::Unknown(format!("Unexpected token {c}")))?;
            self.at += p.len();
            Ok(Tok::Punct(p))
        }
    }
}

/// A parsed expression tree, with every name resolved.
#[derive(Debug, Clone)]
pub enum Node {
    Lit(Val),
    /// A numeric literal with its source text.
    Num(f64, String),
    /// A callee name, until the call resolves it.
    Ident(String),
    Datum,
    Item,
    /// The `event` global: a `ReferenceError` outside event handlers.
    Event,
    Array(Vec<Node>),
    Object(Vec<(String, Node)>),
    Member(Box<Node>, Box<Node>),
    Call(&'static str, Vec<Node>),
    Unary(&'static str, Box<Node>),
    Binary(&'static str, Box<Node>, Box<Node>),
    Logical(&'static str, Box<Node>, Box<Node>),
    Cond(Box<Node>, Box<Node>, Box<Node>),
}

pub struct Parser {
    lex: Lexer,
    tok: Tok,
    /// The first failure of the code generator (an unknown function, a missing argument): Vega raises it after the
    /// whole expression parsed, so a syntax error elsewhere in it wins.
    codegen: Option<EvalError>,
    /// How many nested expressions the parser is inside.
    depth: usize,
}

/// How deep an expression tree may nest, by parentheses or by a chain of operators, members and calls alike.
/// Production renders deeper ones (Esprima and Vega's code generator recurse on V8's stack and give up at some
/// thousand levels); evaluating and dropping a tree recurse on the caller's stack, so past this depth the
/// expression is refused, not run.
const MAX_DEPTH: usize = 100;

/// A source this short cannot nest deep enough to need more than a small stack.
const INLINE_LEN: usize = 32;

/// The stack a longer source parses on: [`MAX_DEPTH`] levels of the recursive descent in a debug build.
const PARSE_STACK: usize = 32 * 1024 * 1024;

const CONSTANTS: [(&str, f64); 11] = [
    ("NaN", f64::NAN),
    ("E", std::f64::consts::E),
    ("LN2", std::f64::consts::LN_2),
    ("LN10", std::f64::consts::LN_10),
    ("LOG2E", std::f64::consts::LOG2_E),
    ("LOG10E", std::f64::consts::LOG10_E),
    ("PI", std::f64::consts::PI),
    ("SQRT1_2", std::f64::consts::FRAC_1_SQRT_2),
    ("SQRT2", std::f64::consts::SQRT_2),
    ("MIN_VALUE", 5e-324),
    ("MAX_VALUE", f64::MAX),
];

fn too_deep<T>() -> Res<T> {
    Err(EvalError::Unmodeled(crate::refuse!("labels.expr nested past {MAX_DEPTH} levels is not supported")))
}

/// Whether `node` is at most `budget` levels deep. Recurses no deeper than `budget`, however long a chain is.
fn within(node: &Node, budget: usize) -> bool {
    let Some(below) = budget.checked_sub(1) else { return false };
    match node {
        Node::Member(a, b) | Node::Binary(_, a, b) | Node::Logical(_, a, b) => within(a, below) && within(b, below),
        Node::Unary(_, a) => within(a, below),
        Node::Cond(a, b, c) => within(a, below) && within(b, below) && within(c, below),
        Node::Array(items) | Node::Call(_, items) => items.iter().all(|n| within(n, below)),
        Node::Object(props) => props.iter().all(|(_, n)| within(n, below)),
        Node::Lit(_) | Node::Num(..) | Node::Ident(_) | Node::Datum | Node::Item | Node::Event => true,
    }
}

/// `node`, if it is within [`MAX_DEPTH`]: every node a loop builds on top of the last passes through here, so a
/// chain is refused where it passes the bound and never grows past it.
fn bounded(node: Node) -> Res<Node> {
    if within(&node, MAX_DEPTH) {
        Ok(node)
    } else {
        too_deep()
    }
}

fn binary_precedence(tok: &Tok) -> usize {
    match tok {
        Tok::Punct(p) => match *p {
            "||" => 1,
            "&&" => 2,
            "|" => 3,
            "^" => 4,
            "&" => 5,
            "==" | "!=" | "===" | "!==" => 6,
            "<" | ">" | "<=" | ">=" => 7,
            "<<" | ">>" | ">>>" => 8,
            "+" | "-" => 9,
            "*" | "/" | "%" => 11,
            _ => 0,
        },
        Tok::Ident(w) if w == "in" || w == "instanceof" => 7,
        _ => 0,
    }
}

impl Parser {
    pub fn parse(src: &str) -> Res<Node> {
        if src.len() <= INLINE_LEN {
            return Self::parse_here(src);
        }
        std::thread::scope(|scope| {
            std::thread::Builder::new()
                .stack_size(PARSE_STACK)
                .spawn_scoped(scope, || Self::parse_here(src))
                .expect("the OS starts a parse thread")
                .join()
                .unwrap_or_else(|panic| std::panic::resume_unwind(panic))
        })
    }

    fn parse_here(src: &str) -> Res<Node> {
        let mut p = Parser { lex: Lexer { chars: src.chars().collect(), at: 0 }, tok: Tok::End, codegen: None, depth: 0 };
        let parsed = p.advance(true).and_then(|_| p.conditional()).and_then(|node| {
            if p.tok == Tok::End { Ok(node) } else { unknown("Unexpect token after expression.") }
        });
        match (parsed, p.codegen) {
            // vega-functions reports any failure of the parser itself as the expression it could not parse.
            (Err(EvalError::Unknown(_)), _) => unknown(format!("Expression parse error: ({src})")),
            (Err(other), _) => Err(other),
            (Ok(_), Some(codegen)) => Err(codegen),
            (Ok(node), None) => Ok(node),
        }
    }

    /// A failure of the code generator, which Vega raises once the whole expression parsed: the first is kept and
    /// the parse goes on with an `undefined` in its place.
    fn codegen_failure(&mut self, what: impl Into<String>) -> Node {
        self.codegen.get_or_insert(EvalError::Unknown(what.into()));
        Node::Lit(Val::Undefined)
    }

    /// Runs one more level of nesting: past [`MAX_DEPTH`], the expression is refused.
    fn nested<T>(&mut self, parse: impl FnOnce(&mut Self) -> Res<T>) -> Res<T> {
        if self.depth >= MAX_DEPTH {
            return too_deep();
        }
        self.depth += 1;
        let parsed = parse(self);
        self.depth -= 1;
        parsed
    }

    fn advance(&mut self, regex_ok: bool) -> Res<Tok> {
        let next = self.lex.next(regex_ok)?;
        Ok(std::mem::replace(&mut self.tok, next))
    }

    /// Consumes the current token and lexes the next one, knowing whether it follows an operand.
    fn take(&mut self) -> Res<Tok> {
        let regex_next = match &self.tok {
            Tok::Num(..) | Tok::Str(_) | Tok::Regex(..) | Tok::Punct(")" | "]" | "}") => false,
            Tok::Ident(w) => KEYWORDS.contains(&w.as_str()),
            _ => true,
        };
        // After an operand `/` divides; after an operator or `(` it starts a regex. An identifier is an operand.
        self.advance(regex_next)
    }

    fn expect(&mut self, p: &str) -> Res<()> {
        if matches!(&self.tok, Tok::Punct(q) if *q == p) {
            self.take()?;
            Ok(())
        } else {
            unknown(format!("Unexpected token, expected {p}"))
        }
    }

    fn matches(&self, p: &str) -> bool {
        matches!(&self.tok, Tok::Punct(q) if *q == p)
    }

    fn conditional(&mut self) -> Res<Node> {
        self.nested(Self::conditional_level)
    }

    fn conditional_level(&mut self) -> Res<Node> {
        let test = self.binary()?;
        if !self.matches("?") {
            return Ok(test);
        }
        self.take()?;
        let yes = self.conditional()?;
        self.expect(":")?;
        let no = self.conditional()?;
        bounded(Node::Cond(Box::new(test), Box::new(yes), Box::new(no)))
    }

    fn binary(&mut self) -> Res<Node> {
        self.binary_at(0)
    }

    fn binary_at(&mut self, min_prec: usize) -> Res<Node> {
        let mut left = self.unary()?;
        loop {
            let prec = binary_precedence(&self.tok);
            if prec == 0 || prec <= min_prec {
                return Ok(left);
            }
            let op = match self.take()? {
                Tok::Punct(p) => p,
                Tok::Ident(w) if w == "in" => "in",
                Tok::Ident(_) => "instanceof",
                _ => return unknown("Unexpected token"),
            };
            let right = self.binary_at(prec)?;
            left = bounded(if op == "&&" || op == "||" {
                Node::Logical(if op == "&&" { "&&" } else { "||" }, Box::new(left), Box::new(right))
            } else {
                Node::Binary(op, Box::new(left), Box::new(right))
            })?;
        }
    }

    fn unary(&mut self) -> Res<Node> {
        match &self.tok {
            Tok::Punct("++" | "--") => unknown("Disabled."),
            Tok::Punct(op @ ("+" | "-" | "~" | "!")) => {
                let op: &'static str = op;
                self.take()?;
                let inner = self.nested(Self::unary)?;
                bounded(Node::Unary(op, Box::new(inner)))
            }
            Tok::Ident(w) if matches!(w.as_str(), "delete" | "void" | "typeof") => unknown("Disabled."),
            _ => self.postfix(),
        }
    }

    fn postfix(&mut self) -> Res<Node> {
        let mut expr = self.primary()?;
        loop {
            if self.matches(".") {
                self.take()?;
                let name = match self.take()? {
                    Tok::Ident(n) => n,
                    _ => return unknown("Unexpected token"),
                };
                expr = bounded(Node::Member(Box::new(expr), Box::new(Node::Lit(Val::string(name)?))))?;
            } else if self.matches("[") {
                self.take()?;
                let key = self.conditional()?;
                if self.matches(",") {
                    return unknown("Disabled.");
                }
                self.expect("]")?;
                expr = bounded(Node::Member(Box::new(expr), Box::new(key)))?;
            } else if self.matches("(") {
                let Node::Ident(name) = &expr else { return unknown("Illegal callee type: MemberExpression") };
                let name = name.clone();
                self.take()?;
                let mut args = Vec::new();
                if !self.matches(")") {
                    loop {
                        args.push(self.conditional()?);
                        if self.matches(")") {
                            break;
                        }
                        self.expect(",")?;
                    }
                }
                self.expect(")")?;
                expr = bounded(funcs::resolve(&name, args).unwrap_or_else(|failure| {
                    self.codegen.get_or_insert(failure);
                    Node::Lit(Val::Undefined)
                }))?;
            } else {
                return Ok(expr);
            }
        }
    }

    fn primary(&mut self) -> Res<Node> {
        let tok = self.tok.clone();
        match tok {
            Tok::Punct("(") => {
                self.take()?;
                let inner = self.conditional()?;
                if self.matches(",") {
                    return unknown("Disabled.");
                }
                self.expect(")")?;
                Ok(inner)
            }
            Tok::Punct("[") => {
                self.take()?;
                let mut items = Vec::new();
                while !self.matches("]") {
                    if self.matches(",") {
                        return unknown("Unexpected token ,");
                    }
                    items.push(self.conditional()?);
                    if !self.matches("]") {
                        self.expect(",")?;
                    }
                }
                self.take()?;
                bounded(Node::Array(items))
            }
            Tok::Punct("{") => {
                self.take()?;
                let mut props = Vec::new();
                while !self.matches("}") {
                    let key = match self.take()? {
                        Tok::Ident(k) | Tok::Str(k) => k,
                        Tok::Num(n, _) => super::val::num_to_str(n),
                        _ => return unknown("Unexpected token"),
                    };
                    if matches!(key.as_str(), "__proto__" | "constructor" | "toString" | "valueOf" | "hasOwnProperty" | "isPrototypeOf" | "propertyIsEnumerable" | "toLocaleString" | "__defineGetter__" | "__defineSetter__" | "__lookupGetter__" | "__lookupSetter__") {
                        return unknown(format!("Illegal property: {key}"));
                    }
                    self.expect(":")?;
                    props.push((key, self.conditional()?));
                    if !self.matches("}") {
                        self.expect(",")?;
                    }
                }
                self.take()?;
                bounded(Node::Object(props))
            }
            Tok::Num(n, raw) => {
                self.take()?;
                Ok(Node::Num(n, raw))
            }
            Tok::Str(s) => {
                self.take()?;
                // A string that spells a method every object has takes the compiled expression's parameter lookup
                // down with it.
                if matches!(
                    s.as_str(),
                    "constructor" | "toString" | "valueOf" | "hasOwnProperty" | "isPrototypeOf" | "propertyIsEnumerable" | "toLocaleString"
                        | "__defineGetter__" | "__defineSetter__" | "__lookupGetter__" | "__lookupSetter__"
                ) {
                    return Ok(self.codegen_failure("Operator not defined: undefined"));
                }
                Ok(Node::Lit(Val::string(s)?))
            }
            Tok::Regex(source, flags) => {
                self.take()?;
                Ok(Node::Lit(Val::Regex(source, flags)))
            }
            Tok::Ident(name) => {
                self.take()?;
                if self.matches("(") {
                    return Ok(Node::Ident(name));
                }
                match name.as_str() {
                    "true" => Ok(Node::Lit(Val::Bool(true))),
                    "false" => Ok(Node::Lit(Val::Bool(false))),
                    "null" => Ok(Node::Lit(Val::Null)),
                    w if KEYWORDS.contains(&w) && w != "if" => unknown("Disabled."),
                    "datum" => Ok(Node::Datum),
                    "item" => Ok(Node::Item),
                    "event" => Ok(Node::Event),
                    "_" => Ok(self.codegen_failure("Illegal identifier: _")),
                    w => {
                        if let Some((_, v)) = CONSTANTS.iter().find(|(n, _)| *n == w) {
                            return Ok(Node::Lit(Val::Num(*v)));
                        }
                        Ok(self.codegen_failure(format!("Unrecognized signal name: \"{w}\"")))
                    }
                }
            }
            Tok::Punct(p) => unknown(format!("Unexpected token {p}")),
            Tok::End => unknown("Unexpected end of input"),
        }
    }
}
