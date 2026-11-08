//! Dependency extraction over minijinja's AST. The rules are
//! `jinja2.meta.find_undeclared_variables` plus the dbt Charts layer of
//! `jinja.py::extract_variable_dependencies`: a read counts unless an
//! enclosing scope assigned the name; every `Var` except a call's callee is a
//! "bare" use (a dbt builtin such as `ref` is stripped only when it is never
//! used bare).

use std::collections::{BTreeSet, HashSet};

use minijinja::machinery::ast;

use crate::environment::{LISTING_FUNCTIONS, RESERVED_VARIABLE_NAMES};
use crate::{FilterCall, Listing};

/// Called in dbt model SQL; stripped from variables only when never bare.
const DBT_BUILTIN_CALLS: &[&str] = &[
    "ref",
    "source",
    "var",
    "env_var",
    "config",
    "is_incremental",
];

/// Jinja2's default globals, which its tracker seeds as already declared.
const JINJA2_DEFAULT_GLOBALS: &[&str] =
    &["range", "dict", "lipsum", "cycler", "joiner", "namespace"];

#[derive(Default)]
pub struct Deps {
    pub variables: BTreeSet<String>,
    pub queries: BTreeSet<String>,
    pub queries_nonliteral: bool,
    pub filter_calls: Vec<FilterCall>,
    pub filter_nonliteral: bool,
    pub listings: BTreeSet<Listing>,
    pub listings_nonliteral: bool,
    /// A construct the walk does not model; the dependency fields are not
    /// to be trusted when set.
    pub unanalyzed: Option<&'static str>,
    /// Some expression adds, subtracts, multiplies, divides or raises.
    pub arithmetic: bool,
    /// Some expression joins values into text with `~`.
    pub concat: bool,
    /// Every comparison of one ordering operator between two operands, in walk order.
    pub orderings: Vec<OrderingSite>,
    /// An ordering chained with another comparison (`a < b < c`), which no site covers.
    pub ordering_chained: bool,
}

/// One `left <op> right` comparison: each operand's byte range in the source, so a
/// caller can rewrite the comparison around its operands.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct OrderingSite {
    pub left: (u32, u32),
    pub op: &'static str,
    pub right: (u32, u32),
}

#[derive(Default)]
struct Walk<'a> {
    scopes: Vec<HashSet<&'a str>>,
    /// Index in `scopes` where each jinja2 frame (template, loop, macro,
    /// with, ...) begins; `if` branches share their frame.
    frames: Vec<usize>,
    /// Per frame: every name the frame references or binds at its own level
    /// anywhere in its body (jinja2 analyzes a frame before its inner frames
    /// are built, so an inner frame sees bindings written after it).
    levels: Vec<HashSet<&'a str>>,
    /// Per scope: the names it binds (set, loop target, macro), as opposed to
    /// names merely marked declared after a first read.
    stored: Vec<HashSet<&'a str>>,
    undeclared: HashSet<&'a str>,
    bare: HashSet<&'a str>,
    queries: BTreeSet<String>,
    queries_uses: usize,
    queries_literal: usize,
    filter_calls: Vec<FilterCall>,
    listings: BTreeSet<Listing>,
    listing_uses: usize,
    listing_literal: usize,
    unanalyzed: Option<&'static str>,
    arithmetic: bool,
    concat: bool,
    orderings: Vec<OrderingSite>,
    ordering_chained: bool,
}

pub fn analyze(root: &ast::Stmt<'_>) -> Deps {
    let level = match root {
        ast::Stmt::Template(t) => level_names(&t.children),
        _ => HashSet::new(),
    };
    let mut w = Walk::new(level);
    w.stmt(root);
    w.finish()
}

/// Dependencies of a bare expression (a `where:` clause).
pub fn analyze_expr(expr: &ast::Expr<'_>) -> Deps {
    let mut w = Walk::new(HashSet::new());
    w.expr(expr);
    w.finish()
}

impl<'a> Walk<'a> {
    fn new(mut root_level: HashSet<&'a str>) -> Self {
        let mut w = Walk::default();
        w.frames.push(0);
        root_level.insert("self");
        root_level.extend(JINJA2_DEFAULT_GLOBALS);
        w.levels.push(root_level);
        w.scopes.push(HashSet::new());
        w.stored.push(HashSet::new());
        w.assign("self");
        JINJA2_DEFAULT_GLOBALS.iter().for_each(|g| w.assign(g));
        w
    }

