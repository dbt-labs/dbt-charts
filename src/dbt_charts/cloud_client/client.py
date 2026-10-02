"""The HTTP client behind ``dct cloud`` — one method per Cloud setup endpoint.

Every method sends a bearer-authenticated JSON request under the deployment's
``/api/`` prefix and parses the answer with the model in ``contract.py`` that
the server serialized it from, so a shape can only change on both sides at
once. Nothing here decides anything: a refusal is the server's, verbatim, and
a body that is not the contract is an error rather than a best guess.

Imports nothing from ``dbt_charts.core`` and nothing from Django — this runs on
the CLI's side of the wire, where neither exists.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from dbt_charts.cloud_client.config import (
    TOKEN_ENV_VAR,
    CloudConfig,
    config_path,
    rejected_fields,
)
from dbt_charts.cloud_client.contract import (
    ApiError,
    AuthorizationServerMetadata,
    BoardList,
    ConnectionList,
    ConnectionTestResult,
    DeleteResult,
    DeviceAuthorization,
    DeviceToken,
    ErrorCode,
    GrantList,
    GrantRevokeResult,
    InvitationList,
    InviteResult,
    MemberList,
    MemberSummary,
    OrgList,
    OrgStatus,
    OrgSummary,
    ProjectList,
    ProjectSummary,
    RenderResult,
    RepoPick,
    SourceList,
    SyncResult,
)
from dbt_charts.cloud_client.errors import (
    ApiFailed,
    CredentialMissing,
    DeviceLoginFailed,
    InvalidCredential,
    PickTimedOut,
    SkewDirection,
    TransportFailed,
    UnexpectedResponse,
)

M = TypeVar("M", bound=BaseModel)

DEFAULT_TIMEOUT_SECONDS = 30.0
# How long `project connect` waits for the browser hop, and how often it looks.
PICK_WAIT_SECONDS = 300.0
PICK_POLL_SECONDS = 3.0
# The floor wait_for_pick clamps a caller-supplied poll interval to — below
# this is a poll rate no legitimate wait needs, and it protects against a
# --poll-interval close to zero hammering the API.
MIN_POLL_INTERVAL_SECONDS = 1.0

# The seeded public client `dct cloud login` authenticates as -- no secret,
# ever: the device grant's whole point is a client that can't hold one.
CLIENT_ID = "dct"
# What `dct cloud login` requests. Every verb the CLI can reach needs some
# atom of this, and the device grant asks once, up front, rather than
# escalating scope per verb.
DEVICE_LOGIN_SCOPE = "dashboards:read orgs:admin projects:admin connections:admin"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DISCOVERY_PATH = "/.well-known/oauth-authorization-server"
# RFC 8628's own backoff: a `slow_down` response widens the poll interval by
# this many seconds, not to a server-chosen value.
SLOW_DOWN_BACKOFF_SECONDS = 5.0


class CloudClient:
    """A session against one dbt charts Cloud deployment."""

    def __init__(
        self,
        host: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not token:
            raise CredentialMissing(config_path(), TOKEN_ENV_VAR)
        # A control character here becomes an illegal HTTP header byte on
        # send -- httpx/httpcore's own error for that quotes the raw header
        # (the bearer token) back verbatim. CloudConfig already strips a
        # trailing newline (the common case); anything left is rejected
        # outright rather than guessed at, before it ever reaches the network.
        if any(ord(char) < 0x20 for char in token):
            raise InvalidCredential(config_path())
        self.host = host.rstrip("/")
        self._http = httpx.Client(
            base_url=f"{self.host}/api",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> CloudClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._http.close()

    def connect_url(self, org_slug: str) -> str:
        """Where a user completes the GitHub install and repo pick for *org_slug*.

        ``landing=terminal`` is unconditional: every caller of this client
        finishes the project itself from the pick, so the browser must land on
        a page that says "go back to your terminal", not on the New Project
        form that would create a second project against the same repo.
        """
        return f"{self.host}/{org_slug}/github/connect/?landing=terminal"

    # --- organizations -----------------------------------------------------

    def list_orgs(self) -> OrgList:
        return self._request("GET", "/orgs", OrgList)

    def create_org(self, name: str, slug: str) -> OrgSummary:
        return self._request(
            "POST", "/orgs", OrgSummary, payload={"name": name, "slug": slug}
        )

    def org_status(self, org: str) -> OrgStatus:
        return self._request("GET", f"/orgs/{org}/status", OrgStatus)

    def delete_org(self, org: str) -> DeleteResult:
        return self._request("DELETE", f"/orgs/{org}", DeleteResult)

    # --- members and invitations --------------------------------------------

    def list_members(self, org: str) -> MemberList:
        return self._request("GET", f"/orgs/{org}/members", MemberList)

    def invite_member(
        self, org: str, email: str, role: str | None = None
    ) -> InviteResult:
        return self._request(
            "POST",
            f"/orgs/{org}/members",
            InviteResult,
            payload={"emails": email, **({"role": role} if role else {})},
        )

    def remove_member(self, org: str, email: str) -> DeleteResult:
        return self._request("DELETE", f"/orgs/{org}/members/{email}", DeleteResult)

    def set_member_role(self, org: str, email: str, role: str) -> MemberSummary:
        return self._request(
            "POST",
            f"/orgs/{org}/members/{email}/role",
            MemberSummary,
            payload={"role": role},
        )

    def list_invitations(self, org: str) -> InvitationList:
        return self._request("GET", f"/orgs/{org}/invitations", InvitationList)

    def revoke_invitation(self, org: str, email: str) -> DeleteResult:
        return self._request("DELETE", f"/orgs/{org}/invitations/{email}", DeleteResult)

    def resend_invitation(self, org: str, email: str) -> InviteResult:
        return self._request(
            "POST", f"/orgs/{org}/invitations/{email}/resend", InviteResult
        )

    # --- connector grants ----------------------------------------------------

    def list_grants(self, org: str) -> GrantList:
        return self._request("GET", f"/orgs/{org}/grants", GrantList)

    def revoke_grant(self, org: str, grant_id: str) -> GrantRevokeResult:
        return self._request(
            "DELETE", f"/orgs/{org}/grants/{grant_id}", GrantRevokeResult
        )

    # --- projects ----------------------------------------------------------

    def list_projects(self, org: str) -> ProjectList:
        return self._request("GET", f"/orgs/{org}/projects", ProjectList)

    def create_project(
        self,
        org: str,
        name: str,
        slug: str,
        git_remote_url: str,
        trunk_branch: str,
        git_subdirectory: str,
        *,
        git_username: str = "",
        git_password: str = "",
    ) -> ProjectSummary:
        """Connect a git URL. Blank ``git_username``/``git_password`` mean an
        anonymous (public) repository, which is the server's contract too."""
        return self._request(
            "POST",
            f"/orgs/{org}/projects",
            ProjectSummary,
            payload={
                "name": name,
                "slug": slug,
                "git_remote_url": git_remote_url,
                "trunk_branch": trunk_branch,
                "git_subdirectory": git_subdirectory,
                "git_username": git_username,
                "git_password": git_password,
            },
        )

    def create_project_from_pick(
        self,
        org: str,
        name: str,
        slug: str,
        trunk_branch: str,
        dbt_root_choice: str,
        git_subdirectory: str,
    ) -> ProjectSummary:
        """Finish the project the browser hop picked a repository for."""
        return self._request(
            "POST",
            f"/orgs/{org}/projects/from-pick",
            ProjectSummary,
            payload={
                "name": name,
                "slug": slug,
                "trunk_branch": trunk_branch,
                "dbt_root_choice": dbt_root_choice,
                "git_subdirectory": git_subdirectory,
            },
        )

    def sync_project(self, org: str, project: str) -> SyncResult:
        return self._request("POST", f"/orgs/{org}/projects/{project}/sync", SyncResult)

    def render_project(self, org: str, project: str, *, force: bool) -> RenderResult:
        """``force`` re-renders every board (``render/all``, a project-admin
        act); otherwise only boards with no render yet start."""
        path = f"/orgs/{org}/projects/{project}/render"
        return self._request("POST", path + "/all" if force else path, RenderResult)

    def delete_project(self, org: str, project: str) -> DeleteResult:
        return self._request("DELETE", f"/orgs/{org}/projects/{project}", DeleteResult)

    def scaffold_project(self, org: str, project: str) -> SyncResult:
        return self._request(
            "POST", f"/orgs/{org}/projects/{project}/scaffold", SyncResult
        )

    def list_boards(self, org: str, project: str) -> BoardList:
        return self._request("GET", f"/orgs/{org}/projects/{project}/boards", BoardList)

    # --- the GitHub handoff ------------------------------------------------

    def latest_pick(self, org: str) -> RepoPick:
        """The newest repository this caller picked in the browser for this org.

        404s until there is one — "not yet" and "never" are the same answer, by
        design on the server's side.
        """
        return self._request("GET", f"/orgs/{org}/github/pick", RepoPick)

    def wait_for_pick(
        self,
        org: str,
        timeout: float = PICK_WAIT_SECONDS,
        interval: float = PICK_POLL_SECONDS,
    ) -> RepoPick:
        """Block until the browser hop lands a pick, or say it never did.

        Bounded and in the foreground: the user is looking at the browser this
        is waiting on, so an unbounded poll would strand them with no output.

        ``interval`` reaches ``time.sleep`` directly; the CLI validates it
        against ``MIN_POLL_INTERVAL_SECONDS`` before a client is ever
        constructed, so no rewrite happens here.
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self.latest_pick(org)
            except ApiFailed as exc:
                if exc.api_error.code is not ErrorCode.NOT_FOUND:
                    raise
            if time.monotonic() >= deadline:
                raise PickTimedOut(self.connect_url(org), timeout)
            time.sleep(interval)

    # --- connections -------------------------------------------------------

    def list_connections(self, org: str) -> ConnectionList:
        return self._request("GET", f"/orgs/{org}/connections", ConnectionList)

    def create_connection(
        self, org: str, connection_type: str, fields: dict[str, str]
    ) -> ConnectionTestResult:
        """Create a connection and run its credential check in one call.

        ``fields`` is the warehouse type's own vocabulary — the server's
        per-type form owns which keys are required and says so field by field,
        so there is no second copy of that table here to drift from it.
        """
        return self._request(
            "POST",
            f"/orgs/{org}/connections",
            ConnectionTestResult,
            payload={"connection_type": connection_type, **fields},
        )

    def test_connection(self, org: str, connection: str) -> ConnectionTestResult:
        return self._request(
            "POST", f"/orgs/{org}/connections/{connection}/test", ConnectionTestResult
        )

    def delete_connection(self, org: str, connection: str) -> DeleteResult:
        return self._request(
            "DELETE", f"/orgs/{org}/connections/{connection}", DeleteResult
        )

    def refresh_connection_schema(self, org: str, connection: str) -> SyncResult:
        return self._request(
            "POST",
            f"/orgs/{org}/connections/{connection}/schema-refresh",
            SyncResult,
        )

    # --- sources -----------------------------------------------------------

    def list_sources(self, org: str, project: str) -> SourceList:
        return self._request(
            "GET", f"/orgs/{org}/projects/{project}/sources", SourceList
        )

    def map_source(
        self,
        org: str,
        project: str,
        name: str,
        connection: str,
        schema_override: str | None,
    ) -> SourceList:
        """Map ``name`` to ``connection``. ``schema_override=None`` omits the
        field entirely so the server preserves whatever override the source
        already has; ``""`` is sent and clears it deliberately -- these are
        not the same request."""
        return self._request(
            "POST",
            f"/orgs/{org}/projects/{project}/sources",
            SourceList,
            payload={
                "name": name,
                "connection": connection,
                **(
                    {"schema_override": schema_override}
                    if schema_override is not None
                    else {}
                ),
            },
        )

    # --- transport ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        model: type[M],
        payload: dict[str, Any] | None = None,
    ) -> M:
        """One round trip. ``payload`` is an unvalidated JSON body by nature —
        the server's forms are what decide whether its fields are right."""
        try:
            response = self._http.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise TransportFailed(self.host, exc) from exc
        url = str(response.request.url)
        if response.status_code >= 400:
            raise self._failure(response, url)
        try:
            return model.model_validate_json(response.content)
        except ValidationError as exc:
            # JSON that parsed and failed on its fields is a contract the two
            # ends disagree on; a body that is not JSON at all is not Cloud.
            # Every error's own "missing" vs. anything-else type says which
            # side is behind -- see SkewDirection's docstring.
            error_types = {error["type"] for error in exc.errors()}
            if "json_invalid" in error_types:
                skew = None
            elif error_types <= {"missing"}:
                skew = SkewDirection.CLOUD_OLDER
            else:
                skew = SkewDirection.CLOUD_NEWER
            raise UnexpectedResponse(
                url, response.status_code, str(exc), skew=skew
            ) from exc

    def _failure(
        self, response: httpx.Response, url: str
    ) -> ApiFailed | UnexpectedResponse:
        """The error body as the contract, or the fact that it wasn't one."""
        try:
            return ApiFailed(
                response.status_code, ApiError.model_validate_json(response.content)
            )
        except ValidationError as exc:
            return UnexpectedResponse(url, response.status_code, str(exc))


