"""The Cloud setup API's wire contract — one definition in the source tree.

Cloud's ``/api/`` views serialize their responses through these models and the
``dct cloud`` client parses them back, so a response shape cannot change on the
server without the type the CLI reads changing in the same commit. That is why
this lives in the ``dbt-charts`` package rather than in ``apps/cloud``: the
monorepo lets Cloud import the client's types, never the reverse.

One definition keeps the *source tree* honest; it says nothing about the
wire. The ``dct`` parsing Cloud's answers is a PyPI install that lags the
deployed server, so every model here ignores fields it has never heard of —
``ContractModel`` says why. That is forward tolerance of an older client, not
a compatibility shim: no old shape or alias is kept, and removing or renaming
a response field still breaks every installed ``dct``, deliberately.

The OAuth device-grant models and the ``login``/``whoami`` result shapes are
not shared with Cloud's views: those are what the CLI reads from the
authorization server, or emits itself, and no Cloud view serializes them.

Nothing here may import ``dbt_charts.core`` or Django. These are transport
shapes — they carry no board semantics and run on the CLI's side of the wire,
where neither exists.

**Auth-failure shape.** ``ErrorCode`` encodes the decision this API pins for
every token surface after it:

- a missing or unusable credential is ``UNAUTHENTICATED`` (401);
- a credential whose *scope* does not cover the operation is
  ``INSUFFICIENT_SCOPE`` (403) and names the scope, because the refusal is a
  fact about the token alone — it is decided before any resource is looked up,
  so it identifies no org and enumerates nothing;
- every resource the caller may not see is ``NOT_FOUND`` (404), whether or not
  it exists. Org slugs are company names; a 403/404 split on them would hand any
  token holder a map of every org in the deployment.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Base of every shape in this module: an unknown field is ignored.

    Cloud deploys continuously and the ``dct`` parsing its answers is
    whatever the user last installed, so a field this client has never heard
    of is a newer Cloud, not bad input -- and must not become a parse error
    that kills every verb until they upgrade. Nothing here is a request body
    (those are plain dicts the server's forms validate), so ``extra="forbid"``
    would catch nothing on this side of the wire; a typo'd kwarg where the
    server *builds* one of these is pyright's to catch, not pydantic's.
    """

    model_config = ConfigDict(extra="ignore")


class ErrorCode(str, enum.Enum):
    """The machine-readable half of every error response."""

    UNAUTHENTICATED = "unauthenticated"
    INSUFFICIENT_SCOPE = "insufficient_scope"
    NOT_FOUND = "not_found"
    INVALID_REQUEST = "invalid_request"
    VALIDATION_FAILED = "validation_failed"
    CONFLICT = "conflict"
    CONNECTION_TEST_FAILED = "connection_test_failed"
    # Distinct from CONNECTION_TEST_FAILED: a create that hits this keeps the
    # row it saved, where a real failure discards it (connections/service.py).
    CONNECTION_TEST_INCONCLUSIVE = "connection_test_inconclusive"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"


class ApiError(ContractModel):
    """The one error body every endpoint returns."""

    code: ErrorCode = Field(description="Machine-readable failure kind.")
    message: str = Field(description="Human-readable failure summary.")
    field_errors: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per-field messages, keyed by request field name.",
    )


# --- OAuth device grant (RFC 8628) and discovery (RFC 8414) ----------------
#
# These three carry wire shapes a server we don't own defines -- Cloud's OAuth
# toolkit, not this package's API -- and name only the fields this client reads.


class AuthorizationServerMetadata(ContractModel):
    """The RFC 8414 document a host publishes its OAuth endpoints in."""

    token_endpoint: str = Field(description="Where to poll for a device token.")
    revocation_endpoint: str = Field(description="Where to revoke a token.")
    device_authorization_endpoint: str = Field(
        description="Where to start a device login."
    )