    fn finish(self) -> Deps {
        let variables = self
            .undeclared
            .iter()
            .filter(|n| !RESERVED_VARIABLE_NAMES.contains(n))
            .filter(|n| !(DBT_BUILTIN_CALLS.contains(n) && !self.bare.contains(*n)))
            .map(|n| n.to_string())
            .collect();
        Deps {
            variables,
            queries: self.queries,
            queries_nonliteral: self.queries_uses != self.queries_literal,
            filter_nonliteral: self.bare.contains("filter")
                || self.bare.contains("filter_date_range"),
            filter_calls: self.filter_calls,
            listings: self.listings,
            listings_nonliteral: self.listing_uses != self.listing_literal,
            unanalyzed: self.unanalyzed,
            arithmetic: self.arithmetic,
            concat: self.concat,
            orderings: self.orderings,
            ordering_chained: self.ordering_chained,
        }
    }

    fn frame_start(&self) -> usize {
        self.frames.last().copied().unwrap_or(0)
    }

    /// Referenced earlier in this frame, or anywhere in an enclosing frame.
    fn declared(&self, name: &str) -> bool {
        self.scopes
            .iter()
            .skip(self.frame_start())
            .any(|s| s.contains(name))
            || self.in_parent(name)
    }

    fn in_parent(&self, name: &str) -> bool {
        self.levels.iter().rev().skip(1).any(|l| l.contains(name))
    }

    fn assign(&mut self, name: &'a str) {
        if let Some(scope) = self.scopes.last_mut() {
            scope.insert(name);
        }
    }

