//! The bound model: bind's output and layout's input (spec § 5.2). Every field is a
//! data fact: no pixel, font or width. Types with an invariant keep their fields
//! private behind a checked constructor, so layout never sees a broken value.

use std::cmp::Ordering;
use std::hash::{Hash, Hasher};

use diagnostic::Diagnostic;

use crate::temporal::TimeUnit;
use crate::time_axis::{Instant, TimeAxis};

/// A bound value that breaks a construction invariant: a bind bug, never a user error.
#[derive(Debug, Clone, PartialEq)]
pub enum Invalid {
    Extent { lo: f64, hi: f64 },
    Month(u8),
    BandLength { keys: usize, text: usize },
    BandDuplicate(Key),
    GridDuplicate(Key),
    /// A grid that facets on neither rows nor columns.
    GridNoAxis,
    RungOutside { value: f64 },
    GridCells { expected: usize, got: usize },
    /// A grid's panel ids are not `0, 1, 2, …` in cell order.
    GridPanels,
    NoMeasureScale,
}

/// A chart's body: what its family bound, or the error card it draws instead.
#[derive(Debug)]
pub enum Body {
    /// bar, line, area, scatter, histogram, heatmap.
    Plot(Box<Plot>),
    /// Execute error, bind refusal or no data: the card's text is the diagnostic's message.
    Failed(Diagnostic),
}

/// The bar orientation law's verdict (RL §1d); every other family is vertical.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Orient {
    Vertical,
    Horizontal,
}

/// The six cartesian families' shared frame: scales, panels and the one series list.
#[derive(Debug)]
pub struct Plot {
    orient: Orient,
    x: Scale,
    y: Vec<Scale>,
    panels: Panels,
    series: Vec<Series>,
}

impl Plot {
    /// `y[0]` is the shared measure scale; more only for pinned layer axes.
    pub fn new(orient: Orient, x: Scale, y: Vec<Scale>, panels: Panels, series: Vec<Series>) -> Result<Self, Invalid> {
        if y.is_empty() {
            return Err(Invalid::NoMeasureScale);
        }
        Ok(Self { orient, x, y, panels, series })
    }

    pub fn orient(&self) -> Orient {
        self.orient
    }

    /// The position (category) axis.
    pub fn x(&self) -> &Scale {
        &self.x
    }

    pub fn y(&self) -> &[Scale] {
        &self.y
    }

    pub fn panels(&self) -> &Panels {
        &self.panels
    }

    pub fn series(&self) -> &[Series] {
        &self.series
    }
}

#[derive(Debug)]
pub struct Scale {
    pub kind: ScaleKind,
    pub title: Option<String>,
}

#[derive(Debug)]
pub enum ScaleKind {
    /// Bars, heatmap rows and columns.
    Band(BandDomain),
    /// Nominal x on line and scatter.
    Point(BandDomain),
    Linear(ValueDomain),
    Log(ValueDomain),
    Time(TimeDomain),
}

/// Distinct keys in their final order (the band order law), each with its display text.
#[derive(Debug, Clone, PartialEq)]
pub struct BandDomain {
    keys: Vec<Key>,
    text: Vec<String>,
}

impl BandDomain {
    pub fn new(keys: Vec<Key>, text: Vec<String>) -> Result<Self, Invalid> {
        if keys.len() != text.len() {
            return Err(Invalid::BandLength { keys: keys.len(), text: text.len() });
        }
        if let Some(key) = duplicate(&keys) {
            return Err(Invalid::BandDuplicate(key));
        }
        Ok(Self { keys, text })
    }

    pub fn keys(&self) -> &[Key] {
        &self.keys
    }

    pub fn text(&self) -> &[String] {
        &self.text
    }
}

fn duplicate(keys: &[Key]) -> Option<Key> {
    let mut sorted: Vec<&Key> = keys.iter().collect();
    sorted.sort();
    sorted.windows(2).find(|pair| pair[0] == pair[1]).map(|pair| pair[0].clone())
}