class DeviceAuthorization(ContractModel):
    """The answer to starting a device login: a code to show, one to poll with."""

    device_code: str = Field(
        description="The code this client polls the token endpoint with."
    )
    user_code: str = Field(
        description="The code the user types at the verification URI."
    )
    verification_uri: str = Field(description="Where the user approves the login.")
    verification_uri_complete: str | None = Field(
        default=None, description="verification_uri with the user_code pre-filled."
    )
    expires_in: int = Field(description="Seconds until device_code stops being valid.")
    interval: int = Field(default=5, description="Minimum seconds between polls.")


class DeviceToken(ContractModel):
    """The answer to a successful token poll."""

    access_token: str = Field(description="The bearer token to store.")
    token_type: str = Field(description="Always `Bearer`.")
    expires_in: int | None = Field(default=None, description="Seconds until expiry.")
    scope: str = Field(default="", description="Scopes actually granted.")


class LoginResult(ContractModel):
    """What `dct cloud login` reports on success. Never the token."""

    host: str = Field(description="The Cloud deployment now signed in to.")
    organizations: list[OrgSummary] = Field(
        default_factory=list, description="Organizations the new token can reach."
    )


class DeviceLoginStarted(ContractModel):
    """What `dct cloud login --start` reports: the approval URL, never a
    token -- the device grant hasn't been approved yet."""

    host: str = Field(description="The Cloud deployment this login is against.")
    verification_uri: str = Field(description="Where the user approves the login.")
    verification_uri_complete: str | None = Field(
        default=None, description="verification_uri with the user_code pre-filled."
    )
    user_code: str = Field(
        description="The code the user types at the verification URI."
    )
    expires_in: int = Field(description="Seconds until the code stops being valid.")


class ProjectConnectStarted(ContractModel):
    """What `dct cloud project connect --start` reports: the install/pick
    URL, never a project -- the browser pick hasn't landed yet."""

    org: str = Field(description="The org this connect is against.")
    url: str = Field(
        description="Where to install the GitHub App and pick a repository."
    )


class WhoAmI(ContractModel):
    """What `dct cloud whoami` reports."""

    host: str = Field(description="The Cloud deployment in use.")
    credential_source: Literal["env", "config"] = Field(
        description="Whether the token came from DCT_CLOUD_TOKEN or the config file."
    )
    organizations: list[OrgSummary] = Field(
        default_factory=list, description="Organizations this credential can reach."
    )


class OrgSummary(ContractModel):
    """One organization, as the caller sees it."""

    slug: str = Field(description="URL-safe organization identifier.")
    name: str = Field(description="Display name.")
    role: str = Field(description="The caller's membership role in this org.")


class OrgList(ContractModel):
    organizations: list[OrgSummary] = Field(
        default_factory=list, description="Organizations the caller belongs to."
    )


class MemberSummary(ContractModel):
    """One organization member."""

    email: str = Field(description="The member's account email.")
    name: str = Field(description="Display name; empty if never set.")
    role: str = Field(description="Membership role.")
    joined_at: datetime = Field(description="When the membership was created.")


class MemberList(ContractModel):
    members: list[MemberSummary] = Field(
        default_factory=list, description="The organization's members."
    )


class InviteResult(ContractModel):
    """The answer to inviting one address."""

    email: str = Field(description="The invited address.")
    role: str = Field(description="Role the invitation confers on acceptance.")
    already_member: bool = Field(
        description="True if the address already belongs to the organization."
    )


class InvitationSummary(ContractModel):
    """One pending invitation."""

    email: str = Field(description="The invited address.")
    role: str = Field(description="Role the invitation confers on acceptance.")
    created_at: datetime = Field(description="When the invitation was created.")
    expires_at: datetime = Field(
        description="When the invitation stops being redeemable."
    )


class InvitationList(ContractModel):
    invitations: list[InvitationSummary] = Field(
        default_factory=list, description="Pending invitations."
    )


