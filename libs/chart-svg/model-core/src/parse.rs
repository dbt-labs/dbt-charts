//! The parse context: the model path, the collected errors, the [`SourceMap`].

use diagnostic::{diag, py_repr, Diagnostic, ModelPath, SourceRange};
use indexmap::IndexMap;
use serde_json::Value;
use yaml::{Entry, Kind, Node, Span};

use crate::FromNode;

/// One step of a model path.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Seg {
    Key(String),
    Index(usize),
}

/// Renders a path the way Python's error paths spell it: `style.legend.items.2`; the
/// document itself is the empty string.
pub fn render_path(path: &[Seg]) -> String {
    let mut out = String::new();
    for seg in path {
        match seg {
            Seg::Key(k) => {
                if !out.is_empty() {
                    out.push('.');
                }
                out.push_str(k);
            }
            Seg::Index(i) => {
                if !out.is_empty() {
                    out.push('.');
                }
                out.push_str(&i.to_string());
            }
        }
    }
    out
}

/// Where each parsed value and each map key sits in the source, by model path.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct SourceMap {
    values: IndexMap<String, Span>,
    keys: IndexMap<String, Span>,
}

impl SourceMap {
    /// The span of the value at `path`.
    pub fn value(&self, path: &str) -> Option<Span> {
        self.values.get(path).copied()
    }

    /// The span of the key that introduced `path`.
    pub fn key(&self, path: &str) -> Option<Span> {
        self.keys.get(path).copied()
    }
}

/// What a parse error tells the author beyond its code and params.
#[derive(Debug, Clone, PartialEq)]
pub enum Hint {
    /// An unknown key: the keys allowed there, the nearest, and the other
    /// places in the model tree where the key is valid (`""` is the root).
    Keys {
        allowed: Vec<String>,
        nearest: Option<String>,
        valid_at: Vec<String>,
    },
    /// An unknown tag: the valid tags and the nearest.
    Tags {
        valid: Vec<String>,
        nearest: Option<String>,
    },
    /// A key a past grammar retired.
    Retired(Retirement),
}

/// What the migration registry knows about a retired key.
#[derive(Debug, Clone, PartialEq)]
pub struct Retirement {
    /// Where the key's value lives now, as a dotted document path; `None` when it was removed.
    pub successor: Option<String>,
    /// The grammar version that retired it.
    pub retired_in: String,
    /// The old spelling relative to where it sits (`zero`, or `a.b` when a move crosses levels).
    pub old: String,
    /// The map the old spelling sits in, `root` for the document.
    pub parent: String,
    /// What became of it (`was renamed to 'threshold' in 0.7.0.`).
    pub history: String,
    /// What to do about it.
    pub action: String,
}

/// The migration registry's side of an unknown key. Parse asks it before it
/// reports `ERR-EXTRA-FIELD`.
pub trait Retired {
    /// `parent` is the model path of the map the key sits in.
    fn lookup(&self, parent: &str, key: &str) -> Option<Retirement>;
}

#[derive(Debug, Clone, PartialEq)]
pub struct ParseError {
    /// Carries the model path, the byte range and the other ranges it names.
    pub diagnostic: Diagnostic,
    /// The same range with its line and column.
    pub span: Span,
    pub hint: Option<Hint>,
}

const YAML_REFERENCE_URL: &str = "https://docs.dbtcharts.com/reference/yaml-reference/";
const MAX_ALLOWED_KEYS: usize = 40;
const MAX_MISPLACED_HOMES: usize = 3;