/// A value axis as data: layout reads one domain, with the exact bounds the axis pins.
#[derive(Debug, Clone, PartialEq)]
pub struct ValueDomain {
    /// The shared-scale union: base, unpinned layers, annotations.
    pub extent: Extent,
    pub zero: Zero,
    /// The headroom the pins apply; a log axis applies none, so it is 0.
    pub headroom: f64,
    pub authored: Option<Authored>,
    pub ladder: Ladder,
    /// Exact lower bound the scale takes instead of rounding; `None` leaves it to the scale.
    pub min: Option<Pin>,
    /// Exact upper bound, likewise.
    pub max: Option<Pin>,
    /// The scale's final bounds, as layout uses them: niced, pinned or authored. Layout
    /// picks `BySize` rungs inside them and never nices.
    pub bounds: Bounds,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Bounds {
    pub extent: Extent,
    pub reason: BoundsReason,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BoundsReason {
    /// d3's nice at count 10 over the extent below: the default with no pin, no authored
    /// domain, no stack and no vertical bar, or an authored `scale.nice: true` over any.
    Niced,
    /// The data's own extent (zero included when anchored), not niced: a vertical bar,
    /// or an authored `scale.nice: false`.
    Fitted,
    /// A pinned end stands as is; the other end is the data's (or zero), not niced.
    Pinned,
    /// `scale.domain`, as authored.
    Authored,
    /// A stack's own extent, not niced.
    Stacked,
}

/// An authored `scale.domain`. `extent` is sorted; `reversed` keeps the author's direction.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Authored {
    pub extent: Extent,
    pub reversed: bool,
}

/// An exact domain bound and why it is pinned.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Pin {
    pub value: f64,
    pub reason: PinReason,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PinReason {
    /// The data edge expanded by `scale.headroom` (0 pins the data edge itself).
    Headroom,
    /// A mixed-sign axis whose format step floor would round the bottom rung below the data.
    FormatFloor,
    /// A zero stack's top, flush with its tallest total.
    FlushTop,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Zero {
    Anchored,
    Free,
    /// `scale.zero` authored: `true` includes zero, `false` excludes it.
    Pinned(bool),
}

/// The data half of the tick ladder; layout prints nothing the ladder does not name.
#[derive(Debug, Clone, PartialEq)]
pub enum Ladder {
    /// `scale.values`, verbatim.
    Authored(Vec<Rung>),
    /// `ticks.count` set (or `"auto"` = 6): size-free.
    Fixed(FixedLadder),
    /// `ticks.count` unset: layout picks the count from the plot length.
    BySize { min_step: Option<f64> },
    /// No data-chosen ticks: log axes, independent multiples.
    None,
}

/// A fixed tick ladder: every rung inside its bounds.
#[derive(Debug, Clone, PartialEq)]
pub struct FixedLadder {
    rungs: Vec<Rung>,
    bounds: Extent,
}

impl FixedLadder {
    pub fn new(rungs: Vec<Rung>, bounds: Extent) -> Result<Self, Invalid> {
        if let Some(rung) = rungs.iter().find(|rung| !(bounds.lo..=bounds.hi).contains(&rung.value)) {
            return Err(Invalid::RungOutside { value: rung.value });
        }
        Ok(Self { rungs, bounds })
    }

    pub fn rungs(&self) -> &[Rung] {
        &self.rungs
    }

    pub fn bounds(&self) -> Extent {
        self.bounds
    }
}

/// A tick value and its text, printed by the ruler law (RL §3f).
#[derive(Debug, Clone, PartialEq)]
pub struct Rung {
    pub value: f64,
    pub text: String,
}

/// A closed, finite numeric range.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Extent {
    lo: f64,
    hi: f64,
}

impl Extent {
    pub fn new(lo: f64, hi: f64) -> Result<Self, Invalid> {
        if lo.is_finite() && hi.is_finite() && lo <= hi {
            Ok(Self { lo, hi })
        } else {
            Err(Invalid::Extent { lo, hi })
        }
    }

    pub fn lo(self) -> f64 {
        self.lo
    }

    pub fn hi(self) -> f64 {
        self.hi
    }
}

#[derive(Debug)]
pub struct TimeDomain {
    /// Each x parsed once.
    pub axis: TimeAxis,
    /// The detected grain, or the authored one. `None` is production's continuous temporal
    /// axis: no grain detected (fewer than two distinct dates, or no calendar cadence).
    pub unit: Option<TimeUnit>,
    /// After the calendar gap fill (`axis_x.fill`).
    pub buckets: Vec<Instant>,
    pub fiscal_start: Option<Month>,
}

/// A calendar month, 1 through 12.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Month(u8);

impl Month {
    pub fn new(month: u8) -> Result<Self, Invalid> {
        if (1..=12).contains(&month) {
            Ok(Self(month))
        } else {
            Err(Invalid::Month(month))
        }
    }

