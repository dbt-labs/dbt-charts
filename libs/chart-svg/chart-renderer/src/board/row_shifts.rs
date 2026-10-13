//! The row title band's derivation: the shift every layout item of a `cols`
//! row carries so its titled cells share one first baseline and one body top.
//!
//! Production computes it once per sized board, from each cell's resolved
//! title (`layout_sizing.py::_assign_title_shifts_in_tree`, after the item
//! boxes are sized and before cols alignment), and stores it on the layout item
//! (`LayoutItem.title_shift`). The compile stage never sets one; the shift is a
//! render-time sizing product because it needs the resolved title fonts. Here
//! the same walk runs once at the top of `render_board`, over the sized boxes,
//! and writes the result onto the items `render_chart_item` and
//! `render_nested_board` already read. The law itself (`row_title_shifts`,
//! `chart_title_metrics`) lives in `title_shift.rs` beside the application.
//!
//! A payload that already carries a non-zero shift (the sweep harness hands over
//! production's) is a check, never an input: it must equal the derived value.

use std::collections::BTreeMap;

use serde_json::Value;

use super::{
    active_tab_index, effective_padding, item_subtree_untouched, item_visible,
    nested_child_gap, nested_content_width, slot_dims, BoardDoc, BoardPayload, ItemDoc, LayoutDoc,
};
use crate::text::FontBook;
use crate::title;
use crate::title_shift::{chart_title_metrics, row_title_shifts, TitleMetrics};
use crate::TitleShift;

/// One drawn layout item's shift: what the payload carries, and what the
/// row's titles derive.
pub struct RowShift {
    /// Item index at each nesting level, root first.
    pub path: Vec<usize>,
    pub supplied: TitleShift,
    pub derived: TitleShift,
}

impl RowShift {
    /// The item's JSON pointer into `BoardPayload.resolved`.
    pub fn pointer(&self) -> String {
        let mut pointer = String::new();
        for (depth, index) in self.path.iter().enumerate() {
            let board = if depth == 0 { "" } else { "/board" };
            pointer.push_str(&format!("{board}/layout/items/{index}"));
        }
        pointer
    }

    fn agrees(&self) -> bool {
        // A serialized float round-trips exactly, so anything wider than rounding
        // noise is a different shift; 1e-9 is the slack `normalized.py:59` allows
        // between `body_dy` and `title_dy`.
        (self.supplied.title_dy - self.derived.title_dy).abs() <= 1e-9
            && (self.supplied.body_dy - self.derived.body_dy).abs() <= 1e-9
    }
}

/// `sizing.py:1223` `active_layout_items`: the items a layout draws.
fn drawn_indices(layout: &LayoutDoc, payload: &BoardPayload) -> Result<Vec<usize>, String> {
    let active = active_tab_index(layout, payload)?;
    let mut drawn = Vec::new();
    for (index, item) in layout.items.iter().enumerate() {
        if active.is_some_and(|a| a != index) {
            continue;
        }
        if item_visible(&item.visible, &payload.variable_values)? {
            drawn.push(index);
        }
    }
    Ok(drawn)
}

fn child_path(path: &[usize], index: usize) -> Vec<usize> {
    path.iter().copied().chain([index]).collect()
}

/// A cell's title geometry and the layout item that draws that title.
struct Target {
    path: Vec<usize>,
    metrics: TitleMetrics,
}

struct Walk<'a> {
    payload: &'a BoardPayload,
    fonts: &'a FontBook,
    drawn: Vec<(Vec<usize>, TitleShift)>,
    derived: BTreeMap<Vec<usize>, TitleShift>,
}

