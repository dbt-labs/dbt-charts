"""`dct cloud` — every Wave-1 verb, over a faked Cloud API.

The HTTP layer is an ``httpx.MockTransport``; the seam is the one factory the
command module builds its client with, so everything above it (argument
parsing, context resolution, output, exit codes) is the real code path.
"""

from __future__ import annotations

import json
import re
import webbrowser
from collections.abc import Callable
from pathlib import Path

import click
import httpx
import pytest
import typer.main
from click.testing import Result
from rich.markup import render as render_markup
from typer.testing import CliRunner

from dbt_charts.cli.commands import cloud as cloud_cmd
from dbt_charts.cli.main import app
from dbt_charts.cloud_client.client import CLIENT_ID, CloudClient
from dbt_charts.cloud_client.config import (
    TOKEN_ENV_VAR,
    CloudConfig,
    PendingConnect,
    PendingLogin,
    read_config,
    read_pending_connect,
    read_pending_login,
    save_config,
    save_pending_connect,
    save_pending_login,
)
from dbt_charts.cloud_client.contract import (
    DBT_ROOT_OTHER,
    DBT_ROOT_REPO_ROOT,
    ApiError,
    BoardList,
    ConnectionList,
    ConnectionTestResult,
    DeviceLoginStarted,
    GrantList,
    InvitationList,
    LoginResult,
    MemberList,
    OrgList,
    OrgStatus,
    ProjectConnectStarted,
    ProjectList,
    ProjectSummary,
    RenderResult,
    SourceList,
    SyncResult,
    WhoAmI,
)

runner = CliRunner()

Route = tuple[str, str]


def _project(
    slug: str = "analytics", repo: str = "GitHub: acme/analytics"
) -> dict[str, object]:
    return {
        "slug": slug,
        "name": slug,
        "repo_label": repo,
        "trunk_branch": "main",
        "work_branch": f"dbt-charts/{slug}",
        "git_subdirectory": "",
        "unmapped_source_count": 1,
    }


def _connection(slug: str = "acme-bigquery") -> dict[str, object]:
    return {
        "slug": slug,
        "name": slug,
        "connection_type": "bigquery",
        "is_active": True,
        "last_test_success": True,
        "last_test_error": "",
    }


