"""Guard: every attribute-value/attribute-name interpolation under
``core/render/**`` routes through the one sanctioned escaper.

Mirrors ``test_no_html_in_python.py``'s AST-scan shape. An f-string's
``JoinedStr.values`` alternates literal ``Constant`` fragments and
``FormattedValue`` interpolations; a literal fragment ending in ``="``
(or ``='``) immediately before an interpolation puts that interpolation in
*attribute-value* position, and a literal fragment starting with ``="``
(or ``='``) immediately after puts it in *attribute-name* position. Text and
child-markup positions (``>{x}<``) share one AST shape, so this guard cannot
check them.
"""

from __future__ import annotations

import ast
import re

from ._paths import DBT_CHARTS_DIR, DBT_CHARTS_PKG_DIR

_SCAN_ROOT = DBT_CHARTS_PKG_DIR / "core" / "render"

_ALLOWED_VALUE_CALLS = {"escape_attr", "format_svg_numeric", "px"}
_ALLOWED_NAME_CALLS = {"attr_name"}

_NUMERIC_FORMAT_TYPES = set("dfFgGeEn") | {"%"}
_FORMAT_SPEC_BODY_RE = re.compile(r"^[<>=^]?[+\- ]?#?0?[0-9]*[,_]?(\.[0-9]+)?$")


def _call_name(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _format_spec_text(format_spec: ast.expr | None) -> str | None:
    """The literal text of a ``JoinedStr`` format spec, or None if absent or
    built from a dynamic (non-constant) piece — e.g. ``{x:{width}.2f}``."""
    if not isinstance(format_spec, ast.JoinedStr):
        return None
    parts: list[str] = []
    for part in format_spec.values:
        if not (isinstance(part, ast.Constant) and isinstance(part.value, str)):
            return None
        parts.append(part.value)
    return "".join(parts)


def _is_numeric_format_spec(spec: str) -> bool:
    """True for a format spec ending in a numeric presentation type
    (``d f F g G e E % n``), so ``:.1f``/``:,.0f`` pass but ``:s``/``:>10``
    (string type, or none at all) don't — a numeric type can never emit
    ``<>&"``, but ``:s`` calls ``str()`` on anything."""
    if not spec or spec[-1] not in _NUMERIC_FORMAT_TYPES:
        return False
    return bool(_FORMAT_SPEC_BODY_RE.fullmatch(spec[:-1]))


def _is_literal_constant(node: ast.expr) -> bool:
    """True when *node* can only ever evaluate to one of a fixed set of
    string literals baked into this source file (e.g. ``"true" if cond else
    "false"``) — no user data can reach it, so it can never carry a
    markup-breaking character regardless of whether it is escaped."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.IfExp):
        return _is_literal_constant(node.body) and _is_literal_constant(node.orelse)
    return False


def _is_allowed_value(node: ast.expr, format_spec: ast.expr | None) -> bool:
    spec_text = _format_spec_text(format_spec)
    if spec_text is not None and _is_numeric_format_spec(spec_text):
        return True
    if _is_literal_constant(node):
        return True
    return _call_name(node) in _ALLOWED_VALUE_CALLS


def _is_allowed_name(node: ast.expr) -> bool:
    return _call_name(node) in _ALLOWED_NAME_CALLS


def _regex_compile_args(tree: ast.AST) -> set[int]:
    """id() of every ``JoinedStr`` passed directly to ``re.compile(...)`` —
    a regex *pattern*, never markup, so the attribute-escaping check doesn't
    apply to it (unlike a ``.sub()`` replacement, which builds real output
    and stays covered)."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "compile"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
        ):
            ids.update(id(arg) for arg in node.args if isinstance(arg, ast.JoinedStr))
    return ids


def _scan_literal(literal: str, quote_state: str | None) -> str | None:
    """Advance *quote_state* (None, or the quote char of an attribute value
    currently open) across every character of *literal*.

    A single ``before``-fragment check (endswith ``="``) only catches an
    interpolation that is the *first* thing inside its attribute value. A
    still-open attribute survives across the plain-text gap between two
    interpolations (``viewBox="0 0 {width} {height}"`` — the space before
    ``{height}`` closes nothing), so the scan carries state across the whole
    ``JoinedStr`` instead of looking at one fragment in isolation.
    """
    i, n = 0, len(literal)
    while i < n:
        ch = literal[i]
        if quote_state is None and ch == "=" and i + 1 < n and literal[i + 1] in "\"'":
            quote_state = literal[i + 1]
            i += 2
            continue
        if quote_state is not None and ch == quote_state:
            quote_state = None
        i += 1
    return quote_state


def _is_safe_sub_replacement(node: ast.expr) -> bool:
    """A callable (never processes backslash escapes) or a plain string
    literal (author-controlled, no interpolation) — anything else, including
    an f-string built from a runtime value, goes through re.sub's own
    backslash-escape processing regardless of prior XML escaping."""
    if isinstance(node, ast.Lambda):
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if _call_name(node) == "partial":
        return True
    # A bare name/attribute is assumed to reference a function (this repo's
    # convention for a `.sub(_replace, ...)` callback), not a pre-built string.
    return isinstance(node, (ast.Name, ast.Attribute))


def _sub_replacement_violations(tree: ast.AST, filename: str) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("sub", "subn")
        ):
            continue
        is_module_re_call = (
            isinstance(node.func.value, ast.Name) and node.func.value.id == "re"
        )
        repl_index = 1 if is_module_re_call else 0
        repl = next((kw.value for kw in node.keywords if kw.arg == "repl"), None)
        if repl is None and len(node.args) > repl_index:
            repl = node.args[repl_index]
        if repl is not None and not _is_safe_sub_replacement(repl):
            violations.append(
                f"{filename}:{node.lineno}: re.sub()/.sub() replacement must be "
                "a callable (lambda) or a plain string literal — the string-"
                "replacement form processes backslash escapes in it"
            )
    return violations