impl Walk<'_> {
    /// `layout_sizing.py::_assign_title_shifts_in_tree`. `boxes` is
    /// `size_board_items_json`'s boxes for this layout's items, nested boxes
    /// under `"nested"`.
    fn walk_layout(
        &mut self,
        layout: &LayoutDoc,
        boxes: &[Value],
        card_pad: f64,
        path: &[usize],
    ) -> Result<(), String> {
        let drawn = drawn_indices(layout, self.payload)?;
        for &index in &drawn {
            self.drawn.push((child_path(path, index), layout.items[index].title_shift));
        }
        if layout.kind == "cols" {
            let mut targets = Vec::with_capacity(drawn.len());
            for &index in &drawn {
                let item_box = boxes.get(index).ok_or("title band: item boxes shorter than the layout")?;
                targets.push(self.band_target(
                    &layout.items[index],
                    item_box,
                    card_pad,
                    child_path(path, index),
                )?);
            }
            let metrics: Vec<_> = targets.iter().map(|t| t.as_ref().map(|t| t.metrics)).collect();
            for (target, shift) in targets.iter().zip(row_title_shifts(&metrics)) {
                if let Some(target) = target {
                    self.derived.insert(target.path.clone(), shift);
                }
            }
        }
        for &index in &drawn {
            let item = &layout.items[index];
            // A collapsed details section is never sized or drawn.
            if item.kind != "board" || item_subtree_untouched(item, self.payload)? {
                continue;
            }
            let nested = parse_nested(item)?;
            let item_box = boxes.get(index).ok_or("title band: item boxes shorter than the layout")?;
            self.walk_layout(
                &nested.layout,
                nested_boxes(item_box)?,
                nested.style.frame.card_padding,
                &child_path(path, index),
            )?;
        }
        Ok(())
    }

    /// `layout_sizing.py::_title_band_target`: the layout item that draws a
    /// cols cell's title, with its title geometry. An untitled board that only wraps
    /// a stack (the way a `width:` is authored) draws its first item's title, so
    /// that item is the one to shift. `None`: the cell has no title this row can align.
    fn band_target(
        &self,
        item: &ItemDoc,
        item_box: &Value,
        card_pad: f64,
        path: Vec<usize>,
    ) -> Result<Option<Target>, String> {
        // A details section draws its summary bar at its top edge, not a title.
        if item.details_variable.as_deref().is_some_and(|v| !v.is_empty()) {
            return Ok(None);
        }
        let width = item_box["width"].as_f64().ok_or("title band: item box carries no width")?;
        if let Some(chart) = &item.chart {
            let chart_type = chart["chart_type"].as_str().ok_or("title band: chart missing chart_type")?;
            let (slot_width, _, _) =
                slot_dims(width, 0.0, chart_type, card_pad, &chart["layout_padding"])?;
            let metrics = chart_title_metrics(
                chart,
                card_pad,
                slot_width,
                self.fonts,
                self.payload.board_style.spark_bar_title.as_ref(),
            )?;
            return Ok(metrics.map(|metrics| Target { path, metrics }));
        }
        // A `chart_error` item resolved no chart (`_require_resolved` returns None).
        if item.kind != "board" {
            return Ok(None);
        }
        let nested = parse_nested(item)?;
        let style = &nested.style;
        let nested_pad = style.frame.card_padding;
        let wrapper_top = style.margin.unwrap_or_default().top + effective_padding(style).top;
        let visible_variables = nested.visible_variables();
        if nested.title.is_empty() {
            if !nested.text.is_empty() || !visible_variables.is_empty() || nested.layout.kind != "rows" {
                return Ok(None);
            }
            let Some(&first) = drawn_indices(&nested.layout, self.payload)?.first() else {
                return Ok(None);
            };
            let inner_box = nested_boxes(item_box)?
                .get(first)
                .ok_or("title band: nested boxes shorter than the layout")?;
            let inner = self.band_target(
                &nested.layout.items[first],
                inner_box,
                nested_pad,
                child_path(&path, first),
            )?;
            return Ok(inner.map(|t| Target {
                path: t.path,
                metrics: TitleMetrics { ascent: t.metrics.ascent + wrapper_top, tail: t.metrics.tail },
            }));
        }
        let content_width = nested_content_width(&nested, width);
        let child_gap = nested_child_gap(&nested);
        let fonts_dir = super::fonts_dir_str(self.payload);
        // The title shares a band with its variable controls: that layout sizes
        // itself and is not aligned (`title_band.py:183`).
        if crate::variables::should_use_title_inline_band(
            &nested.title,
            &visible_variables,
            content_width,
            nested.title_style.as_ref(),
            nested.title_fonts.as_ref(),
            nested.style.title.font.case.as_deref(),
            &style.variables,
            nested_pad,
            &self.payload.variable_values,
            fonts_dir,
        )? {
            return Ok(None);
        }
        let title_style = nested
            .title_style
            .clone()
            .ok_or("nested board has a title but payload carries no title_style")?;
        let title_fonts = nested
            .title_fonts
            .clone()
            .ok_or("nested board has a title but payload carries no title_fonts")?;
        // `title_band.py:173-215` `board_title_metrics`.
        let inner = (content_width - 2.0 * nested_pad).max(1.0);
        let block = title::get_title_height(
            &nested.title,
            &self.payload.variable_values,
            style.title.font.case.as_deref(),
            inner,
            title_style.clone(),
            title_fonts.clone(),
        )?
        .max(style.title.min_height);
        let baseline = title::title_baseline_offset(
            &nested.title,
            &self.payload.variable_values,
            style.title.font.case.as_deref(),
            inner,
            title_style,
            title_fonts,
        )?;
        // A title flows straight into text below it. Layout items and controls sit
        // one gap down, and an item's ink starts a card padding inside its own box.
        let gap_below = if !nested.text.is_empty() {
            0.0
        } else if !nested.layout.items.is_empty() {
            child_gap + nested_pad
        } else if !visible_variables.is_empty() {
            child_gap
        } else {
            0.0
        };
        Ok(Some(Target {
            path,
            metrics: TitleMetrics {
                ascent: wrapper_top + nested_pad + baseline,
                tail: block - baseline + gap_below,
            },
        }))
    }
}

