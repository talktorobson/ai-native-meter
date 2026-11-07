"""Engineer-level KPIs: pseudonyms, per-engineer stats, ranks, guardrails, the paired adopter
comparison, the CLI and the dashboard section, all offline; plus a guard that no real login
reaches a published artifact."""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import AFTER, BEFORE, MID

from devex import cli, dashboard_engineers, dashboard_real, engineers, github_source, real
from devex.cli import main
from devex.metrics import PullRequest, Window

ROOT = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 7, 6)
WINDOW = Window(T0, T0 + timedelta(weeks=2))
EARLIER = Window(T0 - timedelta(weeks=52), T0 - timedelta(weeks=50))


def pr(
    n: int,
    author: str,
    *,
    size: int = 100,
    ai: bool = False,
    reverted: bool = False,
    review_h: float | None = 2.0,
    cycle_h: float = 10.0,
    bot: bool = False,
    window: Window = WINDOW,
) -> PullRequest:
    opened = window.start + timedelta(hours=n)
    return PullRequest(
        id=f"o/r#{n}",
        author=author,
        opened_at=opened,
        merged_at=opened + timedelta(hours=cycle_h),
        first_review_at=None if review_h is None else opened + timedelta(hours=review_h),
        lines_changed=size,
        ai_assisted=ai,
        reverted=reverted,
        is_bot=bot,
    )


def by_name(people):
    return {e.name: e for e in people}


def test_pseudonyms_are_stable_case_insensitive_and_opaque():
    name = engineers.pseudonym("Alice")

    assert re.fullmatch(r"eng-[0-9a-f]{6}", name)
    assert name == engineers.pseudonym("alice")
    assert name != engineers.pseudonym("bob")
    assert "alice" not in name


def test_engineer_stats_exclude_bots_and_reverts_from_quality_adjusted():
    prs = (
        pr(1, "alice", size=10, ai=True, reverted=True, review_h=None),
        pr(2, "alice", size=30, review_h=0.05),
        pr(3, "alice", size=50, review_h=3.0, cycle_h=20),
        pr(4, "renovate", bot=True),
    )

    (alice,) = engineers.engineers(prs, WINDOW, reveal=True)

    assert alice.name == "alice"
    assert alice.merged_prs == 3
    assert alice.prs_per_week == pytest.approx(1.5)
    assert alice.quality_adjusted_per_week == pytest.approx(1.0)
    assert alice.ai_share == pytest.approx(1 / 3)
    assert alice.revert_rate == pytest.approx(1 / 3)
    assert alice.median_size == 30
    assert alice.median_first_review_h == pytest.approx((0.05 + 3.0) / 2)
    assert alice.median_cycle_h == 10
    assert alice.light_review_share == pytest.approx(2 / 3)  # unreviewed + 3-minute review


def test_engineers_are_pseudonymous_unless_revealed():
    (person,) = engineers.engineers((pr(1, "alice"),), WINDOW)

    assert person.name == engineers.pseudonym("alice")


def test_no_review_at_all_has_no_median_and_no_rank():
    people = engineers.engineers((pr(1, "a", review_h=None), pr(2, "b")), WINDOW, reveal=True)
    kpi = engineers.KPI_BY_KEY["median_first_review_h"]

    assert by_name(people)["a"].median_first_review_h is None
    assert engineers.ranks(people, kpi) == {"b": 1}


