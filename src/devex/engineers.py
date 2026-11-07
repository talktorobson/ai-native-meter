"""Engineer-level KPIs on real PR data: a ranking with its quality columns and guardrail flags,
the spread of per-engineer output, and the paired change of engineers active in two windows.

Individual numbers are for distribution, coaching and self-reflection, not public leaderboards
(ADR 0005): every name is a stable pseudonym unless the caller opts in to real logins, and
throughput is never reported without the quality columns next to it. Pure functions only.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from statistics import median, quantiles

from devex import metrics, real
from devex.metrics import PullRequest, Window

__all__ = [
    "ADOPTER_SHARE",
    "CAVEATS",
    "GUARDS",
    "KPIS",
    "PERCENTILES",
    "Engineer",
    "Group",
    "Kpi",
    "Pair",
    "Paired",
    "Ranking",
    "concentration",
    "engineers",
    "flags",
    "paired",
    "percentiles",
    "pseudonym",
    "ranking",
    "ranks",
    "to_markdown",
    "view_markdown",
    "view_pairs",
]

SALT = "ai-native-meter/engineers/v1"
ADOPTER_SHARE = 0.5  # an engineer is an AI adopter at this AI-assisted share of their PRs
HIGH_THROUGHPUT = 0.75  # guardrails apply from this percentile of per-engineer PRs per week
SPLIT_FACTOR = 0.5  # "small PRs": median size under this fraction of the repository median
LIGHT_REVIEW = 0.5  # "light review": this share of PRs merged unreviewed or approved within
FAST_REVIEW_H = 5 / 60  # five minutes of opening, the closest public proxy for a rubber stamp
PERCENTILES = (10, 25, 50, 75, 90)
TOP_SHARE = 0.10  # concentration: share of merged PRs by the top 10% of engineers
BOOTSTRAP = 2000
SEED = 20260101


def pseudonym(login: str) -> str:
    """A stable, non-reversible name: the same login gets the same name in every window, run and
    repository. Lower-cased first, as GitHub logins are case-insensitive."""
    return "eng-" + hashlib.sha256(f"{SALT}:{login.lower()}".encode()).hexdigest()[:6]


@dataclass(frozen=True, slots=True)
class Kpi:
    key: str
    label: str
    fmt: str
    higher_first: bool  # rank 1 goes to the highest value


KPIS: tuple[Kpi, ...] = (
    Kpi("prs_per_week", "PRs / week", "{:.2f}", True),
    Kpi("quality_adjusted_per_week", "Quality-adj. / week", "{:.2f}", True),
    Kpi("ai_share", "AI share", "{:.0%}", True),
    Kpi("revert_rate", "Revert rate", "{:.1%}", False),
    Kpi("median_size", "Median size", "{:.0f}", False),
    Kpi("median_first_review_h", "First review (h)", "{:.1f}", False),
    Kpi("median_cycle_h", "Cycle (h)", "{:.1f}", False),
)
KPI_BY_KEY = {k.key: k for k in KPIS}


@dataclass(frozen=True, slots=True)
class Engineer:
    """One human's merged PRs in one window. ``name`` is a pseudonym unless revealed."""

    name: str
    merged_prs: int
    reverted: int
    prs_per_week: float
    quality_adjusted_per_week: float  # merged PRs per week, reverted ones excluded
    ai_share: float
    revert_rate: float
    median_size: float
    median_first_review_h: float | None  # None when no PR had a human review
    median_cycle_h: float
    light_review_share: float  # no human review, or the first one within FAST_REVIEW_H
    flags: tuple[str, ...] = ()


def _hours(start, end) -> float:
    return (end - start).total_seconds() / 3600


def _engineer(name: str, prs: Sequence[PullRequest], weeks: float) -> Engineer:
    reviewed = tuple(_hours(p.opened_at, p.first_review_at) for p in prs if p.first_review_at)
    light = len(prs) - len(reviewed) + sum(h <= FAST_REVIEW_H for h in reviewed)
    reverted = sum(map(metrics.is_failed, prs))
    return Engineer(
        name=name,
        merged_prs=len(prs),
        reverted=reverted,
        prs_per_week=len(prs) / weeks,
        quality_adjusted_per_week=(len(prs) - reverted) / weeks,
        ai_share=sum(p.ai_assisted for p in prs) / len(prs),
        revert_rate=reverted / len(prs),
        median_size=float(median(p.lines_changed for p in prs)),
        median_first_review_h=float(median(reviewed)) if reviewed else None,
        median_cycle_h=float(median(_hours(p.opened_at, p.merged_at) for p in prs)),
        light_review_share=light / len(prs),
    )