impl ParseError {
    /// The diagnostic with what the author is told beyond its code: for an unknown key, the
    /// generic form of Python's hint (the keys allowed there, the nearest, the blocks that do take the key) and fields
    /// (`unknown_field`, `parent_path`, `allowed_keys`, `docs_topic`, `suggestion`).
    pub fn into_diagnostic(self) -> Diagnostic {
        let (
            Some(Hint::Keys {
                mut allowed,
                nearest,
                valid_at,
            }),
            Some(path),
        ) = (self.hint, self.diagnostic.path.clone())
        else {
            return self.diagnostic;
        };
        let (field, parent) = match path.0.split_last() {
            Some((field, parent)) => (field.clone(), parent.to_vec()),
            None => return self.diagnostic,
        };
        allowed.sort();
        let shown = if parent.is_empty() {
            "root".to_string()
        } else {
            parent.join(".")
        };
        let topic = match path.0.first().map(String::as_str) {
            Some("variables") => "variables",
            Some("queries") => "queries",
            Some("charts") => "charts",
            Some("rows" | "cols" | "grid" | "tabs") => "layout",
            _ => "board",
        };
        let at = format!(
            "Unknown field {} at {shown}.",
            diagnostic::py_str_repr(&field)
        );
        let hint = if allowed.is_empty() {
            format!("{at} See: {YAML_REFERENCE_URL}")
        } else {
            let more = if allowed.len() > MAX_ALLOWED_KEYS {
                ", ..."
            } else {
                ""
            };
            let keys = allowed
                .iter()
                .take(MAX_ALLOWED_KEYS)
                .cloned()
                .collect::<Vec<_>>();
            let mut parts = vec![at, format!("Allowed keys: {}{more}.", keys.join(", "))];
            if let Some(n) = &nearest {
                parts.push(format!("Did you mean {}?", diagnostic::py_str_repr(n)));
            }
            if !valid_at.is_empty() {
                let homes: Vec<String> = valid_at
                    .iter()
                    .take(MAX_MISPLACED_HOMES)
                    .map(|h| format!("`{h}`"))
                    .collect();
                parts.push(format!(
                    "`{field}` belongs at {}, not at `{shown}`.",
                    homes.join(" or ")
                ));
            }
            parts.push(format!("See: {YAML_REFERENCE_URL}"));
            parts.join(" ")
        };
        let mut d = self
            .diagnostic
            .with_sink_param("unknown_field", field.into())
            .with_sink_param("parent_path", shown.into())
            .with_sink_param("allowed_keys", allowed.into())
            .with_sink_param("docs_topic", topic.into());
        if let Some(n) = nearest {
            d = d.with_sink_param("suggestion", n.into());
        }
        d.with_hint(hint)
    }

    /// The model path the error is about; the document itself is `""`.
    pub fn path(&self) -> String {
        self.diagnostic
            .path
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default()
    }
}

/// `root` for the document, else the dotted path.
fn display_parent(parent: &str) -> &str {
    if parent.is_empty() {
        "root"
    } else {
        parent
    }
}

/// What to try for an unknown key: where it is, what is allowed there, the near miss, and the
/// other blocks that take the key.
fn unknown_key_hint(
    key: &str,
    parent: &str,
    allowed: &[String],
    nearest: Option<&str>,
    valid_at: &[String],
) -> String {
    let mut hint = format!("Unknown field '{key}' at {}.", display_parent(parent));
    if !allowed.is_empty() {
        let shown: Vec<&str> = allowed.iter().take(40).map(String::as_str).collect();
        let more = if allowed.len() > 40 { ", ..." } else { "" };
        hint.push_str(&format!(" Allowed keys: {}{more}.", shown.join(", ")));
    }
    if let Some(near) = nearest {
        hint.push_str(&format!(" Did you mean '{near}'?"));
    }
    if !valid_at.is_empty() {
        let places: Vec<&str> = valid_at
            .iter()
            .map(|p| if p.is_empty() { "root" } else { p.as_str() })
            .collect();
        hint.push_str(&format!(" The key is also valid at {}.", places.join(", ")));
    }
    hint
}

fn range(span: Span) -> SourceRange {
    SourceRange::new(span.start, span.end)
}

/// A parse result: the twin (partial when `errors` is not empty), the errors
/// and the source map.
#[derive(Debug)]
pub struct Parse<A> {
    pub twin: A,
    pub errors: Vec<ParseError>,
    pub source_map: SourceMap,
}

/// The tree walk that finds where a key is valid.
pub struct Walk<'a> {
    key: &'a str,
    path: Vec<Seg>,
    open: Vec<&'static str>,
    found: Vec<String>,
}

impl Walk<'_> {
    /// Enter a model: note it when it owns the key, then walk its fields.
    /// A model already open on this path is not entered again.
    pub fn model(&mut self, name: &'static str, keys: &[String], fields: impl FnOnce(&mut Self)) {
        if self.open.contains(&name) {
            return;
        }
        if keys.iter().any(|k| k == self.key) {
            self.found.push(render_path(&self.path));
        }
        self.open.push(name);
        fields(self);
        self.open.pop();
    }

    pub fn field(&mut self, name: &str, walk: impl FnOnce(&mut Self)) {
        self.path.push(Seg::Key(name.to_string()));
        walk(self);
        self.path.pop();
    }
}

/// The keys of one map being read. A reader claims the keys it owns; whatever
/// is left over is unknown.
pub struct MapRead<'n> {
    pub(crate) node: &'n Node,
    map: &'n IndexMap<String, Entry>,
    claimed: Vec<String>,
}

