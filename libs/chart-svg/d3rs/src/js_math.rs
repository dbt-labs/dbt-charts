//! JavaScript numeric semantics the d3 and Vega ports depend on.

/// JavaScript truthiness of a number: 0 and NaN are falsy.
pub fn js_truthy(x: f64) -> bool {
    x != 0.0 && !x.is_nan()
}

/// JavaScript's `Math.sign`: NaN and the signed zeros come back as they are.
pub fn js_sign(x: f64) -> f64 {
    if x > 0.0 {
        1.0
    } else if x < 0.0 {
        -1.0
    } else {
        x
    }
}

/// JavaScript's `Math.min` of two numbers: NaN wins (`f64::min` ignores it).
pub fn js_min(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else {
        a.min(b)
    }
}

/// JavaScript's `Math.max` of two numbers: NaN wins (`f64::max` ignores it).
pub fn js_max(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else {
        a.max(b)
    }
}

/// JavaScript's `Math.round`: ties go toward +infinity (`f64::round` sends
/// them away from zero). `x - floor(x)` is exact, so no `x + 0.5` rounding.
pub fn js_round(x: f64) -> f64 {
    let floor = x.floor();
    if x - floor >= 0.5 {
        floor + 1.0
    } else {
        floor
    }
}

/// V8's `Math.hypot`: scaled by the largest magnitude and summed with Kahan compensation.
pub fn hypot(values: &[f64]) -> f64 {
    let nums: Vec<f64> = values.iter().map(|v| v.abs()).collect();
    if nums.is_empty() {
        return 0.0;
    }
    let max = nums.iter().copied().fold(0.0_f64, |m, x| if x > m || x.is_infinite() { x } else { m });
    if max.is_infinite() {
        return f64::INFINITY;
    }
    if nums.iter().any(|x| x.is_nan()) {
        return f64::NAN;
    }
    if max == 0.0 {
        return 0.0;
    }
    let (mut sum, mut compensation) = (0.0_f64, 0.0_f64);
    for x in nums {
        let n = x / max;
        let summand = n * n - compensation;
        let preliminary = sum + summand;
        compensation = (preliminary - sum) - summand;
        sum = preliminary;
    }
    sum.sqrt() * max
}

fn hi(x: f64) -> i32 {
    (x.to_bits() >> 32) as i32
}

fn lo(x: f64) -> u32 {
    x.to_bits() as u32
}

fn with_hi(x: f64, h: i32) -> f64 {
    f64::from_bits((u64::from(h as u32) << 32) | u64::from(lo(x)))
}

fn with_lo(x: f64, l: u32) -> f64 {
    f64::from_bits((x.to_bits() & 0xffff_ffff_0000_0000) | u64::from(l))
}

