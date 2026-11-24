//! The board's prose plan, decided once ahead of sizing and drawing: `render/prose.py`'s `_run_grid`,
//! `_Runs`, `_placing_gap`, `_gather_runs`, `_goes_multi_column`, `_drawn_text` and `plan_board_prose`.
//! Runs of stacked prose share one grid, the thirds or halves of the root's content width; each text
//! then takes as many of its spans as it earns. Sizing and drawing both read the plan instead of
//! deciding anything themselves, so they cannot disagree.

use std::collections::BTreeMap;

use bind::bound::DrawnSet;
use board_model::board::layout::{Layout, LayoutItem, Placement};
use board_model::board::{items, BoardBody};
use board_model::ids::ItemId;
use diagnostic::{diag, Diagnostic};
use mdsvg::types::{AnyBlock, ListItem, Paragraph};

use crate::board::assemble::{board_geometry, Facts};
use crate::board::prose::{choose_grid, grid_span, slot_width, text_setup, Source};
use crate::board::prose_flow::{columns_of, Grid, Text};

/// `ProsePlan`: the grid a run of stacked prose sits on, and whether the board's lone columns keep to
/// their span (`snap`: some drawn block on the board really lays out in two or more columns).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Plan {
    pub grid: Grid,
    pub snap: bool,
}

/// The plan of every board that has text, by where the board sits.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ProsePlans(BTreeMap<ItemId, Plan>);

impl ProsePlans {
    /// The plan of the board at `at`; `None` when it has no text.
    pub fn get(&self, at: &ItemId) -> Option<Plan> {
        self.0.get(at).copied()
    }
}

/// A text-bearing board and where it sits.
#[derive(Clone)]
struct Member<'a> {
    at: ItemId,
    body: &'a BoardBody,
}

/// One layout being walked: the board's own text, its layout and the items of it, and the gap its
/// cards are pitched by (`card_row_gap`).
struct Level<'a> {
    text: Option<Member<'a>>,
    layout: &'a Layout,
    placements: Vec<Placement<'a>>,
    gap: f64,
    /// A board's own body and place, which a tabs layout reads its panes from; a pane has none.
    owner: Option<(&'a BoardBody, ItemId)>,
}

/// `_Runs`: the runs of stacked prose found so far, each with the card gap it sits on.
struct Runs<'a, F> {
    facts: &'a F,
    drawn: &'a DrawnSet,
    card_gap: f64,
    runs: Vec<(Vec<Member<'a>>, f64)>,
    /// The gap of the card layout walked most recently: prose with no card row after it is adjacent to
    /// the one above.
    last_gap: Option<f64>,
}

