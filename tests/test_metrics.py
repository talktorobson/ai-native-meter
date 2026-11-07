from datetime import datetime, timedelta

import pytest

from devex.metrics import (
    PullRequest,
    Window,
    change_failure_rate,
    cohort_scorecards,
    cost_per_merged_pr,
    gaming_signals,
    merged_in,
    scorecard,
)

T0 = datetime(2026, 1, 5)
WINDOW = Window(T0, T0 + timedelta(weeks=2))


def pr(
    pid: str,
    *,
    opened_h: float = 0,
    merged_h: float | None = 10,
    review_h: float | None = 2,
    lines: int = 50,
    ai: bool = False,
    reverted: bool = False,
    incident: bool = False,
    cost: float = 0.0,
    bot: bool = False,
) -> PullRequest:
    at = lambda h: None if h is None else T0 + timedelta(hours=h)  # noqa: E731
    return PullRequest(
        id=pid,
        author="eng-1",
        opened_at=at(opened_h),
        merged_at=at(merged_h),
        first_review_at=at(review_h),
        lines_changed=lines,
        ai_assisted=ai,
        reverted=reverted,
        caused_incident=incident,
        token_cost_usd=cost,
        is_bot=bot,
    )


def test_window_weeks_and_bounds():
    assert WINDOW.weeks == pytest.approx(2.0)
    assert WINDOW.contains(T0)
    assert not WINDOW.contains(WINDOW.end)
    assert not WINDOW.contains(None)


def test_merged_in_excludes_bots_unmerged_and_out_of_window():
    prs = (
        pr("a"),
        pr("bot", bot=True),
        pr("open", merged_h=None),
        pr("late", merged_h=24 * 15),
    )
    assert [p.id for p in merged_in(prs, WINDOW)] == ["a"]


def test_change_failure_rate_counts_reverts_and_incidents():
    prs = (pr("a"), pr("b", reverted=True), pr("c", incident=True), pr("d"))
    assert change_failure_rate(prs) == 0.5
    assert change_failure_rate(()) == 0.0


def test_cost_per_merged_pr_keeps_abandoned_spend():
    prs = (pr("a", cost=4.0), pr("abandoned", merged_h=None, cost=6.0))
    assert cost_per_merged_pr(prs, WINDOW) == 10.0


def test_scorecard_core_numbers():
    prs = (
        pr("a", merged_h=10, review_h=2, lines=40, ai=True, cost=2.0),
        pr("b", merged_h=30, review_h=6, lines=60, reverted=True),
        pr("c", merged_h=20, review_h=4, lines=80, ai=True, cost=4.0),
        pr("bot", bot=True),
    )
    card = scorecard(prs, engineers=3, window=WINDOW)
    assert card.merged_prs == 3
    assert card.prs_per_engineer_week == pytest.approx(0.5)
    assert card.quality_adjusted_prs_per_engineer_week == pytest.approx(2 / 6)
    assert card.change_failure_rate == pytest.approx(1 / 3)
    assert card.median_cycle_time_hours == 20
    assert card.median_review_latency_hours == 4
    assert card.median_pr_size_lines == 60
    assert card.ai_assisted_share == pytest.approx(2 / 3)
    assert card.cost_per_merged_pr_usd == pytest.approx(2.0)


def test_scorecard_handles_empty_input():
    card = scorecard((), engineers=10, window=WINDOW)
    assert card.merged_prs == 0
    assert card.prs_per_engineer_week == 0
    assert card.median_cycle_time_hours == 0


def test_cohort_scorecards_split_by_predicate():
    prs = (pr("a", ai=True), pr("b", ai=True), pr("c"))
    cards = cohort_scorecards(
        prs, WINDOW, lambda p: "ai" if p.ai_assisted else "human", lambda _: 1
    )
    assert set(cards) == {"ai", "human"}
    assert cards["ai"].merged_prs == 2
    assert cards["human"].merged_prs == 1


def test_gaming_signals_flags_pr_splitting_quality_and_cost():
    base = scorecard((pr("a", lines=200, cost=1.0),), 1, WINDOW)
    split = scorecard(
        tuple(pr(f"s{i}", lines=20, reverted=i == 0, cost=1.0) for i in range(4)), 1, WINDOW
    )
    signals = gaming_signals(base, split)
    assert len(signals) == 2
    assert any("PR splitting" in s for s in signals)
    assert any("change failure rate" in s for s in signals)

    pricey = scorecard((pr("a", lines=200, cost=5.0),), 1, WINDOW)
    assert any("Token cost" in s for s in gaming_signals(base, pricey))


def test_gaming_signals_quiet_on_healthy_improvement():
    base = scorecard((pr("a", lines=100, cost=1.0),), 1, WINDOW)
    better = scorecard((pr("a", lines=90, cost=0.5), pr("b", lines=110, cost=0.5)), 1, WINDOW)
    assert gaming_signals(base, better) == ()
