"""The HTTP layer: what it sends, what it parses, and how it fails."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import quote

import httpx
import pytest

from dbt_charts.cloud_client.client import CloudClient
from dbt_charts.cloud_client.contract import ErrorCode, OrgSummary, SetupStage
from dbt_charts.cloud_client.errors import (
    ApiFailed,
    CredentialMissing,
    InvalidCredential,
    InvalidPathSegment,
    TransportFailed,
    UnexpectedResponse,
)

Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler, *, token: str = "t0ken") -> CloudClient:
    return CloudClient(
        host="https://cloud.example",
        token=token,
        transport=httpx.MockTransport(handler),
    )


def test_get_carries_the_bearer_token_and_hits_the_api_prefix() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"organizations": []})

    with client(handler) as cloud:
        assert cloud.list_orgs().organizations == []

    assert str(seen[0].url) == "https://cloud.example/api/orgs"
    assert seen[0].headers["authorization"] == "Bearer t0ken"


def test_response_parses_into_the_contract_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "organizations": [
                    {"slug": "acme-data", "name": "Acme Data", "role": "ADMIN"}
                ]
            },
        )

    with client(handler) as cloud:
        orgs = cloud.list_orgs()

    assert orgs.organizations == [
        OrgSummary(slug="acme-data", name="Acme Data", role="ADMIN")
    ]


def test_create_org_posts_the_form_fields() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            201, json={"slug": "acme-data", "name": "Acme Data", "role": "ADMIN"}
        )

    with client(handler) as cloud:
        assert cloud.create_org(name="Acme Data", slug="acme-data").slug == "acme-data"

    assert seen == [{"name": "Acme Data", "slug": "acme-data"}]


def test_status_parses_the_state_machine() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "stage": "unmapped_sources",
                "next_step": "Map source `db` to a connection.",
                "connection_count": 1,
                "tested_connection_count": 1,
                "projects": [],
            },
        )

    with client(handler) as cloud:
        status = cloud.org_status("acme-data")

    assert status.stage is SetupStage.UNMAPPED_SOURCES
    assert status.next_step == "Map source `db` to a connection."


def test_api_error_payload_becomes_a_typed_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "code": "validation_failed",
                "message": "The request did not validate.",
                "field_errors": {"slug": ["Already taken."]},
            },
        )

    with client(handler) as cloud, pytest.raises(ApiFailed) as caught:
        cloud.create_org(name="Acme", slug="acme")

    failure = caught.value
    assert failure.status_code == 400
    assert failure.api_error.code is ErrorCode.VALIDATION_FAILED
    assert failure.api_error.field_errors == {"slug": ["Already taken."]}
    assert "slug: Already taken." in str(failure)


def test_unreachable_host_names_the_host_and_the_cause() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nodename nor servname provided")

    with client(handler) as cloud, pytest.raises(TransportFailed) as caught:
        cloud.list_orgs()

    message = str(caught.value)
    assert "https://cloud.example" in message
    assert "nodename nor servname provided" in message


def test_a_body_that_is_not_the_contract_is_a_loud_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    with client(handler) as cloud, pytest.raises(UnexpectedResponse) as caught:
        cloud.list_orgs()

    message = str(caught.value)
    assert "https://cloud.example/api/orgs" in message
    assert "not the dbt charts Cloud API" in message
    assert "upgrade" not in message


def test_json_missing_required_fields_blames_an_older_cloud() -> None:
    """JSON that parsed and then failed only on MISSING fields -- here Cloud
    simply hasn't deployed a field this dct's contract already requires --
    means Cloud is BEHIND this dct, not ahead of it (FR-58). Telling the user
    to "upgrade dbt-charts" here is backwards: upgrading only requires more
    fields Cloud still won't send."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"projects": [{"slug": "analytics"}]})

    with client(handler) as cloud, pytest.raises(UnexpectedResponse) as caught:
        cloud.list_projects("acme-data")

    message = str(caught.value)
    assert "Cloud is older than this dct" in message
    assert "upgrade dbt-charts" not in message


