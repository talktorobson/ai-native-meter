import json
import subprocess
from datetime import datetime

import pytest
from conftest import AFTER, BEFORE, REPO

from devex import github_source as gs
from devex.metrics import Window


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (101, ()),
        (103, ()),
        (201, ("agent:copilot-swe-agent",)),
        (202, ("co-author:copilot",)),
        (203, ("review-fix:copilot",)),
        (204, ("co-author:claude", "marker:claude")),
        (205, ("label:ai-generated",)),
        (208, ("agent:copilot-swe-agent",)),
    ],
)
def test_ai_evidence_names_every_observable_signal(nodes, number, expected):
    assert gs.ai_evidence(nodes[number]) == expected


def test_a_human_coauthor_trailer_is_not_ai_evidence(nodes):
    assert gs.ai_evidence(nodes[201]) == ("agent:copilot-swe-agent",)


def test_area_labels_that_mention_copilot_are_not_evidence(nodes):
    assert "label:copilot" not in gs.ai_evidence(nodes[205])


@pytest.mark.parametrize(
    ("evidence", "cohort"),
    [
        (("agent:copilot", "co-author:copilot"), "agent-authored"),
        (("co-author:claude", "review-fix:copilot"), "AI co-authored"),
        (("label:ai-generated",), "AI co-authored"),
        (("review-fix:copilot",), "AI review fix only"),
        ((), "no AI signal"),
    ],
)
def test_ai_cohort_takes_the_strongest_evidence(evidence, cohort):
    assert gs.ai_cohort(evidence) == cohort


@pytest.mark.parametrize(
    ("actor", "agent", "automation"),
    [
        ({"login": "copilot-swe-agent", "__typename": "Bot"}, True, False),
        ({"login": "Copilot", "__typename": "Bot"}, True, False),
        ({"login": "dependabot[bot]", "__typename": "Bot"}, False, True),
        ({"login": "renovate", "__typename": "User"}, False, True),
        ({"login": "vs-code-engineering", "__typename": "Bot"}, False, True),
        ({"login": "dotnet-bot", "__typename": "User"}, False, True),
        ({"login": "dotnet-maestro", "__typename": "Bot"}, False, True),
        ({"login": "alice", "__typename": "User"}, False, False),
        (None, False, False),
    ],
)
def test_agents_and_automation_are_told_apart(actor, agent, automation):
    assert gs.is_ai_agent(actor) is agent
    assert gs.is_automation(actor) is automation


def test_agent_prs_are_credited_to_the_human_assignee(nodes):
    assert gs.driver(nodes[201]) == "alice"
    assert gs.driver(nodes[101]) == "alice"


def test_agent_prs_without_a_human_assignee_fall_back_to_the_merger(nodes):
    assert gs.driver(nodes[208]) == "erin"


def test_agent_prs_with_no_human_at_all_stay_with_the_agent():
    node = {"author": {"login": "copilot-swe-agent", "__typename": "Bot"}, "mergedBy": None}
    assert gs.driver(node) == "copilot-swe-agent"


def test_first_review_skips_bots_and_self_reviews(nodes):
    assert gs.first_human_review(nodes[201]) == "2026-07-06T13:00:00Z"
    assert gs.first_human_review(nodes[102]) == "2025-01-08T15:00:00Z"
    assert gs.first_human_review(nodes[208]) is None


@pytest.mark.parametrize(
    ("number", "targets"),
    [
        (104, (101,)),
        (206, (202,)),
        (209, (204,)),
        (105, ()),
        (207, ()),
        (101, ()),
    ],
)
def test_revert_targets_need_a_revert_title_and_a_same_repo_reference(nodes, number, targets):
    assert gs.revert_targets(nodes[number], REPO) == targets


def test_revert_targets_ignore_self_references():
    node = {"number": 7, "title": "Revert broken change", "body": "Reverts #7. Also reverts #6"}
    assert gs.revert_targets(node, REPO) == (6,)


def test_to_record_keeps_the_evidence_behind_each_classification(nodes):
    assert gs.to_record(nodes[201], REPO) == {
        "number": 201,
        "author": "alice",
        "login": "copilot-swe-agent",
        "created": "2026-07-06T09:00:00Z",
        "merged": "2026-07-06T19:00:00Z",
        "review": "2026-07-06T13:00:00Z",
        "lines": 40,
        "bot": False,
        "ai": ["agent:copilot-swe-agent"],
        "reverts": [],
    }
    assert gs.to_record(nodes[103], REPO)["bot"] is True


def test_to_pull_requests_maps_times_to_naive_utc(small_snapshot):
    pr = next(p for p in gs.to_pull_requests(small_snapshot) if p.id == f"{REPO}#201")
    assert pr.opened_at == datetime(2026, 7, 6, 9)
    assert pr.merged_at == datetime(2026, 7, 6, 19)
    assert pr.first_review_at == datetime(2026, 7, 6, 13)
    assert pr.ai_assisted and not pr.is_bot
    assert pr.token_cost_usd == 0 and not pr.caused_incident


def test_reverted_prs_are_flagged(small_snapshot):
    reverted = {p.id for p in gs.to_pull_requests(small_snapshot) if p.reverted}
    assert reverted == {f"{REPO}#101", f"{REPO}#202", f"{REPO}#204"}