    /// Runs `f` in a new scope and returns what it bound. `frame` is the
    /// level names of a new jinja2 frame; `None` is an `if` branch, which
    /// shares its frame.
    fn scoped(
        &mut self,
        frame: Option<HashSet<&'a str>>,
        f: impl FnOnce(&mut Self),
    ) -> HashSet<&'a str> {
        let framed = frame.is_some();
        if let Some(names) = frame {
            self.frames.push(self.scopes.len());
            self.levels.push(names);
        }
        self.scopes.push(HashSet::new());
        self.stored.push(HashSet::new());
        f(self);
        self.scopes.pop();
        if framed {
            self.frames.pop();
            self.levels.pop();
        }
        self.stored.pop().unwrap_or_default()
    }

    fn store(&mut self, name: &'a str) {
        self.assign(name);
        if let Some(stored) = self.stored.last_mut() {
            stored.insert(name);
        }
    }

    fn is_local(&self, name: &str) -> bool {
        self.stored.iter().any(|s| s.contains(name))
    }

    /// jinja2's branch rule, measured against `find_undeclared_variables`: a
    /// name an `if` branch binds reads as undeclared (even when every branch
    /// binds it, even inside the branch) unless the frame bound it before the
    /// `if` or a parent frame references it (then it is an alias). It is
    /// bound in the enclosing scope afterwards.
    fn branches(&mut self, bodies: [&[ast::Stmt<'a>]; 2]) {
        let stores: Vec<_> = bodies
            .into_iter()
            .map(|b| self.scoped(None, |w| w.stmts(b)))
            .collect();
        let start = self.frame_start();
        for name in stores.into_iter().flatten() {
            let in_frame = self.stored.iter().skip(start).any(|s| s.contains(name));
            if !in_frame && !self.in_parent(name) {
                self.undeclared.insert(name);
            }
            self.store(name);
        }
    }

    /// A read of `name`: undeclared unless a scope holds it, and declared from
    /// here on (jinja's tracker reports a name once, flow-insensitively).
    fn read(&mut self, name: &'a str, callee: bool) {
        self.queries_uses += usize::from(name == "queries");
        self.listing_uses += usize::from(LISTING_FUNCTIONS.contains(&name));
        if !self.declared(name) {
            self.undeclared.insert(name);
            self.assign(name);
        }
        if !callee {
            self.bare.insert(name);
        }
    }

    fn stmts(&mut self, body: &[ast::Stmt<'a>]) {
        for s in body {
            self.stmt(s);
        }
    }

    fn stmt(&mut self, stmt: &ast::Stmt<'a>) {
        match stmt {
            ast::Stmt::Template(t) => self.stmts(&t.children),
            ast::Stmt::EmitExpr(e) => self.expr(&e.expr),
            ast::Stmt::EmitRaw(_) => {}
            ast::Stmt::ForLoop(f) => {
                self.expr(&f.iter);
                let mut level = level_names(&f.body);
                level.insert("loop");
                level.extend(target_names(&f.target));
                self.scoped(Some(level), |w| {
                    w.assign("loop");
                    w.target(&f.target);
                    if let Some(e) = &f.filter_expr {
                        w.expr(e);
                    }
                    w.stmts(&f.body);
                });
                self.scoped(Some(level_names(&f.else_body)), |w| w.stmts(&f.else_body));
            }
            ast::Stmt::IfCond(i) => {
                self.expr(&i.expr);
                self.branches([&i.true_body, &i.false_body]);
            }
            // Values are read before any target binds: `with a = 1, b = a` reads the outer `a`.
            ast::Stmt::WithBlock(wb) => {
                let mut level = level_names(&wb.body);
                level.extend(wb.assignments.iter().flat_map(|(t, _)| target_names(t)));
                self.scoped(Some(level), |w| {
                    for (_, e) in &wb.assignments {
                        w.expr(e);
                    }
                    for (t, _) in &wb.assignments {
                        w.target(t);
                    }
                    w.stmts(&wb.body);
                });
            }
            ast::Stmt::Set(s) => {
                self.expr(&s.expr);
                self.target(&s.target);
            }
            ast::Stmt::SetBlock(s) => {
                if s.filter.is_some() {
                    self.unanalyzed = Some("set block with a filter");
                }
                self.target(&s.target);
                self.scoped(Some(level_names(&s.body)), |w| w.stmts(&s.body));
            }
            ast::Stmt::AutoEscape(a) => {
                self.expr(&a.enabled);
                // jinja2 parses `autoescape` as a scope: its body is an inner frame.
                self.scoped(Some(level_names(&a.body)), |w| w.stmts(&a.body));
            }
            ast::Stmt::FilterBlock(f) => {
                self.expr(&f.filter);
                self.scoped(Some(level_names(&f.body)), |w| w.stmts(&f.body));
            }
            // Refused at parse (`jinja2_refusal`), so never analyzed.
            ast::Stmt::Do(_) => {}
            ast::Stmt::Macro(m) => {
                self.store(m.name);
                self.macro_body(m);
            }
            ast::Stmt::CallBlock(c) => {
                self.call(&c.call);
                self.macro_body(&c.macro_decl);
            }
            ast::Stmt::Block(_)
            | ast::Stmt::Import(_)
            | ast::Stmt::FromImport(_)
            | ast::Stmt::Extends(_)
            | ast::Stmt::Include(_) => {
                self.unanalyzed = Some("inheritance, import, include or block tag")
            }
        }
    }

    /// Defaults read in the enclosing scope; parameters and the names a macro
    /// body gets from the call machinery are assigned inside.
    fn macro_body(&mut self, m: &ast::Macro<'a>) {
        m.defaults.iter().for_each(|d| self.expr(d));
        let mut level = level_names(&m.body);
        level.extend(["caller", "kwargs", "varargs"]);
        level.extend(m.args.iter().flat_map(target_names));
        self.scoped(Some(level), |w| {
            for name in ["caller", "kwargs", "varargs"] {
                w.assign(name);
            }
            m.args.iter().for_each(|a| w.target(a));
            w.stmts(&m.body);
        });
    }

    /// An assignment target: a name, a tuple of targets, or `ns.attr`, which
    /// jinja parses as a namespace-ref read of `ns`, never a bare name use.
    fn target(&mut self, target: &ast::Expr<'a>) {
        match target {
            ast::Expr::Var(v) => {
                self.queries_uses += usize::from(v.id == "queries");
                self.listing_uses += usize::from(LISTING_FUNCTIONS.contains(&v.id));
                self.store(v.id);
                self.bare.insert(v.id);
            }
            ast::Expr::List(l) => l.items.iter().for_each(|t| self.target(t)),
            ast::Expr::GetAttr(g) => match &g.expr {
                ast::Expr::Var(v) if !self.declared(v.id) => {
                    self.undeclared.insert(v.id);
                    self.assign(v.id);
                }
                ast::Expr::Var(_) => {}
                _ => {}
            },
            _ => {}
        }
    }

    fn opt(&mut self, e: &Option<ast::Expr<'a>>) {
        if let Some(e) = e {
            self.expr(e);
        }
    }

    fn arg(&mut self, a: &ast::CallArg<'a>) {
        match a {
            ast::CallArg::Pos(e)
            | ast::CallArg::Kwarg(_, e)
            | ast::CallArg::PosSplat(e)
            | ast::CallArg::KwargSplat(e) => self.expr(e),
        }
    }

    /// `queries.NAME` / `queries['NAME']` read by literal name.
    fn query_read(&mut self, base: &ast::Expr<'a>, name: &str) {
        if matches!(base, ast::Expr::Var(v) if v.id == "queries") {
            self.queries.insert(name.to_string());
            self.queries_literal += 1;
        }
    }

    fn call(&mut self, call: &ast::Call<'a>) {
        if let ast::Expr::Var(v) = &call.expr {
            self.read(v.id, true);
            let literal = match call.args.as_slice() {
                [ast::CallArg::Pos(ast::Expr::Const(c))] => c.value.as_str(),
                _ => None,
            };
            if LISTING_FUNCTIONS.contains(&v.id) {
                if let Some(pattern) = literal {
                    self.listings.insert(Listing {
                        function: v.id.to_string(),
                        pattern: pattern.to_string(),
                    });
                    self.listing_literal += 1;
                }
            }
            if !self.is_local(v.id) && matches!(v.id, "filter" | "filter_date_range") {
                let column = match call.args.first() {
                    Some(ast::CallArg::Pos(ast::Expr::Const(c))) => {
                        c.value.as_str().map(str::to_string)
                    }
                    _ => None,
                };
                self.filter_calls.push(FilterCall {
                    function: v.id.to_string(),
                    column,
                });
            }
        } else {
            self.expr(&call.expr);
        }
        call.args.iter().for_each(|a| self.arg(a));
    }

    pub(crate) fn expr(&mut self, expr: &ast::Expr<'a>) {
        match expr {
            ast::Expr::Var(v) => self.read(v.id, false),
            ast::Expr::Const(_) => {}
            ast::Expr::Slice(s) => {
                self.expr(&s.expr);
                self.opt(&s.start);
                self.opt(&s.stop);
                self.opt(&s.step);
            }
            ast::Expr::UnaryOp(o) => self.expr(&o.expr),
            ast::Expr::BinOp(o) => {
                use ast::BinOpKind as K;
                match o.op {
                    K::Add | K::Sub | K::Mul | K::Div | K::FloorDiv | K::Rem | K::Pow => {
                        self.arithmetic = true
                    }
                    K::Lt | K::Lte | K::Gt | K::Gte => {
                        let op = match o.op {
                            K::Lt => "<",
                            K::Lte => "<=",
                            K::Gt => ">",
                            _ => ">=",
                        };
                        self.orderings.push(OrderingSite { left: extent(&o.left), op, right: extent(&o.right) });
                    }
                    K::Concat => self.concat = true,
                    K::Eq | K::Ne | K::ScAnd | K::ScOr | K::In => {}
                }
                self.expr(&o.left);
                self.expr(&o.right);
            }
            ast::Expr::Compare(c) => {
                use ast::CompareOpKind as K;
                // A single comparison parses as a `BinOp`; `Compare` is a chain.
                if c.ops.iter().any(|o| matches!(o.op, K::Lt | K::Lte | K::Gt | K::Gte)) {
                    self.ordering_chained = true;
                }
                self.expr(&c.expr);
                c.ops.iter().for_each(|o| self.expr(&o.expr));
            }
            ast::Expr::IfExpr(i) => {
                self.expr(&i.test_expr);
                self.expr(&i.true_expr);
                self.opt(&i.false_expr);
            }
            ast::Expr::Filter(f) => {
                self.opt(&f.expr);
                f.args.iter().for_each(|a| self.arg(a));
            }
            ast::Expr::Test(t) => {
                self.expr(&t.expr);
                t.args.iter().for_each(|a| self.arg(a));
            }
            ast::Expr::GetAttr(g) => {
                self.query_read(&g.expr, g.name);
                self.expr(&g.expr);
            }
            ast::Expr::GetItem(g) => {
                if let ast::Expr::Const(key) = &g.subscript_expr {
                    if let Some(key) = key.value.as_str() {
                        self.query_read(&g.expr, key);
                    }
                }
                self.expr(&g.expr);
                self.expr(&g.subscript_expr);
            }
            ast::Expr::Call(c) => self.call(c),
            ast::Expr::List(l) => l.items.iter().for_each(|e| self.expr(e)),
            ast::Expr::Map(m) => {
                for (k, v) in m.keys.iter().zip(&m.values) {
                    self.expr(k);
                    self.expr(v);
                }
            }
        }
    }
}

/// Names an expression reads (a callee included).
fn expr_names<'a>(expr: &ast::Expr<'a>) -> HashSet<&'a str> {
    let mut w = Walk::new(HashSet::new());
    w.expr(expr);
    w.undeclared.union(&w.bare).copied().collect()
}