def test_json_with_a_wrong_field_type_blames_a_newer_cloud() -> None:
    """A field that parsed but with a type/shape this dct has never seen --
    not merely absent -- means Cloud is AHEAD of this dct, so the upgrade
    advice is the right one here."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "stage": "not-a-real-stage",
                "next_step": None,
                "connection_count": 1,
                "tested_connection_count": 1,
                "projects": [],
            },
        )

    with client(handler) as cloud, pytest.raises(UnexpectedResponse) as caught:
        cloud.org_status("acme-data")

    message = str(caught.value)
    assert "Cloud is newer than this dct" in message
    assert "upgrade dbt-charts" in message


def test_an_error_body_that_is_not_the_contract_is_a_loud_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway</html>")

    with client(handler) as cloud, pytest.raises(UnexpectedResponse) as caught:
        cloud.list_orgs()

    message = str(caught.value)
    assert "502" in message
    assert "not the dbt charts Cloud API" in message
    assert "upgrade" not in message


def test_a_field_this_dct_has_never_heard_of_is_ignored() -> None:
    """Cloud deploys continuously; this client is a PyPI install that lags it.
    A response field added after this dct shipped must not stop it reading
    the fields it does know -- at any depth of the body."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "projects": [
                    {
                        "slug": "analytics",
                        "name": "Analytics",
                        "repo_label": "acme/analytics",
                        "trunk_branch": "main",
                        "work_branch": "dct",
                        "git_subdirectory": "",
                        "unmapped_source_count": 0,
                        "added_in_a_later_release": True,
                    }
                ],
                "also_added_in_a_later_release": {"nested": 1},
            },
        )

    with client(handler) as cloud:
        projects = cloud.list_projects("acme-data").projects

    assert [(p.slug, p.unmapped_source_count) for p in projects] == [("analytics", 0)]


def test_no_token_is_refused_before_any_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not reach the network without a credential")

    with pytest.raises(CredentialMissing) as caught:
        client(handler, token="")

    assert str(caught.value)


