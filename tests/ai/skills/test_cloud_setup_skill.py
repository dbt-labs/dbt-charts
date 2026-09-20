"""Content-regression tests for the cloud-setup onboarding skill.

Deterministic — reads the rendered SKILL.md body and asserts the guidance that
stops the four onboarding failures seen in the wild. No LLM is invoked; this is
the same pattern as `test_review_skills.py`.

The skill is CLI-only (`surfaces: [cli]`), so every read passes `surface="cli"`.
"""

from __future__ import annotations

import pytest

from dbt_charts.agent_api.skills import get_skill, list_skills

SKILL = "cloud-setup"


@pytest.fixture(scope="module")
def body() -> str:
    return get_skill(SKILL, surface="cli").body


def test_skill_is_discoverable() -> None:
    names = {s.name for s in list_skills(surface="cli").skills}
    assert SKILL in names, f"Registry missing {SKILL}; got {sorted(names)}"


def test_description_has_negative_boundary() -> None:
    assert "Do NOT use" in get_skill(SKILL, surface="cli").description


def test_disambiguates_from_dbt_labs_cloud(body: str) -> None:
    assert "dbtcharts.com" in body
    assert "getdbt.com" in body
    lowered = body.lower()
    assert "dbt labs" in lowered, "must name dbt Labs' dbt Cloud as the wrong product"


def test_forbids_account_creation_refusal(body: str) -> None:
    lowered = body.lower()
    assert "never refuse" in lowered
    assert "not a blocker" in lowered
    assert "sign up" in lowered or "sign-up" in lowered


def test_checks_github_auth_up_front(body: str) -> None:
    assert "gh auth status" in body
    before_you_start = body.index("Before you start")
    step_connect = body.index("Connect the project")
    assert before_you_start < body.index("gh auth status") < step_connect, (
        "GitHub auth check must sit in 'Before you start', not only at connect time"
    )


def test_owns_repo_folder_decision(body: str) -> None:
    lowered = body.lower()
    assert "dbt_charts.yml" in body
    assert "new repo" in lowered
    assert "git repo" in lowered


def test_step3_gates_on_authored_boards(body: str) -> None:
    """Step 3 must key the board-build handoff off user-authored boards, not the
    mere presence of `charts/` — else the `dct init` starter board gets connected
    and rendered as the user's own."""
    lowered = body.lower()
    assert "starter board" in lowered
    assert "authored" in lowered


def test_no_destructive_verb(body: str) -> None:
    """The skill must never name a Cloud delete verb (the 'delete the temp
    credential file' housekeeping instruction is fine — that's a local file)."""
    for verb in ("org delete", "project delete", "connection delete", "cloud delete"):
        assert verb not in body.lower(), (
            f"cloud-setup must not invoke `dct cloud {verb}`"
        )


def test_login_waits_for_approval_itself(body: str) -> None:
    """The agent must surface the URL at once via `--start`, then block on
    the CLI's own `--wait` -- not grep a shared log file for the URL or the
    `Logged in` line (FR-67: two agents/users sharing `/tmp/dct-login.log`
    read each other's device code)."""
    step1 = body[body.index("## Step 1") : body.index("## Step 2")]
    assert "dct cloud login --start" in step1
    assert "dct cloud login --wait" in step1
    assert "Logged in" in step1
    assert "/tmp/dct-login.log" not in body
    lowered = step1.lower()
    assert "which account" in lowered, "must say whose browser account approves"


def test_warns_about_local_only_sources_before_boards(body: str) -> None:
    """A DuckDB/SQLite project learns Cloud can't connect to it before any board
    is authored in that dialect — in the pre-flight, not at connection time."""
    lowered = body.lower()
    duckdb_at = lowered.index("duckdb")
    assert body.index("Before you start") < duckdb_at < body.index("## Step 1")
    assert "parquet" in lowered
    assert "bigquery, postgresql, redshift, snowflake" in lowered


def test_recommends_registry_sources_first(body: str) -> None:
    """The pre-flight bullet recommends the `sources:` registry before the
    inline one-off path and no longer warns that Cloud can't resolve
    registry file sources."""
    where_data_lives = body.index("Where the data lives")
    step1 = body.index("## Step 1")
    section_lower = body[where_data_lives:step1].lower()
    assert "sources:" in section_lower
    assert section_lower.index("sources:") < section_lower.index("source: ")
    assert "one-off" in section_lower
    assert "does not resolve registry file sources" not in body.lower()


def test_render_gated_on_mapped_sources(body: str) -> None:
    """A board whose every query failed still completes a render, so `ready`
    from `dct cloud boards` is not evidence of data — the skill must say so."""
    assert "`ready`" in body
    render_at = body.index("## Step 7")
    assert "unmapped" in body[render_at:].lower()


