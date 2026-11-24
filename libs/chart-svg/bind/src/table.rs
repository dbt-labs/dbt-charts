//! A table's data: each cell of a flat table of text, boolean, numeric, date and datetime columns, and the header each
//! column heads with: a text column's humanized header, cased by the header font. `chart-renderer/src/families/table/bind.rs` (`visible_columns`,
//! `build_cell_data`) and `compile/resolve/chart/_table.py` decide the same facts; what this slice
//! does not model is refused by name.

use board_model::board::Board;
use board_model::chart::query::ChartQuery;
use board_model::chart::ChartLink;
use board_model::chart::TableChart;
use board_model::ids::ChartId;
use board_model::facet::{ColorOrColumn, FontWeightValue};
use board_model::primitives::LinkSpec;
use board_model::style::table::{CellAlign, ColumnFontStyle, SymbolMode, PixelWidth, TableChartStyle, TableColumnConfig, TableColumnDefaultsConfig};
use column_types::{is_date_like, ColumnType, Dataset};
use diagnostic::{codes, diag, py_str, refuse, Diagnostic};
use serde_json::{json, Value};

use crate::derived::Placed;
use crate::bound::{CellLink, Href, DecimalPad, Empty, Failure, Family, NumberCell, RowLink, Table, TableCell, TableColumn, TableLinks};
use crate::links;
use crate::formats::locale::{active_number_locale, NumberLocale};
use crate::formats::{column_format, ColumnScale, inferred_number_format, AuthoredFormat, Formats, Kind, Repeat, ResolvedFormat, Tiers};
use crate::kpi::{authored_format, declared_column_formats};
use indexmap::IndexMap;
use std::collections::BTreeMap;
use crate::run::RunResult;
use crate::table_dates::parse_temporal;

/// `UNCOMPACTED_COLUMN_SPEC` (`numeral_scale.py`): what a default-format column prints when its vote declines to
/// compact: grouped digits to the most precision a cell can carry, trimmed so whole values stay whole.
const UNCOMPACTED_COLUMN_SPEC: &str = ",.6~f";

/// A numeric column awaiting the table's bake: `out` is its place among the drawn columns, none for a column that only
/// votes (a hidden one, a text one of numeric strings), whose cells paint nowhere.
struct PendingNumbers<'a> {
    out: Option<usize>,
    numbers: Vec<Option<serde_json::Number>>,
    text: bool,
    own: Option<AuthoredFormat>,
    declared: Option<&'a AuthoredFormat>,
    name: String,
    at: String,
}

