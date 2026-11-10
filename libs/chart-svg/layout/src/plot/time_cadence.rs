//! The label cadence of a time axis over a calendar grain: which grain the labels are written in,
//! which periods carry one, and how a crowded axis resolves (years thinned, parity, a tilt).
//!
//! Size-free: the verdict depends on the card's usable width, never on the plot the gutters leave.
//!
//! Over daily data production first tries the native labels: the day numbers, then the Mondays'
//! (`submonth_candidate_fits`), and when neither clears and the data holds fewer than two month
//! starts, the Mondays alone, tilted rather than thinned. Weekly data over bucket-aligned marks (a
//! bar's, `Crowding::bucket_aligned`) takes the same rungs, its own weeks standing for the Mondays;
//! over a line's weekly data, with fewer than two month openers, production judges the native day
//! labels as a flat, parity or tilted row (`generic_layout`). Every other case walks the bucketed
//! ladder from the label grain.

use bind::temporal::TimeUnit;
use d3rs::calendar::weekday;
use diagnostic::{diag, Diagnostic};
use measure::shaped::{Font, FontBook};

/// Production's `LABEL_GAP_SPACES` (`constants/axis.py`): the spaces a clipped edge period's label
/// needs beyond its text, and a context row's between neighbors.
pub(crate) const LABEL_GAP_SPACES: f64 = 1.0;

/// Production's `LABEL_GAP_SPACES_NUMERIC`: the spaces a day number needs beside its neighbor.
const LABEL_GAP_SPACES_NUMERIC: f64 = 2.0;

/// The width production's cadence and tilt verdicts read for `text` (`ax_x.labels.measure`): raw
/// advances at the painted weight, unkerned, in the family production maps the painted one to
/// (`stand_in_family`: Inter for every sans face). Drawn ink, gutters and positions are shaped in
/// the painted face ([`FontBook::run`]) and never read this; only a verdict production takes on
/// these sums may. A stand-in face with no `wght` axis fails as production does.
pub fn decision_width(book: &FontBook, text: &str, font: &Font) -> Result<f64, Diagnostic> {
    let stand_in = Font {
        family: measure::stand_in_family(font.family),
        ..*font
    };
    book.weighted_advance(text, &stand_in)
        .map_err(diag::err_internal)
}

/// Years a dense year axis labels in turn, the round steps readers group on, before any tilt.
const YEAR_STEPS: [i32; 5] = [5, 10, 25, 50, 100];

pub(crate) type Date = (i32, u8, u8);

/// The rows a label paints: its own text and its context.
pub type Rows = (String, String);

/// What the cadence ladder settled on.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Cadence {
    /// The vocabulary labels are written in.
    pub format: TimeUnit,
    /// The grain whose period openers carry a label.
    pub visibility: TimeUnit,
    /// At year cadence, only years divisible by this are labeled.
    pub year_step: i32,
    /// Vega's own `labelOverlap: parity` thins what is drawn.
    pub parity: bool,
    pub angle: f64,
    /// The labels measured when every enabled strategy was tried and they still collide.
    pub collision: Option<usize>,
}

impl Cadence {
    /// The labels fit only by hiding some, tilting or colliding: an interval axis never takes it.
    pub fn thinned(&self) -> bool {
        self.year_step > 1 || self.parity || self.angle != 0.0 || self.collision.is_some()
    }
}

/// How the axis resolves crowding, as its style sets it.
#[derive(Debug, Clone, Copy)]
pub struct Crowding<'a> {
    /// `overlap.skip`: the ladder may step to a coarser grain.
    pub skip: bool,
    /// `overlap.tilt` with its angles, shallowest first.
    pub tilt: Option<&'a [f64]>,
    /// The width the crowding is judged against: the card's width times the theme's usable ratio.
    pub usable_width: f64,
    /// An edge label anchors on the plot edge, reserving its full width from it.
    pub flush: bool,
    pub font_size: f64,
    /// Production's `bucket_aligned_temporal`: the marks span buckets, as a bar's do
    /// (`bar/time_x.rs:468`; a line or area never sets it, `temporal.rs:1442`). A weekly axis then
    /// tries its native week labels as a daily one does (`resolve_vega_label_layout`, `temporal.rs:1486`).
    pub bucket_aligned: bool,
}

