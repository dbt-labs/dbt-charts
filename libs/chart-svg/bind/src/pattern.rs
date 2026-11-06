//! The `pattern:` channel's data half (bar, area, pie, heatmap): which field splits the
//! series, which scale the pattern draws on, and which ink paints it. Palettes are resolved
//! by the caller; this module only checks that the palette suits the field.

use diagnostic::{py_str_repr as r, Diagnostic};

use crate::column_kind::{channel_type, ChannelType, ColumnKind, Role};

/// The scale a pattern field draws on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PatternScale {
    /// One motif per discrete value.
    Category,
    /// A ramp over a number column.
    Ramp,
}

/// A pattern palette's own kind.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PaletteKind {
    Categorical,
    Sequential,
    Diverging,
}

/// The authored channels and style a pattern is checked against.
pub struct PatternInput<'a> {
    pub field: &'a str,
    /// The authored `color:` column.
    pub color: Option<&'a str>,
    /// `style.pattern.palette`, or `None` when unset.
    pub palette: Option<&'a str>,
    pub steps: bool,
    /// `style.pattern.color` in the chart's own style.
    pub local_ink: Option<&'a str>,
    /// `style.pattern.ground` names a color palette.
    pub ground_is_palette: bool,
}

/// The series field of a chart whose `pattern:` names none of its drawn `axes` and that
/// authors no `color:`: the pattern alone splits the series, so it is the color field.
pub fn series_field<'a>(pattern: Option<&'a str>, color: Option<&'a str>, axes: &[&str]) -> Option<&'a str> {
    color.or(pattern.filter(|field| !axes.contains(field)))
}

/// The scale a pattern over a column of kind `kind` draws on: a number or boolean ramps,
/// anything else takes one motif per value.
pub fn pattern_scale(kind: ColumnKind, field: &str) -> Result<PatternScale, Diagnostic> {
    Ok(match channel_type(kind, Role::Color, field)? {
        ChannelType::Quantitative => PatternScale::Ramp,
        ChannelType::Temporal | ChannelType::Nominal => PatternScale::Category,
    })
}

/// The palette role an unset `style.pattern.palette` takes.
pub fn default_palette(scale: PatternScale) -> &'static str {
    match scale {
        PatternScale::Category => "pattern_category",
        PatternScale::Ramp => "pattern_sequential",
    }
}

/// Refuses a pattern whose channels or style contradict its field's scale. `palette` is
/// the kind of the palette `input.palette` (or the default role) resolves to.
pub fn check_pattern(input: &PatternInput, scale: PatternScale, palette: PaletteKind) -> Result<(), Diagnostic> {
    let field = input.field;
    if let Some(color) = input.color.filter(|color| *color != field) {
        return Err(Diagnostic::refused(format!(
            "chart.pattern ({}) and chart.color ({}) must name the same column: one column \
             drives both the hue and the pattern of a series.",
            r(field),
            r(color)
        )));
    }
    let ramp = scale == PatternScale::Ramp;
    if ramp == (palette == PaletteKind::Categorical) {
        let name = input.palette.unwrap_or(default_palette(scale));
        let kind = kind_name(palette);
        let (shape, wanted) = if ramp { ("quantitative", "sequential or diverging") } else { ("discrete", "categorical") };
        return Err(Diagnostic::refused(format!(
            "style.pattern.palette {} is a {kind} pattern palette, but pattern field {} is {shape}: \
             use a {wanted} palette.",
            r(name),
            r(field)
        )));
    }
    if !ramp && input.steps {
        return Err(Diagnostic::refused(format!(
            "style.pattern.steps is for a quantitative pattern field; {} is on a category scale, which \
             has no steps.",
            r(field)
        )));
    }
    if let (Some(color), Some(ink)) = (input.color, input.local_ink) {
        return Err(Diagnostic::refused(format!(
            "style.pattern.color ({}) and `color: {color}` both paint the pattern: drop one. With a `color:` \
             channel each series' pattern takes its hue.",
            r(ink)
        )));
    }
    if ramp && input.ground_is_palette {
        return Err(Diagnostic::refused(format!(
            "style.pattern.ground names a color palette, which colors the series of a category pattern; \
             {} is on a {} ramp.",
            r(field),
            kind_name(palette)
        )));
    }
    Ok(())
}

