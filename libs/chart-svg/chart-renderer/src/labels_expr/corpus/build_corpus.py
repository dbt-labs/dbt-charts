"""Rebuild corpus.json: what real Vega paints for each expression in exprs.json.

Run from this directory with Vega 6.2.0 installed (`npm i vega@6.2.0`), as
`uv run python build_corpus.py [exprs.json [corpus.json]]`. The other recorded files rebuild with
`uv run python build_corpus.py exprs_view.json corpus_view.json` (the expressions that read the view),
`TZ=UTC node datestr.mjs` (`datestr.json`: `new Date(s)` for every string shape) and
`node names.mjs > names.txt`, whose second line is `function_names.json`.
`../corpus_tests.rs` replays the result. Each case is `{"e": expression, "o": outcome}`; an outcome is
`"unknown"` (Vega's compiler rejects it), `"throws"` (it throws while painting), `{"c": lines}` (the same lines
at every tick) or one `lines` list per tick, and `o` is a list of three outcomes (linear, time, point axes)
when they differ.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

KINDS = ("linear", "time", "point")


def load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path: str | Path, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


# The Vega whose output the corpus records (the bundle vl-convert 1.9 ships): `npm i vega@6.2.0`.
VEGA = "6.2.0"
installed = load("node_modules/vega/package.json")["version"]
if installed != VEGA:
    sys.exit(
        f"the corpus is Vega {VEGA}'s; node_modules/vega is {installed} (npm i vega@{VEGA})"
    )
dropped: list[str] = []
src = sys.argv[1] if len(sys.argv) > 1 else "exprs.json"
dst = sys.argv[2] if len(sys.argv) > 2 else "corpus.json"
env = dict(os.environ, TZ="UTC")  # the render pins UTC
tmp = Path(tempfile.mkdtemp())
exprs = load(src)
out: list[dict[str, Any]] = []


def run(batch: list[str], kinds: str | None = None) -> list[dict[str, Any]] | None:
    inp, res = tmp / "in.json", tmp / "out.json"
    save(inp, json.dumps(batch))
    cmd = ["node", "--max-old-space-size=3000", "corpus.mjs", str(inp), str(res)] + (
        [kinds] if kinds else []
    )
    done = subprocess.run(cmd, capture_output=True, timeout=600, env=env, check=False)
    return load(res) if done.returncode == 0 else None


# vega-functions' pluck memoizes its accessors per key for the life of the process, so each pluck case runs alone.
for e in (e for e in exprs if "pluck(" in e):
    for kind in KINDS:
        out += run([e], kind) or []
rest = [e for e in exprs if "pluck(" not in e]
for i in range(0, len(rest), 250):
    batch = rest[i : i + 250]
    got = run(batch)
    if (
        got is None
    ):  # one expression exhausted memory (a huge pad or sequence): find it and report it
        got = []
        for e in batch:
            rows = run([e])
            if rows is None:
                dropped.append(e)
            else:
                got += rows
    out += got

ticks: dict[str, list[dict[str, Any]]] = {}
for c in out:
    if c["status"] == "ok":
        ticks.setdefault(
            c["kind"],
            [
                {"value": t["value"], "label": t["label"], "index": t["index"]}
                for t in c["ticks"]
            ],
        )


def outcome(c: dict[str, Any]) -> object:
    if c["status"] != "ok":
        return c["status"]
    lines = [t["lines"] for t in c["ticks"]]
    return {"c": lines[0]} if all(t == lines[0] for t in lines) else lines


by: dict[str, dict[str, object]] = {}
for c in out:
    by.setdefault(c["expr"], {})[c["kind"]] = outcome(c)
cases = []
for e, per_kind in by.items():
    outs = [per_kind[k] for k in KINDS]
    text = json.dumps(outs, ensure_ascii=False)
    try:
        text.encode("utf-8")  # Rust strings cannot hold a lone surrogate
    except UnicodeEncodeError:
        dropped.append(e)
        continue
    if len(text) > 3000:
        dropped.append(e)
        continue
    cases.append({"e": e, "o": outs[0] if all(x == outs[0] for x in outs) else outs})
save(
    dst,
    json.dumps(
        {"kinds": ticks, "cases": cases}, ensure_ascii=False, separators=(",", ":")
    )
    + "\n",
)
print(len(cases), "expressions")
for e in dropped:
    print("dropped:", e[:120])
print(len(dropped), "dropped")
