// Writes ../locale/{number,time}/<tag>.json: d3-format's and d3-time-format's locale files, byte for byte.
//
//   pnpm install --frozen-lockfile && node gen_locales.mjs
//
// A file under ../locale that upstream no longer ships is deleted, so the folder is exactly these releases.
import {copyFileSync, mkdirSync, readFileSync, readdirSync, rmSync} from "node:fs";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pins = JSON.parse(readFileSync(join(here, "package.json"), "utf8")).dependencies;

// `ar-001` names a UN M.49 region (the world), not a country: a region keys the CLDR first weekday, and Vega never
// exposed it.
const SKIP = new Set(["ar-001.json"]);

for (const [pkg, kind] of [["d3-format", "number"], ["d3-time-format", "time"]]) {
  const root = join(here, "node_modules", pkg);
  const got = JSON.parse(readFileSync(join(root, "package.json"), "utf8")).version;
  if (got !== pins[pkg]) {
    console.error(`${pkg}@${pins[pkg]} is pinned; installed ${got}. Run pnpm install --frozen-lockfile.`);
    process.exit(1);
  }
  const out = join(here, "..", "locale", kind);
  rmSync(out, {recursive: true, force: true});
  mkdirSync(out, {recursive: true});
  const names = readdirSync(join(root, "locale")).filter((f) => f.endsWith(".json") && !SKIP.has(f));
  for (const name of names) copyFileSync(join(root, "locale", name), join(out, name));
  console.log(`${pkg}@${got}: ${names.length} locales -> locale/${kind}/`);
}
