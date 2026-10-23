//! d3-geo 3.1.1 `src/projection/albers.js`: `conicEqualArea` with the standard parallels and view of the
//! contiguous United States.

use super::conic_equal_area::conic_equal_area;
use super::Projection;

pub fn albers() -> Projection {
    let mut p = conic_equal_area();
    p.set_parallels([29.5, 45.5])
        .set_scale(1070.0)
        .set_translate([480.0, 250.0])
        .set_rotate([96.0, 0.0, 0.0])
        .set_center([-0.6, 38.7]);
    p
}
