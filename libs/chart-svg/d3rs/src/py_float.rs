/// One addend of CPython's `sum()`.
#[derive(Debug, Clone, Copy)]
pub enum PyNum {
    Int(f64),
    Float(f64),
}

/// CPython's `sum()` over floats and ints (3.12+). `bltinmodule.c` sums a
/// leading run of ints exactly, adds the first float to it plainly (the
/// `PyNumber_Add` that leaves the int loop), then compensates each later float
/// by Neumaier's algorithm and adds each later int plainly. 3.11 sums
/// naively; the two agree wherever the partial sums are exact, which every
/// corpus total is. A ported `sum()` whose result is compared for equality or
/// formatted must go through this.
pub fn py_sum(values: impl IntoIterator<Item = PyNum>) -> f64 {
    let mut sum = 0.0_f64;
    let mut compensation = 0.0_f64;
    let mut in_float_loop = false;
    for value in values {
        match value {
            PyNum::Int(x) => sum += x,
            PyNum::Float(x) if !in_float_loop => {
                sum += x;
                in_float_loop = true;
            }
            PyNum::Float(x) => {
                let t = sum + x;
                if sum.abs() >= x.abs() {
                    compensation += (sum - t) + x;
                } else {
                    compensation += (x - t) + sum;
                }
                sum = t;
            }
        }
    }
    if compensation != 0.0 && compensation.is_finite() {
        sum += compensation;
    }
    sum
}

