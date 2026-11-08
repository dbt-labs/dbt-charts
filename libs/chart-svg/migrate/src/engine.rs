//! Recognition and the migration loop.
//!
//! Recognition reads the document, not a version marker: it asks which
//! retired constructs are present, oldest grammar first. A document the
//! current grammar already accepts is current and is never probed.

use std::collections::HashSet;

use yaml::{Kind, Node, Value};

use crate::apply::{
    apply_conditional_move, apply_deletions, apply_map_key_deletions, apply_move,
    conditional_move_fires, deletions_that_fire, map_key_deletion_fires, Applied, MigrationError,
};
use crate::catalog::{current, current_grammar, entries, Date};
use crate::doc::{document_keys, map};
use crate::locate::move_source_locations;
use crate::registry::Registry;

/// What `migrate` did with a document.
#[derive(Debug, Clone, PartialEq)]
pub enum Outcome {
    /// Nothing retired was found; the document is returned as it came.
    Current,
    Migrated,
    /// Neither current nor any retained grammar: the parser reports it.
    Unrecognized,
    /// Recognized as old but the chain could not finish (a conflict, a value no
    /// map covers, a result the current grammar still rejects, or an expired
    /// grammar); `why` is the notice owed the author. Edits already applied
    /// before the chain ran (the pre-checks) stay in the returned node.
    Kept {
        why: String,
    },
}

#[derive(Debug)]
pub struct Migrated {
    pub node: Node,
    pub outcome: Outcome,
    /// Author-facing notices, in the order Python's loader emits them.
    pub notices: Vec<String>,
    /// Applied moves and deletions, in order, for a text rewrite to replay
    /// against the returned node's source.
    pub edits: Vec<crate::doc::Edit>,
}

const IN_MEMORY: &str =
    "dbt charts migrated this YAML in memory; `dct migrate` may be able to update the file.";

/// Recognizes the grammar `node` was authored against and rewrites it to the
/// current one. Never fails: a document that cannot be migrated comes back
/// unchanged and the parser reports it against the current grammar.
/// `today` bounds the transparent-migration window (six months).
pub fn migrate(node: Node, today: Date) -> Migrated {
    let reg = Registry::get();
    let mut doc = node.clone();
    let mut applied = Applied::default();
    let identity_applied = theme_needs_recheck(&doc);

    if identity_applied {
        if let Err(e) = apply_identity_moves(&mut doc, reg, &mut applied) {
            return kept(node, doc, applied, failed(&e));
        }
    }
    if retired_token_needs_recheck(&doc, reg) {
        let mkd = reg.all.map_key_deletions.iter().collect::<Vec<_>>();
        let mut struck = Applied::default();
        if apply_map_key_deletions(&mut doc, &mkd, &mut struck) {
            applied.notices.push(IN_MEMORY.to_string());
            applied.notices.append(&mut struck.notices);
            applied.edits.append(&mut struck.edits);
        }
        if !identity_applied {
            if let Err(e) = apply_identity_moves(&mut doc, reg, &mut applied) {
                return kept(node, doc, applied, failed(&e));
            }
        }
    }

    if accepts_current(&doc) {
        return finish(node, doc, applied, None);
    }
    match migrate_mapping(&doc, reg, today) {
        Ok(Some((out, more))) => {
            applied.notices.extend(more.notices);
            applied.edits.extend(more.edits);
            finish(node, out, applied, None)
        }
        Ok(None) => finish(node, doc, applied, None),
        Err(Stop::Unsupported) => finish(node, doc, applied, Some(Outcome::Unrecognized)),
        Err(Stop::TooOld(m)) => {
            let why = format!(
                "dbt charts did not migrate this YAML. {m} Reporting it against the current schema instead — errors below may name retired syntax rather than anything you just changed."
            );
            applied.notices.push(why.clone());
            finish(node, doc, applied, Some(Outcome::Kept { why }))
        }
        Err(Stop::Failed(e)) => {
            let why = failed(&e);
            applied.notices.push(why.clone());
            finish(node, doc, applied, Some(Outcome::Kept { why }))
        }
    }
}

fn failed(e: &MigrationError) -> String {
    format!(
        "dbt charts could not finish migrating this YAML. {} Reporting it against the current schema instead — errors below may name retired syntax rather than anything you just changed.",
        e.message()
    )
}

