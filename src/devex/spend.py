"""Token economics: what AI spend buys, measured per accepted outcome rather than per token.

Pure functions over costed usage events (see ``devex.usage``). The headline unit is the cost
per accepted change, a merged PR that was not reverted, because a cheaper token that produces
a reverted change is not a saving. Spend on reverted and abandoned work is reported as waste,
and the team guards reuse the scorecard's cost-versus-quality check.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median, quantiles

from devex import routing
from devex.metrics import cost_quality_signal
from devex.usage import Costed, PriceTable, UsageEvent, event_cost, price_on

__all__ = [
    "BY",
    "FRONTIER",
    "Anomaly",
    "Budget",
    "Change",
    "Outcomes",
    "Period",
    "Routable",
    "Slice",
    "SpendReport",
    "Spender",
    "Task",
    "TaskTypeCost",
    "TeamGuard",
    "analyze",
    "anomalies",
    "breakdown",
    "budget_forecast",
    "cache_ratio",
    "changes",
    "engineer_minutes",
    "frontier_share",
    "outcomes",
    "per_task_type",
    "period",
    "quarter",
    "routing_opportunity",
    "tasks",
    "team_guards",
    "to_markdown",
    "top_spenders",
    "weekly",
]

FRONTIER = "frontier-large"
BY: Mapping[str, Callable[[Costed], str]] = {
    "model": lambda c: c.event.model,
    "team": lambda c: c.event.team,
    "surface": lambda c: c.event.surface,
    "task": lambda c: c.event.task_type,
    "tier": lambda c: c.tier,
    "engineer": lambda c: c.event.engineer,
}


def _div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _p90(values: Sequence[float]) -> float:
    if len(values) < 2:
        return float(values[0]) if values else 0.0
    return quantiles(values, n=10, method="inclusive")[8]


def _usd(items: Iterable[Costed]) -> float:
    return sum(c.usd for c in items)


@dataclass(frozen=True, slots=True)
class Slice:
    key: str
    usd: float
    tokens: int
    events: int
    usd_share: float
    token_share: float


def breakdown(costed: Sequence[Costed], by: str = "model") -> tuple[Slice, ...]:
    """Spend and tokens per model, team, surface, task type, tier or engineer, largest first.
    Comparing the two shares is the model mix: a frontier model is a small share of tokens and
    a large share of dollars."""
    if by not in BY:
        raise ValueError(f"cannot break spend down by {by!r}; use one of {', '.join(BY)}")
    key = BY[by]
    groups: dict[str, list[Costed]] = defaultdict(list)
    for item in costed:
        groups[key(item)].append(item)
    total_usd = _usd(costed)
    total_tokens = sum(c.event.tokens for c in costed)
    slices = (
        Slice(
            name,
            _usd(items),
            sum(c.event.tokens for c in items),
            len(items),
            _div(_usd(items), total_usd),
            _div(sum(c.event.tokens for c in items), total_tokens),
        )
        for name, items in groups.items()
    )
    return tuple(sorted(slices, key=lambda s: (-s.usd, s.key)))


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    task_type: str
    team: str
    engineer: str
    start: datetime
    usd: float
    tokens: int
    steps: int
    outcome: str | None
    model: str = ""


# Later outcomes win: a merged change that is reverted is a reverted change.
_PRECEDENCE = ("abandoned", "resolved", "merged", "reverted")


def _outcome(events: Iterable[UsageEvent]) -> str | None:
    seen = {e.outcome for e in events if e.outcome}
    return max(seen, key=_PRECEDENCE.index, default=None)


def _group(costed: Iterable[Costed], key: Callable[[UsageEvent], str | None]) -> dict:
    groups: dict[str, list[Costed]] = defaultdict(list)
    for item in costed:
        name = key(item.event)
        if name:
            groups[name].append(item)
    return groups


def tasks(costed: Iterable[Costed]) -> tuple[Task, ...]:
    """Events sharing a task_id; aggregated events without one (completions) are not tasks."""

    def build(task_id: str, items: list[Costed]) -> Task:
        first = min(items, key=lambda c: c.event.timestamp).event
        return Task(
            task_id,
            first.task_type,
            first.team,
            first.engineer,
            first.timestamp,
            _usd(items),
            sum(c.event.tokens for c in items),
            len(items),
            _outcome(c.event for c in items),
            breakdown(items, "model")[0].key,
        )

    groups = _group(costed, lambda e: e.task_id)
    return tuple(build(name, items) for name, items in sorted(groups.items()))


@dataclass(frozen=True, slots=True)
class TaskTypeCost:
    task_type: str
    tasks: int
    usd: float
    tokens: int
    usd_per_task: float
    tokens_per_task: float
    median_usd: float
    p90_usd: float


def per_task_type(items: Sequence[Task]) -> tuple[TaskTypeCost, ...]:
    """Tokens and dollars per PR authoring task, review, incident, agent session and chat."""
    groups: dict[str, list[Task]] = defaultdict(list)
    for task in items:
        groups[task.task_type].append(task)
    return tuple(
        TaskTypeCost(
            name,
            len(group),
            sum(t.usd for t in group),
            sum(t.tokens for t in group),
            _div(sum(t.usd for t in group), len(group)),
            _div(sum(t.tokens for t in group), len(group)),
            median(t.usd for t in group),
            _p90([t.usd for t in group]),
        )
        for name, group in sorted(groups.items())
    )


@dataclass(frozen=True, slots=True)
class Change:
    """Every event linked to one PR: authoring chat, agent steps, AI review and CI."""

    key: str
    team: str
    engineer: str
    start: datetime
    usd: float
    outcome: str | None

    @property
    def accepted(self) -> bool:
        return self.outcome == "merged"

    @property
    def concluded(self) -> bool:
        return self.outcome in ("merged", "reverted", "abandoned")


def change_key(event: UsageEvent) -> str | None:
    if event.pr is not None:
        return f"{event.repo}#{event.pr}"
    if event.task_type in ("pr", "agent_session") and event.task_id:
        return event.task_id
    return None


def changes(costed: Iterable[Costed]) -> tuple[Change, ...]:
    def build(key: str, items: list[Costed]) -> Change:
        first = min(items, key=lambda c: c.event.timestamp).event
        outcome = _outcome(c.event for c in items)
        return Change(key, first.team, first.engineer, first.timestamp, _usd(items), outcome)

    return tuple(build(k, items) for k, items in sorted(_group(costed, change_key).items()))


@dataclass(frozen=True, slots=True)
class Outcomes:
    total_usd: float
    change_usd: float
    accepted: int
    reverted: int
    abandoned: int
    in_flight: int
    in_flight_usd: float
    cost_per_accepted_change: float
    all_in_cost_per_accepted_change: float
    incident_usd: float
    incidents: int
    incidents_resolved: int
    cost_per_resolved_incident: float
    waste_reverted_usd: float
    waste_abandoned_usd: float

    @property
    def waste_usd(self) -> float:
        return self.waste_reverted_usd + self.waste_abandoned_usd

    @property
    def waste_share(self) -> float:
        return _div(self.waste_usd, self.total_usd)

    @property
    def revert_rate(self) -> float:
        return _div(self.reverted, self.accepted + self.reverted)


def outcomes(costed: Sequence[Costed]) -> Outcomes:
    """Cost per accepted change = spend on concluded changes / merged-and-not-reverted changes.
    Changes with no outcome yet are in flight and left out of both sides; the all-in variant
    divides every dollar not spent on incidents (completions and chat included) instead."""
    found = changes(costed)
    concluded = tuple(c for c in found if c.concluded)
    flight = tuple(c for c in found if not c.concluded)
    accepted = sum(c.accepted for c in concluded)
    incident_tasks = tuple(t for t in tasks(costed) if t.task_type == "incident")
    incident_usd = sum(t.usd for t in incident_tasks)
    resolved = sum(t.outcome == "resolved" for t in incident_tasks)
    total = _usd(costed)

    def spent(outcome: str) -> float:
        return sum(c.usd for c in concluded if c.outcome == outcome)

    return Outcomes(
        total_usd=total,
        change_usd=sum(c.usd for c in concluded),
        accepted=accepted,
        reverted=sum(c.outcome == "reverted" for c in concluded),
        abandoned=sum(c.outcome == "abandoned" for c in concluded),
        in_flight=len(flight),
        in_flight_usd=sum(c.usd for c in flight),
        cost_per_accepted_change=_div(sum(c.usd for c in concluded), accepted),
        all_in_cost_per_accepted_change=_div(total - incident_usd, accepted),
        incident_usd=incident_usd,
        incidents=len(incident_tasks),
        incidents_resolved=resolved,
        cost_per_resolved_incident=_div(incident_usd, resolved),
        waste_reverted_usd=spent("reverted"),
        waste_abandoned_usd=spent("abandoned"),
    )


def cache_ratio(costed: Iterable[Costed]) -> float:
    """Share of input tokens served from the prompt cache (billed at the cached price)."""
    items = tuple(costed)
    return _div(
        sum(c.event.cached_input_tokens for c in items), sum(c.event.input_tokens for c in items)
    )


def frontier_share(costed: Iterable[Costed]) -> tuple[float, float]:
    """(share of dollars, share of tokens) spent on frontier-tier models."""
    items = tuple(costed)
    top = tuple(c for c in items if c.tier == FRONTIER)
    return (
        _div(_usd(top), _usd(items)),
        _div(sum(c.event.tokens for c in top), sum(c.event.tokens for c in items)),
    )


@dataclass(frozen=True, slots=True)
class Routable:
    frontier_usd: float
    classified_usd: float
    routable_usd: float
    savings_usd: float
    by_task_type: Mapping[str, float]

    @property
    def unclassified_usd(self) -> float:
        return self.frontier_usd - self.classified_usd


def _reprice(event: UsageEvent, tier: str, table: PriceTable) -> float | None:
    day = event.timestamp.date()
    models = {p.model for p in table.prices if p.tier == tier}
    options = (
        event_cost(event, price)
        for model in sorted(models)
        for price in (price_on(table, model, day),)
        if price is not None
    )
    return min(options, default=None)


def routing_opportunity(
    costed: Iterable[Costed],
    table: PriceTable,
    *,
    catalog: Sequence[routing.Model] = routing.DEFAULT_CATALOG,
    min_quality: float = 0.9,
) -> Routable:
    """Frontier spend on calls whose task class the routing evals say a cheaper tier passes.
    Savings reprice those calls at the cheapest model of the eval-chosen tier on the same day.
    Calls without a task class (no classifier at the gateway) cannot be judged and are shown
    as unclassified rather than counted either way."""
    top = tuple(c for c in costed if c.tier == FRONTIER)
    classified = tuple(c for c in top if c.event.task_class)

    def target(c: Costed) -> str:
        request = routing.Request("", c.event.input_tokens, c.event.output_tokens)
        return routing.route(
            routing.TaskClass(c.event.task_class), catalog, min_quality, request
        ).name

    moves = tuple(
        (c, cheaper)
        for c in classified
        for tier in (target(c),)
        if tier != FRONTIER
        for cheaper in (_reprice(c.event, tier, table),)
        if cheaper is not None
    )
    by_type: dict[str, float] = defaultdict(float)
    for c, cheaper in moves:
        by_type[c.event.task_type] += max(c.usd - cheaper, 0.0)
    return Routable(
        frontier_usd=_usd(top),
        classified_usd=_usd(classified),
        routable_usd=sum(c.usd for c, _ in moves),
        savings_usd=sum(max(c.usd - cheaper, 0.0) for c, cheaper in moves),
        by_task_type=dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
    )


def engineer_minutes(usd: float, hourly_cost: float) -> float:
    """AI dollars expressed as minutes of a loaded engineer-hour; the rate is an assumption."""
    return _div(usd, hourly_cost) * 60


def quarter(day: date) -> tuple[date, date]:
    """Calendar quarter containing ``day`` as [start, end)."""
    first = 3 * ((day.month - 1) // 3) + 1
    start = date(day.year, first, 1)
    end = date(day.year + (first + 3 > 12), (first + 2) % 12 + 1, 1)
    return start, end


@dataclass(frozen=True, slots=True)
class Budget:
    budget_usd: float
    start: date
    end: date
    as_of: date
    spent_usd: float
    daily_run_rate: float
    projected_usd: float
    exhausted_on: date | None

    @property
    def burn(self) -> float:
        return _div(self.spent_usd, self.budget_usd)

    @property
    def elapsed(self) -> float:
        return _div((self.as_of - self.start).days + 1, (self.end - self.start).days)

    @property
    def projected_share(self) -> float:
        return _div(self.projected_usd, self.budget_usd)


def budget_forecast(
    costed: Sequence[Costed],
    budget_usd: float,
    *,
    as_of: date | None = None,
    trailing_days: int = 28,
) -> Budget:
    """Quarter-to-date burn and a linear forecast: spend so far plus the trailing daily run rate
    for every remaining day of the quarter. Linear on purpose: easy to explain, easy to check."""
    today = as_of or max(c.event.timestamp.date() for c in costed)
    start, end = quarter(today)
    daily: dict[date, float] = defaultdict(float)
    for c in costed:
        daily[c.event.timestamp.date()] += c.usd
    to_date = sorted((d, v) for d, v in daily.items() if start <= d <= today)
    spent = sum(v for _, v in to_date)
    window_start = today - timedelta(days=trailing_days - 1)
    rate = sum(v for d, v in to_date if d >= window_start) / trailing_days
    remaining = (end - today).days - 1
    running = 0.0
    crossed = None
    for day, value in to_date:
        running += value
        if crossed is None and running > budget_usd:
            crossed = day
    if crossed is None and rate > 0 and spent + rate * remaining > budget_usd:
        crossed = today + timedelta(days=math.ceil((budget_usd - spent) / rate))
    return Budget(budget_usd, start, end, today, spent, rate, spent + rate * remaining, crossed)


@dataclass(frozen=True, slots=True)
class Anomaly:
    kind: str  # "cost" (far above the team's p90 for the task type) or "runaway" (agent loop)
    task: Task
    threshold: float


def anomalies(
    items: Sequence[Task],
    *,
    factor: float = 3.0,
    min_group: int = 10,
    runaway_factor: float = 4.0,
    runaway_min_steps: int = 25,
) -> tuple[Anomaly, ...]:
    """Agent sessions with ``runaway_factor`` x the median number of steps (and at least the
    minimum), then tasks costing more than ``factor`` x the p90 of the same team, task type and
    main model: like is compared with like, so a deliberate model change does not make every
    task on one side of it look anomalous."""

    def group(task: Task) -> tuple[str, str, str]:
        return task.team, task.task_type, task.model

    groups: dict[tuple[str, str, str], list[Task]] = defaultdict(list)
    for task in items:
        groups[group(task)].append(task)
    limits = {
        key: factor * _p90([t.usd for t in members])
        for key, members in groups.items()
        if len(members) >= min_group
    }
    sessions = tuple(t for t in items if t.task_type == "agent_session")
    typical = median(t.steps for t in sessions) if sessions else 0
    step_limit = max(runaway_min_steps, runaway_factor * typical)
    runaway = tuple(Anomaly("runaway", t, step_limit) for t in sessions if t.steps >= step_limit)
    looped = {a.task.task_id for a in runaway}
    costly = tuple(
        Anomaly("cost", t, limits[group(t)])
        for t in items
        if group(t) in limits and t.usd > limits[group(t)] and t.task_id not in looped
    )
    return tuple(sorted((*runaway, *costly), key=lambda a: (a.kind != "runaway", -a.task.usd)))


@dataclass(frozen=True, slots=True)
class Spender:
    engineer: str
    team: str
    usd: float
    share: float
    accepted: int
    cost_per_accepted_change: float


def top_spenders(costed: Sequence[Costed], n: int = 10) -> tuple[Spender, ...]:
    """Pseudonymised: for coaching and anomaly follow-up, never a leaderboard (ADR 0005)."""
    total = _usd(costed)
    accepted: dict[str, int] = defaultdict(int)
    for change in changes(costed):
        accepted[change.engineer] += change.accepted
    groups = _group(costed, lambda e: e.engineer)
    spenders = (
        Spender(
            name,
            items[0].event.team,
            _usd(items),
            _div(_usd(items), total),
            accepted[name],
            _div(_usd(items), accepted[name]),
        )
        for name, items in groups.items()
    )
    return tuple(sorted(spenders, key=lambda s: (-s.usd, s.engineer)))[:n]


def weekly(
    costed: Sequence[Costed], by: str = "tier"
) -> tuple[tuple[date, ...], dict[str, tuple[float, ...]]]:
    """Spend per ISO week (Monday start) per key, for the spend-over-time chart."""
    key = BY[by]

    def monday(moment: datetime) -> date:
        return moment.date() - timedelta(days=moment.weekday())

    weeks = tuple(sorted({monday(c.event.timestamp) for c in costed}))
    index = {w: i for i, w in enumerate(weeks)}
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(weeks))
    for c in costed:
        totals[key(c)][index[monday(c.event.timestamp)]] += c.usd
    return weeks, {name: tuple(values) for name, values in sorted(totals.items())}


@dataclass(frozen=True, slots=True)
class Period:
    label: str
    usd: float
    days: int
    outcomes: Outcomes
    frontier_usd_share: float
    cache_ratio: float
    routing: Routable | None = None

    @property
    def usd_per_day(self) -> float:
        return _div(self.usd, self.days)


def _change_start(costed: Sequence[Costed]) -> Callable[[Costed], datetime]:
    starts = {c.key: c.start for c in changes(costed)}

    def when(item: Costed) -> datetime:
        key = change_key(item.event)
        return starts[key] if key else item.event.timestamp

    return when


def period(
    costed: Sequence[Costed],
    label: str,
    start: date,
    end: date,
    table: PriceTable | None = None,
) -> Period:
    """Events in [start, end). Events linked to a change follow the change's first event, so
    a PR that straddles the boundary is costed whole in the period it started. With a price
    table, the period's remaining routing opportunity is included."""
    when = _change_start(costed)
    inside = tuple(c for c in costed if start <= when(c).date() < end)
    days = (end - start).days
    return Period(
        label,
        _usd(inside),
        days,
        outcomes(inside),
        frontier_share(inside)[0],
        cache_ratio(inside),
        routing_opportunity(inside, table) if table else None,
    )