/// Names an assignment target binds (`ns` for `ns.attr`).
fn target_names<'a>(target: &ast::Expr<'a>) -> Vec<&'a str> {
    match target {
        ast::Expr::Var(v) => vec![v.id],
        ast::Expr::List(l) => l.items.iter().flat_map(target_names).collect(),
        ast::Expr::GetAttr(g) => target_names(&g.expr),
        _ => vec![],
    }
}

/// Every name a frame references or binds at its own level, not descending
/// into the inner frames (loop, macro, with, set-block, filter-block and
/// autoescape bodies); `if` branches share their frame.
fn level_names<'a>(body: &[ast::Stmt<'a>]) -> HashSet<&'a str> {
    let mut names = HashSet::new();
    for stmt in body {
        match stmt {
            ast::Stmt::EmitExpr(e) => names.extend(expr_names(&e.expr)),
            ast::Stmt::Set(s) => {
                names.extend(expr_names(&s.expr));
                names.extend(target_names(&s.target));
            }
            ast::Stmt::SetBlock(s) => names.extend(target_names(&s.target)),
            ast::Stmt::IfCond(i) => {
                names.extend(expr_names(&i.expr));
                names.extend(level_names(&i.true_body));
                names.extend(level_names(&i.false_body));
            }
            ast::Stmt::ForLoop(f) => names.extend(expr_names(&f.iter)),
            ast::Stmt::WithBlock(w) => {
                names.extend(w.assignments.iter().flat_map(|(_, e)| expr_names(e)));
            }
            ast::Stmt::AutoEscape(a) => names.extend(expr_names(&a.enabled)),
            ast::Stmt::FilterBlock(f) => names.extend(expr_names(&f.filter)),
            ast::Stmt::Macro(m) => {
                names.insert(m.name);
                m.defaults.iter().for_each(|d| names.extend(expr_names(d)));
            }
            ast::Stmt::CallBlock(c) => {
                let mut w = Walk::new(HashSet::new());
                w.call(&c.call);
                names.extend(w.undeclared.union(&w.bare));
            }
            _ => {}
        }
    }
    names
}