fn parse_nested(item: &ItemDoc) -> Result<BoardDoc, String> {
    let json = item.board.as_ref().ok_or("board item missing its resolved board")?;
    serde_json::from_value(json.clone()).map_err(|e| format!("nested board payload deserialize: {e}"))
}

fn nested_boxes(item_box: &Value) -> Result<&[Value], String> {
    item_box["nested"]
        .as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| "title band: a board item's boxes carry no nested boxes".to_string())
}

/// Every drawn layout item's supplied and derived shift, in tree order.
/// `items` is `size_board_items_json`'s output for `payload`.
pub fn derive_title_shifts(
    payload: &BoardPayload,
    fonts: &FontBook,
    items: &[Value],
) -> Result<Vec<RowShift>, String> {
    let board: BoardDoc = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("board payload deserialize: {e}"))?;
    derive(&board, payload, fonts, items)
}

fn derive(
    board: &BoardDoc,
    payload: &BoardPayload,
    fonts: &FontBook,
    items: &[Value],
) -> Result<Vec<RowShift>, String> {
    let card_pad = board.card_padding.ok_or("board card_padding missing (nested board?)")?;
    let mut walk =
        Walk { payload, fonts, drawn: Vec::new(), derived: BTreeMap::new() };
    walk.walk_layout(&board.layout, items, card_pad, &[])?;
    Ok(walk
        .drawn
        .into_iter()
        .map(|(path, supplied)| {
            let derived = walk.derived.get(&path).copied().unwrap_or_default();
            RowShift { path, supplied, derived }
        })
        .collect())
}

/// Derive the board's row shifts and write them onto the layout items
/// `render_chart_item` and `render_nested_board` read. A non-zero shift the
/// payload already carries must equal the derived one; a zero one carries no
/// claim (the compile stage emits zero for every item).
pub(super) fn assign(
    board: &mut BoardDoc,
    payload: &BoardPayload,
    fonts: &FontBook,
    items: &[Value],
) -> Result<(), String> {
    let shifts = derive(board, payload, fonts, items)?;
    for shift in &shifts {
        if !shift.supplied.is_zero() && !shift.agrees() {
            return Err(format!(
                "title shift at {}: the payload supplies {:?} but the row's titles derive {:?}",
                shift.pointer(),
                shift.supplied,
                shift.derived
            ));
        }
    }
    for shift in shifts.iter().filter(|s| !s.derived.is_zero()) {
        set_shift(board, &shift.path, shift.derived)?;
    }
    Ok(())
}

fn set_shift(board: &mut BoardDoc, path: &[usize], shift: TitleShift) -> Result<(), String> {
    let (&root, rest) = path.split_first().ok_or("title shift: empty item path")?;
    let item = board.layout.items.get_mut(root).ok_or("title shift: item path outside the layout")?;
    if rest.is_empty() {
        item.title_shift = shift;
        return Ok(());
    }
    let pointer: String = rest
        .iter()
        .map(|i| format!("/layout/items/{i}"))
        .collect::<Vec<_>>()
        .join("/board");
    let nested = item.board.as_mut().ok_or("title shift: item path through a chart")?;
    let nested_item = nested
        .pointer_mut(&pointer)
        .ok_or("title shift: item path outside the nested layout")?;
    nested_item["title_shift"] = serde_json::json!({"title_dy": shift.title_dy, "body_dy": shift.body_dy});
    Ok(())
}
