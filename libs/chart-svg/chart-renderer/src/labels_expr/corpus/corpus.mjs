import * as vega from 'vega';
import fs from 'fs';
const exprs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const kinds = (process.argv[4] || 'linear,time,point').split(',');
const KINDS = {
  linear: {scale: {type: 'linear', domain: [-5, 123456.789], range: [0, 300], nice: false}, values: [-5, 0, 7.5, 1000, 123456.789]},
  time: {scale: {type: 'time', domain: [Date.UTC(2020, 0, 1), Date.UTC(2020, 5, 1)], range: [0, 300]}},
  point: {scale: {type: 'point', domain: ['a', 'bb', 'Zürich', '', ' x ', '2020-03-01', '10', '0'], range: [0, 300]}},
};
// What every axis also holds, for the expressions that read the view: scales of each type and the chart's source.
const EXTRA_SCALES = [
  {name: 'y', type: 'linear', domain: [0, 50], range: [50, 0]},
  {name: 'l', type: 'log', domain: [1, 1000], range: [0, 300]},
  {name: 'ln', type: 'log', domain: [-1000, -1], range: [0, 300]},
  {name: 'p', type: 'pow', exponent: 2, domain: [0, 10], range: [0, 300]},
  {name: 'q', type: 'sqrt', domain: [0, 100], range: [0, 300]},
  {name: 's', type: 'symlog', domain: [-100, 100], range: [0, 300]},
  {name: 'u', type: 'utc', domain: [Date.UTC(2020, 0, 1), Date.UTC(2021, 0, 1)], range: [0, 300]},
  {name: 'b', type: 'band', domain: ['a', 'b', 'c', 'd'], range: [0, 300], paddingInner: 0.2, paddingOuter: 0.1},
  {name: 'br', type: 'band', domain: ['a', 'b', 'c', 'd'], range: [300, 0], padding: 0.3},
  {name: 'pt', type: 'point', domain: ['a', 'b', 'c'], range: [0, 300], padding: 0.5},
  {name: 'o', type: 'ordinal', domain: ['a', 'b', 'c'], range: ['red', '#00ff00', 'blue']},
];
const SOURCE = [{a: 1, b: 'x', c: null}, {a: 2, b: 'y', c: 3}, {a: 2, b: 'z', c: 's'}];
const COMPILE = /Unrecognized|parse error|Illegal|Invalid or unexpected|Undefined data|Disabled|signal name|Unexpect|Missing arguments|Too many|literal|Unsupported|is not a function|before initialization|Octal|Invalid regular|Invalid flags|Unexpected token|SyntaxError/i;
function lines(t) {
  const one = v => (v == null ? '' : (v + '').trim());
  if (Array.isArray(t)) return t.length > 1 ? t.map(one) : [one(t[0])];
  return [one(t)];
}
function enc(v) {
  if (v instanceof Date) return {t: 'date', v: +v};
  if (typeof v === 'number') return {t: 'num', v: Number.isNaN(v) ? 'NaN' : v === Infinity ? 'Infinity' : v === -Infinity ? '-Infinity' : v};
  if (typeof v === 'string') return {t: 'str', v};
  return {t: 'other', v: String(v)};
}
async function run(expr, kind) {
  const k = KINDS[kind];
  const spec = {$schema: 'https://vega.github.io/schema/vega/v5.json', width: 300, height: 50, padding: 5,
    data: [{name: 'source_0', values: SOURCE}],
    scales: [{name: 'x', ...k.scale}, ...EXTRA_SCALES],
    axes: [{orient: 'bottom', scale: 'x', labelOverlap: false, ...(k.values ? {values: k.values} : {}), encode: {labels: {update: {text: {signal: expr}}}}}]};
  const errs = [];
  const logger = {level() { return this; }, error: (...a) => { errs.push(a.map(String).join(' ')); }, warn() { return this; }, info() { return this; }, debug() { return this; }};
  let view;
  try {
    view = new vega.View(vega.parse(spec), {renderer: 'none', logLevel: vega.Error}).logger(logger);
  } catch (e) {
    return {expr, kind, status: 'unknown', error: String(e.message || e)};
  }
  try {
    await view.runAsync();
  } catch (e) {
    return {expr, kind, status: 'throws', error: String(e.message || e)};
  }
  if (errs.length) return {expr, kind, status: 'throws', error: errs[0]};
  const ticks = [];
  const walk = n => {
    if (n.role === 'axis-label') for (const it of n.items) ticks.push({value: enc(it.datum.value), label: it.datum.label, index: it.datum.index, lines: lines(it.text), raw: Array.isArray(it.text) ? 'array' : typeof it.text});
    for (const key of ['items']) if (n[key]) for (const c of n[key]) if (c && (c.marktype || c.items)) walk(c);
  };
  walk(view.scenegraph().root);
  return {expr, kind, status: 'ok', ticks};
}
const out = [];
for (const e of exprs) for (const kind of kinds) out.push(await run(e, kind));
fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 1));
console.log(out.length, 'cases', out.filter(c => c.status === 'ok').length, 'ok', out.filter(c => c.status === 'throws').length, 'throws', out.filter(c => c.status === 'unknown').length, 'unknown');
