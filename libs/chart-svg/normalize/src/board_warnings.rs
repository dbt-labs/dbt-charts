//! The board-level authoring warnings Python's `detect_board_warnings` reports beyond the ones
//! `lint` already ports: the double header, the heading with no title, the stacked prose rows,
//! the retired ignore codes, the discarded axis align, the unused format names, and the html
//! policy cap. Each reads the merged root as written, in Python's order; `lint` interleaves them.

use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored};
use board_model::board::theme::built_in_theme;
use board_model::board::BoardBodyAuthored;
use board_model::board::HtmlPolicy;
use board_model::chart::ChartAuthored;
use board_model::ids::{ChartId, ItemId};
use diagnostic::{diag, py_repr, Diagnostic, ModelPath};
use mdsvg::types::AnyBlock;
use model_core::{LeafValues, Tri};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

type Patch = <board_model::board::Board as model_core::Leaf>::Patch;
type NestedBoard = <board_model::board::layout::NestedBoard as model_core::Leaf>::Whole;

fn text_of<T: AsRef<str>>(slot: &Tri<T>) -> Option<&str> {
    match slot {
        Tri::Value(t) => Some(t.as_ref()).filter(|t| !t.is_empty()),
        _ => None,
    }
}

/// The cells a layout places, in order.
fn cells(layout: &LayoutAuthored) -> Vec<&LayoutItemAuthored> {
    match layout {
        LayoutAuthored::Rows(items) | LayoutAuthored::Cols(items) => items.iter().collect(),
        LayoutAuthored::Grid(g) => g
            .items
            .iter()
            .flatten()
            .filter_map(|cell| cell.item.as_ref())
            .collect(),
        LayoutAuthored::Tabs(_) => Vec::new(),
    }
}

/// The heading level, its text and the body's first non-blank line, as authored, when `body`
/// opens with one. mdsvg renders board text, so asking it keeps a fenced `#` comment from reading
/// as a heading. The line lets the stamping pass narrow a `text:` block to just it.
fn opening_heading(body: &str) -> Option<(u8, String, String)> {
    match mdsvg::parse(body).first() {
        Some(AnyBlock::Heading(h)) => {
            let line = body.lines().find(|l| !l.trim().is_empty()).unwrap_or("");
            Some((
                h.level,
                h.spans.iter().map(|s| s.text.as_str()).collect(),
                line.to_string(),
            ))
        }
        _ => None,
    }
}

pub(crate) fn path(segments: &[&str]) -> ModelPath {
    ModelPath(segments.iter().map(|s| s.to_string()).collect())
}

/// A dotted authored path as a model path.
fn dotted(path: &str) -> ModelPath {
    ModelPath(path.split('.').map(str::to_string).collect())
}

/// The authored path of a layout item (`rows.3`, `grid.items.0`, `tabs.items.1.rows.2`) as the
/// source map keys it: an [`ItemId`]'s indices become segments, and a grid cell is its `items`
/// entry, not the `item` inside it.
fn authored(at: &ItemId) -> String {
    let id = at
        .as_str()
        .trim_start_matches('@')
        .replace('[', ".")
        .replace(']', "");
    id.split('.')
        .filter(|segment| *segment != "item")
        .collect::<Vec<_>>()
        .join(".")
}

/// The board's body for the double-header check: its own `text`, else a bare text block in its
/// first slot (a tab pane opens under its tab label, not under the board header).
fn body_opening<'a>(text: Option<&'a str>, layout: Option<&'a LayoutAuthored>) -> Option<&'a str> {
    text.or_else(|| {
        let LayoutItemAuthored::Board(first) = cells(layout?).into_iter().next()? else {
            return None;
        };
        let bare = text_of(&first.body.title).is_none()
            && first
                .body
                .layout
                .as_ref()
                .is_none_or(|l| cells(l).is_empty())
            && !matches!(first.body.layout, Some(LayoutAuthored::Tabs(_)));
        bare.then(|| text_of(&first.body.text)).flatten()
    })
}