class DeleteResult(ContractModel):
    """The answer to deleting a resource."""

    deleted: bool = Field(description="Whether this call deleted the resource.")
    message: str = Field(description="What happened, in one line.")


class GrantSummary(ContractModel):
    """One live connector session against an organization."""

    grant_id: str = Field(description="The grant's id.")
    user_email: str = Field(description="The account that consented.")
    application_name: str = Field(description="The OAuth client this grant authorizes.")
    scopes: list[str] = Field(description="Scopes consented for this client.")
    created_at: datetime = Field(description="When this grant was recorded.")


class GrantList(ContractModel):
    grants: list[GrantSummary] = Field(
        default_factory=list, description="Live connector grants in this organization."
    )


class GrantRevokeResult(ContractModel):
    """The answer to revoking a connector grant."""

    revoked: bool = Field(description="Whether this call revoked the grant.")
    self_revoked: bool = Field(
        description=(
            "True when the revoked grant also authorized the credential making"
            " this very call — that credential dies with it."
        )
    )
    other_organizations_affected_count: int = Field(
        default=0,
        description=(
            "How many OTHER organizations this revoke also disconnected — the"
            " client shares one credential per (user, application), so"
            " revoking one grant kills every sibling grant for the same pair."
            " Always accurate, regardless of which of those organizations the"
            " caller can see."
        ),
    )
    other_organizations_affected: list[str] = Field(
        default_factory=list,
        description=(
            "Names of the affected organizations the CALLER administers —"
            " never a full roster: naming an organization the caller has no"
            " admin relationship with would disclose its existence to them."
            " May be shorter than `other_organizations_affected_count`."
        ),
    )
    message: str = Field(description="What happened, in one line.")


class BoardSummary(ContractModel):
    """One board's render state, for the read-only board listing."""

    slug: str = Field(description="URL-safe board identifier.")
    title: str = Field(description="Board title.")
    render_status: str = Field(
        description=(
            "`ready`, `errored` (rendered, but some charts came back as error "
            "cards), `warning` (last render failed), `blocked` (a source this "
            "board names maps to a connection that has not passed its last "
            "test, so no render of this board can hold real data; a board "
            "naming no source is judged against every source its project "
            "maps), or `not_rendered`. "
            "`blocked` outranks the render-derived values: a render over a "
            "credential that never worked describes the credential."
        )
    )
    error: str = Field(
        default="",
        description=(
            "Why this board is not serving correct content — the failed "
            "render's text under `warning`, the first chart diagnostic under "
            "`errored`, and under `blocked` the source plus, for a connection "
            "this token may list, its slug, whether it failed its last test "
            "or has never been tested, and the `dct cloud connection test` "
            "command. Never the warehouse driver's own error text: run that "
            "command to see it. Empty otherwise."
        ),
    )
    rendered_at: datetime | None = Field(
        default=None,
        description=(
            "When this board's most recent complete render finished. Null "
            "for a board with no complete render. A Cloud older than this "
            "field omits the key entirely, which the CLI reports as unknown "
            "rather than as no-render; `--json` cannot tell the two apart."
        ),
    )
    commit: str | None = Field(
        default=None,
        description=(
            "Full SHA of the commit whose tree that render read the board "
            "from, in Cloud's own copy of the repository -- on a project "
            "edited in Cloud that is a work-branch commit, not one the "
            "caller has locally. It advances when the board's own content "
            "changes, not on every commit: a board a push did not touch is "
            "not normally re-rendered, so it keeps the commit it was last "
            "rendered from (a board whose query results have their own TTL "
            "re-renders on that schedule, and moves it). "
            "Null for a board with no complete render, and for a render made "
            "before Cloud recorded the commit; never guessed after the fact. "
            "A Cloud older than this field omits the key entirely. "
            "`dct cloud boards` prints the 7-character prefix."
        ),
    )
    url: str = Field(description="Where to view this board.")