def test_a_token_with_a_control_character_is_refused_before_any_request() -> None:
    """HIGH-1: a token with an embedded newline becomes an illegal HTTP
    header byte -- httpx/httpcore's own error text for that echoes the raw
    header (bearer token included). Reject it here, before it ever reaches
    the network, rather than let it surface via a transport error."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not reach the network with a malformed credential")

    with pytest.raises(InvalidCredential) as caught:
        client(handler, token="abc\ndef")

    message = str(caught.value)
    assert "abc" not in message
    assert "def" not in message


def test_transport_failed_never_echoes_a_local_protocol_errors_raw_text() -> None:
    """HIGH-1, defense in depth: even if some other path let a malformed
    header reach the wire, TransportFailed must not interpolate an
    httpx.LocalProtocolError-family cause's own text -- that text is httpx's
    literal illegal header bytes, which is the credential."""
    secret_fragment = "Bearer t0ken-with-a-newline"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.LocalProtocolError(f"Illegal header value b'{secret_fragment}\\n'")

    with client(handler) as cloud, pytest.raises(TransportFailed) as caught:
        cloud.list_orgs()

    message = str(caught.value)
    assert secret_fragment not in message
    assert "https://cloud.example" in message


def test_connection_test_failure_is_the_servers_own_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "code": "connection_test_failed",
                "message": "Could not reach the warehouse: permission denied.",
                "field_errors": {},
            },
        )

    with client(handler) as cloud, pytest.raises(ApiFailed) as caught:
        cloud.test_connection("acme-data", "acme-bigquery")

    assert caught.value.api_error.code is ErrorCode.CONNECTION_TEST_FAILED
    assert "permission denied" in str(caught.value)


def test_every_verb_targets_its_documented_endpoint() -> None:
    """One call per Wave-1 verb, pinned to method and path."""
    seen: list[tuple[str, str]] = []
    bodies: dict[str, object] = {
        "/api/orgs": {"organizations": []},
        "/api/orgs/acme/projects": {"projects": []},
        "/api/orgs/acme/projects/an/sync": {"queued": True, "message": "Sync queued."},
        "/api/orgs/acme/projects/an/render": {"started": 2, "unrendered_remaining": 0},
        "/api/orgs/acme/projects/an/sources": {"sources": [], "unmapped_count": 0},
        "/api/orgs/acme/connections": {"connections": []},
        "/api/orgs/acme/status": {
            "stage": "done",
            "next_step": None,
            "connection_count": 0,
            "tested_connection_count": 0,
            "projects": [],
        },
        "/api/orgs/acme/github/pick": {
            "repo_id": "1",
            "full_name": "acme/analytics",
            "default_branch": "main",
            "private": False,
            "picked_at": "2026-08-30T00:00:00Z",
            "dbt_roots": [],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json=bodies[request.url.path])

    with client(handler) as cloud:
        cloud.list_orgs()
        cloud.list_projects("acme")
        cloud.sync_project("acme", "an")
        cloud.render_project("acme", "an", force=False)
        cloud.list_sources("acme", "an")
        cloud.list_connections("acme")
        cloud.org_status("acme")
        cloud.latest_pick("acme")

    assert seen == [
        ("GET", "/api/orgs"),
        ("GET", "/api/orgs/acme/projects"),
        ("POST", "/api/orgs/acme/projects/an/sync"),
        ("POST", "/api/orgs/acme/projects/an/render"),
        ("GET", "/api/orgs/acme/projects/an/sources"),
        ("GET", "/api/orgs/acme/connections"),
        ("GET", "/api/orgs/acme/status"),
        ("GET", "/api/orgs/acme/github/pick"),
    ]


def test_every_wave_2_verb_targets_its_documented_endpoint() -> None:
    """One call per management verb, pinned to method and path."""
    seen: list[tuple[str, str]] = []
    delete_result = {"deleted": True, "message": "ok"}
    sync_result = {"queued": True, "message": "ok"}
    bodies: dict[str, object] = {
        "/api/orgs/acme": delete_result,
        "/api/orgs/acme/members": {"members": []},
        "/api/orgs/acme/members/a@example.com": delete_result,
        "/api/orgs/acme/members/a@example.com/role": {
            "email": "a@example.com",
            "name": "",
            "role": "admin",
            "joined_at": "2026-01-01T00:00:00Z",
        },
        "/api/orgs/acme/invitations": {"invitations": []},
        "/api/orgs/acme/invitations/a@example.com": delete_result,
        "/api/orgs/acme/invitations/a@example.com/resend": {
            "email": "a@example.com",
            "role": "creator",
            "already_member": False,
        },
        "/api/orgs/acme/grants": {"grants": []},
        "/api/orgs/acme/grants/g-1": {
            "revoked": True,
            "self_revoked": False,
            "message": "ok",
        },
        "/api/orgs/acme/projects/an": delete_result,
        "/api/orgs/acme/projects/an/scaffold": sync_result,
        "/api/orgs/acme/projects/an/boards": {"boards": []},
        "/api/orgs/acme/connections/wh": delete_result,
        "/api/orgs/acme/connections/wh/schema-refresh": sync_result,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json=bodies[request.url.path])

    with client(handler) as cloud:
        cloud.delete_org("acme")
        cloud.list_members("acme")
        cloud.remove_member("acme", "a@example.com")
        cloud.set_member_role("acme", "a@example.com", "admin")
        cloud.list_invitations("acme")
        cloud.revoke_invitation("acme", "a@example.com")
        cloud.resend_invitation("acme", "a@example.com")
        cloud.list_grants("acme")
        cloud.revoke_grant("acme", "g-1")
        cloud.delete_project("acme", "an")
        cloud.scaffold_project("acme", "an")
        cloud.list_boards("acme", "an")
        cloud.delete_connection("acme", "wh")
        cloud.refresh_connection_schema("acme", "wh")

    assert seen == [
        ("DELETE", "/api/orgs/acme"),
        ("GET", "/api/orgs/acme/members"),
        ("DELETE", "/api/orgs/acme/members/a@example.com"),
        ("POST", "/api/orgs/acme/members/a@example.com/role"),
        ("GET", "/api/orgs/acme/invitations"),
        ("DELETE", "/api/orgs/acme/invitations/a@example.com"),
        ("POST", "/api/orgs/acme/invitations/a@example.com/resend"),
        ("GET", "/api/orgs/acme/grants"),
        ("DELETE", "/api/orgs/acme/grants/g-1"),
        ("DELETE", "/api/orgs/acme/projects/an"),
        ("POST", "/api/orgs/acme/projects/an/scaffold"),
        ("GET", "/api/orgs/acme/projects/an/boards"),
        ("DELETE", "/api/orgs/acme/connections/wh"),
        ("POST", "/api/orgs/acme/connections/wh/schema-refresh"),
    ]


def test_invite_member_omits_the_role_field_when_none() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            201,
            json={"email": "a@example.com", "role": "creator", "already_member": False},
        )

    with client(handler) as cloud:
        cloud.invite_member("acme", "a@example.com")

    assert seen == [{"emails": "a@example.com"}]


def test_connect_url_declares_the_terminal_landing() -> None:
    """Every ``cloud_client`` caller finishes the project itself from the
    ``RepoPick`` row, so the browser must land on the terminal page after the
    pick, never on the New Project form. The flag is unconditional: drop it and
    a user who presses Create Project makes a duplicate of the CLI's project."""
    with client(lambda request: httpx.Response(200)) as cloud:
        assert (
            cloud.connect_url("acme")
            == "https://cloud.example/acme/github/connect/?landing=terminal"
        )