fn comparable(title: &str) -> String {
    title
        .chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

/// A board `title:` re-stated by the heading opening its body. Root only: a titled section card
/// pairing its title with a styled heading is a deliberate pattern.
pub fn double_header(patch: &Patch) -> Option<Diagnostic> {
    let title = text_of(&patch.body.title)?;
    let own_text = text_of(&patch.body.text);
    let (level, heading, line) =
        opening_heading(body_opening(own_text, patch.body.layout.as_ref())?)?;
    // Level 1 is the document-title level, a double header whatever it says; below it only a
    // heading that repeats the title is one.
    if level != 1 && comparable(&heading) != comparable(title) {
        return None;
    }
    // The heading is the line to delete, so it takes the mark and `title:` rides along. A heading
    // in a layout slot has no recoverable path, so `title:` takes the mark instead.
    let found = diag::warn_double_header(title, level, heading)
        .with_sink_param("source_needle", line.into());
    Some(match own_text {
        Some(_) => found.at_path(path(&["text"])).relating_path(
            path(&["title"]),
            format!(
                "the board title {} already heads the board",
                py_repr(&title.into())
            ),
        ),
        None => found.at_path(path(&["title"])),
    })
}

/// Each `rows:` item that is a board with its position, tab panes' included; stops at `cols:` and
/// `grid:`, whose cell is a card titled level with its neighbors.
fn rows_in<'a>(layout: &'a LayoutAuthored, at: &ItemId, out: &mut Vec<(ItemId, &'a NestedBoard)>) {
    match layout {
        LayoutAuthored::Cols(_) | LayoutAuthored::Grid(_) => {}
        LayoutAuthored::Rows(_) => {
            for (pos, item) in placed(layout, at) {
                let LayoutItemAuthored::Board(b) = item else {
                    continue;
                };
                out.push((pos.clone(), b));
                if let Some(l) = &b.body.layout {
                    rows_in(l, &pos, out);
                }
            }
        }
        LayoutAuthored::Tabs(t) => {
            let base = at.key("tabs").key("items");
            for (i, tab) in t.items.iter().flatten().enumerate() {
                if let Some(l) = &tab.layout {
                    rows_in(l, &base.index(i), out);
                }
            }
        }
    }
}

/// Text opening with a heading that its board has no `title:` for, at the path of its `text:`
/// (`text`, `rows.0.text`). `skip_first` is whether [`double_header`] fired on a layout slot: that
/// heading is the one line it tells the author to delete.
pub fn untitled_sections(patch: &Patch, skip_first: bool) -> Vec<Diagnostic> {
    let mut rows = Vec::new();
    if let Some(layout) = &patch.body.layout {
        rows_in(layout, &ItemId::root(), &mut rows);
    }
    if skip_first && !rows.is_empty() {
        rows.remove(0);
    }
    let root = (
        "text".to_string(),
        text_of(&patch.body.title),
        text_of(&patch.body.text),
    );
    std::iter::once(root)
        .chain(rows.iter().map(|(pos, b)| {
            (
                format!("{}.text", authored(pos)),
                text_of(&b.body.title),
                text_of(&b.body.text),
            )
        }))
        .filter(|(_, title, _)| title.is_none())
        .filter_map(|(at, _, text)| Some((at, opening_heading(text?)?)))
        .map(|(at, (_, heading, line))| {
            diag::warn_body_heading_no_title(heading)
                .with_sink_param("source_needle", line.into())
                .at_path(dotted(&at))
        })
        .collect()
}

/// Characters a prose block needs before it can reach a second column: two columns of five lines
/// at the narrowest measure. Below it a block is one column wide at any board width, and two
/// single-column blocks cannot misalign.
const MIN_FRAGMENTING_CHARS: usize = 450;

/// Whether a slot is a passage of prose big enough to fragment the page when stacked. A slot with
/// anything only a slot carries (`style:`, `visible:`, `details:`, a height, its own theme) is
/// not: merging it into its neighbor would have to discard that.
fn fragmenting_prose(item: &LayoutItemAuthored) -> bool {
    let LayoutItemAuthored::Board(b) = item else {
        return false;
    };
    let slot_only = matches!(b.visible, Tri::Value(_)) || matches!(b.height, Tri::Value(_));
    !slot_only && fragmenting_body(&b.body)
}

/// The body half of [`fragmenting_prose`]: long prose with a paragraph, no layout of its own and
/// nothing a merged block could not carry.
pub fn fragmenting_body(body: &BoardBodyAuthored) -> bool {
    let Some(text) = text_of(&body.text) else {
        return false;
    };
    let own_layout = body.layout.as_ref().is_some_and(|l| !cells(l).is_empty());
    let own_style = matches!(body.style, Tri::Value(_))
        || matches!(body.details, Tri::Value(_))
        || matches!(body.theme, Tri::Value(_))
        || matches!(body.extends, Tri::Value(_));
    !own_layout
        && !own_style
        && text.chars().count() >= MIN_FRAGMENTING_CHARS
        && mdsvg::parse(text)
            .iter()
            .any(|block| matches!(block, AnyBlock::Paragraph(_)))
}

/// The items a layout places with the position each has in the board index, as
/// [`crate::index::LayoutFacts`] numbers them.
fn placed<'a>(layout: &'a LayoutAuthored, at: &ItemId) -> Vec<(ItemId, &'a LayoutItemAuthored)> {
    match layout {
        LayoutAuthored::Rows(items) => items
            .iter()
            .enumerate()
            .map(|(i, item)| (at.key("rows").index(i), item))
            .collect(),
        LayoutAuthored::Cols(items) => items
            .iter()
            .enumerate()
            .map(|(i, item)| (at.key("cols").index(i), item))
            .collect(),
        LayoutAuthored::Grid(g) => g
            .items
            .iter()
            .flatten()
            .enumerate()
            .filter_map(|(i, cell)| {
                let pos = at.key("grid").key("items").index(i).key("item");
                Some((pos, cell.item.as_ref()?))
            })
            .collect(),
        LayoutAuthored::Tabs(_) => Vec::new(),
    }
}