impl<'n> MapRead<'n> {
    pub fn new(node: &'n Node) -> Option<Self> {
        let Kind::Map(map) = &node.kind else {
            return None;
        };
        Some(MapRead {
            node,
            map,
            claimed: Vec::new(),
        })
    }

    /// The keys present that no reader has claimed yet.
    pub fn unclaimed(&self) -> impl Iterator<Item = &str> {
        self.map
            .keys()
            .filter(|k| !self.claimed.contains(k))
            .map(String::as_str)
    }

    /// Name a key this reader owns without reading it.
    pub fn ask(&mut self, key: &str) {
        if !self.claimed.iter().any(|k| k == key) {
            self.claimed.push(key.to_string());
        }
    }

    /// Every key a reader owns here, present or not.
    pub fn allowed(&self) -> &[String] {
        &self.claimed
    }

    /// Whether `key` is present, claimed or not.
    pub fn has(&self, key: &str) -> bool {
        self.map.contains_key(key)
    }

    pub fn entry(&self, key: &str) -> Option<&'n Entry> {
        self.map.get(key)
    }

    fn claim(&mut self, key: &str) -> Option<&'n Entry> {
        self.ask(key);
        self.map.get(key)
    }
}

/// The parse context threaded through every reader.
pub struct Cx<'a> {
    path: Vec<Seg>,
    errors: Vec<ParseError>,
    map: SourceMap,
    retired: Option<&'a dyn Retired>,
    root_walk: fn(&mut Walk),
}

impl<'a> Cx<'a> {
    /// A context for parsing `T` at the document root.
    pub fn new<T: FromNode>(retired: Option<&'a dyn Retired>) -> Self {
        Cx {
            path: Vec::new(),
            errors: Vec::new(),
            map: SourceMap::default(),
            retired,
            root_walk: T::walk,
        }
    }

    pub fn finish<A>(self, twin: A) -> Parse<A> {
        Parse {
            twin,
            errors: self.errors,
            source_map: self.map,
        }
    }

    pub fn path(&self) -> String {
        render_path(&self.path)
    }

    fn record(&mut self, node: &Node) {
        self.map.values.insert(self.path(), node.span);
    }

    /// Read `node` as the child `seg` of the current path.
    pub fn at<R>(&mut self, seg: Seg, node: &Node, read: impl FnOnce(&mut Self) -> R) -> R {
        self.path.push(seg);
        self.record(node);
        let out = read(self);
        self.path.pop();
        out
    }

    /// Read a map entry as the child `key` of the current path, recording its
    /// key span as well as its value span.
    pub fn entry<R>(&mut self, key: &str, entry: &Entry, read: impl FnOnce(&mut Self) -> R) -> R {
        self.path.push(Seg::Key(key.to_string()));
        self.map.keys.insert(self.path(), entry.key_span);
        self.record(&entry.value);
        let out = read(self);
        self.path.pop();
        out
    }

    /// Claim `key` in `m` and read its value as a child of the current path.
    /// `None` when the key is absent.
    pub fn field<R>(
        &mut self,
        m: &mut MapRead,
        key: &str,
        read: impl FnOnce(&Node, &mut Self) -> R,
    ) -> Option<R> {
        let entry = m.claim(key)?;
        self.path.push(Seg::Key(key.to_string()));
        self.map.keys.insert(self.path(), entry.key_span);
        self.record(&entry.value);
        let out = read(&entry.value, self);
        self.path.pop();
        Some(out)
    }

    /// Run `read`; if it reported errors, take them back and return them
    /// instead of the value.
    pub fn attempt<R>(&mut self, read: impl FnOnce(&mut Self) -> R) -> Result<R, Vec<ParseError>> {
        let before = self.errors.len();
        let out = read(self);
        if self.errors.len() > before {
            Err(self.errors.split_off(before))
        } else {
            Ok(out)
        }
    }

    pub fn push(&mut self, error: ParseError) {
        self.errors.push(error);
    }

    fn model_path(&self) -> ModelPath {
        ModelPath(
            self.path
                .iter()
                .map(|seg| match seg {
                    Seg::Key(k) => k.clone(),
                    Seg::Index(i) => i.to_string(),
                })
                .collect(),
        )
    }

    fn error(&mut self, diagnostic: Diagnostic, span: Span, hint: Option<Hint>) {
        let diagnostic = diagnostic.at(range(span)).at_path(self.model_path());
        self.errors.push(ParseError {
            diagnostic,
            span,
            hint,
        });
    }

