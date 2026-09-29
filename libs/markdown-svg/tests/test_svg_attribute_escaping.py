"""Guard: every attribute-value or CSS-declaration-value interpolation under
``src/mdsvg/**`` routes through the one sanctioned escaper.

Mirrors dbt-charts' ``tests/test_svg_attribute_escaping.py`` (same AST-scan
shape, same stateful quote-tracking) for this package's own escaper,
``mdsvg.utils.escape_xml``, since this library cannot import ``dbt_charts``.
mdsvg's one dynamic attribute *name* (``_XhtmlSerializer.handle_starttag``'s
``{name}=``) is safe only because nh3 already validated it as a legal HTML
attribute name upstream, so there is no ``attr_name``-equivalent guard here.
It also emits CSS text into an SVG ``<style>`` block (``_style_rule_bodies``)
— that text is XML-parsed too, so an unescaped value there can close the
``<style>`` element early and inject markup; the same escaper closes that hole.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_SCAN_ROOT = Path(__file__).parent.parent / "src" / "mdsvg"

_ALLOWED_VALUE_CALLS = {"escape_xml", "escape_svg_text", "format_number"}

_NUMERIC_FORMAT_TYPES = set("dfFgGeEn") | {"%"}
_FORMAT_SPEC_BODY_RE = re.compile(r"^[<>=^]?[+\- ]?#?0?[0-9]*[,_]?(\.[0-9]+)?$")

# Functions whose *entire* return value is CSS text placed directly into an
# SVG <style> element's text content (never re-wrapped in an attribute value
# downstream, unlike an inline `style="..."` attribute — see the module
# docstring). Every interpolation anywhere in one of these functions must be
# escaped; add a function here when it grows a second one.
_STYLE_BLOCK_BODY_FUNCTIONS = {"_style_rule_bodies"}


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
    built from a dynamic (non-constant) piece."""
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
    (``d f F g G e E % n``) — a numeric type can never emit ``<>&"``, but
    ``:s``/``:>10`` (string type, or none at all) call ``str()``."""
    if not spec or spec[-1] not in _NUMERIC_FORMAT_TYPES:
        return False
    return bool(_FORMAT_SPEC_BODY_RE.fullmatch(spec[:-1]))


def _is_allowed_value(node: ast.expr, format_spec: ast.expr | None) -> bool:
    spec_text = _format_spec_text(format_spec)
    if spec_text is not None and _is_numeric_format_spec(spec_text):
        return True
    return _call_name(node) in _ALLOWED_VALUE_CALLS


def _scan_literal(literal: str, quote_state: str | None) -> str | None:
    """Advance *quote_state* (None, or the quote char of an attribute value
    currently open) across every character of *literal*. A still-open
    attribute survives the plain-text gap between two interpolations
    (``viewBox="0 0 {w} {h}"``), so state carries across the whole
    ``JoinedStr`` rather than looking at one adjacent fragment in isolation.
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


def _joined_str_violations(
    node: ast.JoinedStr, filename: str, *, style_block_body: bool
) -> list[str]:
    violations: list[str] = []
    parts = node.values
    quote_state: str | None = None
    for part in parts:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            quote_state = _scan_literal(part.value, quote_state)
            continue
        if not isinstance(part, ast.FormattedValue):
            continue
        line = part.lineno

        if quote_state == "'":
            violations.append(
                f'{filename}:{line}: single-quoted attribute value (use "...")'
            )
        elif quote_state == '"' and not _is_allowed_value(part.value, part.format_spec):
            violations.append(
                f"{filename}:{line}: attribute value not routed through "
                "escape_xml()/format_number()/a numeric format spec"
            )
        elif (
            quote_state is None
            and style_block_body
            and not _is_allowed_value(part.value, part.format_spec)
        ):
            violations.append(
                f"{filename}:{line}: <style>-block CSS value not routed "
                "through escape_xml()/format_number()/a numeric format spec"
            )

    return violations


def find_violations(source: str, filename: str) -> list[str]:
    """Return one message per bare attribute interpolation found in *source*."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    style_block_body_ids: set[int] = set()
    for func in ast.walk(tree):
        if (
            isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef))
            and func.name in _STYLE_BLOCK_BODY_FUNCTIONS
        ):
            style_block_body_ids.update(id(n) for n in ast.walk(func))

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            violations.extend(
                _joined_str_violations(
                    node, filename, style_block_body=id(node) in style_block_body_ids
                )
            )
    violations.extend(_sub_replacement_violations(tree, filename))
    return violations


_BAD_SAMPLES = {
    """x = f'fill="{color}"'""": "bare value",
    """x = f"fill='{color}'\"""": "single-quoted value",
    """x = f'viewBox="0 0 {w} {h}"'""": "second interpolation in one attribute",
    """x = f'fill="{color:s}"'""": "string format spec",
    """x = f'fill="{color:>10}"'""": "width-only format spec, no type",
    "def _style_rule_bodies(self):\n"
    '    return f"font-family: {self.style.font_family};"': "bare style-block CSS value",
    """x = pattern.sub(f'fill: {escape_xml(color)}', svg)""": (
        "an f-string .sub() replacement — re.sub processes its own backslash "
        "escapes regardless of prior XML escaping"
    ),
}

_GOOD_SAMPLES = [
    """x = f'fill="{escape_xml(color)}"'""",
    """x = f'width="{format_number(w)}"'""",
    """x = f'x="{v:.1f}"'""",
    """x = f'x="{v:,.0f}"'""",
    """x = f'viewBox="0 0 {format_number(w)} {format_number(h)}"'""",
    "def _style_rule_bodies(self):\n"
    '    return f"font-family: {escape_xml(self.style.font_family)};"',
    'def _other_function():\n    return f"font-family: {family};"',
    """x = pattern.sub(lambda m: f'fill: {escape_xml(color)}', svg)""",
    """x = pattern.sub(_replace, svg)""",
    """x = pattern.sub("", svg)""",
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
        rel = py_file.relative_to(_SCAN_ROOT.parent.parent).as_posix()
        source = py_file.read_text(encoding="utf-8", errors="replace")
        violations.extend(find_violations(source, rel))

    assert not violations, (
        "Bare SVG attribute interpolation(s) found — route every attribute "
        "value through escape_xml()/format_number() (mdsvg.utils):\n"
        + "\n".join(violations)
    )