fn kind_name(palette: PaletteKind) -> &'static str {
    match palette {
        PaletteKind::Categorical => "categorical",
        PaletteKind::Sequential => "sequential",
        PaletteKind::Diverging => "diverging",
    }
}

/// The one ink every pattern is drawn in, or `None` when a `color:` channel gives each
/// series its own hue: `style.pattern.color`, else the chart's single-series color.
pub fn pattern_ink<C>(color: Option<&str>, style_ink: Option<C>, single: C) -> Option<C> {
    color.is_none().then(|| style_ink.unwrap_or(single))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(color: Option<&'static str>) -> PatternInput<'static> {
        PatternInput { field: "tier", color, palette: None, steps: false, local_ink: None, ground_is_palette: false }
    }

    #[test]
    fn a_pattern_on_no_axis_and_no_color_is_the_series_field() {
        assert_eq!(series_field(Some("tier"), None, &["region", "sales"]), Some("tier"));
        assert_eq!(series_field(Some("region"), None, &["region", "sales"]), None);
        assert_eq!(series_field(Some("tier"), Some("tier"), &["region"]), Some("tier"));
    }

    #[test]
    fn a_number_column_ramps_and_any_other_takes_one_motif_per_value() {
        assert_eq!(pattern_scale(ColumnKind::Number { year: true }, "f").unwrap(), PatternScale::Ramp);
        assert_eq!(pattern_scale(ColumnKind::Boolean, "f").unwrap(), PatternScale::Ramp);
        assert_eq!(pattern_scale(ColumnKind::Text { year: false, dated: true, date_like: true }, "f").unwrap(), PatternScale::Category);
        assert_eq!(pattern_scale(ColumnKind::Date, "f").unwrap(), PatternScale::Category);
        assert_eq!(default_palette(PatternScale::Ramp), "pattern_sequential");
    }

    fn refusal(input: &PatternInput, scale: PatternScale, palette: PaletteKind) -> String {
        check_pattern(input, scale, palette).unwrap_err().to_string()
    }

    #[test]
    fn a_channel_that_contradicts_the_pattern_is_refused_with_resolves_text() {
        assert!(check_pattern(&input(Some("tier")), PatternScale::Category, PaletteKind::Categorical).is_ok());
        assert_eq!(
            refusal(&input(Some("other")), PatternScale::Category, PaletteKind::Categorical),
            "chart.pattern ('tier') and chart.color ('other') must name the same column: one column drives both \
             the hue and the pattern of a series."
        );
        let inked = PatternInput { local_ink: Some("#123456"), ..input(Some("tier")) };
        assert_eq!(
            refusal(&inked, PatternScale::Category, PaletteKind::Categorical),
            "style.pattern.color ('#123456') and `color: tier` both paint the pattern: drop one. With a `color:` \
             channel each series' pattern takes its hue."
        );
    }

    #[test]
    fn a_palette_that_contradicts_the_scale_is_refused_with_resolves_text() {
        assert_eq!(
            refusal(&input(None), PatternScale::Ramp, PaletteKind::Categorical),
            "style.pattern.palette 'pattern_sequential' is a categorical pattern palette, but pattern field 'tier' \
             is quantitative: use a sequential or diverging palette."
        );
        let steps = PatternInput { steps: true, ..input(None) };
        assert_eq!(
            refusal(&steps, PatternScale::Category, PaletteKind::Categorical),
            "style.pattern.steps is for a quantitative pattern field; 'tier' is on a category scale, which has no \
             steps."
        );
        let ground = PatternInput { ground_is_palette: true, ..input(None) };
        assert_eq!(
            refusal(&ground, PatternScale::Ramp, PaletteKind::Diverging),
            "style.pattern.ground names a color palette, which colors the series of a category pattern; 'tier' is \
             on a diverging ramp."
        );
    }

    #[test]
    fn the_pattern_ink_is_the_style_color_or_the_single_series_color_without_a_color_channel() {
        assert_eq!(pattern_ink(None, None, "#111"), Some("#111"));
        assert_eq!(pattern_ink(None, Some("#222"), "#111"), Some("#222"));
        assert_eq!(pattern_ink(Some("tier"), Some("#222"), "#111"), None);
    }
}
