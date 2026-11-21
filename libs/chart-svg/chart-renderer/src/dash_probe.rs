//! Test probe: every dashed stroke in a rendered SVG, with the length its pattern runs along.

use scene::{PathCommand, PathData};

pub(crate) struct Dashed {
    pub tag: String,
    pub dashes: Vec<f64>,
    pub length: f64,
    pub closed: bool,
}

impl Dashed {
    /// `n` whole periods, less the trailing gap on an open stroke, equal the stroke's length.
    pub fn assert_fits(&self) {
        let repeated = if self.dashes.len() % 2 == 1 { [&self.dashes[..], &self.dashes[..]].concat() } else { self.dashes.clone() };
        let period: f64 = repeated.iter().sum();
        let tail = if self.closed { 0.0 } else { repeated[repeated.len() - 1] };
        let n = ((self.length + tail) / period).round();
        assert!((n * period - tail - self.length).abs() < 0.01, "{} {:?} does not end on ink over {}px", self.tag, self.dashes, self.length);
    }
}

fn numbers(text: &str) -> Vec<f64> {
    text.split(|c: char| !(c.is_ascii_digit() || matches!(c, '.' | '-' | 'e')))
        .filter(|t| !t.is_empty())
        .map(|t| t.parse().unwrap_or_else(|_| panic!("number {t:?} in {text:?}")))
        .collect()
}

/// A printed path `d` of `M`, `L`, `C` and `Z` commands.
fn path_data(d: &str) -> PathData {
    let mut data = Vec::new();
    let mut command = None;
    let mut body = String::new();
    let flush = |command: Option<char>, body: &str, data: &mut Vec<PathCommand>| {
        let n = numbers(body);
        match command {
            Some('M') => data.push(PathCommand::M(n[0], n[1])),
            Some('L') => data.push(PathCommand::L(n[0], n[1])),
            Some('C') => data.push(PathCommand::C(n[0], n[1], n[2], n[3], n[4], n[5])),
            Some('Z') => data.push(PathCommand::Z),
            _ => {}
        }
    };
    for c in d.chars() {
        if matches!(c, 'M' | 'L' | 'C' | 'Z') {
            flush(command, &body, &mut data);
            command = Some(c);
            body.clear();
        } else {
            body.push(c);
        }
    }
    flush(command, &body, &mut data);
    PathData(data)
}

pub(crate) fn dashed_strokes(svg: &str) -> Vec<Dashed> {
    let doc = roxmltree::Document::parse(svg).expect("well-formed svg");
    doc.descendants()
        .filter(|n| n.is_element())
        .filter_map(|n| {
            let dashes = numbers(n.attribute("stroke-dasharray")?);
            let attr = |name: &str| -> f64 { n.attribute(name).unwrap_or("0").parse().expect("a number") };
            let (length, closed) = match n.tag_name().name() {
                "line" => ((attr("x2") - attr("x1")).hypot(attr("y2") - attr("y1")), false),
                "rect" => (2.0 * (attr("width") + attr("height")), true),
                "path" => (path_data(n.attribute("d")?).length(), false),
                _ => return None,
            };
            Some(Dashed { tag: n.tag_name().name().to_string(), dashes, length, closed })
        })
        .collect()
}