@dataclass(frozen=True, slots=True)
class TeamGuard:
    team: str
    before: Outcomes
    after: Outcomes
    signal: str | None


def team_guards(
    costed: Sequence[Costed],
    split: date,
    *,
    max_cfr_increase_pp: float = 0.02,
) -> tuple[TeamGuard, ...]:
    """Per team: did cost per accepted change fall while the revert rate rose? Uses the same
    check as the scorecard Goodhart guards (``metrics.cost_quality_signal``), with a wider bar
    (2 points rather than 1) because a team is a much smaller sample than the org."""
    when = _change_start(costed)
    names = sorted({c.event.team for c in costed})

    def side(team: str, after: bool) -> Outcomes:
        return outcomes(
            tuple(c for c in costed if c.event.team == team and (when(c).date() >= split) is after)
        )

    def guard(team: str) -> TeamGuard:
        before, after = side(team, False), side(team, True)
        signal = cost_quality_signal(
            before.cost_per_accepted_change,
            after.cost_per_accepted_change,
            before.revert_rate,
            after.revert_rate,
            max_cfr_increase_pp=max_cfr_increase_pp,
        )
        return TeamGuard(team, before, after, signal)

    return tuple(map(guard, names))


@dataclass(frozen=True, slots=True)
class SpendReport:
    events: int
    first: date
    last: date
    by: str
    breakdown: tuple[Slice, ...]
    mix: tuple[Slice, ...]
    outcomes: Outcomes
    task_types: tuple[TaskTypeCost, ...]
    cache_ratio: float
    frontier_usd_share: float
    frontier_token_share: float
    routing: Routable
    hourly_cost: float
    budget: Budget | None
    anomalies: tuple[Anomaly, ...]
    spenders: tuple[Spender, ...]
    periods: tuple[Period, ...]
    guards: tuple[TeamGuard, ...]
    org_signal: str | None
    note: str
    weeks: tuple[date, ...] = ()
    weekly_by_tier: Mapping[str, tuple[float, ...]] | None = None


