"""The metrics engine run on a real snapshot: current window vs baseline window (year over year
by default), an optional earlier reference window, AI cohorts, the evidence behind the AI
attribution, the Goodhart guards, and how much a repository consolidation moves the headline.
Pure functions only."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from devex import github_source, metrics
from devex.github_source import COHORTS, Snapshot
from devex.metrics import PullRequest, Scorecard, Window

__all__ = [
    "CONSOLIDATION_ROWS",
    "LIMITS",
    "Period",
    "RealView",
    "Variant",
    "build",
    "change",
    "cohorts",
    "consolidation",
    "evidence_counts",
    "moved_note",
    "pick_windows",
    "to_markdown",
    "window_label",
]

LIMITS = (
    "Reverts are a change-failure proxy: incidents, hotfixes and rollbacks without a revert PR "
    "are invisible, so the true failure rate is higher on both sides.",
    "AI attribution is incomplete: a window with no observable signal is not a window with no AI "
    "use, and assistance without a trailer, marker or agent author is counted as human.",
    "Repositories change shape: code that moved in from another repository inflates a "
    "before/after comparison unless the baseline counts the old repository too.",
    "Correlation is not causation: team size, roadmap, release cadence and tooling all changed "
    "between the windows. Comparing the same quarter a year apart removes seasonality, "
    "nothing else.",
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
class Period:
    """One fetched window, scored. ``role`` is "reference", "baseline" or "current"."""

    role: str
    window: Window
    label: str
    card: Scorecard
    cohorts: dict[str, Scorecard]
    evidence: tuple[tuple[str, int], ...]
    repos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Variant:
    """One way of drawing the repository boundary for the baseline vs current comparison."""

    name: str
    baseline: Scorecard
    current: Scorecard
    headline: bool = False


@dataclass(frozen=True, slots=True)
class RealView:
    snapshot: Snapshot
    prs: tuple[PullRequest, ...]
    periods: tuple[Period, ...]  # chronological
    fetched_on: str
    signals: tuple[str, ...]  # current vs baseline
    signals_reference: tuple[str, ...]  # current vs reference, empty without a reference
    variants: tuple[Variant, ...] = ()  # empty unless the baseline counts a predecessor repo

    def _role(self, role: str) -> Period | None:
        return next((p for p in self.periods if p.role == role), None)

    @property
    def baseline(self) -> Period:
        return self._role("baseline")

    @property
    def current(self) -> Period:
        return self._role("current")

    @property
    def reference(self) -> Period | None:
        return self._role("reference")

    @property
    def before(self) -> Scorecard:
        return self.baseline.card

    @property
    def after(self) -> Scorecard:
        return self.current.card

    @property
    def before_label(self) -> str:
        return self.baseline.label

    @property
    def after_label(self) -> str:
        return self.current.label

    @property
    def cohorts_before(self) -> dict[str, Scorecard]:
        return self.baseline.cohorts

    @property
    def cohorts_after(self) -> dict[str, Scorecard]:
        return self.current.cohorts

    @property
    def evidence_after(self) -> tuple[tuple[str, int], ...]:
        return self.current.evidence


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


def _year_before(day: date) -> date | None:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return None


def pick_windows(
    windows: Sequence[Window], baseline: str | None = None, current: str | None = None
) -> tuple[Window | None, Window, Window]:
    """Choose ``(reference, baseline, current)`` among fetched windows, by start date.

    ``current`` defaults to the latest window. ``baseline`` defaults to the window that starts
    one year before ``current`` (same season), else the earliest. The reference is the earliest
    remaining window that starts before the baseline, if any.
    """
    if len(windows) < 2:
        raise ValueError("snapshot needs two fetched windows (baseline and current)")
    by_start = {w.start.date().isoformat(): w for w in windows}

    def find(start: str) -> Window:
        if start not in by_start:
            raise ValueError(f"no fetched window starts on {start}; have {', '.join(by_start)}")
        return by_start[start]

    ordered = sorted(windows, key=lambda w: w.start)
    cur = find(current) if current else ordered[-1]
    year_before = _year_before(cur.start.date())
    same_season = by_start.get(year_before.isoformat()) if year_before else None
    base = find(baseline) if baseline else same_season or ordered[0]
    if base == cur:
        raise ValueError("baseline and current must be different windows")
    earlier = [w for w in ordered if w.start < base.start and w != cur]
    return (earlier[0] if earlier else None), base, cur


def _card(prs: Sequence[PullRequest], window: Window) -> Scorecard:
    return metrics.scorecard(prs, github_source.engineers_in(prs, window), window)


def _under(scope: str | None, path: str) -> bool:
    return bool(path) and scope is not None and (scope == path or scope.startswith(f"{path}/"))


def consolidation(
    snapshot: Snapshot, prs: Sequence[PullRequest], before: Window, after: Window
) -> tuple[Variant, ...]:
    """The comparison with and without the predecessor repositories fetched for the baseline,
    plus, where file scopes are known, the current window without PRs confined to the paths
    the predecessors' code moved to."""
    moved = tuple(p for p in snapshot.predecessors if p.window == before)
    if not moved:
        return ()
    primary = tuple(pr for pr in prs if pr.id.startswith(f"{snapshot.repo}#"))
    scopes = {github_source.pr_id(r, snapshot.repo): r.get("scope") for r in snapshot.records}
    paths = tuple(p.moved_to for p in moved if p.moved_to)
    outside = tuple(
        pr for pr in primary if not any(_under(scopes.get(pr.id), path) for path in paths)
    )
    known = any(scopes.get(pr.id) is not None for pr in metrics.merged_in(primary, after))
    label, repos = window_label(before), " + ".join(p.repo for p in moved)
    return (
        Variant(f"{snapshot.repo} only (uncorrected)", _card(primary, before), _card(prs, after)),
        Variant(
            f"{label} counts {repos} (headline)",
            _card(prs, before),
            _card(prs, after),
            headline=True,
        ),
        *(
            (
                Variant(
                    f"{window_label(after)} without PRs only in "
                    + ", ".join(f"`{path}`" for path in paths),
                    _card(primary, before),
                    _card(outside, after),
                ),
            )
            if paths and known
            else ()
        ),
    )