/// Binds `chart` to its query's rows, with the `style` of the placement it is bound at.
pub fn bind_table(id: &ChartId, chart: &TableChart, placed: &Placed, board: &Board, run: &RunResult) -> Result<Result<Family, Failure>, Diagnostic> {
    let (style, own, board_formats) = (&placed.style.charts.table, &placed.own.charts.table, &placed.scope.total.formats);
    refuse_unmodeled(chart, style, board)?;
    let Some(ChartQuery::Hoisted(query)) = &chart.shared.base.query else {
        return Err(diag::err_internal(format!("chart {id} has no hoisted query")));
    };
    let dataset: &Dataset = run.dataset(query, id)?;
    let rows = dataset.rows.as_slice();
    let Some(first) = rows.first() else {
        // `_materialize_table_columns`: with no row to name its columns, only an authored `style.columns` entry
        // gives the table columns, and those draw their header band over the empty body.
        if !style.columns.is_empty() {
            return Err(Diagnostic::refused(refuse!("a table with `style.columns` over a query that returned no rows is not supported")));
        }
        return Ok(Ok(Family::Table(Table { columns: Vec::new(), empty: Some(Empty { filters: run.empty_filters_line(placed.public_id) }), links: None })));
    };
    let case = style.header.font.case.as_str();
    // `style.columns` styles the query's columns and hides none but those it marks `visible: false`.
    // Production resolves it from the chart's own `style:` alone and reads no board or family tier's.
    let authored = &style.columns;
    if authored.len() != own.columns.len() || authored.keys().any(|name| !own.columns.contains_key(name)) {
        return Err(Diagnostic::refused(refuse!("a `style.columns` set on a board or family tier is not supported")));
    }
    if let Some(unknown) = authored.keys().find(|name| !first.contains_key(name.as_str())) {
        return Err(Diagnostic::refused(refuse!("a `style.columns` entry for a column the query does not return ({unknown}) is not supported")));
    }
    let aliases = crate::formats::aliases(board_formats);
    let formats = Formats::new(Some(&aliases));
    let result_columns: Vec<&str> = first.keys().map(String::as_str).collect();
    let declared: IndexMap<&str, AuthoredFormat> = match board.body.queries.get(query.name().as_str()) {
        Some(model) => declared_column_formats(query.name().as_str(), model, &result_columns)?.into_iter().map(|(column, format)| (column, AuthoredFormat::named(format.as_str()))).collect(),
        None => IndexMap::new(),
    };
    refuse_style_inputs(authored.values(), &style.column_defaults, first)?;
    let mut columns = Vec::with_capacity(first.len());
    // Each drawn column's source name, whether it paints swatches, and its own `link:`, in `columns`' order.
    let mut drawn: Vec<(&str, bool, Option<&LinkSpec>)> = Vec::with_capacity(first.len());
    // The numeric columns, baked together once every column is read: a column's magnitude and precision
    // depend on its siblings' (`number_columns`). A hidden one is baked beside them and drawn nowhere.
    let mut pending: Vec<PendingNumbers<'_>> = Vec::new();
    for name in first.keys() {
        let config = authored.get(name);
        let boxed = column_box(config, &style.column_defaults);
        let hidden = config.is_some_and(|c| c.visible == Some(false));
        if let Some(config) = config {
            refuse_unmodeled_column(config)?;
        }
        let kind = dataset.schema.get(name).map_err(diag::err_internal)?;
        let swatch = config.is_some_and(|c| c.swatch == Some(true));
        let own_format = config.and_then(|c| c.format.as_ref()).map(authored_format);
        let declared_format = declared.get(name.as_str());
        let at = format!("charts.{id}.style.columns.{name}.format");
        if matches!(kind, ColumnType::String) {
            refuse_numeric_text(rows, name, formats)?;
        }
        if hidden {
            // `_bake_scale_stops` bakes every materialized column through `coerce_numeric_cell`: a number (the
            // wire decodes a `Decimal` to one) or a numeric string votes. A boolean, temporal, bytes, list or struct cell coerces to
            // no number, so those columns vote nowhere.
            let numbers = match kind {
                ColumnType::Integer | ColumnType::Float | ColumnType::Decimal => rows.iter().map(|row| number_of(row.get(name)).map(|n| n.cloned())).collect::<Result<Vec<_>, _>>()?,
                ColumnType::String => rows.iter().map(|row| coerced_number(row.get(name))).collect(),
                ColumnType::Json => return Err(Diagnostic::refused(refuse!("a table hidden `json` column is not supported"))),
                _ => continue,
            };
            if numbers.iter().any(Option::is_some) {
                pending.push(PendingNumbers { out: None, numbers, text: matches!(kind, ColumnType::String), own: own_format, declared: declared_format, name: name.clone(), at });
            }
            continue;
        }
        // A swatch cell is a color, which only a text cell holds.
        if swatch && !matches!(kind, ColumnType::String | ColumnType::Boolean) {
            return Err(Diagnostic::refused(refuse!("a table non-text column with `swatch: true` is not supported")));
        }
        let mut default_align = CellAlign::Left;
        let cells = match kind {
            ColumnType::Integer | ColumnType::Float | ColumnType::Decimal => {
                // A left-aligned number paints one start-anchored run beside no lane, and a centered column
                // sizes differently from a lane; neither is modeled.
                match boxed.align {
                    Some(CellAlign::Left) => return Err(Diagnostic::refused(refuse!("a table numeric column with `align: left` is not supported"))),
                    Some(CellAlign::Center) => return Err(Diagnostic::refused(refuse!("a table numeric column with `align: center` is not supported"))),
                    Some(CellAlign::Right) | None => {}
                }
                let numbers = rows.iter().map(|row| number_of(row.get(name)).map(|n| n.cloned())).collect::<Result<Vec<_>, _>>()?;
                pending.push(PendingNumbers { out: Some(columns.len()), numbers, text: false, own: own_format, declared: declared_format, name: name.clone(), at });
                Vec::new()
            }
            ColumnType::String | ColumnType::Boolean => {
                if own_format.is_some() {
                    return Err(Diagnostic::refused(refuse!("a table column with `format` is not supported")));
                }
                if declared_format.is_some() {
                    return Err(Diagnostic::refused(refuse!("a table whose query declares column formats is not supported")));
                }
                let cells = rows.iter().map(|row| if swatch { swatch_cell(row.get(name)).map(TableCell::Text) } else { table_cell(row.get(name), kind) }).collect::<Result<Vec<_>, _>>()?;
                // Its numeric strings still vote in the bake, though it prints them as written.
                if matches!(kind, ColumnType::String) {
                    let numbers: Vec<_> = rows.iter().map(|row| coerced_number(row.get(name))).collect();
                    if numbers.iter().any(Option::is_some) {
                        pending.push(PendingNumbers { out: None, numbers, text: true, own: None, declared: None, name: name.clone(), at });
                    }
                }
                cells
            }
            ColumnType::Date | ColumnType::Datetime => {
                // `is_numeric_cell`: a column with a format paints every cell, a null's dash included, in the numeral
                // face; a date lane takes that face anyway, a text column does not model it.
                if matches!(kind, ColumnType::Datetime) && (own_format.is_some() || declared_format.is_some()) {
                    return Err(Diagnostic::refused(refuse!("a table datetime column with a `format` is not supported")));
                }
                let tiers = Tiers { own: own_format.as_ref(), column: declared_format, board: None, inferred: None };
                let cells = rows.iter().map(|row| temporal_cell(row.get(name), kind, tiers, formats)).collect::<Result<Vec<_>, _>>()?;
                if matches!(kind, ColumnType::Date) && cells.iter().any(|cell| matches!(cell, TableCell::Date(_))) {
                    // `classify_date_column_align`: every cell of a `date` column is date-like, so it aligns right,
                    // and `is_date` makes it a lane. Any other authored alignment leaves the lane, which is not modeled.
                    match boxed.align {
                        Some(CellAlign::Left | CellAlign::Center) => return Err(Diagnostic::refused(refuse!("a table date column with `align: left` or `align: center` is not supported"))),
                        Some(CellAlign::Right) | None => default_align = CellAlign::Right,
                    }
                    // `null_body_font`: a null paints in the body face at the column's midpoint unless the column has
                    // a format, when it paints in the lane's numeral face; only the first is modeled.
                    if (own_format.is_some() || declared_format.is_some()) && cells.iter().any(|cell| matches!(cell, TableCell::Null)) {
                        return Err(Diagnostic::refused(refuse!("a table date column with a `format` and a null cell is not supported")));
                    }
                }
                cells
            }
            _ => return Err(Diagnostic::refused(refuse!("a table column of a type other than text, number, boolean, date or datetime is not supported"))),
        };
        let label = match config.and_then(|c| c.label.as_ref()) {
            Some(label) => column_label(label.as_str())?,
            None => display_text::slug_to_text(name).map_err(diag::err_internal)?,
        };
        let header = display_text::case::apply_case(&label, case).map_err(diag::err_internal)?;
        let align = boxed.align.unwrap_or(default_align);
        let width = boxed.width.map(|width| pixel_width(width, "`width`")).transpose()?;
        let max_width = boxed.max_width.map(|width| pixel_width(width, "`max_width`")).transpose()?;
        let background = boxed.background.map(|color| cell_color(color, "`background`")).transpose()?.flatten();
        let (ink, weight) = boxed.font.map(font_paint).transpose()?.unwrap_or_default();
        let own_link = config.and_then(|c| c.link.as_ref()).filter(|spec| !is_blank(spec));
        // `automatic_link.resolve_filter_cell_link`: a cell in a column named for a board variable links to
        // `?column=value` unless the column authors its own `link:`.
        if own_link.is_none() && run.variables.contains_key(name.as_str()) {
            return Err(Diagnostic::refused(refuse!("a table column named for a board variable is not supported")));
        }
        drawn.push((name.as_str(), swatch, own_link));
        columns.push(TableColumn { header, cells, align, width, swatch, max_width, background, ink, weight });
    }
    if columns.is_empty() {
        return Err(Diagnostic::refused(refuse!("a table whose columns are all hidden is not supported")));
    }
    let numbers: Vec<Vec<Option<&serde_json::Number>>> = pending.iter().map(|column| column.numbers.iter().map(Option::as_ref).collect()).collect();
    let inputs: Vec<NumberInput<'_>> = pending
        .iter()
        .zip(&numbers)
        .map(|(column, cells)| NumberInput { cells, name: &column.name, tiers: Tiers { own: column.own.as_ref(), column: column.declared, board: None, inferred: None }, at: &column.at, role: if column.out.is_some() { Role::Painted } else if column.text { Role::TextVote } else { Role::Vote } })
        .collect();
    match number_columns(&inputs, formats, style.symbol_mode, true) {
        Err(error) if error.code == codes::ERR_FORMAT_SIGN_PLACEMENT_TABLE_UNSUPPORTED.id => {
            return Ok(Err(Failure { chart: id.clone(), diagnostic: error.with_sink_param("chart_id", json!(id.as_str())) }));
        }
        baked => {
            for (column, cells) in pending.iter().zip(baked?) {
                if let Some(out) = column.out {
                    columns[out].cells = cells;
                }
            }
        }
    }
    let links = match bind_links(chart, placed.public_id, &drawn, rows, first, board, run) {
        Err(error) if error.code == codes::ERR_TABLE_LINK_FILTER_UNKNOWN.id || error.code == codes::ERR_LINK_SCHEME_UNSAFE_AT_RENDER.id => {
            let diagnostic = error.with_sink_param("source_path", json!(format!("charts.{id}"))).with_sink_param("chart_id", json!(id.as_str()));
            return Ok(Err(Failure { chart: id.clone(), diagnostic }));
        }
        links => links?,
    };
    Ok(Ok(Family::Table(Table { columns, empty: None, links })))
}