def test_ranks_are_competition_ranks_in_each_kpis_direction():
    prs = (
        *(pr(i, "a") for i in range(3)),
        *(pr(10 + i, "b") for i in range(3)),
        pr(20, "c", reverted=True),
    )
    people = engineers.engineers(prs, WINDOW, reveal=True)

    assert engineers.ranks(people, engineers.KPI_BY_KEY["prs_per_week"]) == {
        "a": 1,
        "b": 1,
        "c": 3,
    }
    assert engineers.ranks(people, engineers.KPI_BY_KEY["revert_rate"]) == {
        "a": 1,
        "b": 1,
        "c": 3,
    }


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, ()),
        ({"median_size": 20.0}, ("small PRs",)),
        ({"revert_rate": 0.1}, ("reverts above repo",)),
        ({"light_review_share": 0.5}, ("light review",)),
        (
            {"median_size": 20.0, "revert_rate": 0.1, "light_review_share": 1.0},
            ("small PRs", "reverts above repo", "light review"),
        ),
        ({"prs_per_week": 1.0, "median_size": 20.0, "revert_rate": 0.5}, ()),
    ],
)
def test_guardrails_only_check_high_throughput_engineers(changes, expected):
    base = engineers.Engineer(
        name="x",
        merged_prs=10,
        reverted=0,
        prs_per_week=5.0,
        quality_adjusted_per_week=5.0,
        ai_share=0.5,
        revert_rate=0.0,
        median_size=100.0,
        median_first_review_h=1.0,
        median_cycle_h=5.0,
        light_review_share=0.0,
    )
    person = engineers.Engineer(
        **{**{f: getattr(base, f) for f in base.__dataclass_fields__}, **changes}
    )

    assert (
        engineers.flags(person, threshold=4.0, repo_size=100.0, repo_revert_rate=0.01) == expected
    )


def test_percentiles_and_concentration():
    assert engineers.percentiles([]) == (0.0,) * 5
    assert engineers.percentiles([2.0]) == (2.0,) * 5
    assert engineers.percentiles([1, 2, 3, 4, 5], (25, 50, 75)) == (2.0, 3.0, 4.0)
    people = engineers.engineers(
        tuple(pr(i * 10 + j, f"e{i}") for i, n in enumerate((10, 5, 3, 1, 1)) for j in range(n)),
        WINDOW,
    )
    assert engineers.concentration(people) == pytest.approx(10 / 20)


def test_ranking_filters_sorts_and_flags():
    prs = (
        *(pr(i, "fast", size=5) for i in range(8)),
        *(pr(20 + i, "steady", size=120) for i in range(6)),
        *(pr(40 + i, "small", size=100) for i in range(5)),
        pr(60, "drive-by"),
    )

    board = engineers.ranking(prs, WINDOW, min_prs=5, reveal=True)

    assert board.active == 4
    assert [e.name for e in board.people] == ["fast", "steady", "small"]
    assert board.ranks["prs_per_week"] == {"fast": 1, "steady": 2, "small": 3}
    assert board.flagged == (board.people[0],)
    assert board.people[0].flags == ("small PRs",)
    assert board.top_share == pytest.approx(8 / 20)

    by_size = engineers.ranking(prs, WINDOW, min_prs=5, reveal=True, sort="median_size")
    assert [e.name for e in by_size.people] == ["fast", "small", "steady"]


def test_paired_change_splits_adopters_and_others():
    prs = (
        *(pr(i, "adopter", window=EARLIER) for i in range(2)),
        *(pr(10 + i, "adopter", ai=True) for i in range(6)),
        *(pr(20 + i, "steady", window=EARLIER) for i in range(4)),
        *(pr(30 + i, "steady") for i in range(4)),
        pr(40, "gone", window=EARLIER),
        pr(41, "new", ai=True),
    )

    result = engineers.paired(prs, EARLIER, WINDOW)

    assert result.adopters.n == 1 and result.others.n == 1
    assert result.adopters.median_change == pytest.approx(2.0)
    assert result.others.median_change == pytest.approx(0.0)
    assert result.adopters.improved == 1.0 and result.others.improved == 0.0
    assert result.gap == pytest.approx(2.0)
    assert result.interval == (pytest.approx(2.0), pytest.approx(2.0))
    assert engineers.paired(prs, EARLIER, WINDOW, min_prs=5).adopters.n == 0


def test_bootstrap_is_deterministic():
    prs = tuple(
        pr(i * 10 + j, f"e{i}", window=w, ai=w is WINDOW and i % 2 == 0)
        for i in range(8)
        for w, k in ((EARLIER, 2), (WINDOW, 2 + i))
        for j in range(k)
    )

    first = engineers.paired(prs, EARLIER, WINDOW)

    assert first == engineers.paired(prs, EARLIER, WINDOW)
    assert first.interval[0] <= first.gap <= first.interval[1]


