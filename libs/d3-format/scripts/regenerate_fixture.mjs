/**
 * Regenerate the d3_reference.json parity fixture.
 *
 * Usage:
 *   pnpm add --dir libs/d3-format/scripts --ignore-workspace d3-format
 *   node libs/d3-format/scripts/regenerate_fixture.mjs
 *
 * The add writes an untracked package.json and pnpm-lock.yaml beside this
 * script; delete them afterwards.
 * Output is written to libs/d3-format/tests/fixtures/d3_reference.json
 * relative to the script's location (two directories up).
 */

import { format } from 'd3-format';
import { writeFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join, resolve } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUTPUT = resolve(__dirname, '../tests/fixtures/d3_reference.json');

const specs = [
  '.2f', ',.2f', '$,.2f', '.0f', ',.0f', '.1%', '.2%', '.0%', '.1~%',
  '~s', ',.2s', '.2e', '.4e', '.2g', '.2r', 'd', ',d', 'n',
  '0>10.2f', '+,.2f', ' ,.2f', '(,.2f', '06.2f', '.2s', '$,.0f',
  '.3f', '.6f',
  // Rounding-boundary specs: exercise ROUND_HALF_UP vs banker's rounding
  '.1f', '.0e', '.1s', '.0s', '.1g', '.0g', '.1r', '.0r',
  // Empty type (default/formatDefault): always strips trailing zeros
  '', '.6', '.4', '.2',
  // Percent-to-significant-digits: formatRounded(x * 100, p) with a '%' suffix
  'p', '.1p', '.2p', ',.1p', '(.1p', '+.1p', ' .1p', '$.1p', '.1~p', '010.1p', '.0p',
  // Radix types: round-half-up to an integer, then base conversion
  'b', 'o', 'x', 'X',
  '#b', '#o', '#x', '#X',
  ',b', ',o', ',x', ',X',
  '~x', '+x', '(x', ' x',
  '010b', '010o', '010x', '#010x', '#010X', '<10x', '^10x',
  // Character data: the value is stringified verbatim, bypassing sign handling
  'c', '$c', '#c', ',c', '+c', '(c', '~c', '010c', '<10c', '^10c', '=10c', '$10c',
  // Alternate form on a type that takes no 0b/0o/0x prefix — '#' is inert
  '#,.2f', '#s', '#d', '#.1p',
  // Unknown type letters: d3 aliases them to '.12~g', keeping any explicit precision
  'q', '.1q', '.4q', '~q', 'Z', '.3Z',
  // Precision clamping: [1,21] significant for gprs%p, [0,20] fixed for the rest
  '.30f', '.30e', '.30%', '.25g', '.25r', '.25s', '.21p', '.22p',
];

const normalValues = [
  { label: '0', value: 0 },
  { label: '1', value: 1 },
  { label: '-1', value: -1 },
  { label: '0.5', value: 0.5 },
  { label: '-0.5', value: -0.5 },
  { label: '1.5', value: 1.5 },
  { label: '-1.5', value: -1.5 },
  { label: '12.34', value: 12.34 },
  { label: '0.00001234', value: 0.00001234 },
  { label: '1234.5678', value: 1234.5678 },
  { label: '1000000.0', value: 1000000.0 },
  { label: '-1000000.0', value: -1000000.0 },
  { label: '1500000.0', value: 1500000.0 },
  { label: '1500000000.0', value: 1500000000.0 },
  { label: '1500000000000.0', value: 1500000000000.0 },
  { label: '999', value: 999 },
  { label: '1000', value: 1000 },
  { label: '999999', value: 999999 },
  { label: 'NaN', value: NaN },
  { label: 'Infinity', value: Infinity },
  { label: '-Infinity', value: -Infinity },
  { label: '-0', value: -0 },
  // Negative near-zero values that round to zero under low precision
  { label: '-0.4', value: -0.4 },
  { label: '-0.0001', value: -0.0001 },
  { label: '-0.04', value: -0.04 },
  // Extreme magnitudes: exercise SI clamp to ±Y (±1e24)
  { label: '1e25', value: 1e25 },
  { label: '1e30', value: 1e30 },
  { label: '1e-25', value: 1e-25 },
  // Rounding-boundary values: half-steps that distinguish ROUND_HALF_UP vs banker's
  { label: '0.05', value: 0.05 },
  { label: '0.15', value: 0.15 },
  { label: '0.25', value: 0.25 },
  { label: '0.35', value: 0.35 },
  { label: '0.45', value: 0.45 },
  { label: '2.5', value: 2.5 },
  { label: '3.5', value: 3.5 },
  { label: '4.5', value: 4.5 },
  { label: '5.5', value: 5.5 },
  { label: '6.5', value: 6.5 },
  { label: '7.5', value: 7.5 },
  { label: '8.5', value: 8.5 },
  { label: '9.5', value: 9.5 },
  { label: '25', value: 25 },
  { label: '250', value: 250 },
  { label: '2500', value: 2500 },
  { label: '25000', value: 25000 },
  { label: '250000', value: 250000 },
  // Radix-flavored values: hex digit letters (grouping must not treat 'e' as an
  // exponent), byte/word boundaries, and a width wider than the pad target.
  { label: '255', value: 255 },
  { label: '4095', value: 4095 },
  { label: '65261', value: 65261 },
  { label: '65535', value: 65535 },
  { label: '3735928559', value: 3735928559 },
  { label: '-255', value: -255 },
  { label: '0.1234', value: 0.1234 },
  { label: '0.9', value: 0.9 },
  { label: '-0.9', value: -0.9 },
];

const results = [];

for (const spec of specs) {
  let formatter;
  try {
    formatter = format(spec);
  } catch (e) {
    console.error(`Skipping spec ${spec}: ${e.message}`);
    continue;
  }

  for (const { label, value } of normalValues) {
    try {
      const expected = formatter(value);
      results.push({ spec, value: label, expected });
    } catch (e) {
      console.error(`Skipping ${spec}(${label}): ${e.message}`);
    }
  }
}

writeFileSync(OUTPUT, JSON.stringify(results, null, 2));
console.log(`Generated ${results.length} fixtures → ${OUTPUT}`);