type Measure<'a> = &'a mut dyn FnMut(&str) -> Result<f64, Diagnostic>;

/// The cadence of labels written in `format` over `span`, every bucket of a `data` grain from the
/// data's first to its last (`resolve_vega_label_layout`, a line or area's continuous scale).
/// `label_auto`: no `labels.time_unit` was authored (or the one authored named no period the data
/// spans), so daily and weekly data try their native labels before the bucketed ladder.
#[allow(clippy::too_many_arguments)]
pub fn resolve(
    span: &[Date],
    data: TimeUnit,
    format: TimeUnit,
    label_auto: bool,
    fiscal_start: u8,
    crowding: &Crowding,
    quarters: &[String; 4],
    measure: Measure,
) -> Result<Cadence, Diagnostic> {
    let ladder = Ladder {
        dates: span,
        data,
        fiscal_start,
        crowding,
        quarters,
    };
    if span.len() < 2 {
        return Ok(ladder.plain(format));
    }
    match data {
        TimeUnit::YearMonthDate if label_auto => ladder.submonth(measure),
        TimeUnit::YearWeek if label_auto && crowding.bucket_aligned => ladder.submonth(measure),
        TimeUnit::YearWeek if label_auto => ladder.weekly(format, measure),
        _ => ladder.resolve(format, measure),
    }
}

/// The labels `cadence` draws over `span` (`build_temporal_labels`): the dates that open its
/// visibility grain (every `year_step`-th year at year cadence), each with the rows it paints. The
/// first, the span's earliest, carries the axis's context row.
pub fn labeled(
    span: &[Date],
    data: TimeUnit,
    cadence: &Cadence,
    fiscal_start: u8,
    crowding: &Crowding,
    quarters: &[String; 4],
) -> Result<Vec<(usize, Rows)>, Diagnostic> {
    let ladder = Ladder {
        dates: span,
        data,
        fiscal_start,
        crowding,
        quarters,
    };
    ladder
        .openers(cadence.visibility, cadence.year_step)
        .into_iter()
        .enumerate()
        .map(|(k, at)| Ok((at, ladder.rows(at, cadence.format, k == 0)?)))
        .collect()
}

struct Ladder<'a> {
    dates: &'a [Date],
    data: TimeUnit,
    fiscal_start: u8,
    crowding: &'a Crowding<'a>,
    quarters: &'a [String; 4],
}