class FakeApi:
    """A routing table plus the requests it saw."""

    def __init__(self) -> None:
        self.routes: dict[Route, list[httpx.Response]] = {}
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.requests: list[httpx.Request] = []
        self.opened: list[str] = []

    def add(
        self, method: str, path: str, body: dict[str, object], status: int = 200
    ) -> None:
        self.routes.setdefault((method, path), []).append(
            httpx.Response(status, json=body)
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        payload: dict[str, object] = self._decode(request.content)
        self.calls.append((request.method, request.url.path, payload))
        self.requests.append(request)
        queued = self.routes.get((request.method, request.url.path))
        if not queued:
            raise AssertionError(f"unrouted {request.method} {request.url.path}")
        return queued[0] if len(queued) == 1 else queued.pop(0)

    @staticmethod
    def _decode(content: bytes) -> dict[str, object]:
        """The request body -- JSON for /api/ calls, form-encoded for the
        OAuth device-grant endpoints, which speak RFC 6749's wire format."""
        if not content:
            return {}
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return dict(httpx.QueryParams(content))

    def body(self, method: str, path: str) -> dict[str, object]:
        for seen_method, seen_path, payload in self.calls:
            if (seen_method, seen_path) == (method, path):
                return payload
        raise AssertionError(f"{method} {path} was never called")


GIT_URL = "https://github.com/acme/analytics"


def _connect(*extra: str) -> list[str]:
    return ["cloud", "project", "connect", "--org", "acme-data", *extra]


def _checkout(
    path: Path, monkeypatch: pytest.MonkeyPatch, remote: str = GIT_URL
) -> None:
    """Stand *path* up as a clone of *remote*: the `.git` marker the write
    target walks to, plus the remote the connect verifies itself against."""
    (path / ".git").mkdir()
    monkeypatch.setattr(cloud_cmd, "git_remotes", _remotes(remote))


def _no_sleep(seconds: float) -> None:
    """login/logout's poll never actually waits in a test."""


def _remotes(*urls: str) -> Callable[[Path], list[str]]:
    """A stand-in for the real `git remote` read, which no test shells out to."""

    def remotes(start: Path) -> list[str]:
        return list(urls)

    return remotes


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("DCT_CLOUD_TOKEN", raising=False)
    monkeypatch.delenv("DCT_CLOUD_HOST", raising=False)
    # Seeds the same ambient default every existing test in this file relies
    # on, but through the REAL config file rather than a literal baked into
    # the factory below -- HIGH-4: the factory must honor `--host`/
    # DCT_CLOUD_HOST/config precedence the same way client_from_config does,
    # not discard whatever `host` argument the command resolved and pass it.
    save_config(CloudConfig(host="https://cloud.example"))
    fake = FakeApi()

    def factory(config: CloudConfig, host: str | None = None) -> CloudClient:
        return CloudClient(
            host=host or config.host,
            token="t0ken",
            transport=httpx.MockTransport(fake.handle),
        )

    monkeypatch.setattr(cloud_cmd, "client_from_config", factory)
    monkeypatch.setattr(cloud_cmd, "git_remotes", _remotes())
    # login/logout's OAuth calls aren't under /api and don't hold a token yet
    # -- they get the same fake transport through their own seam, and never
    # sleep between polls.
    monkeypatch.setattr(
        cloud_cmd, "_oauth_transport", lambda: httpx.MockTransport(fake.handle)
    )
    monkeypatch.setattr(cloud_cmd, "_sleep", _no_sleep)
    monkeypatch.setattr(webbrowser, "open", fake.opened.append)
    return fake


def out(result: Result) -> str:
    """Everything the user saw, stdout and stderr interleaved."""
    return result.output


class TestUse:
    def test_writes_the_default_org_and_project(self, api: FakeApi) -> None:
        result = runner.invoke(app, ["cloud", "use", "acme-data/analytics"])
        assert result.exit_code == 0, out(result)
        assert read_config().org == "acme-data"
        assert read_config().project == "analytics"

    def test_an_org_alone_clears_the_project(self, api: FakeApi) -> None:
        save_config(CloudConfig(org="old", project="stale"))
        result = runner.invoke(app, ["cloud", "use", "acme-data"])
        assert result.exit_code == 0, out(result)
        assert read_config().project == ""

    def test_a_malformed_target_is_refused(self, api: FakeApi) -> None:
        result = runner.invoke(app, ["cloud", "use", "acme/analytics/extra"])
        assert result.exit_code != 0
        assert "org" in out(result).lower()

    def test_keeps_the_stored_token(self, api: FakeApi) -> None:
        save_config(CloudConfig(token="keep-me"))
        runner.invoke(app, ["cloud", "use", "acme-data"])
        assert read_config().token == "keep-me"

    def test_usage_line_is_safe_rich_markup(self) -> None:
        """Click's usage line embeds the `target` argument's metavar verbatim
        ("Usage: dct cloud use [OPTIONS] ORG[/PROJECT]"). Typer prints that
        line through Rich with markup enabled whenever a real terminal drives
        the CLI (rich_markup_mode is "rich" outside an agent/piped context --
        dbt_charts.cli.main._RICH_MARKUP_MODE). Rich reads a bare "[/PROJECT]"
        as a closing tag with no opener and raises MarkupError, crashing every
        argument error `dct cloud use` reports. CliRunner can't reproduce this
        end-to-end: pytest's captured, non-tty stdout makes is_plain_output()
        True and takes the plain-Click path instead, so this exercises Rich's
        markup renderer directly on the exact usage string Click generates.
        """
        root_cmd = typer.main.get_command(app)
        assert isinstance(root_cmd, click.Group)
        with click.Context(root_cmd, info_name="dct") as root_ctx:
            cloud_group = root_cmd.get_command(root_ctx, "cloud")
            assert isinstance(cloud_group, click.Group)
            with click.Context(
                cloud_group, parent=root_ctx, info_name="cloud"
            ) as cloud_ctx:
                use_cmd = cloud_group.get_command(cloud_ctx, "use")
                assert use_cmd is not None
                with click.Context(
                    use_cmd, parent=cloud_ctx, info_name="use"
                ) as use_ctx:
                    usage = use_ctx.get_usage()

        render_markup(usage)


DISCOVERY_DOC: dict[str, object] = {
    "issuer": "https://cloud.example",
    "device_authorization_endpoint": "https://cloud.example/o/device-authorization/",
    "token_endpoint": "https://cloud.example/o/token/",
    "revocation_endpoint": "https://cloud.example/o/revoke_token/",
}
DEVICE_AUTH_RESPONSE: dict[str, object] = {
    "device_code": "devc-1",
    "user_code": "ABCD-EFGH",
    "verification_uri": "https://cloud.example/activate",
    "verification_uri_complete": "https://cloud.example/activate?user_code=ABCD-EFGH",
    "expires_in": 600,
    "interval": 5,
}
TOKEN_RESPONSE: dict[str, object] = {
    "access_token": "new-token-xyz",
    "token_type": "Bearer",
    "expires_in": 3600,
    "scope": "dashboards:read orgs:admin projects:admin connections:admin",
}


def _seed_login(api: FakeApi) -> None:
    api.add("GET", "/.well-known/oauth-authorization-server", DISCOVERY_DOC)
    api.add("POST", "/o/device-authorization/", DEVICE_AUTH_RESPONSE)
    api.add("POST", "/o/token/", TOKEN_RESPONSE)


class TestLogin:
    def test_stores_the_token_and_never_prints_it(self, api: FakeApi) -> None:
        _seed_login(api)
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )

        result = runner.invoke(app, ["cloud", "login"])

        assert result.exit_code == 0, out(result)
        assert read_config().token == "new-token-xyz"
        assert "new-token-xyz" not in out(result)
        assert "acme-data" in out(result)

    def test_refuses_when_the_env_var_would_shadow_the_new_token(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(TOKEN_ENV_VAR, "env-token")

        result = runner.invoke(app, ["cloud", "login"])

        assert result.exit_code != 0
        assert TOKEN_ENV_VAR in out(result)
        assert read_config().token == ""
        assert api.calls == []

    def test_json_emits_host_and_orgs_with_no_token_field(self, api: FakeApi) -> None:
        _seed_login(api)
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )

        result = runner.invoke(app, ["cloud", "login", "--json"])

        assert result.exit_code == 0, out(result)
        parsed = LoginResult.model_validate_json(result.stdout)
        assert parsed.host == "https://cloud.example"
        assert parsed.organizations[0].slug == "acme-data"
        assert "new-token-xyz" not in result.stdout
        assert "token" not in json.loads(result.stdout)

    def test_opens_the_approval_url_in_the_browser_and_still_prints_it(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISPLAY", ":0")
        _seed_login(api)
        api.add("GET", "/api/orgs", {"organizations": []})

        result = runner.invoke(app, ["cloud", "login"])

        assert result.exit_code == 0, out(result)
        assert api.opened == ["https://cloud.example/activate?user_code=ABCD-EFGH"]
        assert "https://cloud.example/activate?user_code=ABCD-EFGH" in out(result)

    def test_no_browser_only_prints_the_approval_url(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISPLAY", ":0")
        _seed_login(api)
        api.add("GET", "/api/orgs", {"organizations": []})

        result = runner.invoke(app, ["cloud", "login", "--no-browser"])

        assert result.exit_code == 0, out(result)
        assert api.opened == []
        assert "https://cloud.example/activate?user_code=ABCD-EFGH" in out(result)

    @pytest.mark.parametrize("platform", ["darwin", "win32"])
    def test_a_desktop_os_launches_without_a_display_variable(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch, platform: str
    ) -> None:
        monkeypatch.setattr(cloud_cmd.sys, "platform", platform)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        _seed_login(api)
        api.add("GET", "/api/orgs", {"organizations": []})

        result = runner.invoke(app, ["cloud", "login"])

        assert result.exit_code == 0, out(result)
        assert api.opened == ["https://cloud.example/activate?user_code=ABCD-EFGH"]

    def test_wayland_alone_counts_as_a_display(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cloud_cmd.sys, "platform", "linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        _seed_login(api)
        api.add("GET", "/api/orgs", {"organizations": []})

        result = runner.invoke(app, ["cloud", "login"])

        assert result.exit_code == 0, out(result)
        assert api.opened == ["https://cloud.example/activate?user_code=ABCD-EFGH"]

    def test_headless_linux_never_launches_a_browser(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cloud_cmd.sys, "platform", "linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        _seed_login(api)
        api.add("GET", "/api/orgs", {"organizations": []})

        result = runner.invoke(app, ["cloud", "login"])

        assert result.exit_code == 0, out(result)
        assert api.opened == []
        assert "https://cloud.example/activate?user_code=ABCD-EFGH" in out(result)

    def test_a_browser_that_will_not_open_leaves_the_printed_url_to_do_the_job(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_display(url: str) -> bool:
            raise OSError("no display")

        monkeypatch.setattr(webbrowser, "open", no_display)
        monkeypatch.setenv("DISPLAY", ":0")
        _seed_login(api)
        api.add("GET", "/api/orgs", {"organizations": []})

        result = runner.invoke(app, ["cloud", "login"])

        assert result.exit_code == 0, out(result)
        assert "https://cloud.example/activate?user_code=ABCD-EFGH" in out(result)

    def test_re_login_says_it_replaced_the_previous_credential(
        self, api: FakeApi
    ) -> None:
        save_config(CloudConfig(host="https://cloud.example", token="old-token"))
        _seed_login(api)
        api.add("GET", "/api/orgs", {"organizations": []})

        result = runner.invoke(app, ["cloud", "login"])

        assert result.exit_code == 0, out(result)
        assert "replaced" in out(result).lower()
        assert read_config().token == "new-token-xyz"

    def test_a_denied_login_fails_loudly_and_leaves_no_token(
        self, api: FakeApi
    ) -> None:
        api.add("GET", "/.well-known/oauth-authorization-server", DISCOVERY_DOC)
        api.add("POST", "/o/device-authorization/", DEVICE_AUTH_RESPONSE)
        api.add("POST", "/o/token/", {"error": "access_denied"}, status=400)

        result = runner.invoke(app, ["cloud", "login"])

        assert result.exit_code != 0
        assert "denied" in out(result).lower()
        assert read_config().token == ""


def _pending() -> PendingLogin:
    return PendingLogin(
        device_code="devc-1",
        token_endpoint="https://cloud.example/o/token/",
        interval=5.0,
        expires_in=600.0,
        host="https://cloud.example",
    )


class TestLoginStartWait:
    """`--start`/`--wait` split the device grant across two invocations, so an
    agent that cannot block for a browser approval can hand the URL over and
    poll separately -- see FR-67/FR-82 in the task this ships."""

    def test_start_prints_the_url_to_stdout_and_does_not_poll(
        self, api: FakeApi
    ) -> None:
        api.add("GET", "/.well-known/oauth-authorization-server", DISCOVERY_DOC)
        api.add("POST", "/o/device-authorization/", DEVICE_AUTH_RESPONSE)

        result = runner.invoke(app, ["cloud", "login", "--start"])

        assert result.exit_code == 0, out(result)
        assert result.stdout.strip() == (
            "https://cloud.example/activate?user_code=ABCD-EFGH"
        )
        pending = read_pending_login()
        assert pending is not None
        assert pending.device_code == "devc-1"
        assert pending.token_endpoint == "https://cloud.example/o/token/"
        assert pending.host == "https://cloud.example"
        assert read_config().token == ""

    def test_start_never_opens_a_browser(self, api: FakeApi) -> None:
        api.add("GET", "/.well-known/oauth-authorization-server", DISCOVERY_DOC)
        api.add("POST", "/o/device-authorization/", DEVICE_AUTH_RESPONSE)

        result = runner.invoke(app, ["cloud", "login", "--start"])

        assert result.exit_code == 0, out(result)
        assert api.opened == []

    def test_start_hints_at_wait_on_stderr_not_stdout(self, api: FakeApi) -> None:
        """An agent that runs --start and never runs --wait leaves login
        half-done (FR-87) -- the hint nudges it, on stderr so it never
        pollutes a `URL=$(dct cloud login --start)` capture."""
        api.add("GET", "/.well-known/oauth-authorization-server", DISCOVERY_DOC)
        api.add("POST", "/o/device-authorization/", DEVICE_AUTH_RESPONSE)

        result = runner.invoke(app, ["cloud", "login", "--start"])

        assert result.exit_code == 0, out(result)
        assert "dct cloud login --wait" in result.stderr
        assert "--wait" not in result.stdout

    def test_start_json_emits_the_device_login_started_shape(
        self, api: FakeApi
    ) -> None:
        api.add("GET", "/.well-known/oauth-authorization-server", DISCOVERY_DOC)
        api.add("POST", "/o/device-authorization/", DEVICE_AUTH_RESPONSE)

        result = runner.invoke(app, ["cloud", "login", "--start", "--json"])

        assert result.exit_code == 0, out(result)
        parsed = DeviceLoginStarted.model_validate_json(result.stdout)
        assert parsed.host == "https://cloud.example"
        assert parsed.user_code == "ABCD-EFGH"

    def test_wait_polls_the_pending_login_and_saves_the_token(
        self, api: FakeApi
    ) -> None:
        save_pending_login(_pending())
        api.add("POST", "/o/token/", TOKEN_RESPONSE)
        api.add("GET", "/api/orgs", {"organizations": []})

        result = runner.invoke(app, ["cloud", "login", "--wait"])

        assert result.exit_code == 0, out(result)
        assert read_config().token == "new-token-xyz"
        assert read_pending_login() is None

    def test_wait_json_emits_the_login_result(self, api: FakeApi) -> None:
        save_pending_login(_pending())
        api.add("POST", "/o/token/", TOKEN_RESPONSE)
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )

        result = runner.invoke(app, ["cloud", "login", "--wait", "--json"])

        assert result.exit_code == 0, out(result)
        parsed = LoginResult.model_validate_json(result.stdout)
        assert parsed.host == "https://cloud.example"
        assert parsed.organizations[0].slug == "acme-data"

    def test_wait_with_no_pending_login_is_a_clear_error(self, api: FakeApi) -> None:
        result = runner.invoke(app, ["cloud", "login", "--wait"])

        assert result.exit_code != 0
        assert "--start" in out(result)
        assert api.calls == []

    def test_wait_on_a_denied_login_clears_the_pending_record(
        self, api: FakeApi
    ) -> None:
        save_pending_login(_pending())
        api.add("POST", "/o/token/", {"error": "access_denied"}, status=400)

        result = runner.invoke(app, ["cloud", "login", "--wait"])

        assert result.exit_code != 0
        assert "denied" in out(result).lower()
        assert read_pending_login() is None
        assert read_config().token == ""

    def test_start_and_wait_together_are_rejected(self, api: FakeApi) -> None:
        result = runner.invoke(app, ["cloud", "login", "--start", "--wait"])

        assert result.exit_code != 0
        assert api.calls == []


class TestLogout:
    def test_revokes_and_clears_the_token(self, api: FakeApi) -> None:
        save_config(CloudConfig(host="https://cloud.example", token="old-token"))
        api.add("GET", "/.well-known/oauth-authorization-server", DISCOVERY_DOC)
        api.add("POST", "/o/revoke_token/", {})

        result = runner.invoke(app, ["cloud", "logout"])

        assert result.exit_code == 0, out(result)
        assert read_config().token == ""
        assert api.body("POST", "/o/revoke_token/") == {
            "token": "old-token",
            "client_id": CLIENT_ID,
        }

    def test_an_unreachable_host_still_clears_the_stored_credential(
        self, api: FakeApi
    ) -> None:
        save_config(CloudConfig(host="https://cloud.example", token="stale-token"))

        result = runner.invoke(app, ["cloud", "logout"])

        assert result.exit_code != 0
        assert read_config().token == ""

    def test_an_unknown_token_still_clears_the_stored_credential(
        self, api: FakeApi
    ) -> None:
        """RFC 7009: a 200 for a token the server no longer recognizes is
        still success -- logout must not treat that as a failure."""
        save_config(CloudConfig(host="https://cloud.example", token="stale"))
        api.add("GET", "/.well-known/oauth-authorization-server", DISCOVERY_DOC)
        api.add("POST", "/o/revoke_token/", {})

        result = runner.invoke(app, ["cloud", "logout"])

        assert result.exit_code == 0, out(result)
        assert read_config().token == ""

    def test_refuses_when_the_env_var_is_the_credential_in_effect(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save_config(CloudConfig(host="https://cloud.example", token="config-token"))
        monkeypatch.setenv(TOKEN_ENV_VAR, "env-token")

        result = runner.invoke(app, ["cloud", "logout"])

        assert result.exit_code != 0
        assert TOKEN_ENV_VAR in out(result)
        assert read_config().token == "config-token"

    def test_with_no_stored_token_nothing_is_called(self, api: FakeApi) -> None:
        result = runner.invoke(app, ["cloud", "logout"])

        assert result.exit_code == 0, out(result)
        assert api.calls == []


class TestWhoAmi:
    def test_reports_host_source_and_orgs(self, api: FakeApi) -> None:
        save_config(CloudConfig(host="https://cloud.example", token="tok"))
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )

        result = runner.invoke(app, ["cloud", "whoami"])

        assert result.exit_code == 0, out(result)
        assert "cloud.example" in out(result)
        assert "config file" in out(result)
        assert "acme-data" in out(result)

    def test_reports_the_env_credential_source(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(TOKEN_ENV_VAR, "env-token")
        api.add("GET", "/api/orgs", {"organizations": []})

        result = runner.invoke(app, ["cloud", "whoami"])

        assert result.exit_code == 0, out(result)
        assert TOKEN_ENV_VAR in out(result)

    def test_json_shape(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )

        result = runner.invoke(app, ["cloud", "whoami", "--json"])

        assert result.exit_code == 0, out(result)
        parsed = WhoAmI.model_validate_json(result.stdout)
        assert parsed.credential_source == "config"
        assert parsed.organizations[0].slug == "acme-data"

    def test_marks_the_published_org_inside_a_published_checkout(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`whoami` marks which of the listed orgs owns this checkout
        instead of leaving the caller to guess among all of them."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "dbt_charts.yml").write_text(
            'published_to: "https://cloud.example/acme-data/analytics/"\n'
        )
        monkeypatch.chdir(tmp_path)
        api.add(
            "GET",
            "/api/orgs",
            {
                "organizations": [
                    {"slug": "acme-data", "name": "Acme", "role": "ADMIN"},
                    {"slug": "other-co", "name": "Other Co", "role": "ADMIN"},
                ]
            },
        )

        result = runner.invoke(app, ["cloud", "whoami"])

        assert result.exit_code == 0, out(result)
        assert "PUBLISHED HERE" in out(result)
        assert "analytics" in out(result)

    def test_a_host_mismatch_is_noted_but_does_not_abort(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "dbt_charts.yml").write_text(
            'published_to: "https://other-host.example/acme-data/analytics/"\n'
        )
        monkeypatch.chdir(tmp_path)
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )

        result = runner.invoke(app, ["cloud", "whoami"])

        assert result.exit_code == 0, out(result)
        assert "other-host.example" in out(result)
        assert "--host https://other-host.example" in out(result)


class TestOrgs:
    def test_lists_orgs(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )
        result = runner.invoke(app, ["cloud", "orgs"])
        assert result.exit_code == 0, out(result)
        assert "acme-data" in result.output

    def test_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )
        result = runner.invoke(app, ["cloud", "orgs", "--json"])
        assert result.exit_code == 0, out(result)
        parsed = OrgList.model_validate_json(result.stdout)
        assert parsed.organizations[0].slug == "acme-data"

    def test_create_posts_name_and_slug(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs",
            {"slug": "acme-data", "name": "Acme Data", "role": "ADMIN"},
            status=201,
        )
        result = runner.invoke(
            app, ["cloud", "org", "create", "Acme Data", "--slug", "acme-data"]
        )
        assert result.exit_code == 0, out(result)
        assert api.body("POST", "/api/orgs") == {
            "name": "Acme Data",
            "slug": "acme-data",
        }

    def test_create_points_at_connect_with_the_org_named(self, api: FakeApi) -> None:
        """The next step after creating an org is connecting a repo into it,
        and connect never reads the `dct cloud use` default -- so the hint
        must name the org on the connect line itself."""
        api.add(
            "POST",
            "/api/orgs",
            {"slug": "acme-data", "name": "Acme Data", "role": "ADMIN"},
            status=201,
        )
        result = runner.invoke(app, ["cloud", "org", "create", "Acme Data"])
        assert result.exit_code == 0, out(result)
        assert "dct cloud project connect --org acme-data" in out(result)
        assert "dct cloud use" not in out(result)

    def test_a_field_error_is_reported_and_exits_non_zero(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs",
            {
                "code": "conflict",
                "message": "That slug is taken.",
                "field_errors": {"slug": ["An organization with this slug exists."]},
            },
            status=409,
        )
        result = runner.invoke(app, ["cloud", "org", "create", "Acme", "--slug", "x"])
        assert result.exit_code == 1
        assert "slug" in out(result)
        assert "An organization with this slug exists." in out(result)


class TestHostPrecedence:
    """HIGH-4: --host flag > DCT_CLOUD_HOST env > the config file's stored
    default (client_from_config's own `host or config.host`) -- and every
    request actually goes to the resolved host, bearer token included."""

    def _orgs(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )

    def test_explicit_host_flag_sends_the_request_there(self, api: FakeApi) -> None:
        self._orgs(api)
        result = runner.invoke(
            app, ["cloud", "orgs", "--host", "https://selfhosted.internal"]
        )
        assert result.exit_code == 0, out(result)
        assert len(api.requests) == 1
        request = api.requests[0]
        assert request.url.host == "selfhosted.internal"
        assert request.headers["authorization"] == "Bearer t0ken"

    def test_env_var_host_beats_the_config_default(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The `api` fixture seeds the config file's host to cloud.example --
        the env var must win over that stored default with no --host flag."""
        self._orgs(api)
        monkeypatch.setenv("DCT_CLOUD_HOST", "https://env.example")
        result = runner.invoke(app, ["cloud", "orgs"])
        assert result.exit_code == 0, out(result)
        assert api.requests[0].url.host == "env.example"

    def test_flag_beats_the_env_var(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._orgs(api)
        monkeypatch.setenv("DCT_CLOUD_HOST", "https://env.example")
        result = runner.invoke(app, ["cloud", "orgs", "--host", "https://flag.example"])
        assert result.exit_code == 0, out(result)
        assert api.requests[0].url.host == "flag.example"

    def test_login_stores_the_flag_host_not_the_config_default(
        self, api: FakeApi
    ) -> None:
        """login resolves its host the same way before it has any client to
        build -- --host beats the config file's stored default here too."""
        api.add("GET", "/.well-known/oauth-authorization-server", DISCOVERY_DOC)
        api.add("POST", "/o/device-authorization/", DEVICE_AUTH_RESPONSE)
        api.add("POST", "/o/token/", TOKEN_RESPONSE)
        api.add("GET", "/api/orgs", {"organizations": []})

        result = runner.invoke(
            app, ["cloud", "login", "--host", "https://flag.example"]
        )

        assert result.exit_code == 0, out(result)
        assert read_config().host == "https://flag.example"


class TestStatus:
    def _status(
        self, stage: str, next_step: str | None, **project: object
    ) -> dict[str, object]:
        return {
            "stage": stage,
            "next_step": next_step,
            "connection_count": 1,
            "tested_connection_count": 1,
            "projects": [
                {
                    "slug": "analytics",
                    "stage": stage,
                    "synced": True,
                    "unmapped_source_count": 1,
                    "board_count": 14,
                    "ready_board_count": 0,
                    "unrendered_board_count": 14,
                    "rendering_board_count": 0,
                    "failed_board_count": 0,
                    "failed_board_slugs": [],
                    "errored_board_count": 0,
                    "errored_board_slugs": [],
                    **project,
                }
            ],
        }

    def test_shows_the_stage_and_the_next_step(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/status",
            self._status("unmapped_sources", "Map source `db` to a connection."),
        )
        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])
        assert result.exit_code == 0, out(result)
        assert "unmapped_sources" in result.output
        assert "Map source `db` to a connection." in result.output
        assert "analytics" in result.output

    def test_an_unreadable_config_is_named_beside_its_project(
        self, api: FakeApi
    ) -> None:
        """The count next to a project whose config would not read is 0 and
        means nothing — without the reason printed, an agent reads it as
        "nothing left to map"."""
        api.add(
            "GET",
            "/api/orgs/acme-data/status",
            self._status(
                "unmapped_sources",
                "Fix the project config.",
                unmapped_source_count=0,
                config_error="Could not read dbt_charts.yml on main: bad byte",
            ),
        )

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert "Could not read dbt_charts.yml on main" in result.output

    def test_every_board_count_is_on_the_projects_boards_line(
        self, api: FakeApi
    ) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/status",
            self._status(
                "unrendered_boards",
                "Renders are already in progress — poll status again.",
                ready_board_count=11,
                unrendered_board_count=0,
                rendering_board_count=3,
            ),
        )

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert (
            "analytics boards: 14 total, 11 ready, 0 unrendered, 3 rendering,"
            " 0 failed, 0 errored" in result.output
        )

    def test_done_points_at_the_board_urls(self, api: FakeApi) -> None:
        api.add("GET", "/api/orgs/acme-data/status", self._status("done", None))

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert "dct cloud boards" in result.output

    def test_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add("GET", "/api/orgs/acme-data/status", self._status("done", None))
        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data", "--json"])
        assert result.exit_code == 0, out(result)
        assert OrgStatus.model_validate_json(result.stdout).next_step is None

    def test_checkout_not_connected_to_the_org_gets_a_trailing_warning(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`status` must not read as if this checkout were part of an
        org whose projects' git remotes it does not match."""
        monkeypatch.setattr(
            cloud_cmd, "git_remotes", _remotes("https://github.com/someone/else")
        )
        api.add("GET", "/api/orgs/acme-data/status", self._status("done", None))
        api.add("GET", "/api/orgs/acme-data/projects", {"projects": [_project()]})

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert "match this directory" in out(result)
        assert "dct cloud project connect --org acme-data" in out(result)

    def test_checkout_connected_to_the_org_prints_no_warning(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            cloud_cmd, "git_remotes", _remotes("https://github.com/acme/analytics")
        )
        api.add("GET", "/api/orgs/acme-data/status", self._status("done", None))
        api.add("GET", "/api/orgs/acme-data/projects", {"projects": [_project()]})

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert "match this directory" not in out(result)

    def test_json_stdout_stays_the_org_status_shape_and_the_warning_goes_to_stderr(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            cloud_cmd, "git_remotes", _remotes("https://github.com/someone/else")
        )
        body = self._status("done", None)
        api.add("GET", "/api/orgs/acme-data/status", body)
        api.add("GET", "/api/orgs/acme-data/projects", {"projects": [_project()]})

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data", "--json"])

        assert result.exit_code == 0, out(result)
        assert json.loads(result.stdout).keys() == body.keys()
        assert "match this directory" in result.stderr

    def test_no_remote_checkout_still_warns_when_the_org_has_projects(
        self, api: FakeApi
    ) -> None:
        """The no-remote / not-a-git-repo case: `git_remotes` already returns
        `[]` by default in the `api` fixture -- this must not skip the
        mismatch check and must not call `list_projects` to answer it."""
        api.add("GET", "/api/orgs/acme-data/status", self._status("done", None))

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert "match this directory" in out(result)

    def test_no_projects_points_at_connect_with_the_org_named(
        self, api: FakeApi
    ) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/status",
            {**self._status("missing_project", "Connect a project."), "projects": []},
        )
        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])
        assert result.exit_code == 0, out(result)
        assert "dct cloud project connect --org acme-data" in out(result)
        assert "match this directory" not in out(result)

    def test_published_to_this_org_counts_as_connected(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A renamed repository or an `insteadOf` remote never key-matches;
        the checkout's own published_to record outranks the remote."""
        monkeypatch.setattr(
            cloud_cmd, "git_remotes", _remotes("https://github.com/someone/else")
        )
        (tmp_path / "dbt_charts.yml").write_text(
            'published_to: "https://cloud.example/acme-data/analytics/"\n'
        )
        monkeypatch.chdir(tmp_path)
        api.add("GET", "/api/orgs/acme-data/status", self._status("done", None))
        api.add("GET", "/api/orgs/acme-data/projects", {"projects": [_project()]})

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert "match this directory" not in out(result)

    def test_a_failed_project_listing_drops_the_advisory_not_the_status(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            cloud_cmd, "git_remotes", _remotes("https://github.com/someone/else")
        )
        api.add("GET", "/api/orgs/acme-data/status", self._status("done", None))
        api.add("GET", "/api/orgs/acme-data/projects", {"detail": "boom"}, status=503)

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert "match this directory" not in out(result)

    def test_a_principal_with_no_org_is_told_to_create_one(self, api: FakeApi) -> None:
        api.add("GET", "/api/orgs", {"organizations": []})
        result = runner.invoke(app, ["cloud", "status"])
        assert result.exit_code == 1
        assert "dct cloud org create" in out(result)

    def test_repo_resolved_file_sources_are_accounted_for(self, api: FakeApi) -> None:
        """A done project with no connection would otherwise look sourceless —
        name the file sources so the user sees where the data came from."""
        api.add(
            "GET",
            "/api/orgs/acme-data/status",
            self._status("done", None, unmapped_source_count=0, file_sources=["marts"]),
        )

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert "marts" in result.output
        assert "repo" in result.output.lower()

    def test_boards_that_rendered_with_chart_errors_are_named(
        self, api: FakeApi
    ) -> None:
        """A board of error cards renders, so it is neither unrendered nor a
        failed render — without its own count and line the output shows a
        project whose every number reads healthy."""
        api.add(
            "GET",
            "/api/orgs/acme-data/status",
            self._status(
                "done",
                "Fix the chart error(s) on board(s) that rendered with error cards.",
                unrendered_board_count=0,
                errored_board_count=1,
                errored_board_slugs=["revenue"],
            ),
        )

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert "1 errored" in result.output
        assert "revenue" in result.output
        assert "chart errors" in result.output

    def test_an_unknown_extra_field_from_a_newer_cloud_is_ignored(
        self, api: FakeApi
    ) -> None:
        """FR-07: a Cloud deploy ahead of this dct may add fields this
        contract has never heard of; they must be ignored, not a hard parse
        error, at any depth of the body."""
        body: dict[str, object] = {
            **self._status("done", None, future_board_count=3),
            "future_org_field": "something new",
        }
        api.add("GET", "/api/orgs/acme-data/status", body)

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert "analytics" in result.output

    def test_a_cloud_behind_the_newest_board_counts_shows_unknown_not_zero(
        self, api: FakeApi
    ) -> None:
        """FR-58: a Cloud deploy that predates #8606/#8612 simply omits
        ready_board_count/errored_board_count(_slugs) from its response.
        `dct cloud status` must still exit 0, show `unknown` rather than a
        fake 0 for those counts, and say which side is behind."""
        api.add(
            "GET",
            "/api/orgs/acme-data/status",
            {
                "stage": "unrendered_boards",
                "next_step": "Render the unrendered board(s).",
                "connection_count": 1,
                "tested_connection_count": 1,
                "projects": [
                    {
                        "slug": "analytics",
                        "stage": "unrendered_boards",
                        "synced": True,
                        "unmapped_source_count": 0,
                        "board_count": 14,
                        "unrendered_board_count": 14,
                        "rendering_board_count": 0,
                        "failed_board_count": 0,
                        "failed_board_slugs": [],
                        # ready_board_count, errored_board_count and
                        # errored_board_slugs deliberately omitted: this
                        # Cloud predates #8606/#8612.
                    }
                ],
            },
        )

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert (
            "analytics boards: 14 total, unknown ready, 14 unrendered,"
            " 0 rendering, 0 failed, unknown errored" in result.output
        )
        assert "older than this dct" in result.output.lower()

    def test_json_shows_null_for_a_board_count_this_cloud_does_not_report(
        self, api: FakeApi
    ) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/status",
            {
                "stage": "unrendered_boards",
                "next_step": "Render the unrendered board(s).",
                "connection_count": 1,
                "tested_connection_count": 1,
                "projects": [
                    {
                        "slug": "analytics",
                        "stage": "unrendered_boards",
                        "synced": True,
                        "unmapped_source_count": 0,
                        "board_count": 14,
                        "unrendered_board_count": 14,
                        "rendering_board_count": 0,
                        "failed_board_count": 0,
                        "failed_board_slugs": [],
                    }
                ],
            },
        )

        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data", "--json"])

        assert result.exit_code == 0, out(result)
        parsed = OrgStatus.model_validate_json(result.stdout)
        assert parsed.projects[0].ready_board_count is None
        assert parsed.projects[0].errored_board_count is None
        assert parsed.projects[0].errored_board_slugs is None
        raw = json.loads(result.stdout)
        assert raw["projects"][0]["ready_board_count"] is None
        assert raw["projects"][0]["errored_board_count"] is None


