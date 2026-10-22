//! d3-geo 3.1.1 `src/clip/line.js`: Liang-Barsky clipping of one segment to a rectangle.

use crate::js_math::js_truthy;

/// Clips the segment `a`-`b` to [x0, y0, x1, y1] in place; false when it misses the rectangle.
pub fn clip_line(a: &mut [f64; 2], b: &mut [f64; 2], x0: f64, y0: f64, x1: f64, y1: f64) -> bool {
    let (ax, ay, bx, by) = (a[0], a[1], b[0], b[1]);
    let (mut t0, mut t1) = (0.0, 1.0);
    let dx = bx - ax;
    let dy = by - ay;

    let mut r = x0 - ax;
    if !js_truthy(dx) && r > 0.0 {
        return false;
    }
    r /= dx;
    if dx < 0.0 {
        if r < t0 {
            return false;
        }
        if r < t1 {
            t1 = r;
        }
    } else if dx > 0.0 {
        if r > t1 {
            return false;
        }
        if r > t0 {
            t0 = r;
        }
    }

    r = x1 - ax;
    if !js_truthy(dx) && r < 0.0 {
        return false;
    }
    r /= dx;
    if dx < 0.0 {
        if r > t1 {
            return false;
        }
        if r > t0 {
            t0 = r;
        }
    } else if dx > 0.0 {
        if r < t0 {
            return false;
        }
        if r < t1 {
            t1 = r;
        }
    }

    r = y0 - ay;
    if !js_truthy(dy) && r > 0.0 {
        return false;
    }
    r /= dy;
    if dy < 0.0 {
        if r < t0 {
            return false;
        }
        if r < t1 {
            t1 = r;
        }
    } else if dy > 0.0 {
        if r > t1 {
            return false;
        }
        if r > t0 {
            t0 = r;
        }
    }

    r = y1 - ay;
    if !js_truthy(dy) && r < 0.0 {
        return false;
    }
    r /= dy;
    if dy < 0.0 {
        if r > t1 {
            return false;
        }
        if r > t0 {
            t0 = r;
        }
    } else if dy > 0.0 {
        if r < t0 {
            return false;
        }
        if r < t1 {
            t1 = r;
        }
    }

    if t0 > 0.0 {
        *a = [ax + t0 * dx, ay + t0 * dy];
    }
    if t1 < 1.0 {
        *b = [ax + t1 * dx, ay + t1 * dy];
    }
    true
}
