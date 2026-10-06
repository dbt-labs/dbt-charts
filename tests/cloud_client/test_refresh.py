"""Silent renewal of a stored login: the refresh grant, and the 401 retry."""

from __future__ import annotations

import stat
import threading
import time
from pathlib import Path

import httpx
import pytest

from dbt_charts.cloud_client.client import (
    CLIENT_ID,
    CloudClient,
    client_from_config,
    refresh_access_token,
)
from dbt_charts.cloud_client.config import (
    CloudConfig,
    config_path,
    load_config,
    read_config,
    save_config,
)
from dbt_charts.cloud_client.errors import (
    ApiFailed,
    SessionExpired,
    UnexpectedResponse,
)

HOST = "https://cloud.example"
TOKEN_URL = "https://cloud.example/o/token/"
DISCOVERY_DOC = {
    "issuer": HOST,
    "device_authorization_endpoint": f"{HOST}/o/device-authorization/",
    "token_endpoint": TOKEN_URL,
    "revocation_endpoint": f"{HOST}/o/revoke_token/",
}
ROTATED = {
    "access_token": "new-access",
    "refresh_token": "new-refresh",
    "token_type": "Bearer",
    "expires_in": 36000,
}


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("DCT_CLOUD_TOKEN", raising=False)
    return tmp_path


class Cloud:
    """A fake Cloud whose bearer check accepts one access token at a time."""

    def __init__(self, valid: str, token_response: httpx.Response | None = None):
        self.valid = valid
        self.token_response = token_response or httpx.Response(200, json=ROTATED)
        self.token_forms: list[dict[str, str]] = []
        self.bearers: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=DISCOVERY_DOC)
        if request.url.path == "/o/token/":
            self.token_forms.append(dict(httpx.QueryParams(request.content)))
            # Widen the race window so the concurrency test fails without the
            # lock instead of passing on scheduling luck.
            time.sleep(0.05)
            return self.token_response
        bearer = request.headers["authorization"].removeprefix("Bearer ")
        self.bearers.append(bearer)
        if bearer != self.valid:
            return httpx.Response(401, json={"code": "unauthenticated", "message": "x"})
        return httpx.Response(200, json={"organizations": []})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


def _stored(token: str = "old-access", refresh: str = "old-refresh") -> CloudConfig:
    config = CloudConfig(host=HOST, token=token, refresh_token=refresh)
    save_config(config)
    return config


# --- refresh_access_token ------------------------------------------------


def test_refresh_access_token_posts_the_refresh_grant() -> None:
    cloud = Cloud(valid="x")

    token = refresh_access_token(TOKEN_URL, "old-refresh", transport=cloud.transport)

    assert cloud.token_forms == [
        {
            "grant_type": "refresh_token",
            "refresh_token": "old-refresh",
            "client_id": CLIENT_ID,
        }
    ]
    assert (token.access_token, token.refresh_token) == ("new-access", "new-refresh")


def test_an_invalid_grant_means_the_session_expired_and_never_echoes_the_token() -> (
    None
):
    cloud = Cloud(
        valid="x", token_response=httpx.Response(400, json={"error": "invalid_grant"})
    )

    with pytest.raises(SessionExpired) as caught:
        refresh_access_token(TOKEN_URL, "secret-refresh", transport=cloud.transport)

    assert "dct cloud login" in str(caught.value)
    assert "secret-refresh" not in str(caught.value)


def test_a_malformed_refresh_response_names_fields_only() -> None:
    cloud = Cloud(
        valid="x",
        token_response=httpx.Response(200, json={"access_token": "leaky-access"}),
    )

    with pytest.raises(UnexpectedResponse) as caught:
        refresh_access_token(TOKEN_URL, "r", transport=cloud.transport)

    assert "leaky-access" not in str(caught.value)
    assert "refresh_token" in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(502, text="<html>bad gateway</html>"),
        httpx.Response(400, json={"error": "temporarily_unavailable"}),
    ],
    ids=["non-oauth-502", "retryable-oauth-error"],
)
def test_a_refresh_failure_that_is_not_invalid_grant_is_not_a_dead_session(
    response: httpx.Response,
) -> None:
    cloud = Cloud(valid="x", token_response=response)

    with pytest.raises(UnexpectedResponse) as caught:
        refresh_access_token(TOKEN_URL, "secret-refresh", transport=cloud.transport)

    assert "secret-refresh" not in str(caught.value)


# --- CloudClient retry ---------------------------------------------------


