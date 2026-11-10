//! Board ids by tree position: the pass that writes onto the merged root the ids
//! [`LayoutFacts::board_ids`] holds, as Python's normalize gives them to its boards.
//!
//! A nested board's authored `id:` does not survive: Python names the board by where it sits, and
//! reads the authored one only for the variable of a collapsible board, which the layout facts
//! already hold.

use crate::index::{FactsError, LayoutFacts};
use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored};
use board_model::board::Board;
use board_model::ids::{BoardId, ItemId};
use model_core::Leaf;

type Patch = <Board as Leaf>::Patch;

/// Writes `facts.board_ids` onto the root and every nested board of `patch`.
pub fn fill_ids(patch: &mut Patch, facts: &LayoutFacts) -> Result<(), FactsError> {
    let root = ItemId::root();
    patch.body.id = Some(id_at(facts, &root)?);
    match &mut patch.body.layout {
        Some(l) => layout(l, facts, &root),
        None => Ok(()),
    }
}

fn id_at(facts: &LayoutFacts, pos: &ItemId) -> Result<BoardId, FactsError> {
    let no_id = || FactsError::NoBoardId { pos: pos.clone() };
    let id = facts.board_ids.get(pos).ok_or_else(no_id)?;
    BoardId::try_from(id.as_str()).map_err(|_| no_id())
}

fn layout(l: &mut LayoutAuthored, facts: &LayoutFacts, pos: &ItemId) -> Result<(), FactsError> {
    match l {
        LayoutAuthored::Rows(items) => {
            let base = pos.key("rows");
            items
                .iter_mut()
                .enumerate()
                .try_for_each(|(i, item)| self::item(item, facts, &base.index(i)))
        }
        LayoutAuthored::Cols(items) => {
            let base = pos.key("cols");
            items
                .iter_mut()
                .enumerate()
                .try_for_each(|(i, item)| self::item(item, facts, &base.index(i)))
        }
        LayoutAuthored::Grid(g) => {
            let base = pos.key("grid").key("items");
            for (i, cell) in g.items.iter_mut().flatten().enumerate() {
                if let Some(it) = &mut cell.item {
                    self::item(it, facts, &base.index(i).key("item"))?;
                }
            }
            Ok(())
        }
        LayoutAuthored::Tabs(t) => {
            let base = pos.key("tabs").key("items");
            for (i, tab) in t.items.iter_mut().flatten().enumerate() {
                if let Some(l) = &mut tab.layout {
                    layout(l, facts, &base.index(i))?;
                }
            }
            Ok(())
        }
    }
}

fn item(
    item: &mut LayoutItemAuthored,
    facts: &LayoutFacts,
    pos: &ItemId,
) -> Result<(), FactsError> {
    let LayoutItemAuthored::Board(nested) = item else {
        return Ok(());
    };
    nested.body.id = Some(id_at(facts, pos)?);
    match &mut nested.body.layout {
        Some(l) => layout(l, facts, pos),
        None => Ok(()),
    }
}
