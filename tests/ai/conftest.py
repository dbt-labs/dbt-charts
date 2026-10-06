"""Shared fixtures for dbt_charts.ai tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.adapters import AdapterRegistry, build_adapter_registry


@pytest.fixture
def registry(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> AdapterRegistry:
    """A writable AdapterRegistry rooted at tmp_path.

    Most contract / handler tests just need a registry that resolves the
    project root and supports source lookup. They don't care which adapters
    are registered — only that one exists when the handler asks for it.
    """
    return build_adapter_registry(local_project(tmp_path), read_only=False)


# ---------------------------------------------------------------------------
# OpenAI strict-mode schema validation, shared by test_llm.py and
# test_tool_schemas.py so there is exactly one recursive checker for the
# ruleset — a rule guarded in one file and not the other is the shape that
# produced the #7013 outage (a `format` violation no test caught for 17h).
# ---------------------------------------------------------------------------

# OpenAI's Structured Outputs / strict function-calling schema validator only
# accepts this fixed allowlist for string "format" — anything else (e.g. the
# "path" pydantic emits for pathlib.Path fields) is a 400 at request time,
# rejecting the whole tools array before any tool is ever called.
_OPENAI_ALLOWED_STRING_FORMATS = {
    "date-time",
    "time",
    "date",
    "duration",
    "email",
    "hostname",
    "ipv4",
    "ipv6",
    "uuid",
}

# Every JSON Schema keyword our normalized tool payload is verified to emit
# safely today. `format` is included here as a keyword (its *value* is
# checked separately against the allowlist above); everything else is a
# structural keyword confirmed either by OpenAI's docs (`type`, `properties`,
# `required`, `additionalProperties`, `items`, `$ref`, `$defs`, `anyOf`,
# `enum`, `description`, `title`) or by a live API call against our own
# schemas (`default`, `minimum`, `maximum` on the `limit` fields — the docs
# call these unsupported, current behavior disagrees, see
# apps/evals/tests/e2e/test_openai_tool_schema_acceptance.py). A keyword
# outside this set is unverified: it may be silently accepted, silently
# ignored, or a 400 like #7013's `format: "path"` was — flagging it forces
# that to be checked against a live call before it ships.
_OPENAI_VERIFIED_SCHEMA_KEYWORDS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "$ref",
    "$defs",
    "anyOf",
    "enum",
    "description",
    "title",
    "default",
    "minimum",
    "maximum",
    "format",
}


def strict_mode_violations(schema: object, path: str = "$") -> list[str]:
    """Recursively check *schema* against OpenAI's strict-mode schema rules.

    Every check here 400s the *entire* tools array before any tool call
    runs (the #7013 mechanism), so each is worth verifying independently of
    ``dbt_charts.ai.llm._to_strict_json_schema`` rather than trusting that
    transform blindly:

    - a string `format` value outside OpenAI's allowlist
    - an object missing `additionalProperties: false`
    - a `required` list that omits a declared property
    - a `default: null` entry (the transform is supposed to strip these)
    - any schema keyword outside the verified-safe set
    """
    violations: list[str] = []
    if not isinstance(schema, dict):
        return violations

    unknown_keywords = set(schema) - _OPENAI_VERIFIED_SCHEMA_KEYWORDS
    if unknown_keywords:
        violations.append(
            f"{path}: unverified schema keyword(s) {sorted(unknown_keywords)}"
        )

    fmt = schema.get("format")
    if isinstance(fmt, str) and fmt not in _OPENAI_ALLOWED_STRING_FORMATS:
        violations.append(f"{path}.format={fmt!r}")

    if schema.get("type") == "object":
        if schema.get("additionalProperties") is not False:
            actual = schema.get("additionalProperties", "<missing>")
            violations.append(
                f"{path}: additionalProperties must be false (is {actual!r})"
            )

    properties = schema.get("properties")
    if properties:
        missing_required = set(properties) - set(schema.get("required") or [])
        if missing_required:
            violations.append(f"{path}: required is missing {sorted(missing_required)}")

    if "default" in schema and schema["default"] is None:
        violations.append(f"{path}: has default: null")

    # OpenAI rejects a `$ref` carrying sibling keys; the transform inlines it.
    # Checked here independently of the transform, so a regression there is
    # caught over the real emitted bytes rather than only by its unit test.
    if "$ref" in schema and len(schema) > 1:
        violations.append(
            f"{path}: $ref with sibling keys {sorted(set(schema) - {'$ref'})}"
        )

    for name, sub in schema.get("$defs", {}).items():
        violations.extend(strict_mode_violations(sub, f"{path}.$defs.{name}"))
    for name, sub in schema.get("properties", {}).items():
        violations.extend(strict_mode_violations(sub, f"{path}.properties.{name}"))
    for key in ("anyOf", "allOf", "oneOf"):
        for index, sub in enumerate(schema.get(key, [])):
            violations.extend(strict_mode_violations(sub, f"{path}.{key}[{index}]"))
    if "items" in schema:
        violations.extend(strict_mode_violations(schema["items"], f"{path}.items"))
    return violations