impl<'a, F: Facts> Runs<'a, F> {
    fn close(&mut self, run: Vec<Member<'a>>, gap: f64) -> Vec<Member<'a>> {
        if !run.is_empty() {
            self.runs.push((run, gap));
        }
        Vec::new()
    }

    /// Finishes a run no card row follows: the row above it, else the board's own non-root gap.
    fn close_unended(&mut self, run: Vec<Member<'a>>, gap: f64) -> Vec<Member<'a>> {
        let gap = self.last_gap.unwrap_or(gap);
        self.close(run, gap)
    }

    /// `card_row_gap`: a root cols or grid pitches its cards by its own type's gap, any other board by
    /// its `style.gap` (0 when unset); either way plus the card gap.
    fn row_gap(&self, body: &BoardBody, is_root: bool) -> Result<f64, Diagnostic> {
        let geometry = board_geometry(self.facts.style(body)?)?;
        let own = match body.layout {
            Layout::Cols(_) | Layout::Grid(_) if is_root => {
                let layout_gap = if matches!(body.layout, Layout::Cols(_)) { geometry.cols_gap } else { geometry.grid_gap };
                if body.card_gap { 0.0 } else { layout_gap }
            }
            _ => geometry.gap.unwrap_or(0.0),
        };
        Ok(own + self.card_gap)
    }

    fn pane_gap(&self, body: &BoardBody, tab: usize) -> Result<f64, Diagnostic> {
        Ok(board_geometry(self.facts.pane_style(body, tab)?)?.gap.unwrap_or(0.0) + self.card_gap)
    }

    /// `_placing_gap`: the card gap of the layout `body` holds its items in. Tab panels are full width and
    /// pitch no cards themselves; prose above the tabs takes the active panel's.
    fn placing_gap(&self, body: &BoardBody, is_root: bool) -> Result<f64, Diagnostic> {
        match (&body.layout, self.drawn.active_tab(&body.id)) {
            (Layout::Tabs(_), Some(open)) => self.pane_gap(body, open),
            (Layout::Tabs(_), None) => self.row_gap(body, false),
            _ => self.row_gap(body, is_root),
        }
    }

    fn level(&self, body: &'a BoardBody, at: &ItemId, is_root: bool) -> Result<Level<'a>, Diagnostic> {
        let text = super::prose::board_text(body).is_some().then(|| Member { at: at.clone(), body });
        Ok(Level { text, layout: &body.layout, placements: items(body, at).collect(), gap: self.placing_gap(body, is_root)?, owner: Some((body, at.clone())) })
    }

    /// `_gather_runs`: collects a board's prose into runs of stacked blocks and returns the open run. A
    /// run is prose stacked with nothing between it but more prose, so only a chart or a
    /// `cols`/`grid`/`tabs` layout ends one: those have card edges of their own, and their gap is the
    /// run's. A card's text is its own run, on the gap of the layout holding the card.
    fn gather(&mut self, level: Level<'a>, mut run: Vec<Member<'a>>) -> Result<Vec<Member<'a>>, Diagnostic> {
        run.extend(level.text);
        if matches!(level.layout, Layout::Rows(_)) {
            for placement in level.placements {
                run = match placement.item {
                    LayoutItem::Chart { .. } => self.close_unended(run, level.gap),
                    LayoutItem::Board(nested) => {
                        let inner = self.level(&nested.body, &placement.id, false)?;
                        self.gather(inner, run)?
                    }
                };
            }
            return Ok(run);
        }
        self.close(run, level.gap);
        if let Layout::Tabs(tabs) = level.layout {
            let (body, at) = level.owner.ok_or_else(|| diag::err_internal("a tabs layout inside a tab is refused before it is planned"))?;
            for (index, tab) in tabs.values().enumerate() {
                // Each pane starts from the row holding it, not from a card beside it.
                let gap = self.pane_gap(body, index)?;
                self.last_gap = Some(gap);
                let pane = Level {
                    text: None,
                    layout: &tab.body.layout,
                    placements: items(body, &at).filter(|placement| placement.pane.first() == Some(&index)).collect(),
                    gap,
                    owner: None,
                };
                let inner = self.gather(pane, Vec::new())?;
                self.close(inner, gap);
            }
        } else {
            for placement in level.placements {
                if let LayoutItem::Board(nested) = placement.item {
                    self.last_gap = Some(level.gap);
                    let inner = self.level(&nested.body, &placement.id, false)?;
                    let inner = self.gather(inner, Vec::new())?;
                    self.close(inner, level.gap);
                }
            }
        }
        self.last_gap = Some(level.gap);
        Ok(Vec::new())
    }
}

/// The top-level list items of a passage's blocks.
fn list_items(blocks: &[AnyBlock]) -> impl Iterator<Item = &ListItem> {
    blocks.iter().flat_map(|block| match block {
        AnyBlock::UnorderedList(list) => list.items.iter(),
        AnyBlock::OrderedList(list) => list.items.iter(),
        _ => [].iter(),
    })
}

/// The lines a list item's own text wraps to at `width`.
fn item_lines(text: &Text, item: &ListItem, width: f64) -> Result<usize, Diagnostic> {
    let paragraph = vec![AnyBlock::Paragraph(Paragraph { spans: item.spans.clone() })];
    Ok(text.metrics(&paragraph, width)?[0].line_advances.len())
}

/// The text a member draws, set up the way sizing and drawing set it up.
fn setup<F: Facts>(facts: &F, member: &Member) -> Result<Text, Diagnostic> {
    let Source { style, text, book } = facts.prose(member.body)?.ok_or_else(|| diag::err_internal("a planned board has text"))?;
    text_setup(style, text, book)
}

/// `_run_grid`: the grid for a run, the narrowest any block needs, and halves if it is list-heavy. List
/// heavy means more than half of the run's list items fit on one line at the halves measure but wrap at
/// the thirds measure, at the board's width.
fn run_grid<F: Facts>(run: &[Member], drawn: &BTreeMap<ItemId, f64>, board_width: f64, gap: f64, facts: &F) -> Result<usize, Diagnostic> {
    let (mut items, mut one_line_at_halves) = (0usize, 0usize);
    let mut grid = 3;
    for member in run.iter().filter(|member| drawn.contains_key(&member.at)) {
        let text = setup(facts, member)?;
        grid = grid.min(choose_grid(board_width, gap, text.pad, text.char_px));
        let list: Vec<&ListItem> = list_items(&text.blocks).collect();
        if list.is_empty() {
            continue;
        }
        let measure = |count: usize| text.measure(grid_span(board_width, count, gap) - 2.0 * text.pad) - text.renderer.style.list_indent;
        let (half, third) = (measure(2), measure(3));
        items += list.len();
        for item in list {
            if item_lines(&text, item, half)? == 1 && item_lines(&text, item, third)? > 1 {
                one_line_at_halves += 1;
            }
        }
    }
    Ok(if one_line_at_halves * 2 > items { grid.min(2) } else { grid })
}

/// `_goes_multi_column`: whether a block, at its real `slot` width, lays out in two or more columns.
fn goes_multi_column<F: Facts>(member: &Member, content_width: f64, grid: Grid, facts: &F) -> Result<bool, Diagnostic> {
    let text = setup(facts, member)?;
    Ok(columns_of(&text, slot_width(content_width, text.pad), grid)?.stretches.iter().any(|stretch| stretch.n_cols() > 1))
}

/// `plan_board_prose`: chooses each run of prose's grid once. `board_width` is the root board's content
/// width, the width its cards (and so its prose columns) divide; `slots` maps every drawn board to the
/// content width its box gives it, the width its text is sized and drawn at (`prose_slot_widths`).
/// A drawn board is a placed one under no collapsed details section or closed tab: only text that is
/// drawn chooses a grid or snaps the board's lone columns.
pub fn plan_board_prose<F: Facts>(root: &BoardBody, slots: &BTreeMap<ItemId, f64>, board_width: f64, card_gap: f64, drawn: &DrawnSet, facts: &F) -> Result<ProsePlans, Diagnostic> {
    let mut found = Runs { facts, drawn, card_gap, runs: Vec::new(), last_gap: None };
    let root_at = ItemId::root();
    let level = found.level(root, &root_at, true)?;
    let open = found.gather(level, Vec::new())?;
    let root_gap = found.row_gap(root, false)?;
    found.close_unended(open, root_gap);
    let runs = found.runs;
    let mut grids = Vec::with_capacity(runs.len());
    for (run, gap) in &runs {
        grids.push(Grid { count: run_grid(run, slots, board_width, *gap, facts)?, board_width, gap: *gap });
    }
    // Board-scoped: one block that really lays out in columns, at the slot the layout gives it, snaps
    // every lone column on the board to its span.
    let mut snap = false;
    'runs: for ((run, _), grid) in runs.iter().zip(&grids) {
        for member in run {
            if let Some(&content_width) = slots.get(&member.at) {
                if goes_multi_column(member, content_width, *grid, facts)? {
                    snap = true;
                    break 'runs;
                }
            }
        }
    }
    let mut plans = BTreeMap::new();
    for ((run, _), grid) in runs.iter().zip(grids) {
        for member in run {
            plans.insert(member.at.clone(), Plan { grid, snap });
        }
    }
    Ok(ProsePlans(plans))
}