fn kept(original: Node, doc: Node, mut applied: Applied, why: String) -> Migrated {
    applied.notices.push(why.clone());
    finish(original, doc, applied, Some(Outcome::Kept { why }))
}

fn finish(original: Node, doc: Node, applied: Applied, forced: Option<Outcome>) -> Migrated {
    let outcome = forced.unwrap_or(
        if doc == original && applied.edits.is_empty() && applied.notices.is_empty() {
            Outcome::Current
        } else {
            Outcome::Migrated
        },
    );
    Migrated {
        node: doc,
        outcome,
        notices: applied.notices,
        edits: applied.edits,
    }
}

enum Stop {
    Unsupported,
    TooOld(String),
    Failed(MigrationError),
}

impl From<MigrationError> for Stop {
    fn from(e: MigrationError) -> Stop {
        Stop::Failed(e)
    }
}

/// Applies every identity-path move whose value map renames something here.
fn apply_identity_moves(
    doc: &mut Node,
    reg: &Registry,
    out: &mut Applied,
) -> Result<(), MigrationError> {
    let identity: Vec<_> = reg
        .all
        .moves
        .iter()
        .filter(|m| m.old_path == m.new_path && m.rewrite_in_document.is_none())
        .collect();
    if !identity
        .iter()
        .any(|m| !move_source_locations(doc, m).is_empty())
    {
        return Ok(());
    }
    let mut work = doc.clone();
    let authored = doc.to_json();
    let mut local = Applied::default();
    for m in identity {
        apply_move(&mut work, m, &authored, &mut local)?;
    }
    *doc = work;
    out.notices.append(&mut local.notices);
    out.notices.push(IN_MEMORY.to_string());
    out.edits.append(&mut local.edits);
    Ok(())
}

/// A top-level `theme:` string that is not a current built-in name.
fn theme_needs_recheck(doc: &Node) -> bool {
    let Some(Kind::Scalar(Value::Str(theme))) = crate::doc::child(doc, "theme").map(|n| &n.kind)
    else {
        return false;
    };
    let names: Vec<String> = current_grammar()
        .and_then(|g| {
            g.root()
                .get("properties")?
                .get("theme")?
                .get("enum")?
                .as_array()
                .cloned()
        })
        .unwrap_or_default()
        .into_iter()
        .filter_map(|v| v.as_str().map(str::to_string))
        .collect();
    !names.iter().any(|n| n == theme)
}

/// Whether the document carries a retired token spelling or a retired
/// `style.palettes` role key. Registry-free value-shape check; an
/// over-trigger costs one extra pass, an under-trigger leaves a spelling.
fn retired_token_needs_recheck(doc: &Node, reg: &Registry) -> bool {
    let tokens: Vec<&crate::decl::ValueMap> = reg.all.retired_tokens.iter().map(|t| &**t).collect();
    let roles: Vec<&str> = reg
        .all
        .retired_role_keys
        .iter()
        .map(String::as_str)
        .collect();
    fn scan(n: &Node, tokens: &[&crate::decl::ValueMap], roles: &[&str]) -> bool {
        match &n.kind {
            Kind::Scalar(Value::Str(_)) => tokens
                .iter()
                .any(|t| crate::locate::identity_value_would_change(t, n)),
            Kind::Scalar(_) => false,
            Kind::Map(m) => {
                if let Some(p) = m.get("palettes").and_then(|e| map(&e.value)) {
                    if roles.iter().any(|r| p.contains_key(*r)) {
                        return true;
                    }
                }
                m.values().any(|e| scan(&e.value, tokens, roles))
            }
            Kind::Seq(items) => items.iter().any(|i| scan(i, tokens, roles)),
        }
    }
    scan(doc, &tokens, &roles)
}

/// Whether the current grammar accepts `doc`.
fn accepts_current(doc: &Node) -> bool {
    current_grammar().is_some_and(|g| g.valid(g.root(), doc))
}