def test_reverts_after_the_horizon_do_not_count(small_snapshot, nodes):
    late = dict(nodes[209], number=299, mergedAt="2026-08-20T00:00:00Z")
    late_record = gs.to_record(late, REPO)
    others = tuple(r for r in small_snapshot.records if r["number"] != 209)
    snapshot = gs.Snapshot(REPO, "main", small_snapshot.windows, (*others, late_record))
    reverted = {p.id for p in gs.to_pull_requests(snapshot) if p.reverted}
    assert f"{REPO}#204" not in reverted


def test_engineers_are_distinct_humans_with_a_merged_pr(small_snapshot):
    prs = gs.to_pull_requests(small_snapshot)
    assert gs.engineers_in(prs, BEFORE) == 3
    assert gs.engineers_in(prs, AFTER) == 5


def test_snapshot_round_trips_and_stays_one_pr_per_line(snapshot_file, small_snapshot):
    assert gs.load_snapshot(snapshot_file) == small_snapshot
    text = snapshot_file.read_text(encoding="utf-8")
    assert sum(line.startswith('{"number":') for line in text.splitlines()) == len(
        small_snapshot.records
    )
    assert json.loads(text)["url"] == "https://github.com/microsoft/vscode"


def test_merge_snapshot_replaces_records_and_sorts_windows(small_snapshot):
    newer = dict(small_snapshot.records[0], lines=999)
    window = Window(datetime(2026, 1, 1), datetime(2026, 4, 1))
    merged = gs.merge_snapshot(small_snapshot, REPO, "main", window, "2026-11-08", (newer,))
    assert [w.start.year for w, _ in merged.windows] == [2025, 2026, 2026]
    assert merged.records[0]["lines"] == 999
    assert len(merged.records) == len(small_snapshot.records)


def test_merge_snapshot_starts_over_for_another_repo(small_snapshot):
    merged = gs.merge_snapshot(small_snapshot, "other/repo", "trunk", BEFORE, "2026-11-08", ())
    assert merged.records == () and len(merged.windows) == 1


def test_search_queries_slice_the_window_without_overlap():
    queries = gs.search_queries(REPO, "main", datetime(2025, 1, 1), datetime(2025, 1, 20))
    assert len(queries) == 3
    assert queries[0] == (
        "repo:microsoft/vscode is:pr is:merged base:main "
        "merged:2025-01-01T00:00:00Z..2025-01-07T23:59:59Z"
    )
    assert queries[-1].endswith("merged:2025-01-15T00:00:00Z..2025-01-19T23:59:59Z")


def _fake_runner(pages: list[list[dict]], issue_count: int | None = None):
    calls: list[dict] = []

    def run(query, variables):
        calls.append(dict(variables))
        if "defaultBranchRef" in query:
            return {"repository": {"defaultBranchRef": {"name": "main"}}}
        after = variables.get("after")
        index = 0 if after is None else int(after)
        is_tail = "revert in:title" in variables["q"]
        page = pages[-1] if is_tail else pages[index]
        more = not is_tail and index + 1 < len(pages) - 1
        return {
            "search": {
                "issueCount": issue_count or sum(map(len, pages)),
                "pageInfo": {"hasNextPage": more, "endCursor": str(index + 1)},
                "nodes": [*page, {}],
            }
        }

    return run, calls


def test_fetch_pages_through_results_and_keeps_only_reverts_from_the_tail(nodes):
    pages = [[nodes[201]], [nodes[202]], [nodes[205], nodes[209]]]
    run, calls = _fake_runner(pages)
    window = Window(datetime(2026, 7, 6), datetime(2026, 7, 13))
    branch, records = gs.fetch(REPO, window, run=run)
    assert branch == "main"
    assert [r["number"] for r in records] == [201, 202, 209]
    assert [c.get("after") for c in calls[1:3]] == [None, "1"]


def test_fetch_refuses_slices_over_the_search_cap(nodes):
    run, _ = _fake_runner([[nodes[201]], []], issue_count=gs.SEARCH_CAP + 1)
    with pytest.raises(RuntimeError, match="smaller slices"):
        gs.fetch(REPO, Window(datetime(2026, 7, 6), datetime(2026, 7, 13)), run=run)


def test_gh_runner_posts_the_query_through_gh(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(cmd=cmd, body=json.loads(kwargs["input"]))
        return subprocess.CompletedProcess(cmd, 0, stdout='{"data": {"ok": 1}}', stderr="")

    monkeypatch.setattr(gs.subprocess, "run", fake_run)
    assert gs.gh_runner("query { ok }", {"a": 1}) == {"ok": 1}
    assert seen["cmd"] == ["gh", "api", "graphql", "--input", "-"]
    assert seen["body"] == {"query": "query { ok }", "variables": {"a": 1}}


def test_gh_runner_retries_then_raises(monkeypatch):
    attempts = []

    def failing(cmd, **kwargs):
        attempts.append(1)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="rate limited")

    monkeypatch.setattr(gs.subprocess, "run", failing)
    monkeypatch.setattr(gs.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="rate limited"):
        gs.gh_runner("query { ok }", {}, attempts=3)
    assert len(attempts) == 3
