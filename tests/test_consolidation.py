"""A predecessor repository counted in the baseline window, offline."""

from datetime import datetime

import pytest
from conftest import AFTER, BEFORE, MID, PRED, REPO

from devex import cli, dashboard_real, real
from devex import github_source as gs
from devex.cli import main
from devex.metrics import Window


def _files(*paths: str, total: int | None = None) -> dict:
    return {"files": {"totalCount": total or len(paths), "nodes": [{"path": p} for p in paths]}}


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        (
            _files("extensions/copilot/src/a.ts", "extensions/copilot/test/b.ts"),
            "extensions/copilot",
        ),
        (_files("extensions/copilot/a.ts", "extensions/git/b.ts"), "extensions"),
        (_files("src/vs/a.ts", "README.md"), ""),
        (_files("extensions/copilot/a.ts", total=101), None),
        ({}, None),
    ],
)
def test_scope_is_the_common_directory_of_the_changed_files(node, expected):
    assert gs.scope(node) == expected


def test_to_record_keeps_a_known_scope_only(nodes):
    assert "scope" not in gs.to_record(nodes[201], REPO)
    node = {**nodes[201], **_files("extensions/copilot/a.ts")}
    assert gs.to_record(node, REPO)["scope"] == "extensions/copilot"


def test_predecessor_records_keep_their_repo_and_number(consolidated_snapshot):
    keys = [(r.get("repo", REPO), r["number"]) for r in consolidated_snapshot.records]
    assert (REPO, 301) in keys and (PRED, 301) in keys
    assert keys == sorted(keys, key=lambda k: (k[0] != REPO, k))
    assert consolidated_snapshot.repos_in(MID) == (REPO, PRED)
    assert consolidated_snapshot.repos_in(BEFORE) == (REPO,)


def test_reverts_resolve_within_their_own_repository(consolidated_snapshot):
    reverted = {pr.id for pr in gs.to_pull_requests(consolidated_snapshot) if pr.reverted}
    assert f"{PRED}#301" in reverted and f"{REPO}#301" not in reverted


def test_engineers_are_distinct_logins_across_repositories(consolidated_snapshot):
    prs = gs.to_pull_requests(consolidated_snapshot)
    primary = [pr for pr in prs if pr.id.startswith(f"{REPO}#")]
    assert gs.engineers_in(primary, MID) == 2  # bob, alice
    assert gs.engineers_in(prs, MID) == 3  # + frank; bob counted once


def test_predecessors_survive_a_round_trip_and_a_primary_refetch(consolidated_snapshot, tmp_path):
    path = tmp_path / "s.json"
    gs.write_snapshot(consolidated_snapshot, path)
    loaded = gs.load_snapshot(path)
    assert loaded == consolidated_snapshot
    refetched = gs.merge_snapshot(loaded, REPO, "main", AFTER, "2026-11-08", ())
    assert refetched.predecessors == loaded.predecessors
    assert gs.merge_snapshot(loaded, "other/repo", "main", AFTER, "x", ()).predecessors == ()


def test_refetching_a_predecessor_replaces_it(consolidated_snapshot):
    again = gs.merge_predecessor(
        consolidated_snapshot, PRED, MID, "2026-11-09", "extensions/copilot", ()
    )
    assert [p.fetched_on for p in again.predecessors] == ["2026-11-09"]
    assert len(again.records) == len(consolidated_snapshot.records)


@pytest.mark.parametrize(
    ("repo", "window", "error"),
    [
        (REPO, MID, "own repository"),
        (PRED, Window(datetime(2024, 1, 1), datetime(2024, 4, 1)), "first"),
    ],
)
def test_merge_predecessor_needs_another_repo_and_a_fetched_window(
    consolidated_snapshot, repo, window, error
):
    with pytest.raises(ValueError, match=error):
        gs.merge_predecessor(consolidated_snapshot, repo, window, "x", "", ())


@pytest.fixture(scope="module")
def view(consolidated_snapshot) -> real.RealView:
    return real.build(consolidated_snapshot)


def test_the_baseline_counts_the_predecessor_and_the_reference_does_not(view):
    assert view.baseline.repos == (REPO, PRED)
    assert view.reference.repos == (REPO,)
    assert (view.before.merged_prs, view.before.engineers) == (5, 3)


def test_consolidation_reports_uncorrected_headline_and_sensitivity(view):
    uncorrected, headline, sensitivity = view.variants
    assert (uncorrected.baseline.merged_prs, uncorrected.current.merged_prs) == (2, 9)
    assert headline.headline and (headline.baseline, headline.current) == (view.before, view.after)
    assert (sensitivity.baseline.merged_prs, sensitivity.current.merged_prs) == (2, 8)
    assert "`extensions/copilot`" in sensitivity.name


def test_no_predecessor_means_no_consolidation(three_window_snapshot):
    built = real.build(three_window_snapshot)
    assert built.variants == () and real.moved_note(built) == ""
    assert "Repository consolidation" not in real.to_markdown(built)
    assert "Repository consolidation" not in dashboard_real.render(built)


def test_markdown_and_dashboard_show_the_consolidation(view):
    md = real.to_markdown(view)
    assert (
        "**Repository consolidation** (2026-07-06 to 2026-07-20 vs 2025-07-06 to 2025-07-20)" in md
    )
    assert f"{PRED}'s code now lives in {REPO} under `extensions/copilot`" in md
    assert f"| {REPO} only (uncorrected) | 2 → 9 (+350%) |" in md
    html = dashboard_real.render(view)
    assert "Baseline counts" in html and f"https://github.com/{PRED}" in html
    assert '<tr class="headline">' in html
    assert f"{PRED}&#x27;s code now lives" in html and "&amp;#x27;" not in html


def test_cli_fetch_into_adds_a_predecessor(monkeypatch, nodes, consolidated_snapshot, tmp_path):
    path = tmp_path / "s.json"
    gs.write_snapshot(consolidated_snapshot, path)
    monkeypatch.setattr(
        gs, "fetch", lambda repo, window, **_: ("main", (gs.to_record(nodes[201], repo),))
    )
    args = ["--repo", "o/old", "--into", REPO, "--moved-to", "old", "--snapshot", str(path)]
    main(["fetch", *args, "--since", "2026-07-06", "--until", "2026-07-20", "--fetched-on", "x"])
    loaded = gs.load_snapshot(path)
    assert loaded.repos_in(AFTER) == (REPO, "o/old")
    assert {"repo": "o/old"}.items() <= next(
        r for r in loaded.records if r.get("repo") == "o/old"
    ).items()


@pytest.mark.parametrize("into", [REPO, "someone/else"])
def test_cli_fetch_into_fails_before_the_network(
    monkeypatch, consolidated_snapshot, tmp_path, into
):
    path = tmp_path / "s.json"
    gs.write_snapshot(consolidated_snapshot, path)
    monkeypatch.setattr(gs, "fetch", lambda *a, **k: pytest.fail("fetched"))
    args = ["--repo", "o/old", "--into", into, "--snapshot", str(path)]
    with pytest.raises(SystemExit):
        main(["fetch", *args, "--since", "2024-01-01", "--until", "2024-04-01"])


def test_cli_fetch_into_derives_the_path_from_the_target():
    args = cli.build_parser().parse_args(["fetch", "--repo", "o/old", "--into", "Owner/Name"])
    assert cli._snapshot_path(args) == cli.DATA / "real" / "owner-name.json"