fn migrate_mapping(
    doc: &Node,
    reg: &Registry,
    today: Date,
) -> Result<Option<(Node, Applied)>, Stop> {
    let mut identifier = recognize(doc, reg)?;
    let recognized = identifier.clone();
    let cur = current();
    if identifier == cur.version {
        return Ok(None);
    }
    enforce_support_window(&identifier, today)?;
    let mut result = doc.clone();
    let mut out = Applied::default();
    while identifier != cur.version {
        let moves = reg.moves_from(&identifier);
        let deletions = reg.deletions_from(&identifier);
        let conds = reg.conditional_moves_from(&identifier);
        let mkds = reg.map_key_deletions_from(&identifier);
        // The chain follows the manifest, not the declarations: a boundary
        // with none still leads on to the next one.
        let Some(next) = entries()
            .iter()
            .position(|e| e.version == identifier)
            .and_then(|i| entries().get(i + 1))
            .map(|e| e.version.as_str())
        else {
            break;
        };
        apply_map_key_deletions(&mut result, &mkds, &mut out);
        step_moves(&mut result, &moves, &mut out)?;
        apply_deletions(&mut result, &deletions, &mut out);
        for c in &conds {
            apply_conditional_move(&mut result, c, &mut out)?;
        }
        identifier = next.to_string();
    }
    if !accepts_current(&result) {
        let why = current_grammar()
            .and_then(|g| crate::validate::first_rejection(g.root(), &result))
            .unwrap_or_default();
        return Err(Stop::Failed(MigrationError::Other(format!(
            "Recognized schema {recognized} and applied its migration, but the result still fails the current schema at {why}."
        ))));
    }
    dedupe(&mut out.notices);
    out.notices.push(IN_MEMORY.to_string());
    Ok(Some((result, out)))
}

/// Keeps each notice's first report: a position two moves reach (a tab's chart,
/// by its plain and its `tabs` move) owes the author one.
fn dedupe(notices: &mut Vec<String>) {
    let mut seen = HashSet::new();
    notices.retain(|n| seen.insert(n.clone()));
}

fn owned_keys(doc: &Node) -> HashSet<String> {
    document_keys(doc).into_iter().map(str::to_string).collect()
}

/// The grammar whose transition chain `doc` enters at.
fn recognize(doc: &Node, reg: &Registry) -> Result<String, Stop> {
    let cur = current();
    let current_ok = accepts_current(doc);
    if current_ok && !retired_token_needs_recheck(doc, reg) {
        return Ok(cur.version.clone());
    }
    let keys = owned_keys(doc);
    for e in entries() {
        if e.version == cur.version {
            continue;
        }
        if transition_applies(doc, &e.version, reg, &keys) {
            return Ok(e.version.clone());
        }
    }
    if current_ok {
        return Ok(cur.version.clone());
    }
    Err(Stop::Unsupported)
}

/// A pure dry run: whether `identifier`'s transition would find anything to rewrite.
fn transition_applies(
    doc: &Node,
    identifier: &str,
    reg: &Registry,
    keys: &HashSet<String>,
) -> bool {
    for m in reg.moves_from(identifier) {
        if m.rewrites_in_document_in_place() {
            continue;
        }
        if m.old_path.iter().all(|p| p == "*" || keys.contains(p))
            && !move_source_locations(doc, m).is_empty()
        {
            return true;
        }
    }
    if deletions_that_fire(doc, &reg.deletions_from(identifier)) {
        return true;
    }
    if reg
        .conditional_moves_from(identifier)
        .iter()
        .any(|c| conditional_move_fires(doc, c))
    {
        return true;
    }
    map_key_deletion_fires(doc, &reg.map_key_deletions_from(identifier))
}

fn enforce_support_window(identifier: &str, today: Date) -> Result<(), Stop> {
    let Some(entry) = entries().iter().find(|e| e.version == identifier) else {
        return Err(Stop::Failed(MigrationError::Other(format!(
            "Schema {identifier} is not a retained grammar; cannot enforce the transparent-migration support window."
        ))));
    };
    let Some(released) = entry.released_at else {
        return Err(Stop::Failed(MigrationError::Other(format!(
            "Schema {identifier} has no release date; the DEV version must never reach the support-window check."
        ))));
    };
    let cutoff = subtract_months(today, 6);
    if released < cutoff {
        return Err(Stop::TooOld(format!(
            "Schema {identifier} is older than the transparent-migration cutoff {:04}-{:02}-{:02}; run `dct migrate` to update the file.",
            cutoff.0, cutoff.1, cutoff.2
        )));
    }
    Ok(())
}