    /// `ERR-VALIDATION-FIELD` at the current path.
    pub fn invalid(&mut self, span: Span, message: impl Into<String>) {
        let d = diag::err_validation_field(self.path(), message.into());
        self.error(d, span, None);
    }

    /// A value of the wrong kind at the current path.
    pub fn mismatch(&mut self, node: &Node, expected: &str) {
        let got = match &node.kind {
            Kind::Scalar(yaml::Value::Str(s)) => {
                format!(" (got: {})", py_repr(&Value::String(s.clone())))
            }
            _ => String::new(),
        };
        self.invalid(node.span, format!("Input should be {expected}{got}"));
    }

    /// A scalar where a mapping goes (`ERR-WRONG-SHAPE`), or any other
    /// non-mapping.
    pub fn not_a_map(&mut self, node: &Node, keys: &[String]) {
        if keys.is_empty() || !matches!(node.kind, Kind::Scalar(_)) {
            return self.mismatch(node, "a valid dictionary");
        }
        let d = diag::err_wrong_shape(self.path(), keys.join(", "));
        self.error(d, node.span, None);
    }

    /// Report every key of `m` no reader claimed.
    pub fn unknown_keys(&mut self, m: &MapRead) {
        let allowed = m.allowed();
        let Kind::Map(map) = &m.node.kind else { return };
        let parent = self.path();
        for key in m.unclaimed() {
            let Some(entry) = map.get(key) else { continue };
            self.path.push(Seg::Key(key.to_string()));
            let field_path = self.path();
            self.path.pop();
            let retired = self.retired.and_then(|r| r.lookup(&parent, key));
            let (d, hint) = match retired {
                Some(r) => {
                    let d = diag::err_retired_field(
                        r.old.as_str(),
                        r.parent.as_str(),
                        r.history.as_str(),
                    )
                    .with_sink_param("field_path", field_path.into())
                    .with_sink_param("unknown_field", key.into())
                    .with_sink_param("parent_path", display_parent(&parent).into())
                    .with_sink_param("retired_in", r.retired_in.as_str().into())
                    .with_hint(r.action.as_str());
                    let d = match &r.successor {
                        Some(s) => d.with_sink_param("successor", s.as_str().into()),
                        None => d,
                    };
                    (d, Hint::Retired(r))
                }
                None => {
                    let nearest = nearest(key, allowed);
                    let valid_at = self.valid_elsewhere(key, &parent);
                    let d = diag::err_extra_field(field_path).with_hint(unknown_key_hint(
                        key,
                        &parent,
                        allowed,
                        nearest.as_deref(),
                        &valid_at,
                    ));
                    let hint = Hint::Keys {
                        allowed: allowed.to_vec(),
                        nearest,
                        valid_at,
                    };
                    (d, hint)
                }
            };
            let at = ModelPath(
                self.model_path()
                    .0
                    .into_iter()
                    .chain([key.to_string()])
                    .collect(),
            );
            let diagnostic = d.at(range(entry.key_span)).at_path(at);
            self.errors.push(ParseError {
                diagnostic,
                span: entry.key_span,
                hint: Some(hint),
            });
        }
    }

    fn valid_elsewhere(&self, key: &str, here: &str) -> Vec<String> {
        let mut w = Walk {
            key,
            path: Vec::new(),
            open: Vec::new(),
            found: Vec::new(),
        };
        (self.root_walk)(&mut w);
        let mut found: Vec<String> = Vec::new();
        for p in w.found {
            if !is_here(&p, here) && !found.contains(&p) {
                found.push(p);
            }
        }
        found.sort_by_key(|p| p.len());
        found.truncate(3);
        found
    }

    /// A required key (a union's tag) is absent.
    pub fn missing(&mut self, span: Span, key: &str, hint: Hint) {
        self.path.push(Seg::Key(key.to_string()));
        let d = diag::err_validation_field(self.path(), "Field required".to_string());
        self.error(d, span, Some(hint));
        self.path.pop();
    }

    /// An unknown tag, listing the valid ones and the nearest.
    pub fn unknown_tag(&mut self, node: &Node, given: &str, valid: &[&str]) {
        let valid: Vec<String> = valid.iter().map(|s| s.to_string()).collect();
        let d = diag::err_validation_field(
            self.path(),
            format!(
                "Input should be {} (got: {})",
                one_of(&valid),
                py_repr(&Value::String(given.to_string()))
            ),
        );
        let hint = Hint::Tags {
            nearest: nearest(given, &valid),
            valid,
        };
        self.error(d, node.span, Some(hint));
    }

