"""`dct cloud` — operate dbt charts Cloud from the terminal.

Thin over ``dbt_charts.cloud_client``: parse arguments, resolve which org and
project the call is about, hand the values to one client method, print what
came back. No decision about what is valid lives here — the API's forms answer
that, field by field, and this prints their answer.

**Signing in.** ``dct cloud login`` runs the OAuth 2.0 device grant (RFC 8628)
against the host's discovered endpoints: it prints a code and a URL, waits for
the browser approval, then stores the token. A script that cannot block for
that approval splits it: ``login --start`` prints the approval URL to stdout
and exits immediately (persisting the pending grant), and a later
``login --wait`` blocks for the approval and finishes the login.
``dct cloud logout`` revokes and clears the credential; ``dct cloud whoami``
reports the host, where the credential came from, and what it can reach. A
token may also come from the ``DCT_CLOUD_TOKEN`` environment variable (CI,
agents) or a hand-written ``token:`` line in the user config file — every
verb that needs one says exactly that when it is missing.

**Connecting a project.** ``project connect`` (with no ``--git-url``) hands a
repository pick to the browser and waits for it. The same split applies:
``connect --start`` prints the install/pick URL and exits immediately
(persisting the pending connect), and a later ``connect --wait`` blocks for
the pick and finishes the project.

**Secrets never reach argv** (the initiative's warehouse-secret decision):
``connection create`` takes key material from a file, from stdin, or from a
named environment variable, and nothing secret is written to the config or to
output.
"""

from __future__ import annotations

import os
import sys
import time
import webbrowser
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import typer
from pydantic import BaseModel
from rich.markup import escape
from rich.table import Table

from dbt_charts.cli._console import dct_console, is_plain_output
from dbt_charts.cli._json_output import print_json_result as _print_json_result
from dbt_charts.cli._parsing import parse_kv_pairs
from dbt_charts.cloud_client.client import (
    MIN_POLL_INTERVAL_SECONDS,
    PICK_POLL_SECONDS,
    PICK_WAIT_SECONDS,
    CloudClient,
    client_from_config,
    discover_endpoints,
    poll_device_token,
    revoke_token,
    start_device_login,
)
from dbt_charts.cloud_client.config import (
    TOKEN_ENV_VAR,
    CloudConfig,
    PendingConnect,
    PendingLogin,
    clear_pending_connect,
    clear_pending_login,
    credential_source,
    load_config,
    read_config,
    read_pending_connect,
    read_pending_login,
    save_config,
    save_pending_connect,
    save_pending_login,
)
from dbt_charts.cloud_client.context import (
    REFUSE_DEFAULT_CONNECT,
    REFUSE_DEFAULT_DESTRUCTIVE,
    CloudContext,
    git_remotes,
    repo_key,
    repo_matches,
    resolve_org,
    resolve_project,
)
from dbt_charts.cloud_client.contract import (
    DBT_ROOT_OTHER,
    DBT_ROOT_REPO_ROOT,
    AuthorizationServerMetadata,
    BoardList,
    BoardSummary,
    ConnectionList,
    DeleteResult,
    DeviceAuthorization,
    DeviceLoginStarted,
    DeviceToken,
    GrantList,
    InvitationList,
    LoginResult,
    MemberList,
    OrgList,
    OrgStatus,
    ProjectConnectStarted,
    ProjectList,
    ProjectSummary,
    RepoPick,
    SourceList,
    WhoAmI,
)
from dbt_charts.cloud_client.errors import (
    CloudError,
    EnvCredentialActive,
    NoPendingConnect,
    NoPendingLogin,
)
from dbt_charts.cloud_client.published_to import (
    DBT_CHARTS_YML,
    find_local_project_dir,
    published_to_url,
    resolve_published_to,
    set_published_to,
)

err_console = dct_console(stderr=True)

# The credential field each warehouse type expects key material in. Only types
# that take a key file appear; the server's per-type form owns everything else.
KEYFILE_FIELDS = {"bigquery": "credentials_json", "snowflake": "private_key"}
# Field names that carry credentials. `--set` refuses all of them: a secret in
# argv is in the shell history and the process listing.
SECRET_FIELDS = frozenset(
    {"password", "credentials_json", "private_key", "private_key_passphrase"}
)

OrgOption = Annotated[
    str | None,
    typer.Option("--org", help="Organization slug (default: inferred, then `use`)"),
]
# For verbs that pass a ``refuse_default`` reason: same flag, honest help.
RefusingOrgOption = Annotated[
    str | None,
    typer.Option(
        "--org",
        help="Organization slug (inferred from this repo, else required;"
        " never the `use` default)",
    ),
]
ProjectOption = Annotated[
    str | None,
    typer.Option("--project", help="Project slug (default: inferred, then `use`)"),
]
HostOption = Annotated[
    str | None,
    typer.Option(
        "--host",
        envvar="DCT_CLOUD_HOST",
        help="Cloud deployment to talk to (default: the configured host)",
    ),
]
JsonOption = Annotated[
    bool, typer.Option("--json", help="Emit the raw API response as JSON")
]
YesOption = Annotated[
    bool,
    typer.Option(
        "--yes", help="Skip the confirmation prompt (required, non-interactively)"
    ),
]

