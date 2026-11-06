//! The field tree of a frozen grammar: the paths a rename can name, and where
//! a board holds a board.
//!
//! A *model* is a schema object that declares `properties`. The walk descends
//! a field through arrays and open maps (`*`) and through unions into the
//! models they hold. An ancestor already on the path is opaque, so a board
//! nested in a board is walked once and no deeper; `board_nesting_prefixes`
//! finds the rest in the document.

use serde_json::Value as Json;
use std::collections::{BTreeSet, HashMap};
use std::rc::Rc;

use crate::schema::{Pos, Schema};
use crate::validate::resolve;

type Path = Vec<String>;
type Held = Vec<(usize, Vec<String>)>;

pub struct Fields {
    schema: &'static Schema,
    models: Vec<Pos>,
    ids: HashMap<usize, usize>,
    held: HashMap<(usize, usize), Rc<Held>>,
}

impl Fields {
    pub fn new(schema: &'static Schema) -> Self {
        let mut f = Fields {
            schema,
            models: Vec::new(),
            ids: HashMap::new(),
            held: HashMap::new(),
        };
        f.model(schema.root());
        f
    }

    fn model(&mut self, node: Pos) -> usize {
        let addr = node as *const Json as usize;
        if let Some(id) = self.ids.get(&addr) {
            return *id;
        }
        self.models.push(node);
        self.ids.insert(addr, self.models.len() - 1);
        self.models.len() - 1
    }

    fn properties(&self, model: usize) -> Vec<(&'static str, Pos)> {
        self.models
            .get(model)
            .and_then(|m| m.get("properties"))
            .and_then(Json::as_object)
            .map(|p| p.iter().map(|(k, v)| (k.as_str(), v)).collect())
            .unwrap_or_default()
    }

    /// The models `node` holds, each with the `*` segments it sits under.
    fn nested(&mut self, node: Pos, suffix: &[String], out: &mut Held, via: &mut BTreeSet<String>) {
        let root = self.schema.root();
        let mut node = node;
        let mut added = None;
        if let Some(r) = node.get("$ref").and_then(Json::as_str) {
            if !via.insert(r.to_string()) {
                return;
            }
            added = Some(r.to_string());
            node = resolve(root, r).filter(|t| t.is_object()).unwrap_or(node);
        }
        if node.get("properties").is_some_and(Json::is_object) {
            let id = self.model(node);
            out.push((id, suffix.to_vec()));
        }
        for kw in ["anyOf", "oneOf", "allOf"] {
            if let Some(Json::Array(bs)) = node.get(kw) {
                for b in bs.iter().filter(|b| b.is_object()) {
                    self.nested(b, suffix, out, via);
                }
            }
        }
        if let Some(t) = node.get("then").filter(|t| t.is_object()) {
            self.nested(t, suffix, out, via);
        }
        let star = [suffix, &["*".to_string()]].concat();
        if let Some(i) = node.get("items").filter(|i| i.is_object()) {
            self.nested(i, &star, out, via);
        }
        if node.get("properties").is_none() {
            if let Some(a) = node.get("additionalProperties").filter(|a| a.is_object()) {
                self.nested(a, &star, out, via);
            }
        }
        if let Some(r) = added {
            via.remove(&r);
        }
    }

    /// Models held by field `name` of `model`, memoized per (model, name index).
    fn held_by(&mut self, model: usize, field: usize) -> Rc<Held> {
        if let Some(h) = self.held.get(&(model, field)) {
            return h.clone();
        }
        let props = self.properties(model);
        let mut out = Vec::new();
        if let Some((_, schema)) = props.get(field) {
            self.nested(schema, &[], &mut out, &mut BTreeSet::new());
        }
        let out = Rc::new(out);
        self.held.insert((model, field), out.clone());
        out
    }

    fn closure(&mut self, model: usize) -> BTreeSet<usize> {
        let mut seen = BTreeSet::from([model]);
        let mut stack = vec![model];
        while let Some(m) = stack.pop() {
            for f in 0..self.properties(m).len() {
                for (n, _) in self.held_by(m, f).iter() {
                    if seen.insert(*n) {
                        stack.push(*n);
                    }
                }
            }
        }
        seen
    }

    /// Field paths below the root that end in one of `tails`, as
    /// `relative_field_paths` keeps them: a path is kept when it ends in a tail
    /// or is shorter than the widest tail.
    pub fn paths(&mut self, tails: &BTreeSet<Path>) -> Vec<Path> {
        let width = tails.iter().map(Vec::len).max().unwrap_or(0);
        let mut memo = HashMap::new();
        let mut closures = HashMap::new();
        let all = self.walk(0, &BTreeSet::new(), tails, width, &mut memo, &mut closures);
        let mut seen = BTreeSet::new();
        all.iter()
            .filter(|p| seen.insert((*p).clone()))
            .cloned()
            .collect()
    }