    /// Two or more keys of an exclusive group are present.
    pub fn exclusive(&mut self, m: &MapRead, keys: &[&str]) {
        let present: Vec<&Entry> = keys.iter().filter_map(|k| m.entry(k)).collect();
        let Some(second) = present.get(1) else { return };
        let names = keys
            .iter()
            .filter(|k| m.has(k))
            .map(|k| format!("`{k}`"))
            .collect::<Vec<_>>();
        let mut d = diag::err_validation_field(
            self.path(),
            format!("Only one of {} may be set", names.join(", ")),
        );
        for (name, e) in keys
            .iter()
            .zip(keys.iter().map(|k| m.entry(k)))
            .filter_map(|(n, e)| e.map(|e| (n, e)))
        {
            d = d.relating(range(e.key_span), *name);
        }
        self.error(d, second.key_span, None);
    }
}

/// Whether `pattern` (a walk path, where `*` is any map key) is the path `here`.
fn is_here(pattern: &str, here: &str) -> bool {
    let (mut a, mut b) = (pattern.split('.'), here.split('.'));
    loop {
        match (a.next(), b.next()) {
            (None, None) => return true,
            (Some(x), Some(y)) if x == "*" || x == y => {}
            _ => return false,
        }
    }
}

/// The comparison form of a name: case, spaces and hyphens ignored.
fn name_key(name: &str) -> String {
    let mut out = String::new();
    let mut gap = false;
    for c in name.trim().chars().flat_map(char::to_lowercase) {
        if c.is_whitespace() || c == '-' {
            gap = true;
        } else {
            if gap {
                out.push('_');
                gap = false;
            }
            out.push(c);
        }
    }
    if gap {
        out.push('_');
    }
    out
}

/// `difflib.get_close_matches(given, candidates, n=1, cutoff=0.6)` over
/// `name_key` forms, as `yaml_error_formatter.suggest_similar_value` calls it;
/// the candidate comes back in its original spelling.
pub fn nearest(given: &str, candidates: &[String]) -> Option<String> {
    let word: Vec<char> = name_key(given).chars().collect();
    let mut keyed: Vec<(String, &String)> = Vec::new();
    for c in candidates {
        let k = name_key(c);
        if !keyed.iter().any(|(seen, _)| *seen == k) {
            keyed.push((k, c));
        }
    }
    keyed
        .into_iter()
        .map(|(k, original)| (ratio(&k.chars().collect::<Vec<_>>(), &word), k, original))
        .filter(|(score, _, _)| *score >= 0.6)
        .max_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.cmp(&b.1)))
        .map(|(_, _, original)| original.clone())
}

/// `difflib.SequenceMatcher(None, a, b).ratio()` for strings under 200 chars.
fn ratio(a: &[char], b: &[char]) -> f64 {
    let total = a.len() + b.len();
    if total == 0 {
        return 1.0;
    }
    2.0 * matched(a, b, (0, a.len()), (0, b.len())) as f64 / total as f64
}

/// Characters in the matching blocks of `a[alo..ahi]` and `b[blo..bhi]`: the
/// longest match (earliest in `a`, then `b`), then both sides of it.
fn matched(
    a: &[char],
    b: &[char],
    (alo, ahi): (usize, usize),
    (blo, bhi): (usize, usize),
) -> usize {
    let (mut best_i, mut best_j, mut best_k) = (alo, blo, 0);
    let mut prev = vec![0usize; b.len() + 1];
    for i in alo..ahi {
        let mut row = vec![0usize; b.len() + 1];
        for j in blo..bhi {
            if a.get(i) != b.get(j) {
                continue;
            }
            let k = prev.get(j).copied().unwrap_or(0) + 1;
            if let Some(slot) = row.get_mut(j + 1) {
                *slot = k;
            }
            if k > best_k {
                (best_i, best_j, best_k) = (i + 1 - k, j + 1 - k, k);
            }
        }
        prev = row;
    }
    if best_k == 0 {
        return 0;
    }
    best_k
        + matched(a, b, (alo, best_i), (blo, best_j))
        + matched(a, b, (best_i + best_k, ahi), (best_j + best_k, bhi))
}

/// `'a', 'b' or 'c'`, as pydantic words a literal error.
fn one_of(valid: &[String]) -> String {
    let quoted: Vec<String> = valid.iter().map(|v| format!("'{v}'")).collect();
    match quoted.split_last() {
        Some((last, rest)) if !rest.is_empty() => format!("{} or {last}", rest.join(", ")),
        _ => quoted.join(""),
    }
}