def engineers(
    prs: Iterable[PullRequest], window: Window, reveal: bool = False
) -> tuple[Engineer, ...]:
    """Every engineer with a merged PR in the window (bots excluded, as everywhere), by name."""
    merged = metrics.merged_in(prs, window)
    by_login: dict[str, list[PullRequest]] = {}
    for pr in merged:
        by_login.setdefault(pr.author, []).append(pr)
    name: Callable[[str], str] = (lambda s: s) if reveal else pseudonym
    return tuple(
        sorted(
            (
                _engineer(name(login), tuple(items), window.weeks)
                for login, items in by_login.items()
            ),
            key=lambda e: e.name,
        )
    )


def percentiles(values: Sequence[float], points: Sequence[int] = PERCENTILES) -> tuple[float, ...]:
    """Inclusive percentiles; with a single value every percentile is that value."""
    if not values:
        return tuple(0.0 for _ in points)
    if len(values) == 1:
        return tuple(float(values[0]) for _ in points)
    cuts = quantiles(values, n=100, method="inclusive")
    return tuple(cuts[p - 1] for p in points)


def concentration(people: Sequence[Engineer], top: float = TOP_SHARE) -> float:
    """Share of merged PRs by the most prolific ``top`` fraction of engineers (at least one)."""
    counts = sorted((e.merged_prs for e in people), reverse=True)
    if not counts:
        return 0.0
    return sum(counts[: max(1, round(len(counts) * top))]) / sum(counts)


def flags(
    person: Engineer, *, threshold: float, repo_size: float, repo_revert_rate: float
) -> tuple[str, ...]:
    """Goodhart guards for one engineer: only high-throughput engineers are checked, because
    that is where a target on throughput would be gamed."""
    if person.prs_per_week < threshold:
        return ()
    checks = (
        ("small PRs", person.median_size < SPLIT_FACTOR * repo_size),
        ("reverts above repo", person.revert_rate > repo_revert_rate),
        ("light review", person.light_review_share >= LIGHT_REVIEW),
    )
    return tuple(label for label, hit in checks if hit)


def _sort_key(kpi: Kpi) -> Callable[[Engineer], tuple]:
    def key(e: Engineer) -> tuple:
        value = getattr(e, kpi.key)
        missing = value is None
        signed = 0.0 if missing else (-value if kpi.higher_first else value)
        return (missing, signed, -e.merged_prs, e.name)

    return key


def ranks(people: Sequence[Engineer], kpi: Kpi) -> dict[str, int]:
    """Competition ranks (1, 2, 2, 4) on one KPI; engineers with no value get no rank."""
    ordered = sorted((e for e in people if getattr(e, kpi.key) is not None), key=_sort_key(kpi))
    result: dict[str, int] = {}
    for i, e in enumerate(ordered):
        prev = ordered[i - 1] if i else None
        same = prev is not None and getattr(prev, kpi.key) == getattr(e, kpi.key)
        result[e.name] = result[prev.name] if same else i + 1
    return result


@dataclass(frozen=True, slots=True)
class Ranking:
    """The engineers of one window with at least ``min_prs`` merged PRs, sorted on one KPI."""

    window: Window
    sort: Kpi
    min_prs: int
    active: int  # engineers with at least one merged PR
    people: tuple[Engineer, ...]  # eligible engineers, best first on ``sort``, flags set
    ranks: dict[str, dict[str, int]]  # KPI key -> name -> rank among eligible engineers
    threshold: float  # PRs per week from which guardrails apply
    repo_size: float
    repo_revert_rate: float
    spread: tuple[float, ...]  # PERCENTILES of PRs per week over all active engineers
    core_spread: tuple[float, ...]  # the same over eligible engineers
    top_share: float  # share of merged PRs by the top TOP_SHARE of active engineers

    @property
    def flagged(self) -> tuple[Engineer, ...]:
        return tuple(e for e in self.people if e.flags)


def ranking(
    prs: Iterable[PullRequest],
    window: Window,
    *,
    sort: str = "prs_per_week",
    min_prs: int = 5,
    reveal: bool = False,
) -> Ranking:
    items = tuple(prs)
    everyone = engineers(items, window, reveal)
    eligible = tuple(e for e in everyone if e.merged_prs >= min_prs)
    card = metrics.scorecard(items, len(everyone), window)
    threshold = percentiles([e.prs_per_week for e in eligible], (int(HIGH_THROUGHPUT * 100),))[0]
    marked = tuple(
        replace(
            e,
            flags=flags(
                e,
                threshold=threshold,
                repo_size=card.median_pr_size_lines,
                repo_revert_rate=card.change_failure_rate,
            ),
        )
        for e in eligible
    )
    kpi = KPI_BY_KEY[sort]
    return Ranking(
        window=window,
        sort=kpi,
        min_prs=min_prs,
        active=len(everyone),
        people=tuple(sorted(marked, key=_sort_key(kpi))),
        ranks={k.key: ranks(marked, k) for k in KPIS},
        threshold=threshold,
        repo_size=card.median_pr_size_lines,
        repo_revert_rate=card.change_failure_rate,
        spread=percentiles([e.prs_per_week for e in everyone]),
        core_spread=percentiles([e.prs_per_week for e in eligible]),
        top_share=concentration(everyone),
    )