def test_blocked_is_not_a_state_to_re_render_past(body: str) -> None:
    """`blocked` means a mapped connection never passed its last test, so no
    render can hold real data. An agent that reads it as a transient render
    state will loop on `dct cloud render` forever, which is how a project sat
    serving three empty boards behind a credential that never worked."""
    render_at = body.index("Step 7")
    step7 = body[render_at:]
    assert "`blocked`" in step7
    assert "dct cloud connection test" in step7


def test_github_only(body: str) -> None:
    """Cloud connects GitHub repositories only; the skill must not promise any
    other git host, and must keep the public-URL path to GitHub URLs."""
    lowered = body.lower()
    assert "any repo cloud can clone" not in lowered
    assert "any git host" not in lowered
    assert "other git hosts" in lowered, "must say other hosts are unsupported"
    assert "https://github.com/" in body


def test_connect_runs_right_after_login(body: str) -> None:
    """The GitHub App install is the only other browser hop, so it follows login
    in the same sitting (FR-82: two agents/users sharing `/tmp/dct-connect.log`
    could read each other's install URL)."""
    connect_at = body.index("Connect the project")
    assert body.index("## Step 1") < connect_at < body.index("Boards, if none exist")
    connect_section = body[connect_at : body.index("Boards, if none exist")]
    assert "dct cloud project connect --org <org> --start" in connect_section
    assert "dct cloud project connect --wait" in connect_section
    assert "/tmp/dct-connect.log" not in body


def test_connect_wait_is_not_deferred_past_board_authoring(body: str) -> None:
    """FR-87: an onboarding trial ran `--start`, handed over the URL, then
    authored boards for ~12 minutes and never ran `--wait` -- the project was
    never created and `status` sat at `missing_project` until nudged. `--start`
    and `--wait` must read as one inseparable unit, with `--wait` run before
    Step 4 (board authoring), not deferred until just before some later step."""
    connect_at = body.index("Connect the project")
    boards_at = body.index("Boards, if none exist")
    connect_section = body[connect_at:boards_at]
    lowered = connect_section.lower()
    assert "inseparable unit" in lowered
    assert "before any board authoring" in lowered
    start_at = connect_section.index("dct cloud project connect --org <org> --start")
    wait_at = connect_section.index("dct cloud project connect --wait")
    assert start_at < wait_at
    between = connect_section[start_at:wait_at]
    assert "step 4" not in between.lower(), (
        "the text between --start and --wait must not send the agent off to "
        "board authoring before running --wait"
    )


def test_serves_locally_and_keeps_going(body: str) -> None:
    """The user should see boards in a local `dct serve` before Cloud renders
    them; the agent opens the URL and continues, it does not stop for approval."""
    serve_at = body.index("dct serve", body.index("## Step 1"))
    assert serve_at < body.index("## When you're done")
    lowered = body.lower()
    assert "keep going" in lowered
    assert "local url" in lowered


def test_file_source_projects_skip_the_warehouse_step(body: str) -> None:
    """A csv/json/parquet project needs no connection at all; the skill must say
    so at the top of the warehouse step, so `connections: 0` doesn't read as an
    unfinished setup."""
    step5 = body.index("## Step 5")
    head = body[step5 : body.index("## Step 6")].lower()
    assert "skip this whole step" in head
    assert "file source" in head
    assert "connections: 0" in head


def test_says_how_to_ship_a_change_after_setup(body: str) -> None:
    """Day two is 'I edited a board, get it live'. A push republishes on its
    own, on a latency that depends on how the repo is connected, and `ready`
    is not evidence the live render is yours -- RENDERED_AT and COMMIT are.
    (This last assertion replaced a "no timestamp" one: the listing carries
    both facts now, which is what this branch added.)"""
    step8 = body.index("## Step 8")
    section = body[step8 : body.index("## The loop that actually drives this")]
    lowered = section.lower()
    assert "github app" in lowered
    assert "hourly" in lowered
    assert "dct cloud project sync" in section
    assert "`ready` is not proof" in section
    assert "RENDERED_AT" in section
    assert "COMMIT" in section


def test_day_two_is_reachable_from_the_status_loop(body: str) -> None:
    """The loop section tells the agent to drive from `dct cloud status` until
    it reports nothing left, and `status` never names "tell the user how to ship
    a change" — so Step 8 is skippable unless the sign-off says otherwise."""
    section = body[body.index("## When you're done") :].lower()
    assert "step 8" in section
    assert "will never prompt you to" in section


def test_uses_bare_dct(body: str) -> None:
    assert "dct cloud login" in body
    assert "uv run dct" not in body, "shipped skill uses bare `dct`"