impl Ladder<'_> {
    fn band(&self) -> f64 {
        self.crowding.usable_width / self.dates.len() as f64
    }

    /// Every label of `format` drawn flat, none thinned.
    fn plain(&self, format: TimeUnit) -> Cadence {
        Cadence {
            format,
            visibility: format,
            year_step: 1,
            parity: false,
            angle: 0.0,
            collision: None,
        }
    }

    /// The rungs a daily or bucket-aligned weekly axis tries before the bucketed ladder: the day
    /// numbers (daily data only), then the weeks'.
    fn native_candidates(&self) -> &'static [TimeUnit] {
        if self.data == TimeUnit::YearMonthDate {
            &[TimeUnit::YearMonthDate, TimeUnit::YearWeek]
        } else {
            &[TimeUnit::YearWeek]
        }
    }

    /// The dates a `candidate` rung labels (`submonth_candidate_indices`): the Mondays of daily data
    /// for the week rung, every bucket otherwise (a weekly axis labels each week).
    fn candidate_indices(&self, candidate: TimeUnit) -> Vec<usize> {
        if self.data == TimeUnit::YearMonthDate && candidate == TimeUnit::YearWeek {
            self.mondays()
        } else {
            (0..self.dates.len()).collect()
        }
    }

    /// Daily data, or weekly data over bucket-aligned marks, labels unset (`resolve_vega_label_layout`'s
    /// first native branch): the day numbers (daily only), then the weeks' (the Mondays' over daily
    /// data), when each clears; else the month ladder when two month starts are there to promote to;
    /// else the weeks alone, tilted rather than thinned.
    fn submonth(&self, measure: Measure) -> Result<Cadence, Diagnostic> {
        for &candidate in self.native_candidates() {
            if self.candidate_fits(candidate, measure)? {
                return Ok(self.plain(candidate));
            }
        }
        if self.openers(TimeUnit::YearMonth, 1).len() >= 2 {
            return self.resolve(TimeUnit::YearMonth, measure);
        }
        let mondays = self.candidate_indices(TimeUnit::YearWeek);
        if mondays.is_empty() {
            return Err(Diagnostic::refused(
                "a continuous daily axis spanning no Monday has no weekly rung",
            ));
        }
        let widths = mondays
            .iter()
            .map(|&i| measure(&self.dates[i].2.to_string()))
            .collect::<Result<Vec<_>, _>>()?;
        let band = self.crowding.usable_width / widths.len() as f64;
        let (angle, fits) = match self.crowding.tilt {
            Some(angles) => {
                let pick = pick_tilt(&widths, band, angles, self.crowding.font_size);
                (pick.angle, pick.fits)
            }
            None => (0.0, fits_flat(&widths, band)),
        };
        Ok(Cadence {
            angle,
            collision: (!fits).then_some(widths.len()),
            ..self.plain(TimeUnit::YearWeek)
        })
    }

    fn mondays(&self) -> Vec<usize> {
        (0..self.dates.len())
            .filter(|&i| {
                let (y, m, d) = self.dates[i];
                weekday(y, m, d) == 0
            })
            .collect()
    }

    /// Whether `candidate`'s labels over daily dates clear each other (`submonth_candidate_fits`):
    /// the day-number row and the context row (month, year) are each their own clearance, a pair
    /// needing half its widths plus a gap on each side against the calendar distance between them.
    fn candidate_fits(&self, candidate: TimeUnit, measure: Measure) -> Result<bool, Diagnostic> {
        let indices = self.candidate_indices(candidate);
        if indices.is_empty() {
            return Ok(false);
        }
        let space = measure(" ")?;
        let (mut day_row, mut context_row) = (Vec::new(), Vec::new());
        for (position, &index) in indices.iter().enumerate() {
            let (day, context) = label_rows(
                self.dates[index],
                candidate,
                position == 0,
                self.fiscal_start,
                self.quarters,
            )?;
            day_row.push((index, day));
            if !context.is_empty() {
                context_row.push((index, context));
            }
        }
        let band = self.band();
        for (row, gap) in [
            (day_row, LABEL_GAP_SPACES_NUMERIC * space),
            (context_row, LABEL_GAP_SPACES * space),
        ] {
            for pair in row.windows(2) {
                let ((left_at, left), (right_at, right)) = (&pair[0], &pair[1]);
                let widths = measure(left)? + gap + measure(right)? + gap;
                if widths / 2.0 > (right_at - left_at) as f64 * band {
                    return Ok(false);
                }
            }
        }
        Ok(true)
    }

    /// Weekly data, labels unset (the second native branch): with two month openers to promote to,
    /// the month ladder; else the day labels as they stand, each as wide as its widest row plus a
    /// space. (Production also keeps them when the labels are in the day grain and clear, which
    /// `label_auto` over weekly data never is.)
    fn weekly(&self, format: TimeUnit, measure: Measure) -> Result<Cadence, Diagnostic> {
        if self.openers(TimeUnit::YearMonth, 1).len() >= 2 {
            return self.resolve(TimeUnit::YearMonth, measure);
        }
        let gap = measure(" ")?;
        let mut gapped = |text: &str| Ok(measure(text)? + gap);
        let widths = (0..self.dates.len())
            .map(|at| Self::token_width(&self.rows(at, format, at == 0)?, format, &mut gapped))
            .collect::<Result<Vec<_>, _>>()?;
        self.generic(format, &widths)
    }

    /// `generic_layout`: flat if the widths clear, else (`overlap.skip`) every other label, else a
    /// tilt of whatever is left; what still collides is counted.
    fn generic(&self, format: TimeUnit, widths: &[f64]) -> Result<Cadence, Diagnostic> {
        let Crowding {
            skip,
            tilt,
            usable_width,
            font_size,
            ..
        } = *self.crowding;
        let flat = self.plain(format);
        if fits_flat(widths, usable_width / widths.len() as f64) {
            return Ok(flat);
        }
        let mut considered = widths.to_vec();
        if skip {
            considered = widths.iter().step_by(2).copied().collect();
            if fits_flat(&considered, usable_width / considered.len() as f64) {
                return Ok(Cadence {
                    parity: true,
                    ..flat
                });
            }
        }
        let band = usable_width / considered.len() as f64;
        let (angle, fits) = match tilt {
            Some(angles) => {
                let pick = pick_tilt(&considered, band, angles, font_size);
                (pick.angle, pick.fits)
            }
            None => (0.0, false),
        };
        Ok(Cadence {
            parity: skip,
            angle,
            collision: (!fits).then_some(considered.len()),
            ..flat
        })
    }

    fn resolve(&self, format: TimeUnit, measure: Measure) -> Result<Cadence, Diagnostic> {
        let Crowding {
            skip,
            tilt,
            usable_width,
            font_size,
            ..
        } = *self.crowding;
        let (visibility, year_step, fits) = self.walk(format, measure)?;
        // At year cadence every visible tick is a January: the vocabulary is the bare year.
        let promoted = if visibility == TimeUnit::Year {
            TimeUnit::Year
        } else {
            format
        };
        let cadence = |parity, angle, collision| Cadence {
            format: promoted,
            visibility,
            year_step,
            parity,
            angle,
            collision,
        };
        if fits {
            return Ok(cadence(false, 0.0, None));
        }
        // A year axis too dense for flat labels climbs the year steps tilted before it leaves the
        // thinning to Vega's parity pass, which would keep a tick and a gridline at every year.
        if let (Some(angles), true, TimeUnit::Year) = (tilt, skip, visibility) {
            for step in YEAR_STEPS {
                let stepped = self.openers(visibility, step);
                if stepped.len() < 2 {
                    continue;
                }
                let widths = self.widths(&stepped, TimeUnit::Year, measure)?;
                let pick = pick_tilt(
                    &widths,
                    usable_width / widths.len() as f64,
                    angles,
                    font_size,
                );
                if pick.fits {
                    return Ok(Cadence {
                        format: TimeUnit::Year,
                        year_step: step,
                        ..cadence(false, pick.angle, None)
                    });
                }
            }
        }
        // The widths the tilt is measured on are every other opener's at year cadence, a halving
        // that never changes what is drawn: the drawn set stays every opener, thinned by Vega. They
        // are every date that opens the grain, not one per period: a year of daily rows opens on
        // each of its first seven days.
        let mut visible: Vec<usize> = (0..self.dates.len())
            .filter(|&i| self.opens(i, visibility))
            .collect();
        let parity = skip && visibility == TimeUnit::Year;
        if parity {
            visible = visible.into_iter().step_by(2).collect();
        }
        let widths = self.widths(&visible, promoted, measure)?;
        let band = usable_width / widths.len().max(1) as f64;
        let (angle, fits) = match tilt {
            Some(angles) => {
                let pick = pick_tilt(&widths, band, angles, font_size);
                (pick.angle, pick.fits)
            }
            None => (0.0, parity && fits_flat(&widths, band)),
        };
        Ok(Cadence {
            year_step: 1,
            ..cadence(parity, angle, (!fits).then_some(widths.len()))
        })
    }

    /// `walk_label_cadence_ladder`: from `format` coarser until the labels clear, the finest rung that
    /// does. At year cadence the clearance is measured in the bare-year vocabulary. Production
    /// prefers a rung no sparser than a ceiling but accepts a sparser clearing rung over none; the
    /// finest clearing rung is the first of either, since the spacing of a coarser rung's openers
    /// (whole months, quarters, years of a bucket sequence) only grows.
    fn walk(
        &self,
        format: TimeUnit,
        measure: Measure,
    ) -> Result<(TimeUnit, i32, bool), Diagnostic> {
        let mut unit = format;
        loop {
            let vocabulary = if unit == TimeUnit::Year {
                TimeUnit::Year
            } else {
                format
            };
            if self.clears(&self.openers(unit, 1), vocabulary, measure)? {
                return Ok((unit, 1, true));
            }
            let next = self
                .crowding
                .skip
                .then(|| coarser(unit))
                .flatten()
                .filter(|&next| self.openers(next, 1).len() >= 2);
            let Some(next) = next else {
                return self.year_steps(unit, measure);
            };
            unit = next;
        }
    }

    /// The rung past the last grain: at year cadence, only the years divisible by each round step.
    fn year_steps(
        &self,
        unit: TimeUnit,
        measure: Measure,
    ) -> Result<(TimeUnit, i32, bool), Diagnostic> {
        if self.crowding.skip && unit == TimeUnit::Year {
            for step in YEAR_STEPS {
                let labeled = self.openers(unit, step);
                if labeled.len() >= 2 && self.clears(&labeled, unit, measure)? {
                    return Ok((unit, step, true));
                }
            }
        }
        Ok((unit, 1, false))
    }

    fn opens(&self, at: usize, label: TimeUnit) -> bool {
        opens_label(self.dates[at], self.data, label, self.fiscal_start)
    }

    /// The indices of the dates that open a `label` period and whose year is a multiple of
    /// `year_step`, the first of each period (`openers_at`). Only a year can open on several dates
    /// of a bucket sequence (a daily row's first seven days); no other grain repeats.
    fn openers(&self, label: TimeUnit, year_step: i32) -> Vec<usize> {
        let mut out: Vec<usize> = Vec::new();
        let mut last_year = None;
        for at in 0..self.dates.len() {
            let year = self.dates[at].0;
            if !self.opens(at, label) || year % year_step != 0 {
                continue;
            }
            if label == TimeUnit::Year && last_year == Some(year) {
                continue;
            }
            last_year = Some(year);
            out.push(at);
        }
        out
    }

    /// The rows one label paints at `dates[at]`, the first label of the axis carrying the year.
    fn rows(
        &self,
        at: usize,
        vocabulary: TimeUnit,
        first: bool,
    ) -> Result<(String, String), Diagnostic> {
        label_rows(
            self.dates[at],
            vocabulary,
            first,
            self.fiscal_start,
            self.quarters,
        )
    }

    /// The width a label of `rows` reserves (`_cadence_token_width`): row one alone for a month or
    /// quarter, whose year row has its own clearance; the widest row for the others.
    fn token_width(
        rows: &(String, String),
        vocabulary: TimeUnit,
        measure: Measure,
    ) -> Result<f64, Diagnostic> {
        let width = measure(&rows.0)?;
        if matches!(vocabulary, TimeUnit::YearMonth | TimeUnit::YearQuarter) || rows.1.is_empty() {
            return Ok(width);
        }
        Ok(width.max(measure(&rows.1)?))
    }

    fn widths(
        &self,
        labeled: &[usize],
        vocabulary: TimeUnit,
        measure: Measure,
    ) -> Result<Vec<f64>, Diagnostic> {
        labeled
            .iter()
            .enumerate()
            .map(|(k, &at)| {
                Self::token_width(&self.rows(at, vocabulary, k == 0)?, vocabulary, measure)
            })
            .collect()
    }

    /// Whether `dates[at]` stands on a native tick of `vocabulary`'s grain (`_is_calendar_tick`).
    fn on_tick(&self, at: usize, vocabulary: TimeUnit) -> bool {
        let (y, m, d) = self.dates[at];
        match vocabulary {
            TimeUnit::Year => d == 1 && opens_period(m, 12, self.fiscal_start),
            TimeUnit::YearQuarter => d == 1 && opens_period(m, 3, self.fiscal_start),
            TimeUnit::YearMonth => d == 1,
            TimeUnit::YearWeek => weekday(y, m, d) == 0,
            TimeUnit::YearMonthDate => vocabulary == self.data,
        }
    }

    /// Every neighboring pair of `labeled` clears: each reserves half its width, a flushed first
    /// date its full width and a flushed last date, when it stands on a native tick, likewise; a
    /// month or quarter's year row clears the neighbor's year row on its own
    /// (`temporal_visibility_fits`, `_pair_clears`). A day or week's second row is inside its
    /// token width already, so production's year-row test for the other grains never binds.
    fn clears(
        &self,
        labeled: &[usize],
        vocabulary: TimeUnit,
        measure: Measure,
    ) -> Result<bool, Diagnostic> {
        let band = self.band();
        let last = self.dates.len() - 1;
        for (k, pair) in labeled.windows(2).enumerate() {
            let (i, j) = (pair[0], pair[1]);
            let clearance = (j - i) as f64 * band;
            let left_flush = self.crowding.flush && i == 0;
            let right_flush = self.crowding.flush && j == last && self.on_tick(j, vocabulary);
            let reach = |width: f64, flush: bool| if flush { width } else { width / 2.0 };
            let (row_i, row_j) = (
                self.rows(i, vocabulary, k == 0)?,
                self.rows(j, vocabulary, false)?,
            );
            let (wi, wj) = (
                Self::token_width(&row_i, vocabulary, measure)?,
                Self::token_width(&row_j, vocabulary, measure)?,
            );
            if reach(wi, left_flush) + reach(wj, right_flush) > clearance {
                return Ok(false);
            }
            if !row_i.1.is_empty() && !row_j.1.is_empty() {
                let (yi, yj) = (measure(&row_i.1)?, measure(&row_j.1)?);
                if reach(yi, left_flush) + reach(yj, right_flush) > clearance {
                    return Ok(false);
                }
            }
        }
        Ok(true)
    }
}

