"""`begin_github_connect`: the one place the connect fallback order lives."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

import httpx
import pytest

from dbt_charts.cloud_client import context
from dbt_charts.cloud_client.client import CloudClient, GitHubConnectBegun
from dbt_charts.cloud_client.contract import ErrorCode
from dbt_charts.cloud_client.errors import ApiFailed, CloudTooOld

PICK_BODY: dict[str, object] = {
    "repo_id": "1",
    "full_name": "acme/analytics",
    "default_branch": "main",
    "private": False,
    "picked_at": "2026-08-30T00:00:00Z",
    "dbt_roots": [],
}
CONNECT_URL = "https://cloud.example/acme/github/connect/?landing=terminal"
INTENT_URL = f"{CONNECT_URL}&intent=signed"
PICK_PATH = "/api/orgs/acme/github/pick"
START_PATH = "/api/orgs/acme/github/connect"
STARTED = {"org": "acme", "url": INTENT_URL, "picked": None}


def _error(status: int, code: str, **extra: object) -> httpx.Response:
    return httpx.Response(
        status, json={"code": code, "message": f"{code} message", **extra}
    )


def _begin(
    routes: dict[str, httpx.Response],
    full_name: str | None,
    github_token: str | None,
) -> tuple[GitHubConnectBegun, list[tuple[str, dict[str, object]]]]:
    """`begin_github_connect` against *routes*, plus the (path, body) calls."""
    calls: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content)))
        return routes[request.url.path]

    cloud = CloudClient(
        host="https://cloud.example",
        token="t0ken",
        transport=httpx.MockTransport(handler),
    )
    with cloud:
        return cloud.begin_github_connect("acme", full_name, github_token), calls


class TestBeginGithubConnect:
    def test_a_proven_pick_needs_no_browser(self) -> None:
        begun, calls = _begin(
            {PICK_PATH: httpx.Response(201, json=PICK_BODY)}, "acme/analytics", "gho"
        )

        assert begun == GitHubConnectBegun(
            url=CONNECT_URL, picked="acme/analytics", notes=[]
        )
        assert calls == [
            (PICK_PATH, {"full_name": "acme/analytics", "github_token": "gho"})
        ]

    def test_a_refused_proof_is_a_note_and_the_intent_url_follows(self) -> None:
        begun, calls = _begin(
            {
                PICK_PATH: _error(400, "validation_failed"),
                START_PATH: httpx.Response(200, json=STARTED),
            },
            "acme/analytics",
            "gho",
        )

        assert begun.url == INTENT_URL
        assert begun.picked is None
        assert begun.notes == [
            "validation_failed message",
            "Continuing with a browser step instead.",
        ]
        assert [path for path, _body in calls] == [PICK_PATH, START_PATH]
        assert calls[1][1] == {"full_name": "acme/analytics"}

    def test_a_malformed_repo_raises_instead_of_falling_back(self) -> None:
        with pytest.raises(ApiFailed) as caught:
            _begin(
                {
                    PICK_PATH: _error(
                        400, "validation_failed", field_errors={"full_name": ["bad"]}
                    )
                },
                "nope",
                "gho",
            )

        assert caught.value.api_error.field_errors == {"full_name": ["bad"]}

    def test_no_token_names_the_repo_in_the_connect_url_only(self) -> None:
        begun, calls = _begin(
            {START_PATH: httpx.Response(200, json=STARTED)}, "acme/analytics", None
        )

        assert begun == GitHubConnectBegun(url=INTENT_URL, picked=None, notes=[])
        assert [path for path, _body in calls] == [START_PATH]

    def test_no_repo_is_the_plain_connect_url_with_no_call(self) -> None:
        begun, calls = _begin({}, None, "gho")

        assert begun == GitHubConnectBegun(url=CONNECT_URL, picked=None, notes=[])
        assert calls == []

    def test_an_old_deployment_falls_back_with_a_note(self) -> None:
        begun, calls = _begin(
            {
                PICK_PATH: httpx.Response(404, text="Not Found"),
                START_PATH: httpx.Response(405, text="Method Not Allowed"),
            },
            "acme/analytics",
            "gho",
        )

        assert begun.url == CONNECT_URL
        assert begun.picked is None
        assert len(begun.notes) == 1
        assert "predates" in begun.notes[0]
        assert [path for path, _body in calls] == [PICK_PATH, START_PATH]

    def test_an_outage_raises_rather_than_falling_back(self) -> None:
        with pytest.raises(ApiFailed) as caught:
            _begin({PICK_PATH: _error(503, "unavailable")}, "acme/analytics", "gho")

        assert caught.value.api_error.code is ErrorCode.UNAVAILABLE


def test_a_real_org_404_is_not_a_missing_route() -> None:
    with pytest.raises(ApiFailed):
        _begin({START_PATH: _error(404, "not_found")}, "acme/analytics", None)


def test_a_missing_route_is_cloud_too_old() -> None:
    cloud = CloudClient(
        host="https://cloud.example",
        token="t0ken",
        transport=httpx.MockTransport(lambda request: httpx.Response(404, text="<p>")),
    )
    with cloud, pytest.raises(CloudTooOld):
        cloud.prove_pick("acme", "acme/analytics", "gho")


def _gh_answers(
    monkeypatch: pytest.MonkeyPatch, run: Callable[[list[str]], str]
) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        stdout = run(args)
        return subprocess.CompletedProcess(args, 0 if stdout else 1, stdout, "")

    monkeypatch.setattr(context.subprocess, "run", fake_run)
    return seen


class TestGhToken:
    def test_reads_the_github_com_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _gh_answers(monkeypatch, lambda args: "gho_abc\n")

        assert context.gh_token() == "gho_abc"
        assert seen == [["gh", "auth", "token", "--hostname", "github.com"]]

    def test_signed_out_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _gh_answers(monkeypatch, lambda args: "")

        assert context.gh_token() is None

    def test_a_missing_gh_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def missing(args: list[str], **_kwargs: object) -> None:
            raise FileNotFoundError("gh")

        monkeypatch.setattr(context.subprocess, "run", missing)

        assert context.gh_token() is None