class BoardList(ContractModel):
    boards: list[BoardSummary] = Field(
        default_factory=list, description="Boards visible to the caller."
    )


class ProjectSummary(ContractModel):
    """One connected project."""

    slug: str = Field(description="URL-safe project identifier.")
    name: str = Field(description="Display name.")
    repo_label: str = Field(
        description="Credential-free label for the repo the boards come from."
    )
    trunk_branch: str = Field(description="Branch Cloud pulls from.")
    work_branch: str = Field(description="Branch Cloud commits edits to.")
    git_subdirectory: str = Field(
        description="Path to the dbt project inside the repo; empty for the root."
    )
    unmapped_source_count: int | None = Field(
        description=(
            "Declared sources with no connection behind them yet; null when "
            "config_error is set, because the count is then unknown. This "
            "listing carries no stage, so the field itself has to say so."
        )
    )
    config_error: str | None = Field(
        default=None,
        description=(
            "Why Cloud could not read this project's dbt_charts.yml, when it "
            "could not — the unmapped count beside it is null, not zero."
        ),
    )


class ProjectList(ContractModel):
    projects: list[ProjectSummary] = Field(
        default_factory=list, description="Projects in the organization."
    )


class ConnectionSummary(ContractModel):
    """One warehouse connection. Carries no credential material, ever."""

    slug: str = Field(description="URL-safe connection identifier.")
    name: str = Field(description="Display name.")
    connection_type: str = Field(description="Warehouse kind, e.g. bigquery.")
    is_active: bool = Field(description="Whether boards may use this connection.")
    last_test_success: bool | None = Field(
        default=None, description="Result of the most recent test; null if untested."
    )
    last_test_error: str = Field(
        default="", description="Failure text from the most recent test."
    )


class ConnectionList(ContractModel):
    connections: list[ConnectionSummary] = Field(
        default_factory=list, description="Connections the caller may use."
    )


class ConnectionTestResult(ContractModel):
    """The outcome of asking Cloud to reach a warehouse."""

    success: bool = Field(description="Whether the warehouse answered.")
    message: str = Field(
        description="Authored, classified failure copy (or the host guard's own"
        " message); empty on success."
    )
    connection: ConnectionSummary = Field(description="The connection that was tested.")


class SourceSummary(ContractModel):
    """One `sources:` name declared by the project's dbt_charts.yml."""

    name: str = Field(description="Source name as boards reference it.")
    connection_slug: str | None = Field(
        default=None, description="Connection backing this source; null if unmapped."
    )
    schema_override: str = Field(
        default="", description="Schema this source resolves against, when overridden."
    )
    is_default: bool = Field(description="Whether boards use this source by default.")


class SourceList(ContractModel):
    sources: list[SourceSummary] = Field(
        default_factory=list, description="The project's declared sources."
    )
    unmapped_count: int = Field(description="How many have no connection yet.")
    file_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Sources Cloud resolves from the repo itself — the csv/json/parquet "
            "entries of dbt_charts.yml. They appear in no `sources` entry and "
            "never in `unmapped_count`: the files are in the branch Cloud "
            "already synced, so there is nothing to map them to."
        ),
    )


class DbtRoot(ContractModel):
    """A dbt project root discovered in a picked repository."""

    path: str = Field(description="Folder holding dbt_project.yml; empty for the root.")
    is_dct: bool = Field(description="Whether the folder also declares dbt_charts.yml.")


# The two values a project-create request may send for ``dbt_root_choice``
# instead of a discovered ``DbtRoot.path``. They are wire vocabulary, not an
# implementation detail of either end: the browser form posts them from a radio
# group and the CLI posts them from its flags, and the same server-side form
# reads both. Defined here so a rename cannot land on one end alone — the sole
# remaining copy is the radio group's own ``value=`` in
# ``templates/projects/_project_create_form_fields.html``, where a template
# cannot import a constant.
DBT_ROOT_REPO_ROOT = "__root__"
DBT_ROOT_OTHER = "__other__"