/// `date` of a `data` grain's bucket sequence opens a `label` period (`is_label_opener`). A row of
/// a year, quarter or month grain is the first of its bucket; a week opens a month within its first
/// seven days, and a year or quarter too; a day only a year within the first seven (production's
/// rule) and a quarter on the first.
pub(crate) fn opens_label(
    (y, m, d): Date,
    data: TimeUnit,
    label: TimeUnit,
    fiscal_start: u8,
) -> bool {
    match label {
        TimeUnit::Year => opens_period(m, 12, fiscal_start) && d <= 7,
        TimeUnit::YearQuarter => {
            opens_period(m, 3, fiscal_start) && (d == 1 || data == TimeUnit::YearWeek && d <= 7)
        }
        TimeUnit::YearMonth => match data {
            TimeUnit::YearWeek => d <= 7,
            TimeUnit::YearMonthDate => d == 1,
            _ => true,
        },
        TimeUnit::YearWeek => data != TimeUnit::YearMonthDate || weekday(y, m, d) == 0,
        TimeUnit::YearMonthDate => true,
    }
}

/// Whether a Vega tick on `(y, m, d)` of a `data` axis opens a `label` period (`opens_label_period`).
/// Unlike [`opens_label`], the per-row predicate, it keeps the gate's own clauses: a monthly axis
/// carries no day clause (every tick inside an opening month passes) and a pair with no clause
/// has no gate at all.
pub(crate) fn tick_opens_label(
    (y, m, d): Date,
    data: TimeUnit,
    label: TimeUnit,
    fiscal_start: u8,
) -> bool {
    use TimeUnit::{Year, YearMonth, YearMonthDate, YearQuarter, YearWeek};
    let day_clause = || match data {
        YearWeek => d <= 7,
        YearMonthDate => d == 1,
        _ => true,
    };
    match (label, data) {
        (YearQuarter, YearMonth | YearWeek | YearMonthDate) => {
            opens_period(m, 3, fiscal_start) && day_clause()
        }
        (YearMonth, YearWeek) => d <= 7,
        (YearMonth, YearMonthDate) => d == 1,
        (YearWeek, YearMonthDate) => weekday(y, m, d) == 0,
        (Year, grain) if grain != Year => opens_period(m, 12, fiscal_start) && day_clause(),
        _ => true,
    }
}