/// V8's `Math.pow`: fdlibm's `e_pow.c` as V8 12.4 ports it (`src/base/ieee754.cc`), the pow
/// node 22 and vl-convert's Vega run. It is not correctly rounded (`Math.pow(10, 26)` is
/// `9.999999999999999e25`), so d3 code that calls `Math.pow` needs this, not `f64::powf`.
#[allow(
    clippy::approx_constant,
    clippy::eq_op,
    reason = "bit-exact fdlibm constants (std consts can differ in the last bit) and the fdlibm `y - y` NaN idiom"
)]
pub fn pow(x: f64, y: f64) -> f64 {
    const BP: [f64; 2] = [1.0, 1.5];
    const DP_H: [f64; 2] = [0.0, 5.84962487220764160156e-01];
    const DP_L: [f64; 2] = [0.0, 1.35003920212974897128e-08];
    const TWO53: f64 = 9007199254740992.0;
    const HUGE: f64 = 1.0e300;
    const TINY: f64 = 1.0e-300;
    const L1: f64 = 5.99999999999994648725e-01;
    const L2: f64 = 4.28571428578550184252e-01;
    const L3: f64 = 3.33333329818377432918e-01;
    const L4: f64 = 2.72728123808534006489e-01;
    const L5: f64 = 2.30660745775561754067e-01;
    const L6: f64 = 2.06975017800338417784e-01;
    const P1: f64 = 1.66666666666666019037e-01;
    const P2: f64 = -2.77777777770155933842e-03;
    const P3: f64 = 6.61375632143793436117e-05;
    const P4: f64 = -1.65339022054652515390e-06;
    const P5: f64 = 4.13813679705723846039e-08;
    const LG2: f64 = 6.93147180559945286227e-01;
    const LG2_H: f64 = 6.93147182464599609375e-01;
    const LG2_L: f64 = -1.90465429995776804525e-09;
    const OVT: f64 = 8.0085662595372944372e-17;
    const CP: f64 = 9.61796693925975554329e-01;
    const CP_H: f64 = 9.61796700954437255859e-01;
    const CP_L: f64 = -7.02846165095275826516e-09;
    const IVLN2: f64 = 1.44269504088896338700e+00;
    const IVLN2_H: f64 = 1.44269502162933349609e+00;
    const IVLN2_L: f64 = 1.92596299112661746887e-08;

    let (hx, lx) = (hi(x), lo(x));
    let (hy, ly) = (hi(y), lo(y));
    let mut ix = hx & 0x7fffffff;
    let iy = hy & 0x7fffffff;

    if (iy as u32 | ly) == 0 {
        return 1.0;
    }
    if ix > 0x7ff00000 || (ix == 0x7ff00000 && lx != 0) || iy > 0x7ff00000 || (iy == 0x7ff00000 && ly != 0) {
        return x + y;
    }

    let mut yisint = 0;
    if hx < 0 {
        if iy >= 0x43400000 {
            yisint = 2;
        } else if iy >= 0x3ff00000 {
            let k = (iy >> 20) - 0x3ff;
            if k > 20 {
                let j = ly >> (52 - k);
                if (j << (52 - k)) == ly {
                    yisint = 2 - (j & 1) as i32;
                }
            } else if ly == 0 {
                let j = iy >> (20 - k);
                if (j << (20 - k)) == iy {
                    yisint = 2 - (j & 1);
                }
            }
        }
    }

    if ly == 0 {
        if iy == 0x7ff00000 {
            if ((ix - 0x3ff00000) as u32 | lx) == 0 {
                return y - y;
            } else if ix >= 0x3ff00000 {
                return if hy >= 0 { y } else { 0.0 };
            } else {
                return if hy < 0 { -y } else { 0.0 };
            }
        }
        if iy == 0x3ff00000 {
            return if hy < 0 { 1.0 / x } else { x };
        }
        if hy == 0x40000000 {
            return x * x;
        }
        if hy == 0x3fe00000 && hx >= 0 {
            return x.sqrt();
        }
    }

    let mut ax = x.abs();
    if lx == 0 && (ix == 0x7ff00000 || ix == 0 || ix == 0x3ff00000) {
        let mut z = ax;
        if hy < 0 {
            z = 1.0 / z;
        }
        if hx < 0 {
            if ((ix - 0x3ff00000) | yisint) == 0 {
                z = f64::NAN;
            } else if yisint == 1 {
                z = -z;
            }
        }
        return z;
    }

    let mut n = (hx >> 31) + 1;
    if (n | yisint) == 0 {
        return f64::NAN;
    }
    let mut s = 1.0;
    if (n | (yisint - 1)) == 0 {
        s = -1.0;
    }

    let (t1, t2);
    if iy > 0x41e00000 {
        if iy > 0x43f00000 {
            if ix <= 0x3fefffff {
                return if hy < 0 { HUGE * HUGE } else { TINY * TINY };
            }
            if ix >= 0x3ff00000 {
                return if hy > 0 { HUGE * HUGE } else { TINY * TINY };
            }
        }
        if ix < 0x3fefffff {
            return if hy < 0 { s * HUGE * HUGE } else { s * TINY * TINY };
        }
        if ix > 0x3ff00000 {
            return if hy > 0 { s * HUGE * HUGE } else { s * TINY * TINY };
        }
        let t = ax - 1.0;
        let w = (t * t) * (0.5 - t * (0.3333333333333333333333 - t * 0.25));
        let u = IVLN2_H * t;
        let v = t * IVLN2_L - w * IVLN2;
        t1 = with_lo(u + v, 0);
        t2 = v - (t1 - u);
    } else {
        n = 0;
        if ix < 0x00100000 {
            ax *= TWO53;
            n -= 53;
            ix = hi(ax);
        }
        n += (ix >> 20) - 0x3ff;
        let j = ix & 0x000fffff;
        ix = j | 0x3ff00000;
        let k = if j <= 0x3988E {
            0
        } else if j < 0xBB67A {
            1
        } else {
            n += 1;
            ix -= 0x00100000;
            0
        };
        ax = with_hi(ax, ix);

        let u = ax - BP[k];
        let v = 1.0 / (ax + BP[k]);
        let ss = u * v;
        let s_h = with_lo(ss, 0);
        let t_h = with_hi(0.0, ((ix >> 1) | 0x20000000) + 0x00080000 + ((k as i32) << 18));
        let t_l = ax - (t_h - BP[k]);
        let s_l = v * ((u - s_h * t_h) - s_h * t_l);
        let s2 = ss * ss;
        let mut r = s2 * s2 * (L1 + s2 * (L2 + s2 * (L3 + s2 * (L4 + s2 * (L5 + s2 * L6)))));
        r += s_l * (s_h + ss);
        let s2 = s_h * s_h;
        let t_h = with_lo(3.0 + s2 + r, 0);
        let t_l = r - ((t_h - 3.0) - s2);
        let u = s_h * t_h;
        let v = s_l * t_h + t_l * ss;
        let p_h = with_lo(u + v, 0);
        let p_l = v - (p_h - u);
        let z_h = CP_H * p_h;
        let z_l = CP_L * p_h + p_l * CP + DP_L[k];
        let t = f64::from(n);
        t1 = with_lo(((z_h + z_l) + DP_H[k]) + t, 0);
        t2 = z_l - (((t1 - t) - DP_H[k]) - z_h);
    }

    let y1 = with_lo(y, 0);
    let p_l = (y - y1) * t1 + y * t2;
    let mut p_h = y1 * t1;
    let z = p_l + p_h;
    let (j, i) = (hi(z), lo(z));
    if j >= 0x40900000 {
        if ((j - 0x40900000) as u32 | i) != 0 || p_l + OVT > z - p_h {
            return s * HUGE * HUGE;
        }
    } else if (j & 0x7fffffff) >= 0x4090cc00 && (((j as u32).wrapping_sub(0xc090cc00) | i) != 0 || p_l <= z - p_h) {
        return s * TINY * TINY;
    }

    let i = j & 0x7fffffff;
    let mut k = (i >> 20) - 0x3ff;
    let mut n = 0;
    if i > 0x3fe00000 {
        n = j + (0x00100000 >> (k + 1));
        k = ((n & 0x7fffffff) >> 20) - 0x3ff;
        let t = with_hi(0.0, n & !(0x000fffff >> k));
        n = ((n & 0x000fffff) | 0x00100000) >> (20 - k);
        if j < 0 {
            n = -n;
        }
        p_h -= t;
    }
    let t = with_lo(p_l + p_h, 0);
    let u = t * LG2_H;
    let v = (p_l - (t - p_h)) * LG2 + t * LG2_L;
    let z = u + v;
    let w = v - (z - u);
    let t = z * z;
    let t1 = z - t * (P1 + t * (P2 + t * (P3 + t * (P4 + t * P5))));
    // V8's own grouping; fdlibm's `e_pow.c` subtracts `(w + z * w)` after the division instead.
    let r = (z * t1) / ((t1 - 2.0) - (w + z * w));
    let z = 1.0 - (r - z);
    let j = hi(z).wrapping_add(((n as u32) << 20) as i32);
    let z = if (j >> 20) <= 0 { libm::scalbn(z, n) } else { with_hi(z, j) };
    s * z
}

