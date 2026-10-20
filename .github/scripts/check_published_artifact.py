#!/usr/bin/env python3
"""Audit a built dbt-charts wheel against the packaging boundary policy.

Re-derives the boundary truth from the built artifact itself, independent of
whatever produced it, so a same-commit relaxation of the boundary policy or
the packaging config still fails before the artifact reaches PyPI.

Finding categories:
  out_of_bounds_file    — wheel entry outside [tool.dbt_charts_boundary].allowed_roots
  name_substring        — content contains a forbidden_name_substrings value
  content_regex         — content matches a forbidden_content_regexes pattern
                          (personal names, internal decision-doc references,
                          internal repo paths)
  token_prefix          — content contains a forbidden_token_prefixes value
  forbidden_module_path — wheel RECORD entry path contains a forbidden_module_paths
                          substring (proprietary module gate)
  dep_missing           — package imports something not declared as a runtime dep
  dep_unused            — declared runtime dep never imported
  dev_context_leaked    — wheel ships an AGENTS.md or CLAUDE.md file
  native_extension      — policy declares a compiled extension the wheel lacks, the
                          wheel is not a platform wheel, or (release only) the
                          extension embeds build-machine paths

Stdlib-only, so it runs standalone in a release checkout. A wrapper in the
development repo imports these checks and adds its workspace-only ones.

Exits 0 on clean, 1 on any finding, 2 on configuration error (missing policy file).
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STDLIB = set(sys.stdlib_module_names)

# Known PyPI distribution → canonical Python top-level import name(s). A
# stdlib-only audit cannot introspect installed packages, so deps that don't
# follow the "lowercase + s/-/_/g" mapping need a manual entry. Keys are
# already canonicalized (lowercase, hyphens-as-underscores) so callers can
# look up by canonical form directly.
_PYPI_TO_IMPORT_NAMES: dict[str, list[str]] = {
    "pyyaml": ["yaml"],
    "vl_convert_python": ["vl_convert"],
    "dbt_adapters": ["dbt"],
    "dbt_duckdb": ["dbt"],
    "dbt_core": ["dbt"],
    "dbt_protos": ["dbtlabs"],
    "beautifulsoup4": ["bs4"],
    # pydantic v2 ships a compiled `pydantic_core` companion package alongside
    # the pure-Python `pydantic`. Importing `pydantic_core` is satisfied by
    # declaring `pydantic`; treat them as one PyPI distribution.
    "pydantic": ["pydantic", "pydantic_core"],
    "python_dotenv": ["dotenv"],
    # Pillow ships its module under the `PIL` import name for historical
    # compatibility with the original Python Imaging Library.
    "pillow": ["PIL"],
}


def _canonicalize(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


@dataclass(frozen=True)
class Policy:
    allowed_roots: list[str]
    forbidden_name_substrings: list[str]
    forbidden_token_prefixes: list[str]
    # Path substrings that must not appear as wheel RECORD entry paths.
    # Used to assert proprietary module paths are absent from the OSS wheel.
    # Each entry is matched as a substring of the wheel member path.
    forbidden_module_paths: list[str]
    dependencies: list[str]
    optional_dependencies: list[str]
    # Import names the wheel may reference without a declared runtime dep —
    # typically lazy imports behind a feature gate (`google.cloud.bigquery`).
    allowed_external_imports: list[str]
    # Declared runtime deps that the wheel doesn't import directly but that
    # another declared dep loads at runtime. The AST scan only sees direct
    # imports, so these would otherwise fire `dep_unused`.
    transitively_required_dependencies: list[str]
    # Regex patterns that must not match any decodable wheel file content.
    forbidden_content_regexes: list[str]
    # Wheel path stem of the compiled extension the wheel must carry (e.g.
    # `dbt_charts/_engine`). None for a pure-Python wheel.
    native_extension: str | None

    @property
    def allowed_top_level(self) -> set[str]:
        return {Path(r).name for r in self.allowed_roots}


@dataclass(frozen=True)
class Finding:
    file: str
    category: str
    detail: str


def _strip_dep_marker(spec: str) -> str:
    """Reduce a PEP 508 dep spec to its distribution name."""
    name = spec.split(";", 1)[0].strip()
    for sep in ("[", "<", ">", "=", "!", "~", " "):
        if sep in name:
            name = name.split(sep, 1)[0].strip()
    return name


def _load_policy(path: Path) -> Policy:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    boundary = data.get("tool", {}).get("dbt_charts_boundary", {})
    project = data.get("project", {})
    if "allowed_roots" not in boundary:
        print(
            f"BLOCKER: {path} missing [tool.dbt_charts_boundary] allowed_roots",
            file=sys.stderr,
        )
        raise SystemExit(2)
    optional: list[str] = []
    for extra_deps in project.get("optional-dependencies", {}).values():
        optional.extend(_strip_dep_marker(d) for d in extra_deps)
    return Policy(
        allowed_roots=list(boundary["allowed_roots"]),
        forbidden_name_substrings=list(boundary.get("forbidden_name_substrings", [])),
        forbidden_token_prefixes=list(boundary.get("forbidden_token_prefixes", [])),
        forbidden_module_paths=list(boundary.get("forbidden_module_paths", [])),
        dependencies=[_strip_dep_marker(d) for d in project.get("dependencies", [])],
        optional_dependencies=optional,
        allowed_external_imports=list(boundary.get("allowed_external_imports", [])),
        transitively_required_dependencies=list(
            boundary.get("transitively_required_dependencies", [])
        ),
        forbidden_content_regexes=list(boundary.get("forbidden_content_regexes", [])),
        native_extension=boundary.get("native_extension"),
    )


def _decode(blob: bytes) -> str | None:
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _walk_wheel(path: Path) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(path) as zf:
        return [
            (info.filename, zf.read(info))
            for info in zf.infolist()
            if not info.is_dir()
        ]


def _is_metadata_entry(member: str) -> bool:
    """Wheel entries always permitted regardless of allowlist.

    Hatchling's PEP 639 license placement is `<dist>.dist-info/licenses/
    LICENSE`, already covered by the `.dist-info` arm below -- if the build
    backend or its version ever emits a bare `LICENSE` at the wheel root
    again, add `or member == "LICENSE"` back here.
    """
    first = member.split("/", 1)[0]
    return first.endswith(".dist-info")


def _collect_imports(content: str) -> set[str]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".", 1)[0])
    return names


# ---- Per-category checks --------------------------------------------------


def _check_allowlist(members: list[str], policy: Policy) -> list[Finding]:
    allowed = policy.allowed_top_level
    findings: list[Finding] = []
    for m in members:
        if _is_metadata_entry(m):
            continue
        first = m.split("/", 1)[0]
        if first in allowed:
            continue
        findings.append(
            Finding(
                m, "out_of_bounds_file", f"top-level `{first}` not in allowed_roots"
            )
        )
    return findings


# Home directories, the cargo registry and Windows user profiles: what rustc
# embeds in panic locations unless the build remaps them.
_BUILD_PATH = re.compile(
    rb"(?:/home|/Users)/[^/\x00]+/|/root/\.cargo|[A-Za-z]:\\Users\\|\.cargo[/\\]registry"
)


def _check_native_extension(
    wheel: list[tuple[str, bytes]], policy: Policy
) -> list[Finding]:
    """Assert the wheel is a platform wheel carrying exactly one compiled extension.

    The extension is binary, so `_check_content_scans` skips it; its raw bytes
    are scanned for forbidden name substrings here instead.
    """
    if policy.native_extension is None:
        return []
    stem = policy.native_extension
    extensions = [
        (m, b)
        for m, b in wheel
        if m.startswith(f"{stem}.") and m.endswith((".so", ".pyd"))
    ]
    if len(extensions) != 1:
        return [
            Finding(
                stem,
                "native_extension",
                f"expected exactly one `{stem}` extension, found {len(extensions)}",
            )
        ]
    findings: list[Finding] = []
    member, blob = extensions[0]
    wheel_file = next(b for m, b in wheel if m.endswith(".dist-info/WHEEL"))
    if b"Root-Is-Purelib: false" not in wheel_file:
        findings.append(
            Finding(
                member,
                "native_extension",
                "wheel is tagged pure-Python; expected `Root-Is-Purelib: false`",
            )
        )
    for sub in policy.forbidden_name_substrings:
        if sub.encode() in blob:
            findings.append(
                Finding(
                    member, "name_substring", f"contains forbidden substring `{sub}`"
                )
            )
    return findings


def _check_build_paths(wheel: list[tuple[str, bytes]], policy: Policy) -> list[Finding]:
    """Reject a compiled extension that embeds build-machine paths.

    Release-only: development CI builds without --remap-path-prefix.
    """
    if policy.native_extension is None:
        return []
    stem = policy.native_extension
    findings: list[Finding] = []
    for member, blob in wheel:
        if not (member.startswith(f"{stem}.") and member.endswith((".so", ".pyd"))):
            continue
        build_path = _BUILD_PATH.search(blob)
        if build_path:
            findings.append(
                Finding(
                    member,
                    "native_extension",
                    f"embeds a build-machine path `{build_path.group().decode()}`; "
                    "build with --remap-path-prefix",
                )
            )
    return findings


def _check_dev_context_leaked(members: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for m in members:
        name = m.rsplit("/", 1)[-1]
        if name in ("AGENTS.md", "CLAUDE.md"):
            findings.append(
                Finding(
                    m, "dev_context_leaked", f"developer-context file `{name}` in wheel"
                )
            )
    return findings


def _check_content_scans(
    members: list[tuple[str, bytes]],
    policy: Policy,
) -> list[Finding]:
    findings: list[Finding] = []
    for member, blob in members:
        # `agent_api/search.py` runtime-scrubs `see ai_notes/...` references
        # from board descriptions — its regex carries the literal substring.
        if member == "dbt_charts/agent_api/search.py":
            continue
        text = _decode(blob)
        if text is None:
            continue
        for sub in policy.forbidden_name_substrings:
            if sub in text:
                findings.append(
                    Finding(
                        member,
                        "name_substring",
                        f"contains forbidden substring `{sub}`",
                    )
                )
        for prefix in policy.forbidden_token_prefixes:
            if re.search(_token_prefix_pattern(prefix), text):
                findings.append(
                    Finding(
                        member,
                        "token_prefix",
                        f"contains forbidden token prefix `{prefix}`",
                    )
                )
    return findings


def _check_content_regexes(
    members: list[tuple[str, bytes]],
    policy: Policy,
) -> list[Finding]:
    if not policy.forbidden_content_regexes:
        return []
    patterns = [(raw, re.compile(raw)) for raw in policy.forbidden_content_regexes]
    findings: list[Finding] = []
    for member, blob in members:
        # Same self-scan carve-out as _check_content_scans.
        if member == "dbt_charts/agent_api/search.py":
            continue
        text = _decode(blob)
        if text is None:
            continue
        for raw, pat in patterns:
            if pat.search(text):
                findings.append(
                    Finding(
                        member,
                        "content_regex",
                        f"matches forbidden content pattern `{raw}`",
                    )
                )
    return findings


def _token_prefix_pattern(prefix: str) -> str:
    """Build the token-prefix scan regex.

    Whitespace inside the prefix means it's a literal marker (e.g.
    `BEGIN PRIVATE KEY` for PEM headers) — match it as a substring at a
    word boundary. Whitespace-free prefixes (`sk-`, `ghp_`, `AKIA`, …) are
    credential shapes — require a 16+ alphanumeric/symbol suffix.

    Suffix-length floor of 16 is the smallest real-shape credential we
    care about: AWS Access Key IDs are exactly 20 chars total (`AKIA` + 16
    char suffix); OpenAI keys are 48+; GitHub PATs are 36+; Anthropic keys
    are 95+. 16 still rejects every realistic docstring placeholder
    (`sk-...`, `sk-test`, `sk-foo` — all ≤ 4 chars after the prefix) and
    short hyphenated identifiers.
    """
    escaped = re.escape(prefix)
    if any(c.isspace() for c in prefix):
        return r"\b" + escaped
    return r"\b" + escaped + r"[A-Za-z0-9_/+\-]{16,}\b"


def _check_forbidden_module_paths(members: list[str], policy: Policy) -> list[Finding]:
    """Assert wheel RECORD entry paths do not contain forbidden module path substrings.

    Fails with ``forbidden_module_path`` when any wheel member path contains
    a listed substring — the category name is distinct from ``name_substring``
    (content scan) so callers can filter by category.
    """
    findings: list[Finding] = []
    for m in members:
        if _is_metadata_entry(m):
            continue
        for sub in policy.forbidden_module_paths:
            if sub in m:
                findings.append(
                    Finding(
                        m,
                        "forbidden_module_path",
                        f"wheel member path contains forbidden module path `{sub}`",
                    )
                )
    return findings


def _imports_for_dep(canonical_dep: str) -> set[str]:
    """Map a canonical PyPI name to its canonical Python import names."""
    mapped = _PYPI_TO_IMPORT_NAMES.get(canonical_dep)
    if mapped is not None:
        return {_canonicalize(m) for m in mapped}
    return {canonical_dep}


def _check_dep_parity(
    declared: list[str],
    optional: list[str],
    imported: set[str],
    in_package: set[str],
    allowed_external: list[str],
    transitively_required: list[str],
) -> list[Finding]:
    canonical_declared = [_canonicalize(d) for d in declared]
    canonical_optional = [_canonicalize(d) for d in optional]
    canonical_imported = {_canonicalize(i) for i in imported}
    canonical_in_package = {_canonicalize(n) for n in in_package}
    canonical_stdlib = {_canonicalize(s) for s in _STDLIB}
    canonical_allowed_external = {_canonicalize(n) for n in allowed_external}
    canonical_transitive = {_canonicalize(d) for d in transitively_required}

    # An import is satisfied by either a runtime or an optional dep. Optional
    # deps are allowed to be "unused" by the base install — they're only
    # imported under their corresponding extra — so they participate in the
    # `dep_missing` direction only.
    satisfied: set[str] = set()
    for d in canonical_declared + canonical_optional:
        satisfied |= _imports_for_dep(d)

    findings: list[Finding] = []
    missing = (
        canonical_imported
        - satisfied
        - canonical_stdlib
        - canonical_in_package
        - canonical_allowed_external
    )
    for name in sorted(missing):
        findings.append(
            Finding(
                "<wheel>",
                "dep_missing",
                f"imports `{name}` but no matching runtime dep is declared",
            )
        )
    for original, canon in zip(declared, canonical_declared, strict=True):
        if canon in canonical_in_package or canon in canonical_transitive:
            continue
        if not (_imports_for_dep(canon) & canonical_imported):
            findings.append(
                Finding(
                    "<pyproject.toml>",
                    "dep_unused",
                    f"declared dep `{original}` is never imported",
                )
            )
    return findings


def _audit(*, wheel: list[tuple[str, bytes]], policy: Policy) -> list[Finding]:
    wheel_names = [m for m, _ in wheel]
    wheel_members = [(m, b) for m, b in wheel if not _is_metadata_entry(m)]

    findings: list[Finding] = []
    findings += _check_allowlist(wheel_names, policy)
    findings += _check_dev_context_leaked(wheel_names)
    findings += _check_native_extension(wheel, policy)
    findings += _check_forbidden_module_paths(wheel_names, policy)
    findings += _check_content_scans(wheel_members, policy)
    findings += _check_content_regexes(wheel_members, policy)

    in_package = policy.allowed_top_level
    imports: set[str] = set()
    for m, blob in wheel:
        if _is_metadata_entry(m):
            continue
        if not m.endswith(".py"):
            continue
        text = _decode(blob)
        if text is not None:
            imports |= _collect_imports(text)
    findings += _check_dep_parity(
        policy.dependencies,
        policy.optional_dependencies,
        imports,
        in_package,
        policy.allowed_external_imports,
        policy.transitively_required_dependencies,
    )

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=_REPO_ROOT / "pyproject.toml",
    )
    args = parser.parse_args()

    if not args.pyproject.exists():
        print(f"BLOCKER: pyproject not found: {args.pyproject}", file=sys.stderr)
        return 2
    if not args.wheel.exists():
        print(f"BLOCKER: wheel not found: {args.wheel}", file=sys.stderr)
        return 2

    policy = _load_policy(args.pyproject)
    wheel = _walk_wheel(args.wheel)
    findings = _audit(wheel=wheel, policy=policy) + _check_build_paths(wheel, policy)
    if not findings:
        return 0
    for f in findings:
        print(f"{f.file}: {f.category}: {f.detail}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