/// The next coarser grain (`next_coarser`).
fn coarser(unit: TimeUnit) -> Option<TimeUnit> {
    match unit {
        TimeUnit::YearMonthDate | TimeUnit::YearWeek => Some(TimeUnit::YearMonth),
        TimeUnit::YearMonth => Some(TimeUnit::YearQuarter),
        TimeUnit::YearQuarter => Some(TimeUnit::Year),
        TimeUnit::Year => None,
    }
}

/// `month` opens a fiscal period of `length` months under a fiscal year starting in `fiscal_start`.
fn opens_period(month: u8, length: i32, fiscal_start: u8) -> bool {
    (i32::from(month) - i32::from(fiscal_start)).rem_euclid(length) == 0
}

/// The two rows a tick of `grain` paints (`format_label`): the grain's own text and its context,
/// the year for a month or quarter that opens a fiscal year or is the axis's first, the month for
/// a day or week that opens one (with the year when it opens a fiscal year too).
pub fn label_rows(
    (year, month, day): Date,
    grain: TimeUnit,
    first: bool,
    fiscal_start: u8,
    quarters: &[String; 4],
) -> Result<Rows, Diagnostic> {
    let opens_year = opens_period(month, 12, fiscal_start);
    let months = || -> Result<_, Diagnostic> {
        let locale = bind::time_locale::active_time_locale().map_err(diag::err_internal)?;
        Ok(locale.short_months[usize::from(month) - 1].clone())
    };
    let context = if first || opens_year {
        format!("{year:04}")
    } else {
        String::new()
    };
    match grain {
        TimeUnit::Year => Ok((format!("{year:04}"), String::new())),
        TimeUnit::YearQuarter => {
            let fiscal_month =
                (i32::from(month) - 1 - (i32::from(fiscal_start) - 1)).rem_euclid(12);
            let quarter = (fiscal_month / 3) as usize;
            Ok((quarters[quarter].clone(), context))
        }
        TimeUnit::YearMonth => Ok((months()?, context)),
        TimeUnit::YearWeek | TimeUnit::YearMonthDate => {
            let opens_month = if grain == TimeUnit::YearWeek {
                day <= 7
            } else {
                day == 1
            };
            let row2 = if first || opens_month && opens_year {
                format!("{}'{:02}", months()?, year % 100)
            } else if opens_month {
                months()?
            } else {
                String::new()
            };
            Ok((day.to_string(), row2))
        }
    }
}