def find_violations(source: str, filename: str) -> list[str]:
    """Return one message per bare attribute interpolation found in *source*."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    violations: list[str] = []
    regex_pattern_ids = _regex_compile_args(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        if id(node) in regex_pattern_ids:
            continue
        parts = node.values
        quote_state: str | None = None
        for i, part in enumerate(parts):
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                quote_state = _scan_literal(part.value, quote_state)
                continue
            if not isinstance(part, ast.FormattedValue):
                continue
            line = part.lineno
            after = parts[i + 1] if i + 1 < len(parts) else None

            if quote_state == "'":
                violations.append(
                    f'{filename}:{line}: single-quoted attribute value (use "...")'
                )
            elif quote_state == '"' and not _is_allowed_value(
                part.value, part.format_spec
            ):
                violations.append(
                    f"{filename}:{line}: attribute value not routed through "
                    "escape_attr()/format_svg_numeric()/px()/a numeric format spec"
                )

            if (
                isinstance(after, ast.Constant)
                and isinstance(after.value, str)
                and (after.value.startswith('="') or after.value.startswith("='"))
                and not _is_allowed_name(part.value)
            ):
                violations.append(
                    f"{filename}:{line}: attribute name not routed through attr_name()"
                )

    for call_node in ast.walk(tree):
        if isinstance(call_node, ast.Call) and isinstance(
            call_node.func, ast.Attribute
        ):
            if (
                call_node.func.attr == "format"
                and isinstance(call_node.func.value, ast.Constant)
                and isinstance(call_node.func.value.value, str)
                and "<" in call_node.func.value.value
            ):
                violations.append(
                    f"{filename}:{call_node.lineno}: markup built with .format()"
                )
        if isinstance(call_node, ast.BinOp) and isinstance(call_node.op, ast.Mod):
            left = call_node.left
            if (
                isinstance(left, ast.Constant)
                and isinstance(left.value, str)
                and "<" in left.value
            ):
                violations.append(
                    f"{filename}:{call_node.lineno}: markup built with % formatting"
                )

    violations.extend(_sub_replacement_violations(tree, filename))

    return violations


_BAD_SAMPLES = {
    """x = f'font-family="{family}"'""": "bare value",
    """x = f"font-family='{family}'\"""": "single-quoted value",
    """x = f'data-var-{v}="true"'""": "bare name",
    """x = f'id="chart-{cid}"'""": "prefixed value",
    """x = f'viewBox="0 0 {w} {h}"'""": "second interpolation in one attribute",
    """x = f'fill="{tone_color:s}"'""": "string format spec",
    """x = f'fill="{tone_color:>10}"'""": "width-only format spec, no type",
    """x = pattern.sub(lambda m: f'id="{m.group(1)}"', svg)""": (
        "a .sub() replacement builds real output, not a pattern — stays covered"
    ),
    """x = pattern.sub(f'fill: {escape_attr(color)}', svg)""": (
        "an f-string .sub() replacement — re.sub processes its own backslash "
        "escapes regardless of prior XML escaping"
    ),
    """x = re.sub(r'<a', f'<a href="{escape_attr(url)}"', svg)""": (
        "same bug via the module-level re.sub(pattern, repl, string) form"
    ),
}

_GOOD_SAMPLES = [
    """x = re.compile(f'id="({FAMILY})"')""",
    """x = f'font-family="{escape_attr(family)}"'""",
    """x = f'width="{px(w)}"'""",
    """x = f'value="{format_svg_numeric(v)}"'""",
    """x = f'x="{v:.1f}"'""",
    """x = f'data-var-{attr_name(v)}="true"'""",
    """x = f'viewBox="0 0 {escape_attr(w)} {escape_attr(h)}"'""",
    """x = f'x="{v:,.0f}"'""",
    """x = f'x="{v:g}"'""",
    """x = f'x="{v:%}"'""",
    """x = f'data-dbt-checked="{"true" if checked else "false"}"'""",
    """x = pattern.sub(lambda m: f'fill: {escape_attr(color)}', svg)""",
    """x = pattern.sub(_replace, svg)""",
    """x = pattern.sub("", svg)""",
    """x = re.sub(r'<a', _replace, svg)""",
]


def test_detector_fires_on_known_bad_patterns() -> None:
    for sample, why in _BAD_SAMPLES.items():
        assert find_violations(sample, "sample.py"), (
            f"detector missed a known offender ({why}): {sample!r}"
        )


def test_detector_accepts_sanctioned_escapers() -> None:
    for sample in _GOOD_SAMPLES:
        assert not find_violations(sample, "sample.py"), (
            f"detector false-positived on a sanctioned escaper: {sample!r}"
        )


def test_no_bare_svg_attribute_interpolations() -> None:
    assert _SCAN_ROOT.exists(), f"scan root does not exist: {_SCAN_ROOT}"

    violations: list[str] = []
    for py_file in sorted(_SCAN_ROOT.rglob("*.py")):
        rel = py_file.relative_to(DBT_CHARTS_DIR).as_posix()
        source = py_file.read_text(encoding="utf-8", errors="replace")
        violations.extend(find_violations(source, rel))

    assert not violations, (
        "Bare SVG attribute interpolation(s) found — route every attribute "
        "value through escape_attr()/format_svg_numeric()/px(), and every "
        "attribute name through attr_name() (both in "
        "dbt_charts.core.render.svg_utils):\n" + "\n".join(violations)
    )
