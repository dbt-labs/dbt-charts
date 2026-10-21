"""Expand ``{{ s_<key> }}`` macros in wheel skill bodies.

Skill markdown is authored once with ``{{ s_<key> }}`` tokens. On the shared
tool-call surface the ``tool`` side of the alias renders; on the CLI surface
(``dct skills``, ``get_skill``/``list_skills`` called with ``surface="cli"``)
the ``dct`` side renders. A third surface, ``install``, renders only when a
skill is written to disk by ``dct init skills``: the one place a skill's
bare registry name (``chart``) becomes its ``dct-``-prefixed on-disk
name (``dct-chart``). ``cli`` stays bare so ``dct skills <name>``
resolves what it prints. The alias table lives in ``surface_aliases.yaml``
next to this module so every wheel skill and every surface points at the
same source of truth.

Skills that should never appear on one surface (e.g. ``mcp-setup`` is
CLI-only) declare ``surfaces:`` in their frontmatter; the registry hides them
from the wrong surface entirely. Skills shown on both surfaces use ``{{ s_key }}``
wherever they reference a tool/command so the prose reads naturally to both
audiences.

Board-YAML template tokens such as ``{{ region }}`` (no ``s_`` prefix) pass
through untouched — the regex only matches the ``s_`` prefix.
"""

from __future__ import annotations

import re
from functools import cache
from importlib.resources import files
from typing import Literal

import yaml

SkillSurface = Literal["tool", "cli", "install"]

_ALIASES_PATH = files("dbt_charts.agent_api").joinpath("surface_aliases.yaml")

# Matches `{{ s_<key> }}`. Keys are lowercase ASCII letters, digits,
# underscores; must start with a letter.  Plain `{{ variable }}` tokens
# (board-YAML template tokens) do NOT match because they lack the `s_` prefix.
_MACRO_RE = re.compile(r"\{\{\s*s_([a-z][a-z0-9_]*)\s*\}\}")

# Matches `{{#if_tool NAME}} ... {{/if_tool}}` (non-nested, DOTALL so the
# block can span lines). NAME is a bare tool name, same charset as `s_` keys.
_IF_TOOL_RE = re.compile(
    r"\{\{#if_tool\s+([a-z][a-z0-9_]*)\s*\}\}(.*?)\{\{/if_tool\}\}", re.DOTALL
)

# Matches `{{#if_surface tool}} ... {{/if_surface}}` / `{{#if_surface cli}}`:
# content that exists on one side only and is too large or generated to be an
# alias value (the CLI command table). `cli` also renders on the `install`
# surface, which is the same dct-side text written to disk.
_IF_SURFACE_RE = re.compile(
    r"\{\{#if_surface\s+(tool|cli)\s*\}\}(.*?)\{\{/if_surface\}\}", re.DOTALL
)


@cache
def _aliases() -> dict[str, dict[str, str]]:
    """Load and validate ``surface_aliases.yaml`` once per process.

    ``install`` is optional per entry and defaults to the ``dct`` value. The
    two surfaces render identically except for the bare-name family, which is
    the only content that differs between an on-disk directory name and a
    `dct skills` lookup.
    """
    raw = yaml.safe_load(_ALIASES_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{_ALIASES_PATH}: top level must be a mapping")
    out: dict[str, dict[str, str]] = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{_ALIASES_PATH}: entry {key!r} must be a mapping")
        unknown = set(entry) - {"tool", "dct", "install"}
        if unknown:
            raise ValueError(
                f"{_ALIASES_PATH}: entry {key!r} has unknown keys {sorted(unknown)!r}"
            )
        tool = entry.get("tool", "")
        dct = entry.get("dct", "")
        if not isinstance(tool, str) or not isinstance(dct, str):
            raise ValueError(
                f"{_ALIASES_PATH}: entry {key!r} must have string `tool` and `dct` values"
            )
        install = entry.get("install", dct)
        if not isinstance(install, str):
            raise ValueError(
                f"{_ALIASES_PATH}: entry {key!r} must have a string `install` value"
            )
        out[key] = {"tool": tool, "dct": dct, "install": install}
    return out


class MissingSurfaceAlias(KeyError):
    """Raised when a SKILL.md references a macro key that has no alias entry."""


def render_skill_body(
    body: str,
    *,
    surface: SkillSurface,
    available_tools: set[str] | None = None,
) -> str:
    """Return ``body`` with ``{{#if_tool}}``/``{{#if_surface}}`` blocks gated and ``{{ s_key }}`` macros expanded.

    ``{{#if_tool NAME}} ... {{/if_tool}}`` blocks are kept only when the surface
    actually has ``NAME`` in its tool set — dropped (including the markers)
    otherwise. ``available_tools=None`` (the default) keeps every block, so
    callers that don't declare a tool set (CLI, MCP, full chat) are unaffected.
    Blocks are non-nested.

    Each ``{{ s_key }}`` resolves to the ``tool``, ``dct``, or ``install`` side
    of the alias table based on the active surface. Unknown keys raise ``MissingSurfaceAlias``
    so a typo fails the surface fan-out test rather than shipping an unexpanded
    token into an agent's context window.

    Board-YAML template tokens (``{{ variable }}`` without ``s_`` prefix) pass
    through untouched.
    """

    def gate(match: re.Match[str]) -> str:
        name, block = match.group(1), match.group(2)
        if available_tools is None or name in available_tools:
            return block
        return ""

    side = {"tool": "tool", "cli": "dct", "install": "install"}[surface]

    def surface_gate(match: re.Match[str]) -> str:
        return match.group(2) if (match.group(1) == "tool") == (side == "tool") else ""

    surfaced = _IF_SURFACE_RE.sub(surface_gate, body)

    aliases = _aliases()

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in aliases:
            raise MissingSurfaceAlias(
                f"surface_aliases.yaml has no entry for {key!r} "
                f"(referenced as {{{{ s_{key} }}}})"
            )
        return aliases[key][side]

    # Macros first, so an alias value may carry a `{{#if_tool}}` block.
    return _IF_TOOL_RE.sub(gate, _MACRO_RE.sub(repl, surfaced))
