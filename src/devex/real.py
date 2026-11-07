"""The metrics engine run on a real snapshot: baseline window vs current window, AI cohorts,
the evidence behind the AI attribution, and the Goodhart guards. Pure functions only."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from devex import github_source, metrics
from devex.github_source import COHORTS, Snapshot
from devex.metrics import PullRequest, Scorecard, Window

__all__ = [
    "LIMITS",
    "RealView",
    "build",
    "change",
    "cohorts",
    "evidence_counts",
    "to_markdown",
    "window_label",
]

LIMITS = (
    "Reverts are a change-failure proxy: incidents, hotfixes and rollbacks without a revert PR "
    "are invisible, so the true failure rate is higher on both sides.",
    "AI attribution is incomplete: the baseline has no observable signal, not no AI use, and "
    "assistance without a trailer, marker or agent author is counted as human.",
    "Correlation is not causation: team size, roadmap, release cadence and tooling all changed "
    "between the windows.",
    "Open source is not an enterprise: drive-by contributors dilute per-engineer numbers and "
    "review norms differ.",
    "Token cost is not observable in public data, so cost per merged PR is not reported.",
)

ROWS = (
    ("Merged PRs", "merged_prs", "{:,}"),
    ("Active engineers", "engineers", "{:,}"),
    ("PRs / engineer / week", "prs_per_engineer_week", "{:.2f}"),
    ("Quality-adjusted PRs / engineer / week", "quality_adjusted_prs_per_engineer_week", "{:.2f}"),
    ("Revert rate (change failure proxy)", "change_failure_rate", "{:.1%}"),
    ("Median cycle time (h)", "median_cycle_time_hours", "{:.1f}"),
    ("Median time to first human review (h)", "median_review_latency_hours", "{:.1f}"),
    ("Median PR size (lines)", "median_pr_size_lines", "{:.0f}"),
    ("AI-assisted share", "ai_assisted_share", "{:.0%}"),
)


@dataclass(frozen=True, slots=True)
class RealView:
    snapshot: Snapshot
    prs: tuple[PullRequest, ...]
    before_window: Window
    after_window: Window
    before_label: str
    after_label: str
    fetched_on: str
    before: Scorecard
    after: Scorecard
    cohorts_before: dict[str, Scorecard]
    cohorts_after: dict[str, Scorecard]
    evidence_after: tuple[tuple[str, int], ...]
    signals: tuple[str, ...]


def window_label(window: Window) -> str:
    start, end = window.start, window.end
    months = (end.year - start.year) * 12 + end.month - start.month
    if start.day == end.day == 1 and months == 3 and start.month % 3 == 1:
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    return f"{start:%Y-%m-%d} to {end:%Y-%m-%d}"


def cohorts(
    prs: Sequence[PullRequest], evidence: dict[str, tuple[str, ...]], window: Window
) -> dict[str, Scorecard]:
    """``cohort_scorecards`` by strength of AI evidence; each cohort counts its own engineers."""

    def cohort_of(pr: PullRequest) -> str:
        return github_source.ai_cohort(evidence.get(pr.id, ()))

    merged = metrics.merged_in(prs, window)
    authors = {name: {pr.author for pr in merged if cohort_of(pr) == name} for name in COHORTS}
    cards = metrics.cohort_scorecards(merged, window, cohort_of, lambda n: len(authors[n]))
    return {name: cards[name] for name in COHORTS if name in cards}


def evidence_counts(
    prs: Sequence[PullRequest], evidence: dict[str, tuple[str, ...]], window: Window
) -> tuple[tuple[str, int], ...]:
    """How many merged PRs carry each kind of evidence (a PR can carry several)."""
    counts = Counter(e for pr in metrics.merged_in(prs, window) for e in evidence.get(pr.id, ()))
    return tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def build(snapshot: Snapshot) -> RealView:
    """First fetched window is the baseline, last one is the current window."""
    if len(snapshot.windows) < 2:
        raise ValueError("snapshot needs two fetched windows (baseline and current)")
    before_w, after_w = snapshot.windows[0][0], snapshot.windows[-1][0]
    prs = github_source.to_pull_requests(snapshot)
    evidence = snapshot.evidence()
    before = metrics.scorecard(prs, github_source.engineers_in(prs, before_w), before_w)
    after = metrics.scorecard(prs, github_source.engineers_in(prs, after_w), after_w)
    return RealView(
        snapshot=snapshot,
        prs=prs,
        before_window=before_w,
        after_window=after_w,
        before_label=window_label(before_w),
        after_label=window_label(after_w),
        fetched_on=max(f for _, f in snapshot.windows),
        before=before,
        after=after,
        cohorts_before=cohorts(prs, evidence, before_w),
        cohorts_after=cohorts(prs, evidence, after_w),
        evidence_after=evidence_counts(prs, evidence, after_w),
        signals=metrics.gaming_signals(before, after),
    )


def _cohort_rows(label: str, cards: dict[str, Scorecard]) -> str:
    total = sum(c.merged_prs for c in cards.values())
    return "\n".join(
        f"| {label} | {name} | {c.merged_prs:,} | {c.merged_prs / total:.0%} | {c.engineers} "
        f"| {c.change_failure_rate:.1%} | {c.median_cycle_time_hours:.1f} "
        f"| {c.median_review_latency_hours:.1f} | {c.median_pr_size_lines:.0f} |"
        for name, c in cards.items()
    )


def change(before: float, after: float, fmt: str) -> str:
    """Rates change in percentage points; everything else relatively."""
    if fmt.endswith("%}"):
        return f"{(after - before) * 100:+.1f} pp"
    return f"{metrics.relative_change(before, after):+.0%}"


def to_markdown(view: RealView) -> str:
    before, after = view.before, view.after
    table = "\n".join(
        f"| {label} | {fmt.format(getattr(before, f))} | {fmt.format(getattr(after, f))} "
        f"| {change(getattr(before, f), getattr(after, f), fmt)} |"
        for label, f, fmt in ROWS
    )
    evidence = "\n".join(f"| `{kind}` | {count:,} |" for kind, count in view.evidence_after)
    signals = view.signals or ("None triggered.",)
    return "\n".join(
        (
            f"## Real data: {view.snapshot.repo}, {view.before_label} vs {view.after_label}",
            "",
            f"Source: {view.snapshot.url} · merged PRs into `{view.snapshot.default_branch}` · "
            f"fetched {view.fetched_on} · {len(view.prs):,} PRs in the snapshot.",
            "",
            f"| Metric | {view.before_label} | {view.after_label} | Change |",
            "|---|---|---|---|",
            table,
            "| Token cost per merged PR | not observable | not observable | n/a |",
            "",
            "**AI cohorts** (cohort engineers are the distinct authors in that cohort)",
            "",
            "| Window | Cohort | Merged PRs | Share | Engineers | Revert rate "
            "| Median cycle (h) | Median first review (h) | Median size |",
            "|---|---|---|---|---|---|---|---|---|",
            _cohort_rows(view.before_label, view.cohorts_before),
            _cohort_rows(view.after_label, view.cohorts_after),
            "",
            f"**AI evidence on merged PRs, {view.after_label}** (a PR can carry several)",
            "",
            "| Evidence | PRs |",
            "|---|---|",
            evidence or "| none | 0 |",
            "",
            "**Goodhart guards**",
            *(f"- {s}" for s in signals),
            "",
            "**Limits**",
            *(f"- {s}" for s in LIMITS),
        )
    )