/// Runs of consecutive prose-only rows anywhere in the tree, one warning per run: each block is
/// flowed alone, so stacked they render as unrelated grids. `cols:` is an authored spread, and
/// grid and tab items are positioned rather than stacked. An imported board of prose counts
/// toward a run (`prose_imports`), but a run whose second row is one is not reported: that row has
/// no coordinates in this file, so the warning would have nowhere to point.
pub fn adjacent_text_rows(patch: &Patch, prose_imports: &BTreeSet<ItemId>) -> Vec<Diagnostic> {
    fn walk(
        layout: &LayoutAuthored,
        at: &ItemId,
        prose_imports: &BTreeSet<ItemId>,
        out: &mut Vec<Diagnostic>,
    ) {
        let items = placed(layout, at);
        if let LayoutAuthored::Rows(_) = layout {
            // Each prose row of the current run with its position, `None` when it is an import.
            let mut run: Vec<Option<&ItemId>> = Vec::new();
            let mut flush = |run: &mut Vec<Option<&ItemId>>| {
                // The mark sits on the second row, the first that should have been merged upward,
                // with the block it should join alongside (when that one is authored here too).
                if let (true, Some(Some(second))) = (run.len() >= 2, run.get(1)) {
                    let found =
                        diag::warn_adjacent_text_rows(run.len()).at_path(dotted(&authored(second)));
                    out.push(match run.first() {
                        Some(Some(first)) => found.relating_path(
                            dotted(&authored(first)),
                            "the block this prose should join",
                        ),
                        _ => found,
                    });
                }
                run.clear();
            };
            for (pos, item) in &items {
                if prose_imports.contains(pos) {
                    run.push(None);
                } else if fragmenting_prose(item) {
                    run.push(Some(pos));
                } else {
                    flush(&mut run);
                }
            }
            flush(&mut run);
        }
        match layout {
            LayoutAuthored::Tabs(t) => {
                let base = at.key("tabs").key("items");
                for (i, tab) in t.items.iter().flatten().enumerate() {
                    if let Some(l) = &tab.layout {
                        walk(l, &base.index(i), prose_imports, out);
                    }
                }
            }
            _ => {
                for (pos, item) in items {
                    if let LayoutItemAuthored::Board(b) = item {
                        if let Some(l) = &b.body.layout {
                            walk(l, &pos, prose_imports, out);
                        }
                    }
                }
            }
        }
    }
    let mut out = Vec::new();
    if let Some(layout) = &patch.body.layout {
        walk(layout, &ItemId::root(), prose_imports, &mut out);
    }
    out
}

/// The board asked for a more permissive html policy than the ceiling allows. `pinned` is whether
/// the host's environment set the ceiling, which names its source.
pub fn html_policy_capped(
    requested: HtmlPolicy,
    effective: HtmlPolicy,
    pinned: bool,
) -> Option<Diagnostic> {
    let source = match pinned {
        true => "DCT_HTML_POLICY_CEILING env var",
        false => "project config",
    };
    (requested != effective).then(|| {
        let (requested, effective) = (requested.as_str(), effective.as_str());
        diag::warn_html_policy_capped(requested, source, effective, effective)
    })
}

