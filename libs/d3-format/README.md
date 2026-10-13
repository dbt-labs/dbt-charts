# d3-format

Python implementation of the [d3-format](https://github.com/d3/d3-format) spec parser and formatter. Byte-for-byte parity with d3.js across the whole grammar — every type letter, the `#` alternate form, sign modes, padding, and grouping. Raises `D3FormatError` only on text d3 itself rejects, instead of silently producing wrong output.

## Install

This library ships as a peer package inside the `dbt-charts` wheel (`dbt-charts/_build_backend.py`) — it is not published to PyPI or independently installable. Import directly:

```python
from d3_format import format, parse, D3FormatError
```

## API

### `format(spec_str)(value) -> str`

Formats `value` using a d3-format spec string. Matches d3.js's `d3.format(spec)(value)` API.

```python
from d3_format import format

format(",.2f")(1234.56)    # "1,234.56"
format("$,.2f")(1234.56)   # "$1,234.56"
format(".1~%")(0.05)       # "5%"
format("~s")(1500000)      # "1.5M"
format(".2e")(1234.56)     # "1.23e+3"
```

One-shot convenience signature also available:

```python
format(",.2f", 1234.56)    # "1,234.56"
```

### `format_locale(definition) -> FormatLocale`

d3's `formatLocale`: a locale from a d3 locale definition (`decimal`, `thousands`,
`grouping`, `currency`, `numerals`, `percent`, `minus`, `nan`). Its `format(spec)`
works like the module's `format`, which is `EN_US.format`.

```python
from d3_format import format_locale

fr = format_locale({"decimal": ",", "thousands": "\u00a0", "grouping": [3],
                    "currency": ["", "\u00a0€"]})
fr.format("$,.2f")(1234.5)   # "1 234,50 €"
```

### `parse(spec_str) -> FormatSpec`

Parses a d3-format spec string into a `FormatSpec` dataclass. Raises `D3FormatError` on invalid or unsupported syntax.

```python
from d3_format import parse

spec = parse("$,.2f")
# FormatSpec(fill=' ', align='>', sign='-', symbol='$', zero=False,
#            width=None, comma=True, precision=2, trim=False, type='f')
```

### `D3FormatError`

Raised by both `parse` and `format` when a spec falls outside d3's grammar. Carries:

- `spec` — the original spec string
- `position` — character offset where the offending token begins
- `reason` — human-readable description of what was expected

```python
from d3_format import D3FormatError

try:
    format("percent_1")(255)   # not a d3 spec — "_" is not in the grammar
except D3FormatError as e:
    print(e.spec)       # "percent_1"
    print(e.position)   # 1
    print(e.reason)     # "unexpected characters 'ercent_1' after type"
```

## d3-format grammar

```
[[fill]align][sign][symbol][0][width][,][.precision][~][type]
```

| Token | Meaning |
|-------|---------|
| `fill` | Any single character; default space |
| `align` | `>` right (default) · `<` left · `^` center · `=` sign-then-pad |
| `sign` | `-` minus-only (default) · `+` always · `(` parens negatives · ` ` space-for-positive |
| `symbol` | `$` currency prefix · `#` alternate form (`0b`/`0o`/`0x` prefix on `b`/`o`/`x`/`X`) |
| `0` | Zero-pad to width |
| `width` | Minimum output width (integer) |
| `,` | Thousands separator |
| `.precision` | Number of digits after the decimal (or significant figures for `s`, `g`, `r`) |
| `~` | Trim trailing zeros and trailing decimal point |
| `type` | See type table below |

## Types

| Type | Description | Example spec | Input | Output |
|------|-------------|--------------|-------|--------|
| `f` | Fixed-point | `.2f` | 1234.56 | `1234.56` |
| `%` | Percentage (×100 + %) | `.1~%` | 0.05 | `5%` |
| `p` | Percentage to significant digits | `.1p` | 0.1234 | `10%` |
| `e` | Exponential | `.2e` | 1234.56 | `1.23e+3` |
| `s` | SI prefix | `~s` | 1500000 | `1.5M` |
| `g` | General (shorter of `e`/`f`) | `.4g` | 0.0001234 | `0.0001234` |
| `r` | Rounded significant digits | `.2r` | 12.56 | `13` |
| `d` | Integer | `d` | 12.9 | `13` |
| `n` | Locale number (same as `,g`) | `n` | 1234 | `1,234` |
| `b` | Binary | `#b` | 255 | `0b11111111` |
| `o` | Octal | `#o` | 255 | `0o377` |
| `x` | Hex, lowercase | `#x` | 255 | `0xff` |
| `X` | Hex, uppercase | `#X` | 255 | `0xFF` |
| `c` | Character data (verbatim) | `c` | 1234.5678 | `1234.5678` |

The empty type is `.12~g`. So is any other ASCII letter: d3 does not reject an
unrecognized type letter, it falls back, and this port matches that rather than
turning a d3 quirk into a parse error.

`b`, `o`, `x` and `X` round to an integer first, so they carry no precision;
`c` bypasses sign handling entirely and keeps the value's own ASCII hyphen.

## Divergences from d3.js

| Topic | d3.js | This library |
|-------|-------|--------------|
| Non-finite `s` | Reuses the previous call's SI prefix (d3 keeps `prefixExponent` in module state) | Emits no prefix, so the result does not depend on call order |

Everything else — every type letter, SI prefixes, sign modes, parens negatives,
the `#` alternate form, zero-pad, width, align, fill, tilde-trim, precision
clamping, locales — matches d3.js byte-for-byte, verified by the checked-in
fixtures.

## Parity fixture

`tests/fixtures/d3_reference.json` contains 5,665 `(spec, value, expected)` triples captured from d3.js. CI asserts byte-for-byte equality for every triple.

To regenerate when the spec matrix changes:

```bash
cd libs/d3-format/scripts
npm install d3-format
node regenerate_fixture.mjs
```

The script writes directly to `tests/fixtures/d3_reference.json` — do not use `>` redirection (that would overwrite the file with the console status line).

The regeneration script is `scripts/regenerate_fixture.mjs`. Do not run it in CI — the fixture is stable until the spec matrix is intentionally updated.

The locale fixture (`tests/fixtures/locale_parity.json`) comes from the d3-format Vega bundles, through `vl_convert` (a dbt-charts dependency, not this library's): `uv run python libs/d3-format/scripts/regenerate_locale_fixture.py` from the repo root.

## dbt charts-specific extensions

dbt charts wraps this library in `dbt-charts/src/dbt_charts/core/compile/format_value.py` to add:

- **`bi` notation**: d3's SI `k/M/G/T` suffixes are remapped to `K/M/B/T` with no separator (e.g. `"1.5G"` → `"1.5B"`). The `B`-for-billion convention is dbt charts-specific; this library emits `G` per the SI standard.
- **`editorial` notation**: SI suffixes remapped to `k/mn/bn/tr` (journalistic abbreviations, no space).
- **`None` value**: rendered as `"—"` (em dash) regardless of spec.

These extensions are specific to the dbt charts product surface.
