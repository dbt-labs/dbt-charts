//! Port of d3-shape 3.2.0 `src/symbol.js` and `src/symbol/*.js`: the symbol
//! types, each drawing a shape of `size` square pixels centered on the origin
//! onto a path.

use std::f64::consts::{PI, TAU};

use crate::path::{Command, Path};

/// d3's `symbolsFill` (circle to wye) and `symbolsStroke` (the rest, drawn
/// with a 1.5px round-capped stroke), in d3's order.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SymbolType {
    Circle,
    Cross,
    Diamond,
    Square,
    Star,
    Triangle,
    Wye,
    Plus,
    Times,
    Triangle2,
    Asterisk,
    Square2,
    Diamond2,
}

impl SymbolType {
    /// d3's export name without the `symbol` prefix, lowercased: `circle`, `triangle2`, ...
    pub fn from_name(name: &str) -> Option<SymbolType> {
        Some(match name {
            "circle" => SymbolType::Circle,
            "cross" => SymbolType::Cross,
            "diamond" => SymbolType::Diamond,
            "square" => SymbolType::Square,
            "star" => SymbolType::Star,
            "triangle" => SymbolType::Triangle,
            "wye" => SymbolType::Wye,
            "plus" => SymbolType::Plus,
            "times" => SymbolType::Times,
            "triangle2" => SymbolType::Triangle2,
            "asterisk" => SymbolType::Asterisk,
            "square2" => SymbolType::Square2,
            "diamond2" => SymbolType::Diamond2,
            _ => return None,
        })
    }

    /// `symbol(type, size)()`: the path data.
    pub fn draw(self, size: f64) -> Vec<Command> {
        let mut path = Path::new();
        let sqrt3 = 3f64.sqrt();
        match self {
            SymbolType::Circle => {
                let r = (size / PI).sqrt();
                path.move_to(r, 0.0);
                path.arc(0.0, 0.0, r, 0.0, TAU, false);
            }
            SymbolType::Cross => {
                let r = (size / 5.0).sqrt() / 2.0;
                path.move_to(-3.0 * r, -r);
                path.line_to(-r, -r);
                path.line_to(-r, -3.0 * r);
                path.line_to(r, -3.0 * r);
                path.line_to(r, -r);
                path.line_to(3.0 * r, -r);
                path.line_to(3.0 * r, r);
                path.line_to(r, r);
                path.line_to(r, 3.0 * r);
                path.line_to(-r, 3.0 * r);
                path.line_to(-r, r);
                path.line_to(-3.0 * r, r);
                path.close_path();
            }
            SymbolType::Diamond => {
                let tan30 = (1f64 / 3.0).sqrt();
                let y = (size / (tan30 * 2.0)).sqrt();
                let x = y * tan30;
                path.move_to(0.0, -y);
                path.line_to(x, 0.0);
                path.line_to(0.0, y);
                path.line_to(-x, 0.0);
                path.close_path();
            }
            SymbolType::Square => {
                let w = size.sqrt();
                let x = -w / 2.0;
                path.rect(x, x, w, w);
            }
            SymbolType::Star => {
                let ka = 0.890_813_091_529_285_2;
                let kr = (PI / 10.0).sin() / (7.0 * PI / 10.0).sin();
                let kx = (TAU / 10.0).sin() * kr;
                let ky = -(TAU / 10.0).cos() * kr;
                let r = (size * ka).sqrt();
                let x = kx * r;
                let y = ky * r;
                path.move_to(0.0, -r);
                path.line_to(x, y);
                for i in 1..5 {
                    let a = TAU * f64::from(i) / 5.0;
                    let c = a.cos();
                    let s = a.sin();
                    path.line_to(s * r, -c * r);
                    path.line_to(c * x - s * y, s * x + c * y);
                }
                path.close_path();
            }
            SymbolType::Triangle => {
                let y = -(size / (sqrt3 * 3.0)).sqrt();
                path.move_to(0.0, y * 2.0);
                path.line_to(-sqrt3 * y, -y);
                path.line_to(sqrt3 * y, -y);
                path.close_path();
            }
            SymbolType::Wye => {
                let c = -0.5;
                let s = sqrt3 / 2.0;
                let k = 1.0 / 12f64.sqrt();
                let a = (k / 2.0 + 1.0) * 3.0;
                let r = (size / a).sqrt();
                let (x0, y0) = (r / 2.0, r * k);
                let (x1, y1) = (x0, r * k + r);
                let (x2, y2) = (-x1, y1);
                path.move_to(x0, y0);
                path.line_to(x1, y1);
                path.line_to(x2, y2);
                path.line_to(c * x0 - s * y0, s * x0 + c * y0);
                path.line_to(c * x1 - s * y1, s * x1 + c * y1);
                path.line_to(c * x2 - s * y2, s * x2 + c * y2);
                path.line_to(c * x0 + s * y0, c * y0 - s * x0);
                path.line_to(c * x1 + s * y1, c * y1 - s * x1);
                path.line_to(c * x2 + s * y2, c * y2 - s * x2);
                path.close_path();
            }
            SymbolType::Plus => {
                let r = (size - (size / 7.0).min(2.0)).sqrt() * 0.87559;
                path.move_to(-r, 0.0);
                path.line_to(r, 0.0);
                path.move_to(0.0, r);
                path.line_to(0.0, -r);
            }
            SymbolType::Times => {
                let r = (size - (size / 6.0).min(1.7)).sqrt() * 0.6189;
                path.move_to(-r, -r);
                path.line_to(r, r);
                path.move_to(-r, r);
                path.line_to(r, -r);
            }
            SymbolType::Triangle2 => {
                let s = size.sqrt() * 0.6824;
                let t = s / 2.0;
                let u = (s * sqrt3) / 2.0;
                path.move_to(0.0, -s);
                path.line_to(u, t);
                path.line_to(-u, t);
                path.close_path();
            }
            SymbolType::Asterisk => {
                let r = (size + (size / 28.0).min(0.75)).sqrt() * 0.59436;
                let t = r / 2.0;
                let u = t * sqrt3;
                path.move_to(0.0, r);
                path.line_to(0.0, -r);
                path.move_to(-u, -t);
                path.line_to(u, t);
                path.move_to(-u, t);
                path.line_to(u, -t);
            }
            SymbolType::Square2 => {
                let r = size.sqrt() * 0.4431;
                path.move_to(r, r);
                path.line_to(r, -r);
                path.line_to(-r, -r);
                path.line_to(-r, r);
                path.close_path();
            }
            SymbolType::Diamond2 => {
                let r = size.sqrt() * 0.62625;
                path.move_to(0.0, -r);
                path.line_to(r, 0.0);
                path.line_to(0.0, r);
                path.line_to(-r, 0.0);
                path.close_path();
            }
        }
        path.into_commands()
    }
}