def test_mapping_rerenders_and_force_is_the_manual_lever(body: str) -> None:
    """The first render fires on sync, before any source is mapped. The skill
    must say that mapping re-renders on its own, and name ``--force`` for the
    case where nothing else moves a dead board."""
    map_at = body.index("## Step 6")
    render_at = body.index("## Step 7")
    assert "re-render" in body[map_at:render_at].lower()
    assert "dct cloud render --force" in body[render_at:]


def test_verifies_each_board_with_its_own_token(body: str) -> None:
    """The agent fetches the board it published rather than handing an
    unchecked URL to the user: the login token reads board pages, so
    "I could not see it myself" is no longer a valid handoff."""
    done_at = body.index("## When you're done")
    section = body[done_at:]
    assert "Authorization: Bearer" in section
    assert "dct cloud boards --json" in section
    lowered = section.lower()
    assert "unverified" in lowered, "a refused fetch is not a broken board"
    assert "never echo" in lowered, "must warn against printing the credential"


def test_does_not_promise_connect_always_writes_published_to(body: str) -> None:
    """Connect writes `published_to:` only into an existing dbt_charts.yml in
    a checkout of the repo it connected -- the common first-connect case is a
    printed key the agent has to add itself, so the skill must not tell it to
    expect a file change."""
    claim = body[body.index("published_to:", body.index("## Step 3")) :]
    flowed = " ".join(claim[: claim.index("## Step 4")].split())
    assert "writes only when that file already exists" in flowed
    assert "prints the exact key and value" in flowed


def test_proves_a_change_is_live_by_commit_not_by_status(body: str) -> None:
    """`ready` means a render exists, not a render of the agent's commit. The
    skill has to name the check that tells them apart, or an agent reports a
    silently dropped edit as shipped."""
    assert "COMMIT" in body
    assert "RENDERED_AT" in body
    assert "dct cloud boards" in body


def test_liveness_is_not_equality_with_the_local_head(body: str) -> None:
    """The check is "these two values moved", never "COMMIT equals your
    `git rev-parse HEAD`". Cloud commits board edits to a work branch and
    merges the upstream branch into it on every sync, so on a project ever
    edited in Cloud that equality is unreachable and an agent waiting on it
    never stops."""
    step = body[body.index("## Step 8") :]
    assert "not to your `git rev-parse HEAD`" in step
    assert "work branch" in step
    assert "does not terminate" in step


def test_an_unmoved_board_is_reported_unconfirmed_not_diagnosed(body: str) -> None:
    """A sync only *queues* the re-renders; they run behind live page views and
    re-run each board's queries. So "neither value moved" is "not rendered
    yet", and an agent that turns it into a cause -- the wrong branch, a
    dropped push -- sends the user to debug something that is not broken."""
    # Whitespace-collapsed: these are prose sentences the markdown hard-wraps,
    # so a phrase assertion would otherwise pin where the line happens to break.
    step = " ".join(body[body.index("## Step 8") :].split())
    assert '"not confirmed yet"' in step
    assert "still queued" in step
    assert "a render that fails writes no new" in step.lower()
    # All three `warning`/`errored` cases, since a gap in the split sends the
    # agent to wait on a render that already ran and failed.
    assert "`warning` that **appeared** since your before-reading is the answer" in step
    assert "`warning` that was **already there** is undated and therefore" in step
    assert "the previous render's chart diagnostic" in step
    assert "`ready` or `not_rendered` with nothing moved" in step
    for wrong_cause in (
        "the commit is not on the branch Cloud serves",
        "check which branch the project tracks",
    ):
        assert wrong_cause not in body


def test_states_that_pushing_does_not_publish(body: str) -> None:
    """An agent that pushes and walks away leaves the work unpublished for up
    to an hour on a repository-URL project."""
    lowered = body.lower()
    assert "dct cloud project sync" in body
    assert "does not publish" in lowered


def test_collects_the_two_decisions_before_step_one(body: str) -> None:
    """A reviewer-gated agent stops per push and per credential unless both
    were granted before the run started."""
    consent = body.split("## Get consent up front")[1].split("\n## ")[0]
    assert "credential" in consent
    assert "default branch" in consent
    assert "before Step 1" in consent


def test_done_is_checked_against_this_repository(body: str) -> None:
    """`status` is org-scoped: another project's `done` is not this repo's."""
    assert "org-scoped" in body
    assert "git remote -v" in body


def test_conditional_bigquery_binding_covers_tables(body: str) -> None:
    """The grant stays dataset-scoped; only the conditional fallback needs
    the Table resource type spelled out."""
    assert "scoped to the target dataset" in body
    assert "`Dataset` and `Table`" in body


def test_revoking_the_bigquery_key_waits_for_the_connection(body: str) -> None:
    assert "never right after the test passes" in body