fn days_in_month(year: i32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 => 29,
        _ => 28,
    }
}

pub fn subtract_months(d: Date, months: i32) -> Date {
    let m = d.1 as i32 - months;
    let year = d.0 + (m - 1).div_euclid(12);
    let month = (m - 1).rem_euclid(12) as u32 + 1;
    (year, month, d.2.min(days_in_month(year, month)))
}

/// What a `dct migrate` text rewrite is owed for one document.
#[derive(Debug)]
pub enum TextPlan {
    /// Nothing to write: already current, or newer than the target.
    Current,
    /// The document the writer's result must equal, the applied edits in order
    /// (the contract the writer consumes: `Edit`), and the author-facing
    /// notices, held until the write succeeds.
    Planned {
        node: Node,
        edits: Vec<crate::doc::Edit>,
        notices: Vec<String>,
    },
}

/// Why no plan could be made, by the error class the caller raises.
#[derive(Debug, PartialEq)]
pub enum TextError {
    /// Neither current nor any retained grammar.
    Unsupported(String),
    /// A recognized grammar whose declared migration still leaves the document invalid.
    Incomplete(String),
    /// A move would overwrite an authored destination value.
    Conflict(String),
    /// Any other refusal.
    Migration(String),
}

impl From<MigrationError> for TextError {
    fn from(e: MigrationError) -> TextError {
        match e {
            MigrationError::Conflict(m) => TextError::Conflict(m),
            other => TextError::Migration(other.message().to_string()),
        }
    }
}

/// Dotted-version order: whether `version` is newer than `other`.
fn is_newer(version: &str, other: &str) -> bool {
    let parts = |v: &str| -> Option<Vec<u32>> { v.split('.').map(|p| p.parse().ok()).collect() };
    matches!((parts(version), parts(other)), (Some(a), Some(b)) if a > b)
}

/// The moves, deletions and map-key deletions a text rewrite can express (never
/// a `ConditionalMove`), walked from the recognized grammar to `stop_target`
/// (the newest released grammar a project allows) or the current one.
pub fn plan_text(node: &Node, stop_target: Option<&str>) -> Result<TextPlan, TextError> {
    let reg = Registry::get();
    let cur = current();
    let recognized = recognize(node, reg).map_err(|_| {
        let why = current_grammar()
            .and_then(|g| crate::validate::first_rejection(g.root(), node))
            .unwrap_or_default();
        TextError::Unsupported(format!(
            "Unsupported YAML syntax; current schema rejected it: {why}"
        ))
    })?;
    if recognized == cur.version || stop_target.is_some_and(|t| is_newer(&recognized, t)) {
        return Ok(TextPlan::Current);
    }
    let mut staged = node.clone();
    let mut out = Applied::default();
    let mut identifier = recognized.clone();
    while identifier != cur.version && Some(identifier.as_str()) != stop_target {
        let (moves, deletions, mkds) = (
            reg.moves_from(&identifier),
            reg.deletions_from(&identifier),
            reg.map_key_deletions_from(&identifier),
        );
        let Some(target) = moves
            .first()
            .map(|m| m.target)
            .or_else(|| deletions.first().map(|d| d.target))
            .or_else(|| mkds.first().map(|d| d.target))
        else {
            break;
        };
        apply_map_key_deletions(&mut staged, &mkds, &mut out);
        step_moves(&mut staged, &moves, &mut out)?;
        apply_deletions(&mut staged, &deletions, &mut out);
        identifier = target.to_string();
    }
    let rejection = if stop_target.is_none() {
        rejection_of(&staged)
    } else {
        reach_current(&staged, &identifier, reg)?
    };
    if let Some(why) = rejection {
        return Err(TextError::Incomplete(format!(
            "Recognized schema {recognized} and applied its migration, but the result still fails the current schema at {why}."
        )));
    }
    dedupe(&mut out.notices);
    Ok(TextPlan::Planned {
        node: staged,
        edits: out.edits,
        notices: out.notices,
    })
}

