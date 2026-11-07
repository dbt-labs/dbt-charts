"""The `trino` client, held to the host its source names.

Imported only once the ``trino`` extra is known to be installed.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import requests
import trino.client
import trino.dbapi

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _place(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    return (
        parts.scheme,
        parts.hostname,
        parts.port or _DEFAULT_PORTS.get(parts.scheme),
    )


class SourceHostSession(requests.Session):
    """A session that sends a request, or follows a redirect, to one place only."""

    def __init__(self, scheme: str, host: str, port: int) -> None:
        super().__init__()
        self._origin = f"{scheme}://{host}:{port}"
        self._place = _place(self._origin)

    def send(
        self,
        request: requests.PreparedRequest,
        **kwargs: Any,  # type-state: explicit_any — requests' own send options
    ) -> requests.Response:
        url = str(request.url)
        if _place(url) != self._place:
            parts = urlsplit(url)
            raise ValueError(
                f"dbt Charts only follows URLs on {self._origin}, the "
                f"host the source names, but the Trino server sent one on "
                f"{parts.scheme}://{parts.netloc}."
            )
        return super().send(request, **kwargs)


class _DirectRequest(trino.client.TrinoRequest):
    def process(self, http_response: requests.Response) -> trino.client.TrinoStatus:
        # Refused here, before the client builds the segments: it would fetch
        # their URLs on a fresh session that SourceHostSession does not guard.
        status = super().process(http_response)
        if isinstance(status.rows, dict):
            raise ValueError(
                "The Trino server sent spooled results, which dbt Charts never "
                "requests and does not read."
            )
        return status


class SourceHostConnection(trino.dbapi.Connection):
    """A `trino.dbapi.Connection` held to the place it dials, refusing spooled results."""

    def __init__(
        self,
        **kwargs: Any,  # type-state: explicit_any — trino.dbapi.Connection's own kwargs
    ) -> None:
        super().__init__(**kwargs)
        # The client applies `verify` only to the session it builds, so it is
        # carried over from that one.
        default_session = self._http_session
        self._http_session = SourceHostSession(self.http_scheme, self.host, self.port)
        self._http_session.verify = default_session.verify
        default_session.close()

    def _create_request(self) -> trino.client.TrinoRequest:
        return _DirectRequest(
            self.host,
            self.port,
            self._client_session,
            self._http_session,
            self.http_scheme,
            self.auth,
            self.max_attempts,
            self.request_timeout,
        )