@dataclass(frozen=True, slots=True)
class Pair:
    """One engineer active in both windows."""

    name: str
    before: float  # PRs per week in the baseline window
    after: float  # PRs per week in the current window
    after_ai_share: float

    @property
    def adopter(self) -> bool:
        return self.after_ai_share >= ADOPTER_SHARE

    @property
    def change(self) -> float:
        return self.after / self.before - 1


@dataclass(frozen=True, slots=True)
class Group:
    label: str
    pairs: tuple[Pair, ...]

    @property
    def n(self) -> int:
        return len(self.pairs)

    def _median(self, f: Callable[[Pair], float]) -> float:
        return float(median(map(f, self.pairs))) if self.pairs else 0.0

    @property
    def median_before(self) -> float:
        return self._median(lambda p: p.before)

    @property
    def median_after(self) -> float:
        return self._median(lambda p: p.after)

    @property
    def median_change(self) -> float:
        return self._median(lambda p: p.change)

    @property
    def pooled_change(self) -> float:
        before = sum(p.before for p in self.pairs)
        return sum(p.after for p in self.pairs) / before - 1 if before else 0.0

    @property
    def improved(self) -> float:
        return sum(p.after > p.before for p in self.pairs) / self.n if self.pairs else 0.0


@dataclass(frozen=True, slots=True)
class Paired:
    """Engineers with at least ``min_prs`` merged PRs in both windows, split by AI adoption in the
    current window, with a bootstrap interval on the gap between the two median changes."""

    min_prs: int
    adopters: Group
    others: Group
    gap: float  # adopters' median change minus the others'
    interval: tuple[float, float]  # 90% bootstrap interval of ``gap``