    pub fn get(self) -> u8 {
        self.0
    }
}

/// One decoded cell, the value a category, band or panel is keyed by.
///
/// Equality and order are by value within a variant; across variants the order is
/// the declaration order and nothing is equal (the declared column type picks one
/// variant per column, so `Int(1)` and `Num(1.0)` never meet). A float or decimal and a
/// date are their wire text, production's partition identity (a value's canonical JSON):
/// `2` and `2.0` are two keys, as are two spellings of one instant (`2024-01-01T00:00:00Z`
/// and `2024-01-01T00:00:00+00:00`). They order by value, then text.
#[derive(Debug, Clone)]
pub enum Key {
    Null,
    Bool(bool),
    Int(i64),
    Num { value: f64, text: String },
    Text(String),
    Date { instant: Instant, text: String },
}

impl Key {
    fn rank(&self) -> u8 {
        match self {
            Key::Null => 0,
            Key::Bool(_) => 1,
            Key::Int(_) => 2,
            Key::Num { .. } => 3,
            Key::Text(_) => 4,
            Key::Date { .. } => 5,
        }
    }
}

fn cmp_num(a: f64, b: f64) -> Ordering {
    a.partial_cmp(&b).unwrap_or_else(|| a.is_nan().cmp(&b.is_nan()))
}

impl Ord for Key {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Key::Bool(a), Key::Bool(b)) => a.cmp(b),
            (Key::Int(a), Key::Int(b)) => a.cmp(b),
            (Key::Num { value: a, text: x }, Key::Num { value: b, text: y }) => cmp_num(*a, *b).then_with(|| x.cmp(y)),
            (Key::Text(a), Key::Text(b)) => a.cmp(b),
            (Key::Date { instant: a, text: x }, Key::Date { instant: b, text: y }) => {
                cmp_num(a.at, b.at).then_with(|| x.cmp(y))
            }
            _ => self.rank().cmp(&other.rank()),
        }
    }
}

impl PartialOrd for Key {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl PartialEq for Key {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}

impl Eq for Key {}

impl Hash for Key {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.rank().hash(state);
        match self {
            Key::Null => {}
            Key::Bool(b) => b.hash(state),
            Key::Int(i) => i.hash(state),
            Key::Num { text, .. } => text.hash(state),
            Key::Text(s) => s.hash(state),
            Key::Date { text, .. } => text.hash(state),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Series {
    pub key: SeriesKey,
    /// `field_labels`, humanized wide labels.
    pub display: String,
}

#[derive(Debug, Clone, PartialEq)]
pub enum SeriesKey {
    /// A color column's raw value.
    Value(Key),
    /// A wide `y` measure's column.
    Measure(String),
    /// A layer's name.
    Layer(String),
}

#[derive(Debug, Clone, PartialEq)]
pub enum Panels {
    Single,
    Grid(Grid),
}

/// An index into a layer's per-panel lists.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PanelId(pub usize);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Sharing {
    Shared,
    Independent,
}

/// The product of row and column values, row-major; an empty combination's cell is `None`.
/// An axis the chart does not facet on is `None`, so a real null facet value (`Key::Null`)
/// never stands for an absent axis.
#[derive(Debug, Clone, PartialEq)]
pub struct Grid {
    rows: Option<Vec<Key>>,
    cols: Option<Vec<Key>>,
    cells: Vec<Option<PanelId>>,
    x: Sharing,
    y: Sharing,
    mirror: bool,
}

impl Grid {
    /// At least one axis is faceted; row and column keys are distinct; the drawn cells' panel
    /// ids must run `0, 1, 2, …` in cell order.
    pub fn new(
        rows: Option<Vec<Key>>,
        cols: Option<Vec<Key>>,
        cells: Vec<Option<PanelId>>,
        x: Sharing,
        y: Sharing,
        mirror: bool,
    ) -> Result<Self, Invalid> {
        if rows.is_none() && cols.is_none() {
            return Err(Invalid::GridNoAxis);
        }
        let (row_keys, col_keys) = (rows.as_deref().unwrap_or_default(), cols.as_deref().unwrap_or_default());
        if let Some(key) = duplicate(row_keys).or_else(|| duplicate(col_keys)) {
            return Err(Invalid::GridDuplicate(key));
        }
        let span = |axis: &Option<Vec<Key>>| axis.as_ref().map_or(1, Vec::len);
        let expected = span(&rows) * span(&cols);
        if cells.len() != expected {
            return Err(Invalid::GridCells { expected, got: cells.len() });
        }
        if !cells.iter().flatten().enumerate().all(|(i, id)| id.0 == i) {
            return Err(Invalid::GridPanels);
        }
        Ok(Self { rows, cols, cells, x, y, mirror })
    }

    pub fn rows(&self) -> Option<&[Key]> {
        self.rows.as_deref()
    }

    pub fn cols(&self) -> Option<&[Key]> {
        self.cols.as_deref()
    }

    pub fn cells(&self) -> &[Option<PanelId>] {
        &self.cells
    }

    pub fn panel_count(&self) -> usize {
        self.cells.iter().flatten().count()
    }

    pub fn x(&self) -> Sharing {
        self.x
    }

    pub fn y(&self) -> Sharing {
        self.y
    }

    pub fn mirror(&self) -> bool {
        self.mirror
    }
}
