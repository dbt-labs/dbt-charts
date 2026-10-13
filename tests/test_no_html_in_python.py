"""Guard: no hand-authored HTML document/chrome markup in Python source files.

Detects HTML page-builder tags embedded in Python string literals within the
dbt charts core package (the ``core`` subpackage) — where the audit's
hand-authored page chrome (board wrapper, nav, serve prompt, variable controls)
lived.

Tags that are part of SVG geometry (<g>, <rect>, <path>, <circle>, etc.) are
excluded from detection since chart-rendering code legitimately emits SVG.

Files that have genuine Class-2 or Class-3 HTML reasons (algorithmic converters,
SVG-embedded style/script) are individually allowlisted below with a one-line
WHY comment.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from ._paths import DBT_CHARTS_DIR, DBT_CHARTS_PKG_DIR

# ── Scan scope ───────────────────────────────────────────────────────────────
# Paths are built from segments off the package accessor (never literal
# "dbt-charts/<subdir>/" strings — see test_no_literal_dbt_charts_subdir_paths.py).
_SCAN_ROOTS = (DBT_CHARTS_PKG_DIR / "core",)

# ── HTML tags to detect (document/chrome markup, not SVG geometry) ───────────
# Deliberately excluded from the tag set:
#   - SVG geometry: g, rect, text, line, path, circle, polyline, svg,
#     foreignObject, tspan, defs, clipPath, use, stop, linearGradient, pattern,
#     marker (chart geometry that legitimately appears in render code).
#   - `a`: a first-class SVG element too (SVG hyperlinks, e.g. a linked KPI),
#     so it can't distinguish HTML chrome from SVG anchors. Page chrome is never
#     a bare `<a>` anyway — the structural tags below catch it.
_TAG_GROUP = (
    r"html|head|body"
    r"|div|span|style|script"
    r"|button|input|label|form|fieldset"
    r"|nav|header|footer|section|main|article|aside"
    r"|ul|ol|li|table|thead|tbody|tr|td|th"
    r"|select|option|textarea"
    r"|p|h[1-6]|pre|img|embed|iframe|link"
)

# A bare tag mentioned in prose (`/data/<source>/<schema>/<table>/`, a security
# note that says "strips <script>") is NOT markup. We only flag a literal when a
# tag is used as *constructed markup*, evidenced by one of:
#   - a full-document marker (`<!DOCTYPE`, `<html`, `<head`, `<body`),
#   - an opening tag carrying an attribute (`<div class=`, `<input type=`),
#   - a self-closing opening tag (`<br/>`, `<input .../>`),
#   - a closing tag (`</div>`, `</style>`).
_DOC_MARKER = re.compile(r"<(?:!DOCTYPE|html\b|head\b|body\b)", re.IGNORECASE)
_OPEN_WITH_ATTR = re.compile(rf"<(?:{_TAG_GROUP})\b[^>]*\s[\w-]+\s*=", re.IGNORECASE)
_SELF_CLOSING = re.compile(rf"<(?:{_TAG_GROUP})\b[^>]*/>", re.IGNORECASE)
_CLOSING = re.compile(rf"</(?:{_TAG_GROUP})\s*>", re.IGNORECASE)
_MARKUP_PATTERNS = (_DOC_MARKER, _OPEN_WITH_ATTR, _SELF_CLOSING, _CLOSING)

# ── Per-file allowlist ────────────────────────────────────────────────────────
# Keyed by resolved Path (built from segments) → mandatory WHY string documenting
# the legitimate Class-2/3 reason.
_RENDER = DBT_CHARTS_PKG_DIR / "core" / "render"
_ALLOWLIST: dict[Path, str] = {
    # WHY: embed_svg_script() wraps JS in <script> for inline SVG execution —
    # this is SVG scripting, not page-chrome HTML.
    (_RENDER / "script_embedding.py").resolve(): (
        "SVG script embedding (<script> in SVG context)"
    ),
    # WHY: font_selection reads finished markup to decide which boards an export
    # carries, so its <style> pattern matches markup rather than authoring any —
    # the CSS-rule scan is scoped to style blocks because an unscoped one walked
    # the whole document from every "." in a table of decimals.
    (_RENDER / "font_selection.py").resolve(): (
        "reads emitted markup (a <style> matcher, not authored HTML)"
    ),
    # WHY: _dedupe_repeated_style_rules matches an SVG <style> block mdsvg already
    # emitted and re-wraps a subset of its own lines in the same literal tags — it
    # never composes new markup, only drops already-rendered CSS lines. Same SVG
    # <style> category as font_selection.py above, applied post-render.
    (_RENDER / "boards.py").resolve(): (
        "SVG <style> element dedup (reads + re-wraps already-rendered CSS, not authored HTML)"
    ),
}

# ── String-literal extractor ─────────────────────────────────────────────────


def _string_literals(source: str) -> list[str]:
    """Return all string literal values in a Python source file.

    Skips comments (the AST drops them) and module/class/function docstrings —
    documentation legitimately *names* tags in prose (URL grammars like
    ``/data/<source>/<table>/``, security notes), but is never the emitted
    markup. f-string template parts (``JoinedStr``) are included, since that is
    where hand-authored ``<tag …>`` builders live.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstring_ids.add(id(first.value))

    literals: list[str] = []
    for node in ast.walk(tree):
        # Normal string constants (skip docstrings)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstring_ids:
                literals.append(node.value)
        # f-string: walk parts for literal string segments
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    literals.append(part.value)
    return literals