/// ECMAScript WhiteSpace and LineTerminator: what `\s` and `String.prototype.trim` strip.
pub fn is_js_space(c: char) -> bool {
    matches!(
        c,
        '\t' | '\n' | '\u{b}' | '\u{c}' | '\r' | ' ' | '\u{a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}'
            | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}' | '\u{feff}'
    )
}

/// JavaScript's `Number.prototype.toString` for a double: shortest round-trip digits, positional notation
/// for exponents in [-7, 21), else `d.ddde+x`.
pub fn js_number_string(v: f64) -> String {
    if v.is_nan() {
        return "NaN".to_string();
    }
    if v.is_infinite() {
        return if v > 0.0 { "Infinity" } else { "-Infinity" }.to_string();
    }
    if v == 0.0 {
        return "0".to_string();
    }
    let sign = if v < 0.0 { "-" } else { "" };
    let scientific = format!("{:e}", v.abs());
    let (mantissa, exponent) = scientific.split_once('e').unwrap();
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let n = exponent.parse::<i32>().unwrap() + 1;
    let k = digits.len() as i32;
    let body = if k <= n && n <= 21 {
        format!("{digits}{}", "0".repeat((n - k) as usize))
    } else if 0 < n && n <= 21 {
        format!("{}.{}", &digits[..n as usize], &digits[n as usize..])
    } else if -6 < n && n <= 0 {
        format!("0.{}{digits}", "0".repeat((-n) as usize))
    } else {
        let e = n - 1;
        let exp = format!("e{}{}", if e < 0 { '-' } else { '+' }, e.abs());
        if k == 1 {
            format!("{digits}{exp}")
        } else {
            format!("{}.{}{exp}", &digits[..1], &digits[1..])
        }
    };
    format!("{sign}{body}")
}