def analyze(
    costed: Sequence[Costed],
    table: PriceTable,
    *,
    by: str = "model",
    budget_usd: float | None = None,
    split: date | None = None,
    hourly_cost: float = 100.0,
    top: int = 10,
) -> SpendReport:
    first = min(c.event.timestamp.date() for c in costed)
    last = max(c.event.timestamp.date() for c in costed)
    periods = (
        (
            period(costed, f"before {split.isoformat()}", first, split, table),
            period(costed, f"from {split.isoformat()}", split, last + timedelta(days=1), table),
        )
        if split
        else ()
    )
    org_signal = (
        cost_quality_signal(
            periods[0].outcomes.cost_per_accepted_change,
            periods[1].outcomes.cost_per_accepted_change,
            periods[0].outcomes.revert_rate,
            periods[1].outcomes.revert_rate,
        )
        if periods
        else None
    )
    usd_share, token_share = frontier_share(costed)
    weeks, by_tier = weekly(costed, "tier")
    return SpendReport(
        events=len(costed),
        first=first,
        last=last,
        by=by,
        breakdown=breakdown(costed, by),
        mix=breakdown(costed, "tier"),
        outcomes=outcomes(costed),
        task_types=per_task_type(tasks(costed)),
        cache_ratio=cache_ratio(costed),
        frontier_usd_share=usd_share,
        frontier_token_share=token_share,
        routing=routing_opportunity(costed, table),
        hourly_cost=hourly_cost,
        budget=budget_forecast(costed, budget_usd) if budget_usd else None,
        anomalies=anomalies(tasks(costed)),
        spenders=top_spenders(costed, top),
        periods=periods,
        guards=team_guards(costed, split) if split else (),
        org_signal=org_signal,
        note=table.note,
        weeks=weeks,
        weekly_by_tier=by_tier,
    )


