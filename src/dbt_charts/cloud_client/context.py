"""Which org and project a ``dct cloud`` call is about.

Auth is user-global; context is per-invocation. It resolves in the order the
initiative's spec fixes: explicit ``--org``/``--project`` flags, then this
repository's own ``published_to:`` record (the nearest ``dbt_charts.yml``
above the invocation, written by ``project connect``), then the repository
the command runs in (its git remotes, matched against the org's connected
projects), then the ``dct cloud use`` default, then a loud error listing the
candidates.

The repo step is exact-match-or-error, never a preference: one repository can
back several Cloud projects (nested dbt roots), and picking one of them
silently is how a render lands on the wrong board set. Two matches list both
and exit non-zero. A ``published_to`` naming a different host than the one in
use is the same kind of refusal, not a silent skip: it names both hosts and
lets the caller pick a remedy (``--host``, or fixing the record).

The record never names the target of a verb that refuses the stored default
(``org``/``project``/``connection delete``, ``member remove``, ``invite
revoke``, ``grant revoke``, and ``project connect``). A fork of a connected repository carries ``published_to``
verbatim and, unlike a git-remote match, a file record does not correct
itself in a fork -- so ``dct cloud project delete`` run in one would delete
the upstream project. Those verbs take an explicit flag or a repo match.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dbt_charts.cloud_client.client import CloudClient
from dbt_charts.cloud_client.config import CloudConfig
from dbt_charts.cloud_client.errors import ContextUnresolved
from dbt_charts.cloud_client.published_to import resolve_published_to

# How ProjectSummary.repo_label spells a GitHub-backed project.
GITHUB_LABEL_PREFIX = "GitHub: "
# `git@github.com:acme/analytics.git` — scp-style, which urlsplit cannot read.
_SCP_REMOTE = re.compile(r"^[\w.+-]+@([^:/]+):(.+)$")
_GIT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class CloudContext:
    """The org and project one invocation resolved to."""

    org: str
    project: str


def git_remotes(start: Path) -> list[str]:
    """Every remote URL of the repository ``start`` sits in; empty outside one.

    Read from the repository's own config rather than ``git remote -v``, which
    prints URLs after any ``url.<base>.insteadOf`` rewrite — a machine-local
    setting that would make the same checkout answer differently per developer.
    ``--local`` is also what makes "not in a repository" a failure here instead
    of a read of the user's global config.
    """
    try:
        completed = subprocess.run(
            ["git", "config", "--local", "--get-regexp", r"^remote\..*\.url$"],
            cwd=start,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    urls: list[str] = []
    for line in completed.stdout.splitlines():
        _key, _, url = line.partition(" ")
        if url and url not in urls:
            urls.append(url)
    return urls


def repo_key(url: str) -> str:
    """One comparable identity for a repository, from any way of naming it.

    An ssh remote, an https remote with credentials in it, and the label the
    API reports for a project all describe the same repository; this is what
    makes them comparable.
    """
    text = url.strip()
    if text.startswith(GITHUB_LABEL_PREFIX):
        return f"github.com/{text[len(GITHUB_LABEL_PREFIX) :].strip().lower()}"
    scp = _SCP_REMOTE.match(text)
    if scp:
        host, path = scp.group(1), scp.group(2)
    else:
        parsed = urlsplit(text)
        host = parsed.netloc.rpartition("@")[2]
        path = parsed.path
    host = host.partition(":")[0].lower()
    path = path.strip("/").lower().removesuffix(".git")
    return f"{host}/{path}" if host else path


REFUSE_DEFAULT_DESTRUCTIVE = (
    "This is a destructive command, so it never falls back to the stored"
    " `dct cloud use` default."
)
REFUSE_DEFAULT_CONNECT = (
    "Connecting binds this repository to an organization, so it never guesses"
    " from the stored `dct cloud use` default."
)


def resolve_org(
    client: CloudClient,
    org_flag: str | None,
    config: CloudConfig,
    remotes: list[str],
    start: Path,
    *,
    refuse_default: str | None = None,
) -> str:
    """The organization this call is about.

    With ``refuse_default`` — the verb's reason for never answering from
    ``config.org`` — only an explicit ``--org`` or an unambiguous repo match
    may answer; ``published_to`` is skipped too (a fork carries the record
    verbatim), and the refusal leads with that reason.
    """
    if org_flag:
        return org_flag
    if refuse_default is None:
        published = _published_context(client, start)
        if published is not None:
            return published[0]
    matches = repo_matches(client, None, remotes)
    orgs = {org for org, _project in matches}
    if len(orgs) == 1:
        return matches[0][0]
    if len(orgs) > 1:
        raise ContextUnresolved(
            "This repository is connected to more than one organization."
            f" Name the one you mean:\n{_org_candidate_lines(matches)}"
        )
    if refuse_default is not None:
        raise ContextUnresolved(_no_org_message(client, refuse_default))
    if config.org:
        return config.org
    raise ContextUnresolved(_no_org_message(client))


def resolve_project(
    client: CloudClient,
    org_flag: str | None,
    project_flag: str | None,
    config: CloudConfig,
    remotes: list[str],
    start: Path,
    *,
    refuse_default: str | None = None,
) -> CloudContext:
    """The organization and project this call is about.

    ``refuse_default`` (the verb's reason, see ``resolve_org``) never
    returns ``config.org`` or ``config.project`` and never reads
    ``published_to`` — only explicit flags or an unambiguous repo match may
    answer. The ranking's "repo match outranks the stored default" step is
    otherwise unchanged; refusing simply removes the steps a fork or a stale
    default could answer wrongly.
    """
    if org_flag and project_flag:
        return CloudContext(org_flag, project_flag)

    if refuse_default is None and org_flag is None and project_flag is None:
        published = _published_context(client, start)
        if published is not None:
            return CloudContext(*published)

    matches = repo_matches(client, org_flag, remotes)
    if project_flag:
        matches = [match for match in matches if match[1] == project_flag]
    if len(matches) > 1:
        raise ContextUnresolved(
            f"This repository backs {len(matches)} dbt charts projects. Name the"
            f" one you mean:\n{_candidate_lines(matches)}"
        )
    if len(matches) == 1:
        return CloudContext(matches[0][0], matches[0][1])

    if refuse_default is not None:
        raise ContextUnresolved(
            _unresolved_context_message(client, org_flag, project_flag, refuse_default)
        )

    org = org_flag or config.org
    project = project_flag or (config.project if org == config.org else "")
    if org and project:
        return CloudContext(org, project)
    raise ContextUnresolved(_unresolved_context_message(client, org, project_flag))


def _published_context(client: CloudClient, start: Path) -> tuple[str, str] | None:
    """(org, project) named by this checkout's own ``published_to``, if any.

    Raises ``ContextUnresolved`` when the nearest ``dbt_charts.yml`` cannot be
    read, declares a ``published_to`` that doesn't parse, or names a different
    host than this call is using — a corrupted or stale record is worth
    failing over, not silently skipping past.
    """
    try:
        published = resolve_published_to(start)
    except ValueError as exc:
        raise ContextUnresolved(str(exc)) from None
    if published is None:
        return None
    if published.host != client.host:
        raise ContextUnresolved(
            f"{published.yml_path} declares published_to on {published.host},"
            f" but this call is using {client.host}. Pass --host {published.host}"
            " to match it, or fix published_to if this project moved."
        )
    return published.org, published.project


def repo_matches(
    client: CloudClient, org_flag: str | None, remotes: list[str]
) -> list[tuple[str, str]]:
    """Every (org, project) whose repository is the one we are standing in."""
    if not remotes:
        return []
    keys = {repo_key(remote) for remote in remotes}
    orgs = (
        [org_flag]
        if org_flag
        else [org.slug for org in client.list_orgs().organizations]
    )
    return [
        (org, project.slug)
        for org in orgs
        for project in client.list_projects(org).projects
        if repo_key(project.repo_label) in keys
    ]


def _candidate_lines(candidates: list[tuple[str, str]]) -> str:
    return "\n".join(
        f"  --org {org} --project {project}" for org, project in sorted(candidates)
    )


def _org_candidate_lines(candidates: list[tuple[str, str]]) -> str:
    """Candidate orgs, one line each — no ``--project``.

    The verbs that resolve an org this way (`status`, `orgs`, `connections`,
    `org create`) declare no ``--project`` option, so offering one here would
    hand back a flag they reject. Two projects of the same org collapse to one
    line for the same reason: the choice being made is the org.
    """
    return "\n".join(f"  --org {org}" for org in sorted({o for o, _p in candidates}))


def _no_org_message(client: CloudClient, refuse_default: str | None = None) -> str:
    """The "no org" refusal, listing the caller's orgs to name.

    A verb refusing the stored default (``refuse_default``) is not offered
    ``dct cloud use`` as the remedy — following it would only re-run into
    the same refusal.
    """
    organizations = client.list_orgs().organizations
    if not organizations:
        body = (
            "You do not belong to a dbt charts Cloud organization yet. Create"
            " one with `dct cloud org create <name>`."
        )
    else:
        listing = "\n".join(f"  {org.slug} ({org.name})" for org in organizations)
        remedy = (
            "Pass --org."
            if refuse_default is not None
            else "No organization selected. Pass --org, or set a default with"
            " `dct cloud use <org>`."
        )
        body = f"{remedy} Yours:\n{listing}"
    return body if refuse_default is None else f"{refuse_default} {body}"


def _unresolved_context_message(
    client: CloudClient,
    org: str | None,
    project_flag: str | None,
    refuse_default: str | None = None,
) -> str:
    """The refusal for a call that resolved neither an org nor a project.

    A caller who named a project but has no org to put it in is missing the
    org, not the project: answering with the project refusal would list
    projects and ask them to supply what they just supplied.
    """
    if project_flag and not org:
        return _no_org_message(client, refuse_default)
    return _no_project_message(client, org, refuse_default)


def _no_project_message(
    client: CloudClient, org: str | None, refuse_default: str | None = None
) -> str:
    """Compose the "no project" refusal, listing every candidate to name.

    ``org`` may be ``None`` (nothing given) or an unvalidated slug — the
    stored ``dct cloud use`` default, or a typo'd ``--org``, neither ever
    checked against Cloud (``resolve_org``'s own docstring: "a slug that
    does not exist fails loudly on the next verb that uses it"). Querying
    ``list_projects(org)`` for an org that turns out not to exist would
    raise ``ApiFailed`` here, mid-message-composition — replacing the
    actionable "no project selected" refusal with a confusing "no
    organization" one instead. Only query an org this call has *itself*
    just confirmed exists (``client.list_orgs()``, which always succeeds
    for the caller's own memberships); an unrecognized ``org`` is dropped
    in favor of listing every org the caller does belong to, not trusted as
    a real scope to narrow the query to.
    """
    known_orgs = {o.slug for o in client.list_orgs().organizations}
    org_slugs = [org] if org in known_orgs else sorted(known_orgs)
    candidates = [
        (org_slug, project.slug)
        for org_slug in org_slugs
        for project in client.list_projects(org_slug).projects
    ]
    if not candidates:
        # connect never reads the stored default, so the hint must not lean
        # on it: name the org only when exactly one is in scope.
        org_hint = org_slugs[0] if len(org_slugs) == 1 else "<org>"
        body = (
            "No project to work on. Connect this repository with"
            f" `dct cloud project connect --org {org_hint}`."
        )
    elif refuse_default is None:
        body = (
            "No project selected, and this repository matches none of the"
            " connected ones. Name it, or set a default with"
            f" `dct cloud use <org>/<project>`:\n{_candidate_lines(candidates)}"
        )
    else:
        body = f"Name the project:\n{_candidate_lines(candidates)}"
    return body if refuse_default is None else f"{refuse_default} {body}"
