//! Board-wide category-to-color planning: one data value gets one palette slot
//! across every chart on the board. A value's identity is its slot, not a hex,
//! so a nested board repaints the same slot from its own palette.
//!
//! Display order is deliberately absent: the plan decides which slot a value
//! owns, never the order a chart shows its values in.

use std::collections::{HashMap, HashSet};

use column_types::{ColumnType, ColumnTypes, Dataset};
use diagnostic::{codes, Diagnostic};
use indexmap::IndexMap;
use serde_json::json;


/// Authored pins: data field to (value to color).
pub type Pins = IndexMap<String, IndexMap<String, String>>;

/// The distinct values one chart draws per bound field, in first-seen row order.
pub type ChartFieldValues = IndexMap<String, Vec<String>>;

/// Fewer charts than this have no cross-chart consistency problem to solve.
const MIN_CHARTS_FOR_BINDING: usize = 2;

#[derive(Debug, Clone, PartialEq)]
pub struct CategoryColorScale {
    pub field: String,
    /// Value to palette slot, in domain order.
    pub slots: IndexMap<String, usize>,
    /// Pins naming a color outside the palette: the value still owns a slot
    /// (its companion ink indexes it) but paints this literal.
    pub overrides: IndexMap<String, String>,
    /// Pins whose value no chart draws; they claim no slot.
    pub unseen_pins: IndexMap<String, String>,
}

#[derive(Debug, PartialEq)]
pub enum Observed {
    Values(Vec<String>),
    /// The field names positions or measures, not categories: it cannot bind
    /// for any chart on the board.
    Disqualified,
}

/// The distinct strings `field` holds in `dataset`. A null is skipped, not
/// disqualifying; a column not declared `string` disqualifies the field, and so
/// does a column whose every value is date-shaped, since a time axis is ordered,
/// not a set of names. A pinned field skips the date test: the author named its
/// values. A column the schema does not declare, or a cell its `string`
/// declaration contradicts, is an error.
pub fn observe(dataset: &Dataset, field: &str, pinned: bool) -> Result<Observed, Diagnostic> {
    if dataset.schema.get(field).map_err(Diagnostic::refused)? != &ColumnType::String {
        return Ok(Observed::Disqualified);
    }
    ColumnTypes::default()
        .with(field, ColumnType::String)
        .check_rows(&dataset.rows)
        .map_err(|mismatch| mismatch.into_diagnostic())?;
    let mut values: Vec<String> = Vec::new();
    for text in dataset.rows.iter().filter_map(|row| row.get(field)?.as_str()) {
        if !values.iter().any(|seen| seen == text) {
            values.push(text.to_string());
        }
    }
    if !pinned && !values.is_empty() && values.iter().all(|v| column_types::is_date_like(v)) {
        return Ok(Observed::Disqualified);
    }
    Ok(Observed::Values(values))
}

/// One scale per bound field. A field binds when it is authored or at least two
/// charts encode it. Slots go to the pins first, then to `seeded` options (the
/// options of a variable a chart's link sets from the field), then to the values
/// the data holds in first-seen order. Seeding never binds a field on its own
/// and never costs one: options the palette cannot seat are left unseeded.
///
/// A field with more values than the palette seats does not bind, except an
/// authored one, which is refused.
pub fn plan_category_colors(
    observations: &[ChartFieldValues],
    authored: &Pins,
    palette: &[String],
    seeded: &IndexMap<String, Vec<String>>,
) -> Result<IndexMap<String, CategoryColorScale>, Diagnostic> {
    let mut chart_counts: IndexMap<&str, usize> = IndexMap::new();
    let mut observed: IndexMap<&str, Vec<&str>> =
        authored.keys().map(|field| (field.as_str(), Vec::new())).collect();
    for chart in observations {
        for (field, values) in chart {
            *chart_counts.entry(field).or_default() += 1;
            let seen = observed.entry(field).or_default();
            for value in values {
                if !seen.contains(&value.as_str()) {
                    seen.push(value);
                }
            }
        }
    }

    let mut fields: Vec<&str> = chart_counts.keys().copied().collect();
    fields.extend(authored.keys().map(String::as_str).filter(|f| !chart_counts.contains_key(f)));

    let mut scales = IndexMap::new();
    for field in fields {
        let is_authored = authored.contains_key(field);
        if !is_authored && chart_counts.get(field).copied().unwrap_or(0) < MIN_CHARTS_FOR_BINDING {
            continue;
        }
        let options: &[String] = seeded.get(field).map_or(&[], Vec::as_slice);
        let seen = &observed[field];
        let discovered: Vec<&str> = options
            .iter()
            .map(String::as_str)
            .chain(seen.iter().copied().filter(|v| !options.iter().any(|o| o == v)))
            .collect();
        if discovered.is_empty() {
            continue;
        }
        let mut seats = Seats::for_values(authored.get(field), &discovered, palette);
        if seats.needed > palette.len() && !options.is_empty() {
            seats = Seats::for_values(authored.get(field), seen, palette);
        }
        if seats.needed > palette.len() {
            if !is_authored {
                continue;
            }
            return Err(Diagnostic::new(
                &codes::ERR_CATEGORY_COLOR_PALETTE_EXHAUSTED,
                [
                    ("field", json!(field)),
                    ("swatch_count", json!(palette.len())),
                    ("value_count", json!(seats.needed)),
                ],
            ));
        }
        scales.insert(field.to_string(), seats.into_scale(field, palette)?);
    }
    Ok(scales)
}