# ── Self-check detector ───────────────────────────────────────────────────────


def _is_markup(text: str) -> bool:
    """Return True if *text* looks like constructed HTML markup (not prose)."""
    return any(pat.search(text) for pat in _MARKUP_PATTERNS)


def _has_html_tag(source: str) -> bool:
    """Return True if any string literal in *source* contains HTML markup."""
    return any(_is_markup(lit) for lit in _string_literals(source))


def test_detector_fires_on_known_bad_pattern() -> None:
    """Self-check: the detector must catch known offending patterns.

    If this fails, the guard is vacuously green and provides no protection.
    """
    offending_samples = [
        'result = f"""<div class="container">{content}</div>"""',
        'html = "<span class=\\"foo\\">label</span>"',
        'return f"<h1>Error: {msg}</h1>"',
        'content = "<button onclick=\\"save()\\">Save</button>"',
        'page = "<html><body><p>Hello</p></body></html>"',
        'output = \'<input type="text" name="q"/>\'',
        "block = '<style>.foo { color: red; }</style>'",
        "js_block = '<script>alert(1)</script>'",
    ]
    for sample in offending_samples:
        assert _has_html_tag(sample), (
            f"Detector did not fire on known offender: {sample!r}"
        )


def test_detector_ignores_svg_geometry() -> None:
    """Self-check: SVG primitives must NOT trigger the guard."""
    svg_samples = [
        'return f"<g transform=\\"translate({x},{y})\\">"',
        'elem = f"<rect x=\\"{x}\\" y=\\"{y}\\" width=\\"{w}\\" height=\\"{h}\\"/>"',
        'path = f"<path d=\\"{d}\\"/>"',
        'circle = "<circle cx=\\"50\\" cy=\\"50\\" r=\\"25\\"/>"',
        'line = f"<line x1=\\"{x1}\\" y1=\\"{y1}\\" x2=\\"{x2}\\" y2=\\"{y2}\\"/>"',
        'elem = "<polyline points=\\"0,0 10,10\\"/>"',
        'svg = "<svg xmlns=\\"http://www.w3.org/2000/svg\\">"',
        'fo = "<foreignObject x=\\"0\\" y=\\"0\\"/>"',
        'ts = "<tspan dy=\\"1.2em\\">"',
        'defs = "<defs><clipPath id=\\"c\\"><rect/></clipPath></defs>"',
        'stop = "<stop offset=\\"0%\\" stop-color=\\"{color}\\"/>"',
        'lg = "<linearGradient id=\\"g\\">"',
        'use = "<use href=\\"#icon\\"/>"',
        'marker = "<marker id=\\"m\\">"',
        # SVG hyperlink — <a> is a first-class SVG element, must not fire.
        'anchor = f"<a href=\\"{href}\\"><text>{v}</text></a>"',
    ]
    for sample in svg_samples:
        assert not _has_html_tag(sample), (
            f"Detector incorrectly fired on SVG primitive: {sample!r}"
        )


def test_detector_ignores_prose_tag_mentions() -> None:
    """Self-check: tag names inside docstrings/descriptions are not markup.

    A bare tag in prose (a URL grammar, a security note) must not trip the
    guard — only constructed markup (attribute, self-close, or closing tag) does.
    """
    prose_samples = [
        '"""Canonical data URL: /data/<source>/<schema>/<table>/."""',
        'd = "PRAGMA table_info(<table>) lists columns"',
        'note = "mdsvg strips <script>/event-handlers as a best-effort guard"',
        'ref = "the dot-joined <source>.<schema>.<table> location"',
        's = "wrap the body in a <div> by default"',  # bare mention, no attr/close
    ]
    for sample in prose_samples:
        assert not _has_html_tag(sample), (
            f"Detector fired on a prose tag mention: {sample!r}"
        )


# ── Main guard ────────────────────────────────────────────────────────────────


def test_no_hand_authored_html_in_python_source() -> None:
    """Fail if any non-allowlisted Python file contains HTML doc/chrome markup.

    This guards against regressions where someone adds a new inline HTML
    page-builder f-string to the dbt charts core Python source.
    """
    # Guard against the vacuous-green failure mode: if the scan roots don't
    # resolve, the loop below scans nothing and the test passes for free.
    assert any(r.exists() for r in _SCAN_ROOTS), (
        f"scan roots do not exist — accessors are wrong: {_SCAN_ROOTS}"
    )

    violations: list[str] = []

    for scan_root in _SCAN_ROOTS:
        if not scan_root.exists():
            continue
        for py_file in sorted(scan_root.rglob("*.py")):
            if py_file.resolve() in _ALLOWLIST:
                continue
            rel = py_file.relative_to(DBT_CHARTS_DIR).as_posix()
            source = py_file.read_text(encoding="utf-8", errors="replace")
            if _has_html_tag(source):
                # Collect the offending literal for the error message
                offenders = [
                    lit[:120] + ("…" if len(lit) > 120 else "")
                    for lit in _string_literals(source)
                    if _is_markup(lit)
                ]
                violations.append(
                    f"\n  {rel}:\n"
                    + "\n".join(f"    string: {o!r}" for o in offenders[:3])
                )

    assert not violations, (
        "Hand-authored HTML found in Python source files. "
        "Move the markup to a Jinja template, or add the file to "
        "_ALLOWLISTED_FILES with a WHY comment if it's a legitimate Class-2/3 use."
        + "".join(violations)
    )
