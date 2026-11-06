"""A key a past grammar retired is reported as retired -- never as unknown.

The facts come from the ``versions/*.py`` declarations through ``retired_keys``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations.migrations import (
    retired_keys,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import JsonObject
from dbt_charts.core.diagnostics.codes_compile import ERR_EXTRA_FIELD, ERR_RETIRED_FIELD

# `titel:` is an unrelated typo, so migration declines the file and the retired
# key reaches validation beside a current-grammar key.
_BOARD = """titel: x
queries:
  q: {sql: "SELECT 1 AS a, 2 AS b"}
charts:
  rev:
    type: bar
    query: q
    x: a
    y: b
    style:
      axis_y:
        grid:
          zero: {color: red}
rows: [rev]
"""


def test_a_retired_key_leads_with_its_history_not_unknown_field() -> None:
    result = compile(_BOARD)

    [retired] = [e for e in result.errors if e.code == ERR_RETIRED_FIELD.code]
    assert retired.message == (
        "'zero' at charts.rev.style.axis_y.grid was renamed to 'threshold' in 0.7.0."
    )
    assert retired.hint is not None
    assert retired.hint.startswith("Rename the key.")
    assert retired.fields["retired_in"] == "0.7.0"
    assert retired.fields["successor"] == "charts.rev.style.axis_y.grid.threshold"
    assert not [
        e for e in result.errors if "'zero'" in e.message and "Unknown" in e.message
    ]


def test_the_unrelated_typo_stays_an_unknown_field() -> None:
    result = compile(_BOARD)

    assert [e.code for e in result.errors if "'titel'" in e.message] == [
        ERR_EXTRA_FIELD.code
    ]


def test_a_removed_key_leads_with_the_declared_reason() -> None:
    result = compile(
        _BOARD.replace("zero: {color: red}", "threshold: {color: red}").replace(
            "    style:",
            "    conditional_formatting: {b: {when: [{gt: 1, background: red}]}}\n    style:",
        )
    )

    [retired] = [e for e in result.errors if e.code == ERR_RETIRED_FIELD.code]
    assert retired.message.startswith(
        "'conditional_formatting' at charts.rev was removed in 0.7.0: "
    )
    assert retired.hint is not None
    assert retired.hint.startswith("Delete the key.")


def _resolve(root: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    if "$ref" not in node:
        return node
    target: Any = root
    for part in node["$ref"].removeprefix("#").strip("/").split("/"):
        if part:
            target = target[part]
    return target


def _example(root: dict[str, Any], node: dict[str, Any], depth: int = 0) -> object:
    """The smallest value *node* accepts: const/enum member, default, or its
    required fields."""
    node = _resolve(root, node)
    if "const" in node:
        return node["const"]
    if "enum" in node:
        return node["enum"][0]
    if node.get("default") is not None:
        return node["default"]
    branches = [
        b for b in node.get("anyOf", node.get("oneOf", [])) if b != {"type": "null"}
    ]
    if branches and depth < 24:
        return _example(root, branches[0], depth + 1)
    properties = node.get("properties", {})
    if node.get("type") == "array":
        return [_example(root, node.get("items", {}), depth + 1)] if depth < 24 else []
    if properties or node.get("type") == "object":
        extra = node.get("additionalProperties")
        if not properties and isinstance(extra, dict) and depth < 24:
            return {"k": _example(root, extra, depth + 1)}
        return {
            name: _example(root, properties[name], depth + 1)
            for name in node.get("required", [])
            if depth < 24
        }
    if node.get("type") == "number" and "exclusiveMaximum" in node:
        return (node.get("exclusiveMinimum", 0) + node["exclusiveMaximum"]) / 2
    return {"integer": 1, "number": 1.5, "boolean": True}.get(node.get("type"), "x")


def _instances(
    root: dict[str, Any],
    node: dict[str, Any],
    path: tuple[str, ...],
    budget: list[int],
    chart_type: str | None,
    depth: int = 0,
) -> Iterator[object]:
    """Minimal instances of *node* with a value at *path* (``*`` is a map entry or
    list slot), found anywhere beneath *node*: a ``Deletion`` or ``ConditionalMove``
    tail is positional, so the search tries every field that could hold it."""
    node = _resolve(root, node)
    budget[0] -= 1
    if budget[0] < 0 or depth > 24:
        return
    if not path:
        yield _example(root, node)
        return
    for branch in node.get("anyOf", node.get("oneOf", [])):
        yield from _instances(root, branch, path, budget, chart_type, depth + 1)
    for rule in node.get("allOf", []):
        # A cross-field rule constrains a valid instance rather than selecting an
        # arm; every other rule is a `type`-discriminated union: the `if` names
        # the tag, the `then` the arm.
        if "x-dbt-charts-cross-field-rule" in rule:
            continue
        [(tag_key, tag_schema)] = rule["if"]["properties"].items()
        for tag_value in tag_schema["enum"]:
            if chart_type is not None and tag_value != chart_type:
                continue
            for child in _instances(
                root, rule["then"], path, budget, chart_type, depth + 1
            ):
                yield {**child, tag_key: tag_value}
    head, rest = path[0], path[1:]
    properties = node.get("properties", {})
    extra = node.get("additionalProperties")
    if node.get("type") == "array" and head == "*":
        for child in _instances(
            root, node.get("items", {}), rest, budget, chart_type, depth + 1
        ):
            yield [child]
    if not (properties or isinstance(extra, dict)):
        return
    required = {
        name: _example(root, properties[name], depth + 1)
        for name in node.get("required", [])
        if name in properties
    }
    if head in properties:
        for child in _instances(
            root, properties[head], rest, budget, chart_type, depth + 1
        ):
            yield {**required, head: child}
    elif head == "*" and isinstance(extra, dict):
        for child in _instances(root, extra, rest, budget, chart_type, depth + 1):
            yield {"c1": child}
    for name, sub in properties.items():
        for child in _instances(root, sub, path, budget, chart_type, depth + 1):
            yield {**required, name: child}
    if isinstance(extra, dict):
        for child in _instances(root, extra, path, budget, chart_type, depth + 1):
            yield {"c1": child}
    if node.get("type") == "array":
        for child in _instances(
            root, node.get("items", {}), path, budget, chart_type, depth + 1
        ):
            yield [child]


def _matches(key: tuple[str, ...], path: tuple[str, ...]) -> bool:
    """*key* is the concrete spelling of declared *path* (``*`` binds anything),
    anchored at its end: a ``Deletion`` or ``ConditionalMove`` tail is positional."""
    return len(key) >= len(path) and all(
        want in ("*", got) for want, got in zip(path, key[-len(path) :], strict=True)
    )


def _retired_in(document: dict[str, object]) -> set[tuple[str, ...]]:
    return set(retired_keys(cast(JsonObject, document)))


_INLINE_ITEM_ROOTS = frozenset(("rows", "cols", "grid", "tabs"))


# The 0.5.0 schema has no histogram or donut chart model carrying a chart-local
# ``style``, so no 0.5.0 board can spell these declared positions.
_UNINSTANTIABLE = frozenset(
    (("style", field), family)
    for family in ("histogram", "donut")
    for field in ("font", "border")
)