def _bootstrap(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    if not a or not b:
        return (0.0, 0.0)
    rng = random.Random(SEED)
    gaps = sorted(
        median(rng.choices(a, k=len(a))) - median(rng.choices(b, k=len(b)))
        for _ in range(BOOTSTRAP)
    )
    return (gaps[int(0.05 * BOOTSTRAP)], gaps[int(0.95 * BOOTSTRAP) - 1])


def paired(
    prs: Iterable[PullRequest], before: Window, after: Window, *, min_prs: int = 1
) -> Paired:
    items = tuple(prs)
    then = {e.name: e for e in engineers(items, before) if e.merged_prs >= min_prs}
    now = {e.name: e for e in engineers(items, after) if e.merged_prs >= min_prs}
    pairs = tuple(
        Pair(name, then[name].prs_per_week, now[name].prs_per_week, now[name].ai_share)
        for name in sorted(then.keys() & now.keys())
    )
    adopters = Group(
        f"AI adopters (≥{ADOPTER_SHARE:.0%} AI-assisted)", tuple(p for p in pairs if p.adopter)
    )
    others = Group("Others", tuple(p for p in pairs if not p.adopter))
    return Paired(
        min_prs=min_prs,
        adopters=adopters,
        others=others,
        gap=adopters.median_change - others.median_change,
        interval=_bootstrap([p.change for p in adopters.pairs], [p.change for p in others.pairs]),
    )


GUARDS = (
    ("small PRs", f"median PR size under {SPLIT_FACTOR:.0%} of the repository median (splitting?)"),
    ("reverts above repo", "revert rate above the repository's"),
    (
        "light review",
        f"at least {LIGHT_REVIEW:.0%} of PRs merged with no human review, or with the first one "
        f"within {FAST_REVIEW_H * 60:.0f} minutes of opening",
    ),
)
CAVEATS = (
    "Individual numbers are for distribution, coaching and self-reflection, not for "
    "leaderboards or performance ratings (ADR 0005). Names are stable pseudonyms.",
    "PRs per week counts the whole window: part-time, new, leaving or on-leave engineers look "
    "slower, and PR count says nothing about difficulty or about review, design and mentoring "
    "work.",
    "Agent PRs are credited to the human assignee, so delegating to an agent raises that "
    "engineer's count by design; GitHub records the agent as author, so their own review can "
    "count as the first human review.",
    "The paired comparison is observational: adopters chose to adopt, are defined on the current "
    "window, and differ in role and area; small groups and regression to the mean make medians "
    "move.",
)


def _cell(person: Engineer, kpi: Kpi, ranked: dict[str, dict[str, int]]) -> str:
    value = getattr(person, kpi.key)
    if value is None:
        return "-"
    rank = ranked[kpi.key].get(person.name)
    return f"{kpi.fmt.format(value)} (#{rank})"


def _pct(points: Sequence[float]) -> str:
    return " | ".join(f"{v:.2f}" for v in points)


def to_markdown(
    label: str, board: Ranking, pairs: Sequence[Paired], *, top: int = 15, revealed: bool = False
) -> str:
    """The ranking of one window, the spread of output and the paired adopter comparison."""
    kpis = KPIS
    head = "| # | Engineer | Merged PRs | " + " | ".join(k.label for k in kpis) + " | Flags |"
    rule = "|" + "---|" * (len(kpis) + 4)
    rows = "\n".join(
        f"| {i} | {e.name} | {e.merged_prs} | "
        + " | ".join(_cell(e, k, board.ranks) for k in kpis)
        + f" | {', '.join(e.flags) or '-'} |"
        for i, e in enumerate(board.people[:top], 1)
    )
    points = " | ".join(f"p{p}" for p in PERCENTILES)
    flagged = board.flagged
    guard_lines = "\n".join(f"- **{name}**: {rule_}" for name, rule_ in GUARDS)
    pair_rows = "\n".join(
        f"| ≥ {p.min_prs} | {g.label} | {g.n} | {g.median_before:.2f} → {g.median_after:.2f} "
        f"| {g.median_change:+.0%} | {g.pooled_change:+.0%} | {g.improved:.0%} |"
        for p in pairs
        for g in (p.adopters, p.others)
    )
    gaps = "\n".join(
        f"- At least {p.min_prs} merged PR{'s' if p.min_prs > 1 else ''} in each window: "
        f"adopters' median change minus the others' = **{p.gap:+.0%}** "
        f"(90% bootstrap interval {p.interval[0]:+.0%} to {p.interval[1]:+.0%})."
        for p in pairs
    )
    notice = (
        "> **Real GitHub logins shown (`--logins`). Local use only: do not publish or share.**\n\n"
        if revealed
        else ""
    )
    return (
        f"""### Engineers, {label}

{notice}{board.active} engineers merged at least one PR; {len(board.people)} merged at least \
{board.min_prs} and are ranked, sorted by **{board.sort.label}**. Each cell is the value and \
its rank among ranked engineers (rank 1 is the highest for PRs and AI share, the lowest \
otherwise). Quality-adjusted excludes reverted PRs.

{head}
{rule}
{rows}

#### Guardrails

Checked for engineers at or above the p{int(HIGH_THROUGHPUT * 100)} of ranked throughput \
({board.threshold:.2f} PRs per week), the place where a throughput target would be gamed. \
{len(flagged)} flagged. A flag is a prompt for a conversation, not a verdict.

{guard_lines}

#### Spread of per-engineer output (PRs per week)

| Engineers | {points} | Top {TOP_SHARE:.0%} merge |
|---|{"---|" * len(PERCENTILES)}---|
| All {board.active} active | {_pct(board.spread)} | {board.top_share:.0%} of PRs |
| {len(board.people)} ranked (≥ {board.min_prs} PRs) | {_pct(board.core_spread)} | |

#### AI adopters vs others, same engineers in both windows

Adopters have at least {ADOPTER_SHARE:.0%} AI-assisted merged PRs in the current window. Change \
is each engineer's PRs per week, current over baseline; pooled sums the group first.

| Min PRs each | Group | Engineers | Median PRs / week | Median change | Pooled | Improved |
|---|---|---|---|---|---|---|
{pair_rows}

{gaps}

#### Read with care

"""
        + "\n".join(f"- {c}" for c in CAVEATS)
        + "\n"
    )


ROLES = ("reference", "baseline", "current")


def view_pairs(view: real.RealView, min_prs: int) -> tuple[Paired, ...]:
    """Baseline vs current, for every engineer active in both and for the ranked ones."""
    before, after = view.baseline.window, view.current.window
    return tuple(paired(view.prs, before, after, min_prs=k) for k in sorted({1, min_prs}))


def view_markdown(
    view: real.RealView,
    *,
    role: str = "current",
    sort: str = "prs_per_week",
    top: int = 15,
    min_prs: int = 5,
    reveal: bool = False,
) -> str:
    period = next((p for p in view.periods if p.role == role), None)
    if period is None:
        raise ValueError(f"{view.snapshot.repo} has no {role} window")
    board = ranking(view.prs, period.window, sort=sort, min_prs=min_prs, reveal=reveal)
    label = f"{view.snapshot.repo}, {period.label} ({role})"
    return to_markdown(label, board, view_pairs(view, min_prs), top=top, revealed=reveal)