/// The byte range an expression covers in its source. A postfix node (`a.b`, `a[0]`,
/// `f(x)`, `x | f`) carries the span of its last operator only, so the extent is taken
/// over the whole subtree.
fn extent(expr: &ast::Expr<'_>) -> (u32, u32) {
    let own = expr.span();
    let mut range = (own.start_offset, own.end_offset);
    let mut take = |e: &ast::Expr<'_>| {
        let (start, end) = extent(e);
        range = (range.0.min(start), range.1.max(end));
    };
    let arg = |a: &ast::CallArg<'_>| match a {
        ast::CallArg::Pos(e) | ast::CallArg::Kwarg(_, e) | ast::CallArg::PosSplat(e) | ast::CallArg::KwargSplat(e) => {
            extent(e)
        }
    };
    let mut children: Vec<(u32, u32)> = Vec::new();
    match expr {
        ast::Expr::Var(_) | ast::Expr::Const(_) => {}
        ast::Expr::Slice(x) => {
            take(&x.expr);
            [&x.start, &x.stop, &x.step].into_iter().flatten().for_each(&mut take);
        }
        ast::Expr::UnaryOp(x) => take(&x.expr),
        ast::Expr::BinOp(x) => {
            take(&x.left);
            take(&x.right);
        }
        ast::Expr::Compare(x) => {
            take(&x.expr);
            x.ops.iter().for_each(|o| take(&o.expr));
        }
        ast::Expr::IfExpr(x) => {
            take(&x.test_expr);
            take(&x.true_expr);
            if let Some(f) = &x.false_expr {
                take(f);
            }
        }
        ast::Expr::Filter(x) => {
            if let Some(e) = &x.expr {
                take(e);
            }
            children.extend(x.args.iter().map(arg));
        }
        ast::Expr::Test(x) => {
            take(&x.expr);
            children.extend(x.args.iter().map(arg));
        }
        ast::Expr::GetAttr(x) => take(&x.expr),
        ast::Expr::GetItem(x) => {
            take(&x.expr);
            take(&x.subscript_expr);
        }
        ast::Expr::Call(x) => {
            take(&x.expr);
            children.extend(x.args.iter().map(arg));
        }
        ast::Expr::List(x) => x.items.iter().for_each(&mut take),
        ast::Expr::Map(x) => x.keys.iter().chain(&x.values).for_each(&mut take),
    }
    children.into_iter().fold(range, |(lo, hi), (start, end)| (lo.min(start), hi.max(end)))
}