def _money(value: float) -> str:
    return f"${value:,.2f}" if abs(value) < 100 else f"${value:,.0f}"


def _pct(value: float) -> str:
    return f"{value:.1%}"


def to_markdown(report: SpendReport) -> str:
    o = report.outcomes
    r = report.routing
    minutes = engineer_minutes(o.cost_per_accepted_change, report.hourly_cost)
    lines = [
        f"## Token economics, {report.first} to {report.last} ({report.events:,} usage events)",
        "",
        "| Headline | Value |",
        "|---|---:|",
        f"| **Cost per accepted change** (merged, not reverted) | **{_money(o.cost_per_accepted_change)}** |",  # noqa: E501
        f"| All-in cost per accepted change (chat and completions included) | {_money(o.all_in_cost_per_accepted_change)} |",  # noqa: E501
        f"| Accepted changes / reverted / abandoned / in flight | {o.accepted} / {o.reverted} / {o.abandoned} / {o.in_flight} |",  # noqa: E501
        f"| Total spend | {_money(o.total_usd)} |",
        f"| Waste: reverted + abandoned work | {_money(o.waste_usd)} ({_pct(o.waste_share)} of spend; reverted {_money(o.waste_reverted_usd)}, abandoned {_money(o.waste_abandoned_usd)}) |",  # noqa: E501
        f"| Cost per resolved incident | {_money(o.cost_per_resolved_incident)} ({o.incidents_resolved}/{o.incidents} resolved) |",  # noqa: E501
        f"| Prompt-cache hit ratio (input tokens) | {_pct(report.cache_ratio)} |",
        f"| Frontier-tier share of $ / of tokens | {_pct(report.frontier_usd_share)} / {_pct(report.frontier_token_share)} |",  # noqa: E501
        f"| Routing opportunity (frontier $ a cheaper tier passes evals for) | {_money(r.routable_usd)} routable, {_money(r.savings_usd)} saving; {_money(r.unclassified_usd)} unclassified |",  # noqa: E501
        f"| Value check: cost per accepted change at ${report.hourly_cost:,.0f}/h loaded (assumption) | {minutes:.1f} engineer-minutes |",  # noqa: E501
        "",
        f"### Spend by {report.by}",
        "",
        "| Key | Spend | Share of $ | Share of tokens | Events |",
        "|---|---:|---:|---:|---:|",
        *(
            f"| {s.key} | {_money(s.usd)} | {_pct(s.usd_share)} | {_pct(s.token_share)} | {s.events:,} |"  # noqa: E501
            for s in report.breakdown
        ),
        "",
        "### Cost per task type",
        "",
        "| Task type | Tasks | Spend | $ per task | Median | p90 | Tokens per task |",
        "|---|---:|---:|---:|---:|---:|---:|",
        *(
            f"| {t.task_type} | {t.tasks:,} | {_money(t.usd)} | {_money(t.usd_per_task)} | {_money(t.median_usd)} | {_money(t.p90_usd)} | {t.tokens_per_task:,.0f} |"  # noqa: E501
            for t in report.task_types
        ),
    ]
    if report.periods:
        lines += [
            "",
            "### Before and after",
            "",
            "| Period | $ per day | Cost per accepted change | Revert rate | Frontier $ share | Cache ratio | Waste share | Routing saving left |",  # noqa: E501
            "|---|---:|---:|---:|---:|---:|---:|---:|",
            *(
                f"| {p.label} | {_money(p.usd_per_day)} | {_money(p.outcomes.cost_per_accepted_change)} | {_pct(p.outcomes.revert_rate)} | {_pct(p.frontier_usd_share)} | {_pct(p.cache_ratio)} | {_pct(p.outcomes.waste_share)} | {_money(p.routing.savings_usd if p.routing else 0.0)} |"  # noqa: E501
                for p in report.periods
            ),
            "",
            "### Guards: cheaper must not mean worse",
            "",
            f"- Org: {report.org_signal or 'quiet. Cost per accepted change did not fall at the expense of the revert rate.'}",  # noqa: E501
            "",
            "| Team | Cost per accepted change | Revert rate | Signal |",
            "|---|---:|---:|---|",
            *(
                f"| {g.team} | {_money(g.before.cost_per_accepted_change)} -> {_money(g.after.cost_per_accepted_change)} | {_pct(g.before.revert_rate)} -> {_pct(g.after.revert_rate)} | {g.signal or 'quiet'} |"  # noqa: E501
                for g in report.guards
            ),
        ]
    if report.budget:
        b = report.budget
        exhausted = f", exhausted on {b.exhausted_on}" if b.exhausted_on else ""
        lines += [
            "",
            f"### Budget, {b.start} to {b.end - timedelta(days=1)}",
            "",
            f"- Spent {_money(b.spent_usd)} of {_money(b.budget_usd)} ({_pct(b.burn)}) with {_pct(b.elapsed)} of the quarter elapsed.",  # noqa: E501
            f"- Linear forecast at the trailing 28-day run rate ({_money(b.daily_run_rate)}/day): {_money(b.projected_usd)} ({_pct(b.projected_share)} of budget){exhausted}.",  # noqa: E501
        ]
    lines += [
        "",
        "### Anomalies",
        "",
        *(
            f"- {a.kind}: `{a.task.task_id}` ({a.task.task_type}, {a.task.team}, {a.task.engineer}) {_money(a.task.usd)} on {a.task.model}, {a.task.steps} calls, outcome {a.task.outcome or 'none'}"  # noqa: E501
            for a in report.anomalies
        ),
        *(() if report.anomalies else ("- none",)),
        "",
        "### Top spenders (pseudonymised; for follow-up, not a leaderboard)",
        "",
        "| Engineer | Team | Spend | Share | Accepted changes | $ per accepted change |",
        "|---|---|---:|---:|---:|---:|",
        *(
            f"| {s.engineer} | {s.team} | {_money(s.usd)} | {_pct(s.share)} | {s.accepted} | {_money(s.cost_per_accepted_change)} |"  # noqa: E501
            for s in report.spenders
        ),
        "",
        f"_Prices: {report.note}_",
    ]
    return "\n".join(lines) + "\n"
