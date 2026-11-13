//! Another file's queries and charts: a chart's `file.yml#name`, a `queries:`, `variables:` or
//! `charts:` entry that is a ref (`file.queries.name`, `file.variables.name`, `file.charts.name`),
//! each read from the file the host holds (Python's `resolve_external_query`, `load_from_reference`
//! and `_load_chart_ref_with_lexical_queries`). Only the named entry is read: the rest of that file is
//! not this board's to judge.
//!
//! A chart a ref names stands in the importing board under its local name, but its `query:` names
//! mean the queries of the file that declares it (lexical scope): [`Origin`] keeps that file, and
//! `hoist` hoists each such query under an id carrying it.

use crate::compose::{check_composed, composed_queries};
use crate::files::ProjectFiles;
use crate::hoist::{check_layers, HoistError};
use crate::meta::merged_patch;
use crate::sources::SourceUse;
use board_model::ids::{ChartRef, QueryId, QueryName, QueryRef, VariableRef};
use board_model::query::{Query, QueryAuthored};
use diagnostic::{diag, py_str_repr};
use indexmap::IndexMap;
use migrate::{read_board, Date};
use model_core::{parse_patch, Leaf, Tri};
use project::ProjectPath;
use std::collections::{BTreeMap, BTreeSet};

type Entries = IndexMap<String, yaml::Entry>;
type QueryPatch = <Query as Leaf>::Patch;

/// Where another file's queries are read from.
pub(crate) struct External<'a> {
    pub(crate) files: &'a dyn ProjectFiles,
    pub(crate) today: Date,
}

/// What a cross-file ref points at.
#[derive(Clone, Copy)]
enum RefKind {
    Query,
    Variable,
    Chart,
}

