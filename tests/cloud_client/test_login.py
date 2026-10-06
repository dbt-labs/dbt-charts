"""The OAuth 2.0 device grant (RFC 8628) and its RFC 8414 discovery step."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from dbt_charts.cloud_client.client import (
    CLIENT_ID,
    DEVICE_GRANT_TYPE,
    discover_endpoints,
    poll_device_token,
    revoke_token,
    start_device_login,
)
from dbt_charts.cloud_client.errors import (
    DeviceLoginFailed,
    TransportFailed,
    UnexpectedResponse,
)

Handler = Callable[[httpx.Request], httpx.Response]

DISCOVERY_DOC = {
    "issuer": "https://cloud.example",
    "device_authorization_endpoint": "https://cloud.example/o/device-authorization/",
    "token_endpoint": "https://cloud.example/o/token/",
    "revocation_endpoint": "https://cloud.example/o/revoke_token/",
    # Real RFC 8414 documents carry many more fields than this client reads.
    "scopes_supported": ["dashboards:read", "orgs:admin"],
}

DEVICE_AUTH_RESPONSE = {
    "device_code": "devc-secret-123",
    "user_code": "ABCD-EFGH",
    "verification_uri": "https://cloud.example/activate",
    "verification_uri_complete": "https://cloud.example/activate?user_code=ABCD-EFGH",
    "expires_in": 600,
    "interval": 5,
}


class FakeClock:
    """A controllable clock/sleep pair so polling tests never actually wait."""

    def __init__(self) -> None:
        self.value = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.value += seconds


def _form(request: httpx.Request) -> dict[str, str]:
    return dict(httpx.QueryParams(request.content))


# --- discovery ---------------------------------------------------------


def test_discover_endpoints_reads_the_rfc8414_document() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/.well-known/oauth-authorization-server"
        return httpx.Response(200, json=DISCOVERY_DOC)

    metadata = discover_endpoints(
        "https://cloud.example", transport=httpx.MockTransport(handler)
    )
    assert (
        metadata.device_authorization_endpoint
        == (DISCOVERY_DOC["device_authorization_endpoint"])
    )
    assert metadata.token_endpoint == DISCOVERY_DOC["token_endpoint"]
    assert metadata.revocation_endpoint == DISCOVERY_DOC["revocation_endpoint"]


def test_a_doc_without_device_authorization_names_the_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        doc = {**DISCOVERY_DOC, "device_authorization_endpoint": None}
        return httpx.Response(200, json=doc)

    with pytest.raises(DeviceLoginFailed) as caught:
        discover_endpoints(
            "https://cloud.example", transport=httpx.MockTransport(handler)
        )
    message = str(caught.value)
    assert "https://cloud.example" in message
    assert "device login" in message


# --- start_device_login --------------------------------------------------


def test_start_device_login_posts_client_id_and_scope() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_form(request))
        return httpx.Response(200, json=DEVICE_AUTH_RESPONSE)

    result = start_device_login(
        "https://cloud.example/o/device-authorization/",
        transport=httpx.MockTransport(handler),
    )
    assert result.device_code == "devc-secret-123"
    assert result.user_code == "ABCD-EFGH"
    assert seen == [
        {
            "client_id": CLIENT_ID,
            "scope": "dashboards:read orgs:admin projects:admin connections:admin",
        }
    ]


def test_start_device_login_reports_a_rejection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_client"})

    with pytest.raises(DeviceLoginFailed) as caught:
        start_device_login(
            "https://cloud.example/o/device-authorization/",
            transport=httpx.MockTransport(handler),
        )
    assert "invalid_client" in str(caught.value)


# --- poll_device_token -----------------------------------------------------


def test_poll_device_token_succeeds_after_pending() -> None:
    responses = iter(
        [
            httpx.Response(400, json={"error": "authorization_pending"}),
            httpx.Response(400, json={"error": "authorization_pending"}),
            httpx.Response(
                200,
                json={
                    "access_token": "tok-secret-abc",
                    "refresh_token": "ref-secret-abc",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "dashboards:read",
                },
            ),
        ]
    )
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_form(request))
        return next(responses)

    clock = FakeClock()
    token = poll_device_token(
        "https://cloud.example/o/token/",
        "devc-secret-123",
        5.0,
        600.0,
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        now=clock.now,
    )
    assert token.access_token == "tok-secret-abc"
    assert token.refresh_token == "ref-secret-abc"
    assert len(calls) == 3
    assert all(call["device_code"] == "devc-secret-123" for call in calls)
    assert all(call["grant_type"] == DEVICE_GRANT_TYPE for call in calls)
    assert all(call["client_id"] == CLIENT_ID for call in calls)
    assert clock.slept == [5.0, 5.0, 5.0]


def test_slow_down_widens_the_interval_by_five_seconds() -> None:
    responses = iter(
        [
            httpx.Response(400, json={"error": "slow_down"}),
            httpx.Response(
                200,
                json={
                    "access_token": "t0k",
                    "refresh_token": "r0k",
                    "token_type": "Bearer",
                },
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    clock = FakeClock()
    poll_device_token(
        "https://cloud.example/o/token/",
        "d",
        5.0,
        600.0,
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        now=clock.now,
    )
    assert clock.slept == [5.0, 10.0]


def test_access_denied_fails_loudly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "access_denied"})

    clock = FakeClock()
    with pytest.raises(DeviceLoginFailed) as caught:
        poll_device_token(
            "https://cloud.example/o/token/",
            "devc-secret-123",
            1.0,
            600.0,
            transport=httpx.MockTransport(handler),
            sleep=clock.sleep,
            now=clock.now,
        )
    assert "denied" in str(caught.value).lower()


def test_expired_token_fails_loudly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "expired_token"})

    clock = FakeClock()
    with pytest.raises(DeviceLoginFailed) as caught:
        poll_device_token(
            "https://cloud.example/o/token/",
            "devc-secret-123",
            1.0,
            600.0,
            transport=httpx.MockTransport(handler),
            sleep=clock.sleep,
            now=clock.now,
        )
    assert "expired" in str(caught.value).lower()


def test_client_side_deadline_stops_polling_before_another_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not poll past the client-side deadline")

    clock = FakeClock()
    with pytest.raises(DeviceLoginFailed) as caught:
        poll_device_token(
            "https://cloud.example/o/token/",
            "d",
            5.0,
            1.0,  # expires before the first interval-second sleep completes
            transport=httpx.MockTransport(handler),
            sleep=clock.sleep,
            now=clock.now,
        )
    assert "expired" in str(caught.value).lower()


def test_failure_messages_never_contain_the_device_code_or_token() -> None:
    device_code = "devc-should-never-leak"
    token = "tok-should-never-leak"

    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "access_denied"})

    clock = FakeClock()
    with pytest.raises(DeviceLoginFailed) as caught:
        poll_device_token(
            "https://cloud.example/o/token/",
            device_code,
            1.0,
            60.0,
            transport=httpx.MockTransport(denied),
            sleep=clock.sleep,
            now=clock.now,
        )
    assert device_code not in str(caught.value)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(TransportFailed) as caught2:
        revoke_token(
            "https://cloud.example/o/revoke_token/",
            token,
            transport=httpx.MockTransport(unreachable),
        )
    assert token not in str(caught2.value)

    def malformed_success(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": token, "refresh_token": "r"})

    with pytest.raises(UnexpectedResponse) as caught3:
        poll_device_token(
            "https://cloud.example/o/token/",
            device_code,
            1.0,
            60.0,
            transport=httpx.MockTransport(malformed_success),
            sleep=clock.sleep,
            now=clock.now,
        )
    assert token not in str(caught3.value)
    assert "token_type" in str(caught3.value)


# --- revoke_token ------------------------------------------------------


def test_revoke_token_posts_token_and_client_id() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_form(request))
        return httpx.Response(200)

    revoke_token(
        "https://cloud.example/o/revoke_token/",
        "tok-abc",
        transport=httpx.MockTransport(handler),
    )
    assert seen == [{"token": "tok-abc", "client_id": CLIENT_ID}]


def test_revoke_token_treats_an_unknown_token_as_success() -> None:
    """RFC 7009: 200 for an unknown/expired token is the correct answer."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    revoke_token(
        "https://cloud.example/o/revoke_token/",
        "already-gone",
        transport=httpx.MockTransport(handler),
    )


def test_revoke_token_raises_loudly_on_a_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(UnexpectedResponse):
        revoke_token(
            "https://cloud.example/o/revoke_token/",
            "tok-abc",
            transport=httpx.MockTransport(handler),
        )
