"""Registry loader — parse and validate registry YAML into typed models.

Purpose: Load a registry.yaml string (or the built-in package registry) into
         a list of validated RegisteredView instances.

Two public functions:
- load_registry_yaml(raw, include_experimental=False)  — parse a YAML string
- load_builtin_registry(include_experimental=False)    — load the shipped registry
"""

from __future__ import annotations

from importlib.resources import files

import yaml
from pydantic import ValidationError

from dbt_charts.core.registered_views.models import RegisteredView


class RegistryLoadError(Exception):
    """Raised when a registry YAML fails to parse or validate."""


def load_registry_yaml(
    raw: str,
    *,
    include_experimental: bool = False,
) -> list[RegisteredView]:
    """Parse and validate a registry YAML string.

    Args:
        raw: YAML string with a top-level 'views' list.
        include_experimental: When False (default), experimental views are
            excluded from the returned list. Pass True to include them.

    Returns:
        List of validated RegisteredView instances in declaration order,
        filtered by the experimental flag.

    Raises:
        RegistryLoadError: If the YAML is malformed, missing the 'views' key,
            any entry fails Pydantic validation, or duplicate names are found.
    """
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RegistryLoadError(f"Registry YAML is not valid YAML: {exc}") from exc

    if not isinstance(data, dict) or "views" not in data:
        raise RegistryLoadError(
            "Registry YAML must have a top-level 'views' key containing a list."
        )

    raw_entries = data["views"]
    if not isinstance(raw_entries, list):
        raise RegistryLoadError("'views' must be a list of view entries.")

    views: list[RegisteredView] = []
    for i, entry in enumerate(raw_entries):
        try:
            view = RegisteredView.model_validate(entry)
        except ValidationError as exc:
            name = (
                entry.get("name", f"<entry {i}>")
                if isinstance(entry, dict)
                else f"<entry {i}>"
            )
            raise RegistryLoadError(
                f"Registry entry {name!r} failed validation: {exc}"
            ) from exc
        views.append(view)

    # Duplicate-name check across all entries (before filtering).
    seen: set[str] = set()
    for v in views:
        if v.name in seen:
            raise RegistryLoadError(f"Registry has a duplicate view name: {v.name!r}")
        seen.add(v.name)

    if not include_experimental:
        views = [v for v in views if not v.experimental]

    return views


def load_builtin_registry(
    *,
    include_experimental: bool = False,
) -> list[RegisteredView]:
    """Load the shipped built-in registry.yaml package data.

    Args:
        include_experimental: When False (default), experimental views are
            excluded. Built-in system views are all non-experimental.

    Returns:
        List of validated RegisteredView instances from the built-in registry.

    Raises:
        RegistryLoadError: If the built-in registry fails to load or validate.
    """
    registry_text = (
        files("dbt_charts.core.registered_views")
        .joinpath("registry.yaml")
        .read_text(encoding="utf-8")
    )
    return load_registry_yaml(registry_text, include_experimental=include_experimental)


def route_namespaces(views: list[RegisteredView]) -> list[str]:
    """Return the distinct literal first segment of each view's route.

    ``/data/…`` → ``data``, ``/inspector/<source>/`` → ``inspector``. First-seen
    order is preserved. Hosts mount these as URL prefixes (Cloud) or match them in
    the built-in router (``dct serve``), so the first segment must be a non-empty
    literal namespace — not a path param and not a rootless ``/`` route, both of
    which have no stable mount point. Fail loud rather than silently emit a broken
    prefix.
    """
    namespaces: list[str] = []
    for view in views:
        first = view.route.strip("/").split("/", 1)[0]
        if not first or first.startswith("<"):
            raise RegistryLoadError(
                f"Registered view {view.name!r} route {view.route!r} must begin with "
                "a literal namespace segment (e.g. '/data/'); a path param or empty "
                "first segment has no stable mount point."
            )
        if first not in namespaces:
            namespaces.append(first)
    return namespaces


def registered_view_prefixes(*, include_experimental: bool = False) -> list[str]:
    """Return the distinct top-level route namespace of every built-in view.

    Cloud derives its URL patterns from this list so a view added to
    ``registry.yaml`` is reachable under the Cloud project scope with no extra
    wiring — the same single source of truth ``dct serve`` gets from the built-in
    router.
    """
    return route_namespaces(
        load_builtin_registry(include_experimental=include_experimental)
    )