/// [`py_sum`] over a list of floats.
pub fn py_float_sum(values: &[f64]) -> f64 {
    py_sum(values.iter().map(|&x| PyNum::Float(x)))
}
/// `repr(float)`: the shortest round-tripping digits, laid out as CPython's
/// `format_float_short` does for `'r'` — scientific when the decimal point
/// sits at or before `-4` or after `16` digits.
pub fn float_repr(x: f64) -> String {
    if x.is_nan() {
        return "nan".to_string();
    }
    if x.is_infinite() {
        return if x < 0.0 { "-inf" } else { "inf" }.to_string();
    }
    let sign = if x.is_sign_negative() { "-" } else { "" };
    if x == 0.0 {
        return format!("{sign}0.0");
    }
    // `{:e}` yields the shortest round-tripping digits ("d.ddde-7", "de16")
    // but breaks an exact tie between two equally short candidates upward;
    // CPython takes the correctly rounded one, half-even. Re-render at the
    // same digit count in exact mode, keeping the shortest form if the
    // rounded one does not round-trip (a power-of-two boundary).
    let shortest = format!("{:e}", x.abs());
    let digit_count =
        shortest.split('e').next().expect("mantissa").len() - usize::from(shortest.contains('.'));
    let rounded = format!("{:.*e}", digit_count - 1, x.abs());
    let sci = if rounded.parse::<f64>() == Ok(x.abs()) {
        rounded
    } else {
        shortest
    };
    let (mantissa, exp) = sci
        .split_once('e')
        .expect("LowerExp always has an exponent");
    let exp: i32 = exp.parse().expect("LowerExp exponent is an integer");
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let decpt = exp + 1;
    let ndigits = digits.len() as i32;
    let body = if decpt <= -4 || decpt > 16 {
        let mut m = digits[..1].to_string();
        if ndigits > 1 {
            m.push('.');
            m.push_str(&digits[1..]);
        }
        let e = decpt - 1;
        format!("{m}e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs())
    } else if decpt <= 0 {
        format!("0.{}{digits}", "0".repeat((-decpt) as usize))
    } else if decpt >= ndigits {
        format!("{digits}{}.0", "0".repeat((decpt - ndigits) as usize))
    } else {
        format!(
            "{}.{}",
            &digits[..decpt as usize],
            &digits[decpt as usize..]
        )
    };
    format!("{sign}{body}")
}

/// CPython's `format(x, "g")`: `%g` at precision 6. The double's exact
/// binary value is rounded half to even to six significant digits (Rust's
/// `{:e}` with a precision does the same exact rounding); fixed notation
/// when the rounded decimal exponent is in `-4..6`, otherwise exponent
/// notation with at least two exponent digits; trailing zeros are trimmed.
/// d3's `g` type (`format::format_general`) is a different algorithm.
pub fn format_g(x: f64) -> String {
    if x.is_nan() {
        return "nan".to_string();
    }
    if x.is_infinite() {
        return if x < 0.0 { "-inf" } else { "inf" }.to_string();
    }
    let sign = if x.is_sign_negative() { "-" } else { "" };
    if x == 0.0 {
        return format!("{sign}0");
    }
    let sci = format!("{:.5e}", x.abs());
    let (mantissa, exp) = sci
        .split_once('e')
        .expect("LowerExp always has an exponent");
    let exp: i32 = exp.parse().expect("LowerExp exponent is an integer");
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let digits = digits.trim_end_matches('0');
    let body = if (-4..6).contains(&exp) {
        if exp < 0 {
            format!("0.{}{digits}", "0".repeat((-exp - 1) as usize))
        } else if digits.len() as i32 <= exp + 1 {
            format!("{digits}{}", "0".repeat((exp + 1) as usize - digits.len()))
        } else {
            format!("{}.{}", &digits[..=exp as usize], &digits[exp as usize + 1..])
        }
    } else {
        let point = if digits.len() > 1 { "." } else { "" };
        format!(
            "{}{point}{}e{}{:02}",
            &digits[..1],
            &digits[1..],
            if exp < 0 { '-' } else { '+' },
            exp.abs()
        )
    };
    format!("{sign}{body}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn float_sum_is_compensated_like_python_312() {
        assert_eq!(py_float_sum(&[0.1; 10]), 1.0);
        assert_eq!(py_float_sum(&[]), 0.0);
        // Left-to-right addition lands one ulp low here; CPython does not.
        let widths = [300.2, 100.1, 55.000_000_000_000_01];
        assert_eq!(widths.iter().sum::<f64>(), 455.299_999_999_999_95);
        assert_eq!(py_float_sum(&widths), 455.3);
    }

    #[test]
    fn an_int_addend_skips_the_compensation() {
        // Each value is what `sum()` returns for the same list in CPython 3.13.
        let later_int = [
            PyNum::Float(39.289),
            PyNum::Int(200.0),
            PyNum::Float(7.679),
            PyNum::Float(28.675),
        ];
        assert_eq!(py_sum(later_int), 275.643);
        assert_eq!(py_float_sum(&[39.289, 200.0, 7.679, 28.675]), 275.643_000_000_000_03);
        // The first float after a leading int is added plainly, uncompensated.
        let leading_int = [
            PyNum::Int(200.0),
            PyNum::Float(3.317_999_999_999_999_6),
            PyNum::Float(23.016),
            PyNum::Float(60.172),
        ];
        assert_eq!(py_sum(leading_int), 286.506_000_000_000_03);
    }

    #[test]
    fn float_repr_matches_cpython_layouts() {
        let cases: &[(f64, &str)] = &[
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (0.1, "0.1"),
            (1234.5, "1234.5"),
            (100.0, "100.0"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1.5e16, "1.5e+16"),
            (123456789012345680.0, "1.2345678901234568e+17"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1e-7, "1e-07"),
            (1.5e-7, "1.5e-07"),
            (5e-324, "5e-324"),
            (1e300, "1e+300"),
            (0.30000000000000004, "0.30000000000000004"),
            (f64::INFINITY, "inf"),
            (f64::NEG_INFINITY, "-inf"),
        ];
        for (x, want) in cases {
            assert_eq!(float_repr(*x), *want, "repr({x:e})");
        }
    }

    #[test]
    fn float_repr_breaks_exact_ties_half_even_like_cpython() {
        for (text, want) in [
            ("718102875569663.25", "718102875569663.2"),
            ("199608136375041.625", "199608136375041.62"),
            ("1801514316094494.2", "1801514316094494.2"),
            ("-912024360763169.25", "-912024360763169.2"),
        ] {
            let x: f64 = text.parse().unwrap();
            assert_eq!(float_repr(x), want, "{text}");
        }
    }

    /// `fixtures/float_repr_battery.txt` is CPython's own `repr()` of every
    /// double it lists (`tests/test_float_repr_battery.py` keeps it current).
    #[test]
    fn float_repr_is_cpython_repr_across_the_battery() {
        let battery = include_str!("../../fixtures/float_repr_battery.txt");
        let misses: Vec<String> = battery
            .lines()
            .filter_map(|line| {
                let (bits, want) = line.split_once(' ').expect("`<bits> <repr>`");
                let x = f64::from_bits(u64::from_str_radix(bits, 16).expect("hex bits"));
                let got = float_repr(x);
                (got != want).then(|| format!("{bits}: {got} want {want}"))
            })
            .collect();
        assert!(battery.lines().count() > 7000);
        assert!(
            misses.is_empty(),
            "{} misses:\n{}",
            misses.len(),
            misses.join("\n")
        );
    }

    #[test]
    fn format_g_prints_six_significant_digits_like_cpython() {
        // Every expected string is `format(x, "g")` in CPython.
        let cases: &[(f64, &str)] = &[
            (0.0, "0"),
            (-0.0, "-0"),
            (0.5, "0.5"),
            (0.1, "0.1"),
            (100.0, "100"),
            (100000.0, "100000"),
            (1234567.0, "1.23457e+06"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (-1e-7, "-1e-07"),
            (-517.123456789, "-517.123"),
            (0.000123456789, "0.000123457"),
            (1e16, "1e+16"),
            (5e-324, "4.94066e-324"),
            (f64::MAX, "1.79769e+308"),
            (f64::NAN, "nan"),
            (f64::INFINITY, "inf"),
            (f64::NEG_INFINITY, "-inf"),
        ];
        for (x, want) in cases {
            assert_eq!(format_g(*x), *want, "format({x:e}, 'g')");
        }
    }

    #[test]
    fn format_g_breaks_exact_ties_half_even_and_carries_into_exponent_form() {
        let cases: &[(f64, &str)] = &[
            (123456.5, "123456"),
            (1234565.0, "1.23456e+06"),
            (99999.95, "99999.9"),
            (999999.4999, "999999"),
            (999999.5, "1e+06"),
        ];
        for (x, want) in cases {
            assert_eq!(format_g(*x), *want, "format({x:e}, 'g')");
        }
    }

    /// `fixtures/float_g_battery.txt` is CPython's own `format(x, "g")` of
    /// every double it lists (`tests/test_float_g_battery.py` keeps it
    /// current).
    #[test]
    fn format_g_is_cpython_across_the_battery() {
        let battery = include_str!("../../fixtures/float_g_battery.txt");
        let misses: Vec<String> = battery
            .lines()
            .filter_map(|line| {
                let (bits, want) = line.split_once(' ').expect("`<bits> <g>`");
                let x = f64::from_bits(u64::from_str_radix(bits, 16).expect("hex bits"));
                let got = format_g(x);
                (got != want).then(|| format!("{bits}: {got} want {want}"))
            })
            .collect();
        assert!(battery.lines().count() > 7000);
        assert!(
            misses.is_empty(),
            "{} misses:\n{}",
            misses.len(),
            misses.iter().take(20).cloned().collect::<Vec<_>>().join("\n")
        );
    }
}