/// V8's `Math.exp`: fdlibm's `e_exp.c` as `src/base/ieee754.cc` ports it. Its last bit
/// differs from a correctly rounded exp (`Math.exp(1)` is `Math.E` exactly), so d3 code
/// that calls `Math.exp` needs this, not `f64::exp`.
#[allow(
    clippy::approx_constant,
    reason = "bit-exact fdlibm constant, not std's LOG2_E"
)]
pub fn exp(x: f64) -> f64 {
    const HALF: [f64; 2] = [0.5, -0.5];
    const HUGE: f64 = 1.0e300;
    const TWOM1000: f64 = 9.33263618503218878990e-302;
    const O_THRESHOLD: f64 = 7.09782712893383973096e+02;
    const U_THRESHOLD: f64 = -7.45133219101941108420e+02;
    const LN2_HI: [f64; 2] = [6.93147180369123816490e-01, -6.93147180369123816490e-01];
    const LN2_LO: [f64; 2] = [1.90821492927058770002e-10, -1.90821492927058770002e-10];
    const INVLN2: f64 = 1.44269504088896338700e+00;
    const P1: f64 = 1.66666666666666019037e-01;
    const P2: f64 = -2.77777777770155933842e-03;
    const P3: f64 = 6.61375632143793436117e-05;
    const P4: f64 = -1.65339022054652515390e-06;
    const P5: f64 = 4.13813679705723846039e-08;

    let mut x = x;
    let hx = hi(x);
    let xsb = ((hx >> 31) & 1) as usize;
    let hx = hx & 0x7fffffff;
    if hx >= 0x40862E42 {
        if hx >= 0x7ff00000 {
            if ((hx & 0xfffff) as u32 | lo(x)) != 0 {
                return x + x;
            }
            return if xsb == 0 { x } else { 0.0 };
        }
        if x > O_THRESHOLD {
            return HUGE * HUGE;
        }
        if x < U_THRESHOLD {
            return TWOM1000 * TWOM1000;
        }
    }
    let (hi_, lo_, k): (f64, f64, i32) = if hx > 0x3fd62e42 {
        let (hi_, lo_, k) = if hx < 0x3FF0A2B2 {
            // V8 special-cases exp(1) to Math.E; the reduction below gets its last bit wrong.
            if x == 1.0 {
                return std::f64::consts::E;
            }
            (x - LN2_HI[xsb], LN2_LO[xsb], 1 - xsb as i32 - xsb as i32)
        } else {
            let k = (INVLN2 * x + HALF[xsb]) as i32;
            let t = f64::from(k);
            (x - t * LN2_HI[0], t * LN2_LO[0], k)
        };
        x = hi_ - lo_;
        (hi_, lo_, k)
    } else {
        if hx < 0x3e300000 && HUGE + x > 1.0 {
            return 1.0 + x;
        }
        (0.0, 0.0, 0)
    };
    let t = x * x;
    let c = x - t * (P1 + t * (P2 + t * (P3 + t * (P4 + t * P5))));
    if k == 0 {
        return 1.0 - ((x * c) / (c - 2.0) - x);
    }
    let y = 1.0 - ((lo_ - (x * c) / (2.0 - c)) - hi_);
    if k >= -1021 {
        with_hi(y, hi(y) + (k << 20))
    } else {
        with_hi(y, hi(y) + ((k + 1000) << 20)) * TWOM1000
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn js_sign_keeps_nan_and_signed_zero() {
        assert_eq!((js_sign(-3.0), js_sign(2.0)), (-1.0, 1.0));
        assert!(js_sign(-0.0).is_sign_negative() && js_sign(-0.0) == 0.0);
        assert!(js_sign(f64::NAN).is_nan());
    }

    #[test]
    fn js_round_sends_ties_up_and_never_adds_half() {
        assert_eq!(js_round(2.5), 3.0);
        assert_eq!(js_round(-2.5), -2.0);
        assert_eq!(js_round(0.499_999_999_999_999_94), 0.0);
        assert_eq!(js_round(12.68), 13.0);
    }

    /// Expected values are node 22's `Math.pow`.
    #[test]
    fn pow_is_v8s_fdlibm_pow() {
        assert_eq!(pow(10.0, 26.0), 9.999999999999999e25);
        assert_eq!(pow(10.0, -4.0), 0.00009999999999999999);
        assert_eq!(pow(10.0, 25.0), 1e25);
        assert_eq!(pow(7.5, 0.3), 1.8302835609029082);
        assert_eq!(pow(-2.0, 3.0), -8.0);
        assert_eq!(pow(2.0, -1075.0), 0.0);
        assert_eq!(pow(2.0, -1074.0), 5e-324);
        assert_eq!(pow(f64::NAN, 0.0), 1.0);
        assert!(pow(1.0, f64::INFINITY).is_nan());
        assert!(pow(-2.0, 0.5).is_nan());
    }

    #[test]
    fn js_min_lets_nan_win() {
        assert_eq!(js_min(1.0, 2.0), 1.0);
        assert!(js_min(f64::NAN, 2.0).is_nan());
        assert!(js_min(1.0, f64::NAN).is_nan());
    }

    #[test]
    fn js_max_lets_nan_win() {
        assert_eq!(js_max(1.0, 2.0), 2.0);
        assert!(js_max(f64::NAN, 2.0).is_nan());
        assert!(js_max(1.0, f64::NAN).is_nan());
    }
}