/// Every chart field, style and board fact that changes what the table says and is not modeled.
fn refuse_unmodeled(chart: &TableChart, style: &TableChartStyle, board: &Board) -> Result<(), Diagnostic> {
    let defaults = &style.column_defaults;
    let unmodeled = [
        (board.links.as_ref().is_some_and(|links| links.auto), "automatic links (`links.auto`)"),
        (chart.rows.is_some() || chart.columns.is_some() || chart.values.is_some(), "a pivot (`rows`, `columns` or `values`)"),
        (chart.row_role.is_some(), "a `row_role`"),
        (chart.spanners.is_some(), "`spanners`"),
        (defaults.format.is_some(), "`style.column_defaults.format`"),
        (style.transpose, "`style.transpose`"),
        (!style.row.conditional.is_empty(), "`style.row.conditional`"),
        (board.regional.is_some(), "a locale other than en-US"),
    ];
    match unmodeled.iter().find(|(set, _)| *set) {
        Some((_, what)) => Err(Diagnostic::refused(refuse!("a table with {what} is not supported"))),
        None => Ok(()),
    }
}

/// A table's links as production draws them (`chart-renderer/src/families/table/layout.rs`, `paint_data_rows`): the
/// chart's `link:` (else the host's automatic one) expanded per row, and each non-swatch column's own `link:` per cell, a column's `link:` naming
/// no template when it is empty. Each target is resolved in the host's link context and its scheme checked.
fn bind_links(chart: &TableChart, public_id: &str, drawn: &[(&str, bool, Option<&LinkSpec>)], rows: &[serde_json::Map<String, Value>], first: &serde_json::Map<String, Value>, board: &Board, run: &RunResult) -> Result<Option<TableLinks>, Diagnostic> {
    let href = |template: &str, row: &serde_json::Map<String, Value>| -> Result<Option<Href>, Diagnostic> {
        expand_link(template, row, first)?.map(|expanded| links::resolve(&expanded, run)).transpose()
    };
    let authored_link = match chart.shared.base.link.as_ref() {
        Some(ChartLink::On(spec)) if is_blank(spec) => None,
        link => link,
    };
    let rows_link = links::chart_link(authored_link, links::automatic(run, public_id), board)?;
    let row_links = rows_link
        .map(|(template, open)| {
            // `row_link_label_col`: the first drawn column that is no swatch, else the first.
            let label_column = drawn.iter().find(|(_, swatch, _)| !swatch).or(drawn.first()).map(|(name, ..)| *name);
            rows.iter()
                .map(|row| {
                    Ok(href(template, row)?.map(|href| {
                        let label = label_column.and_then(|name| row.get(name)).map(py_str).filter(|text| !text.is_empty()).unwrap_or_else(|| href.as_str().to_string());
                        RowLink { link: CellLink { href, open }, label }
                    }))
                })
                .collect::<Result<Vec<_>, Diagnostic>>()
        })
        .transpose()?;
    let mut cells = Vec::with_capacity(drawn.len());
    for &(_, swatch, own) in drawn {
        let column = match own.map(|spec| links::spec_link(spec, board)).transpose()?.filter(|_| !swatch) {
            Some((template, open)) => rows.iter().map(|row| Ok(href(template, row)?.map(|href| CellLink { href, open }))).collect::<Result<Vec<_>, Diagnostic>>()?,
            _ => vec![None; rows.len()],
        };
        cells.push(column);
    }
    let authored = rows_link.is_some() || drawn.iter().any(|(_, swatch, own)| !swatch && own.is_some());
    Ok(authored.then_some(TableLinks { rows: row_links, cells }))
}

/// An authored empty `link:` is no link (`de_nonempty_string` on the chart's, `if link_raw:` on a column's).
fn is_blank(spec: &LinkSpec) -> bool {
    match spec {
        LinkSpec::Url(url) => url.as_str().is_empty(),
        LinkSpec::Block(block) => block.url.as_deref() == Some(""),
    }
}

/// `resolve_cell_link` (`chart-renderer/src/families/table/bind.rs`): a `link:` that names a query column verbatim is
/// that row's value, none when it is null or empty. Otherwise each `{{ column }}` or `{{ column | urlencode }}`
/// is replaced by the row's value (`urlencode` is `quote_plus`), a template-free string is the href as written, a
/// null placeholder drops the whole row's link, and an empty result is no link. A `{{ }}` that holds no
/// `\w+` or `\w+ | \w+` stays literal text; another filter is `ERR-TABLE-LINK-FILTER-UNKNOWN`, and only for a
/// non-null value, as production reads the cell before the filter.
fn expand_link(template: &str, row: &serde_json::Map<String, Value>, columns: &serde_json::Map<String, Value>) -> Result<Option<String>, Diagnostic> {
    if columns.contains_key(template) {
        return Ok(row.get(template).filter(|value| !value.is_null()).map(py_str).filter(|text| !text.is_empty()));
    }
    let (mut out, mut has_template, mut has_none, mut at) = (String::new(), false, false, 0);
    while at < template.len() {
        if let Some((inner_len, column, filter)) = template[at..].strip_prefix("{{").and_then(|rest| Some((rest.find("}}")?, rest))).and_then(|(close, rest)| placeholder(&rest[..close]).map(|(column, filter)| (close, column, filter))) {
            has_template = true;
            match row.get(column) {
                None | Some(Value::Null) => has_none = true,
                Some(value) => match filter {
                    None => out.push_str(&py_str(value)),
                    Some("urlencode") => out.push_str(&links::quote_plus(&py_str(value))),
                    Some(other) => return Err(diag::err_table_link_filter_unknown(other)),
                },
            }
            at += 2 + inner_len + 2;
            continue;
        }
        let ch = template[at..].chars().next().expect("at < template.len()");
        out.push(ch);
        at += ch.len_utf8();
    }
    Ok(match (has_template, has_none) {
        (false, _) => Some(template.to_string()),
        (true, true) => None,
        (true, false) => Some(out).filter(|text| !text.is_empty()),
    })
}

/// `parse_template_inner`: the `(column, filter)` of `\s*(\w+)(?:\s*\|\s*(\w+))?\s*`, `\w` being Python's
/// `re` word class (`is_py_word`); none when the span does not match whole.
fn placeholder(inner: &str) -> Option<(&str, Option<&str>)> {
    let word = |text: &str| !text.is_empty() && text.chars().all(d3rs::py_str::is_py_word);
    let trimmed = inner.trim_matches(d3rs::py_str::is_py_space);
    match trimmed.split_once('|') {
        Some((column, filter)) => {
            let (column, filter) = (column.trim_matches(d3rs::py_str::is_py_space), filter.trim_matches(d3rs::py_str::is_py_space));
            (word(column) && word(filter)).then_some((column, Some(filter)))
        }
        None => word(trimmed).then_some((trimmed, None)),
    }
}