cloud_app = typer.Typer(
    name="cloud",
    help="""\b
Operate dbt charts Cloud from the terminal.

  dct cloud login                  Sign in (once per machine)
  dct cloud status                 What is set up, and what is missing next
  dct cloud use acme/analytics     Pin the org and project for this machine
  dct cloud project connect        Connect this repository as a project

Every verb takes --json. Context resolves from --org/--project, then this
repository's own published_to (written by `project connect`), then its git
remote, then `dct cloud use`.
""",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
org_app = typer.Typer(name="org", help="Create organizations.", no_args_is_help=True)
project_app = typer.Typer(
    name="project", help="Connect and sync projects.", no_args_is_help=True
)
connection_app = typer.Typer(
    name="connection",
    help="Create and test warehouse connections.",
    no_args_is_help=True,
)
source_app = typer.Typer(
    name="source", help="Point a board source at a connection.", no_args_is_help=True
)
member_app = typer.Typer(
    name="member", help="Manage organization members.", no_args_is_help=True
)
invite_app = typer.Typer(
    name="invite", help="Manage pending invitations.", no_args_is_help=True
)
grant_app = typer.Typer(
    name="grant", help="Manage connector grants.", no_args_is_help=True
)
cloud_app.add_typer(org_app, name="org")
cloud_app.add_typer(project_app, name="project")
cloud_app.add_typer(connection_app, name="connection")
cloud_app.add_typer(source_app, name="source")
cloud_app.add_typer(member_app, name="member")
cloud_app.add_typer(invite_app, name="invite")
cloud_app.add_typer(grant_app, name="grant")


# =============================================================================
# Context and failure plumbing
# =============================================================================


def print_json_result(result: BaseModel) -> None:
    """``dct cloud``'s one ``--json`` writer: no ``exclude_none``.

    Every model here is ``dbt_charts.cloud_client.contract`` -- the exact
    shape ``apps/cloud/apps/api/transport.py`` serializes server-side
    (``model_dump(mode="json")``, no exclusion) -- so the CLI must emit the
    same keys the HTTP API answers with. Dropping a ``None`` field would let
    a client's ``--json`` and a direct API call disagree about whether
    ``last_test_success: null`` (untested) is present at all.
    """
    _print_json_result(result, exclude_none=False)


@contextmanager
def _reporting(as_json: bool) -> Generator[None]:
    """Turn any CloudError raised inside into this CLI's one failure output.

    Every verb goes through here, including the ones that touch no network:
    `dct cloud use` still reads the config file, and an unreadable one must
    reach the user as the same error every other verb reports, not as a
    traceback.
    """
    try:
        yield
    except CloudError as exc:
        _fail(exc, as_json)


@contextmanager
def _cloud(
    host: str | None, as_json: bool
) -> Generator[tuple[CloudClient, CloudConfig]]:
    """An open client plus the stored config, with every failure formatted once."""
    with _reporting(as_json):
        config = load_config()
        with client_from_config(config, host) as client:
            yield client, config


def _oauth_transport() -> httpx.BaseTransport | None:
    """The transport ``login``/``logout`` send their un-authenticated OAuth
    requests over. Real network in production; tests replace this to route
    through a fake one, the same seam ``client_from_config`` is for the
    bearer-authed calls."""
    return None


# Injectable so a test never actually waits on `dct cloud login`'s poll loop.
_sleep = time.sleep


def _non_interactive() -> bool:
    """True when there is no human here to type a confirmation.

    Not just ``sys.stdin.isatty()``: an agent harness (Claude Code, etc.)
    commonly drives a real pty, so stdin reports a TTY even though the
    thing on the other end is a process that will hang forever waiting for
    a keystroke that never comes. ``is_plain_output()`` is the same
    CLAUDECODE-and-friends detection every other agent-context check in this
    CLI already uses (``_console.py``) -- reused here rather than a second
    copy of the env-var list.
    """
    return not sys.stdin.isatty() or is_plain_output()


def _confirm_destructive(kind: str, coordinate: str, yes: bool) -> None:
    """GitHub-style destructive confirm: type the full org/slug coordinate,
    or pass --yes.

    Always names the resolved coordinate, ``--yes`` included -- a
    non-interactive run still prints what it is about to delete before
    acting, the one chance to notice a stale ``dct cloud use`` default
    before it deletes the wrong thing. Refuses to guess in a
    non-interactive context (``_non_interactive()``): with no --yes, exits
    non-zero having done nothing rather than blocking on a prompt nobody
    can answer.
    """
    err_console.print(
        f"This permanently deletes the {kind} [bold]{escape(coordinate)}[/bold]"
        " and all its data."
    )
    if yes:
        return
    if _non_interactive():
        raise typer.BadParameter(
            f"deleting a {kind} is destructive; pass --yes to confirm"
            " non-interactively.",
            param_hint="--yes",
        )
    typed = typer.prompt(f"Type {coordinate!r} to confirm")
    if typed != coordinate:
        err_console.print("[red]Confirmation did not match; nothing was deleted.[/red]")
        raise typer.Exit(1)


def _fail(exc: CloudError, as_json: bool) -> NoReturn:
    """Report a failure in the shape the caller asked for, and exit non-zero."""
    if as_json:
        print_json_result(exc.as_api_error())
    else:
        # soft_wrap: these messages carry paths, URLs, and `--org x --project y`
        # lines a reader copies — console wrapping would break them mid-token.
        err_console.print(f"[red]Error:[/red] {escape(str(exc))}", soft_wrap=True)
    raise typer.Exit(1)


def _org(
    client: CloudClient,
    config: CloudConfig,
    org_flag: str | None,
    refuse_default: str | None = None,
) -> str:
    return resolve_org(
        client,
        org_flag,
        config,
        git_remotes(Path.cwd()),
        Path.cwd(),
        refuse_default=refuse_default,
    )


def _context(
    client: CloudClient,
    config: CloudConfig,
    org_flag: str | None,
    project_flag: str | None,
    refuse_default: str | None = None,
) -> CloudContext:
    return resolve_project(
        client,
        org_flag,
        project_flag,
        config,
        git_remotes(Path.cwd()),
        Path.cwd(),
        refuse_default=refuse_default,
    )


# =============================================================================
# Verbs
# =============================================================================


@cloud_app.command("use")
def use(
    target: Annotated[
        str, typer.Argument(metavar="ORG(/PROJECT)", help="Default context to store")
    ],
) -> None:
    """Set the default org (and project) for commands run on this machine.

    Stored locally and never checked against Cloud — a slug that does not exist
    fails loudly on the next verb that uses it.
    """
    parts = target.split("/")
    if len(parts) > 2 or not all(parts):
        raise typer.BadParameter(
            f"expected an org or org/project, got: {target!r}",
            param_hint="ORG(/PROJECT)",
        )
    with _reporting(as_json=False):
        stored = read_config()
        # Merge-and-revalidate, as load_config does: carries any field added
        # to CloudConfig later without naming it, and still runs validators.
        path = save_config(
            CloudConfig.model_validate(
                {
                    **stored.model_dump(),
                    "org": parts[0],
                    "project": parts[1] if len(parts) == 2 else "",
                }
            )
        )
    typer.echo(f"Default context set to {target} ({path}).")


def _begin_device_grant(
    host: str | None,
) -> tuple[AuthorizationServerMetadata, DeviceAuthorization, str]:
    """Discover endpoints and start the device grant -- the half bare
    `login` and `--start` share; only what happens with the result differs."""
    stored = read_config()
    target_host = host or stored.host
    metadata = discover_endpoints(target_host, transport=_oauth_transport())
    device = start_device_login(
        metadata.device_authorization_endpoint, transport=_oauth_transport()
    )
    return metadata, device, target_host


def _has_display() -> bool:
    """Whether `webbrowser.open` would reach a real browser. Without a display
    the stdlib falls back to lynx/w3m run in the foreground, which takes over
    the terminal and blocks until the user quits it."""
    return sys.platform in ("darwin", "win32") or bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def _device_login_prompt(device: DeviceAuthorization) -> str:
    return device.verification_uri_complete or (
        f"{device.verification_uri} (code: {device.user_code})"
    )


def _finish_login(target_host: str, token: DeviceToken, as_json: bool) -> None:
    """Save the token and report success -- the half bare `login` and
    `--wait` share once a token exists."""
    stored = read_config()
    replaced = bool(stored.token)
    new_config = CloudConfig.model_validate(
        {**stored.model_dump(), "token": token.access_token, "host": target_host}
    )
    save_config(new_config)
    with client_from_config(new_config, target_host) as client:
        organizations = client.list_orgs().organizations
    if as_json:
        print_json_result(LoginResult(host=target_host, organizations=organizations))
        return
    note = " (replaced the previous credential)" if replaced else ""
    typer.echo(f"Logged in to {target_host}{note}.")
    _print_orgs(OrgList(organizations=organizations))


@cloud_app.command("login")
def login(
    host: HostOption = None,
    start: Annotated[
        bool,
        typer.Option(
            "--start",
            help="Begin the device grant, print the approval URL, and exit"
            " without waiting for approval",
        ),
    ] = False,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait", help="Wait for a login begun with --start to be approved"
        ),
    ] = False,
    no_browser: Annotated[
        bool,
        typer.Option(
            "--no-browser",
            help="Print the approval URL instead of opening it in a browser",
        ),
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Sign in via the OAuth device grant: approve in a browser.

    Bare `dct cloud login` starts the grant, opens the approval page in the
    browser (`--no-browser` only prints the URL), and blocks until it is
    approved -- for a human at a terminal. Approval can legitimately take
    as long as the user needs at the browser, which outlasts most tool
    timeouts an agent runs commands under -- so a script splits it into two
    invocations: `--start` prints the approval URL and returns immediately,
    never opening a browser; a later `--wait` blocks for the approval
    `--start` began.
    """
    if start and wait:
        raise typer.BadParameter(
            "pass only one of --start/--wait", param_hint="--start/--wait"
        )
    with _reporting(as_json):
        if credential_source() == "env":
            raise EnvCredentialActive(TOKEN_ENV_VAR)
        if wait:
            pending = read_pending_login()
            if pending is None:
                raise NoPendingLogin()
            try:
                token = poll_device_token(
                    pending.token_endpoint,
                    pending.device_code,
                    pending.interval,
                    pending.expires_in,
                    transport=_oauth_transport(),
                    sleep=_sleep,
                )
            finally:
                clear_pending_login()
            _finish_login(pending.host, token, as_json)
            return
        metadata, device, target_host = _begin_device_grant(host)
        if start:
            save_pending_login(
                PendingLogin(
                    device_code=device.device_code,
                    token_endpoint=metadata.token_endpoint,
                    interval=float(device.interval),
                    expires_in=float(device.expires_in),
                    host=target_host,
                )
            )
            if as_json:
                print_json_result(
                    DeviceLoginStarted(
                        host=target_host,
                        verification_uri=device.verification_uri,
                        verification_uri_complete=device.verification_uri_complete,
                        user_code=device.user_code,
                        expires_in=device.expires_in,
                    )
                )
                return
            # Bare stdout, no wrapping prose: an agent captures this with
            # plain command substitution (`URL=$(dct cloud login --start)`).
            typer.echo(_device_login_prompt(device))
            err_console.print(
                "Next: dct cloud login --wait to finish signing in once"
                " you've approved it in the browser."
            )
            return
        err_console.print(
            f"Open {escape(_device_login_prompt(device))} to approve this login.",
            soft_wrap=True,
        )
        if not no_browser and _has_display():
            with suppress(OSError):
                webbrowser.open(
                    device.verification_uri_complete or device.verification_uri
                )
        token = poll_device_token(
            metadata.token_endpoint,
            device.device_code,
            float(device.interval),
            float(device.expires_in),
            transport=_oauth_transport(),
            sleep=_sleep,
        )
        _finish_login(target_host, token, as_json)


@cloud_app.command("logout")
def logout(host: HostOption = None, as_json: JsonOption = False) -> None:
    """Revoke and clear the stored credential."""
    with _reporting(as_json):
        if credential_source() == "env":
            raise EnvCredentialActive(TOKEN_ENV_VAR)
        stored = read_config()
        target_host = host or stored.host
        # The stored credential is cleared whether or not Cloud could be
        # reached to revoke it: a logout that leaves the token behind because
        # the host was down is the one outcome the user cannot see.
        try:
            if stored.token:
                metadata = discover_endpoints(target_host, transport=_oauth_transport())
                revoke_token(
                    metadata.revocation_endpoint,
                    stored.token,
                    transport=_oauth_transport(),
                )
        finally:
            save_config(
                CloudConfig.model_validate({**stored.model_dump(), "token": ""})
            )
    message = f"Logged out of {target_host}."
    if as_json:
        print_json_result(DeleteResult(deleted=True, message=message))
        return
    typer.echo(message)


@cloud_app.command("whoami")
def whoami(host: HostOption = None, as_json: JsonOption = False) -> None:
    """Show the Cloud host, credential source, what it reaches -- and, inside
    a published checkout, which org/project owns it."""
    with _cloud(host, as_json) as (client, _config):
        result = WhoAmI(
            host=client.host,
            credential_source=credential_source(),
            organizations=client.list_orgs().organizations,
        )
    if as_json:
        print_json_result(result)
        return
    label = (
        "DCT_CLOUD_TOKEN" if result.credential_source == "env" else "the config file"
    )
    typer.echo(f"{result.host} — credential from {label}")
    here, note = _published_here(result.host, Path.cwd())
    _print_orgs(OrgList(organizations=result.organizations), here=here)
    if note:
        typer.echo(note)


def _published_here(
    host: str, start: Path
) -> tuple[tuple[str, str] | None, str | None]:
    """(org, project) this checkout's published_to names on *host*, plus a
    note to print when it exists but doesn't answer that (a host mismatch or
    a malformed record). Never raises -- whoami is the orientation command,
    not a resolution gate, so a bad record degrades to "nothing to mark"
    rather than aborting the whole command.
    """
    try:
        published = resolve_published_to(start)
    except ValueError as exc:
        return None, str(exc)
    if published is None:
        return None, None
    if published.host != host:
        return None, (
            f"{published.yml_path} declares published_to on {published.host}, not"
            f" {host} -- pass --host {published.host} to match it."
        )
    return (published.org, published.project), None


@cloud_app.command("status")
def status(
    org: OrgOption = None, host: HostOption = None, as_json: JsonOption = False
) -> None:
    """Readiness: what is set up in this org, and what it needs next."""
    with _cloud(host, as_json) as (client, config):
        org_slug = _org(client, config, org)
        result = client.org_status(org_slug)
        unconfirmed = _checkout_unconfirmed(client, org_slug, result)
    if as_json:
        print_json_result(result)
    else:
        _print_status(org_slug, result)
    if unconfirmed:
        # stderr in both modes: `--json` stdout is Cloud's own OrgStatus shape,
        # and this is a fact about the local directory, not about the org.
        err_console.print(
            f"None of {escape(org_slug)}'s projects match this directory's"
            " published_to or git remotes. If this repository is not connected"
            " yet, the status above is for other projects -- connect it:"
            f" dct cloud project connect --org {org_slug}",
            soft_wrap=True,
        )


def _checkout_unconfirmed(
    client: CloudClient, org: str, status_result: OrgStatus
) -> bool:
    """Whether nothing ties this directory to one of *org*'s projects.

    False when the org has no projects yet: `status` already names
    `project connect` as the next step then. An advisory, so a failed
    project listing drops it rather than failing a status already in hand.
    """
    if not status_result.projects:
        return False
    here, _note = _published_here(client.host, Path.cwd())
    if here is not None and here[0] == org:
        return False
    with suppress(CloudError):
        return not repo_matches(client, org, git_remotes(Path.cwd()))
    return False


@cloud_app.command("orgs")
def orgs(host: HostOption = None, as_json: JsonOption = False) -> None:
    """List the organizations you belong to."""
    with _cloud(host, as_json) as (client, _config):
        result = client.list_orgs()
    if as_json:
        print_json_result(result)
        return
    _print_orgs(result)


@org_app.command("create")
def org_create(
    name: Annotated[str, typer.Argument(help="Display name for the organization")],
    slug: Annotated[
        str | None, typer.Option("--slug", help="URL slug (default: from the name)")
    ] = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Create an organization. You become its admin."""
    with _cloud(host, as_json) as (client, _config):
        result = client.create_org(name, slug or _slugify(name))
    if as_json:
        print_json_result(result)
        return
    typer.echo(f"Created {result.slug} ({result.name}); you are {result.role}.")
    typer.echo(f"Next: dct cloud project connect --org {result.slug}")