impl RefKind {
    fn section(self) -> &'static str {
        match self {
            Self::Query => "queries",
            Self::Variable => "variables",
            Self::Chart => "charts",
        }
    }

    fn singular(self) -> &'static str {
        match self {
            Self::Query => "query",
            Self::Variable => "variable",
            Self::Chart => "chart",
        }
    }

    /// The name and file of a ref, when it reads as one of this kind.
    fn parse(self, text: &str) -> Result<(String, String), String> {
        match self {
            Self::Query => QueryRef::try_from(text)
                .map(|r| (r.r#ref.file_part().to_string(), r.r#ref.name().to_string())),
            Self::Variable => VariableRef::try_from(text)
                .map(|r| (r.r#ref.file_part().to_string(), r.r#ref.name().to_string())),
            Self::Chart => ChartRef::try_from(text)
                .map(|r| (r.r#ref.file_part().to_string(), r.r#ref.name().to_string())),
        }
    }
}

/// The directory a ref is anchored at: the compiling board's, `.` at the project root.
pub(crate) fn dir_of(board: &ProjectPath) -> &str {
    board.as_str().rsplit_once('/').map_or("", |(d, _)| d)
}

fn unresolved(spec: &str, context: String) -> HoistError {
    HoistError::UnresolvedReference(Box::new(diag::err_unresolved_reference(
        spec,
        format!(" in {context}"),
    )))
}

impl External<'_> {
    /// `file` anchored at `dir`, read and migrated: the file's path and its top-level mapping.
    /// Python's `resolve_ref` and the front of `read_section`.
    pub(crate) fn open(
        &self,
        spec: &str,
        dir: &str,
        file: &str,
    ) -> Result<(ProjectPath, yaml::Node), HoistError> {
        let dir_name = if dir.is_empty() { "." } else { dir };
        let path = ProjectPath::anchored(dir, file)
            .map_err(|e| unresolved(spec, format!("{} \u{2014} {e}", py_str_repr(dir_name))))?;
        let text = self.files.read(&path)?.ok_or_else(|| {
            unresolved(
                spec,
                format!(
                    "{} \u{2014} {} does not exist",
                    py_str_repr(dir_name),
                    py_str_repr(path.as_str())
                ),
            )
        })?;
        let node = yaml::parse_any(&text).map_err(|mut diagnostics| {
            // Python names the file the text came from, and the ref that led to it.
            for d in &mut diagnostics {
                if d.params.contains_key("source") {
                    d.params.insert(
                        "source",
                        format!("{path} (the file '{spec}' resolved to)").into(),
                    );
                }
            }
            HoistError::File(diagnostics)
        })?;
        let node = read_board(node, self.today).node;
        if !matches!(node.kind, yaml::Kind::Map(_)) {
            let why = format!("{} \u{2014} not a YAML mapping", py_str_repr(path.as_str()));
            return Err(unresolved(spec, why));
        }
        Ok((path, node))
    }

    /// The `queries:` or `variables:` mapping of an opened file.
    fn section<'n>(
        kind: &str,
        spec: &str,
        path: &ProjectPath,
        file: &'n yaml::Node,
    ) -> Result<&'n indexmap::IndexMap<String, yaml::Entry>, HoistError> {
        let section = match &file.kind {
            yaml::Kind::Map(top) => top.get(kind).map(|e| &e.value.kind),
            _ => None,
        };
        match section {
            Some(yaml::Kind::Map(section)) => Ok(section),
            _ => Err(unresolved(
                spec,
                format!(
                    "{} \u{2014} that file has no {kind}: section",
                    py_str_repr(path.as_str())
                ),
            )),
        }
    }

    /// `<file>#<name>` (Python's `resolve_external_query`): the file is anchored at the directory of
    /// the board being compiled, and the query is gathered into `out` under its printed id.
    pub(crate) fn query(
        &self,
        spec: &str,
        chart: &str,
        board: &ProjectPath,
        known: &std::collections::BTreeSet<String>,
        out: &mut Vec<(String, <Query as Leaf>::Patch)>,
        uses: &mut Vec<SourceUse>,
    ) -> Result<QueryId, HoistError> {
        let (file_part, name) = spec.split_once('#').unwrap_or((spec, ""));
        if file_part.is_empty() {
            return Err(HoistError::UnknownQuery(vec![diag::err_unknown_query(
                chart,
                spec,
                known.iter().cloned().collect::<Vec<String>>(),
            )]));
        }
        if name.is_empty() {
            return Err(HoistError::refused(diag::err_query_ref_invalid(spec)));
        }
        let (path, file) = self.open(spec, dir_of(board), file_part)?;
        let section = Self::section("queries", spec, &path, &file)?;
        let Some(entry) = section.get(name) else {
            let available: Vec<&str> = section.keys().map(String::as_str).collect();
            let available = if available.is_empty() {
                "none".to_string()
            } else {
                available.join(", ")
            };
            return Err(HoistError::refused(diag::err_query_not_in_file(
                name, file_part, available,
            )));
        };
        let parsed = parse_patch::<Query>(&entry.value, None);
        if let Some(e) = parsed.errors.first() {
            return Err(HoistError::Message(format!(
                "'{spec}': query '{name}' in {path} is invalid: {}",
                e.diagnostic.code
            )));
        }
        let query = parsed.twin;
        // Python passes this query no default source: it names its own or the board is refused.
        if let Some(QueryAuthored::Sql(q)) = &query {
            if matches!(q.base.source, Tri::Unset | Tri::Null) {
                return Err(source_required(name));
            }
        }
        let id = QueryId::new(
            path,
            QueryName::try_from(name).map_err(HoistError::QueryId)?,
        )
        .map_err(HoistError::QueryId)?;
        let key = id.to_string();
        // Python normalizes it with the compiling board's directory, not the file's.
        uses.extend(SourceUse::of(name, &query, Some(dir_of(board))));
        if !out.iter().any(|(k, _)| *k == key) {
            out.push((key, query));
        }
        Ok(id)
    }

    /// The entry a `queries:` or `variables:` ref (`file.queries.name`, `.yml` implied when the file
    /// has no suffix) names: its node, which stands in the importing board under the local name.
    fn referenced(&self, spec: &str, dir: &str, kind: RefKind) -> Result<yaml::Node, HoistError> {
        let (file, name) = kind.parse(spec).map_err(HoistError::Message)?;
        let name = name.as_str();
        let (path, opened) = self.open(spec, dir, &with_suffix(file))?;
        let section = Self::section(kind.section(), spec, &path, &opened)?;
        match section.get(name) {
            Some(entry) => {
                let mut query = entry.value.clone();
                if matches!(kind, RefKind::Query) {
                    stamp_declaring_source(
                        &mut query,
                        name,
                        self.declared_source(&path, &opened)?,
                    )?;
                }
                Ok(query)
            }
            None => Err(unresolved(
                spec,
                format!(
                    "{} \u{2014} no {} named {} there",
                    py_str_repr(path.as_str()),
                    kind.singular(),
                    py_str_repr(name)
                ),
            )),
        }
    }

    /// The boards a layout nests: `rows` and `cols` items, `grid.items[].item`; a tab is not a
    /// board (it has no `queries:`), so only the layout inside it is walked.
    fn resolve_nested(
        &self,
        m: &mut Entries,
        board: &ProjectPath,
        imports: &mut Imports,
    ) -> Result<(), HoistError> {
        for key in ["rows", "cols"] {
            if let Some(yaml::Kind::Seq(items)) = m.get_mut(key).map(|e| &mut e.value.kind) {
                for item in items {
                    self.resolve_refs(item, board, imports)?;
                }
            }
        }
        for (key, is_grid) in [("grid", true), ("tabs", false)] {
            let Some(yaml::Kind::Map(layout)) = m.get_mut(key).map(|e| &mut e.value.kind) else {
                continue;
            };
            let Some(yaml::Kind::Seq(items)) = layout.get_mut("items").map(|e| &mut e.value.kind)
            else {
                continue;
            };
            for item in items {
                let yaml::Kind::Map(cell) = &mut item.kind else {
                    continue;
                };
                match is_grid {
                    true => {
                        if let Some(e) = cell.get_mut("item") {
                            self.resolve_refs(&mut e.value, board, imports)?;
                        }
                    }
                    false => self.resolve_nested(cell, board, imports)?,
                }
            }
        }
        Ok(())
    }

    /// Replaces every `queries:`, `variables:` and `charts:` entry of the board `node` (and of the
    /// boards nested in its layout) that is a ref with the entry it names, under the entry's own
    /// name, as Python registers it. A chart's file is kept in `imports` under its local name.
    pub(crate) fn resolve_refs(
        &self,
        node: &mut yaml::Node,
        board: &ProjectPath,
        imports: &mut Imports,
    ) -> Result<(), HoistError> {
        let dir = dir_of(board);
        let yaml::Kind::Map(m) = &mut node.kind else {
            return Ok(());
        };
        for kind in [RefKind::Query, RefKind::Variable, RefKind::Chart] {
            let section = m.get_mut(kind.section()).map(|e| &mut e.value.kind);
            if let Some(yaml::Kind::Map(entries)) = section {
                for (name, e) in entries.iter_mut() {
                    let Some(spec) = ref_of(&e.value, kind) else {
                        continue;
                    };
                    e.value = match kind {
                        RefKind::Chart => {
                            let (chart, origin) = self.imported_chart(&spec, dir)?;
                            imports.charts.insert(name.clone(), origin);
                            chart
                        }
                        _ => {
                            match kind {
                                RefKind::Query => imports.queries.insert(name.clone()),
                                _ => imports.variables.insert(name.clone()),
                            };
                            self.referenced(&spec, dir, kind)?
                        }
                    };
                }
            }
        }
        self.resolve_nested(m, board, imports)?;
        Ok(())
    }

    /// The chart a `charts:` ref names (Python's `_load_chart_ref_with_lexical_queries`): a chart
    /// that is itself a ref is followed, each hop anchored at the directory of the file it was
    /// read in, and the file that finally declares the chart is its origin.
    fn imported_chart(&self, spec: &str, dir: &str) -> Result<(yaml::Node, Origin), HoistError> {
        let (mut hop, mut hop_dir) = (spec.to_string(), dir.to_string());
        let mut visited: Vec<String> = Vec::new();
        let (chart, name, path, file) = loop {
            let (file_part, name) = RefKind::Chart.parse(&hop).map_err(HoistError::Message)?;
            let (path, file) = self.open(&hop, &hop_dir, &with_suffix(file_part))?;
            let charts = Self::section("charts", &hop, &path, &file)?;
            let Some(entry) = charts.get(&name) else {
                return Err(unresolved(
                    &hop,
                    format!(
                        "{} \u{2014} no chart named {} there",
                        py_str_repr(path.as_str()),
                        py_str_repr(&name)
                    ),
                ));
            };
            let Some(next) = ref_of(&entry.value, RefKind::Chart) else {
                break (entry.value.clone(), name, path, file);
            };
            let site = format!("{path}.charts.{name}");
            if visited.contains(&site) {
                visited.push(site);
                let chain = visited.join(" -> ");
                return Err(unresolved(spec, format!("circular chart refs: {chain}")));
            }
            visited.push(site);
            hop_dir = dir_of(&path).to_string();
            hop = next;
        };
        let source = self.declared_source(&path, &file)?;
        let origin = Origin {
            spec: spec.to_string(),
            path,
            file,
            source,
            chart: chart.clone(),
            name,
        };
        Ok((chart, origin))
    }

    /// The board-level default source of a file (Python's `_board_level_default_source`): its own
    /// `source:`, else the one its `meta.yml` chain sets (its `extends:` is not consulted).
    ///
    /// Python parses that chain as authored layers here, so each is held to the query shapes
    /// (`check_layers`). Only a file with no `source:` of its own reads it. A board's own
    /// `file#query` ref and a variable ref never ask for the file's source.
    fn declared_source(
        &self,
        path: &ProjectPath,
        file: &yaml::Node,
    ) -> Result<Option<String>, HoistError> {
        let own = match &file.kind {
            yaml::Kind::Map(top) => top.get("source").map(|e| &e.value.kind),
            _ => None,
        };
        let kind = match own {
            None | Some(yaml::Kind::Scalar(yaml::Value::Null)) => {
                return self.meta_source(path);
            }
            Some(kind) => kind,
        };
        match kind {
            yaml::Kind::Scalar(yaml::Value::Str(s)) => Ok(Some(s.clone())),
            other => {
                let got = match other {
                    yaml::Kind::Scalar(yaml::Value::Bool(_)) => "bool",
                    yaml::Kind::Scalar(yaml::Value::Number(n)) if n.is_f64() => "float",
                    yaml::Kind::Scalar(yaml::Value::Number(_)) => "int",
                    yaml::Kind::Scalar(_) => "str",
                    yaml::Kind::Seq(_) => "list",
                    yaml::Kind::Map(_) => "dict",
                };
                Err(HoistError::refused(diag::err_board_source_invalid(
                    path.as_str(),
                    got,
                )))
            }
        }
    }

    /// The `source:` the `meta.yml` chain above `path` sets: the fold the compiling board takes,
    /// so a deeper meta's invalid or null `source:` is judged as it is there.
    fn meta_source(&self, path: &ProjectPath) -> Result<Option<String>, HoistError> {
        let merged = merged_patch(Default::default(), path, ".", self.files, self.today)
            .map_err(|e| HoistError::Meta(Box::new(e)))?;
        check_layers(&merged.layers)?;
        Ok(match merged.patch.body.source {
            Tri::Value(s) => Some(s),
            Tri::Unset | Tri::Null => None,
        })
    }

    /// The id an imported chart's `query:` name stands for (Python's `resolve_name`): a bare name is
    /// a query of the chart's own file, `<file>#<name>` one of the file it names, reached from that
    /// chart's directory; the query, and the ones it composes or refs, are gathered into `out`
    /// under ids that carry the file they are declared in.
    pub(crate) fn imported_query(
        &self,
        name: &str,
        origin: &Origin,
        out: &mut Vec<(String, QueryPatch)>,
        uses: &mut Vec<SourceUse>,
    ) -> Result<QueryId, HoistError> {
        let name = name.trim();
        let mut walk = Walk {
            chart_ref: &origin.spec,
            out,
            uses,
            resolving: BTreeSet::new(),
        };
        let Some((file_part, query)) = name.split_once('#') else {
            let queries = match &origin.file.kind {
                yaml::Kind::Map(top) => top.get("queries").and_then(|e| match &e.value.kind {
                    yaml::Kind::Map(q) => Some(q),
                    _ => None,
                }),
                _ => None,
            };
            let source = origin.source.as_deref();
            return self.walk_query(name, &origin.path, queries, source, &mut walk);
        };
        if file_part.is_empty() || query.is_empty() {
            return Err(HoistError::refused(diag::err_query_ref_invalid(name)));
        }
        let (path, file) = self.open(name, dir_of(&origin.path), file_part)?;
        let queries = Self::section("queries", &origin.spec, &path, &file)?;
        let source = self.declared_source(&path, &file)?;
        self.walk_query(query, &path, Some(queries), source.as_deref(), &mut walk)
    }

    /// The query each `queries.<name>` an imported chart's display text reads stands for (Python's
    /// `text_query_keys`): the chart's own file declares it, or a `<file>#<name>` ref reaches it.
    /// A bare name that file does not declare is `ERR-TEXT-UNKNOWN-QUERY`, whatever the importer
    /// declares.
    pub(crate) fn imported_text_queries(
        &self,
        origin: &Origin,
        out: &mut Vec<(String, QueryPatch)>,
        uses: &mut Vec<SourceUse>,
    ) -> Result<BTreeMap<String, QueryId>, HoistError> {
        let declared: Vec<String> = match &origin.file.kind {
            yaml::Kind::Map(top) => match top.get("queries").map(|e| &e.value.kind) {
                Some(yaml::Kind::Map(q)) => {
                    let mut names: Vec<String> = q.keys().cloned().collect();
                    names.sort();
                    names
                }
                _ => Vec::new(),
            },
            _ => Vec::new(),
        };
        let mut read = BTreeMap::new();
        for (field, text) in display_texts(&origin.chart) {
            if !text.contains("queries") || !(text.contains("{{") || text.contains("{%")) {
                continue;
            }
            let Ok(info) = template::parse(&text, template::Kind::Label) else {
                continue;
            };
            for name in info.queries {
                if !name.contains('#') && !declared.contains(&name) {
                    return Err(HoistError::refused(diag::err_text_unknown_query(
                        format!("Chart '{}' {field}", origin.name),
                        name,
                        declared,
                    )));
                }
                if let std::collections::btree_map::Entry::Vacant(slot) = read.entry(name.clone()) {
                    slot.insert(self.imported_query(&name, origin, out, uses)?);
                }
            }
        }
        Ok(read)
    }

    /// Query `name` of the file at `path` (Python's `_resolve_cross_board_query`): `queries` is that
    /// file's `queries:` mapping and `source` its default source.
    fn walk_query(
        &self,
        name: &str,
        path: &ProjectPath,
        queries: Option<&Entries>,
        source: Option<&str>,
        walk: &mut Walk<'_>,
    ) -> Result<QueryId, HoistError> {
        let query_name = QueryName::try_from(name).map_err(HoistError::QueryId)?;
        let id = QueryId::new(path.clone(), query_name).map_err(HoistError::QueryId)?;
        let key = id.to_string();
        // Done, or being resolved: a composition cycle ends here, and `refuse_cycles` names it once
        // the queries are gathered.
        if walk.resolving.contains(&key) || walk.out.iter().any(|(k, _)| *k == key) {
            return Ok(id);
        }
        let Some(entry) = queries.and_then(|q| q.get(name)) else {
            return Err(HoistError::refused(diag::err_chart_ref_query_undeclared(
                walk.chart_ref,
                name,
                path.as_str(),
            )));
        };
        walk.resolving.insert(key.clone());
        let query = match query_ref_of(&entry.value) {
            Some(target) => {
                let (target_file, target_name) =
                    RefKind::Query.parse(&target).map_err(HoistError::Message)?;
                let (target_path, file) =
                    self.open(&target, dir_of(path), &with_suffix(target_file))?;
                let section = Self::section("queries", walk.chart_ref, &target_path, &file)?;
                let target_source = self.declared_source(&target_path, &file)?;
                let target_id = QueryId::new(
                    target_path.clone(),
                    QueryName::try_from(target_name.as_str()).map_err(HoistError::QueryId)?,
                )
                .map_err(HoistError::QueryId)?;
                if walk.resolving.contains(&target_id.to_string()) {
                    return Err(HoistError::refused(diag::err_query_ref_cycle(
                        walk.chart_ref,
                        target_name.as_str(),
                        target_path.as_str(),
                    )));
                }
                let target_key = target_id.to_string();
                let had = walk.out.iter().any(|(k, _)| *k == target_key);
                self.walk_query(
                    &target_name,
                    &target_path,
                    Some(section),
                    target_source.as_deref(),
                    walk,
                )?;
                // The ref'd entry is the query it names, under its own id; the target itself is
                // gathered only when something else reads it.
                let at = walk.out.iter().position(|(k, _)| *k == target_key);
                let Some(at) = at else {
                    return Err(HoistError::Message(format!(
                        "query '{target_key}' was not gathered"
                    )));
                };
                match had {
                    true => walk.out.get(at).map(|(_, q)| q.clone()),
                    false => Some(walk.out.remove(at).1),
                }
                .ok_or_else(|| {
                    HoistError::Message(format!("query '{target_key}' was not gathered"))
                })?
            }
            None => self.direct_query(name, path, entry, queries, source, walk)?,
        };
        walk.resolving.remove(&key);
        walk.out.push((key, query));
        Ok(id)
    }

    /// A query a file defines itself: its default source applied to SQL that names none, and the
    /// queries its SQL composes (`{{ queries.base }}`) gathered first, from the same file.
    fn direct_query(
        &self,
        name: &str,
        path: &ProjectPath,
        entry: &yaml::Entry,
        queries: Option<&Entries>,
        source: Option<&str>,
        walk: &mut Walk<'_>,
    ) -> Result<QueryPatch, HoistError> {
        let mut node = entry.value.clone();
        stamp_declaring_source(
            &mut node,
            &format!("{path}#{name}"),
            source.map(str::to_string),
        )?;
        let parsed = parse_patch::<Query>(&node, None);
        if let Some(e) = parsed.errors.first() {
            return Err(HoistError::Message(format!(
                "'{}': query '{name}' in {path} is invalid: {}",
                walk.chart_ref, e.diagnostic.code
            )));
        }
        walk.uses
            .extend(SourceUse::of(name, &parsed.twin, Some(dir_of(path))));
        if let Some(QueryAuthored::Sql(sql)) = &parsed.twin {
            let composed = composed_queries(sql.sql.as_deref().unwrap_or_default());
            check_composed(name, &composed)?;
            for dep in composed {
                self.walk_query(&dep, path, queries, source, walk)?;
            }
        }
        Ok(parsed.twin)
    }
}

/// An imported chart's file: where its `query:` names are read, and the source its SQL takes.
pub struct Origin {
    /// The ref as the importer wrote it, which names the chart in messages.
    spec: String,
    path: ProjectPath,
    file: yaml::Node,
    source: Option<String>,
    /// The chart as its file declares it, and its name there.
    chart: yaml::Node,
    name: String,
}

/// The charts of a board that a ref brought in, by their name in the importing board.
#[derive(Default)]
pub struct Imports {
    /// The charts a ref brought in, by their name in the importing board.
    pub charts: BTreeMap<String, Origin>,
    /// The `queries:` entries of the importing board that a ref stands in for: Python reads them
    /// as another file's queries, not as the board's own.
    pub queries: BTreeSet<String>,
    /// The positions of placed boards that are long prose, which the adjacent-text-rows warning
    /// counts toward a run without reporting them as authored rows.
    pub prose: BTreeSet<board_model::ids::ItemId>,
    /// The `variables:` entries a ref stands in for; a variable name is unique to its board tree.
    pub variables: BTreeSet<String>,
}

/// One imported chart's query walk: the queries gathered so far, and the ones in progress.
struct Walk<'a> {
    chart_ref: &'a str,
    out: &'a mut Vec<(String, QueryPatch)>,
    /// The source each gathered query names, with the directory of the file that declares it.
    uses: &'a mut Vec<SourceUse>,
    resolving: BTreeSet<String>,
}

/// `file`, with `.yml` appended when it carries no yaml suffix.
fn with_suffix(file: String) -> String {
    match file.ends_with(".yml") || file.ends_with(".yaml") {
        true => file,
        false => format!("{file}.yml"),
    }
}

/// The ref a `queries:` entry of an imported file is, when it is one: a ref-shaped string (read
/// after trimming, as `normalize_query_value` does) or a mapping with a `ref:` key.
fn query_ref_of(node: &yaml::Node) -> Option<String> {
    match &node.kind {
        yaml::Kind::Scalar(yaml::Value::Str(s)) => QueryRef::try_from(s.trim())
            .ok()
            .map(|_| s.trim().to_string()),
        yaml::Kind::Map(m) => match m.get("ref").map(|e| &e.value.kind) {
            Some(yaml::Kind::Scalar(yaml::Value::Str(s))) => Some(s.clone()),
            _ => None,
        },
        _ => None,
    }
}

pub(crate) fn source_required(query: &str) -> HoistError {
    HoistError::SourceRequired(Box::new(diag::err_source_required(query)))
}

/// A ref'd SQL query that names no source takes the source of the file that declares it, never the
/// importing board's; with none to take, Python refuses it (`ERR-SOURCE-REQUIRED`). A bare SQL
/// string is such a query; a query of another type has no connection slot.
fn stamp_declaring_source(
    query: &mut yaml::Node,
    name: &str,
    source: Option<String>,
) -> Result<(), HoistError> {
    if ref_of(query, RefKind::Query).is_some() {
        return Ok(());
    }
    let span = query.span;
    if let yaml::Kind::Scalar(yaml::Value::Str(sql)) = &query.kind {
        let mut map = indexmap::IndexMap::new();
        let text = yaml::Node {
            span,
            kind: yaml::Kind::Scalar(yaml::Value::Str(sql.clone())),
        };
        map.insert(
            "sql".to_string(),
            yaml::Entry::string_key(span, "sql", text),
        );
        query.kind = yaml::Kind::Map(map);
    }
    let yaml::Kind::Map(m) = &mut query.kind else {
        return Ok(());
    };
    let is_sql = match m.get("type").map(|e| &e.value.kind) {
        Some(yaml::Kind::Scalar(yaml::Value::Str(t))) => t == "sql",
        Some(_) => false,
        None => !["rows", "values", "columns"]
            .iter()
            .any(|k| m.contains_key(*k)),
    };
    let named = !matches!(
        m.get("source").map(|e| &e.value.kind),
        None | Some(yaml::Kind::Scalar(yaml::Value::Null))
    );
    if !is_sql || named {
        return Ok(());
    }
    let Some(source) = source.filter(|s| !s.is_empty()) else {
        return Err(source_required(name));
    };
    let value = yaml::Node {
        span,
        kind: yaml::Kind::Scalar(yaml::Value::Str(source)),
    };
    m.insert(
        "source".to_string(),
        yaml::Entry::string_key(span, "source", value),
    );
    Ok(())
}

/// A `queries:`, `variables:` or `charts:` value that is a ref: a ref-shaped string, or a mapping holding
/// only `ref:` (read as written, so padding is the grammar's error). A query string is read after
/// trimming, as Python's `normalize_query_value` does; a variable or chart string is not.
fn ref_of(node: &yaml::Node, kind: RefKind) -> Option<String> {
    let text = match &node.kind {
        yaml::Kind::Scalar(yaml::Value::Str(s)) => match kind {
            RefKind::Query => s.trim(),
            RefKind::Variable | RefKind::Chart => s.as_str(),
        },
        yaml::Kind::Map(m) if m.len() == 1 => match m.get("ref").map(|e| &e.value.kind) {
            Some(yaml::Kind::Scalar(yaml::Value::Str(s))) => s.as_str(),
            _ => return None,
        },
        _ => return None,
    };
    kind.parse(text).ok().map(|_| text.to_string())
}

/// The display-text fields of an authored chart, by path (Python's `display_texts`): a KPI's label
/// and support label, a callout's title and message, any other family's title and subtitle, and a
/// cartesian layer's label.
fn display_texts(chart: &yaml::Node) -> Vec<(String, String)> {
    let yaml::Kind::Map(m) = &chart.kind else {
        return Vec::new();
    };
    let text = |node: Option<&yaml::Entry>| match node.map(|e| &e.value.kind) {
        Some(yaml::Kind::Scalar(yaml::Value::Str(s))) => Some(s.clone()),
        _ => None,
    };
    let kind = text(m.get("type")).unwrap_or_default();
    let mut out: Vec<(String, String)> = Vec::new();
    let mut add =
        |field: &str, found: Option<String>| out.extend(found.map(|t| (field.to_string(), t)));
    match kind.as_str() {
        "kpi" => {
            add("label", text(m.get("label")));
            let support = match m.get("support").map(|e| &e.value.kind) {
                Some(yaml::Kind::Map(s)) => text(s.get("label")),
                _ => None,
            };
            add("support.label", support);
        }
        "callout" => {
            add("title", text(m.get("title")));
            add("message", text(m.get("message")));
        }
        _ => {
            add("title", text(m.get("title")));
            add("subtitle", text(m.get("subtitle")));
        }
    }
    if matches!(kind.as_str(), "area" | "bar" | "line" | "scatter") {
        if let Some(yaml::Kind::Seq(layers)) = m.get("layers").map(|e| &e.value.kind) {
            for (i, layer) in layers.iter().enumerate() {
                if let yaml::Kind::Map(layer) = &layer.kind {
                    add(&format!("layers[{i}].label"), text(layer.get("label")));
                }
            }
        }
    }
    out
}