def test_a_401_refreshes_once_and_retries_with_the_new_bearer() -> None:
    cloud = Cloud(valid="fresh")
    refreshed: list[str] = []

    def refresh(rejected: str) -> str:
        refreshed.append(rejected)
        return "fresh"

    with CloudClient(HOST, "stale", cloud.transport, refresh=refresh) as client:
        client.list_orgs()
        client.list_orgs()

    assert refreshed == ["stale"]
    assert cloud.bearers == ["stale", "fresh", "fresh"]


def test_a_second_401_surfaces() -> None:
    cloud = Cloud(valid="never")
    calls: list[str] = []

    def refresh(rejected: str) -> str:
        calls.append(rejected)
        return "still-bad"

    with (
        CloudClient(HOST, "stale", cloud.transport, refresh=refresh) as client,
        pytest.raises(ApiFailed),
    ):
        client.list_orgs()

    assert calls == ["stale"]
    assert cloud.bearers == ["stale", "still-bad"]


def test_a_401_without_a_refresh_callable_surfaces_unchanged() -> None:
    cloud = Cloud(valid="never")

    with (
        CloudClient(HOST, "stale", cloud.transport) as client,
        pytest.raises(ApiFailed),
    ):
        client.list_orgs()

    assert cloud.bearers == ["stale"]


# --- client_from_config: the stored credential ------------------------------


def test_an_expired_stored_token_is_renewed_and_the_rotated_pair_saved(
    config_home: Path,
) -> None:
    config = _stored()
    cloud = Cloud(valid="new-access")

    with client_from_config(config, transport=cloud.transport) as client:
        assert client.list_orgs().organizations == []

    saved = read_config()
    assert (saved.token, saved.refresh_token) == ("new-access", "new-refresh")
    assert saved.host == HOST
    assert stat.S_IMODE(config_path().stat().st_mode) == 0o600
    assert [form["refresh_token"] for form in cloud.token_forms] == ["old-refresh"]


def test_a_token_another_process_already_rotated_is_used_without_a_refresh(
    config_home: Path,
) -> None:
    config = _stored()
    save_config(
        CloudConfig(host=HOST, token="peer-access", refresh_token="peer-refresh")
    )
    cloud = Cloud(valid="peer-access")

    with client_from_config(config, transport=cloud.transport) as client:
        client.list_orgs()

    assert cloud.token_forms == []
    assert read_config().refresh_token == "peer-refresh"


@pytest.mark.parametrize(
    "peer",
    [
        CloudConfig(host=HOST),
        CloudConfig(host="https://other.example", token="other-access"),
    ],
    ids=["peer-logged-out", "peer-logged-in-elsewhere"],
)
def test_a_peer_session_change_is_a_dead_session_not_a_borrowed_token(
    config_home: Path, peer: CloudConfig
) -> None:
    config = _stored()
    save_config(peer)
    cloud = Cloud(valid="never")

    with (
        client_from_config(config, transport=cloud.transport) as client,
        pytest.raises(SessionExpired),
    ):
        client.list_orgs()

    assert cloud.bearers == ["old-access"]
    assert cloud.token_forms == []


def test_a_revoked_session_tells_the_user_to_log_in_again(config_home: Path) -> None:
    config = _stored()
    cloud = Cloud(
        valid="x", token_response=httpx.Response(400, json={"error": "invalid_grant"})
    )

    with (
        client_from_config(config, transport=cloud.transport) as client,
        pytest.raises(SessionExpired),
    ):
        client.list_orgs()


def test_an_env_token_is_never_refreshed_and_the_config_is_untouched(
    config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stored()
    before = config_path().read_text(encoding="utf-8")
    monkeypatch.setenv("DCT_CLOUD_TOKEN", "env-token")
    cloud = Cloud(valid="never")

    with (
        client_from_config(load_config(), transport=cloud.transport) as client,
        pytest.raises(ApiFailed),
    ):
        client.list_orgs()

    assert cloud.token_forms == []
    assert config_path().read_text(encoding="utf-8") == before


def test_another_host_never_gets_the_stored_refresh_token(config_home: Path) -> None:
    config = _stored()
    cloud = Cloud(valid="never")

    with (
        client_from_config(
            config, "https://other.example", transport=cloud.transport
        ) as client,
        pytest.raises(ApiFailed),
    ):
        client.list_orgs()

    assert cloud.token_forms == []


def test_concurrent_clients_present_the_refresh_token_once(config_home: Path) -> None:
    """Cloud revokes the whole token family when a refresh token is reused."""
    config = _stored()
    cloud = Cloud(valid="new-access")
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            barrier.wait()
            with client_from_config(config, transport=cloud.transport) as client:
                client.list_orgs()
        except BaseException as exc:  # noqa: BLE001 — surfaced to the main thread
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(cloud.token_forms) == 1