def client_from_config(config: CloudConfig, host: str | None = None) -> CloudClient:
    """Build a client for the configured deployment, or say why we can't."""
    return CloudClient(host=host or config.host, token=config.token)


# --- the OAuth 2.0 device grant (RFC 8628) ----------------------------------
#
# These four run before (or without) a bearer token, so they are free
# functions with their own short-lived httpx.Client rather than CloudClient
# methods -- CloudClient.__init__ refuses to exist without a token, and none
# of these calls sit under the deployment's /api/ prefix.


def discover_endpoints(
    host: str,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> AuthorizationServerMetadata:
    """The RFC 8414 document this host publishes its OAuth endpoints in."""
    host = host.rstrip("/")
    url = f"{host}{DISCOVERY_PATH}"
    with httpx.Client(transport=transport, timeout=timeout) as http:
        try:
            response = http.get(url)
        except httpx.HTTPError as exc:
            raise TransportFailed(host, exc) from exc
    if response.status_code >= 400:
        raise UnexpectedResponse(
            url, response.status_code, "the OAuth discovery document"
        )
    try:
        return AuthorizationServerMetadata.model_validate_json(response.content)
    except ValidationError as exc:
        if rejected_fields(exc) == "device_authorization_endpoint":
            raise DeviceLoginFailed(
                f"{host} does not offer device login: its OAuth discovery document"
                " has no device_authorization_endpoint."
            ) from exc
        raise UnexpectedResponse(
            url, response.status_code, rejected_fields(exc)
        ) from exc


def start_device_login(
    device_authorization_endpoint: str,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> DeviceAuthorization:
    """Begin the device grant: a code to show the user, one to poll with."""
    with httpx.Client(transport=transport, timeout=timeout) as http:
        try:
            response = http.post(
                device_authorization_endpoint,
                data={"client_id": CLIENT_ID, "scope": DEVICE_LOGIN_SCOPE},
            )
        except httpx.HTTPError as exc:
            raise TransportFailed(device_authorization_endpoint, exc) from exc
    if response.status_code >= 400:
        raise _device_login_failure(response, "Cloud refused the device login request")
    try:
        return DeviceAuthorization.model_validate_json(response.content)
    except ValidationError as exc:
        raise UnexpectedResponse(
            device_authorization_endpoint, response.status_code, rejected_fields(exc)
        ) from exc


def poll_device_token(
    token_endpoint: str,
    device_code: str,
    interval: float,
    expires_in: float,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> DeviceToken:
    """Poll until the user approves the login, denies it, or the code expires.

    ``sleep``/``now`` are injectable so a test never actually waits -- a real
    login legitimately blocks for as long as the user takes at the browser.
    """
    deadline = now() + expires_in
    with httpx.Client(transport=transport, timeout=timeout) as http:
        while True:
            sleep(interval)
            if now() >= deadline:
                raise DeviceLoginFailed(
                    "The login code expired before it was approved. Run `dct"
                    " cloud login` again."
                )
            try:
                response = http.post(
                    token_endpoint,
                    data={
                        "grant_type": DEVICE_GRANT_TYPE,
                        "device_code": device_code,
                        "client_id": CLIENT_ID,
                    },
                )
            except httpx.HTTPError as exc:
                raise TransportFailed(token_endpoint, exc) from exc
            if response.status_code < 400:
                try:
                    return DeviceToken.model_validate_json(response.content)
                except ValidationError as exc:
                    # Field names only: pydantic's own text quotes the body,
                    # and this body is the token.
                    raise UnexpectedResponse(
                        token_endpoint, response.status_code, rejected_fields(exc)
                    ) from exc
            error = _oauth_error_code(response)
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += SLOW_DOWN_BACKOFF_SECONDS
                continue
            if error == "access_denied":
                raise DeviceLoginFailed("Login was denied.")
            if error == "expired_token":
                raise DeviceLoginFailed(
                    "The login code expired before it was approved. Run `dct"
                    " cloud login` again."
                )
            raise _device_login_failure(response, "Cloud rejected the login")


def revoke_token(
    revocation_endpoint: str,
    token: str,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Revoke a token.

    RFC 7009: the endpoint answers 200 for an unknown or already-expired
    token too -- that is success, not a failure to report.
    """
    with httpx.Client(transport=transport, timeout=timeout) as http:
        try:
            response = http.post(
                revocation_endpoint, data={"token": token, "client_id": CLIENT_ID}
            )
        except httpx.HTTPError as exc:
            raise TransportFailed(revocation_endpoint, exc) from exc
    if response.status_code >= 400:
        raise UnexpectedResponse(
            revocation_endpoint,
            response.status_code,
            "the token revocation endpoint",
        )


def _oauth_error_code(response: httpx.Response) -> str:
    """The OAuth `error` field from a failure body; "" if unparseable."""
    try:
        body = response.json()
    except ValueError:
        return ""
    return body.get("error", "") if isinstance(body, dict) else ""


def _device_login_failure(response: httpx.Response, prefix: str) -> DeviceLoginFailed:
    """One line naming what Cloud refused -- never the request body."""
    code = _oauth_error_code(response)
    return DeviceLoginFailed(
        f"{prefix}: {code}" if code else f"{prefix} ({response.status_code})."
    )