/// Every set leaf of the merged root with its authored value, by authored path, in file order. A
/// model is reported whole beside its leaves, so `charts.<id>`, `queries.<name>` and `style` each
/// read as one JSON object.
pub struct Leaves(Vec<(String, Value)>);

impl Leaves {
    pub fn of(patch: &Patch) -> Self {
        let mut all = Vec::new();
        patch.leaf_values("", &mut |p, v| all.push((p.to_string(), v.spell())));
        Self(all)
    }

    fn get(&self, path: &str) -> Option<&Value> {
        self.0.iter().find(|(p, _)| p == path).map(|(_, v)| v)
    }

    /// The whole models under `scope.` (`charts.`, `queries.`), by name.
    fn models<'a>(&'a self, scope: &'static str) -> impl Iterator<Item = (&'a str, &'a Value)> {
        self.0.iter().filter_map(move |(p, v)| {
            let name = p.strip_prefix(scope)?;
            (!name.contains('.') && v.is_object()).then_some((name, v))
        })
    }
}

/// `v` at the dotted `path`, through mappings.
fn dig<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(v, |v, key| v.get(key))
}

/// What the detectors read of a board beyond its merged root: the theme in force, and the names
/// its own file (not a `meta.yml` it inherits) defines under `style.formats`.
pub struct Facts<'a> {
    pub theme: &'a str,
    /// The id a user sees for each chart ([`crate::index::BoardIndex::public_ids`]).
    pub public_ids: &'a BTreeMap<ChartId, String>,
    pub own_format_names: BTreeSet<String>,
    /// The queries an imported chart's display text reads ([`crate::index::BoardIndex::text_queries`]).
    pub text_queries: &'a BTreeMap<String, BTreeMap<String, board_model::ids::QueryId>>,
}

/// The names the board file's own patch defines under `style.formats`.
pub fn own_format_names(own: &Patch) -> BTreeSet<String> {
    let leaves = Leaves::of(own);
    leaves
        .get("style")
        .and_then(|style| dig(style, "formats")?.as_object())
        .map(|names| names.keys().cloned().collect())
        .unwrap_or_default()
}

/// Warning codes an earlier release shipped and a later one removed (`RETIRED_WARNING_CODES`).
const RETIRED_WARNING_CODES: &[&str] = &[
    "WARN-FANOUT-RISK",
    "WARN-H1-BODY-NO-TITLE",
    "WARN-LIKELY-CURRENCY-OR-PERCENT-MISSING-FORMATTER",
    "WARN-PIE-TOO-MANY-SEGMENTS",
    "WARN-REDUNDANT-AUTHORED-DEFAULT",
    "WARN-REDUNDANT-AUTHORED-LABEL",
    "WARN-TABLE-CRAMPED",
];

/// Each retired code a chart's `warnings_ignore` or a query's `ignore` names: it can no longer
/// fire, so the entry suppresses nothing. Charts first, then queries. A chart written in place is
/// named by its public id, as Python names it.
pub fn retired_ignore_codes(
    leaves: &Leaves,
    public: &BTreeMap<ChartId, String>,
) -> Vec<Diagnostic> {
    let named = |scope: &'static str, key: &'static str| {
        leaves.models(scope).filter_map(move |(name, model)| {
            let name = match scope {
                "charts." => public_name(public, name)?,
                _ => name,
            };
            Some((format!("{scope}{name}.{key}"), model.get(key)?))
        })
    };
    named("charts.", "warnings_ignore")
        .chain(named("queries.", "ignore"))
        .flat_map(|(source, codes)| {
            let codes: BTreeSet<&str> = codes
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .collect();
            RETIRED_WARNING_CODES
                .iter()
                .filter(|c| codes.contains(*c))
                .map(|code| {
                    diag::warn_retired_ignore_code(source.clone(), *code).at_path(dotted(&source))
                })
                .collect::<Vec<_>>()
        })
        .collect()
}

/// The id a user sees for the chart the merged root holds under `key`: its name, or for a chart
/// written in place the id hoisting slugged from its title.
fn public_name<'a>(public: &'a BTreeMap<ChartId, String>, key: &'a str) -> Option<&'a str> {
    public
        .iter()
        .find(|(id, _)| id.as_str() == key)
        .map(|(_, name)| name.as_str())
}

fn is_house_format(name: &str) -> bool {
    d3rs::format::predefined_spec(name).is_some()
        || d3rs::predefined_formats::PREDEFINED_NATIVE.contains(&name)
}