@org_app.command("delete")
def org_delete(
    org: RefusingOrgOption = None,
    yes: YesOption = False,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Delete an organization and all its data. Refused while it has projects."""
    with _cloud(host, as_json) as (client, config):
        org_slug = _org(client, config, org, refuse_default=REFUSE_DEFAULT_DESTRUCTIVE)
        _confirm_destructive("organization", org_slug, yes)
        result = client.delete_org(org_slug)
    if as_json:
        print_json_result(result)
        return
    typer.echo(result.message)


@cloud_app.command("members")
def members(
    org: OrgOption = None, host: HostOption = None, as_json: JsonOption = False
) -> None:
    """List the organization's members."""
    with _cloud(host, as_json) as (client, config):
        result = client.list_members(_org(client, config, org))
    if as_json:
        print_json_result(result)
        return
    _print_members(result)


@member_app.command("invite")
def member_invite(
    email: Annotated[str, typer.Argument(help="Address to invite")],
    role: Annotated[
        str | None, typer.Option("--role", help="Role to confer on acceptance")
    ] = None,
    org: OrgOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Invite one address to the organization."""
    with _cloud(host, as_json) as (client, config):
        result = client.invite_member(_org(client, config, org), email, role)
    if as_json:
        print_json_result(result)
        return
    if result.already_member:
        typer.echo(f"{result.email} is already a member.")
        return
    typer.echo(f"Invited {result.email} as {result.role}.")


@member_app.command("remove")
def member_remove(
    email: Annotated[str, typer.Argument(help="Address to remove")],
    org: OrgOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Remove a member from the organization."""
    with _cloud(host, as_json) as (client, config):
        result = client.remove_member(
            _org(client, config, org, refuse_default=REFUSE_DEFAULT_DESTRUCTIVE), email
        )
    if as_json:
        print_json_result(result)
        return
    typer.echo(result.message)


@member_app.command("role")
def member_role(
    email: Annotated[str, typer.Argument(help="Member to change")],
    role: Annotated[str, typer.Argument(help="New role")],
    org: OrgOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Change a member's role."""
    with _cloud(host, as_json) as (client, config):
        result = client.set_member_role(_org(client, config, org), email, role)
    if as_json:
        print_json_result(result)
        return
    typer.echo(f"{result.email} is now {result.role}.")


@cloud_app.command("invites")
def invites(
    org: OrgOption = None, host: HostOption = None, as_json: JsonOption = False
) -> None:
    """List pending invitations."""
    with _cloud(host, as_json) as (client, config):
        result = client.list_invitations(_org(client, config, org))
    if as_json:
        print_json_result(result)
        return
    _print_invitations(result)


@invite_app.command("revoke")
def invite_revoke(
    email: Annotated[str, typer.Argument(help="Invited address")],
    org: OrgOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Revoke a pending invitation."""
    with _cloud(host, as_json) as (client, config):
        result = client.revoke_invitation(
            _org(client, config, org, refuse_default=REFUSE_DEFAULT_DESTRUCTIVE), email
        )
    if as_json:
        print_json_result(result)
        return
    typer.echo(result.message)


@invite_app.command("resend")
def invite_resend(
    email: Annotated[str, typer.Argument(help="Invited address")],
    org: OrgOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Re-send a pending invitation."""
    with _cloud(host, as_json) as (client, config):
        result = client.resend_invitation(_org(client, config, org), email)
    if as_json:
        print_json_result(result)
        return
    typer.echo(f"Resent to {result.email}.")


@cloud_app.command("grants")
def grants(
    org: OrgOption = None, host: HostOption = None, as_json: JsonOption = False
) -> None:
    """List live connector grants against an organization."""
    with _cloud(host, as_json) as (client, config):
        result = client.list_grants(_org(client, config, org))
    if as_json:
        print_json_result(result)
        return
    _print_grants(result)


@grant_app.command("revoke")
def grant_revoke(
    grant_id: Annotated[
        str, typer.Argument(help="Grant id, as `dct cloud grants` lists it")
    ],
    org: OrgOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Revoke a connector grant. May cut the credential making this call."""
    with _cloud(host, as_json) as (client, config):
        result = client.revoke_grant(
            _org(client, config, org, refuse_default=REFUSE_DEFAULT_DESTRUCTIVE),
            grant_id,
        )
    if as_json:
        print_json_result(result)
        return
    typer.echo(result.message)


@cloud_app.command("projects")
def projects(
    org: OrgOption = None, host: HostOption = None, as_json: JsonOption = False
) -> None:
    """List the projects connected to an organization."""
    with _cloud(host, as_json) as (client, config):
        org_slug = _org(client, config, org)
        result = client.list_projects(org_slug)
    if as_json:
        print_json_result(result)
        return
    _print_projects(org_slug, result)


@project_app.command("connect")
def project_connect(
    git_url: Annotated[
        str | None,
        typer.Option(
            "--git-url", help="Connect a public GitHub URL, with no browser hop"
        ),
    ] = None,
    root: Annotated[
        str | None,
        typer.Option("--root", help="Folder holding dbt_project.yml inside the repo"),
    ] = None,
    trunk: Annotated[
        str | None,
        typer.Option("--trunk", help="Branch Cloud pulls from (default: the repo's)"),
    ] = None,
    name: Annotated[
        str | None, typer.Option("--name", help="Project name (default: the repo's)")
    ] = None,
    slug: Annotated[
        str | None, typer.Option("--slug", help="Project slug (default: from the name)")
    ] = None,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Seconds to wait for the browser repo pick"),
    ] = PICK_WAIT_SECONDS,
    poll_interval: Annotated[
        float, typer.Option("--poll-interval", help="Seconds between pick checks")
    ] = PICK_POLL_SECONDS,
    start: Annotated[
        bool,
        typer.Option(
            "--start",
            help="Print the install/pick URL and exit without waiting for the pick",
        ),
    ] = False,
    wait: Annotated[
        bool,
        typer.Option("--wait", help="Wait for a connect begun with --start to finish"),
    ] = False,
    org: RefusingOrgOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Connect a GitHub repository as a project.

    \b
    Two paths:
      --git-url URL   fully headless; public GitHub repos only
      (default)       prints a URL, waits for you to install the GitHub App
                      and pick the repo in the browser, then finishes here

    The browser pick is not bypassable: Cloud authorizes it from your own
    GitHub account's list of repositories you administer.

    Bare connect blocks until the pick lands -- for a human at a terminal.
    A script splits it: `--start` prints the install/pick URL and returns
    immediately (persisting the pending connect); a later `--wait` blocks
    for the pick `--start` began and finishes the project. Neither applies
    to `--git-url`, which is already headless.
    """
    if start and wait:
        raise typer.BadParameter(
            "pass only one of --start/--wait", param_hint="--start/--wait"
        )
    if git_url and (start or wait):
        raise typer.BadParameter(
            "--git-url has no browser pick to start or wait for",
            param_hint="--git-url",
        )
    if poll_interval < MIN_POLL_INTERVAL_SECONDS:
        raise typer.BadParameter(
            f"must be at least {MIN_POLL_INTERVAL_SECONDS}s, got {poll_interval!r}",
            param_hint="--poll-interval",
        )
    if wait:
        with _reporting(as_json):
            pending = read_pending_connect()
            if pending is None:
                raise NoPendingConnect()
        with _cloud(pending.host, as_json) as (client, _config):
            pick = client.wait_for_pick(pending.org, timeout, poll_interval)
            project = _finish_from_pick(
                client,
                pending.org,
                pending.name,
                pending.slug,
                pending.trunk,
                pending.root,
                pick,
            )
        clear_pending_connect()
        org_slug = pending.org
    else:
        with _cloud(host, as_json) as (client, config):
            org_slug = _org(client, config, org, refuse_default=REFUSE_DEFAULT_CONNECT)
            if start:
                save_pending_connect(
                    PendingConnect(
                        org=org_slug,
                        host=client.host,
                        name=name,
                        slug=slug,
                        trunk=trunk,
                        root=root,
                    )
                )
                url = client.connect_url(org_slug)
                if as_json:
                    print_json_result(ProjectConnectStarted(org=org_slug, url=url))
                    return
                # Bare stdout, no wrapping prose: an agent captures this with
                # plain command substitution (`URL=$(dct cloud project connect
                # --start)`).
                typer.echo(url)
                err_console.print(
                    "Next: dct cloud project connect --wait to finish connecting"
                    " once you've picked the repo."
                )
                return
            if git_url:
                project = client.create_project(
                    org_slug,
                    name or _repo_name(git_url),
                    slug or _slugify(name or _repo_name(git_url)),
                    git_url,
                    trunk or "",
                    root or "",
                )
            else:
                project = _connect_through_github(
                    client, org_slug, name, slug, trunk, root, timeout, poll_interval
                )
    # The project exists in Cloud from here on: the result is emitted first,
    # so no local bookkeeping can take it away from the caller.
    if as_json:
        print_json_result(project)
    else:
        typer.echo(
            f"Connected {project.slug}: {project.repo_label}"
            f" (trunk: {project.trunk_branch}, work: {project.work_branch})"
        )
        typer.echo(_connect_next_step(org_slug, project))
        if git_url and repo_key(git_url).startswith("github.com/"):
            typer.echo(
                "Note: scaffold PRs and private repositories need the GitHub App"
                f" flow, not a plain git URL: dct cloud project connect --org"
                f" {org_slug} --start"
            )
    _record_published_to(client.host, org_slug, project, git_url)


def _connect_next_step(org: str, project: ProjectSummary) -> str:
    """Where connect sends the user next.

    Syncing a project whose sources are unmapped renders every board against
    no connection, and mapping one afterwards re-renders nothing. Cloud has
    already cloned the repo, so the create response's count is the work
    branch's real answer.
    """
    if project.config_error is not None:
        return f"{project.config_error}\nFix dbt_charts.yml and push before syncing."
    if project.unmapped_source_count:
        return (
            f"Next: dct cloud connection create --org {org} --type <type>, then "
            f"dct cloud source map <source> <connection> --org {org} "
            f"--project {project.slug} ({project.unmapped_source_count} unmapped; "
            "dct cloud sources lists them). Sync only after mapping, or every "
            "board renders without a source."
        )
    return f"Next: dct cloud project sync --org {org} --project {project.slug}"


def _record_published_to(
    host: str, org: str, project: ProjectSummary, git_url: str | None
) -> None:
    """Record `published_to:` locally, reporting instead of raising.

    The connect already succeeded and its result is already out, so a local
    file this cannot edit is a note on stderr -- never a traceback that
    replaces the slug a `--json` caller needs to sync or clean up.
    """
    try:
        url = published_to_url(host, org, project.slug)
        _write_published_to(url, project, git_url)
    except (ValueError, OSError) as exc:
        err_console.print(
            f"Connected, but published_to was not recorded: {escape(str(exc))}",
            soft_wrap=True,
        )


def _write_published_to(url: str, project: ProjectSummary, git_url: str | None) -> None:
    """Splice `published_to: "<url>"` into the just-connected project's
    dbt_charts.yml, or say what to add and where instead.

    Writes only inside a checkout of the repository that was just connected:
    `published_to` outranks the git-remote match, so a `--git-url` connect run
    from some other repository would otherwise commit a record that points
    every later verb there at the wrong project. Never writes outside the
    resolved project root, and never writes a fresh dbt_charts.yml that didn't
    already exist -- connecting is not scaffolding.
    """
    here = Path.cwd()
    target = find_local_project_dir(here, project.git_subdirectory)
    if target is None:
        err_console.print(
            "Could not find a local git checkout to record this in. Once you"
            f' have one, add to its {DBT_CHARTS_YML}:\n  published_to: "{url}"'
        )
        return
    if not _checkout_is(project, git_url, here):
        err_console.print(
            f"This checkout is not {escape(project.repo_label)}, so nothing was"
            f" written here. In a checkout of that repository, add to its"
            f' {DBT_CHARTS_YML}:\n  published_to: "{url}"',
            soft_wrap=True,
        )
        return
    yml_path = target / DBT_CHARTS_YML
    if not yml_path.exists():
        err_console.print(
            f"No {DBT_CHARTS_YML} yet at {target} -- once scaffolded, add:\n"
            f'  published_to: "{url}"'
        )
        return
    yml_path.write_text(
        set_published_to(yml_path.read_text(encoding="utf-8"), url), encoding="utf-8"
    )
    err_console.print(f"Recorded published_to: {url} in {yml_path} -- commit this.")


def _checkout_is(project: ProjectSummary, git_url: str | None, start: Path) -> bool:
    """Whether the checkout at *start* is the repository just connected.

    `repo_label` is Cloud's own name for it and `git_url` is what a headless
    connect passed; `repo_key` is what makes either comparable to a local
    remote.
    """
    connected = {repo_key(project.repo_label)}
    if git_url:
        connected.add(repo_key(git_url))
    return bool(connected & {repo_key(remote) for remote in git_remotes(start)})


def _connect_through_github(
    client: CloudClient,
    org: str,
    name: str | None,
    slug: str | None,
    trunk: str | None,
    root: str | None,
    timeout: float,
    poll_interval: float,
) -> ProjectSummary:
    """Hand the repository pick to the browser, then resume from its outcome."""
    # Progress, not result: on stderr so `--json` leaves stdout parseable.
    err_console.print("Install the dbt charts GitHub App and pick a repository here:")
    err_console.print(f"  {client.connect_url(org)}", soft_wrap=True)
    err_console.print("Waiting for the pick…")
    pick = client.wait_for_pick(org, timeout, poll_interval)
    return _finish_from_pick(client, org, name, slug, trunk, root, pick)


def _finish_from_pick(
    client: CloudClient,
    org: str,
    name: str | None,
    slug: str | None,
    trunk: str | None,
    root: str | None,
    pick: RepoPick,
) -> ProjectSummary:
    """Finish the project once the browser pick has landed -- the half the
    blocking connect and `--wait` share; only how they get *pick* differs."""
    err_console.print(f"Picked {escape(pick.full_name)}.")
    repo_name = pick.full_name.rpartition("/")[2]
    return client.create_project_from_pick(
        org,
        name or repo_name,
        slug or _slugify(name or repo_name),
        trunk or "",
        DBT_ROOT_OTHER if root else _sole_dbt_root(pick),
        root or "",
    )


def _sole_dbt_root(pick: RepoPick) -> str:
    """The repository's one dbt root, or a refusal to guess between several."""
    if not pick.dbt_roots:
        return DBT_ROOT_REPO_ROOT
    if len(pick.dbt_roots) == 1:
        return pick.dbt_roots[0].path
    listing = "\n".join(f"  --root {root.path}" for root in pick.dbt_roots)
    raise typer.BadParameter(
        f"{pick.full_name} holds {len(pick.dbt_roots)} dbt projects. Name the"
        f" one to connect:\n{listing}",
        param_hint="--root",
    )


@project_app.command("sync")
def project_sync(
    org: OrgOption = None,
    project: ProjectOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Pull the project's repository and refresh its branches."""
    with _cloud(host, as_json) as (client, config):
        context = _context(client, config, org, project)
        result = client.sync_project(context.org, context.project)
    if as_json:
        print_json_result(result)
        return
    typer.echo(result.message)
    # The ordering the product used to leave the user to discover: a push puts
    # the commit on the git host, a sync is what brings it into Cloud and
    # re-renders the boards it changed. "A sync", not "this sync": on the
    # `queued=False` response one was already in flight and this invocation
    # started nothing.
    typer.echo(
        "A git push does not publish; a sync does. Confirm with "
        "`dct cloud boards`: each board your commit changed gets a new "
        "RENDERED_AT and a new COMMIT once its re-render lands. Boards it "
        "did not change keep both, and are already serving the right content."
    )


@project_app.command("scaffold")
def project_scaffold(
    org: OrgOption = None,
    project: ProjectOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Open the dbt_charts.yml scaffold PR. GitHub-connected projects only."""
    with _cloud(host, as_json) as (client, config):
        context = _context(client, config, org, project)
        result = client.scaffold_project(context.org, context.project)
    if as_json:
        print_json_result(result)
        return
    typer.echo(result.message)


@project_app.command("delete")
def project_delete(
    org: RefusingOrgOption = None,
    project: ProjectOption = None,
    yes: YesOption = False,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Delete a project and all its data."""
    with _cloud(host, as_json) as (client, config):
        context = _context(
            client, config, org, project, refuse_default=REFUSE_DEFAULT_DESTRUCTIVE
        )
        _confirm_destructive("project", f"{context.org}/{context.project}", yes)
        result = client.delete_project(context.org, context.project)
    if as_json:
        print_json_result(result)
        return
    typer.echo(result.message)


@cloud_app.command("boards")
def boards(
    org: OrgOption = None,
    project: ProjectOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """List a project's boards, their render state, and where to view them."""
    with _cloud(host, as_json) as (client, config):
        context = _context(client, config, org, project)
        result = client.list_boards(context.org, context.project)
    if as_json:
        print_json_result(result)
        return
    _print_boards(result)


@cloud_app.command("connections")
def connections(
    org: OrgOption = None, host: HostOption = None, as_json: JsonOption = False
) -> None:
    """List the warehouse connections you can use in an organization."""
    with _cloud(host, as_json) as (client, config):
        result = client.list_connections(_org(client, config, org))
    if as_json:
        print_json_result(result)
        return
    _print_connections(result)


@connection_app.command("create")
def connection_create(
    connection_type: Annotated[
        str, typer.Option("--type", help="Warehouse type, e.g. bigquery, snowflake")
    ],
    settings: Annotated[
        list[str] | None,
        typer.Option(
            "--set",
            metavar="KEY=VALUE",
            help="A connection field, repeatable (e.g. --set dataset=analytics)",
        ),
    ] = None,
    keyfile: Annotated[
        Path | None,
        typer.Option(
            "--keyfile",
            exists=True,
            dir_okay=False,
            help="File holding the service-account key or private key",
        ),
    ] = None,
    password_stdin: Annotated[
        bool, typer.Option("--password-stdin", help="Read the password from stdin")
    ] = False,
    password_env: Annotated[
        str | None,
        typer.Option(
            "--password-env", metavar="VAR", help="Env var holding the password"
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help=(
                "Display name; its slug is the connection's id"
                " (default: BigQuery's project, otherwise the database)"
            ),
        ),
    ] = None,
    org: OrgOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Create a connection and test it in one call.

    \b
    Non-secret fields go in --set; which ones a type needs is the server's
    answer, and it names any that are missing:
      dct cloud connection create --type bigquery --keyfile key.json \\
          --set project=acme-gcp --set dataset=analytics

    Key material is never a flag value — pass --keyfile, --password-stdin, or
    --password-env VAR.

    The connection is addressed by the slug of --name. Left off, the display
    name defaults to the field that names the warehouse — BigQuery's project,
    otherwise the database — and create prints the slug it derived.

    A connection whose test fails outright is reported as a failure and is
    not saved: read the printed cause, fix it, and run the same command again. A
    test that could not complete in time (the warehouse may still be starting
    up) is different: the connection IS saved, since the check never actually
    disproved the credential, and the error names the slug and the
    `connection test` verb that resolves it once the warehouse answers.
    """
    fields = parse_kv_pairs(settings or [], "--set")
    leaked = sorted(SECRET_FIELDS & set(fields))
    if leaked:
        raise typer.BadParameter(
            f"{', '.join(leaked)} carries a credential and is never taken from"
            " the command line. Use --keyfile, --password-stdin, or"
            " --password-env VAR.",
            param_hint="--set",
        )
    if "name" in fields:
        raise typer.BadParameter(
            "the display name is --name, not --set name=.",
            param_hint="--set",
        )
    if name is not None:
        if not name.strip():
            # Blank reaches the form as "derive one for me", which is what
            # omitting the flag already means — an empty --name is a caller bug
            # (an unset shell variable), not a request.
            raise typer.BadParameter(
                "a display name cannot be blank.", param_hint="--name"
            )
        fields["name"] = name
    fields.update(_secret_field(connection_type, keyfile, password_stdin, password_env))

    with _cloud(host, as_json) as (client, config):
        result = client.create_connection(
            _org(client, config, org), connection_type, fields
        )
    if as_json:
        print_json_result(result)
        return
    typer.echo(f"Created {result.connection.slug} — connection test passed.")
    typer.echo(f"Next: dct cloud source map <source> {result.connection.slug}")


def _secret_field(
    connection_type: str,
    keyfile: Path | None,
    password_stdin: bool,
    password_env: str | None,
) -> dict[str, str]:
    """The one credential field, from the one place the caller named."""
    chosen = [
        flag
        for flag, given in (
            ("--keyfile", keyfile is not None),
            ("--password-stdin", password_stdin),
            ("--password-env", password_env is not None),
        )
        if given
    ]
    if len(chosen) > 1:
        raise typer.BadParameter(
            f"{' and '.join(chosen)} both supply the credential; pass one.",
            param_hint=chosen[0],
        )
    if keyfile is not None:
        field = KEYFILE_FIELDS.get(connection_type)
        if field is None:
            raise typer.BadParameter(
                f"a {connection_type} connection takes no key file; its secret is"
                " a password (--password-stdin or --password-env VAR).",
                param_hint="--keyfile",
            )
        return {field: keyfile.read_text(encoding="utf-8")}
    if password_stdin:
        return {"password": sys.stdin.read().strip("\r\n")}
    if password_env is not None:
        # The variable is named by the caller at run time, so it cannot be an
        # `envvar=` on an option the way every other env-backed flag here is.
        secret = os.environ.get(password_env)
        if not secret:
            raise typer.BadParameter(
                f"{password_env} is not set in this environment.",
                param_hint="--password-env",
            )
        return {"password": secret}
    return {}


@connection_app.command("test")
def connection_test(
    connection: Annotated[
        str, typer.Argument(help="Connection slug, as `dct cloud connections` lists it")
    ],
    org: OrgOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Ask Cloud to re-run a connection's credential check."""
    with _cloud(host, as_json) as (client, config):
        result = client.test_connection(_org(client, config, org), connection)
    if as_json:
        print_json_result(result)
        return
    typer.echo(f"{result.connection.slug}: connection test passed.")


@connection_app.command("schema-refresh")
def connection_schema_refresh(
    connection: Annotated[
        str, typer.Argument(help="Connection slug, as `dct cloud connections` lists it")
    ],
    org: OrgOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Re-run a connection's schema profile."""
    with _cloud(host, as_json) as (client, config):
        result = client.refresh_connection_schema(_org(client, config, org), connection)
    if as_json:
        print_json_result(result)
        return
    typer.echo(result.message)


@connection_app.command("delete")
def connection_delete(
    connection: Annotated[
        str, typer.Argument(help="Connection slug, as `dct cloud connections` lists it")
    ],
    yes: YesOption = False,
    org: RefusingOrgOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Delete a warehouse connection. Boards that query it start failing."""
    with _cloud(host, as_json) as (client, config):
        org_slug = _org(client, config, org, refuse_default=REFUSE_DEFAULT_DESTRUCTIVE)
        _confirm_destructive("connection", f"{org_slug}/{connection}", yes)
        result = client.delete_connection(org_slug, connection)
    if as_json:
        print_json_result(result)
        return
    typer.echo(result.message)


@cloud_app.command("sources")
def sources(
    org: OrgOption = None,
    project: ProjectOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """List the sources a project's boards declare, and what backs them."""
    with _cloud(host, as_json) as (client, config):
        context = _context(client, config, org, project)
        result = client.list_sources(context.org, context.project)
    if as_json:
        print_json_result(result)
        return
    _print_sources(result)


@source_app.command("map")
def source_map(
    source: Annotated[str, typer.Argument(help="Source name a board's `source:` uses")],
    connection: Annotated[str, typer.Argument(help="Connection slug to point it at")],
    schema: Annotated[
        str | None,
        typer.Option("--schema", help="Schema this source resolves against"),
    ] = None,
    org: OrgOption = None,
    project: ProjectOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Point one declared source at a connection.

    Omitting --schema preserves whatever override the source already has;
    ``--schema ""`` clears it deliberately.
    """
    with _cloud(host, as_json) as (client, config):
        context = _context(client, config, org, project)
        result = client.map_source(
            context.org, context.project, source, connection, schema
        )
    if as_json:
        print_json_result(result)
        return
    _print_sources(result)


@cloud_app.command("render")
def render(
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Re-render every board with fresh query results, including "
            "ones that already rendered — the lever for a board that stays "
            "errored after its source was mapped or its warehouse fixed. "
            "A project admin's act.",
        ),
    ] = False,
    org: OrgOption = None,
    project: ProjectOption = None,
    host: HostOption = None,
    as_json: JsonOption = False,
) -> None:
    """Start renders for the project's boards that have none yet, or all of them."""
    with _cloud(host, as_json) as (client, config):
        context = _context(client, config, org, project)
        result = client.render_project(context.org, context.project, force=force)
    if as_json:
        print_json_result(result)
        return
    if force:
        typer.echo(f"Re-rendering {result.started} board(s).")
    else:
        typer.echo(
            f"Started {result.started} board render(s);"
            f" {result.unrendered_remaining} still unrendered."
        )
    # Printed under either count: forcing starts the renders but does not make
    # the warehouse answer, so a re-render over a connection that has not
    # passed its last test still produces boards of error cards.
    if result.blocked:
        typer.echo(result.blocked)


# =============================================================================
# Human output
# =============================================================================


def _table(*columns: str) -> Table:
    table = Table(show_edge=False, pad_edge=False, box=None)
    for column in columns:
        table.add_column(column)
    return table


def _print_orgs(result: OrgList, here: tuple[str, str] | None = None) -> None:
    if not result.organizations:
        typer.echo("No organizations yet. Create one: dct cloud org create <name>")
        return
    columns = ["SLUG", "NAME", "ROLE"]
    if here is not None:
        columns.append("PUBLISHED HERE")
    table = _table(*columns)
    for org in result.organizations:
        row = [org.slug, org.name, org.role]
        if here is not None:
            row.append(here[1] if org.slug == here[0] else "")
        table.add_row(*row)
    dct_console().print(table)


def _print_projects(org: str, result: ProjectList) -> None:
    if not result.projects:
        typer.echo(
            f"No projects yet. Connect one: dct cloud project connect --org {org}"
        )
        return
    table = _table("SLUG", "REPOSITORY", "TRUNK", "UNMAPPED SOURCES")
    for project in result.projects:
        # A count Cloud could not compute prints as a word, not a number: the
        # reason is under the table, but the cell has to say it too or the
        # column reads as something the caller could act on.
        unmapped = project.unmapped_source_count
        table.add_row(
            project.slug,
            project.repo_label,
            project.trunk_branch,
            "unknown" if unmapped is None else str(unmapped),
        )
    console = dct_console()
    console.print(table)
    for project in result.projects:
        if project.config_error:
            console.print(
                f"{project.slug}: {escape(project.config_error)}"
                " — its unmapped count is unknown, not 0."
            )


def _print_connections(result: ConnectionList) -> None:
    if not result.connections:
        typer.echo(
            "No connections yet. Create one: dct cloud connection create --type …"
        )
        return
    table = _table("SLUG", "TYPE", "LAST TEST")
    for connection in result.connections:
        if connection.last_test_success is None:
            outcome = "never tested"
        elif connection.last_test_success:
            outcome = "passed"
        else:
            outcome = f"failed: {connection.last_test_error}"
        table.add_row(connection.slug, connection.connection_type, outcome)
    dct_console().print(table)


def _print_sources(result: SourceList) -> None:
    if not result.sources and not result.file_sources:
        typer.echo("This project declares no sources.")
        return
    if result.sources:
        table = _table("SOURCE", "CONNECTION", "SCHEMA")
        for source in result.sources:
            table.add_row(
                source.name,
                source.connection_slug or "unmapped",
                source.schema_override,
            )
        dct_console().print(table)
    if result.file_sources:
        typer.echo(
            "resolved from repo (no connection needed): "
            + ", ".join(result.file_sources)
        )
    if result.unmapped_count:
        typer.echo(
            f"{result.unmapped_count} unmapped source(s):"
            " dct cloud source map <source> <connection>"
        )


def _print_members(result: MemberList) -> None:
    if not result.members:
        typer.echo("No members yet.")
        return
    table = _table("EMAIL", "NAME", "ROLE")
    for member in result.members:
        table.add_row(member.email, member.name, member.role)
    dct_console().print(table)


def _print_invitations(result: InvitationList) -> None:
    if not result.invitations:
        typer.echo("No pending invitations.")
        return
    table = _table("EMAIL", "ROLE", "EXPIRES")
    for invitation in result.invitations:
        table.add_row(invitation.email, invitation.role, str(invitation.expires_at))
    dct_console().print(table)


def _board_is_skewed(board: BoardSummary) -> bool:
    """Whether this row came from a Cloud that predates the two new fields.

    ``model_fields_set``, not a ``None`` check: ``None`` is a real value on
    both fields (the board has no complete render), so only "was the key in
    the body" tells the two apart.
    """
    return not {"rendered_at", "commit"} <= board.model_fields_set


def _rendered_at_cell(board: BoardSummary) -> str:
    """A render time in the reader's own timezone, to the minute.

    Minute precision is the granularity the question needs -- "did this render
    happen before or after my push" -- and seconds only make the column wider.
    ``-`` when the board has no complete render; nothing stands in for it.
    """
    if _board_is_skewed(board):
        return "unknown"
    if board.rendered_at is None:
        return "-"
    return board.rendered_at.astimezone().strftime("%Y-%m-%d %H:%M")


def _commit_cell(board: BoardSummary) -> str:
    """The render's commit, abbreviated for reading.

    Seven characters is what `git log --oneline` prints and enough to see the
    sha change between two listings, which is what says a re-render landed;
    the full sha stays on the wire for anything machine-read. ``-`` when the
    board has no complete render.
    """
    if _board_is_skewed(board):
        return "unknown"
    if board.commit is None:
        return "-"
    return board.commit[:7]


def _print_boards(result: BoardList) -> None:
    if not result.boards:
        typer.echo("This project has no boards.")
        return
    table = _table("SLUG", "STATUS", "RENDERED_AT", "COMMIT", "URL")
    # A truncated slug or URL is not a slug or a URL. Rich ellipsizes a cell
    # it cannot word-wrap, and neither of these contains a space; at the 80
    # columns Rich assumes whenever stdout is not a terminal -- every agent,
    # every pipe -- five columns squeeze both. Folding wraps them on character
    # boundaries instead, so nothing is lost. STATUS wraps on its own, between
    # the words of its diagnostic.
    table.columns[0].overflow = "fold"
    table.columns[-1].overflow = "fold"
    for board in result.boards:
        status_text = board.render_status
        if board.error:
            status_text = f"{status_text}: {escape(board.error)}"
        table.add_row(
            board.slug,
            status_text,
            _rendered_at_cell(board),
            _commit_cell(board),
            board.url,
        )
    console = dct_console()
    console.print(table)
    if any(_board_is_skewed(board) for board in result.boards):
        # Blank line first: the URL column folds, so without one the advisory
        # reads as another continuation line of the last row.
        console.print()
        console.print(
            "This Cloud is older than this dct and does not report the render"
            " time or the served commit yet — both print as `unknown`, which"
            " is not the `-` a board that has never rendered gets."
        )


def _print_grants(result: GrantList) -> None:
    if not result.grants:
        typer.echo("No connector grants.")
        return
    table = _table("ID", "USER", "APPLICATION", "SCOPES")
    for grant in result.grants:
        table.add_row(
            grant.grant_id,
            grant.user_email,
            grant.application_name,
            " ".join(grant.scopes),
        )
    dct_console().print(table)


def _print_status(org: str, status_result: OrgStatus) -> None:
    """The setup state machine, next step first — this is the agent contract."""
    console = dct_console()
    console.print(f"[bold]{escape(org)}[/bold] — {status_result.stage.value}")
    if status_result.next_step:
        console.print(f"[bold]next:[/bold] {escape(status_result.next_step)}")
    else:
        console.print(
            "[bold]next:[/bold] nothing — setup is complete;"
            " `dct cloud boards` lists the board URLs."
        )
    console.print(
        f"connections: {status_result.connection_count}"
        f" ({status_result.tested_connection_count} tested)"
    )
    if not status_result.projects:
        typer.echo(
            f"No projects yet. Connect one: dct cloud project connect --org {org}"
        )
        return
    table = _table("PROJECT", "STAGE", "SYNCED", "UNMAPPED")
    for project in status_result.projects:
        table.add_row(
            project.slug,
            project.stage.value,
            "yes" if project.synced else "no",
            str(project.unmapped_source_count),
        )
    console.print(table)
    if any(
        project.ready_board_count is None or project.errored_board_count is None
        for project in status_result.projects
    ):
        console.print(
            "This Cloud is older than this dct and does not report one or"
            " both of the ready/errored board counts below yet — a missing"
            " count prints as `unknown`, not a real count."
        )
    # One unwrapped line per project, not more columns: ten numeric columns
    # truncate their own headers at the 80 columns a piped (agent) read gets.
    for project in status_result.projects:
        ready = (
            "unknown"
            if project.ready_board_count is None
            else str(project.ready_board_count)
        )
        errored = (
            "unknown"
            if project.errored_board_count is None
            else str(project.errored_board_count)
        )
        console.print(
            f"{project.slug} boards: {project.board_count} total,"
            f" {ready} ready,"
            f" {project.unrendered_board_count} unrendered,"
            f" {project.rendering_board_count} rendering,"
            f" {project.failed_board_count} failed,"
            f" {errored} errored",
            soft_wrap=True,
        )
        if project.config_error:
            console.print(
                f"{project.slug}: {escape(project.config_error)}"
                " — its source counts are unknown, not 0."
            )
        if project.file_sources:
            console.print(
                f"{project.slug} sources resolved from repo: "
                + escape(", ".join(project.file_sources))
            )
        if project.failed_board_slugs:
            console.print(
                f"{project.slug} render failures: "
                + escape(", ".join(project.failed_board_slugs))
            )
        if project.errored_board_slugs:
            console.print(
                f"{project.slug} boards with chart errors: "
                + escape(", ".join(project.errored_board_slugs))
            )


def _slugify(name: str) -> str:
    """A URL slug from a display name; the server validates the result."""
    slug = "".join(char if char.isalnum() else "-" for char in name.lower())
    return "-".join(part for part in slug.split("-") if part)


def _repo_name(git_url: str) -> str:
    """The repository's own name, as a default project name."""
    return git_url.rstrip("/").rpartition("/")[2].removesuffix(".git")


__all__ = ["cloud_app"]
