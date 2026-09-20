"""Per-invocation context resolution: flags, then the repo, then the default.

Never a guess — an ambiguous repo lists its candidates and refuses.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from dbt_charts.cloud_client.client import CloudClient
from dbt_charts.cloud_client.config import CloudConfig
from dbt_charts.cloud_client.context import (
    REFUSE_DEFAULT_CONNECT,
    REFUSE_DEFAULT_DESTRUCTIVE,
    git_remotes,
    repo_key,
    resolve_org,
    resolve_project,
)
from dbt_charts.cloud_client.errors import ContextUnresolved

Handler = Callable[[httpx.Request], httpx.Response]

ORGS = {
    "organizations": [
        {"slug": "acme-data", "name": "Acme Data", "role": "ADMIN"},
        {"slug": "other-co", "name": "Other Co", "role": "MEMBER"},
    ]
}


def _project(slug: str, repo_label: str) -> dict[str, object]:
    return {
        "slug": slug,
        "name": slug,
        "repo_label": repo_label,
        "trunk_branch": "main",
        "work_branch": f"dbt-charts/{slug}",
        "git_subdirectory": "",
        "unmapped_source_count": 0,
    }


PROJECTS = {
    "/api/orgs/acme-data/projects": {
        "projects": [
            _project("analytics", "GitHub: acme/analytics"),
            _project("marts", "https://gitlab.com/acme/marts.git"),
        ]
    },
    "/api/orgs/other-co/projects": {
        "projects": [_project("elsewhere", "GitHub: other/elsewhere")]
    },
}


def cloud(handler: Handler | None = None) -> CloudClient:
    def default(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/orgs":
            return httpx.Response(200, json=ORGS)
        return httpx.Response(200, json=PROJECTS[request.url.path])

    return CloudClient(
        host="https://cloud.example",
        token="t0ken",
        transport=httpx.MockTransport(handler or default),
    )


class TestRepoKey:
    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:acme/analytics.git",
            "https://github.com/acme/analytics",
            "https://github.com/acme/analytics.git",
            "https://dave:token@github.com/acme/analytics.git",
            "ssh://git@github.com/acme/analytics.git",
            "GitHub: acme/analytics",
        ],
    )
    def test_every_spelling_of_one_repo_shares_a_key(self, url: str) -> None:
        assert repo_key(url) == "github.com/acme/analytics"

    def test_a_non_github_remote_keeps_its_host(self) -> None:
        assert repo_key("https://gitlab.com/acme/marts.git") == "gitlab.com/acme/marts"


class TestResolveProject:
    def test_flags_win_without_asking_the_api(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("explicit flags need no lookup")

        with cloud(handler) as client:
            context = resolve_project(
                client,
                org_flag="acme-data",
                project_flag="analytics",
                config=CloudConfig(org="ignored", project="ignored"),
                remotes=["git@github.com:acme/analytics.git"],
                start=tmp_path,
            )
        assert (context.org, context.project) == ("acme-data", "analytics")

    def test_an_exact_remote_match_beats_the_stored_default(
        self, tmp_path: Path
    ) -> None:
        with cloud() as client:
            context = resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(org="other-co", project="elsewhere"),
                remotes=["git@github.com:acme/analytics.git"],
                start=tmp_path,
            )
        assert (context.org, context.project) == ("acme-data", "analytics")

    def test_the_stored_default_answers_when_therepo_matches_nothing(
        self, tmp_path: Path
    ) -> None:
        with cloud() as client:
            context = resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(org="other-co", project="elsewhere"),
                remotes=["git@github.com:someone/unrelated.git"],
                start=tmp_path,
            )
        assert (context.org, context.project) == ("other-co", "elsewhere")

    def test_a_refusing_verb_never_gets_the_stored_default_when_therepo_matches_nothing(
        self,
        tmp_path: Path,
    ) -> None:
        """HIGH-6: a destructive verb must not silently delete against a
        stale `dct cloud use` default just because the repo it happens to
        be run from matches nothing."""
        with cloud() as client, pytest.raises(ContextUnresolved) as caught:
            resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(org="other-co", project="elsewhere"),
                remotes=["git@github.com:someone/unrelated.git"],
                start=tmp_path,
                refuse_default=REFUSE_DEFAULT_DESTRUCTIVE,
            )

        message = str(caught.value)
        assert "destructive" in message
        assert "--org" in message
        assert "set a default" not in message

    def test_no_project_anywhere_points_at_connect_with_the_org_named(
        self, tmp_path: Path
    ) -> None:
        """The org is known (an explicit, valid --org) and has nothing
        connected: the connect hint carries that org, since connect never
        reads the stored default."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/orgs":
                return httpx.Response(200, json=ORGS)
            return httpx.Response(200, json={"projects": []})

        with cloud(handler) as client, pytest.raises(ContextUnresolved) as caught:
            resolve_project(
                client,
                org_flag="acme-data",
                project_flag=None,
                config=CloudConfig(),
                remotes=[],
                start=tmp_path,
            )

        assert "dct cloud project connect --org acme-data" in str(caught.value)

    def test_no_project_anywhere_with_two_orgs_leaves_the_org_to_the_caller(
        self,
        tmp_path: Path,
    ) -> None:
        """Nothing named and two memberships: the hint cannot pick, so it
        keeps a placeholder rather than the first org alphabetically."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/orgs":
                return httpx.Response(200, json=ORGS)
            return httpx.Response(200, json={"projects": []})

        with cloud(handler) as client, pytest.raises(ContextUnresolved) as caught:
            resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(),
                remotes=[],
                start=tmp_path,
            )

        message = str(caught.value)
        assert "dct cloud project connect --org <org>" in message
        assert "--org acme-data" not in message

    def test_a_refusing_verb_with_nothing_to_delete_still_says_why(
        self, tmp_path: Path
    ) -> None:
        """Zero projects anywhere is the one branch of the project refusal
        that used to drop the verb's reason."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/orgs":
                return httpx.Response(200, json=ORGS)
            return httpx.Response(200, json={"projects": []})

        with cloud(handler) as client, pytest.raises(ContextUnresolved) as caught:
            resolve_project(
                client,
                org_flag="acme-data",
                project_flag=None,
                config=CloudConfig(),
                remotes=[],
                start=tmp_path,
                refuse_default=REFUSE_DEFAULT_DESTRUCTIVE,
            )

        message = str(caught.value)
        assert message.startswith(REFUSE_DEFAULT_DESTRUCTIVE)
        assert "No project to work on" in message

    def test_a_refusing_verb_still_honors_an_exact_repo_match(
        self, tmp_path: Path
    ) -> None:
        with cloud() as client:
            context = resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(org="other-co", project="elsewhere"),
                remotes=["git@github.com:acme/analytics.git"],
                start=tmp_path,
                refuse_default=REFUSE_DEFAULT_DESTRUCTIVE,
            )
        assert (context.org, context.project) == ("acme-data", "analytics")

    def test_a_refusing_verb_still_honors_explicit_flags(self, tmp_path: Path) -> None:
        with cloud() as client:
            context = resolve_project(
                client,
                org_flag="other-co",
                project_flag="elsewhere",
                config=CloudConfig(),
                remotes=[],
                start=tmp_path,
                refuse_default=REFUSE_DEFAULT_DESTRUCTIVE,
            )
        assert (context.org, context.project) == ("other-co", "elsewhere")

    def test_an_ambiguous_repo_lists_candidates_and_refuses(
        self, tmp_path: Path
    ) -> None:
        two_roots = {
            "/api/orgs/acme-data/projects": {
                "projects": [
                    _project("analytics", "GitHub: acme/analytics"),
                    _project("finance", "GitHub: acme/analytics"),
                ]
            },
            "/api/orgs/other-co/projects": {"projects": []},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/orgs":
                return httpx.Response(200, json=ORGS)
            return httpx.Response(200, json=two_roots[request.url.path])

        with cloud(handler) as client, pytest.raises(ContextUnresolved) as caught:
            resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(),
                remotes=["git@github.com:acme/analytics.git"],
                start=tmp_path,
            )

        message = str(caught.value)
        assert "--org acme-data --project analytics" in message
        assert "--org acme-data --project finance" in message

    def test_nothing_to_go_on_is_an_actionable_error(self, tmp_path: Path) -> None:
        with cloud() as client, pytest.raises(ContextUnresolved) as caught:
            resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(),
                remotes=[],
                start=tmp_path,
            )

        message = str(caught.value)
        assert "dct cloud use" in message
        assert "--org acme-data --project analytics" in message

    def test_naming_only_a_project_asks_for_the_org_not_the_project(
        self, tmp_path: Path
    ) -> None:
        """Bug: `--project X` with no resolvable org answered "No project
        selected", telling the caller to supply what they had just supplied.
        The org is the missing half, so the org refusal is the useful one."""
        with cloud() as client, pytest.raises(ContextUnresolved) as caught:
            resolve_project(
                client,
                org_flag=None,
                project_flag="analytics",
                config=CloudConfig(),
                remotes=[],
                start=tmp_path,
            )

        message = str(caught.value)
        assert "No organization selected" in message
        assert "No project selected" not in message

    def test_a_stale_stored_org_does_not_get_queried_while_composing_the_error(
        self,
        tmp_path: Path,
    ) -> None:
        """Bug: the stored `dct cloud use` default is never validated against
        Cloud -- composing the "no project" refusal must not blindly query
        `list_projects` for it, or a deleted/typo'd org turns an actionable
        ContextUnresolved into a confusing ApiFailed instead."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/orgs":
                return httpx.Response(200, json=ORGS)
            if request.url.path == "/api/orgs/deleted-org/projects":
                raise AssertionError(
                    "must not query list_projects for an unvalidated stored org"
                )
            return httpx.Response(200, json=PROJECTS[request.url.path])

        with cloud(handler) as client, pytest.raises(ContextUnresolved) as caught:
            resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(org="deleted-org", project=""),
                remotes=[],
                start=tmp_path,
            )

        message = str(caught.value)
        assert "acme-data" in message
        assert "other-co" in message

    def test_an_org_flag_narrows_the_repo_search_to_that_org(
        self, tmp_path: Path
    ) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json=PROJECTS[request.url.path])

        with cloud(handler) as client:
            context = resolve_project(
                client,
                org_flag="acme-data",
                project_flag=None,
                config=CloudConfig(),
                remotes=["https://gitlab.com/acme/marts.git"],
                start=tmp_path,
            )

        assert (context.org, context.project) == ("acme-data", "marts")
        assert seen == ["/api/orgs/acme-data/projects"]


class TestResolveOrg:
    def test_flag_wins(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("explicit flag needs no lookup")

        with cloud(handler) as client:
            assert (
                resolve_org(
                    client,
                    org_flag="acme-data",
                    config=CloudConfig(),
                    remotes=[],
                    start=tmp_path,
                )
                == "acme-data"
            )

    def test_the_repo_names_the_org(self, tmp_path: Path) -> None:
        with cloud() as client:
            org = resolve_org(
                client,
                org_flag=None,
                config=CloudConfig(org="other-co"),
                remotes=["git@github.com:acme/analytics.git"],
                start=tmp_path,
            )
        assert org == "acme-data"

    def test_the_stored_default_answers_with_no_repo(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("a stored default needs no lookup")

        with cloud(handler) as client:
            assert (
                resolve_org(
                    client,
                    org_flag=None,
                    config=CloudConfig(org="other-co"),
                    remotes=[],
                    start=tmp_path,
                )
                == "other-co"
            )

    def test_a_refusing_verb_never_gets_the_stored_default_with_no_repo_match(
        self,
        tmp_path: Path,
    ) -> None:
        """HIGH-6: org delete/connection delete must not fall back to a
        stale `dct cloud use` default with no repo evidence at all."""
        with cloud() as client, pytest.raises(ContextUnresolved) as caught:
            resolve_org(
                client,
                org_flag=None,
                config=CloudConfig(org="other-co"),
                remotes=[],
                start=tmp_path,
                refuse_default=REFUSE_DEFAULT_DESTRUCTIVE,
            )

        assert "destructive" in str(caught.value)

    def test_the_refusal_names_the_verbs_own_reason(self, tmp_path: Path) -> None:
        """`project connect` refuses the stored default for a reason of its
        own -- it is binding a repository, not deleting anything -- and the
        message says that, then lists the caller's orgs to name. It must not
        then offer `dct cloud use` as the remedy: that is the default it just
        refused, and following it re-runs into the same refusal."""
        with cloud() as client, pytest.raises(ContextUnresolved) as caught:
            resolve_org(
                client,
                org_flag=None,
                config=CloudConfig(org="other-co"),
                remotes=[],
                start=tmp_path,
                refuse_default=REFUSE_DEFAULT_CONNECT,
            )

        message = str(caught.value)
        assert message.startswith(REFUSE_DEFAULT_CONNECT)
        assert "destructive" not in message
        assert "--org" in message
        assert "set a default" not in message
        assert "acme-data (Acme Data)" in message
        assert "other-co (Other Co)" in message

    def test_a_refusing_verb_still_honors_the_repo_match(self, tmp_path: Path) -> None:
        with cloud() as client:
            org = resolve_org(
                client,
                org_flag=None,
                config=CloudConfig(org="other-co"),
                remotes=["git@github.com:acme/analytics.git"],
                start=tmp_path,
                refuse_default=REFUSE_DEFAULT_DESTRUCTIVE,
            )
        assert org == "acme-data"

    def test_a_repo_in_two_orgs_refuses_and_names_each_once(
        self, tmp_path: Path
    ) -> None:
        """One repository can be connected under two organizations — which one
        this call is about is then unknowable, so it refuses. The candidate
        lines name only ``--org``: the verbs that resolve an org this way
        (``status``, ``orgs``, ``connections``) declare no ``--project``, so
        printing one would hand back a flag they reject."""
        shared = {
            "/api/orgs/acme-data/projects": {
                "projects": [
                    _project("analytics", "GitHub: acme/analytics"),
                    _project("finance", "GitHub: acme/analytics"),
                ]
            },
            "/api/orgs/other-co/projects": {
                "projects": [_project("mirror", "GitHub: acme/analytics")]
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/orgs":
                return httpx.Response(200, json=ORGS)
            return httpx.Response(200, json=shared[request.url.path])

        with cloud(handler) as client, pytest.raises(ContextUnresolved) as caught:
            resolve_org(
                client,
                org_flag=None,
                config=CloudConfig(),
                remotes=["git@github.com:acme/analytics.git"],
                start=tmp_path,
            )

        message = str(caught.value)
        assert "--project" not in message
        assert message.count("--org acme-data") == 1
        assert message.count("--org other-co") == 1

    def test_no_org_anywhere_lists_the_callers_orgs(self, tmp_path: Path) -> None:
        with cloud() as client, pytest.raises(ContextUnresolved) as caught:
            resolve_org(
                client, org_flag=None, config=CloudConfig(), remotes=[], start=tmp_path
            )

        message = str(caught.value)
        assert "acme-data" in message
        assert "other-co" in message
        assert "dct cloud use" in message


class TestGitRemotes:
    def test_reads_every_remote_url_of_the_repo(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:acme/analytics.git"],
            cwd=tmp_path,
            check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "fork", "https://github.com/dave/analytics.git"],
            cwd=tmp_path,
            check=True,
        )
        assert sorted(git_remotes(tmp_path)) == [
            "git@github.com:acme/analytics.git",
            "https://github.com/dave/analytics.git",
        ]

    def test_outside_a_repo_there_are_no_remotes(self, tmp_path: Path) -> None:
        assert git_remotes(tmp_path / "nope") == []


def _published(start: Path, value: str) -> None:
    (start / "dbt_charts.yml").write_text(f'published_to: "{value}"\n')


ANALYTICS_REMOTE = "git@github.com:acme/analytics.git"


class TestPublishedToOutranksTheRepo:
    """The record a `project connect` wrote is this repository's own statement
    of where it publishes; the git-remote match is an inference about it.
    """

    def test_the_record_beats_a_git_remote_match(self, tmp_path: Path) -> None:
        _published(tmp_path, "https://cloud.example/other-co/elsewhere/")

        with cloud() as client:
            context = resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(org="acme-data", project="analytics"),
                remotes=[ANALYTICS_REMOTE],
                start=tmp_path,
            )

        assert (context.org, context.project) == ("other-co", "elsewhere")

    def test_the_record_names_the_org(self, tmp_path: Path) -> None:
        _published(tmp_path, "https://cloud.example/other-co/elsewhere/")

        with cloud() as client:
            org = resolve_org(
                client,
                org_flag=None,
                config=CloudConfig(),
                remotes=[ANALYTICS_REMOTE],
                start=tmp_path,
            )

        assert org == "other-co"

    def test_a_destructive_verb_never_reads_the_record(self, tmp_path: Path) -> None:
        """A fork carries `published_to` verbatim and, unlike a remote match,
        the record does not correct itself there -- so a delete run in the fork
        must not resolve to the upstream project from it."""
        _published(tmp_path, "https://cloud.example/other-co/elsewhere/")

        with cloud() as client, pytest.raises(ContextUnresolved) as caught:
            resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(org="other-co", project="elsewhere"),
                remotes=["git@github.com:someone/fork.git"],
                start=tmp_path,
                refuse_default=REFUSE_DEFAULT_DESTRUCTIVE,
            )
        assert "destructive" in str(caught.value)

        with cloud() as client, pytest.raises(ContextUnresolved):
            resolve_org(
                client,
                org_flag=None,
                config=CloudConfig(org="other-co"),
                remotes=["git@github.com:someone/fork.git"],
                start=tmp_path,
                refuse_default=REFUSE_DEFAULT_DESTRUCTIVE,
            )

    def test_a_destructive_verb_still_takes_the_repo_match(
        self, tmp_path: Path
    ) -> None:
        """The remote match is the fork-safe signal, so it keeps answering for
        deletes even when a record is present."""
        _published(tmp_path, "https://cloud.example/other-co/elsewhere/")

        with cloud() as client:
            context = resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(),
                remotes=[ANALYTICS_REMOTE],
                start=tmp_path,
                refuse_default=REFUSE_DEFAULT_DESTRUCTIVE,
            )

        assert (context.org, context.project) == ("acme-data", "analytics")

    def test_explicit_flags_outrank_the_record(self, tmp_path: Path) -> None:
        _published(tmp_path, "https://cloud.example/other-co/elsewhere/")

        with cloud() as client:
            context = resolve_project(
                client,
                org_flag="acme-data",
                project_flag="analytics",
                config=CloudConfig(),
                remotes=[],
                start=tmp_path,
            )

        assert (context.org, context.project) == ("acme-data", "analytics")

    def test_naming_only_the_project_falls_back_to_the_repo_match(
        self, tmp_path: Path
    ) -> None:
        """The record answers only when neither flag is given -- half a
        context from the flags and half from the record would resolve to a
        pair nobody named."""
        _published(tmp_path, "https://cloud.example/other-co/elsewhere/")

        with cloud() as client:
            context = resolve_project(
                client,
                org_flag=None,
                project_flag="analytics",
                config=CloudConfig(),
                remotes=[ANALYTICS_REMOTE],
                start=tmp_path,
            )

        assert (context.org, context.project) == ("acme-data", "analytics")

    def test_a_record_on_another_host_refuses_and_names_both(
        self, tmp_path: Path
    ) -> None:
        _published(tmp_path, "https://other-host.example/acme-data/analytics/")

        with cloud() as client, pytest.raises(ContextUnresolved) as caught:
            resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(),
                remotes=[ANALYTICS_REMOTE],
                start=tmp_path,
            )

        message = str(caught.value)
        assert "https://other-host.example" in message
        assert "https://cloud.example" in message

    def test_an_unparseable_config_file_refuses_instead_of_raising_yaml(
        self, tmp_path: Path
    ) -> None:
        """A half-edited dbt_charts.yml is a routine transient state, and every
        `dct cloud` verb resolves through here: it must reach the caller as
        this CLI's one failure shape, not a YAMLError."""
        (tmp_path / "dbt_charts.yml").write_text("published_to: [unclosed\n")

        with cloud() as client, pytest.raises(ContextUnresolved) as caught:
            resolve_org(
                client,
                org_flag=None,
                config=CloudConfig(org="acme-data"),
                remotes=[],
                start=tmp_path,
            )

        assert str(tmp_path / "dbt_charts.yml") in str(caught.value)

    def test_a_malformed_record_refuses_naming_the_file(self, tmp_path: Path) -> None:
        _published(tmp_path, "not-a-url")

        with cloud() as client, pytest.raises(ContextUnresolved) as caught:
            resolve_project(
                client,
                org_flag=None,
                project_flag=None,
                config=CloudConfig(),
                remotes=[],
                start=tmp_path,
            )

        assert str(tmp_path / "dbt_charts.yml") in str(caught.value)