/// An axis label format as authored: a name, or a mapping whose `spec` is one.
fn format_spec(v: &Value) -> Option<&str> {
    v.as_str().or_else(|| v.get("spec")?.as_str())
}

/// The font family a style slot resolves to: its own `family`, else its `extends:` font's.
fn family<'a>(style: &[&'a Value], slot: &str) -> Option<&'a str> {
    let at = |path: &str| style.iter().find_map(|s| dig(s, path)?.as_str());
    at(&format!("{slot}.family"))
        .or_else(|| at(&format!("fonts.{}.family", at(&format!("{slot}.extends"))?)))
}

/// An authored `axis_y.labels.align` a house-format alias discards: the alias forces
/// `align: right` on a right-edge quantitative axis whose font has tabular figures. Line, area and
/// scatter only (a bar's orientation, and so its axis, is decided by its data). A chart that
/// authors no format reads the board's: the merged `style` over the theme's.
pub fn axis_align_discarded(
    patch: &Patch,
    leaves: &Leaves,
    theme: &Leaves,
    public: &BTreeMap<ChartId, String>,
) -> Vec<Diagnostic> {
    let styles: Vec<&Value> = [leaves.get("style"), theme.get("style")]
        .into_iter()
        .flatten()
        .collect();
    let board = |path: &str| styles.iter().find_map(|s| dig(s, path));
    let ctx_format = board("charts.axis_quantitative.labels.format")
        .or_else(|| board("charts.axis.labels.format"))
        .and_then(format_spec)
        .map(|f| if f == "auto" { "number" } else { f });
    let ctx_font = ["charts.axis_quantitative", "charts.axis_y", "charts.axis"]
        .iter()
        .find_map(|slot| family(&styles, &format!("{slot}.labels.font")))
        .or_else(|| board("font.family")?.as_str());
    let mut out = Vec::new();
    for (id, chart) in &patch.body.charts {
        let Some(ChartAuthored::Line(_) | ChartAuthored::Area(_) | ChartAuthored::Scatter(_)) =
            chart
        else {
            continue;
        };
        let Some(chart) = leaves.get(&format!("charts.{id}")) else {
            continue;
        };
        let Some(id) = public_name(public, id.as_str()) else {
            continue;
        };
        let at = |field: &str| dig(chart, &format!("style.axis_y.{field}"));
        if at("position").and_then(Value::as_str) != Some("right") {
            continue;
        }
        let Some(align) = at("labels.align") else {
            continue;
        };
        let tabular = at("labels.font.family")
            .and_then(Value::as_str)
            .or(ctx_font)
            .is_some_and(display_text::font::family_is_tabular);
        let alias = at("labels.format").and_then(format_spec).or(ctx_format);
        if let Some(alias) = alias.filter(|a| tabular && is_house_format(a)) {
            out.push(diag::warn_axis_align_discarded(
                id,
                align.as_str().unwrap_or_default(),
                alias,
            ));
        }
    }
    out
}

/// The theme in force, as leaves under the same paths the merged root's have.
pub fn theme_leaves(name: &str) -> Leaves {
    match built_in_theme(name) {
        Ok(body) => Leaves::of(&Patch {
            body: body.clone(),
            ..Default::default()
        }),
        Err(_) => Leaves(Vec::new()),
    }
}

/// `ALL_PREDEFINED_NAMES`: the engine's number, time and text format names, sorted.
fn predefined_names() -> Vec<&'static str> {
    use d3rs::predefined_formats::{PREDEFINED_NATIVE, PREDEFINED_SPECS, PREDEFINED_TIME_SPECS};
    let mut names: Vec<&str> = PREDEFINED_SPECS.iter().map(|s| s.name).collect();
    names.extend(PREDEFINED_NATIVE);
    names.extend(PREDEFINED_TIME_SPECS.iter().map(|(n, _)| *n));
    names.extend([
        "date_month",
        "date_quarter",
        "date_week",
        "date_year",
        "email",
        "url",
    ]);
    names.sort_unstable();
    names.dedup();
    names
}