def build(snapshot: Snapshot, baseline: str | None = None, current: str | None = None) -> RealView:
    """Score the reference, baseline and current windows chosen by ``pick_windows``."""
    reference_w, before_w, after_w = pick_windows(
        tuple(w for w, _ in snapshot.windows), baseline, current
    )
    prs = github_source.to_pull_requests(snapshot)
    evidence = snapshot.evidence()

    def period(role: str, window: Window) -> Period:
        return Period(
            role=role,
            window=window,
            label=window_label(window),
            card=_card(prs, window),
            cohorts=cohorts(prs, evidence, window),
            evidence=evidence_counts(prs, evidence, window),
            repos=snapshot.repos_in(window),
        )

    roles = (("reference", reference_w), ("baseline", before_w), ("current", after_w))
    periods = tuple(
        sorted((period(r, w) for r, w in roles if w is not None), key=lambda p: p.window.start)
    )
    by_role = {p.role: p.card for p in periods}
    reference = by_role.get("reference")
    return RealView(
        snapshot=snapshot,
        prs=prs,
        periods=periods,
        fetched_on=max(f for _, f in snapshot.windows),
        signals=metrics.gaming_signals(by_role["baseline"], by_role["current"]),
        signals_reference=(
            metrics.gaming_signals(reference, by_role["current"]) if reference else ()
        ),
        variants=consolidation(snapshot, prs, before_w, after_w),
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


def _heading(view: RealView) -> str:
    ref = view.reference
    tail = f" (pre-AI reference {ref.label})" if ref else ""
    return f"## Real data: {view.snapshot.repo}, {view.before_label} vs {view.after_label}{tail}"


def _evidence_table(view: RealView) -> str:
    counts = [dict(p.evidence) for p in view.periods]
    kinds = sorted(
        {k for c in counts for k in c}, key=lambda k: (-dict(view.evidence_after).get(k, 0), k)
    )
    header = "| Evidence | " + " | ".join(p.label for p in view.periods) + " |"
    rule = "|---|" + "---|" * len(view.periods)
    rows = (f"| `{k}` | " + " | ".join(f"{c.get(k, 0):,}" for c in counts) + " |" for k in kinds)
    return "\n".join((header, rule, *rows)) if kinds else "No AI evidence on merged PRs."


CONSOLIDATION_ROWS = (
    ("Merged PRs", "merged_prs", "{:,}"),
    ("Active engineers", "engineers", "{:,}"),
    ("PRs / engineer / week", "prs_per_engineer_week", "{:.2f}"),
    ("AI-assisted share", "ai_assisted_share", "{:.0%}"),
    ("Revert rate", "change_failure_rate", "{:.1%}"),
    ("Median PR size", "median_pr_size_lines", "{:.0f}"),
)


def moved_note(view: RealView) -> str:
    """One sentence on which repositories each window counts, or "" without predecessors."""
    snapshot, base = view.snapshot, view.baseline
    moved = [p for p in snapshot.predecessors if p.window == base.window]
    if not moved:
        return ""
    where = "; ".join(
        f"{p.repo}'s code now lives in {snapshot.repo}"
        + (f" under `{p.moved_to}`" if p.moved_to else "")
        for p in moved
    )
    others = [p.label for p in view.periods if p.repos == (snapshot.repo,)]
    alone = (
        f"; {', '.join(others[:-1])}{' and ' if len(others) > 1 else ''}{others[-1]} count "
        f"{snapshot.repo} alone."
        if others
        else "."
    )
    return (
        f"{where}. The {base.label} baseline counts {' + '.join(base.repos)}, so both windows "
        f"cover the same code{alone}"
    )


def _consolidation(view: RealView) -> tuple[str, ...]:
    if not view.variants:
        return ()
    base, cur = view.baseline.label, view.current.label

    def cell(v: Variant, f: str, fmt: str) -> str:
        b, c = getattr(v.baseline, f), getattr(v.current, f)
        return f"{fmt.format(b)} → {fmt.format(c)} ({change(b, c, fmt)})"

    def row(v: Variant) -> str:
        name = f"**{v.name}**" if v.headline else v.name
        return (
            f"| {name} | " + " | ".join(cell(v, f, fmt) for _, f, fmt in CONSOLIDATION_ROWS) + " |"
        )

    return (
        "",
        f"**Repository consolidation** ({cur} vs {base})",
        "",
        moved_note(view),
        "",
        "| Comparison | " + " | ".join(label for label, _, _ in CONSOLIDATION_ROWS) + " |",
        "|---|" + "---|" * len(CONSOLIDATION_ROWS),
        *(row(v) for v in view.variants),
    )


def _guards(view: RealView) -> tuple[str, ...]:
    pairs = ((view.baseline, view.signals),) + (
        ((view.reference, view.signals_reference),) if view.reference else ()
    )
    return tuple(
        f"- {view.after_label} vs {p.label}: {s}"
        for p, signals in pairs
        for s in (signals or ("none triggered.",))
    )


def to_markdown(view: RealView) -> str:
    ref, base, cur = view.reference, view.baseline, view.current
    shown = (ref, base, cur) if ref else (base, cur)
    compared = (base, ref) if ref else (base,)

    def row(label: str, f: str, fmt: str) -> str:
        values = " | ".join(fmt.format(getattr(p.card, f)) for p in shown)
        now = getattr(cur.card, f)
        changes = " | ".join(change(getattr(p.card, f), now, fmt) for p in compared)
        return f"| {label} | {values} | {changes} |"

    def title(p: Period) -> str:
        return f"{p.label} ({p.role})"

    header = (
        "| Metric | "
        + " | ".join(title(p) for p in shown)
        + " | "
        + " | ".join(f"{cur.label} vs {p.label}" for p in compared)
        + " |"
    )
    unobservable = " | ".join("not observable" for _ in shown)
    na = " | ".join("n/a" for _ in compared)
    return "\n".join(
        (
            _heading(view),
            "",
            f"Source: {view.snapshot.url} · merged PRs into `{view.snapshot.default_branch}` · "
            f"fetched {view.fetched_on} · {len(view.prs):,} PRs in the snapshot.",
            "",
            header,
            "|---|" + "---|" * (len(shown) + len(compared)),
            *(row(label, f, fmt) for label, f, fmt in ROWS),
            f"| Token cost per merged PR | {unobservable} | {na} |",
            *_consolidation(view),
            "",
            "**AI cohorts** (cohort engineers are the distinct authors in that cohort)",
            "",
            "| Window | Cohort | Merged PRs | Share | Engineers | Revert rate "
            "| Median cycle (h) | Median first review (h) | Median size |",
            "|---|---|---|---|---|---|---|---|---|",
            *(_cohort_rows(p.label, p.cohorts) for p in view.periods),
            "",
            "**AI evidence on merged PRs** (a PR can carry several)",
            "",
            _evidence_table(view),
            "",
            "**Goodhart guards**",
            *_guards(view),
            "",
            "**Limits**",
            *(f"- {s}" for s in LIMITS),
        )
    )
