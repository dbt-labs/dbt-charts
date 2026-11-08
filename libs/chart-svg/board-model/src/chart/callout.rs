//! `chart/authored/callout.py`: a static message, no chrome and no query.

use crate::facet::DisplayText;
use crate::names::ChartWarningCode;
use crate::style::callout::CalloutChartStyle;
use model_derive::Model;

/// Static callout/message chart. It takes none of the shared chart fields.
///
/// `tone:` at the chart root is not a field: it moved to `style.tone`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct CalloutChart {
    /// Body text the callout displays.
    pub message: DisplayText,
    /// Optional chart title shown above the message.
    #[model(absent = "no title")]
    pub title: Option<DisplayText>,
    /// Appearance overrides for this chart alone (tone).
    #[model(fill = fill_style, nullable)]
    pub style: CalloutChartStyle,
    /// Codes of render warnings to suppress for this chart.
    #[model(fill = fill_empty, nullable)]
    pub warnings_ignore: Vec<ChartWarningCode>,
}