class RepoPick(ContractModel):
    """The repository a user picked in the browser, for the CLI to resume from.

    Records the outcome of the browser flow; it is not a way to make a pick.
    Repository authorization still comes from the user's own GitHub OAuth
    listing, in the browser, every time.
    """

    repo_id: str = Field(description="Cloud's id for the picked repository.")
    full_name: str = Field(description="owner/name of the picked repository.")
    default_branch: str = Field(description="The repository's own default branch.")
    private: bool = Field(description="Whether the repository is private.")
    picked_at: datetime = Field(description="When the browser pick completed.")
    dbt_roots: list[DbtRoot] = Field(
        default_factory=list, description="dbt project roots found in the repository."
    )


class SyncResult(ContractModel):
    """The answer to "pull this project's repo"."""

    queued: bool = Field(description="Whether this call enqueued a new sync.")
    message: str = Field(description="What happened, in one line.")


class RenderResult(ContractModel):
    """The answer to "render this project's unrendered boards"."""

    started: int = Field(description="Board renders this call started.")
    unrendered_remaining: int = Field(
        description="Boards still unrendered after this batch."
    )
    blocked: str = Field(
        default="",
        description=(
            "Why the first `blocked` board in the listing was not started:"
            " the source it names, and the connection slug plus the `dct"
            " cloud connection test` command when this token may list that"
            " connection. Boards over passing connections or repo file"
            " sources start beside it. Empty when nothing blocks any board,"
            " which is not the same as"
            " everything being rendered: a project whose every board's last"
            " render failed also reports `started: 0` with this empty. Read"
            " `dct cloud status`'s per-project counts for that case."
        ),
    )


class SetupStage(str, enum.Enum):
    """One step of the org setup state machine, in completion order.

    ``MISSING_PROJECT`` < ``UNSYNCED`` < ``UNTESTED_CONNECTION`` <
    ``UNMAPPED_SOURCES`` < ``MISSING_BOARDS`` < ``UNRENDERED_BOARDS`` <
    ``DONE``. The connection and mapping gates read ``dbt_charts.yml``, which
    exists whether or not a board does, so they come first; a board existing
    is the precondition of boards rendering, so ``MISSING_BOARDS`` sits
    directly before that stage. The "no org yet"
    stage the initiative's spec describes belongs to the ``dct cloud status``
    verb, never to this endpoint: an org-scoped GET cannot express "there is
    no org" (its URL already names one), so ``MISSING_PROJECT`` is the floor
    an org-scoped status can report.
    """

    MISSING_PROJECT = "missing_project"
    UNSYNCED = "unsynced"
    UNTESTED_CONNECTION = "untested_connection"
    UNMAPPED_SOURCES = "unmapped_sources"
    MISSING_BOARDS = "missing_boards"
    UNRENDERED_BOARDS = "unrendered_boards"
    DONE = "done"