    #[allow(clippy::type_complexity)]
    fn walk(
        &mut self,
        model: usize,
        seen: &BTreeSet<usize>,
        tails: &BTreeSet<Path>,
        width: usize,
        memo: &mut HashMap<(usize, BTreeSet<usize>), Rc<Vec<Path>>>,
        closures: &mut HashMap<usize, BTreeSet<usize>>,
    ) -> Rc<Vec<Path>> {
        if seen.contains(&model) {
            return Rc::new(Vec::new());
        }
        let key = (model, seen.clone());
        if let Some(hit) = memo.get(&key) {
            return hit.clone();
        }
        let mut paths = Vec::new();
        let props = self.properties(model);
        for (i, (name, _)) in props.iter().enumerate() {
            let mut candidates: Vec<Path> = vec![vec![name.to_string()]];
            for (nested, suffix) in self.held_by(model, i).iter() {
                let closure = closures
                    .entry(*nested)
                    .or_insert_with(|| self.closure(*nested))
                    .clone();
                let mut ancestors = seen.clone();
                ancestors.insert(model);
                let nested_seen: BTreeSet<usize> =
                    ancestors.intersection(&closure).copied().collect();
                for sub in self
                    .walk(*nested, &nested_seen, tails, width, memo, closures)
                    .iter()
                {
                    candidates.push([&[name.to_string()][..], suffix, sub].concat());
                }
            }
            for c in candidates {
                if c.len() < width || ends_with_any(&c, tails) {
                    paths.push(c);
                }
            }
        }
        let paths = Rc::new(paths);
        memo.insert(key, paths.clone());
        paths
    }

    /// Each `style` field of a board that holds no board: its path, and the
    /// style models it can hold per chart `type:` (`""` where the holder
    /// carries none). A board inside a board is reached through the nesting
    /// prefixes, so the walk does not enter an ancestor twice.
    pub fn style_owners(&mut self) -> Vec<(Path, Vec<(String, Pos)>)> {
        let mut owners: Vec<(Path, Vec<(String, Pos)>)> = Vec::new();
        self.owners(0, &[], &BTreeSet::new(), &mut owners);
        owners
    }

    fn owners(
        &mut self,
        model: usize,
        path: &[String],
        seen: &BTreeSet<usize>,
        out: &mut Vec<(Path, Vec<(String, Pos)>)>,
    ) {
        if seen.contains(&model) {
            return;
        }
        let types = self.chart_types(model);
        let mut below = seen.clone();
        below.insert(model);
        let props = self.properties(model);
        for (i, (name, _)) in props.iter().enumerate() {
            for (nested, suffix) in self.held_by(model, i).iter() {
                let here = [path, &[name.to_string()][..], suffix].concat();
                if *name == "style" {
                    let Some(found) = self.models.get(*nested).copied() else {
                        continue;
                    };
                    let slot = match out.iter().position(|(p, _)| *p == here) {
                        Some(k) => k,
                        None => {
                            out.push((here, Vec::new()));
                            out.len() - 1
                        }
                    };
                    if let Some((_, held)) = out.get_mut(slot) {
                        for t in &types {
                            let entry = (t.clone(), found);
                            if !held
                                .iter()
                                .any(|(ht, hp)| ht == t && std::ptr::eq(*hp, found))
                            {
                                held.push(entry);
                            }
                        }
                    }
                } else {
                    self.owners(*nested, &here, &below, out);
                }
            }
        }
    }

    /// The `type:` literals a model discriminates on, `[""]` when it has none.
    fn chart_types(&self, model: usize) -> Vec<String> {
        let declared = self
            .models
            .get(model)
            .and_then(|m| m.get("properties"))
            .and_then(|p| p.get("type"));
        let literals: Vec<String> = match declared {
            Some(t) => match (t.get("enum"), t.get("const")) {
                (Some(Json::Array(e)), _) => e
                    .iter()
                    .filter_map(Json::as_str)
                    .map(str::to_string)
                    .collect(),
                (_, Some(Json::String(c))) => vec![c.clone()],
                _ => Vec::new(),
            },
            None => Vec::new(),
        };
        if literals.is_empty() {
            vec![String::new()]
        } else {
            literals
        }
    }

    /// Every model a value at `node` can be, through unions, lists and open maps.
    pub fn models_in(&mut self, node: Pos) -> Vec<Pos> {
        let mut held = Vec::new();
        self.nested(node, &[], &mut held, &mut BTreeSet::new());
        let mut out: Vec<Pos> = Vec::new();
        for (id, _) in held {
            if let Some(m) = self.models.get(id).copied() {
                if !out.iter().any(|o| std::ptr::eq(*o, m)) {
                    out.push(m);
                }
            }
        }
        out
    }

    /// Every key path at which the root holds the root (`*` for a map or list level).
    pub fn board_nesting_prefixes(&mut self) -> Vec<Path> {
        let mut found = Vec::new();
        let mut closures = HashMap::new();
        self.nest(0, &[], &BTreeSet::from([0]), &mut found, &mut closures);
        let mut seen = BTreeSet::new();
        found.retain(|p| seen.insert(p.clone()));
        found
    }

    fn nest(
        &mut self,
        model: usize,
        prefix: &[String],
        seen: &BTreeSet<usize>,
        found: &mut Vec<Path>,
        closures: &mut HashMap<usize, BTreeSet<usize>>,
    ) {
        let props = self.properties(model);
        for (i, (name, _)) in props.iter().enumerate() {
            for (nested, suffix) in self.held_by(model, i).iter() {
                let path = [prefix, &[name.to_string()][..], suffix].concat();
                if *nested == 0 {
                    found.push(path);
                } else if !seen.contains(nested)
                    && closures
                        .entry(*nested)
                        .or_insert_with(|| self.closure(*nested))
                        .contains(&0)
                {
                    let mut deeper = seen.clone();
                    deeper.insert(*nested);
                    self.nest(*nested, &path, &deeper, found, closures);
                }
            }
        }
    }
}

fn ends_with_any(path: &[String], tails: &BTreeSet<Path>) -> bool {
    tails.iter().any(|t| path.ends_with(t))
}