/// A field's pins and values, laid out for seating.
struct Seats<'a> {
    /// Pins naming a value the field draws, in authored order. A pin naming
    /// anything else claims nothing: the planner sees one render's rows, and a
    /// value goes missing for reasons that are no typo (a filter, a failed sibling).
    pinned: IndexMap<&'a str, &'a str>,
    unseen: IndexMap<String, String>,
    /// Authored pins first, then the remaining values found in the data.
    domain: Vec<&'a str>,
    /// Palette swatches the field consumes: palette-member pins plus unpinned
    /// values. A pin outside the palette paints its own literal and needs none.
    needed: usize,
}

impl<'a> Seats<'a> {
    fn for_values(
        authored: Option<&'a IndexMap<String, String>>,
        values: &[&'a str],
        palette: &[String],
    ) -> Self {
        let mut pinned = IndexMap::new();
        let mut unseen = IndexMap::new();
        for (value, color) in authored.into_iter().flatten() {
            if values.contains(&value.as_str()) {
                pinned.insert(value.as_str(), color.as_str());
            } else {
                unseen.insert(value.clone(), color.clone());
            }
        }
        let domain: Vec<&str> = pinned
            .keys()
            .copied()
            .chain(values.iter().copied().filter(|v| !pinned.contains_key(v)))
            .collect();
        let in_palette: HashSet<String> = palette.iter().map(|c| c.to_lowercase()).collect();
        let claimed: HashSet<String> = pinned
            .values()
            .map(|c| c.to_lowercase())
            .filter(|c| in_palette.contains(c))
            .collect();
        let needed = claimed.len() + domain.iter().filter(|v| !pinned.contains_key(*v)).count();
        Seats { pinned, unseen, domain, needed }
    }

    /// Every value takes a palette slot: pins claim theirs, the rest the next
    /// free one. An outside-the-palette pin burns a free slot while one can be
    /// spared; past that it shares a slot (and so a companion ink) with another value.
    fn into_scale(self, field: &str, palette: &[String]) -> Result<CategoryColorScale, Diagnostic> {
        let mut owner: HashMap<String, &str> = HashMap::new();
        for (value, color) in &self.pinned {
            if let Some(first) = owner.insert(color.to_lowercase(), value) {
                return Err(Diagnostic::new(
                    &codes::ERR_CATEGORY_COLOR_PIN_DUPLICATE,
                    [
                        ("color", json!(color)),
                        ("field", json!(field)),
                        ("first_value", json!(first)),
                        ("second_value", json!(value)),
                    ],
                ));
            }
        }
        let slot_of_color: HashMap<String, usize> =
            palette.iter().enumerate().map(|(i, c)| (c.to_lowercase(), i)).collect();
        let pinned_slot = |value: &str| {
            self.pinned.get(value).and_then(|color| slot_of_color.get(&color.to_lowercase()).copied())
        };
        let claimed: HashSet<usize> = self.domain.iter().filter_map(|v| pinned_slot(v)).collect();
        let mut free = (0..palette.len()).filter(|i| !claimed.contains(i));
        let unpinned = self.domain.iter().filter(|v| !self.pinned.contains_key(*v)).count();
        let mut spare = palette.len() - claimed.len() - unpinned;
        let mut shared = (0..palette.len()).cycle();

        let mut slots = IndexMap::new();
        let mut overrides = IndexMap::new();
        for value in &self.domain {
            let slot = match (pinned_slot(value), self.pinned.get(value)) {
                (Some(slot), _) => slot,
                (None, Some(color)) => {
                    overrides.insert(value.to_string(), color.to_string());
                    if spare > 0 {
                        spare -= 1;
                        free.next().expect("the seats needed leave a free slot")
                    } else {
                        shared.next().expect("a non-empty palette")
                    }
                }
                (None, None) => free.next().expect("the seats needed leave a free slot"),
            };
            slots.insert(value.to_string(), slot);
        }
        Ok(CategoryColorScale {
            field: field.to_string(),
            slots,
            overrides,
            unseen_pins: self.unseen,
        })
    }
}