class ProjectStatus(ContractModel):
    """One project's own place in the setup state machine.

    ``unrendered_board_count`` counts only boards a render batch would
    actually start (``not_rendered``, no attempt in flight) — a board whose
    last attempt FAILED is never counted here, so this reaching 0 never hides
    a board a retry can't touch, and neither is a board already mid-render
    (see ``rendering_board_count``). ``failed_board_count``/``failed_board_slugs``
    report those separately: they never block ``stage`` from reaching
    ``DONE`` (nothing left for ``dct cloud render`` to start), but they are
    still an action item — see ``OrgStatus.next_step``.

    ``ready_board_count``, ``errored_board_count`` and ``errored_board_slugs``
    are ``None``, not a real count, when Cloud predates the deploy that added
    them — a required-but-absent field on a response that otherwise parses
    is a Cloud deploy one step behind this dct, the mirror case of
    ``ContractModel``'s ``extra="ignore"``. Older fields
    (``board_count``, ``unrendered_board_count``, ``rendering_board_count``,
    ``failed_board_count``, ``failed_board_slugs``) predate this endpoint's
    first release and stay required: any Cloud answering this URL at all
    already sends them. The ``None`` meaning is parse-side only — the Cloud
    producer constructing this model always has a real count and should
    never pass ``None`` for one.
    """

    slug: str = Field(description="URL-safe project identifier.")
    stage: SetupStage = Field(description="This project's own setup stage.")
    synced: bool = Field(description="Whether the trunk branch has completed a sync.")
    unmapped_source_count: int = Field(
        description=(
            "Declared sources with no connection behind them yet. 0 and "
            "meaningless when config_error is set: what the branch declares "
            "is then unknown, not empty."
        )
    )
    config_error: str | None = Field(
        default=None,
        description=(
            "Why Cloud could not read this project's dbt_charts.yml, when it "
            "could not. The source counts beside it are unknown rather than "
            "zero, so the stage is held at unmapped_sources or earlier and "
            "this project can never report done until the file reads."
        ),
    )
    file_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Sources resolved from the repo — the csv/json/parquet entries of "
            "dbt_charts.yml. Never counted in unmapped_source_count and never "
            "a reason this project is not done: they need no connection."
        ),
    )
    board_count: int = Field(
        description=(
            "Boards visible to the caller in this project. A `blocked` board "
            "(see `BoardSummary.render_status`) counts here and in none of "
            "`ready`, `unrendered`, `failed` or `errored` below, since none "
            "of those says anything true about it until the connection its "
            "sources map to passes its test; its siblings over passing "
            "connections keep their own counts. `rendering` is the exception "
            "and can include a blocked board: a render already in flight is "
            "a fact about the queue, not a claim about the board. `stage` and "
            "`next_step` name the test to run."
        )
    )
    ready_board_count: int | None = Field(
        default=None,
        description=(
            "Visible boards whose latest render completed with every chart "
            "clean. Its own count, not board_count "
            "minus the rest — a board already serving a render while a "
            "re-render is in flight is both ready and rendering. ``None`` "
            "means this Cloud predates the deploy that added this count and "
            "does not report it yet — not zero ready boards."
        ),
    )
    unrendered_board_count: int = Field(
        description="Visible, startable boards with no successful render yet."
    )
    rendering_board_count: int = Field(
        description=(
            "Visible boards with a render already in flight (PENDING) and "
            "young enough to trust — neither startable nor failed. While "
            "this is nonzero the stage cannot be DONE: a status read taken "
            "between starting a render and it finishing must not report the "
            "project as finished. A PENDING render older than the pipeline's "
            "own stale-worker bound counts toward failed_board_count "
            "instead, never here — a dead worker must not pin a project at "
            "this stage forever."
        )
    )
    failed_board_count: int = Field(
        description=(
            "Visible boards whose most recent render attempt FAILED, plus "
            "any PENDING render stale enough to presume its worker died."
        )
    )
    failed_board_slugs: list[str] = Field(
        default_factory=list, description="Slugs of the boards counted above."
    )
    errored_board_count: int | None = Field(
        default=None,
        description=(
            "Visible boards whose most recent successful render carried "
            "per-chart errors — the board built, but some or all of its "
            "charts are error cards. Distinct from failed_board_count: that "
            "render produced no board at all, this one produced a broken "
            "one. Neither is startable, so neither moves the stage; both "
            "put a next_step on a DONE org. ``None`` means this Cloud "
            "predates the deploy that added this count and does not report "
            "it yet — not zero errored boards."
        ),
    )
    errored_board_slugs: list[str] | None = Field(
        default=None,
        description=(
            "Slugs of the boards counted above. ``None`` has the same "
            "meaning as errored_board_count being ``None``, not an empty list."
        ),
    )