/// `difflib.SequenceMatcher(None, a, b).ratio()`: twice the characters matched by recursively
/// taking the longest common block, over both lengths. (The autojunk threshold sits at 200
/// characters; a format name is shorter.)
fn ratio(a: &[char], b: &[char]) -> f64 {
    fn matched(a: &[char], b: &[char]) -> usize {
        let mut best = (0, 0, 0);
        for i in 0..a.len() {
            for j in 0..b.len() {
                let run = a
                    .iter()
                    .skip(i)
                    .zip(b.iter().skip(j))
                    .take_while(|(x, y)| x == y)
                    .count();
                if run > best.0 {
                    best = (run, i, j);
                }
            }
        }
        let (run, i, j) = best;
        match run {
            0 => 0,
            _ => {
                let (a_before, a_rest) = a.split_at(i);
                let (b_before, b_rest) = b.split_at(j);
                run + matched(a_before, b_before)
                    + matched(
                        a_rest.get(run..).unwrap_or_default(),
                        b_rest.get(run..).unwrap_or_default(),
                    )
            }
        }
    }
    match a.len() + b.len() {
        0 => 1.0,
        n => 2.0 * matched(a, b) as f64 / n as f64,
    }
}

/// ` Did you mean ...?` for a name close to an engine one: the engine names it prefixes, else
/// the three closest by `difflib.get_close_matches(cutoff=0.7)`.
fn engine_name_hint(name: &str) -> String {
    let engines = predefined_names();
    let mut related: Vec<&str> = engines
        .iter()
        .copied()
        .filter(|e| e.starts_with(name))
        .collect();
    if related.is_empty() {
        let chars: Vec<char> = name.chars().collect();
        let mut close: Vec<(f64, &str)> = engines
            .iter()
            .map(|e| (ratio(&e.chars().collect::<Vec<_>>(), &chars), *e))
            .filter(|(score, _)| *score >= 0.7)
            .collect();
        close.sort_by(|a, b| b.0.total_cmp(&a.0).then(b.1.cmp(a.1)));
        related = close.into_iter().take(3).map(|(_, e)| e).collect();
    }
    match related.is_empty() {
        true => String::new(),
        false => format!(
            " Did you mean {}?",
            related
                .iter()
                .map(|e| format!("`{e}`"))
                .collect::<Vec<_>>()
                .join(" or ")
        ),
    }
}

/// The names a `format('name'` call reads in `text`.
fn format_calls(text: &str, used: &mut BTreeSet<String>) {
    for (at, _) in text.match_indices("format(") {
        let (before, call) = text.split_at(at);
        if before
            .chars()
            .next_back()
            .is_some_and(|c| c.is_alphanumeric() || c == '_')
        {
            continue;
        }
        let mut chars = call.trim_start_matches("format(").trim_start().chars();
        let Some(quote) = chars.next().filter(|q| matches!(q, '\'' | '"')) else {
            continue;
        };
        let name: String = chars.take_while(|c| *c != quote).collect();
        if !name.is_empty() {
            used.insert(name);
        }
    }
}

/// Every format name a `format:` slot or a `format('name')` call in `v` reads.
fn names_read(v: &Value, used: &mut BTreeSet<String>) {
    match v {
        Value::String(s) if s.contains("format(") => format_calls(s, used),
        Value::Array(items) => items.iter().for_each(|i| names_read(i, used)),
        Value::Object(map) => {
            for (k, child) in map {
                match (k.as_str(), format_spec(child)) {
                    ("format", Some(name)) => {
                        used.insert(name.to_string());
                    }
                    _ => names_read(child, used),
                }
            }
        }
        _ => {}
    }
}

/// A name the board's own `style.formats` defines and nothing reads: not a slot, not a query's
/// column format, not a `format('name')` call. Names a `meta.yml` defines are not judged: a
/// sibling board this compile cannot see may use them.
pub fn unused_format_names(own: &BTreeSet<String>, leaves: &Leaves) -> Vec<Diagnostic> {
    let engine = predefined_names();
    let judged: Vec<&String> = own
        .iter()
        .filter(|n| !engine.contains(&n.as_str()))
        .collect();
    if judged.is_empty() {
        return Vec::new();
    }
    let mut used = BTreeSet::new();
    for (_, v) in &leaves.0 {
        names_read(v, &mut used);
    }
    for (_, query) in leaves.models("queries.") {
        let columns = dig(query, "formats").and_then(Value::as_object);
        used.extend(
            columns
                .into_iter()
                .flat_map(|c| c.values())
                .filter_map(Value::as_str)
                .map(str::to_string),
        );
    }
    judged
        .into_iter()
        .filter(|n| !used.contains(*n))
        .map(|name| {
            diag::warn_format_unused_name(name.as_str(), engine_name_hint(name))
                .at_path(path(&["style", "formats", name]))
        })
        .collect()
}
