"""HTTP adapter for executing REST API queries.

Stage: EXECUTE
Purpose: Execute queries against HTTP/REST API endpoints.

This adapter allows fetching data from external APIs, supporting
GET, POST, PUT, DELETE, and PATCH methods with headers, params, and body.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    is_http_query,
)
from dbt_charts.core.compile.template.jinja import resolve_jinja_template
from dbt_charts.core.execute.adapters.base import (
    BaseAdapter,
    QueryParams,
    QueryResult,
    handle_adapter_error,
    plain_error,
)


def _resolve_json_path(data: Any, json_path: str) -> list[Any]:
    """Extract a list from data using a dot-notation JSONPath expression.

    Supports $.key and $.key.subkey format. Raises ValueError if the path
    is missing or the resolved value is not a list.
    """
    path = json_path.lstrip("$").lstrip(".")
    keys = path.split(".") if path else []

    node: Any = data
    traversed: list[str] = []
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            location = ".".join(traversed) or "$"
            raise ValueError(
                f"json_path '{json_path}': key '{key}' not found at '{location}'"
            )
        node = node[key]
        traversed.append(key)

    if not isinstance(node, list):
        raise ValueError(
            f"json_path '{json_path}': expected a list, got {type(node).__name__}"
        )
    return node


class HttpAdapter(BaseAdapter):
    """Adapter for executing HTTP/REST API queries.

    Supported query types: http

    Fetches data from REST API endpoints with support for:
    - Multiple HTTP methods (GET, POST, PUT, DELETE, PATCH)
    - Custom headers
    - Query parameters
    - Request body (JSON)

    Example:
        >>> adapter = HttpAdapter()
        >>> query = HttpQuery(
        ...     url="https://api.example.com/users",
        ...     headers={"Authorization": "Bearer {{ token }}"}
        ... )
        >>> result = adapter.execute(query, {"token": "xyz"})
    """

    def __init__(self, timeout: int = 30):
        """Initialize HTTP adapter.

        Args:
            timeout: Request timeout in seconds
        """
        self.timeout = timeout

    @property
    def supported_types(self) -> set[str]:
        """Return supported query types."""
        return {"http"}

    def _execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        """Execute an HTTP query.

        Uses type guard for type-safe field access.

        Args:
            query: AnyQuery object (HttpQuery expected)
            variables: Variable values for Jinja resolution
            params: Not used for HTTP queries (HTTP doesn't use SQL parameterization)

        Returns:
            QueryResult with data or error
        """
        if not is_http_query(query):
            return plain_error(f"Expected HTTP query, got {query.query_type}")

        url = query.url

        # Resolve Jinja templates in URL and other fields
        try:
            resolved_url = resolve_jinja_template(url, variables)
            method = query.method.upper()
            headers = query.headers or {}
            params_dict: dict[str, Any] = query.params or {}
            body = query.body

            # Resolve templates in headers, params, body
            resolved_headers: dict[str, Any] = {
                k: resolve_jinja_template(v, variables) for k, v in headers.items()
            }

            resolved_params: dict[str, Any] = {}
            for k, v in params_dict.items():
                if isinstance(v, str):
                    resolved_params[k] = resolve_jinja_template(v, variables)
                else:
                    resolved_params[k] = v

            resolved_body: Any = None
            if body:
                if isinstance(body, str):
                    resolved_body = resolve_jinja_template(body, variables)
                else:
                    resolved_body = {
                        k: (
                            resolve_jinja_template(v, variables)
                            if isinstance(v, str)
                            else v
                        )
                        for k, v in body.items()
                    }

        except (ValueError, KeyError, TypeError) as e:
            return handle_adapter_error("HTTP query template resolution", e)

        # Execute HTTP request
        try:
            with httpx.Client(timeout=self.timeout) as client:
                if method == "GET":
                    response = client.get(
                        resolved_url, headers=resolved_headers, params=resolved_params
                    )
                elif method == "POST":
                    response = client.post(
                        resolved_url,
                        headers=resolved_headers,
                        json=resolved_body,
                        params=resolved_params,
                    )
                elif method == "PUT":
                    response = client.put(
                        resolved_url,
                        headers=resolved_headers,
                        json=resolved_body,
                        params=resolved_params,
                    )
                elif method == "DELETE":
                    response = client.delete(
                        resolved_url, headers=resolved_headers, params=resolved_params
                    )
                elif method == "PATCH":
                    response = client.patch(
                        resolved_url,
                        headers=resolved_headers,
                        json=resolved_body,
                        params=resolved_params,
                    )
                else:
                    return plain_error(f"Unsupported HTTP method: {method}")

                response.raise_for_status()
                data = response.json()

                if query.json_path is not None:
                    try:
                        result_data = _resolve_json_path(data, query.json_path)
                    except ValueError as e:
                        return plain_error(str(e))
                elif isinstance(data, list):
                    result_data = data
                else:
                    return plain_error(
                        "HTTP response is a JSON object. "
                        "Set json_path to extract the data array "
                        "(e.g., json_path: $.data)."
                    )

                return QueryResult(data=result_data)

        except httpx.HTTPError as e:
            return handle_adapter_error("HTTP request", e)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return handle_adapter_error("HTTP query execution", e)
