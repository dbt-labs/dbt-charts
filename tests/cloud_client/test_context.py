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
    git_checkout_facts,
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

    def test_an_unparseable_url_does_not_crash(self) -> None:
        """`urlsplit` itself raises ValueError on some malformed input (a
        stray `[` triggers its IPv6-bracket parsing) -- repo_key must stay
        total, since a caller (repo_matches, via a project's stored
        repo_label or this checkout's own origin) cannot control its input.
        A value that cannot match any real "host/path" key is fine; a
        crash is not."""
        assert repo_key("https://user:p[ss@host/x") != "host/x"


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


def _isolate_global_git_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point git's global/system config at empty files so a test's read of
    the *effective* `user.email`/`user.name` never depends on whatever the
    machine running the test happens to have configured."""
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))


class TestGitCheckoutFacts:
    """`dct cloud status`'s local git facts -- zero network access, "as of
    last fetch" ahead/behind, never a fetch of its own."""

    def test_outside_a_repo_is_not_a_checkout(self, tmp_path: Path) -> None:
        """An existing, non-repo directory -- exercises git's own "not a git
        repository" answer, not the OSError path a nonexistent path would."""
        facts = git_checkout_facts(tmp_path)
        assert facts.is_repo is False
        assert facts.read_error is None
        assert facts.branch is None
        assert facts.origin is None
        assert facts.ahead is None
        assert facts.behind is None

    def test_a_directory_git_cannot_read_is_undetermined_not_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `safe.directory` refusal (exit 128) is routine in containers and
        bind mounts -- it must never collapse into "not a checkout": the
        directory could well be a repo tied to a project."""

        def fake_run(  # noqa: ANN001, ANN002, ANN003 -- matches subprocess.run's own loose signature
            args, *a, **kw
        ):
            return subprocess.CompletedProcess(
                args,
                returncode=128,
                stdout="",
                stderr=(
                    f"fatal: detected dubious ownership in repository at '{tmp_path}'\n"
                ),
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        facts = git_checkout_facts(tmp_path)

        assert facts.is_repo is None
        assert facts.read_error is not None
        assert "dubious ownership" in facts.read_error

    def test_a_moved_worktree_is_undetermined_not_not_a_checkout(
        self, tmp_path: Path
    ) -> None:
        """A `.git` FILE pointing at a missing gitdir (a moved or pruned
        worktree, a broken submodule) makes git say "not a git repository:
        <path>" -- WITHOUT the "(or any of the parent directories)" that
        means genuinely no checkout. The two must not collapse to the same
        answer: this one might well be tied to a project."""
        (tmp_path / ".git").write_text("gitdir: /nonexistent/path/.git/worktrees/x\n")

        facts = git_checkout_facts(tmp_path)

        assert facts.is_repo is None
        assert facts.read_error is not None

    def test_a_non_utf8_global_git_identity_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Latin-1 byte in the user's GLOBAL ~/.gitconfig -- not a project
        file -- must not take down every `status` call in every project.
        `subprocess.run(text=True)` decodes strictly; git output itself is
        not guaranteed UTF-8."""
        global_config = tmp_path / "gitconfig"
        global_config.write_bytes(b"[user]\n\tname = Jos\xe9\n\temail = a@b.c\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
        monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(global_config))
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

        facts = git_checkout_facts(repo)

        assert facts.is_repo is True
        assert facts.identity_missing is False

    def test_a_non_utf8_origin_url_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_global_git_config(monkeypatch, tmp_path)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        config_file = repo / ".git" / "config"
        with config_file.open("ab") as f:
            f.write(
                b'[remote "origin"]\n\turl = https://host/caf\xe9\n\tfetch = +refs/*:refs/*\n'
            )

        facts = git_checkout_facts(repo)

        assert facts.is_repo is True
        assert facts.origin is not None

    def test_branch_and_origin_with_no_upstream_tracking(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_global_git_config(monkeypatch, tmp_path)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/acme/analytics.git"],
            cwd=repo,
            check=True,
        )

        facts = git_checkout_facts(repo)

        assert facts.is_repo is True
        assert facts.branch == "main"
        assert facts.origin == "https://github.com/acme/analytics.git"
        assert facts.ahead is None
        assert facts.behind is None

    def test_an_unborn_branch_has_no_commits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A freshly `git init`-ed repo, nothing committed yet, reads
        `ahead is None` the same as a real branch with no upstream tracking
        -- `has_commits` is the field that tells the two apart, so a push
        step is never named for a branch with nothing to push."""
        _isolate_global_git_config(monkeypatch, tmp_path)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

        facts = git_checkout_facts(repo)

        assert facts.ahead is None
        assert facts.has_commits is False

    def test_a_committed_branch_has_commits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_global_git_config(monkeypatch, tmp_path)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
        (repo / "f.txt").write_text("1")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=repo, check=True)

        facts = git_checkout_facts(repo)

        assert facts.has_commits is True

    def test_no_origin_remote_is_none_not_empty_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_global_git_config(monkeypatch, tmp_path)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

        facts = git_checkout_facts(repo)

        assert facts.is_repo is True
        assert facts.origin is None

    def test_detached_head_reports_branch_as_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_global_git_config(monkeypatch, tmp_path)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
        (repo / "f.txt").write_text("1")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=repo, check=True)
        subprocess.run(
            ["git", "checkout", "-q", "--detach", "HEAD"], cwd=repo, check=True
        )

        facts = git_checkout_facts(repo)

        assert facts.is_repo is True
        assert facts.branch is None

    def test_origin_userinfo_is_stripped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A GitHub App / CI token embedded in the remote URL
        (`https://x-access-token:<token>@github.com/...`) must never reach a
        printed line."""
        _isolate_global_git_config(monkeypatch, tmp_path)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(
            [
                "git",
                "remote",
                "add",
                "origin",
                "https://x-access-token:s3cr3t-token@github.com/acme/analytics.git",
            ],
            cwd=repo,
            check=True,
        )

        facts = git_checkout_facts(repo)

        assert facts.origin == "https://github.com/acme/analytics.git"
        assert "s3cr3t-token" not in facts.origin
        assert "x-access-token" not in facts.origin

    def test_scp_style_origin_is_left_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_global_git_config(monkeypatch, tmp_path)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:acme/analytics.git"],
            cwd=repo,
            check=True,
        )

        facts = git_checkout_facts(repo)

        assert facts.origin == "git@github.com:acme/analytics.git"

    def test_an_unparseable_origin_is_redacted_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`urlsplit` raises ValueError on some malformed inputs (a stray
        `[` triggers its IPv6-bracket parsing) -- a parser failure must
        degrade to a redacted placeholder, never a crash, and never the
        raw (potentially credential-bearing) URL."""
        _isolate_global_git_config(monkeypatch, tmp_path)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(
            [
                "git",
                "config",
                "--local",
                "remote.origin.url",
                "https://user:p[ss@host/x",
            ],
            cwd=repo,
            check=True,
        )

        facts = git_checkout_facts(repo)

        assert facts.origin is not None
        assert "p[ss" not in facts.origin
        assert "user" not in facts.origin

    def test_ahead_and_behind_are_as_of_last_fetch_not_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A commit landing on the remote after the last fetch must not move
        `behind` -- the function reads local refs only and never fetches."""
        _isolate_global_git_config(monkeypatch, tmp_path)
        origin = tmp_path / "origin"
        subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
        subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=origin, check=True)
        subprocess.run(["git", "config", "user.name", "A"], cwd=origin, check=True)
        (origin / "f.txt").write_text("1")
        subprocess.run(["git", "add", "f.txt"], cwd=origin, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=origin, check=True)

        work = tmp_path / "work"
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
        subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=work, check=True)
        subprocess.run(["git", "config", "user.name", "A"], cwd=work, check=True)
        (work / "g.txt").write_text("1")
        subprocess.run(["git", "add", "g.txt"], cwd=work, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "local"], cwd=work, check=True)

        # A new commit lands on the remote after `work`'s last fetch.
        (origin / "h.txt").write_text("1")
        subprocess.run(["git", "add", "h.txt"], cwd=origin, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "remote-only"], cwd=origin, check=True
        )

        facts = git_checkout_facts(work)

        assert (facts.ahead, facts.behind) == (1, 0)

        subprocess.run(["git", "fetch", "-q"], cwd=work, check=True)
        facts_after_fetch = git_checkout_facts(work)
        assert (facts_after_fetch.ahead, facts_after_fetch.behind) == (1, 1)

    def test_identity_missing_when_neither_is_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_global_git_config(monkeypatch, tmp_path)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

        assert git_checkout_facts(repo).identity_missing is True

    def test_identity_present_when_locally_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_global_git_config(monkeypatch, tmp_path)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)

        assert git_checkout_facts(repo).identity_missing is False

    def test_identity_missing_when_only_one_of_the_two_is_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard: the check is `or`, not `and` -- a commit needs
        BOTH user.email and user.name, so having only one set must still
        report identity_missing."""
        _isolate_global_git_config(monkeypatch, tmp_path)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)

        assert git_checkout_facts(repo).identity_missing is True