class TestProjects:
    def test_lists_projects(self, api: FakeApi) -> None:
        api.add("GET", "/api/orgs/acme-data/projects", {"projects": [_project()]})
        result = runner.invoke(app, ["cloud", "projects", "--org", "acme-data"])
        assert result.exit_code == 0, out(result)
        assert "analytics" in result.output
        assert "acme/analytics" in result.output

    def test_an_unreadable_config_is_named_beside_its_project(
        self, api: FakeApi
    ) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/projects",
            {
                "projects": [
                    {
                        **_project(),
                        "unmapped_source_count": None,
                        "config_error": "Could not read dbt_charts.yml on main: oops",
                    }
                ]
            },
        )

        result = runner.invoke(app, ["cloud", "projects", "--org", "acme-data"])

        assert result.exit_code == 0, out(result)
        assert "Could not read dbt_charts.yml on main" in result.output
        # The cell says it too: a reader scanning the column sees no number
        # they could act on, whether or not they read the line below.
        assert "unknown" in result.output

    def test_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add("GET", "/api/orgs/acme-data/projects", {"projects": [_project()]})
        result = runner.invoke(
            app, ["cloud", "projects", "--org", "acme-data", "--json"]
        )
        assert result.exit_code == 0, out(result)
        assert ProjectList.model_validate_json(result.stdout).projects[0].slug == (
            "analytics"
        )

    def test_connect_with_a_git_url_is_headless(self, api: FakeApi) -> None:
        api.add("POST", "/api/orgs/acme-data/projects", _project(), status=201)
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--git-url",
                "https://github.com/acme/analytics",
                "--trunk",
                "main",
            ],
        )
        assert result.exit_code == 0, out(result)
        body = api.body("POST", "/api/orgs/acme-data/projects")
        assert body["git_remote_url"] == "https://github.com/acme/analytics"
        assert body["slug"] == "analytics"
        assert body["trunk_branch"] == "main"

    def test_connect_with_a_github_git_url_hints_the_app_flow_for_scaffold(
        self, api: FakeApi
    ) -> None:
        """`--git-url` on a github.com URL succeeds, but `project
        scaffold` later refuses it server-side ("Scaffold PRs need a
        GitHub-connected project; this one uses a plain git URL."). Warn at
        connect time, before the agent commits to the URL path."""
        api.add("POST", "/api/orgs/acme-data/projects", _project(), status=201)
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--git-url",
                "https://github.com/acme/analytics",
            ],
        )
        assert result.exit_code == 0, out(result)
        assert "scaffold" in result.output.lower()
        assert "private" in result.output.lower()
        assert "dct cloud project connect --org acme-data --start" in result.output

    def test_connect_with_a_non_github_git_url_gets_no_app_hint(
        self, api: FakeApi
    ) -> None:
        api.add("POST", "/api/orgs/acme-data/projects", _project(), status=201)
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--git-url",
                "https://gitlab.com/acme/analytics",
            ],
        )
        assert result.exit_code == 0, out(result)
        assert "GitHub App" not in result.output

    def test_connect_sends_the_root_override(self, api: FakeApi) -> None:
        api.add("POST", "/api/orgs/acme-data/projects", _project(), status=201)
        runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--git-url",
                "https://github.com/acme/analytics",
                "--root",
                "warehouse",
            ],
        )
        body = api.body("POST", "/api/orgs/acme-data/projects")
        assert body["git_subdirectory"] == "warehouse"

    def _connect(self, api: FakeApi, project: dict[str, object]) -> Result:
        api.add("POST", "/api/orgs/acme-data/projects", project, status=201)
        return runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--git-url",
                "https://github.com/acme/analytics",
            ],
        )

    def test_connect_points_at_a_connection_while_sources_are_unmapped(
        self, api: FakeApi
    ) -> None:
        result = self._connect(api, {**_project(), "unmapped_source_count": 2})
        assert result.exit_code == 0, out(result)
        assert "Next: dct cloud connection create --org acme-data" in result.output
        assert (
            "dct cloud source map <source> <connection> --org acme-data "
            "--project analytics" in result.output
        )
        assert "project sync" not in result.output

    def test_connect_points_at_sync_once_nothing_is_left_to_map(
        self, api: FakeApi
    ) -> None:
        result = self._connect(api, {**_project(), "unmapped_source_count": 0})
        assert result.exit_code == 0, out(result)
        assert (
            "Next: dct cloud project sync --org acme-data --project analytics"
            in result.output
        )
        assert "connection create" not in result.output

    def test_connect_reports_an_unreadable_config_instead_of_a_next_step(
        self, api: FakeApi
    ) -> None:
        result = self._connect(
            api,
            {
                **_project(),
                "unmapped_source_count": None,
                "config_error": "Could not read dbt_charts.yml on main: bad yaml",
            },
        )
        assert result.exit_code == 0, out(result)
        assert "Could not read dbt_charts.yml on main: bad yaml" in result.output
        assert "Next: dct cloud" not in result.output

    def test_connect_refuses_a_stale_default_with_no_org_flag(
        self, api: FakeApi
    ) -> None:
        """A repository being connected matches nothing yet, so the
        stored `dct cloud use` default would answer every connect -- and a
        stale one binds the repo into the wrong org. Connect refuses instead,
        saying why and listing the orgs the caller could name."""
        save_config(CloudConfig(org="stale-org", project=""))
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )
        api.add("POST", "/api/orgs/stale-org/projects", _project(), status=201)

        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--git-url",
                "https://github.com/acme/analytics",
            ],
        )

        assert result.exit_code != 0
        message = out(result)
        assert "--org" in message
        assert "binds this repository" in message
        assert "destructive" not in message
        # The remedy it offers must not be the default it just refused.
        assert "set a default" not in message
        assert "acme-data (Acme)" in message
        assert not any(
            (method, path) == ("POST", "/api/orgs/stale-org/projects")
            for method, path, _body in api.calls
        )

    def test_connect_without_org_still_resolves_from_a_repo_match(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refusing the stored default does not touch the repo-match step: a
        repo already connected to exactly one org (re-connecting, or a second
        project from the same repo) names that org with no flag."""
        monkeypatch.setattr(
            cloud_cmd, "git_remotes", _remotes("git@github.com:acme/analytics.git")
        )
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )
        api.add("GET", "/api/orgs/acme-data/projects", {"projects": [_project()]})
        api.add("POST", "/api/orgs/acme-data/projects", _project(), status=201)

        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--git-url",
                "https://github.com/acme/analytics",
                "--slug",
                "analytics-2",
            ],
        )

        assert result.exit_code == 0, out(result)
        assert api.body("POST", "/api/orgs/acme-data/projects")["slug"] == (
            "analytics-2"
        )

    def test_an_empty_listing_points_at_connect_with_the_org_named(
        self, api: FakeApi
    ) -> None:
        api.add("GET", "/api/orgs/acme-data/projects", {"projects": []})
        result = runner.invoke(app, ["cloud", "projects", "--org", "acme-data"])
        assert result.exit_code == 0, out(result)
        assert "dct cloud project connect --org acme-data" in out(result)

    def test_sync_reports_what_happened(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/sync",
            {"queued": True, "message": "Sync queued."},
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "sync",
                "--org",
                "acme-data",
                "--project",
                "analytics",
            ],
        )
        assert result.exit_code == 0, out(result)
        assert "Sync queued." in result.output

    def test_sync_says_a_push_is_not_a_publish(self, api: FakeApi) -> None:
        """The ordering nothing in the product used to state: pushing does not
        publish, syncing does. Phrased about syncing rather than about "this
        sync", since a `queued=False` response means one was already running
        and this invocation started nothing."""
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/sync",
            {"queued": True, "message": "Sync queued."},
        )

        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "sync",
                "--org",
                "acme-data",
                "--project",
                "analytics",
            ],
        )

        assert result.exit_code == 0, out(result)
        assert "does not publish" in result.output
        assert "dct cloud boards" in result.output
        assert "RENDERED_AT" in result.output
        assert "COMMIT" in result.output

    def test_sync_advises_even_when_it_queued_nothing(self, api: FakeApi) -> None:
        """`queued=False` means a sync was already in flight, so this
        invocation started nothing -- which is exactly why the advisory talks
        about syncing rather than about "this sync"."""
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/sync",
            {"queued": False, "message": "A sync is already queued."},
        )

        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "sync",
                "--org",
                "acme-data",
                "--project",
                "analytics",
            ],
        )

        assert result.exit_code == 0, out(result)
        assert "A sync is already queued." in result.output
        assert "does not publish" in result.output
        assert "this sync" not in result.output

    def test_sync_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/sync",
            {"queued": False, "message": "A sync is already queued."},
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "sync",
                "--org",
                "acme-data",
                "--project",
                "analytics",
                "--json",
            ],
        )
        assert SyncResult.model_validate_json(result.stdout).queued is False

    def test_scaffold_reports_what_happened(self, api: FakeApi) -> None:
        """HIGH-3: pins `dct cloud project scaffold` -> POST .../scaffold ->
        the message on stdout. FakeApi's routing is keyed on the exact
        (method, path) pair, so a client-method swap (e.g. calling
        `sync_project` here instead of `scaffold_project`) fails this test
        with an "unrouted" AssertionError rather than passing silently."""
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/scaffold",
            {"queued": True, "message": "Opened the scaffold PR."},
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "scaffold",
                "--org",
                "acme-data",
                "--project",
                "analytics",
            ],
        )
        assert result.exit_code == 0, out(result)
        assert "Opened the scaffold PR." in result.output

    def test_scaffold_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/scaffold",
            {"queued": True, "message": "Opened the scaffold PR."},
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "scaffold",
                "--org",
                "acme-data",
                "--project",
                "analytics",
                "--json",
            ],
        )
        assert result.exit_code == 0, out(result)
        assert SyncResult.model_validate_json(result.stdout).queued is True


class TestProjectConnectPublishedTo:
    """A successful `project connect` records `published_to:` in the local
    `dbt_charts.yml` so a cold clone states its own Cloud destination instead
    of relying on git-remote inference -- but only in a checkout of the
    repository it just connected, and never at the cost of the result.
    """

    URL = "https://cloud.example/acme-data/analytics/"

    def test_git_url_path_writes_published_to(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _checkout(tmp_path, monkeypatch)
        yml = tmp_path / "dbt_charts.yml"
        yml.write_text("cache:\n  path: x\n")
        monkeypatch.chdir(tmp_path)
        api.add("POST", "/api/orgs/acme-data/projects", _project(), status=201)

        result = runner.invoke(app, _connect("--git-url", GIT_URL))

        assert result.exit_code == 0, out(result)
        assert f"Recorded published_to: {self.URL}" in out(result)
        assert f'published_to: "{self.URL}"' in yml.read_text()

    def test_browser_pick_path_writes_published_to(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _checkout(tmp_path, monkeypatch)
        yml = tmp_path / "dbt_charts.yml"
        yml.write_text("cache:\n  path: x\n")
        monkeypatch.chdir(tmp_path)
        pick = {
            "repo_id": "42",
            "full_name": "acme/analytics",
            "default_branch": "main",
            "private": True,
            "picked_at": "2026-08-30T00:00:00Z",
            "dbt_roots": [],
        }
        api.add("GET", "/api/orgs/acme-data/github/pick", pick)
        api.add(
            "POST", "/api/orgs/acme-data/projects/from-pick", _project(), status=201
        )

        result = runner.invoke(app, _connect("--poll-interval", "1"))

        assert result.exit_code == 0, out(result)
        assert f"Recorded published_to: {self.URL}" in out(result)
        assert f'published_to: "{self.URL}"' in yml.read_text()

    def test_never_writes_into_a_checkout_of_another_repository(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`published_to` outranks the git-remote match, so recording one
        repository's destination in another's tracked config would point every
        later verb in that repository at the wrong project."""
        _checkout(tmp_path, monkeypatch, remote="https://github.com/other/unrelated")
        yml = tmp_path / "dbt_charts.yml"
        yml.write_text("cache:\n  path: x\n")
        monkeypatch.chdir(tmp_path)
        api.add("POST", "/api/orgs/acme-data/projects", _project(), status=201)

        result = runner.invoke(app, _connect("--git-url", GIT_URL))

        assert result.exit_code == 0, out(result)
        assert "published_to" not in yml.read_text()
        assert "GitHub: acme/analytics" in out(result)
        assert f'published_to: "{self.URL}"' in out(result)

    def test_prints_the_key_to_add_when_no_dbt_charts_yml_exists(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _checkout(tmp_path, monkeypatch)
        monkeypatch.chdir(tmp_path)
        api.add("POST", "/api/orgs/acme-data/projects", _project(), status=201)

        result = runner.invoke(app, _connect("--git-url", GIT_URL))

        assert result.exit_code == 0, out(result)
        assert f'published_to: "{self.URL}"' in out(result)
        assert "No dbt_charts.yml yet" in out(result)
        assert not (tmp_path / "dbt_charts.yml").exists()

    def test_prints_instructions_with_no_local_git_checkout(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `--git-url` connect run with no local clone (e.g. from an
        unrelated directory) has nowhere on disk to write -- it must still
        name the exact key/value to add, never fail or write outside cwd."""
        monkeypatch.chdir(tmp_path)
        api.add("POST", "/api/orgs/acme-data/projects", _project(), status=201)

        result = runner.invoke(app, _connect("--git-url", GIT_URL))

        assert result.exit_code == 0, out(result)
        assert f'published_to: "{self.URL}"' in out(result)
        assert "Could not find a local git checkout" in out(result)

    def test_a_failed_write_never_replaces_the_connect_result(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The project exists in Cloud by the time this runs: a file this
        cannot edit safely must report itself, not abort the verb."""
        _checkout(tmp_path, monkeypatch)
        yml = tmp_path / "dbt_charts.yml"
        yml.write_text("a: 1\n---\nb: 2\n")
        monkeypatch.chdir(tmp_path)
        api.add("POST", "/api/orgs/acme-data/projects", _project(), status=201)

        result = runner.invoke(app, _connect("--git-url", GIT_URL))

        assert result.exit_code == 0, out(result)
        assert "Connected analytics" in out(result)
        assert "Next: " in out(result)
        assert "published_to was not recorded" in out(result)

    def test_json_stays_the_contract_model_when_the_write_fails(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `--json` caller that never receives the project cannot learn the
        slug to sync or clean up, and retries into a second project."""
        _checkout(tmp_path, monkeypatch)
        (tmp_path / "dbt_charts.yml").write_text("a: 1\n---\nb: 2\n")
        monkeypatch.chdir(tmp_path)
        api.add("POST", "/api/orgs/acme-data/projects", _project(), status=201)

        result = runner.invoke(app, _connect("--git-url", GIT_URL, "--json"))

        assert result.exit_code == 0, out(result)
        assert ProjectSummary.model_validate_json(result.stdout).slug == "analytics"
        assert "published_to was not recorded" in result.stderr

    def test_refuses_to_record_a_value_the_config_schema_would_reject(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A path-bearing --host would write a three-segment value that
        `Config` rejects, breaking `dct render` for the whole project."""
        _checkout(tmp_path, monkeypatch)
        yml = tmp_path / "dbt_charts.yml"
        yml.write_text("cache:\n  path: x\n")
        monkeypatch.chdir(tmp_path)
        api.add("POST", "/dct/api/orgs/acme-data/projects", _project(), status=201)

        result = runner.invoke(
            app, _connect("--git-url", GIT_URL, "--host", "https://cloud.example/dct")
        )

        assert result.exit_code == 0, out(result)
        assert "published_to was not recorded" in out(result)
        assert "published_to" not in yml.read_text()


class TestCloudVerbsInAHalfEditedCheckout:
    def test_an_unparseable_dbt_charts_yml_is_a_reported_error(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every context-resolving verb reads dbt_charts.yml now; a file
        mid-edit must fail the way every other `dct cloud` failure does."""
        (tmp_path / "dbt_charts.yml").write_text("published_to: [unclosed\n")
        monkeypatch.chdir(tmp_path)
        api.add("GET", "/api/orgs", {"organizations": []})

        result = runner.invoke(app, ["cloud", "status"])

        assert result.exit_code != 0
        assert "Traceback" not in out(result)
        assert str(tmp_path / "dbt_charts.yml") in out(result)

    def test_whoami_notes_it_and_still_answers(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """whoami is the orientation command, not a resolution gate: a record
        it cannot read is a note beside the answer, never the answer."""
        (tmp_path / "dbt_charts.yml").write_text("published_to: [unclosed\n")
        monkeypatch.chdir(tmp_path)
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )

        result = runner.invoke(app, ["cloud", "whoami"])

        assert result.exit_code == 0, out(result)
        assert "acme-data" in out(result)
        assert str(tmp_path / "dbt_charts.yml") in out(result)

    def test_the_json_error_body_is_the_contract_shape(
        self, api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text("published_to: [unclosed\n")
        monkeypatch.chdir(tmp_path)
        api.add("GET", "/api/orgs", {"organizations": []})

        result = runner.invoke(app, ["cloud", "status", "--json"])

        assert result.exit_code != 0
        assert ApiError.model_validate_json(result.stdout).code == "invalid_request"


class TestProjectConnectGithub:
    PICK = {
        "repo_id": "42",
        "full_name": "acme/analytics",
        "default_branch": "main",
        "private": True,
        "picked_at": "2026-08-30T00:00:00Z",
        "dbt_roots": [{"path": "warehouse", "is_dct": True}],
    }

    def _pick(self, *roots: dict[str, object]) -> dict[str, object]:
        return {**self.PICK, "dbt_roots": list(roots)}

    def test_a_repo_with_no_dbt_root_connects_at_the_repo_root(
        self, api: FakeApi
    ) -> None:
        api.add("GET", "/api/orgs/acme-data/github/pick", self._pick())
        api.add(
            "POST", "/api/orgs/acme-data/projects/from-pick", _project(), status=201
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--poll-interval",
                "1",
            ],
        )
        assert result.exit_code == 0, out(result)
        body = api.body("POST", "/api/orgs/acme-data/projects/from-pick")
        assert body["dbt_root_choice"] == DBT_ROOT_REPO_ROOT

    def test_two_dbt_roots_refuse_to_pick_one_and_name_both(self, api: FakeApi) -> None:
        """Which dbt project to connect is the user's call — a repo with two of
        them has no right answer, and guessing connects the wrong boards."""
        api.add(
            "GET",
            "/api/orgs/acme-data/github/pick",
            self._pick(
                {"path": "warehouse", "is_dct": True},
                {"path": "marts", "is_dct": True},
            ),
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--poll-interval",
                "1",
            ],
        )
        assert result.exit_code != 0
        message = out(result)
        assert "--root warehouse" in message
        assert "--root marts" in message
        assert ("POST", "/api/orgs/acme-data/projects/from-pick") not in [
            (method, path) for method, path, _payload in api.calls
        ]

    def test_a_nonpositive_poll_interval_is_rejected_before_any_call(
        self, api: FakeApi
    ) -> None:
        """--poll-interval below the floor is rejected up front, naming the
        floor -- never silently rewritten (no clamp) and never allowed to
        busy-loop; the one validation site is this CLI check."""
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--poll-interval",
                "0",
            ],
        )
        assert result.exit_code != 0
        assert "poll-interval" in out(result)
        assert api.calls == []

        subsecond = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--poll-interval",
                "0.5",
            ],
        )
        assert subsecond.exit_code != 0
        assert "at least" in out(subsecond)
        assert api.calls == []

    def test_an_explicit_root_overrides_the_discovered_ones(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/github/pick",
            self._pick(
                {"path": "warehouse", "is_dct": True},
                {"path": "marts", "is_dct": True},
            ),
        )
        api.add(
            "POST", "/api/orgs/acme-data/projects/from-pick", _project(), status=201
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--poll-interval",
                "1",
                "--root",
                "marts",
            ],
        )
        assert result.exit_code == 0, out(result)
        body = api.body("POST", "/api/orgs/acme-data/projects/from-pick")
        assert body["dbt_root_choice"] == DBT_ROOT_OTHER
        assert body["git_subdirectory"] == "marts"

    def test_prints_the_handoff_url_then_resumes_from_the_pick(
        self, api: FakeApi
    ) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/github/pick",
            {"code": "not_found", "message": "No repository pick.", "field_errors": {}},
            status=404,
        )
        api.add("GET", "/api/orgs/acme-data/github/pick", self.PICK)
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/from-pick",
            _project(),
            status=201,
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--poll-interval",
                "1",
            ],
        )
        assert result.exit_code == 0, out(result)
        assert (
            "https://cloud.example/acme-data/github/connect/?landing=terminal"
            in result.output
        )
        body = api.body("POST", "/api/orgs/acme-data/projects/from-pick")
        assert body["slug"] == "analytics"
        assert body["dbt_root_choice"] == "warehouse"

    def test_a_timed_out_wait_says_so_and_exits_non_zero(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/github/pick",
            {"code": "not_found", "message": "No repository pick.", "field_errors": {}},
            status=404,
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--poll-interval",
                "1",
                "--timeout",
                "0",
            ],
        )
        assert result.exit_code == 1
        assert (
            "https://cloud.example/acme-data/github/connect/?landing=terminal"
            in out(result)
        )

    def test_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add("GET", "/api/orgs/acme-data/github/pick", self.PICK)
        api.add(
            "POST", "/api/orgs/acme-data/projects/from-pick", _project(), status=201
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--org",
                "acme-data",
                "--poll-interval",
                "1",
                "--json",
            ],
        )
        assert result.exit_code == 0, out(result)
        assert ProjectSummary.model_validate_json(result.stdout).slug == "analytics"


class TestProjectConnectStartWait:
    """`--start`/`--wait` split the browser repo pick across two invocations,
    exactly like `login`'s FR-67 split (#8662) -- see FR-82. The server
    handle here is the org itself: `github/pick` is keyed by (caller, org),
    not a token this client mints, so `--wait` just resumes polling it."""

    PICK = {
        "repo_id": "42",
        "full_name": "acme/analytics",
        "default_branch": "main",
        "private": True,
        "picked_at": "2026-08-30T00:00:00Z",
        "dbt_roots": [{"path": "warehouse", "is_dct": True}],
    }

    def _pending(self) -> PendingConnect:
        return PendingConnect(org="acme-data", host="https://cloud.example")

    def test_start_prints_the_url_and_does_not_poll(self, api: FakeApi) -> None:
        result = runner.invoke(app, _connect("--start"))

        assert result.exit_code == 0, out(result)
        assert result.stdout.strip() == (
            "https://cloud.example/acme-data/github/connect/?landing=terminal"
        )
        pending = read_pending_connect()
        assert pending is not None
        assert pending.org == "acme-data"
        assert pending.host == "https://cloud.example"
        assert api.calls == []

    def test_start_hints_at_wait_on_stderr_not_stdout(self, api: FakeApi) -> None:
        """An agent that runs --start, hands over the URL, then authors
        boards for a while can forget the second step -- the project never
        gets created and `status` sits at missing_project (FR-87). The hint
        goes to stderr so it never pollutes a
        `URL=$(dct cloud project connect --start)` capture."""
        result = runner.invoke(app, _connect("--start"))

        assert result.exit_code == 0, out(result)
        assert "dct cloud project connect --wait" in result.stderr
        assert "--wait" not in result.stdout

    def test_start_persists_the_project_fields_wait_will_need(
        self, api: FakeApi
    ) -> None:
        result = runner.invoke(
            app,
            _connect(
                "--start", "--name", "Analytics", "--slug", "an", "--trunk", "trunk"
            ),
        )

        assert result.exit_code == 0, out(result)
        pending = read_pending_connect()
        assert pending is not None
        assert pending.name == "Analytics"
        assert pending.slug == "an"
        assert pending.trunk == "trunk"

    def test_start_json_emits_the_project_connect_started_shape(
        self, api: FakeApi
    ) -> None:
        result = runner.invoke(app, _connect("--start", "--json"))

        assert result.exit_code == 0, out(result)
        parsed = ProjectConnectStarted.model_validate_json(result.stdout)
        assert parsed.org == "acme-data"
        assert parsed.url == (
            "https://cloud.example/acme-data/github/connect/?landing=terminal"
        )

    def test_wait_polls_the_pending_org_and_finishes_the_project(
        self, api: FakeApi
    ) -> None:
        save_pending_connect(self._pending())
        api.add("GET", "/api/orgs/acme-data/github/pick", self.PICK)
        api.add(
            "POST", "/api/orgs/acme-data/projects/from-pick", _project(), status=201
        )

        result = runner.invoke(app, ["cloud", "project", "connect", "--wait"])

        assert result.exit_code == 0, out(result)
        body = api.body("POST", "/api/orgs/acme-data/projects/from-pick")
        assert body["slug"] == "analytics"
        assert read_pending_connect() is None

    def test_wait_json_emits_the_project_summary(self, api: FakeApi) -> None:
        save_pending_connect(self._pending())
        api.add("GET", "/api/orgs/acme-data/github/pick", self.PICK)
        api.add(
            "POST", "/api/orgs/acme-data/projects/from-pick", _project(), status=201
        )

        result = runner.invoke(app, ["cloud", "project", "connect", "--wait", "--json"])

        assert result.exit_code == 0, out(result)
        assert ProjectSummary.model_validate_json(result.stdout).slug == "analytics"

    def test_wait_with_no_pending_connect_is_a_clear_error(self, api: FakeApi) -> None:
        result = runner.invoke(app, ["cloud", "project", "connect", "--wait"])

        assert result.exit_code != 0
        assert "--start" in out(result)
        assert api.calls == []

    def test_wait_ignores_org_and_host_flags_using_the_pending_ones(
        self, api: FakeApi
    ) -> None:
        """The whole point of the split (FR-82): a concurrent shell's
        `--org`/`--host` must never leak into a `--wait` this call did not
        `--start` -- the pending record, not ambient flags, decides."""
        save_pending_connect(self._pending())
        api.add("GET", "/api/orgs/acme-data/github/pick", self.PICK)
        api.add(
            "POST", "/api/orgs/acme-data/projects/from-pick", _project(), status=201
        )

        result = runner.invoke(
            app,
            [
                "cloud",
                "project",
                "connect",
                "--wait",
                "--org",
                "some-other-org",
                "--host",
                "https://evil.example",
            ],
        )

        assert result.exit_code == 0, out(result)
        assert ("GET", "/api/orgs/some-other-org/github/pick") not in [
            (method, path) for method, path, _payload in api.calls
        ]

    def test_a_timed_out_wait_keeps_the_pending_record(self, api: FakeApi) -> None:
        """Unlike login's device code, the pick has no server-side expiry --
        a local `--wait` timeout is just this invocation giving up, so a
        second `--wait` should resume the same pending connect rather than
        forcing a fresh `--start`."""
        save_pending_connect(self._pending())
        api.add(
            "GET",
            "/api/orgs/acme-data/github/pick",
            {"code": "not_found", "message": "No repository pick.", "field_errors": {}},
            status=404,
        )

        result = runner.invoke(
            app,
            ["cloud", "project", "connect", "--wait", "--timeout", "0"],
        )

        assert result.exit_code != 0
        assert read_pending_connect() is not None

    def test_start_and_wait_together_are_rejected(self, api: FakeApi) -> None:
        result = runner.invoke(app, _connect("--start", "--wait"))

        assert result.exit_code != 0
        assert api.calls == []

    def test_start_with_a_git_url_is_rejected(self, api: FakeApi) -> None:
        """`--git-url` is already headless -- there is no browser pick to
        start or wait for."""
        result = runner.invoke(app, _connect("--start", "--git-url", GIT_URL))

        assert result.exit_code != 0
        assert api.calls == []


class TestConnections:
    def test_lists_connections_with_their_test_state(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/connections",
            {"connections": [_connection()]},
        )
        result = runner.invoke(app, ["cloud", "connections", "--org", "acme-data"])
        assert result.exit_code == 0, out(result)
        assert "acme-bigquery" in result.output
        assert "bigquery" in result.output

    def test_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add(
            "GET", "/api/orgs/acme-data/connections", {"connections": [_connection()]}
        )
        result = runner.invoke(
            app, ["cloud", "connections", "--org", "acme-data", "--json"]
        )
        assert ConnectionList.model_validate_json(result.stdout).connections

    def test_json_emits_a_none_field_as_a_null_key_like_the_server(
        self, api: FakeApi
    ) -> None:
        """HIGH-4: the CLI's --json must serialize exactly like the server
        (`transport.ok`'s plain `model_dump(mode="json")`, no exclude_none) --
        an untested connection's `last_test_success: null` must be a key a
        client can read, not a key that silently vanished."""
        untested = _connection()
        untested["last_test_success"] = None
        api.add("GET", "/api/orgs/acme-data/connections", {"connections": [untested]})
        result = runner.invoke(
            app, ["cloud", "connections", "--org", "acme-data", "--json"]
        )
        assert result.exit_code == 0, out(result)
        data = json.loads(result.stdout)
        assert "last_test_success" in data["connections"][0]
        assert data["connections"][0]["last_test_success"] is None

    def test_create_reads_the_secret_from_a_key_file(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        keyfile = tmp_path / "key.json"
        keyfile.write_text('{"type": "service_account"}', encoding="utf-8")
        api.add(
            "POST",
            "/api/orgs/acme-data/connections",
            {"success": True, "message": "", "connection": _connection()},
            status=201,
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "create",
                "--org",
                "acme-data",
                "--type",
                "bigquery",
                "--keyfile",
                str(keyfile),
                "--set",
                "project=acme-gcp",
                "--set",
                "dataset=analytics",
            ],
        )
        assert result.exit_code == 0, out(result)
        body = api.body("POST", "/api/orgs/acme-data/connections")
        assert body["connection_type"] == "bigquery"
        assert body["credentials_json"] == '{"type": "service_account"}'
        assert body["dataset"] == "analytics"

    def test_create_takes_the_display_name_through_name(self, api: FakeApi) -> None:
        """The alias the server slugifies is a flag of its own — the slug is
        how every later verb addresses the connection."""
        api.add(
            "POST",
            "/api/orgs/acme-data/connections",
            {"success": True, "message": "", "connection": _connection()},
            status=201,
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "create",
                "--org",
                "acme-data",
                "--type",
                "postgres",
                "--password-stdin",
                "--name",
                "Warehouse",
                "--set",
                "host=db.example",
            ],
            input="hunter2\n",
        )
        assert result.exit_code == 0, out(result)
        assert api.body("POST", "/api/orgs/acme-data/connections")["name"] == (
            "Warehouse"
        )

    def test_the_display_name_is_never_taken_through_set(self, api: FakeApi) -> None:
        """One spelling per field, the same rule that sends `--set password=`
        to `--password-stdin`."""
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "create",
                "--org",
                "acme-data",
                "--type",
                "postgres",
                "--password-stdin",
                "--set",
                "name=Warehouse",
                "--set",
                "host=db.example",
            ],
            input="hunter2\n",
        )
        assert result.exit_code != 0
        assert "--name" in out(result)
        assert not api.calls

    def test_an_empty_display_name_is_refused_rather_than_derived(
        self, api: FakeApi
    ) -> None:
        """Blank reaches the server as "derive one for me", which is what
        omitting the flag already says. `--name "$ALIAS"` with ALIAS unset is a
        caller bug, and a silently auto-named connection is the wrong answer."""
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "create",
                "--org",
                "acme-data",
                "--type",
                "postgres",
                "--password-stdin",
                "--name",
                "   ",
                "--set",
                "host=db.example",
            ],
            input="hunter2\n",
        )
        assert result.exit_code != 0
        assert "blank" in out(result)
        assert not api.calls

    def test_create_reads_a_password_from_stdin(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/connections",
            {"success": True, "message": "", "connection": _connection("wh")},
            status=201,
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "create",
                "--org",
                "acme-data",
                "--type",
                "postgres",
                "--password-stdin",
                "--set",
                "host=db.example",
                "--set",
                "database=analytics",
                "--set",
                "username=dbt",
            ],
            input="hunter2\n",
        )
        assert result.exit_code == 0, out(result)
        assert api.body("POST", "/api/orgs/acme-data/connections")["password"] == (
            "hunter2"
        )

    def test_create_reads_a_password_from_a_named_env_var(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WH_PASSWORD", "hunter2")
        api.add(
            "POST",
            "/api/orgs/acme-data/connections",
            {"success": True, "message": "", "connection": _connection("wh")},
            status=201,
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "create",
                "--org",
                "acme-data",
                "--type",
                "postgres",
                "--password-env",
                "WH_PASSWORD",
                "--set",
                "host=db.example",
            ],
        )
        assert result.exit_code == 0, out(result)
        assert api.body("POST", "/api/orgs/acme-data/connections")["password"] == (
            "hunter2"
        )

    def test_an_unset_named_env_var_is_a_loud_error(self, api: FakeApi) -> None:
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "create",
                "--org",
                "acme-data",
                "--type",
                "postgres",
                "--password-env",
                "NOT_SET_ANYWHERE",
            ],
        )
        assert result.exit_code != 0
        assert "NOT_SET_ANYWHERE" in out(result)

    @pytest.mark.parametrize("field", ["password", "credentials_json", "private_key"])
    def test_a_secret_is_never_accepted_as_a_flag_value(
        self, api: FakeApi, field: str
    ) -> None:
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "create",
                "--org",
                "acme-data",
                "--type",
                "postgres",
                "--set",
                f"{field}=hunter2",
            ],
        )
        assert result.exit_code != 0
        assert "--keyfile" in out(result) or "--password-stdin" in out(result)
        assert not api.calls

    def test_two_secret_inputs_at_once_are_refused(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        keyfile = tmp_path / "key.json"
        keyfile.write_text("{}", encoding="utf-8")
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "create",
                "--org",
                "acme-data",
                "--type",
                "bigquery",
                "--keyfile",
                str(keyfile),
                "--password-stdin",
            ],
            input="hunter2\n",
        )
        assert result.exit_code != 0
        assert not api.calls

    def test_a_failing_create_is_a_loud_error(self, api: FakeApi) -> None:
        """A create whose credential check failed exits non-zero and prints the
        server's own message, exactly like the re-test verb below — the setup
        script that reads `$?` must never carry on over a dead warehouse."""
        api.add(
            "POST",
            "/api/orgs/acme-data/connections",
            {
                "code": "connection_test_failed",
                "message": (
                    "bigquery: Could not open the warehouse: Unable to load PEM"
                    " file. The connection was not saved. You can run the same"
                    " command again."
                ),
                "field_errors": {},
            },
            status=422,
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "create",
                "--org",
                "acme-data",
                "--type",
                "postgres",
                "--password-stdin",
                "--set",
                "host=db.example",
            ],
            input="hunter2\n",
        )
        assert result.exit_code == 1
        assert "Unable to load PEM file" in out(result)
        assert "was not saved" in out(result)

    def test_an_inconclusive_create_is_a_loud_error_that_names_the_fix(
        self, api: FakeApi
    ) -> None:
        """A create whose test could not complete in time is still a non-zero
        exit (never silently treated as success), but the row was NOT
        discarded — unlike test_a_failing_create_is_a_loud_error above — so
        the message must say so and name the verb that resolves it, not
        invite a bare retry that would collide on the derived slug."""
        api.add(
            "POST",
            "/api/orgs/acme-data/connections",
            {
                "code": "connection_test_inconclusive",
                "message": (
                    "Could not verify your warehouse connection in time. The"
                    " connection was saved as 'warehouse'. Once the warehouse"
                    " answers, run `dct cloud connection test warehouse` to"
                    " confirm it works."
                ),
                "field_errors": {},
            },
            status=422,
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "create",
                "--org",
                "acme-data",
                "--type",
                "postgres",
                "--password-stdin",
                "--set",
                "host=db.example",
            ],
            input="hunter2\n",
        )
        assert result.exit_code == 1
        assert "was saved as 'warehouse'" in out(result)
        assert "dct cloud connection test warehouse" in out(result)

    def test_a_failing_test_is_a_loud_error(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/connections/acme-bigquery/test",
            {
                "code": "connection_test_failed",
                "message": "Access denied for dataset analytics.",
                "field_errors": {},
            },
            status=422,
        )
        result = runner.invoke(
            app,
            ["cloud", "connection", "test", "acme-bigquery", "--org", "acme-data"],
        )
        assert result.exit_code == 1
        assert "Access denied for dataset analytics." in out(result)

    def test_test_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/connections/acme-bigquery/test",
            {"success": True, "message": "", "connection": _connection()},
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "test",
                "acme-bigquery",
                "--org",
                "acme-data",
                "--json",
            ],
        )
        assert ConnectionTestResult.model_validate_json(result.stdout).success is True

    def test_schema_refresh_reports_what_happened(self, api: FakeApi) -> None:
        """HIGH-3: pins `dct cloud connection schema-refresh` -> POST
        .../schema-refresh -> the message on stdout. FakeApi's routing is
        keyed on the exact (method, path) pair, so a client-method swap
        (e.g. calling `test_connection` here instead of
        `refresh_connection_schema`) fails this test with an "unrouted"
        AssertionError rather than passing silently."""
        api.add(
            "POST",
            "/api/orgs/acme-data/connections/acme-bigquery/schema-refresh",
            {"queued": True, "message": "Schema refresh queued."},
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "schema-refresh",
                "acme-bigquery",
                "--org",
                "acme-data",
            ],
        )
        assert result.exit_code == 0, out(result)
        assert "Schema refresh queued." in result.output

    def test_schema_refresh_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/connections/acme-bigquery/schema-refresh",
            {"queued": True, "message": "Schema refresh queued."},
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "schema-refresh",
                "acme-bigquery",
                "--org",
                "acme-data",
                "--json",
            ],
        )
        assert result.exit_code == 0, out(result)
        assert SyncResult.model_validate_json(result.stdout).queued is True


class TestSources:
    SOURCES = {
        "sources": [
            {
                "name": "db",
                "connection_slug": None,
                "schema_override": "",
                "is_default": True,
            }
        ],
        "unmapped_count": 1,
    }

    def test_lists_sources_and_flags_the_unmapped_ones(self, api: FakeApi) -> None:
        api.add("GET", "/api/orgs/acme-data/projects/analytics/sources", self.SOURCES)
        result = runner.invoke(
            app, ["cloud", "sources", "--org", "acme-data", "--project", "analytics"]
        )
        assert result.exit_code == 0, out(result)
        assert "db" in result.output
        assert "unmapped" in result.output.lower()

    def test_repo_resolved_file_sources_are_listed(self, api: FakeApi) -> None:
        """A project whose only sources are files has an empty `sources` list;
        printing "declares no sources" there would be a lie."""
        api.add(
            "GET",
            "/api/orgs/acme-data/projects/analytics/sources",
            {"sources": [], "unmapped_count": 0, "file_sources": ["marts"]},
        )
        result = runner.invoke(
            app, ["cloud", "sources", "--org", "acme-data", "--project", "analytics"]
        )
        assert result.exit_code == 0, out(result)
        assert "marts" in result.output
        assert "repo" in result.output.lower()

    def test_map_posts_the_source_and_connection(self, api: FakeApi) -> None:
        mapped = {
            "sources": [
                {
                    "name": "db",
                    "connection_slug": "acme-bigquery",
                    "schema_override": "",
                    "is_default": True,
                }
            ],
            "unmapped_count": 0,
        }
        api.add("POST", "/api/orgs/acme-data/projects/analytics/sources", mapped)
        result = runner.invoke(
            app,
            [
                "cloud",
                "source",
                "map",
                "db",
                "acme-bigquery",
                "--org",
                "acme-data",
                "--project",
                "analytics",
            ],
        )
        assert result.exit_code == 0, out(result)
        body = api.body("POST", "/api/orgs/acme-data/projects/analytics/sources")
        assert body == {"name": "db", "connection": "acme-bigquery"}

    def test_map_with_explicit_empty_schema_sends_the_clearing_key(
        self, api: FakeApi
    ) -> None:
        """HIGH-5: --schema "" is a deliberate clear, distinct from omitting
        --schema entirely -- both must reach the server as different
        payloads, not collapse to the same `schema or ""`."""
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/sources",
            {"sources": [], "unmapped_count": 0},
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "source",
                "map",
                "db",
                "acme-bigquery",
                "--schema",
                "",
                "--org",
                "acme-data",
                "--project",
                "analytics",
            ],
        )
        assert result.exit_code == 0, out(result)
        body = api.body("POST", "/api/orgs/acme-data/projects/analytics/sources")
        assert body == {
            "name": "db",
            "connection": "acme-bigquery",
            "schema_override": "",
        }

    def test_map_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/sources",
            {"sources": [], "unmapped_count": 0},
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "source",
                "map",
                "db",
                "acme-bigquery",
                "--org",
                "acme-data",
                "--project",
                "analytics",
                "--json",
            ],
        )
        assert SourceList.model_validate_json(result.stdout).unmapped_count == 0


class TestRender:
    def test_reports_what_it_started(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/render",
            {"started": 14, "unrendered_remaining": 0},
        )
        result = runner.invoke(
            app, ["cloud", "render", "--org", "acme-data", "--project", "analytics"]
        )
        assert result.exit_code == 0, out(result)
        assert "14" in result.output

    def test_a_blocked_project_gets_a_reason_not_a_bare_zero(
        self, api: FakeApi
    ) -> None:
        """``Started 0 board render(s); 0 still unrendered.`` was the
        whole answer on a project whose connection had never passed a test."""
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/render",
            {
                "started": 0,
                "unrendered_remaining": 0,
                "blocked": (
                    "Source `warehouse` maps to connection `prod-dwh`, whose last"
                    " test failed. Run `dct cloud connection test prod-dwh`."
                ),
            },
        )
        result = runner.invoke(
            app, ["cloud", "render", "--org", "acme-data", "--project", "analytics"]
        )
        assert result.exit_code == 0, out(result)
        assert "whose last test failed" in result.output
        assert "dct cloud connection test prod-dwh" in result.output

    def test_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/render",
            {"started": 2, "unrendered_remaining": 3},
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "render",
                "--org",
                "acme-data",
                "--project",
                "analytics",
                "--json",
            ],
        )
        assert RenderResult.model_validate_json(result.stdout).started == 2

    def test_force_asks_the_server_to_rerender_every_board(self, api: FakeApi) -> None:
        path = "/api/orgs/acme-data/projects/analytics/render/all"
        api.add("POST", path, {"started": 47, "unrendered_remaining": 0})
        result = runner.invoke(
            app,
            [
                "cloud",
                "render",
                "--org",
                "acme-data",
                "--project",
                "analytics",
                "--force",
            ],
        )
        assert result.exit_code == 0, out(result)
        assert ("POST", path, {}) in api.calls
        assert "Re-rendering 47" in result.output

    def test_force_still_reports_a_connection_that_has_not_passed(
        self, api: FakeApi
    ) -> None:
        """Forcing starts the renders; it does not make the warehouse answer,
        so the reason is printed under the re-rendering count too. The skill
        tells an agent `--force` will not clear a block, and this is where it
        sees that."""
        path = "/api/orgs/acme-data/projects/analytics/render/all"
        api.add(
            "POST",
            path,
            {
                "started": 3,
                "unrendered_remaining": 0,
                "blocked": (
                    "Source `warehouse` maps to connection `prod-dwh`, whose"
                    " last test failed. Run `dct cloud connection test"
                    " prod-dwh`."
                ),
            },
        )

        result = runner.invoke(
            app,
            [
                "cloud",
                "render",
                "--org",
                "acme-data",
                "--project",
                "analytics",
                "--force",
            ],
        )

        assert result.exit_code == 0, out(result)
        assert "Re-rendering 3" in result.output
        assert "dct cloud connection test prod-dwh" in result.output

    def test_without_force_the_server_starts_only_unrendered_boards(
        self, api: FakeApi
    ) -> None:
        path = "/api/orgs/acme-data/projects/analytics/render"
        api.add("POST", path, {"started": 0, "unrendered_remaining": 0})
        runner.invoke(
            app, ["cloud", "render", "--org", "acme-data", "--project", "analytics"]
        )
        assert [c[1] for c in api.calls if c[0] == "POST"] == [path]


class TestFailureSurfaces:
    def test_without_a_credential_the_error_names_the_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("DCT_CLOUD_TOKEN", raising=False)
        result = runner.invoke(app, ["cloud", "orgs"])
        assert result.exit_code == 1
        message = out(result)
        assert "config.yml" in message
        assert "DCT_CLOUD_TOKEN" in message

    def test_an_unreachable_host_is_reported_not_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("DCT_CLOUD_TOKEN", "t0ken")

        def dead(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        def factory(config: CloudConfig, host: str | None = None) -> CloudClient:
            return CloudClient(
                host="https://cloud.example",
                token="t0ken",
                transport=httpx.MockTransport(dead),
            )

        monkeypatch.setattr(cloud_cmd, "client_from_config", factory)
        result = runner.invoke(app, ["cloud", "orgs"])
        assert result.exit_code == 1
        assert "cloud.example" in out(result)
        assert "connection refused" in out(result)

    def test_json_failures_are_the_contract_error_shape(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/status",
            {"code": "not_found", "message": "No organization.", "field_errors": {}},
            status=404,
        )
        result = runner.invoke(app, ["cloud", "status", "--org", "acme-data", "--json"])
        assert result.exit_code == 1
        assert ApiError.model_validate_json(result.stdout).message == "No organization."


class TestContextResolution:
    def test_the_repo_remote_picks_the_project(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            cloud_cmd, "git_remotes", _remotes("git@github.com:acme/analytics.git")
        )
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )
        api.add("GET", "/api/orgs/acme-data/projects", {"projects": [_project()]})
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/render",
            {"started": 1, "unrendered_remaining": 0},
        )
        result = runner.invoke(app, ["cloud", "render"])
        assert result.exit_code == 0, out(result)

    def test_an_ambiguous_repo_refuses_and_lists_candidates(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            cloud_cmd, "git_remotes", _remotes("git@github.com:acme/analytics.git")
        )
        api.add(
            "GET",
            "/api/orgs",
            {"organizations": [{"slug": "acme-data", "name": "Acme", "role": "ADMIN"}]},
        )
        api.add(
            "GET",
            "/api/orgs/acme-data/projects",
            {"projects": [_project(), _project("finance")]},
        )
        result = runner.invoke(app, ["cloud", "render"])
        assert result.exit_code == 1
        message = out(result)
        assert "--project analytics" in message
        assert "--project finance" in message

    def test_the_stored_default_is_used_when_nothing_else_answers(
        self, api: FakeApi
    ) -> None:
        save_config(CloudConfig(org="acme-data", project="analytics"))
        api.add(
            "POST",
            "/api/orgs/acme-data/projects/analytics/render",
            {"started": 0, "unrendered_remaining": 0},
        )
        result = runner.invoke(app, ["cloud", "render"])
        assert result.exit_code == 0, out(result)


class TestUseReportsConfigFailures:
    def test_an_unreadable_config_is_an_error_not_a_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        directory = tmp_path / "dbt-charts"
        directory.mkdir()
        (directory / "config.yml").write_text(
            "tokenn: sk-live-nope\n", encoding="utf-8"
        )

        result = runner.invoke(app, ["cloud", "use", "acme-data"])

        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "tokenn" in out(result)
        assert "sk-live-nope" not in out(result)


class TestMembers:
    def test_lists_members(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/members",
            {
                "members": [
                    {
                        "email": "a@example.com",
                        "name": "A",
                        "role": "admin",
                        "joined_at": "2026-01-01T00:00:00Z",
                    }
                ]
            },
        )
        result = runner.invoke(app, ["cloud", "members", "--org", "acme-data"])
        assert result.exit_code == 0, out(result)
        assert "a@example.com" in result.output

    def test_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add("GET", "/api/orgs/acme-data/members", {"members": []})
        result = runner.invoke(
            app, ["cloud", "members", "--org", "acme-data", "--json"]
        )
        assert MemberList.model_validate_json(result.stdout).members == []

    def test_invite_sends_the_role_and_the_single_email(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/members",
            {"email": "new@example.com", "role": "admin", "already_member": False},
            status=201,
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "member",
                "invite",
                "new@example.com",
                "--role",
                "admin",
                "--org",
                "acme-data",
            ],
        )
        assert result.exit_code == 0, out(result)
        body = api.body("POST", "/api/orgs/acme-data/members")
        assert body == {"emails": "new@example.com", "role": "admin"}

    def test_remove_a_member(self, api: FakeApi) -> None:
        api.add(
            "DELETE",
            "/api/orgs/acme-data/members/a@example.com",
            {"deleted": True, "message": "Removed a@example.com."},
        )
        result = runner.invoke(
            app,
            ["cloud", "member", "remove", "a@example.com", "--org", "acme-data"],
        )
        assert result.exit_code == 0, out(result)
        assert "Removed" in result.output

    def test_change_a_members_role(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/members/a@example.com/role",
            {
                "email": "a@example.com",
                "name": "A",
                "role": "admin",
                "joined_at": "2026-01-01T00:00:00Z",
            },
        )
        result = runner.invoke(
            app,
            ["cloud", "member", "role", "a@example.com", "admin", "--org", "acme-data"],
        )
        assert result.exit_code == 0, out(result)
        assert api.body("POST", "/api/orgs/acme-data/members/a@example.com/role") == {
            "role": "admin"
        }


class TestInvites:
    def test_lists_invitations(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/invitations",
            {
                "invitations": [
                    {
                        "email": "new@example.com",
                        "role": "creator",
                        "created_at": "2026-01-01T00:00:00Z",
                        "expires_at": "2026-02-01T00:00:00Z",
                    }
                ]
            },
        )
        result = runner.invoke(app, ["cloud", "invites", "--org", "acme-data"])
        assert result.exit_code == 0, out(result)
        assert "new@example.com" in result.output

    def test_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add("GET", "/api/orgs/acme-data/invitations", {"invitations": []})
        result = runner.invoke(
            app, ["cloud", "invites", "--org", "acme-data", "--json"]
        )
        assert InvitationList.model_validate_json(result.stdout).invitations == []

    def test_revoke_an_invitation(self, api: FakeApi) -> None:
        api.add(
            "DELETE",
            "/api/orgs/acme-data/invitations/new@example.com",
            {"deleted": True, "message": "Revoked invitation to new@example.com."},
        )
        result = runner.invoke(
            app,
            ["cloud", "invite", "revoke", "new@example.com", "--org", "acme-data"],
        )
        assert result.exit_code == 0, out(result)

    def test_resend_an_invitation(self, api: FakeApi) -> None:
        api.add(
            "POST",
            "/api/orgs/acme-data/invitations/new@example.com/resend",
            {"email": "new@example.com", "role": "creator", "already_member": False},
        )
        result = runner.invoke(
            app,
            ["cloud", "invite", "resend", "new@example.com", "--org", "acme-data"],
        )
        assert result.exit_code == 0, out(result)
        assert "new@example.com" in result.output


def board_row(output: str) -> dict[str, str]:
    """Reassemble a one-board listing into {column header: cell}.

    Rich renders at 80 columns whenever stdout is not a terminal — every agent,
    every pipe — and five columns do not fit a board URL on one line there, so
    a cell continues on the lines below it *within its own column*. Reading a
    cell means slicing by the header's column offsets; splitting on runs of
    spaces interleaves the continuations of neighboring columns instead.

    Each cell comes back as its own lines joined by a newline — the caller
    rejoins with `""` for a folded value (slug, URL: broken mid-word, no space
    was there) or `" "` for a word-wrapped one (the STATUS diagnostic).
    """
    header, *rest = output.splitlines()
    starts: list[int] = []
    for name in header.split():
        # From the previous column's end, so a header word that is also a
        # substring of an earlier one cannot silently mis-slice every row.
        starts.append(header.index(name, starts[-1] + 1 if starts else 0))
    bounds = list(zip(starts, [*starts[1:], len(header) + 1], strict=True))
    cells: list[list[str]] = [[] for _ in starts]
    for line in rest:
        # Rich separates anything printed after the table with a blank line;
        # past it the text is prose (the version-skew advisory), not cells.
        if not line.strip():
            break
        for cell, (lo, hi) in zip(cells, bounds, strict=True):
            cell.append(line[lo:hi].strip())
    return dict(zip(header.split(), ["\n".join(c).strip() for c in cells], strict=True))


class TestBoards:
    def test_lists_boards_with_render_state(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/projects/analytics/boards",
            {
                "boards": [
                    {
                        "slug": "revenue",
                        "title": "Revenue",
                        "render_status": "warning",
                        "error": "warehouse unreachable",
                        "rendered_at": None,
                        "commit": None,
                        "url": "https://cloud.example/acme-data/analytics/d/revenue",
                    }
                ]
            },
        )
        result = runner.invoke(
            app,
            ["cloud", "boards", "--org", "acme-data", "--project", "analytics"],
        )
        assert result.exit_code == 0, out(result)
        row = board_row(result.output)
        assert row["SLUG"] == "revenue"
        # Word-wrapped across lines at 80 columns; wrapped is not lost.
        assert " ".join(row["STATUS"].split()) == "warning: warehouse unreachable"

    def test_json_render_status_matches_the_table(self, api: FakeApi) -> None:
        """The STATUS column and ``render_status`` in ``--json`` must
        agree for the same board -- an onboarding agent scripts off
        ``--json`` and must see the same value a human reads in the table."""
        api.add(
            "GET",
            "/api/orgs/acme-data/projects/analytics/boards",
            {
                "boards": [
                    {
                        "slug": "revenue",
                        "title": "Revenue",
                        "render_status": "ready",
                        "error": "",
                        "rendered_at": "2026-09-06T17:50:12.481000Z",
                        "commit": "8a2f27c9b1e4d0a3f5c6721e8d94ab3c15f7e0d2",
                        "url": "https://cloud.example/acme-data/analytics/d/revenue",
                    }
                ]
            },
        )
        table = runner.invoke(
            app,
            ["cloud", "boards", "--org", "acme-data", "--project", "analytics"],
        )
        assert table.exit_code == 0, out(table)
        assert board_row(table.output)["STATUS"] == "ready"

        as_json = runner.invoke(
            app,
            [
                "cloud",
                "boards",
                "--org",
                "acme-data",
                "--project",
                "analytics",
                "--json",
            ],
        )
        assert as_json.exit_code == 0, out(as_json)
        payload = json.loads(as_json.stdout)["boards"][0]
        assert payload["render_status"] == "ready"

    def test_a_blocked_board_names_the_connection_to_test(self, api: FakeApi) -> None:
        """the listing printed ``ready`` with an empty error over a
        credential that had never passed a test."""
        api.add(
            "GET",
            "/api/orgs/acme-data/projects/analytics/boards",
            {
                "boards": [
                    {
                        "slug": "revenue",
                        "title": "Revenue",
                        "render_status": "blocked",
                        "error": (
                            "Source `warehouse` maps to connection `prod-dwh`,"
                            " which has never been tested. Run `dct cloud"
                            " connection test prod-dwh`."
                        ),
                        "url": "https://cloud.example/acme-data/analytics/d/revenue",
                    }
                ]
            },
        )
        result = runner.invoke(
            app,
            ["cloud", "boards", "--org", "acme-data", "--project", "analytics"],
        )
        assert result.exit_code == 0, out(result)
        # RENDERED_AT and COMMIT narrowed STATUS, so the diagnostic now wraps
        # mid-sentence; it is all there, just not on one line.
        flowed = " ".join(result.output.split())
        assert "blocked" in flowed
        assert "never been tested" in flowed
        assert "dct cloud connection test prod-dwh" in flowed

    def test_a_chart_diagnostic_with_brackets_does_not_crash_the_table(
        self, api: FakeApi
    ) -> None:
        """`dct_console()` enables Rich markup, so an unescaped bracket in a
        table cell is swallowed at best and raises MarkupError at worst,
        taking the whole listing with it."""
        api.add(
            "GET",
            "/api/orgs/acme-data/projects/analytics/boards",
            {
                "boards": [
                    {
                        "slug": "revenue",
                        "title": "Revenue",
                        "render_status": "errored",
                        "error": "column [region] is 42.1 MB, over the cap",
                        "rendered_at": "2026-09-06T17:50:12.481000Z",
                        "commit": "8a2f27c9b1e4d0a3f5c6721e8d94ab3c15f7e0d2",
                        "url": "https://cloud.example/acme-data/analytics/d/revenue",
                    }
                ]
            },
        )

        result = runner.invoke(
            app,
            ["cloud", "boards", "--org", "acme-data", "--project", "analytics"],
        )

        assert result.exit_code == 0, out(result)
        assert "[region]" in result.output

    @pytest.mark.usefixtures("non_utc_tz")
    def test_names_the_render_time_and_commit_being_served(self, api: FakeApi) -> None:
        """RENDERED_AT is the reader's local time, not the API's UTC: a reader
        seven hours off UTC reading a UTC stamp concludes the render is seven
        hours old. `non_utc_tz` pins America/Phoenix, since every CI runner is
        UTC and the assertion would pass either way there."""
        api.add(
            "GET",
            "/api/orgs/acme-data/projects/analytics/boards",
            {
                "boards": [
                    {
                        "slug": "exec-overview",
                        "title": "Exec overview",
                        "render_status": "ready",
                        "error": "",
                        "rendered_at": "2026-09-06T17:50:12.481000Z",
                        "commit": "8a2f27c9b1e4d0a3f5c6721e8d94ab3c15f7e0d2",
                        "url": "https://cloud.example/acme-data/analytics/d/exec",
                    }
                ]
            },
        )

        result = runner.invoke(
            app,
            ["cloud", "boards", "--org", "acme-data", "--project", "analytics"],
        )

        assert result.exit_code == 0, out(result)
        assert "RENDERED_AT" in result.output
        assert "COMMIT" in result.output
        # Phoenix is UTC-7 year round, so 17:50 UTC reads as 10:50.
        assert "2026-09-06 10:50" in result.output
        assert "8a2f27c" in result.output
        assert "8a2f27c9b1e" not in result.output

    def test_a_board_with_no_complete_render_shows_dashes(self, api: FakeApi) -> None:
        """Null is "no render of anything yet", printed as a dash — never a
        blank cell that reads as a value, and never a nearby sha."""
        api.add(
            "GET",
            "/api/orgs/acme-data/projects/analytics/boards",
            {
                "boards": [
                    {
                        "slug": "new-board",
                        "title": "New board",
                        "render_status": "not_rendered",
                        "error": "",
                        "rendered_at": None,
                        "commit": None,
                        "url": "https://cloud.example/acme-data/analytics/d/new",
                    }
                ]
            },
        )

        result = runner.invoke(
            app,
            ["cloud", "boards", "--org", "acme-data", "--project", "analytics"],
        )

        assert result.exit_code == 0, out(result)
        line = next(ln for ln in result.output.splitlines() if "new-board" in ln)
        cells = re.split(r"\s{2,}", line.strip())
        assert cells[:4] == ["new-board", "not_rendered", "-", "-"]

    def test_a_long_slug_and_url_are_folded_never_truncated(self, api: FakeApi) -> None:
        """Rich ellipsizes a cell it cannot word-wrap, and neither a slug nor a
        URL contains a space. Five columns at the 80 Rich assumes off a
        terminal squeeze both, and both are identifiers a reader matches
        against something else: a truncated slug names no file and a truncated
        URL opens nothing."""
        url = "https://cloud.example/acme-data/analytics/d/quarterly-exec-overview"
        api.add(
            "GET",
            "/api/orgs/acme-data/projects/analytics/boards",
            {
                "boards": [
                    {
                        "slug": "quarterly-exec-overview",
                        "title": "Quarterly exec overview",
                        "render_status": "ready",
                        "error": "",
                        "rendered_at": "2026-09-06T17:50:12.481000Z",
                        "commit": "8a2f27c9b1e4d0a3f5c6721e8d94ab3c15f7e0d2",
                        "url": url,
                    }
                ]
            },
        )

        result = runner.invoke(
            app,
            ["cloud", "boards", "--org", "acme-data", "--project", "analytics"],
        )

        assert result.exit_code == 0, out(result)
        assert "…" not in result.output
        row = board_row(result.output)
        assert "".join(row["SLUG"].split()) == "quarterly-exec-overview"
        assert "".join(row["URL"].split()) == url

    def test_json_keeps_a_null_render_time_and_commit(self, api: FakeApi) -> None:
        """`--json` is the API's own shape: a null key must stay present, not
        vanish into "the key is just missing"."""
        api.add(
            "GET",
            "/api/orgs/acme-data/projects/analytics/boards",
            {
                "boards": [
                    {
                        "slug": "new-board",
                        "title": "New board",
                        "render_status": "not_rendered",
                        "error": "",
                        "rendered_at": None,
                        "commit": None,
                        "url": "https://cloud.example/acme-data/analytics/d/new",
                    }
                ]
            },
        )

        result = runner.invoke(
            app,
            [
                "cloud",
                "boards",
                "--org",
                "acme-data",
                "--project",
                "analytics",
                "--json",
            ],
        )

        payload = json.loads(result.stdout)["boards"][0]
        assert payload["rendered_at"] is None
        assert payload["commit"] is None
        board = BoardList.model_validate_json(result.stdout).boards[0]
        assert board.rendered_at is None
        assert board.commit is None

    def test_a_cloud_without_the_two_fields_still_lists_boards(
        self, api: FakeApi
    ) -> None:
        """A `dct` newer than the deployed Cloud must not lose the whole
        listing -- not even the three columns that Cloud does answer.
        `unknown`, not `-`: `-` means the board has never rendered."""
        api.add(
            "GET",
            "/api/orgs/acme-data/projects/analytics/boards",
            {
                "boards": [
                    {
                        "slug": "revenue",
                        "title": "Revenue",
                        "render_status": "ready",
                        "error": "",
                        "url": "https://cloud.example/acme-data/analytics/d/rev",
                    }
                ]
            },
        )

        result = runner.invoke(
            app,
            ["cloud", "boards", "--org", "acme-data", "--project", "analytics"],
        )

        assert result.exit_code == 0, out(result)
        row = board_row(result.output)
        assert row["SLUG"] == "revenue"
        assert row["RENDERED_AT"] == "unknown"
        assert row["COMMIT"] == "unknown"
        assert "older than this dct" in " ".join(result.output.split())

    def test_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add("GET", "/api/orgs/acme-data/projects/analytics/boards", {"boards": []})
        result = runner.invoke(
            app,
            [
                "cloud",
                "boards",
                "--org",
                "acme-data",
                "--project",
                "analytics",
                "--json",
            ],
        )
        assert BoardList.model_validate_json(result.stdout).boards == []


class TestGrants:
    def test_lists_grants(self, api: FakeApi) -> None:
        api.add(
            "GET",
            "/api/orgs/acme-data/grants",
            {
                "grants": [
                    {
                        "grant_id": "g-1",
                        "user_email": "a@example.com",
                        "application_name": "dct",
                        "scopes": ["orgs:admin"],
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ]
            },
        )
        result = runner.invoke(app, ["cloud", "grants", "--org", "acme-data"])
        assert result.exit_code == 0, out(result)
        assert "a@example.com" in result.output

    def test_json_is_the_contract_model(self, api: FakeApi) -> None:
        api.add("GET", "/api/orgs/acme-data/grants", {"grants": []})
        result = runner.invoke(app, ["cloud", "grants", "--org", "acme-data", "--json"])
        assert GrantList.model_validate_json(result.stdout).grants == []

    def test_revoke_a_grant_warns_when_it_is_this_calls_own(self, api: FakeApi) -> None:
        api.add(
            "DELETE",
            "/api/orgs/acme-data/grants/g-1",
            {
                "revoked": True,
                "self_revoked": True,
                "message": "Grant revoked. This credential dies with it.",
            },
        )
        result = runner.invoke(
            app, ["cloud", "grant", "revoke", "g-1", "--org", "acme-data"]
        )
        assert result.exit_code == 0, out(result)
        assert "dies" in result.output


class _FakeStdin:
    def isatty(self) -> bool:
        return True


class _FakeSys:
    """Stand-in for the ``sys`` module ``cloud.py`` sees, isatty()-only.

    CliRunner replaces the real ``sys.stdin`` with a fresh non-tty stream on
    every invocation, so a pre-invoke monkeypatch of the real module cannot
    force an interactive read. Shadowing the name ``cloud_cmd`` binds is the
    one seam that survives that swap; ``typer.prompt`` itself still reads the
    CliRunner-fed ``input=`` text through click's own machinery, untouched.
    """

    stdin = _FakeStdin()


class TestNonInteractiveDetection:
    def test_a_real_tty_is_interactive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cloud_cmd, "sys", _FakeSys())
        monkeypatch.setattr(cloud_cmd, "is_plain_output", lambda: False)
        assert cloud_cmd._non_interactive() is False

    def test_an_agent_pty_is_non_interactive_even_with_a_real_tty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bug: an agent harness (Claude Code, etc.) commonly drives a real
        pty, so `sys.stdin.isatty()` alone would say "interactive" and hang
        forever on a prompt nobody can answer. CLAUDECODE-and-friends
        detection (`is_plain_output()`, the same check every other
        agent-context probe in this CLI reuses) must still catch it."""
        monkeypatch.setattr(cloud_cmd, "sys", _FakeSys())
        monkeypatch.setattr(cloud_cmd, "is_plain_output", lambda: True)
        assert cloud_cmd._non_interactive() is True


class TestDestructiveConfirm:
    def test_yes_skips_the_prompt(self, api: FakeApi) -> None:
        api.add(
            "DELETE",
            "/api/orgs/acme-data/connections/warehouse",
            {"deleted": True, "message": "Deleted warehouse."},
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "delete",
                "warehouse",
                "--org",
                "acme-data",
                "--yes",
            ],
        )
        assert result.exit_code == 0, out(result)

    def test_without_yes_in_a_non_tty_context_exits_nonzero_and_calls_nothing(
        self, api: FakeApi
    ) -> None:
        result = runner.invoke(
            app,
            ["cloud", "connection", "delete", "warehouse", "--org", "acme-data"],
        )
        assert result.exit_code != 0
        assert api.calls == []

    def test_project_delete_without_yes_in_a_non_tty_context_changes_nothing(
        self, api: FakeApi
    ) -> None:
        result = runner.invoke(
            app,
            ["cloud", "project", "delete", "--org", "acme-data", "--project", "x"],
        )
        assert result.exit_code != 0
        assert api.calls == []

    def test_org_delete_without_yes_in_a_non_tty_context_changes_nothing(
        self, api: FakeApi
    ) -> None:
        result = runner.invoke(app, ["cloud", "org", "delete", "--org", "acme-data"])
        assert result.exit_code != 0
        assert api.calls == []

    def test_typed_confirmation_matching_the_full_coordinate_proceeds(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HIGH-6: the prompt requires the full org/slug coordinate, not the
        bare slug -- typing just 'warehouse' must not be enough."""
        monkeypatch.setattr(cloud_cmd, "sys", _FakeSys())
        monkeypatch.setattr(cloud_cmd, "is_plain_output", lambda: False)
        api.add(
            "DELETE",
            "/api/orgs/acme-data/connections/warehouse",
            {"deleted": True, "message": "Deleted warehouse."},
        )
        result = runner.invoke(
            app,
            ["cloud", "connection", "delete", "warehouse", "--org", "acme-data"],
            input="acme-data/warehouse\n",
        )
        assert result.exit_code == 0, out(result)
        assert api.calls != []

    def test_bare_slug_no_longer_confirms(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cloud_cmd, "sys", _FakeSys())
        monkeypatch.setattr(cloud_cmd, "is_plain_output", lambda: False)
        result = runner.invoke(
            app,
            ["cloud", "connection", "delete", "warehouse", "--org", "acme-data"],
            input="warehouse\n",
        )
        assert result.exit_code != 0
        assert api.calls == []

    def test_typed_confirmation_not_matching_the_slug_aborts(
        self, api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cloud_cmd, "sys", _FakeSys())
        monkeypatch.setattr(cloud_cmd, "is_plain_output", lambda: False)
        result = runner.invoke(
            app,
            ["cloud", "connection", "delete", "warehouse", "--org", "acme-data"],
            input="not-warehouse\n",
        )
        assert result.exit_code != 0
        assert api.calls == []

    def test_yes_still_prints_the_resolved_coordinate_before_acting(
        self, api: FakeApi
    ) -> None:
        """HIGH-6: even with --yes, the resolved coordinate is printed --
        the one chance to notice a stale default before it deletes the
        wrong thing."""
        api.add(
            "DELETE",
            "/api/orgs/acme-data/connections/warehouse",
            {"deleted": True, "message": "Deleted warehouse."},
        )
        result = runner.invoke(
            app,
            [
                "cloud",
                "connection",
                "delete",
                "warehouse",
                "--org",
                "acme-data",
                "--yes",
            ],
        )
        assert result.exit_code == 0, out(result)
        assert "acme-data/warehouse" in out(result)

    @pytest.mark.parametrize(
        "argv",
        [
            ["member", "remove", "bob@acme.com"],
            ["invite", "revoke", "bob@acme.com"],
            ["grant", "revoke", "grant-1"],
        ],
    )
    def test_admin_verbs_refuse_a_stale_default_with_no_org_flag(
        self, api: FakeApi, argv: list[str]
    ) -> None:
        """Removing a member, revoking an invite, or revoking a grant is as
        destructive as a delete: with no --org and no repo match they refuse
        the stored default (and, in a fork, the published_to record) rather
        than act on an org the user did not name."""
        save_config(CloudConfig(org="stale-org", project=""))
        api.add("GET", "/api/orgs", OrgList(organizations=[]).model_dump(mode="json"))

        result = runner.invoke(app, ["cloud", *argv])

        assert result.exit_code != 0
        assert "destructive" in out(result)
        assert not any(
            method in ("DELETE", "POST") for method, _path, _body in api.calls
        )

    def test_org_delete_refuses_a_stale_default_with_no_org_flag(
        self, api: FakeApi
    ) -> None:
        """HIGH-6: with no --org and a repo that matches nothing, a
        destructive verb must not fall back to the stored `dct cloud use`
        default -- it refuses rather than guessing which org to delete."""
        save_config(CloudConfig(org="stale-org", project=""))
        api.add("GET", "/api/orgs", OrgList(organizations=[]).model_dump(mode="json"))

        result = runner.invoke(app, ["cloud", "org", "delete", "--yes"])

        assert result.exit_code != 0
        assert "destructive" in out(result)
        # The candidate-listing GET /api/orgs the error message composes is
        # fine; nothing was deleted.
        assert not any(method == "DELETE" for method, _path, _body in api.calls)