def test_markdown_warns_only_when_logins_are_revealed():
    board = engineers.ranking((pr(1, "alice"),), WINDOW, min_prs=1)
    pairs = (engineers.paired((), EARLIER, WINDOW),)

    assert "Local use only" not in engineers.to_markdown("W", board, pairs)
    assert "Local use only" in engineers.to_markdown("W", board, pairs, revealed=True)


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch, three_window_snapshot):
    folder = tmp_path / "data" / "real"
    folder.mkdir(parents=True)
    github_source.write_snapshot(three_window_snapshot, folder / "microsoft-vscode.json")
    monkeypatch.setattr(cli, "DATA", tmp_path / "data")
    monkeypatch.setattr(cli, "SNAPSHOT", folder / "microsoft-vscode.json")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _logins(snapshot: github_source.Snapshot) -> set[str]:
    return {p.author for p in github_source.to_pull_requests(snapshot) if not p.is_bot}


def test_cli_ranks_under_pseudonyms_by_default(snapshot_dir, three_window_snapshot, capsys):
    assert main(["engineers", "--min-prs", "1", "--sort", "revert_rate"]) == 0
    out = capsys.readouterr().out

    assert "### Engineers, microsoft/vscode, " in out and "(current)" in out
    assert "sorted by **Revert rate**" in out
    assert re.search(r"\| 1 \| eng-[0-9a-f]{6} \|", out)
    assert not any(re.search(rf"\b{re.escape(n)}\b", out) for n in _logins(three_window_snapshot))


def test_cli_reveals_logins_only_on_request(snapshot_dir, capsys):
    assert main(["engineers", "--min-prs", "1", "--window", "reference", "--logins"]) == 0
    out = capsys.readouterr().out

    assert "(reference)" in out and "Local use only" in out
    assert "alice" in out


def test_cli_fails_cleanly_without_the_requested_window(tmp_path, small_snapshot):
    path = tmp_path / "two.json"
    github_source.write_snapshot(small_snapshot, path)

    with pytest.raises(SystemExit, match="no reference window"):
        main(["engineers", "--snapshot", str(path), "--window", "reference"])


def test_dashboard_section_is_sortable_deterministic_and_pseudonymous(three_window_snapshot):
    view = real.build(three_window_snapshot)

    page = dashboard_real.render(view)

    assert page == dashboard_real.render(real.build(three_window_snapshot))
    assert "Engineers, " in page and 'table class="compact sortable"' in page
    assert dashboard_engineers.SORT_JS in page
    assert page.count('class="strip"') == 2
    section = dashboard_engineers.section(view, min_prs=1)
    names = {engineers.pseudonym(n) for n in _logins(three_window_snapshot)}
    assert any(n in section for n in names)
    for text in (page, section):
        assert not any(
            re.search(rf"\b{re.escape(n)}\b", text) for n in _logins(three_window_snapshot)
        )


def test_strip_chart_clips_to_the_axis():
    chart = dashboard_engineers.strip_chart(
        (dashboard_engineers.Strip("w", (("eng-000000", 1e-6), ("eng-ffffff", 1e6)), "#000"),),
        title="t",
        lo=0.1,
        hi=10,
        ticks=((1, "1"),),
        width=300,
        label_w=100,
    )

    xs = [float(x) for x in re.findall(r'<circle cx="([\d.]+)"', chart)]
    assert xs == [100.0, 284.0]


def test_window_fixtures_are_in_the_expected_order():
    assert BEFORE.start < MID.start < AFTER.start


PUBLISHED = (
    *sorted((ROOT / "docs").rglob("*.md")),
    *sorted((ROOT / "docs").glob("*.html")),
    ROOT / "README.md",
)


def test_no_real_login_appears_in_any_published_artifact():
    logins = set().union(
        *(_logins(github_source.load_snapshot(p)) for p in cli.committed_snapshots())
    )
    text = "\n".join(p.read_text(encoding="utf-8") for p in PUBLISHED)
    leaked = sorted(
        login
        for login in logins
        if re.search(rf"(?<![\w-]){re.escape(login)}(?![\w-])", text, re.IGNORECASE)
    )

    assert len(logins) > 500
    assert leaked == []


def test_pseudonyms_do_not_collide_in_committed_snapshots():
    logins = {
        login.lower()
        for p in cli.committed_snapshots()
        for login in _logins(github_source.load_snapshot(p))
    }

    assert len({engineers.pseudonym(login) for login in logins}) == len(logins)