/// One boundary's moves, each fired where its old path's keys are present.
/// Every document-aware rewrite reads the document as the moves began.
fn step_moves(
    doc: &mut Node,
    moves: &[&crate::decl::Move],
    out: &mut Applied,
) -> Result<(), MigrationError> {
    let authored = doc.to_json();
    let mut keys = owned_keys(doc);
    for m in moves {
        if m.old_path.iter().all(|p| p == "*" || keys.contains(p)) {
            apply_move(doc, m, &authored, out)?;
            keys = owned_keys(doc);
        }
    }
    Ok(())
}

fn rejection_of(doc: &Node) -> Option<String> {
    if accepts_current(doc) {
        return None;
    }
    Some(
        current_grammar()
            .and_then(|g| crate::validate::first_rejection(g.root(), doc))
            .unwrap_or_default(),
    )
}

/// Continues a throwaway copy from `identifier` to the current grammar through
/// moves, deletions and map-key deletions only, the writer's own repertoire,
/// and returns why the current grammar rejects the result, if it does.
fn reach_current(
    staged: &Node,
    identifier: &str,
    reg: &Registry,
) -> Result<Option<String>, TextError> {
    let cur = current();
    let mut result = staged.clone();
    let mut scratch = Applied::default();
    let mut identifier = identifier.to_string();
    while identifier != cur.version {
        let (moves, deletions, mkds) = (
            reg.moves_from(&identifier),
            reg.deletions_from(&identifier),
            reg.map_key_deletions_from(&identifier),
        );
        let Some(target) = moves
            .first()
            .map(|m| m.target)
            .or_else(|| deletions.first().map(|d| d.target))
            .or_else(|| mkds.first().map(|d| d.target))
        else {
            break;
        };
        apply_map_key_deletions(&mut result, &mkds, &mut scratch);
        step_moves(&mut result, &moves, &mut scratch)?;
        apply_deletions(&mut result, &deletions, &mut scratch);
        identifier = target.to_string();
    }
    Ok(rejection_of(&result))
}

/// A key the grammar retired, as found in one document.
#[derive(Debug, Clone, PartialEq)]
pub struct RetiredKey {
    pub path: Vec<String>,
    /// The grammar version that dropped it.
    pub retired_in: &'static str,
    /// The document path the declaration moves it to; `None` for a removal.
    pub successor: Option<Vec<String>>,
    pub reason: Option<String>,
}

/// Every key in `doc` a declaration would fire on, by concrete document path,
/// newest grammar first (the newest declaration wins a path). "Retired" means
/// exactly "the migration would act here": the same gates recognition and
/// application use. Read-only, so it works on files migration declined to
/// touch. Identity respells and map-key deletions are not retired keys.
pub fn retired_keys(doc: &Node) -> Vec<RetiredKey> {
    let reg = Registry::get();
    let keys = owned_keys(doc);
    let mut found: Vec<RetiredKey> = Vec::new();
    let mut push = |key: RetiredKey| {
        if !found.iter().any(|f| f.path == key.path) {
            found.push(key);
        }
    };
    for e in entries().iter().rev() {
        for m in reg.moves_from(&e.version) {
            if m.old_path == m.new_path || !m.old_path.iter().all(|p| p == "*" || keys.contains(p))
            {
                continue;
            }
            for loc in move_source_locations(doc, m) {
                let (Ok(path), Ok(successor)) = (
                    crate::locate::move_path(m, &m.old_path, &loc.bindings),
                    crate::locate::move_path(m, &m.new_path, &loc.bindings),
                ) else {
                    continue;
                };
                push(RetiredKey {
                    path,
                    retired_in: m.target,
                    successor: Some(successor),
                    reason: None,
                });
            }
        }
        for (path, d) in crate::apply::fired_deletions(doc, &reg.deletions_from(&e.version)) {
            push(RetiredKey {
                path,
                retired_in: d.target,
                successor: None,
                reason: d.reason.clone(),
            });
        }
        for rule in reg.conditional_moves_from(&e.version) {
            for chart in crate::apply::fired_conditionals(doc, rule) {
                let join = |tail: &[String]| [chart.as_slice(), tail].concat();
                push(RetiredKey {
                    path: join(&rule.old_tail),
                    retired_in: rule.target,
                    successor: Some(join(&rule.new_tail)),
                    reason: None,
                });
            }
        }
    }
    found
}
