//! The pager of a table that scrolls its rows, as a static export draws it (`interactive_controls` is false: no
//! page runtime binds it). A page is a viewport of rows: one pager group per page, the one the body is scrolled to
//! shown by `table_scroll.js`. The laws are `chart-renderer/src/families/table/layout.rs`'s: `emit_scroll_pagers`,
//! `render_pagination_controls`, `range_label_attrs` and `paginator_window`.

use board_model::style::table::PaginatorStyle;
use diagnostic::{diag, Diagnostic};
use measure::shaped::{Font, FontBook};
use scene::{Attrs, Node, Role};

use super::num_full;

/// table.rs:`POOL_PAGER_MAX_PAGES`: past this many pages only the compact pager is drawn, which a per-page pager
/// (about a hundred elements each) would swamp.
const POOL_PAGER_MAX_PAGES: usize = 50;
/// table.rs:`PAGINATOR_SIBLING_COUNT` and `PAGINATOR_BOUNDARY_COUNT`: the window's MUI-style sizing.
const SIBLING_COUNT: i64 = 1;
const BOUNDARY_COUNT: i64 = 1;
/// table.rs:`PAGINATOR_AUX_SLOT_RATIO`: the prev/next and ellipsis slots, a share of the item width.
const AUX_SLOT_RATIO: f64 = 0.66;
const PREV_CHEVRON: &str = "\u{2039}";
const NEXT_CHEVRON: &str = "\u{203a}";
const ELLIPSIS: &str = "\u{2026}";

/// The viewport of rows a pager pages through. `ends` are the rows' bottoms from the first row's top.
pub(super) struct Viewport<'a> {
    pub id: &'a str,
    /// The table's width, which the controls anchor their right edge to.
    pub width: f64,
    /// The pager's top.
    pub y: f64,
    pub height: f64,
    pub page_rows: usize,
    pub ends: &'a [f64],
}

/// layout.rs:`emit_scroll_pagers`: one group naming the viewport, holding a pager per page, or the compact pager
/// alone past `POOL_PAGER_MAX_PAGES`.
pub(super) fn scroll_pagers(theme: &PaginatorStyle, body_family: &str, book: &FontBook, view: &Viewport) -> Result<Node, Diagnostic> {
    let total = view.ends.len();
    let pages = total.div_ceil(view.page_rows);
    let per_page = pages <= POOL_PAGER_MAX_PAGES;
    let template = display_text::language::tr("controls.page_range", None, &[("first", "{first}"), ("last", "{last}"), ("total", "{total}")])?;
    let ends: Vec<String> = view.ends.iter().map(|end| num_full(*end)).collect();
    let page_var = format!("{}_page", view.id);
    let pager = |id: String, page: usize, hidden: bool, compact: bool| -> Result<Node, Diagnostic> {
        let first = (page - 1) * view.page_rows;
        let range = (first + 1, (first + view.page_rows).min(total), total);
        let shown = if compact { 2 } else { pages as i64 };
        let controls = controls(theme, body_family, book, (page as i64, shown), &page_var, (view.width, view.y), range, compact)?;
        Ok(Node::group(Role::Chrome, Attrs::new().quick("data-dbt-scroll-pager", id).set_if(hidden, "display", "none"), controls))
    };
    let mut children = Vec::new();
    if per_page {
        for page in 1..=pages {
            children.push(pager(page.to_string(), page, page != 1, false)?);
        }
    } else {
        children.push(pager("compact".to_string(), 1, false, true)?);
    }
    let attrs = Attrs::new()
        .quick("data-dbt-scroll-range", total.to_string())
        .quick("data-dbt-scroll-for", view.id)
        .quick("data-template", template)
        .quick("data-row-ends", ends.join(","))
        .quick("data-viewport", num_full(view.height))
        .quick("data-page-rows", view.page_rows.to_string());
    Ok(Node::group(Role::Chrome, attrs, children))
}