/// `swatch_color` (`chart-renderer/src/families/table/bind.rs`): a hex color as written (its one trailing
/// newline stays), or `transparent` / `none` as `transparent`. Production raises `ERR-TABLE-SWATCH-NOT-COLOR`
/// for anything else, which this slice refuses.
pub(crate) fn swatch_cell(cell: Option<&Value>) -> Result<String, Diagnostic> {
    let color = cell.and_then(Value::as_str).and_then(|text| {
        let body = text.strip_suffix('\n').unwrap_or(text);
        let hex = body.strip_prefix('#').is_some_and(|digits| matches!(digits.len(), 3 | 6) && digits.bytes().all(|b| b.is_ascii_hexdigit()));
        match (hex, text.to_ascii_lowercase().as_str()) {
            (true, _) => Some(text.to_string()),
            (false, "transparent" | "none") => Some("transparent".to_string()),
            _ => None,
        }
    });
    color.ok_or_else(|| Diagnostic::refused(refuse!("a table swatch cell that is not a color is not supported")))
}

/// A cell as drawn (`chart-renderer/src/families/table/bind.rs`, `format_table_cell_value`): a null
/// or empty value is the null dash, a boolean prints `True` or `False` (`str(value)`), and a plain
/// string prints as is. Production paints a date-shaped string in the numeral face and a temporal
/// one through a date format, which this does not model.
pub(crate) fn table_cell(cell: Option<&Value>, kind: &ColumnType) -> Result<TableCell, Diagnostic> {
    match (cell, kind) {
        (None | Some(Value::Null), _) => Ok(TableCell::Null),
        (Some(Value::Bool(flag)), ColumnType::Boolean) => Ok(TableCell::Text(if *flag { "True" } else { "False" }.to_string())),
        (Some(Value::String(text)), ColumnType::String) if text.trim().is_empty() => Ok(TableCell::Null),
        (Some(Value::String(text)), ColumnType::String) if !text.trim().is_empty() && !is_date_like(text) && matches!(parse_temporal(text.trim(), &ColumnType::String), Ok(None)) => {
            Ok(TableCell::Text(text.clone()))
        }
        _ => Err(Diagnostic::refused(refuse!("a table cell that is not plain text is not supported"))),
    }
}

/// A `date` or `datetime` cell as `format_table_cell_value` prints it (`format_temporal_cell`): the wire's ISO text
/// through the column's time format, a null the null dash. A date paints in its column's date lane, a timestamp as text.
fn temporal_cell(cell: Option<&Value>, kind: &ColumnType, tiers: Tiers<'_>, formats: Formats<'_>) -> Result<TableCell, Diagnostic> {
    let text = match cell {
        None | Some(Value::Null) => return Ok(TableCell::Null),
        Some(Value::String(text)) => crate::display_text::temporal_text(text, kind, tiers, formats)?,
        Some(other) => return Err(diag::err_internal(format!("a {kind} column holds the cell {other}"))),
    };
    Ok(if matches!(kind, ColumnType::Date) { TableCell::Date(text) } else { TableCell::Text(text) })
}

/// `parse_column_width` (`chart-renderer/src/families/table/layout.rs`) for a pixel count or a string
/// holding a plain positive number (`"120"`), which production pins at that many pixels, for `width` and
/// `max_width` alike (`what` names the key). A percent, a `px` suffix (which production reads as no
/// width) or any other CSS width is refused.
fn pixel_width(width: &PixelWidth, what: &str) -> Result<f64, Diagnostic> {
    match width {
        PixelWidth::Pixels(px) => Ok(f64::from(*px)),
        PixelWidth::Css(text) => text.trim().parse::<f64>().ok().filter(|px| px.is_finite() && *px > 0.0).ok_or_else(|| Diagnostic::refused(refuse!("a table column with a CSS {what} is not supported"))),
    }
}

/// A column's boxed keys once `style.column_defaults` has filled what it leaves unset.
struct ColumnBox<'a> {
    width: Option<&'a PixelWidth>,
    max_width: Option<&'a PixelWidth>,
    align: Option<CellAlign>,
    background: Option<&'a ColorOrColumn>,
    font: Option<&'a ColumnFontStyle>,
}

/// `fill_table_column_defaults` (`compile/models/style/authored/table.py`) for the keys the engine draws: a column's
/// own value beats `style.column_defaults`, which fills a key only when the column leaves it unset, a `font`
/// as one whole object. `width` and `max_width` are one sizing slot: a column that claims either is never
/// filled the other from the defaults.
fn column_box<'a>(config: Option<&'a TableColumnConfig>, defaults: &'a TableColumnDefaultsConfig) -> ColumnBox<'a> {
    let sizing_claimed = config.is_some_and(|c| c.width.is_some() || c.max_width.is_some());
    ColumnBox {
        width: config.and_then(|c| c.width.as_ref()).or(defaults.width.as_ref().filter(|_| !sizing_claimed)),
        max_width: config.and_then(|c| c.max_width.as_ref()),
        align: config.and_then(|c| c.align).or(defaults.align),
        background: config.and_then(|c| c.background.as_ref()).or(defaults.background.as_ref()),
        font: config.and_then(|c| c.font.as_ref()).or(defaults.font.as_ref()),
    }
}

/// `sanitize_color` (`core/colors.py`) for a color that is present: a 3- or 6-digit hex color stands as written,
/// a CSS color name is its hex, `transparent` and `none` in any case are `transparent`, anything else is not a color.
pub fn sanitized_color(color: &str) -> Option<String> {
    let hex = color.strip_prefix('#').is_some_and(|digits| matches!(digits.len(), 3 | 6) && digits.bytes().all(|b| b.is_ascii_hexdigit()));
    if hex {
        Some(color.to_string())
    } else if color.eq_ignore_ascii_case("transparent") || color.eq_ignore_ascii_case("none") {
        Some("transparent".to_string())
    } else {
        ::color::css_named_color_to_hex(color).map(str::to_string)
    }
}

/// `column_font_lanes` / `resolve_cell_style` (`chart-renderer/src/families/table/bind.rs`) for a static color
/// (`what` names the key): an empty color paints nothing, one that is no color is refused (production drops it
/// unpainted). A color that names a query column never gets here (`refuse_style_inputs`).
fn cell_color(color: &ColorOrColumn, what: &str) -> Result<Option<String>, Diagnostic> {
    let spec = color.as_str();
    if spec.is_empty() {
        return Ok(None);
    }
    match sanitized_color(spec) {
        Some(color) => Ok(Some(color)),
        None => Err(Diagnostic::refused(refuse!("a table column with a {what} that is not a hex color, a CSS color name, transparent or none is not supported"))),
    }
}

