"""Throughput, quality and cost metrics for an AI-native developer experience.

North star: quality-adjusted merged PRs per engineer per week.
Guardrails: change failure rate, cycle time, review latency, token cost per merged PR.

Everything here is a pure function over immutable records so the same code can run
on a laptop sample or inside a warehouse job.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from statistics import median

__all__ = [
    "PullRequest",
    "Scorecard",
    "Window",
    "change_failure_rate",
    "cohort_scorecards",
    "cost_per_merged_pr",
    "gaming_signals",
    "is_failed",
    "merged_in",
    "relative_change",
    "scorecard",
]


@dataclass(frozen=True, slots=True)
class PullRequest:
    id: str
    author: str
    opened_at: datetime
    merged_at: datetime | None
    first_review_at: datetime | None
    lines_changed: int
    ai_assisted: bool
    reverted: bool = False
    caused_incident: bool = False
    tokens: int = 0
    token_cost_usd: float = 0.0
    is_bot: bool = False


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime

    @property
    def weeks(self) -> float:
        return (self.end - self.start).total_seconds() / (7 * 24 * 3600)

    def contains(self, moment: datetime | None) -> bool:
        return moment is not None and self.start <= moment < self.end


@dataclass(frozen=True, slots=True)
class Scorecard:
    engineers: int
    weeks: float
    merged_prs: int
    prs_per_engineer_week: float
    quality_adjusted_prs_per_engineer_week: float
    change_failure_rate: float
    median_cycle_time_hours: float
    median_review_latency_hours: float
    median_pr_size_lines: float
    ai_assisted_share: float
    token_cost_usd: float
    cost_per_merged_pr_usd: float


def _hours(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _median(values: Iterable[float]) -> float:
    items = tuple(values)
    return float(median(items)) if items else 0.0


def is_failed(pr: PullRequest) -> bool:
    return pr.reverted or pr.caused_incident


def merged_in(prs: Iterable[PullRequest], window: Window) -> tuple[PullRequest, ...]:
    """Human-authored PRs merged inside the window. Bot PRs never count as throughput."""
    return tuple(pr for pr in prs if not pr.is_bot and window.contains(pr.merged_at))


def opened_in(prs: Iterable[PullRequest], window: Window) -> tuple[PullRequest, ...]:
    return tuple(pr for pr in prs if not pr.is_bot and window.contains(pr.opened_at))


def change_failure_rate(merged: Iterable[PullRequest]) -> float:
    items = tuple(merged)
    return _safe_div(sum(map(is_failed, items)), len(items))


def cost_per_merged_pr(prs: Iterable[PullRequest], window: Window) -> float:
    """Token spend on everything opened in the window, divided by what actually shipped.

    Abandoned agent attempts are real cost, so they stay in the numerator.
    """
    items = tuple(prs)
    spend = sum(pr.token_cost_usd for pr in opened_in(items, window))
    return _safe_div(spend, len(merged_in(items, window)))


def scorecard(prs: Iterable[PullRequest], engineers: int, window: Window) -> Scorecard:
    items = tuple(prs)
    merged = merged_in(items, window)
    capacity = engineers * window.weeks
    shipped_clean = sum(not is_failed(pr) for pr in merged)
    return Scorecard(
        engineers=engineers,
        weeks=round(window.weeks, 2),
        merged_prs=len(merged),
        prs_per_engineer_week=_safe_div(len(merged), capacity),
        quality_adjusted_prs_per_engineer_week=_safe_div(shipped_clean, capacity),
        change_failure_rate=change_failure_rate(merged),
        median_cycle_time_hours=_median(_hours(pr.opened_at, pr.merged_at) for pr in merged),
        median_review_latency_hours=_median(
            _hours(pr.opened_at, pr.first_review_at) for pr in merged if pr.first_review_at
        ),
        median_pr_size_lines=_median(pr.lines_changed for pr in merged),
        ai_assisted_share=_safe_div(sum(pr.ai_assisted for pr in merged), len(merged)),
        token_cost_usd=sum(pr.token_cost_usd for pr in opened_in(items, window)),
        cost_per_merged_pr_usd=cost_per_merged_pr(items, window),
    )


def cohort_scorecards(
    prs: Iterable[PullRequest],
    window: Window,
    cohort_of: Callable[[PullRequest], str],
    engineers_of: Callable[[str], int],
) -> dict[str, Scorecard]:
    """Split PRs into cohorts (e.g. AI-assisted vs not, team A vs team B) and score each."""
    items = tuple(prs)
    names = sorted({cohort_of(pr) for pr in items})
    return {
        name: scorecard((pr for pr in items if cohort_of(pr) == name), engineers_of(name), window)
        for name in names
    }


def relative_change(before: float, after: float) -> float:
    return _safe_div(after - before, before)


def cost_quality_signal(
    cost_before: float,
    cost_after: float,
    cfr_before: float,
    cfr_after: float,
    latency_before: float = 0.0,
    latency_after: float = 0.0,
    *,
    max_cfr_increase_pp: float = 0.01,
    max_latency_increase: float = 0.25,
) -> str | None:
    """Token efficiency never trumps quality: a cheaper change is only a gain if it is not a worse
    one. Shared by the PR scorecard guards and the token-spend team guards."""
    if not cost_after < cost_before:
        return None
    worse = (
        ("change failure rate", cfr_after - cfr_before > max_cfr_increase_pp),
        (
            "review latency",
            latency_before > 0
            and relative_change(latency_before, latency_after) > max_latency_increase,
        ),
    )
    hit = tuple(name for name, bad in worse if bad)
    if not hit:
        return None
    return f"Cost per change fell while {' and '.join(hit)} worsened: savings bought with quality."


def gaming_signals(
    baseline: Scorecard,
    current: Scorecard,
    *,
    max_size_drop: float = 0.40,
    max_cfr_increase_pp: float = 0.01,
    max_cost_increase: float = 0.50,
) -> tuple[str, ...]:
    """Goodhart guards: throughput gains that are probably not real gains."""
    throughput_up = current.prs_per_engineer_week > baseline.prs_per_engineer_week
    cheaper_but_worse = cost_quality_signal(
        baseline.cost_per_merged_pr_usd,
        current.cost_per_merged_pr_usd,
        baseline.change_failure_rate,
        current.change_failure_rate,
        baseline.median_review_latency_hours,
        current.median_review_latency_hours,
        max_cfr_increase_pp=max_cfr_increase_pp,
    )
    checks = (
        (
            throughput_up
            and relative_change(baseline.median_pr_size_lines, current.median_pr_size_lines)
            < -max_size_drop,
            "Throughput rose while median PR size collapsed: possible PR splitting.",
        ),
        (
            throughput_up
            and current.change_failure_rate - baseline.change_failure_rate > max_cfr_increase_pp,
            "Throughput rose but change failure rate worsened: speed is being bought with quality.",
        ),
        (
            relative_change(baseline.cost_per_merged_pr_usd, current.cost_per_merged_pr_usd)
            > max_cost_increase,
            "Token cost per merged PR grew faster than the guardrail: check routing and loops.",
        ),
        (cheaper_but_worse is not None, cheaper_but_worse or ""),
    )
    return tuple(message for triggered, message in checks if triggered)