/// layout.rs:`paginator_window`: the numeric pages around `page` with an ellipsis for each gap, `None`.
fn window(page: i64, total: i64) -> Vec<Option<i64>> {
    let mut must_show: Vec<i64> = (1..=BOUNDARY_COUNT.min(total)).collect();
    must_show.extend((total - BOUNDARY_COUNT + 1).max(1)..=total);
    must_show.extend((page - SIBLING_COUNT).max(1)..=(page + SIBLING_COUNT).min(total));
    must_show.sort_unstable();
    must_show.dedup();
    let mut items = Vec::new();
    let mut prev = 0;
    for p in must_show {
        if p > prev + 1 {
            items.push(None);
        }
        items.push(Some(p));
        prev = p;
    }
    items
}

/// layout.rs:`PaginatorTheme.font.size_or(11.0)` as an integer: the label's and the glyphs' size.
fn font_size(theme: &PaginatorStyle) -> Result<i64, Diagnostic> {
    let size = theme.font.as_ref().and_then(|font| font.size).ok_or_else(|| diag::err_internal("style.charts.table.paginator.font.size is unset after the theme cascade"))?;
    Ok(size as i64)
}

/// A pager item: its role in the control, the glyph it draws and the page it targets (`0` is disabled).
struct Item {
    role: &'static str,
    glyph: String,
    target: String,
}

