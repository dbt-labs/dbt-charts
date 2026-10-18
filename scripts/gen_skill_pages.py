"""Regenerate the packaged skill pages (`dct skills chart|styling|theme`).

Each page is `skill_pages/<name>.md.j2` (orientation, the Finish loop, a few
one-line rules, section order) plus tables pulled from the engine at generation
time: the authored pydantic models, the Typer CLI, the diagnostics registry, the
format and palette registries, the Jinja helpers. `skill_pages/allowlist.yml`
names which keys and sections to show in full; it carries no descriptions,
types or values, and a name the engine no longer has fails generation.

Run via: just gen-skill-pages     (or: uv run python dbt-charts/scripts/gen_skill_pages.py)
Writes:  dbt-charts/src/dbt_charts/ai/skills/<name>/SKILL.md
See skill_pages/README.md for the allowlist grammar.
"""

from __future__ import annotations

import functools
import re
import sys
from pathlib import Path
from typing import Any

import typer
import typer.core
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

_DBT_CHARTS_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIR = Path(__file__).resolve().parent / "skill_pages"
SKILLS_DIR = _DBT_CHARTS_DIR / "src" / "dbt_charts" / "ai" / "skills"
PAGES = ("chart", "styling")

# Generated spans are wrapped in these so the byte share can be measured.
_OPEN, _CLOSE = "\x02", "\x03"
_SUMMARY_MAX_WORDS = 12
PROBLEMS: list[str] = []


def _load() -> dict[str, Any]:
    with (SOURCE_DIR / "allowlist.yml").open() as f:
        return yaml.safe_load(f)


_Command = Any  # type-state: explicit_any — typer vendors its own click Command


@functools.cache
def _schema() -> Any:  # type-state: explicit_any — AuthorableSchema, imported lazily
    from dbt_charts.core.compile.schema.introspection import introspect

    return introspect()


def _names(names: list[str]) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    for m in re.finditer(r"[.!?](?=\s+[A-Z`'\"(])", text):
        head = text[: m.end()]
        if not re.search(r"\b(e\.g|i\.e|vs|etc)\.$", head):
            return head
    return text


def _summary(text: str, where: str, *, max_words: int = _SUMMARY_MAX_WORDS) -> str:
    first = _first_sentence(text).rstrip(".")
    if len(first.split()) > max_words:
        head = re.split(r"[;:] ", first)[0]
        if 3 <= len(head.split()) <= max_words:
            return head
    if len(first.split()) > max_words:
        PROBLEMS.append(
            f"{where}: first sentence is {len(first.split())} words "
            f"(max {max_words}); shorten it in the model: {first!r}"
        )
    return first


# --- model walking -------------------------------------------------------------


def _resolve(path: str) -> tuple[Any, str]:
    """Walk `Root.field.field` or `chart:bar.field` to a model; return it and its YAML path."""
    schema = _schema()
    root, *steps = path.split(".")
    if root.startswith("chart:"):
        tag = root.removeprefix("chart:")
        variants = schema.models["AuthoredChart"].union.variants
        if tag not in variants:
            raise KeyError(f"{path}: no chart type {tag!r}; have {sorted(variants)}")
        model, shown = schema.models[variants[tag]], []
    else:
        if root not in schema.models:
            raise KeyError(f"{path}: no model {root!r}")
        model, shown = schema.models[root], []
    for step in steps:
        step, _, pick = step.partition("=")
        field = next((f for f in model.fields if f.name == step), None)
        if field is None:
            raise KeyError(f"{path}: {model.name} has no field {step!r}")
        nested = [n for n in field.nested_models if not n.endswith("Ref")]
        if pick:
            nested = [n for n in nested if n == pick]
        if len(nested) != 1:
            raise KeyError(
                f"{path}: {model.name}.{step} reaches {nested}; "
                "the allowlist path must reach exactly one model"
            )
        model = schema.models[nested[0]]
        shown.append(step)
    return model, ".".join(shown)


def _field(model: Any, key: str, where: str) -> Any:
    field = next((f for f in model.fields if f.name == key), None)
    if field is None:
        raise KeyError(
            f"{where}: {model.name} has no field {key!r} "
            f"(renamed or removed? have {[f.name for f in model.fields]})"
        )
    return field