/// `_style_input_columns` (`compile/resolve/chart/_table.py`): a `background`, `font.color` or `font.weight` that
/// names a query column reads that row's value, and production hides the column it reads, whichever entry or
/// default wrote it and whether or not any column inherits it. Neither is modeled, so any such value anywhere in
/// the authored columns or the defaults is refused, which also keeps a column ID out of every later color read.
fn refuse_style_inputs<'a>(authored: impl Iterator<Item = &'a TableColumnConfig>, defaults: &TableColumnDefaultsConfig, first: &serde_json::Map<String, Value>) -> Result<(), Diagnostic> {
    let sources = authored.map(|c| (c.background.as_ref(), c.font.as_ref())).chain([(defaults.background.as_ref(), defaults.font.as_ref())]);
    for (background, font) in sources {
        let weight = font.and_then(|font| match &font.weight {
            Some(FontWeightValue::Name(name)) => Some(name.as_str()),
            _ => None,
        });
        let inputs = [(background.map(ColorOrColumn::as_str), "`background`"), (font.and_then(|font| font.color.as_ref()).map(ColorOrColumn::as_str), "`font` color"), (weight, "`font`")];
        if let Some((_, what)) = inputs.into_iter().find(|(spec, _)| spec.is_some_and(|spec| first.contains_key(spec))) {
            return Err(Diagnostic::refused(refuse!("a table column with a column ID as its {what} is not supported")));
        }
    }
    Ok(())
}

/// The color and weight a column's `font` paints (`column_font_lanes`), the only two keys production's cell reads.
/// Any other key is refused, as is a weight outside `VALID_FONT_WEIGHTS`, which production drops unpainted.
fn font_paint(font: &ColumnFontStyle) -> Result<(Option<String>, Option<String>), Diagnostic> {
    // Destructured whole, so a key added to `ColumnFontStyle` must be decided here.
    let ColumnFontStyle { extends, family, color, size, weight, style, decoration, case, line_height } = font;
    if extends.is_some() || family.is_some() || size.is_some() || style.is_some() || decoration.is_some() || case.is_some() || line_height.is_some() {
        return Err(Diagnostic::refused(refuse!("a table column with a `font` beyond color and weight is not supported")));
    }
    let ink = color.as_ref().map(|color| cell_color(color, "`font` color")).transpose()?.flatten();
    let weight = match weight {
        None => None,
        Some(FontWeightValue::Name(name)) if name.is_empty() => None,
        Some(weight) => {
            let css = match weight {
                FontWeightValue::Name(name) => name.clone(),
                FontWeightValue::Number(n) => format!("{n}"),
            };
            if !VALID_FONT_WEIGHTS.contains(&css.as_str()) {
                return Err(Diagnostic::refused(refuse!("a table column with a `font` weight that is not a CSS weight is not supported")));
            }
            Some(css)
        }
    };
    Ok((ink, weight))
}

/// board.py's `VALID_FONT_WEIGHTS`: the CSS weights a table cell paints.
const VALID_FONT_WEIGHTS: &[&str] = &["normal", "bold", "100", "200", "300", "400", "500", "600", "700", "800", "900"];

/// `coerce_numeric_cell` (`core/utils.py`) as a JSON number: the cells production's bake reads of a text column.
/// A number keeps its own form (whole numbers infer `integer`), a numeric string reads as a float.
fn coerced_number(cell: Option<&Value>) -> Option<serde_json::Number> {
    match cell {
        Some(Value::Number(n)) => Some(n.clone()),
        other => crate::rows::coerce_numeric_cell(other).and_then(serde_json::Number::from_f64),
    }
}

fn number_of(cell: Option<&Value>) -> Result<Option<&serde_json::Number>, Diagnostic> {
    match cell {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(n)) => Ok(Some(n)),
        Some(other) => Err(diag::err_internal(format!("a numeric column holds the cell {other}"))),
    }
}

/// What a numeric input is for: `Painted` cells print, a vote only joins the bake (`_bake_scale_stops` reads a hidden
/// or text column through `coerce_numeric_cell` but prints none of its cells as numbers), and a text vote differs
/// from the other only in `prints_si`, which `_table.py` reads from the column's non-string cells.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Role {
    Painted,
    Vote,
    TextVote,
}

/// One numeric column's cells, its name (its type default reads it: a `year` column) and the tiers that name
/// its format; `at` is the path a refusal of its format names.
pub struct NumberInput<'a> {
    pub cells: &'a [Option<&'a serde_json::Number>],
    pub name: &'a str,
    pub tiers: Tiers<'a>,
    pub at: &'a str,
    pub role: Role,
}

/// What a column bakes before its cells are formatted (`_table.py:_bake_scale_stops`).
struct Baked {
    format: ResolvedFormat,
    scale: Option<ColumnScale>,
    /// The vote declined to compact the column: its format is the exact digits `,.6~f`.
    exact: bool,
    pad_precision: Option<usize>,
}