/// layout.rs:`render_pagination_controls` for a static export: the muted row-range label, then right-anchored chevrons
/// and windowed page numbers, each clickable one with a hit rect `table_scroll.js` reads. `compact` names its
/// neighbors relatively and draws no page numbers: the host runtime knows the filtered page count, this render does not.
#[allow(clippy::too_many_arguments)]
fn controls(theme: &PaginatorStyle, body_family: &str, book: &FontBook, (page, total_pages): (i64, i64), page_var: &str, (width, y): (f64, f64), (row_start, row_end, total_rows): (usize, usize, usize), compact: bool) -> Result<Vec<Node>, Diagnostic> {
    let (prev_target, next_target) = if compact {
        ("prev".to_string(), "next".to_string())
    } else {
        ((if page > 1 { page - 1 } else { 0 }).to_string(), (if page < total_pages { page + 1 } else { 0 }).to_string())
    };
    let mut sequence = vec![Item { role: "prev", glyph: PREV_CHEVRON.to_string(), target: prev_target }];
    let items = if compact { Vec::new() } else { window(page, total_pages) };
    for item in items {
        sequence.push(match item {
            None => Item { role: "ellipsis", glyph: ELLIPSIS.to_string(), target: "0".to_string() },
            Some(p) => Item { role: "page", glyph: p.to_string(), target: p.to_string() },
        });
    }
    sequence.push(Item { role: "next", glyph: NEXT_CHEVRON.to_string(), target: next_target });

    let size = font_size(theme)? as f64;
    let text_y = y + 18.0;
    // The hit rect hugs the glyph tightly so the cursor matches the visible character: adjacent rects never touch.
    let (rect_w, rect_h, rect_y) = (size + 6.0, (size * 1.8).max(18.0), y + 4.0);
    let family = measure::stand_in_family(body_family);
    let advance = |text: &str| book.raw_advance(text, &Font { family, size, weight: 400.0, italic: false }).map_err(diag::err_internal);
    let aux_slot = theme.item_width * AUX_SLOT_RATIO;
    // measure/pager.rs:`page_slot`: `item_width` holds a two-digit label, and each further digit widens the slot.
    let slot_widths = sequence
        .iter()
        .map(|item| match item.role {
            "page" => Ok(theme.item_width + (advance(&item.glyph)? - advance("00")?).max(0.0)),
            _ => Ok(aux_slot),
        })
        .collect::<Result<Vec<f64>, Diagnostic>>()?;
    let sequence_left = width - slot_widths.iter().sum::<f64>();
    let mut centers = Vec::with_capacity(sequence.len());
    let mut slot_left = sequence_left;
    for slot_width in &slot_widths {
        centers.push(slot_left + slot_width / 2.0);
        slot_left += slot_width;
    }
    // An ellipsis sits midway between its neighbors' ink edges, so the gap on each side is equal whatever the digit counts.
    for i in 0..sequence.len() {
        if sequence[i].role == "ellipsis" {
            let prev_edge = centers[i - 1] + advance(&sequence[i - 1].glyph)? / 2.0;
            let next_edge = centers[i + 1] - advance(&sequence[i + 1].glyph)? / 2.0;
            centers[i] = (prev_edge + next_edge) / 2.0;
        }
    }

    let mut nodes = Vec::new();
    // A page holding only total rows reads as its last data row. The label is dropped, never shrunk, if it would
    // collide with the right-anchored sequence.
    let label = display_text::language::tr("controls.page_range", None, &[("first", &row_start.min(row_end).to_string()), ("last", &row_end.to_string()), ("total", &total_rows.to_string())])?;
    if advance(&label)? <= sequence_left {
        let attrs = Attrs::new()
            .quick("class", "dbt-paginator-label")
            .quick("font-size", (size as i64).to_string())
            .quick("fill", theme.color_inactive.as_str())
            .quick("font-family", body_family)
            .quick("font-weight", theme.weight_inactive.to_string())
            .quick("style", "font-variant-numeric: tabular-nums;")
            .set_if(compact, "data-dbt-pager-label", "");
        nodes.push(Node::text(Role::Label, (0.0, text_y), attrs, label.as_str()));
    }

    let mut glyphs = Vec::new();
    for (item, &center) in sequence.iter().zip(&centers) {
        let active = item.role == "page" && item.target == page.to_string();
        let disabled_chevron = matches!(item.role, "prev" | "next") && item.target == "0";
        let disabled_page = item.role == "page" && item.target == "0";
        let (color, weight, clickable) = if active {
            (theme.color_active.as_str(), theme.weight_active, false)
        } else if disabled_chevron {
            (theme.color_disabled.as_str(), theme.weight_chevron, false)
        } else if disabled_page {
            (theme.color_disabled.as_str(), theme.weight_inactive, false)
        } else if matches!(item.role, "prev" | "next") {
            (theme.color_active.as_str(), theme.weight_chevron, true)
        } else if item.role == "ellipsis" {
            (theme.color_inactive.as_str(), theme.weight_inactive, false)
        } else {
            (theme.color_inactive.as_str(), theme.weight_inactive, true)
        };
        if clickable {
            let attrs = Attrs::new().quick("class", "dbt-page-target").quick("fill", "transparent").quick("data-dbt-page-target", item.target.as_str());
            glyphs.push(Node::rect(Role::Chrome, (center - rect_w / 2.0, rect_y, rect_w, rect_h), attrs));
        }
        let attrs = Attrs::new()
            .quick("font-size", size.to_string())
            .quick("fill", color)
            .quick("text-anchor", "middle")
            .quick("font-family", body_family)
            .quick("font-weight", weight.to_string())
            .quick("class", "dbt-paginator-glyph")
            .quick("style", "font-variant-numeric: tabular-nums;")
            .quick("data-paginator-role", item.role)
            .set_if(compact, "data-dbt-fill-enabled", theme.color_active.as_str())
            .set_if(compact, "data-dbt-fill-disabled", theme.color_disabled.as_str())
            .quick_some("data-pagination-current", active.then_some(page_var));
        glyphs.push(Node::text(Role::Label, (center, text_y), attrs, item.glyph.as_str()));
    }
    nodes.push(Node::group(Role::Chrome, Attrs::new().quick("class", "dbt-paginator").quick("data-paginator", page_var), glyphs));
    Ok(nodes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_window_keeps_the_ends_and_the_neighbors_of_the_page_and_marks_each_gap() {
        assert_eq!(window(1, 3), [Some(1), Some(2), Some(3)]);
        assert_eq!(window(1, 6), [Some(1), Some(2), None, Some(6)]);
        assert_eq!(window(5, 6), [Some(1), None, Some(4), Some(5), Some(6)]);
        assert_eq!(window(3, 8), [Some(1), Some(2), Some(3), Some(4), None, Some(8)]);
    }
}