class OrgStatus(ContractModel):
    """The agent contract: one org-scoped read of the setup state machine.

    Two concerns, deliberately split:

    - ``stage`` and ``next_step`` are ORG TRUTH — computed from the org's
      full, unfiltered state, never narrowed by which scopes this particular
      caller's credential happens to admit. An org that finished setup is
      ``done`` for every caller, including one whose own token grants no
      ``connections.*`` atom (a bare ``dashboards:read`` token, or this
      endpoint's own minimum scope); reporting a caller-narrowed
      ``untested_connection`` instead would hand back a ``next_step`` the
      caller cannot itself run. ``next_step`` may legitimately name an admin
      act for exactly this reason: it describes what the ORG needs next, not
      what this caller is personally permitted to do about it. Disclosure
      stays coarse either way: a stage name, an instruction, and at most the
      slug of a connection an org project's sources are already mapped to —
      never a connection's type, host, credentials, or test output, and never
      the existence of one no project points at. That one slug is what makes
      ``untested_connection`` actionable: the gate is those specific
      connections passing their test, so an instruction that cannot name them
      is one a client can follow forever without the stage moving.
    - ``connection_count``/``tested_connection_count`` are CALLER-RELATIVE,
      resolved through ``usable_connections`` — the same treatment board
      counts always had. A setup-driving agent normally holds admin scopes,
      so its view is the complete one; a narrower caller (e.g. an org-tier
      Viewer, who holds no ``connections.*`` atom) sees zero here, exactly as
      it would on the connections page — never more. This is purely a
      disclosure limit on the two count fields; it does not feed the stage
      machine.

    ``stage``/``next_step`` are otherwise the org's own answer: the earliest
    incomplete stage across ``projects`` (``SetupStage`` order), so a client
    following ``next_step`` alone always closes the org's actual bottleneck. A
    project's own stage gates on the connections ITS sources are actually
    mapped to, not merely on the org having some tested connection — a
    project mapped to a different, untested connection is not done just
    because a sibling connection passed its test elsewhere in the org.

    ``next_step`` is null only when every project is fully ``DONE`` *and* no
    project has a board that is not serving correct content — one whose
    render failed, or one that rendered carrying per-chart errors. Two cases
    never tell a client to call ``dct cloud render`` when render will not
    touch anything: a project stuck only on failed or errored boards reports
    ``stage: done`` (nothing left a retry would start) with a non-null
    generic ``next_step`` — it says such boards exist and points at
    ``dct cloud boards``; the board paths
    themselves appear only in each project's caller-scoped
    ``failed_board_slugs``, so org-truth advice never names a board the
    caller's ACL hides — and, on the same rule, the untested-connection
    ``next_step`` names only connections this caller can list, falling back
    to a generic pointer at ``dct cloud connections`` when it can list none;
    a project with renders already in flight (``rendering_board_count`` > 0,
    no boards left it could start) reports ``stage: unrendered_boards`` still
    — never ``done`` — with a ``next_step`` that says to poll again, not to
    re-render. A PENDING render whose age exceeds the render pipeline's own
    stale-worker bound is treated as failed for both purposes (it folds into
    ``failed_board_count``, never ``rendering_board_count``), so a dead
    worker cannot pin a project at ``unrendered_boards`` forever.
    """

    stage: SetupStage = Field(
        description="Earliest incomplete stage across all projects, as org truth."
    )
    next_step: str | None = Field(
        default=None,
        description=(
            "What the ORG needs next; null only when every project is done "
            "with no failed or chart-errored boards left. May name an admin "
            "act this caller cannot itself perform."
        ),
    )
    connection_count: int = Field(
        description="Connections in this organization the caller may use."
    )
    tested_connection_count: int = Field(
        description="Of those, how many have a passing last test."
    )
    projects: list[ProjectStatus] = Field(
        default_factory=list, description="Per-project setup state, sorted by slug."
    )