def test_export_audit_follows_every_page_with_the_filters() -> None:
    seen: list[httpx.Request] = []

    def event(n: int) -> dict[str, object]:
        return {
            "id": str(n),
            "created_at": "2026-10-08T00:00:00Z",
            "actor": None,
            "action": "public_access_changed",
            "project": None,
            "summary": f"event {n}",
        }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={"events": [event(page)], "next_page": 2 if page == 1 else None},
        )

    with client(handler) as cloud:
        events = cloud.export_audit("acme", {"actor": "a@x.com"})

    assert [e.id for e in events] == ["1", "2"]
    assert [r.url.params["actor"] for r in seen] == ["a@x.com", "a@x.com"]


@pytest.mark.parametrize("email", ["../../members/x", "a?b@c.com", "a#b@c.com"])
def test_path_segments_from_user_input_cannot_retarget_the_request(
    email: str,
) -> None:
    """httpx collapses dot segments and splits on ``?``/``#`` before sending,
    so an unencoded value in a path would silently hit another endpoint."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"message": "ok"})

    client(handler).change_team_member("acme", "t", email, add=False)

    assert seen == [f"/api/orgs/acme/teams/t/members/{quote(email, safe='')}"]


@pytest.mark.parametrize("name", [".", ".."])
def test_a_dot_segment_is_refused_before_any_request(name: str) -> None:
    """httpx resolves a bare ``.`` or ``..`` segment, which encoding leaves
    alone: ``teams/../members/x`` would remove x from the organization."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"message": "ok"})

    cloud = client(handler)
    with pytest.raises(InvalidPathSegment, match="is not a name"):
        cloud.change_team_member("acme", name, "a@x.com", add=False)
    with pytest.raises(InvalidPathSegment, match="is not a name"):
        cloud.set_public("acme", name, allow=True)
    assert seen == []
