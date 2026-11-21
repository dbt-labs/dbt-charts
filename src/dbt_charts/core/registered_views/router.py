"""Route compilation and matching for registered views.

Purpose: Compile route patterns into matchers and resolve request paths
         to a view + path params dict.

Route grammar:
- Must begin and end with '/'.
- Segments are either literal strings or named params in angle-brackets:
  '<name>' where name is a non-empty identifier (letters, digits, underscores).
- Params match exactly one non-empty path segment with no '/' or whitespace.
- No optional segments, no typed converters. Use separate explicit routes
  for each depth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dbt_charts.core.registered_views.models import RegisteredView

# A valid path param name: non-empty, alphanumeric + underscore.
_PARAM_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclass(frozen=True)
class RouteMatch:
    """Result of a successful route match."""

    view: RegisteredView
    path_params: dict[str, str]


def compile_route(route: str) -> re.Pattern[str]:
    """Compile a route string into a regex that captures named path params.

    Args:
        route: URL pattern like '/data/<source>/<schema>/'.

    Returns:
        Compiled regex with named groups for each path param.

    Raises:
        ValueError: If the route does not begin/end with '/', uses an empty
                    param name, or contains invalid param syntax.
    """
    if not route.startswith("/"):
        raise ValueError(f"Route must begin with a leading slash: {route!r}")
    if not route.endswith("/"):
        raise ValueError(f"Route must end with a trailing slash: {route!r}")

    # Split into segments, discarding the empty strings from the surrounding slashes.
    segments = route.strip("/").split("/")
    pattern_parts: list[str] = ["^"]
    for segment in segments:
        if segment.startswith("<") and segment.endswith(">"):
            param_name = segment[1:-1]
            if not param_name:
                raise ValueError(f"Route contains an empty param name '<>': {route!r}")
            if not _PARAM_NAME_RE.match(param_name):
                raise ValueError(
                    f"Invalid param name {param_name!r} in route {route!r}. "
                    "Param names must start with a letter or underscore and "
                    "contain only letters, digits, and underscores."
                )
            # Capture non-empty, no-slash, no-whitespace segments.
            pattern_parts.append(f"/(?P<{param_name}>[^\\s/]+)")
        elif "<" in segment or ">" in segment:
            # Partially formed angle-bracket: not a well-formed param and not a
            # pure literal. Compiling it as a literal would silently produce a
            # pattern that never matches. Error fast instead.
            raise ValueError(
                f"Route segment {segment!r} contains a malformed param placeholder "
                f"in route {route!r}. Use '<name>' for a path param or a plain "
                "string for a literal segment."
            )
        else:
            # Literal segment — escape for regex.
            pattern_parts.append(f"/{re.escape(segment)}")
    pattern_parts.append("/$")
    return re.compile("".join(pattern_parts))


class RouteRouter:
    """Matches request paths against a list of registered views.

    Tries each view's compiled route pattern in registration order and
    returns the first match as a RouteMatch, or None if no route matches.
    """

    def __init__(self, views: list[RegisteredView]) -> None:
        """Build the router from a list of registered views.

        Args:
            views: Ordered list of RegisteredView entries. Each view's route
                   is compiled into a regex at construction time.

        Raises:
            ValueError: If any two views share the same name.
        """
        seen_names: set[str] = set()
        for v in views:
            if v.name in seen_names:
                raise ValueError(
                    f"Registered view registry has duplicate name: {v.name!r}"
                )
            seen_names.add(v.name)

        self._entries: list[tuple[RegisteredView, re.Pattern[str]]] = [
            (v, compile_route(v.route)) for v in views
        ]

    def match(self, path: str) -> RouteMatch | None:
        """Match a request path against all registered routes.

        Args:
            path: Absolute URL path to match, e.g. '/data/snowflake/analytics/'.

        Returns:
            RouteMatch with the matched view and extracted path params,
            or None if no route matches.
        """
        # Routes are directory-like — every compiled pattern ends in '/'. A request
        # path is the same view with or without its trailing slash, so slashless
        # board links (/data/wh/orders) resolve like their /data/wh/orders/ form.
        if path and not path.endswith("/"):
            path = f"{path}/"
        for view, pattern in self._entries:
            m = pattern.match(path)
            if m is None:
                continue
            return RouteMatch(view=view, path_params=m.groupdict())
        return None