/// A table's numeric columns (`allow_shared_scale` false is a chart-attached table's: each value prints at its
/// own magnitude, no column vote): each value through its column's format into the lanes it paints in
/// (`chart-renderer/src/families/table/bind.rs`, `format_numeric_col` and `format_magnitudes`), a null as the
/// null dash. The one place a table number is formatted, with `tiers` naming each column's format.
///
/// A warehouse types a column from its schema, so a numeric column can hold no value; production draws
/// it as dashes under a format it resolved from no values, which is not modeled.
///
/// An SI spec prints each value at its own magnitude, the word in the suffix lane; a money name takes its
/// sub-unit sibling per value (`format_magnitudes`), never the slot-wide vote. A trimmed column is padded
/// to its deepest cell. The numeric columns are baked together as `_table.py` bakes them (a text column of
/// numeric strings that would mix units is refused, `refuse_numeric_text`): an SI column whose values vote one magnitude prints them all in it, a column with no spec of its
/// own whose cells would mix units prints exact digits, and a sibling that prints a unit forces the others
/// onto one scale and one precision.
///
/// A table column paints its prefix in a lane of its own and keeps the sign with the digits, so a format
/// with `sign_placement` is `ERR-FORMAT-SIGN-PLACEMENT-TABLE-UNSUPPORTED` at its `at` (`_table.py`).
pub fn number_columns(inputs: &[NumberInput<'_>], formats: Formats<'_>, symbols: SymbolMode, allow_shared_scale: bool) -> Result<Vec<Vec<TableCell>>, Diagnostic> {
    let mut resolved = Vec::with_capacity(inputs.len());
    let mut tiers = Vec::with_capacity(inputs.len());
    for input in inputs {
        // `_table.py` infers each column's type default for every caller, a chart-attached table's included.
        let input_tiers = Tiers { inferred: inferred_number_format(input.name, input.cells), ..input.tiers };
        let values = input.cells.iter().flatten().map(|n| as_double(n)).collect::<Result<Vec<_>, _>>()?;
        if values.is_empty() {
            return Err(Diagnostic::refused(refuse!("a table numeric column with no value is not supported")));
        }
        // No values to vote over: a money name's sub-unit sibling is picked per value.
        let format = column_format(Kind::Number, input_tiers, formats, None, None, &[])?;
        if format.sign_placement.is_some() {
            return Err(diag::err_format_sign_placement_table_unsupported(input.at));
        }
        refuse_unmodeled_number_format(&format, formats)?;
        let has_format = input_tiers.inferred.is_some() || input_tiers.own.is_some() || input_tiers.column.is_some() || input_tiers.board.is_some();
        resolved.push((values, format, has_format));
        tiers.push(input_tiers);
    }
    let locale = active_number_locale();
    let texts: Vec<bool> = inputs.iter().map(|input| input.role == Role::TextVote).collect();
    let baked = bake_columns(&tiers, &resolved, &texts, formats, allow_shared_scale, &locale)?;
    inputs
        .iter()
        .zip(baked)
        .map(|(input, baked)| {
            // A vote prints nothing: its cells never reach the painter, so a value it would refuse is no error.
            if input.role != Role::Painted {
                return Ok(Vec::new());
            }
            // compile/format.py:`column_symbol_mode`: the format's own `repeat` over the table's.
            let symbols = match baked.format.repeat {
                None => symbols,
                Some(Repeat::Anchor) => SymbolMode::Anchors,
                Some(Repeat::Every) => SymbolMode::All,
            };
            // `symbol_mode: anchors` paints a currency prefix and a unit suffix on the first value's row only.
            let anchor_row = input.cells.iter().position(Option::is_some);
            input
                .cells
                .iter()
                .enumerate()
                .map(|(row, cell)| {
                    let Some(number) = cell else { return Ok(TableCell::Null) };
                    let value = as_double(number)?;
                    // Production passes a printed sign through the digits, not into the prefix lane.
                    let lanes = baked.format.paint_lanes_in(&locale, value, (false, true, symbols == SymbolMode::Anchors, false), baked.scale.as_ref()).map_err(|error| match error.code {
                        code if code == codes::ERR_PERCENT_RANGE.id => Diagnostic::refused(refuse!("a table percent value of 10 or more is not supported")),
                        _ => error,
                    })?;
                    let lane = |text: &str| Some(text.to_string()).filter(|text| !text.is_empty());
                    let magnitude_suffix = locale.magnitude_suffixes().any(|word| word == lanes.suffix.trim());
                    let pad = match baked.pad_precision {
                        Some(precision) => decimal_pad(precision, d3rs::numeral_pad::fractional_digit_count(&lanes.number, &locale.locale.decimal))?,
                        None => DecimalPad::default(),
                    };
                    let (prefix, suffix) = if symbols == SymbolMode::All || anchor_row == Some(row) {
                        (lane(&lanes.prefix), lane(&lanes.suffix))
                    } else {
                        (None, non_anchor_suffix(&lanes.suffix, &baked.format.suffix, &locale))
                    };
                    Ok(TableCell::Number(NumberCell {
                        prefix,
                        number: lanes.number,
                        suffix,
                        measured_prefix: lanes.prefix,
                        measured_suffix: lanes.suffix,
                        pad,
                        value: if number.is_f64() { format!("{value:?}") } else { number.to_string() },
                        magnitude_suffix,
                    }))
                })
                .collect()
        })
        .collect()
}

/// `_table.py:_with_resolved_scale_stops`: every column baked once, then again with the unit its siblings
/// force and the precision they share.
fn bake_columns(tiers: &[Tiers<'_>], resolved: &[(Vec<f64>, ResolvedFormat, bool)], texts: &[bool], formats: Formats<'_>, allow_shared_scale: bool, locale: &NumberLocale) -> Result<Vec<Baked>, Diagnostic> {
    let bake = |force_one_unit: bool, floors: &BTreeMap<i32, usize>| -> Result<Vec<Baked>, Diagnostic> {
        tiers.iter().zip(resolved).map(|(tiers, (values, format, has_format))| bake_column(*tiers, values, format, *has_format, formats, allow_shared_scale, force_one_unit, floors, locale)).collect()
    };
    let mut baked = bake(false, &BTreeMap::new())?;
    if !allow_shared_scale {
        return Ok(baked);
    }
    let prints_exact: Vec<bool> = baked.iter().zip(resolved).map(|(column, (_, _, has_format))| column.exact || (*has_format && column.format.spec == UNCOMPACTED_COLUMN_SPEC)).collect();
    // A column with no format of its own prints SI when its default `number` does.
    let prints_si = |index: usize| -> Result<bool, Diagnostic> {
        let (values, format, has_format) = &resolved[index];
        if baked[index].scale.is_some() {
            return Ok(true);
        }
        // A text cell prints as written, so only a column's non-text cells are evidence of a printed unit.
        if *has_format || baked[index].exact || texts[index] {
            return Ok(false);
        }
        Ok(si_cell_texts(format, formats, values)?.is_some_and(|texts| d3rs::numeral_pad::prints_si_suffix(&texts)))
    };
    let printing: Vec<bool> = (0..baked.len()).map(&prints_si).collect::<Result<_, _>>()?;
    let anchored: Vec<bool> = baked.iter().map(|column| column.scale.is_some()).collect();
    // A column printing its unit on every cell beside one printing it once reads as two notations.
    let mixed_anchoring = anchored.iter().any(|&a| a) && anchored.iter().zip(&printing).any(|(&a, &si)| !a && si);
    let force_one_unit = (prints_exact.iter().any(|&e| e) || mixed_anchoring) && printing.iter().any(|&si| si);
    if force_one_unit {
        baked = bake(true, &BTreeMap::new())?;
    }
    // Columns on one scale share the largest precision any of them needs; the digit spec trims.
    let precision = |scale: &ColumnScale| digit_precision(&scale.digit_spec);
    let mut floors: BTreeMap<i32, usize> = BTreeMap::new();
    for scale in baked.iter().filter_map(|column| column.scale.as_ref()) {
        let floor = floors.entry(scale.exponent).or_insert(0);
        *floor = (*floor).max(precision(scale)?);
    }
    for scale in baked.iter().filter_map(|column| column.scale.as_ref()) {
        if precision(scale)? != floors[&scale.exponent] {
            return bake(force_one_unit, &floors);
        }
    }
    Ok(baked)
}

/// `_table.py:_bake_scale_stops`, one column: its shared magnitude, the exact digits that replace a default
/// SI spec whose cells would mix units, and the precision its decimals are padded to.
#[allow(clippy::too_many_arguments)]
fn bake_column(tiers: Tiers<'_>, values: &[f64], format: &ResolvedFormat, has_format: bool, formats: Formats<'_>, allow_shared_scale: bool, force_one_unit: bool, floors: &BTreeMap<i32, usize>, locale: &NumberLocale) -> Result<Baked, Diagnostic> {
    let si_parts = d3rs::format::parse_parts(&format.spec).ok().filter(|parts| parts.type_ == 's');
    let scale = match (&si_parts, allow_shared_scale) {
        (Some(parts), true) => d3rs::numeral_scale::shared_scale_for_column(values)
            .or_else(|| force_one_unit.then(|| d3rs::numeral_scale::forced_shared_scale(values)).flatten())
            .map(|raw| {
                let magnitude = 10_f64.powi(raw.exponent);
                // The decimals come from the smallest value at or above the unit, so they stay within the
                // significant figures; a row further below prints on that grid or as its floor.
                let majority_scaled = values.iter().map(|value| value.abs()).filter(|value| *value >= magnitude).fold(f64::INFINITY, f64::min) / magnitude;
                let floor = floors.get(&raw.exponent).copied().unwrap_or(0);
                ColumnScale { exponent: raw.exponent, digit_spec: d3rs::numeral_scale::column_digit_spec(parts.sign, parts.precision, majority_scaled, floor) }
            }),
        _ => None,
    };
    let mut format = format.clone();
    let mut exact = false;
    // The vote declined to compact a column whose spec is the engine's default: print every row as exact
    // digits rather than letting each pick its own tier. An authored SI spec, or a board's own `number`, keeps it.
    if allow_shared_scale && scale.is_none() {
        if let Some(texts) = si_cell_texts(&format, formats, values)? {
            if !d3rs::numeral_pad::column_shares_one_printed_unit(&texts) {
                let defaulted = column_format(Kind::Number, Tiers { inferred: None, ..tiers }, formats, Some(UNCOMPACTED_COLUMN_SPEC), None, &[])?;
                if d3rs::format::parse_parts(&defaulted.spec).is_ok_and(|parts| parts.type_ != 's') {
                    format = defaulted;
                    exact = true;
                }
            }
        }
    }
    let decimal = &locale.locale.decimal;
    let (pad_spec, pad_values): (&str, Vec<f64>) = match &scale {
        Some(scale) => (&scale.digit_spec, values.iter().map(|value| value / 10_f64.powi(scale.exponent)).collect()),
        // A column with no format of its own pads only through the SI fallback below (`_table.py`'s
        // `resolved_fmt` is "" without `has_format`), even where the board redefines `number`.
        None => (if has_format || exact { format.spec.as_str() } else { "" }, values.to_vec()),
    };
    let pad_precision = match d3rs::numeral_pad::pad_table_precision(pad_spec) {
        Some(precision) => {
            let mut depths = std::collections::HashSet::new();
            for &value in &pad_values {
                let text = crate::formats::locale::d3_format(pad_spec, value).map_err(Diagnostic::refused)?;
                let floored = scale.is_some() && value != 0.0 && !text.chars().any(|c| ('1'..='9').contains(&c));
                depths.insert(if floored {
                    // The bound prints the smallest step, as deep as the spec.
                    digit_precision(pad_spec)?
                } else {
                    text.split_once('.').map_or(0, |(_, fraction)| fraction.len())
                });
            }
            match depths.iter().max() {
                _ if depths.len() <= 1 => None,
                // The cells this bake formats itself are all as deep as the deepest observed.
                Some(&deepest) if scale.is_some() || exact => Some(precision.min(deepest)),
                _ => Some(precision),
            }
        }
        None => None,
    };
    let pad_precision = match (pad_precision, &scale) {
        (None, None) => match si_cell_texts(&format, formats, values)? {
            Some(texts) => d3rs::numeral_pad::unscaled_pad_precision(&texts, decimal),
            None => None,
        },
        (pad, _) => pad,
    };
    Ok(Baked { format, scale, exact, pad_precision })
}

/// `non_anchor_suffix` (`chart-renderer/src/families/table/bind.rs`): a row that is
/// no anchor keeps no prefix, and a suffix only when it opens with a magnitude word once the authored
/// suffix is peeled off (it is never a magnitude: "EUR" starts with E).
fn non_anchor_suffix(suffix: &str, explicit_suffix: &str, locale: &NumberLocale) -> Option<String> {
    let computed = suffix.strip_suffix(explicit_suffix.trim_end_matches(d3rs::py_str::is_py_space)).unwrap_or(suffix);
    let magnitude = locale.magnitude_suffixes().any(|word| computed.starts_with(word));
    (magnitude && !suffix.is_empty()).then(|| suffix.to_string())
}

/// Each value as an SI spec prints it at its own magnitude (`_per_cell_texts`), or `None` for a spec that
/// is not SI.
fn si_cell_texts(format: &ResolvedFormat, formats: Formats<'_>, values: &[f64]) -> Result<Option<Vec<(f64, String)>>, Diagnostic> {
    if d3rs::format::parse_parts(&format.spec).map_or(true, |parts| parts.type_ != 's') {
        return Ok(None);
    }
    values
        .iter()
        .map(|&value| {
            let spec = crate::formats::sub_unit_spec_for_value(format.raw.as_deref(), &format.spec, value, formats)?;
            Ok((value, crate::formats::locale::d3_format(&spec, value).map_err(Diagnostic::refused)?))
        })
        .collect::<Result<Vec<_>, Diagnostic>>()
        .map(Some)
}

/// The decimals of a baked digit spec, which always names them.
fn digit_precision(spec: &str) -> Result<usize, Diagnostic> {
    d3rs::format::parse_parts(spec).ok().and_then(|parts| parts.precision).ok_or_else(|| diag::err_internal(format!("the baked digit spec {spec} has no precision")))
}

/// `decimal_pad_for` (`chart-renderer/src/families/table/bind.rs`): a whole number takes the `precision + 1`
/// slot of the pad table, a value with `frac` fractional digits `precision - frac`.
fn decimal_pad(precision: usize, frac: usize) -> Result<DecimalPad, Diagnostic> {
    let missing = if frac == 0 {
        precision + 1
    } else {
        precision.checked_sub(frac).ok_or_else(|| diag::err_internal(format!("a trimmed cell prints {frac} fractional digits past its pad table's precision {precision}")))?
    };
    Ok(DecimalPad { missing, dot_kept: missing < precision + 1 })
}

fn as_double(n: &serde_json::Number) -> Result<f64, Diagnostic> {
    n.as_f64().ok_or_else(|| diag::err_internal(format!("the number {n} is not a double")))
}

/// What `number_columns` does not model of a resolved number format, by name.
fn refuse_unmodeled_number_format(format: &ResolvedFormat, formats: Formats<'_>) -> Result<(), Diagnostic> {
    let spec = format.spec.as_str();
    let parts = d3rs::format::parse_parts(spec).ok();
    let outer_space = |affix: &str| affix.trim_matches(d3rs::py_str::is_py_space) != affix;
    let unmodeled = [
        (d3rs::native::predefined_native(spec, 0.0).map_err(Diagnostic::refused)?.is_some(), "a native format (`bytes`, `duration`)"),
        // `paint_lanes` picks the sub-unit sibling from the engine's own table.
        (format.raw.as_deref().and_then(d3rs::format::sub_unit_fallback).is_some_and(|sibling| formats.defines(sibling)), "a board-defined sub-unit sibling (`currency_full`, `number_full`)"),
        (spec.contains('$') && parts.as_ref().is_some_and(|p| matches!(p.type_, '%' | 'p')), "a `$` with a `%` type"),
        (outer_space(&format.prefix) || outer_space(&format.suffix), "an affix with outer whitespace"),
    ];
    match unmodeled.iter().find(|(set, _)| *set) {
        Some((_, what)) => Err(Diagnostic::refused(refuse!("a table numeric column with {what} is not supported"))),
        None => Ok(()),
    }
}

/// A column's label as drawn. Template syntax needs the board's variables and queries; an empty
/// label heads the column with nothing, which this slice does not draw.
fn column_label(label: &str) -> Result<String, Diagnostic> {
    if label.is_empty() {
        return Err(Diagnostic::refused(refuse!("a table column with a blank `label` is not supported")));
    }
    if label.contains("{{") || label.contains("{%") {
        return Err(Diagnostic::refused(refuse!("a table column `label` with template syntax is not supported")));
    }
    Ok(label.to_string())
}

/// Every `style.columns` key that changes what a cell says or how its column is sized and is not
/// modeled; `visible`, `label`, `align`, `format`, `width`, `max_width`, `background` and the `font` color and weight
/// (read by kind or through `column_box` in `bind_table`) are.
fn refuse_unmodeled_column(config: &TableColumnConfig) -> Result<(), Diagnostic> {
    let unmodeled = [
        (config.spark.is_some(), "`spark`"),
        (config.header_overflow.is_some(), "`header_overflow`"),
        (config.header_link.is_some(), "`header_link`"),
        (config.scale.is_some(), "`scale`"),
        (config.glyph.is_some(), "`glyph`"),
        (config.glyph_color.is_some(), "`glyph_color`"),
        (!config.conditional.is_empty(), "`conditional`"),
    ];
    match unmodeled.iter().find(|(set, _)| *set) {
        Some((_, what)) => Err(Diagnostic::refused(refuse!("a table column with {what} is not supported"))),
        None => Ok(()),
    }
}

/// `_table.py:_bake_scale_stops` coerces a text column's numeric strings too, and bakes exact digits onto
/// one whose cells would print mixed units under the default `number`; production then lays that text
/// column out as a numeric lane, which is not modeled.
fn refuse_numeric_text(rows: &[serde_json::Map<String, Value>], name: &str, formats: Formats<'_>) -> Result<(), Diagnostic> {
    let values: Vec<f64> = rows.iter().filter_map(|row| crate::rows::coerce_numeric_cell(row.get(name))).collect();
    if values.is_empty() {
        return Ok(());
    }
    let default = column_format(Kind::Number, Tiers::default(), formats, None, None, &[])?;
    if si_cell_texts(&default, formats, &values)?.is_some_and(|texts| !d3rs::numeral_pad::column_shares_one_printed_unit(&texts)) {
        return Err(Diagnostic::refused(refuse!("a table text column of numeric strings that would print mixed units is not supported")));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn swatch(text: &str) -> Result<String, Diagnostic> {
        swatch_cell(Some(&json!(text)))
    }

    #[test]
    fn a_css_color_name_sanitizes_to_its_hex_in_any_case() {
        assert_eq!(sanitized_color("white").as_deref(), Some("#ffffff"));
        assert_eq!(sanitized_color("WHITE").as_deref(), Some("#ffffff"));
        assert_eq!(sanitized_color("#abc").as_deref(), Some("#abc"));
        assert_eq!(sanitized_color("None").as_deref(), Some("transparent"));
        assert_eq!(sanitized_color("whit"), None);
    }

    fn row(value: Value) -> serde_json::Map<String, Value> {
        value.as_object().unwrap().clone()
    }

    fn expanded(template: &str, data: Value) -> Result<Option<String>, Diagnostic> {
        let data = row(data);
        expand_link(template, &data, &data)
    }

    #[test]
    fn a_link_naming_a_column_is_that_rows_value_and_none_when_null_or_empty() {
        assert_eq!(expanded("url", json!({"url": "https://a.io"})).unwrap().as_deref(), Some("https://a.io"));
        assert_eq!(expanded("url", json!({"url": null})).unwrap(), None);
        assert_eq!(expanded("url", json!({"url": ""})).unwrap(), None);
    }

    #[test]
    fn a_placeholder_takes_the_rows_value_and_urlencode_quotes_it() {
        let data = json!({"name": "A b&c", "n": 3});
        assert_eq!(expanded("/p/{{ name }}/{{n}}", data.clone()).unwrap().as_deref(), Some("/p/A b&c/3"));
        assert_eq!(expanded("/p?q={{ name | urlencode }}", data).unwrap().as_deref(), Some("/p?q=A+b%26c"));
    }

    #[test]
    fn a_null_placeholder_drops_the_whole_link_even_beside_an_unknown_filter() {
        let data = json!({"name": "A", "slug": null});
        assert_eq!(expanded("/p/{{ name }}/{{ slug }}", data.clone()).unwrap(), None);
        assert_eq!(expanded("/p/{{ slug | upper }}", data).unwrap(), None);
    }

    #[test]
    fn an_unknown_filter_on_a_value_is_production_s_error() {
        let error = expanded("/p/{{ name | upper }}", json!({"name": "A"})).unwrap_err();
        assert_eq!(error.code, codes::ERR_TABLE_LINK_FILTER_UNKNOWN.id);
        assert_eq!(error.params["filter_name"], "upper");
    }

    #[test]
    fn braces_that_hold_no_placeholder_stay_text_and_an_empty_expansion_is_no_link() {
        let data = json!({"a": ""});
        assert_eq!(expanded("/p/{{ a b }}", data.clone()).unwrap().as_deref(), Some("/p/{{ a b }}"));
        assert_eq!(expanded("/p", data.clone()).unwrap().as_deref(), Some("/p"));
        assert_eq!(expanded("{{ a }}", data).unwrap(), None);
    }

    #[test]
    fn a_placeholder_is_python_s_word_class_so_a_devanagari_name_stays_text() {
        // `\w` takes `isalnum()` characters, and the vowel signs of this word are not (they are marks).
        let data = json!({"हिंदी": "x"});
        assert_eq!(expanded("/p/{{ हिंदी }}", data).unwrap().as_deref(), Some("/p/{{ हिंदी }}"));
        assert_eq!(placeholder("a_1"), Some(("a_1", None)));
    }

    #[test]
    fn a_swatch_hex_keeps_its_one_trailing_newline() {
        assert_eq!(swatch("#3164a3\n").unwrap(), "#3164a3\n");
    }

    #[test]
    fn a_swatch_none_or_transparent_is_transparent_in_any_case() {
        for text in ["none", "NONE", "Transparent", "transparent"] {
            assert_eq!(swatch(text).unwrap(), "transparent", "{text}");
        }
    }

    #[test]
    fn a_swatch_hex_of_four_or_eight_digits_is_refused() {
        for text in ["#3164", "#3164a3ff", "#31", "3164a3"] {
            assert!(swatch(text).is_err(), "{text}");
        }
    }

    #[test]
    fn a_numeric_string_pins_a_width_at_its_pixels() {
        assert_eq!(pixel_width(&PixelWidth::Css(" 120 ".into()), "`width`").unwrap(), 120.0);
        assert_eq!(pixel_width(&PixelWidth::Pixels(90), "`width`").unwrap(), 90.0);
        for text in ["40%", "120px", "", "0", "-5", "inf", "NaN"] {
            assert!(pixel_width(&PixelWidth::Css(text.into()), "`max_width`").is_err(), "{text}");
        }
    }
}