struct Tilt {
    angle: f64,
    fits: bool,
}

/// Every neighboring pair's mean width fits a band (`_fits_flat`); a lone label by its own width.
fn fits_flat(widths: &[f64], band: f64) -> bool {
    match widths {
        [] => true,
        [only] => *only <= band,
        _ => widths
            .windows(2)
            .all(|pair| (pair[0] + pair[1]) / 2.0 <= band),
    }
}

/// The shallowest angle whose rotated footprint `w·cos + font_size·sin` of the widest label fits
/// `band` (`_pick_tilt_for_widths`), else the last angle, colliding; flat when there are none.
fn pick_tilt(widths: &[f64], band: f64, angles: &[f64], font_size: f64) -> Tilt {
    if angles.is_empty() || widths.is_empty() {
        return Tilt {
            angle: 0.0,
            fits: true,
        };
    }
    let widest = widths.iter().copied().fold(0.0, f64::max);
    let footprint = |angle: f64| {
        let (sin, cos) = angle.abs().to_radians().sin_cos();
        widest * cos + font_size * sin
    };
    match angles.iter().find(|&&angle| footprint(angle) <= band) {
        Some(&angle) => Tilt { angle, fits: true },
        None => Tilt {
            angle: angles[angles.len() - 1],
            fits: false,
        },
    }
}