_SCALARS = {
    "str": "text",
    "int": "int",
    "float": "number",
    "bool": "bool",
    "Any": "any",
}
_MAX_ENUM = 16


def _split_union(text: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        depth += ch == "["
        depth -= ch == "]"
        if ch == "|" and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    return [*parts, text[start:].strip()]


def _union_label(text: str) -> str:
    names: list[str] = []
    for part in _split_union(text):
        if part in ("None", ""):
            continue
        if m := re.fullmatch(r"list\[(.*)\]", part):
            label = f"list of {_union_label(m.group(1))}"
        elif m := re.fullmatch(r"dict\[\w+, (.*)\]", part):
            label = f"map of {_union_label(m.group(1))}"
        elif part == "ENUM":
            label = "name"
        else:
            label = _SCALARS.get(part, "mapping" if part[:1].isupper() else part)
        if label not in names:
            names.append(label)
    return "|".join(names)


def _type_label(field: Any) -> str:
    """Compact type: enum members, scalar names, `mapping`, `list of X`, `map of X`."""
    if field.enum_values is not None and len(field.enum_values) <= _MAX_ENUM:
        names: list[str] = [str(_SCALARS.get(t, t)) for t in field.extra_union_types]
        names += [
            str(v).lower() if isinstance(v, bool) else str(v) for v in field.enum_values
        ]
        return "|".join(names)
    return _union_label(
        re.sub(r"one of: ('[^']*'(, )?)+(\.\.\.)?", "ENUM", field.type_repr)
    )


def _default(field: Any) -> str:
    d = field.default_repr
    return "" if d in (None, "None", "{}", "[]", "null") else str(d).strip('"')


def _line(field: Any, where: str, *, full: bool) -> str:
    typ = _type_label(field)
    nested = [n for n in field.nested_models if not n.endswith("Ref")]
    if len(nested) == 1 and "mapping" in typ.split("|"):
        fields = _schema().models[nested[0]].fields
        if len(fields) <= 6:
            names = [
                f"{f.name}: {{}}" if _type_label(f) == "mapping" else f.name
                for f in fields
            ]
            typ = typ.replace("mapping", "{" + ", ".join(names) + "}", 1)
    default = _default(field)
    meta = ", ".join(
        x
        for x in (typ if typ != "text" else "", f"default {default}" if default else "")
        if x
    )
    text = (
        " ".join(field.description.split()).rstrip(".")
        if full
        else _summary(field.description, f"{where}.{field.name}")
    )
    return f"- `{field.name}`{f' ({meta})' if meta else ''}: {text}"


# --- generated blocks ----------------------------------------------------------


class Generator:
    def __init__(self, mark: bool) -> None:
        self.cfg = _load()
        self.mark = mark

    def _wrap(self, text: str) -> str:
        return f"{_OPEN}{text}{_CLOSE}" if self.mark else text

    def s(self, key: str) -> str:
        """A surface macro, left for `agent_api.skill_render` to expand per surface."""
        return f"{{{{ s_{key} }}}}"

    def cli(self, text: str) -> str:
        """Text that only the CLI surface sees."""
        return f"{{{{#if_surface cli}}}}{text}{{{{/if_surface}}}}"

    def cli_or_files(self, text: str) -> str:
        """Text for a surface that reads and writes project files: the CLI and file-tool hosts."""
        return f"{{{{#if_tool write_file}}}}{text}{{{{/if_tool}}}}"

    def tool_commands(self) -> str:
        """The tool-surface counterpart of `commands()`: the same loop as tool calls."""
        return (
            "{{#if_surface tool}}"
            "| Tool | What it does |\n|---|---|\n"
            "| `execute_sql(source=SOURCE, sql=SQL)` | Run SQL against a source; "
            "the result is CSV under a byte budget, so bound rows with SQL LIMIT. |\n"
            "| `render_board(yaml_content=YAML)` | Run the board's queries and return what "
            "each chart draws (values, ranges, value labels), any problems, and the "
            "board's preview URL. |\n"
            '{{#if_tool search_files}}| `search_files(text="")` | List the project\'s boards; a keyword '
            "searches them; each hit's `board_path` is what `render_board` takes "
            "as `path`. |\n{{/if_tool}}"
            "{{#if_tool write_file}}"
            "| Your host's file tools | Read, write and change board files; "
            "`render_board(path=BOARD)` then checks the saved file. |\n"
            "{{/if_tool}}"
            "| `get_skill(name)` | Read another skill page. |"
            "{{/if_surface}}"
        )

    def keys(self, name: str) -> str:
        """Key list for one allowlisted object, then its unlisted keys by name."""
        spec = self.cfg["objects"][name]
        model, shown = _resolve(spec["path"])
        where = f"objects.{name}"
        detail = set(spec.get("detail", []))
        lines = [
            _line(_field(model, k, where), where, full=k in detail)
            for k in spec["keys"]
        ]
        rest = [
            f.name
            for f in model.fields
            if f.name not in [*spec["keys"], *spec.get("hide", [])]
            and not f.name.startswith("_")
        ]
        label = f"`{spec.get('label', shown or model.name)}`"
        names = ", ".join(f"`{k}`" for k in rest)
        if spec.get("also", True) is False:
            rest = []
        if not lines:
            return self._wrap(f"{label}: {names}.")
        out = [f"{label}:", *lines]
        if rest:
            out.append(f"Also exist: {names}.")
        return self._wrap("\n".join(out))

    def key_names(self, name: str) -> str:
        """Every key of an object as a bare comma list (closed objects, e.g. style root)."""
        model, _ = _resolve(self.cfg["objects"][name]["path"])
        return self._wrap(", ".join(f"`{f.name}`" for f in model.fields))

    def enum(self, path: str, key: str) -> str:
        model, _ = _resolve(path)
        field = _field(model, key, path)
        if field.enum_values is None:
            raise ValueError(f"{path}.{key} is not an enum")
        return self._wrap(", ".join(f"`{v}`" for v in field.enum_values))

    def chart_types(self) -> str:
        """Per chart type: the shared keys it takes, then its own keys in full."""
        schema = _schema()
        variants = schema.models["AuthoredChart"].union.variants
        shared = set(self.cfg["objects"]["chart_common"]["keys"])
        out = []
        for entry in self.cfg["chart_types"]:
            tag, keys = entry["type"], entry.get("keys", [])
            if tag not in variants:
                raise KeyError(f"chart_types: no chart type {tag!r}")
            model = schema.models[variants[tag]]
            where = f"chart_types.{tag}"
            for k in keys:
                _field(model, k, where)
            required = [f.name for f in model.fields if f.required and f.name != "type"]
            takes = [k for k in keys if k in shared]
            have = {f.name for f in model.fields}
            lacks = [
                k for k in self.cfg["objects"]["chart_common"]["keys"] if k not in have
            ]
            parts = [f"requires {_names(required)}"] if required else []
            if takes:
                parts.append(f"takes {_names(takes)}")
            if lacks:
                parts.append(f"rejects {_names(lacks)}")
            out.append(f"- `{tag}`" + (": " + "; ".join(parts) if parts else ""))
            out.extend(
                "  " + _line(_field(model, k, where), where, full=False)
                for k in keys
                if k not in shared
            )
        listed = {e["type"] for e in self.cfg["chart_types"]}
        rest = [t for t in variants if t not in listed]
        out.append("Also exist: " + _names(rest) + ".")
        return self._wrap("\n".join(out))

    def query_types(self) -> str:
        """Query kinds beyond SQL: each variant's `type` and its model's own summary."""
        schema = _schema()
        kinds: dict[str, list[str]] = {}
        for cls in schema.models["AuthoredQuery"].union.variants.values():
            model = schema.models[cls]
            tag = _field(model, "type", cls).enum_values[0]
            if tag != "sql":
                kinds.setdefault(tag, []).append(
                    _first_sentence(model.doc).rstrip(".").lower()
                )
        out = [f"`{t}` ({', or '.join(dict.fromkeys(d))})" for t, d in kinds.items()]
        return self._wrap(", ".join(out))

    def layer_types(self) -> str:
        """The mark types a chart `layers:` entry can take, from its layer models."""
        schema = _schema()
        field = _field(schema.models["BarChart"], "layers", "layer_types")
        tags = [
            _field(schema.models[n], "type", n).enum_values[0]
            for n in field.nested_models
        ]
        return self._wrap(_names(tags))

    def rules(self, name: str) -> str:
        """Cross-field rules a model declares, as the validator states them."""
        spec = self.cfg["rules"][name]
        model, _ = _resolve(spec["path"])
        plain = [c.message for c in model.constraints if "{" not in c.message]
        wanted = spec["only"]
        for w in wanted:
            if not any(w in m for m in plain):
                raise KeyError(f"rules.{name}: no {model.name} rule contains {w!r}")
        msgs = [m for m in dict.fromkeys(plain) if any(w in m for w in wanted)]
        return self._wrap("\n".join(f"- {m}" for m in msgs))

    def codes(self, group: str) -> str:
        """Diagnostic code, its registry summary, and its fix text where it has one."""
        from dbt_charts.core.diagnostics.registry import REGISTRY

        out = []
        for code in self.cfg["codes"][group]:
            if code not in REGISTRY.codes():
                raise KeyError(
                    f"codes.{group}: {code} is not in the diagnostics registry"
                )
            dc = REGISTRY.get(code)
            summary = _summary(dc.summary or dc.doc, code, max_words=26)
            summary = re.sub(r"^Fire[sd]? when ", "", summary)
            fix = getattr(dc, "fix_template", None)
            tail = f" Fix: {' '.join(fix.split())}" if fix and "{" not in fix else ""
            out.append(f"- `{code}`: {summary[:1].upper()}{summary[1:]}.{tail}")
        return self._wrap("\n".join(out))

    def init_command(self) -> str:
        """`dct init` with the opt-outs for the integrations an agent run does not want."""
        cmd = _cli_commands()["init"]
        known = {
            o for p in cmd.params for o in [*p.opts, *getattr(p, "secondary_opts", [])]
        }
        flags = self.cfg["init_flags"]
        missing = [f for f in flags if f not in known]
        if missing:
            raise KeyError(f"init_flags: `dct init` has no option {missing}")
        return self._wrap(" ".join(["dct init", *flags]))

    def commands(self) -> str:
        cmds = _cli_commands()
        rows = ["| Command | What it does |", "|---|---|"]
        for row in self.cfg["commands"]:
            cmd = cmds[row["cmd"]]
            if cmd.help is None:
                raise ValueError(f"`dct {row['cmd']}` has no help text")
            paras = _paragraphs(cmd.help)
            if "line" in row:
                if row["line"] not in cmd.help:
                    raise KeyError(
                        f"commands: {row['line']!r} is not in `dct {row['cmd']}` help"
                    )
                form = row["line"]
            else:
                form = _form(cmd, row)
            if "option" in row:
                option = next(
                    p
                    for p in cmd.params
                    if isinstance(p, typer.core.TyperOption) and row["option"] in p.opts
                )
                if option.help is None:
                    raise ValueError(f"`dct {row['cmd']} {row['option']}` has no help")
                text = option.help
            else:
                text = " ".join(_sentences(paras[a])[b] for a, b in row["pick"])
            rows.append(f"| {self._wrap(f'`{form}`')} | {self._wrap(text)} |")
        return "\n".join(rows)

    def helper(self, name: str) -> str:
        """A Jinja query helper: signature, summary and the docs of the listed args."""
        import inspect

        from dbt_charts.core.compile.template import parameterized as p

        maker = {
            "filter": p.make_filter_helper,
            "filter_date_range": p.make_filter_date_range_helper,
        }
        fn = maker[name](_placeholder, None)
        sig = re.sub(r" -> .*", "", str(inspect.signature(fn)).replace("*, ", ""))
        doc = inspect.getdoc(fn) or ""
        summary = doc.splitlines()[0].rstrip(".")
        args = dict(re.findall(r"^\s{4}(\w+): (.+(?:\n\s{8}.+)*)", doc, re.M))
        extra = [
            f"`{a}`: {' '.join(args[a].split())}"
            for a in self.cfg["helpers"][name]
            if args[a]
        ]
        text = f"- `{name}{sig}`: {summary}." + ("".join(f" {e}" for e in extra))
        return self._wrap(text)

    def formats(self) -> str:
        """Each format name beside what the engine prints for a sample value."""
        from dbt_charts.core.compile.format_value import format_value

        out = []
        for group in self.cfg["formats"]:
            out.extend(
                f"`{name}` {format_value(group['sample'], name)}"
                for name in group["names"]
            )
        from dbt_charts.core.text.predefined_formats import PredefinedNumberFormat

        shown = {n for g in self.cfg["formats"] for n in g["names"]}
        rest = [m.value for m in PredefinedNumberFormat if m.value not in shown]
        return self._wrap(", ".join(out) + ". Also exist: " + _names(rest))

    def format_names(self, which: str) -> str:
        from dbt_charts.core.text import predefined_formats as pf

        names = {"number": pf.PredefinedNumberFormat, "time": pf.PredefinedTimeFormat}[
            which
        ]
        return self._wrap(", ".join(f"`{m.value}`" for m in names))

    def themes(self) -> str:
        """One line per built-in theme: canvas, palette and heading font, from its file."""
        from importlib_resources import files

        root = files("dbt_charts.core") / "defaults" / "themes"
        out = []
        for name in self.cfg["themes"]:
            data = yaml.safe_load((root / f"{name}.yaml").read_text())
            style = data["style"]
            canvas = _theme_value(root, name, ("style", "background"))
            palette = _theme_value(root, name, ("style", "palettes", "category"))
            heading = _theme_value(root, name, ("style", "fonts", "heading", "family"))
            kind = "dark" if _is_dark(canvas) else "light"
            out.append(
                f"- `{name}`: {kind} canvas "
                f"`{canvas}`, categorical `{palette}`, `fonts.heading.family` {heading}"
            )
            del style
        return self._wrap("\n".join(out))

    def palettes(self, family: str) -> str:
        """Palette names of one family; a `-dark` twin is folded into its base name."""
        from dbt_charts.core.compile.models.palette import build_palette_index
        from dbt_charts.core.compile.models.schema_names import _DEFAULTS_DIR

        index = build_palette_index(_DEFAULTS_DIR / "palettes")
        names = [n for n, e in index.items() if e["family"] == family]
        dark = [n for n in names if n.endswith("-dark")]
        bases = [n for n in names if n not in dark]
        text = _names(bases)
        if family == "categorical":
            text = ", ".join(
                f"`{n}` ({len(_palette(n)['colors'])})" for n in sorted(bases)
            )
        if dark:
            lone = [b for b in bases if f"{b}-dark" not in dark]
            if lone or len(dark) != len(bases):
                raise ValueError(
                    f"palettes({family}): not every palette has a -dark fork"
                )
            text += " (each also as `<name>-dark`)"
        return self._wrap(text)

    def aliases(self, scaffold: str) -> str:
        return self._wrap(", ".join(f"`{a}`" for a in _palette(scaffold)["aliases"]))

    def data_extensions(self) -> str:
        from dbt_charts.core.compile.file_refs import INLINE_FILE_SOURCE_SUFFIXES

        return self._wrap(", ".join(f"`{e}`" for e in INLINE_FILE_SOURCE_SUFFIXES))

    def source_types(self) -> str:
        from typing import get_args

        from dbt_charts.core.compile.models.source import SourceConfig

        wanted = self.cfg["source_types"]
        have = {
            t
            for m in get_args(SourceConfig)
            for t in get_args(m.model_fields["type"].annotation)
        }
        missing = [t for t in wanted if t not in have]
        if missing:
            raise KeyError(
                f"source_types: {missing} are not source types; have {sorted(have)}"
            )
        return self._wrap(", ".join(f"`{t}`" for t in wanted))


def _theme_value(root: Any, name: str, path: tuple[str, ...]) -> str:
    """A theme's value at `path`, following `extends` up the built-in chain."""
    data = yaml.safe_load((root / f"{name}.yaml").read_text())
    node: Any = data
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    if node is not None:
        return str(node)
    parent = data.get("extends")
    if parent is None:
        raise KeyError(f"theme {name}: no {'.'.join(path)} in its extends chain")
    return _theme_value(root, parent, path)


def _palette(name: str) -> dict[str, Any]:
    """A palette file with its `extends` parent's colors and aliases merged underneath."""
    from dbt_charts.core.compile.models.palette import build_palette_index
    from dbt_charts.core.compile.models.schema_names import _DEFAULTS_DIR

    entry = build_palette_index(_DEFAULTS_DIR / "palettes")[name]
    data = yaml.safe_load(entry["path"].read_text())
    if data.get("extends"):
        parent = _palette(data["extends"])
        data = {
            "colors": data.get("colors") or parent["colors"],
            "aliases": {**parent["aliases"], **data["aliases"]},
        }
    return data


def _is_dark(token: str) -> bool:
    """Whether a `scaffold.alias` canvas token resolves to a dark color."""
    family, alias = token.split(".")
    data = _palette(family)
    ref = data["aliases"][alias]
    hexcolor = str(data["colors"][ref - 1] if isinstance(ref, int) else ref).lstrip("#")
    r, g, b = (int(hexcolor[k : k + 2], 16) / 255 for k in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5


@functools.cache
def _cli_app() -> typer.Typer:
    from dbt_charts.cli.main import app

    return app


def _cli_commands() -> dict[str, _Command]:
    group = typer.main.get_command(_cli_app())
    assert isinstance(group, typer.core.TyperGroup)
    return dict(group.commands)


def _placeholder(*_: Any) -> str:
    return "?"


def _paragraphs(help_text: str) -> list[str]:
    """Help paragraphs, minus the `\\b` example blocks and the Examples list."""
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", help_text.strip())]
    return [p for p in paras if not p.startswith(("\x08", "Examples:"))]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z`-])", text.strip())
    return [p for p in parts if p]


def _form(cmd: _Command, row: dict[str, Any]) -> str:
    """`dct <cmd> <ARGS> --flag value` from the command's real arguments and options."""
    flags = row.get("flags", [])
    known = {o for p in cmd.params for o in getattr(p, "opts", [])}
    for flag in flags:
        if flag.split()[0] not in known:
            raise KeyError(
                f"commands: `dct {row['cmd']}` has no option {flag.split()[0]!r}"
            )
    args = [
        str(p.name).upper()
        for p in cmd.params
        if p.param_type_name == "argument" and p.name in row.get("args", [])
    ]
    missing = [a for a in row.get("args", []) if a.upper() not in args]
    if missing:
        raise KeyError(f"commands: `dct {row['cmd']}` has no argument {missing}")
    return " ".join(["dct", row["cmd"], *args, *flags])


def render_pages(
    *, mark: bool = False, names: tuple[str, ...] = PAGES, strict: bool = True
) -> dict[str, str]:
    gen = Generator(mark)
    env = Environment(
        loader=FileSystemLoader(SOURCE_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )
    env.globals.update(
        {
            n: getattr(gen, n)
            for n in dir(gen)
            if not n.startswith("_")
            and callable(getattr(gen, n))
            and n not in ("mark",)
        }
    )
    PROBLEMS.clear()
    pages = {p: env.get_template(f"{p}.md.j2").render() for p in names}
    if PROBLEMS and strict:
        raise ValueError("\n".join(dict.fromkeys(PROBLEMS)))
    return pages


def generated_share(pages: dict[str, str]) -> dict[str, tuple[int, int]]:
    """(generated bytes, total bytes) per page, from a `mark=True` render."""
    out = {}
    for name, text in pages.items():
        generated = sum(
            len(s.encode()) for s in re.findall(f"{_OPEN}(.*?){_CLOSE}", text, re.S)
        )
        clean = text.replace(_OPEN, "").replace(_CLOSE, "")
        out[name] = (generated, len(clean.encode()))
    return out


def main() -> None:
    for name, text in render_pages().items():
        path = SKILLS_DIR / name / "SKILL.md"
        path.write_text(text)
        print(f"wrote {path.relative_to(_DBT_CHARTS_DIR)} ({len(text.encode())} bytes)")


if __name__ == "__main__":
    sys.exit(main())
