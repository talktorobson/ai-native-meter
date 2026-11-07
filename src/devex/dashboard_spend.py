"""The "Token economics" section: cost per accepted change and what drives it, on the synthetic
usage events; and its blank twin for public repositories, where token spend is not observable.
Deterministic HTML and SVG, built with the shared chart helpers."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from devex import dashboard
from devex.dashboard import (
    BAD,
    BASELINE_COLOR,
    GOOD,
    NEUTRAL,
    PLATFORM_COLOR,
    Bar,
    Segment,
    Series,
    Stack,
)
from devex.spend import Period, SpendReport, engineer_minutes

__all__ = ["DOC_URL", "TIER_COLORS", "blank_section", "section"]

DOC_URL = "https://github.com/talktorobson/ai-native-meter/blob/main/docs/09-token-economics.md"
TIER_COLORS = {"frontier-large": "#7c3aed", "mid-tier": PLATFORM_COLOR, "small-fast": "#0d9488"}


def _money(value: float) -> str:
    return f"${value:,.2f}" if abs(value) < 100 else f"${value:,.0f}"


def _tile(label: str, value: str, meta: str, *, hint: str = "", color: str = "") -> str:
    style = f' style="color:{color}"' if color else ""
    hero = " hero" if hint == "North star" else ""
    return (
        f'<div class="tile{hero}"><div class="hint">{escape(hint)}</div>'
        f'<div class="label">{escape(label)}</div><div class="value"{style}>{escape(value)}</div>'
        f'<div class="meta">{meta}</div></div>'
    )


def _move(old: float, new: float, direction: int, fmt: str, *, points: bool = False) -> str:
    change = new - old if points else (new - old) / old if old else 0.0
    arrow = "▲" if change > 0 else "▼" if change < 0 else "●"
    shown = f"{change * 100:+.1f} pp" if points else f"{change:+.0%}"
    color = dashboard._delta_color(change, direction)
    return (
        f'<span class="delta" style="color:{color}">{arrow} {shown}</span> '
        f"vs {escape(fmt.format(old))} before"
    )


def _tiles(report: SpendReport) -> str:
    before, after = report.periods
    o = report.outcomes
    b, a = before.outcomes, after.outcomes
    return (
        '<div class="tiles">'
        + _tile(
            "Cost per accepted change",
            _money(a.cost_per_accepted_change),
            _move(b.cost_per_accepted_change, a.cost_per_accepted_change, -1, "${:,.2f}"),
            hint="North star",
        )
        + _tile(
            "Revert rate (the quality it must not cost)",
            f"{a.revert_rate:.1%}",
            _move(b.revert_rate, a.revert_rate, -1, "{:.1%}", points=True),
            hint="Guardrail",
        )
        + _tile(
            "Frontier-model share of $",
            f"{after.frontier_usd_share:.0%}",
            _move(before.frontier_usd_share, after.frontier_usd_share, -1, "{:.0%}", points=True),
            hint="Model mix",
        )
        + _tile(
            "Prompt-cache hit ratio",
            f"{after.cache_ratio:.0%}",
            _move(before.cache_ratio, after.cache_ratio, 1, "{:.0%}", points=True),
            hint="Input tokens",
        )
        + "</div>"
        + '<div class="tiles gap">'
        + _tile(
            "Spend, whole period",
            _money(o.total_usd),
            f"{report.events:,} usage events, {report.first} to {report.last}",
            hint="Total",
        )
        + _tile(
            "Waste: reverted and abandoned work",
            _money(o.waste_usd),
            f"{o.waste_share:.1%} of spend; reverted {_money(o.waste_reverted_usd)}, "
            f"abandoned {_money(o.waste_abandoned_usd)}",
            hint="Waste",
            color=BAD if o.waste_share > 0.1 else "",
        )
        + _tile(
            "Cost per resolved incident",
            _money(o.cost_per_resolved_incident),
            f"{o.incidents_resolved} of {o.incidents} incidents resolved with AI help",
            hint="Operations",
        )
        + _tile(
            "Value check",
            f"{engineer_minutes(o.cost_per_accepted_change, report.hourly_cost):.1f} min",
            f"of a ${report.hourly_cost:,.0f}/h loaded engineer per accepted change "
            "(the rate is an assumption, not a finding)",
            hint="Spend vs value",
        )
        + "</div>"
    )


def _weekly(report: SpendReport, split_label: str) -> str:
    labels = tuple(w.strftime("%b %d").replace(" 0", " ") for w in report.weeks)
    order = sorted(report.weekly_by_tier or {}, key=lambda t: tuple(TIER_COLORS).index(t))
    series = tuple(
        Series(tier, (report.weekly_by_tier or {})[tier], TIER_COLORS[tier]) for tier in order
    )
    chart = dashboard.line_chart(
        series, x_labels=labels, y_format="${:,.0f}", title="Weekly spend by model tier"
    )
    return (
        f'<div class="card"><h3>Weekly spend by model tier ({escape(split_label)})</h3>'
        f'<div class="legend">{dashboard._legend(series, "${:,.0f}")}</div>{chart}'
        f'<p class="small">Weeks start on Monday; the first ({labels[0]}) and last '
        f"({labels[-1]}) are partial. Spikes before the change are runaway agent loops.</p></div>"
    )


def _mix(report: SpendReport) -> str:
    tiers = sorted(report.mix, key=lambda s: tuple(TIER_COLORS).index(s.key))
    stacks = tuple(
        Stack(
            label,
            tuple(Segment(s.key, getattr(s, field), TIER_COLORS[s.key]) for s in tiers),
            "100%",
        )
        for label, field in (("Share of tokens", "token_share"), ("Share of $", "usd_share"))
    )
    legend = "".join(
        f'<span class="key"><i style="background:{TIER_COLORS[s.key]}"></i>{escape(s.key)} '
        f"{s.token_share:.0%} of tokens, {s.usd_share:.0%} of $</span><br>"
        for s in tiers
    )
    return (
        '<div class="card"><h3>Model mix by tier: tokens vs dollars</h3>'
        + dashboard.stacked_bar_chart(stacks, title="Model mix", label_w=130)
        + f'<div class="legend gap">{legend}</div></div>'
    )


def _task_types(report: SpendReport) -> str:
    rows = tuple(sorted(report.task_types, key=lambda t: -t.usd_per_task))
    chart = dashboard.bar_chart(
        tuple(
            Bar(t.task_type, t.usd_per_task, PLATFORM_COLOR, _money(t.usd_per_task)) for t in rows
        ),
        title="Dollars per task",
        label_w=130,
    )
    table = "".join(
        f"<tr><td>{escape(t.task_type)}</td><td>{t.tasks:,}</td><td>{_money(t.median_usd)}</td>"
        f"<td>{_money(t.p90_usd)}</td><td>{t.tokens_per_task:,.0f}</td></tr>"
        for t in rows
    )
    return (
        '<div class="card"><h3>Cost per task type (mean $ per task)</h3>'
        + chart
        + '<table class="compact"><thead><tr><th>Task</th><th>Tasks</th><th>Median</th>'
        f"<th>p90</th><th>Tokens/task</th></tr></thead><tbody>{table}</tbody></table></div>"
    )


def _period_row(p: Period) -> str:
    o = p.outcomes
    saving = _money(p.routing.savings_usd) if p.routing else "-"
    return (
        f'<tr><td class="nm">{escape(p.label)}</td>'
        f"<td>{_money(p.usd_per_day)}</td>"
        f"<td><strong>{_money(o.cost_per_accepted_change)}</strong></td>"
        f"<td>{o.revert_rate:.1%}</td><td>{p.frontier_usd_share:.0%}</td>"
        f"<td>{p.cache_ratio:.0%}</td><td>{o.waste_share:.1%}</td><td>{saving}</td></tr>"
    )


def _periods(report: SpendReport) -> str:
    return (
        '<div class="card"><h3>Before and after the routing change</h3>'
        '<table class="compact"><thead><tr><th>Period</th><th>$/day</th><th>$/accepted</th>'
        "<th>Reverts</th><th>Frontier $</th><th>Cache</th><th>Waste</th>"
        "<th>Routing saving left</th></tr></thead><tbody>"
        + "".join(map(_period_row, report.periods))
        + "</tbody></table>"
        '<p class="small">Routing saving left: frontier spend on calls whose task class a '
        "cheaper tier passes the routing evals for, repriced at that tier.</p></div>"
    )


def _guards(report: SpendReport) -> str:
    org = (
        f'<div class="alert warn">⚠ Org: {escape(report.org_signal)}</div>'
        if report.org_signal
        else '<div class="alert ok">✓ Org: cost per accepted change did not fall at the '
        "expense of the revert rate (bar: +1 pp).</div>"
    )
    rows = "".join(
        f"<tr><td>{escape(g.team)}</td>"
        f"<td>{_money(g.before.cost_per_accepted_change)} → "
        f"{_money(g.after.cost_per_accepted_change)}</td>"
        f"<td>{g.before.revert_rate:.1%} → {g.after.revert_rate:.1%}</td>"
        + (
            f'<td style="color:{BAD};font-weight:600">⚠ cheaper but worse</td>'
            if g.signal
            else f'<td style="color:{GOOD}">✓ quiet</td>'
        )
        + "</tr>"
        for g in report.guards
    )
    return (
        org + '<div class="card gap"><h3>Team guards (bar: +2 pp, teams are small samples)</h3>'
        '<table class="compact"><thead><tr><th>Team</th><th>Cost per accepted change</th>'
        f"<th>Revert rate</th><th>Guard</th></tr></thead><tbody>{rows}</tbody></table></div>"
    )


def _budget(report: SpendReport) -> str:
    b = report.budget
    if b is None:
        return ""
    top = dashboard.nice_max(max(b.budget_usd, b.projected_usd))
    over = b.projected_usd > b.budget_usd
    chart = dashboard.bar_chart(
        (
            Bar("Budget", b.budget_usd, BASELINE_COLOR, _money(b.budget_usd)),
            Bar("Spent to date", b.spent_usd, PLATFORM_COLOR, _money(b.spent_usd)),
            Bar(
                "Forecast at quarter end",
                b.projected_usd,
                BAD if over else GOOD,
                _money(b.projected_usd),
            ),
        ),
        title="Budget burn",
        scale=top,
        label_w=170,
    )
    exhausted = f" Budget exhausted on {b.exhausted_on}." if b.exhausted_on else ""
    return (
        f'<div class="card"><h3>Budget, {b.start} to quarter end</h3>{chart}'
        f'<p class="small">{b.burn:.0%} spent with {b.elapsed:.0%} of the quarter elapsed. '
        f"Linear forecast: spend so far plus the trailing 28-day run rate "
        f"({_money(b.daily_run_rate)}/day) for the days left, "
        f"{b.projected_share:.0%} of budget.{exhausted}</p></div>"
    )


def _anomalies(report: SpendReport) -> str:
    rows = "".join(
        f"<tr><td>{escape(a.kind)}</td><td><code>{escape(a.task.task_id)}</code></td>"
        f"<td>{escape(a.task.team)}</td><td>{escape(a.task.engineer)}</td>"
        f"<td>{_money(a.task.usd)}</td><td>{a.task.steps}</td>"
        f"<td>{escape(a.task.outcome or 'none')}</td></tr>"
        for a in report.anomalies[:8]
    )
    spenders = "".join(
        f"<tr><td>{escape(s.engineer)}</td><td>{escape(s.team)}</td><td>{_money(s.usd)}</td>"
        f"<td>{s.share:.1%}</td><td>{s.accepted}</td>"
        f"<td>{_money(s.cost_per_accepted_change)}</td></tr>"
        for s in report.spenders[:5]
    )
    return (
        '<div class="grid2 gap"><div class="card"><h3>Anomalies: runaway agent loops and '
        "tasks above 3x their team's p90</h3>"
        '<table class="compact"><thead><tr><th>Kind</th><th>Task</th><th>Team</th>'
        "<th>Engineer</th><th>Spend</th><th>Calls</th><th>Outcome</th></tr></thead>"
        f"<tbody>{rows or '<tr><td colspan=7>none</td></tr>'}</tbody></table></div>"
        '<div class="card"><h3>Top spenders (pseudonymised, for follow-up, not a leaderboard)'
        '</h3><table class="compact"><thead><tr><th>Engineer</th><th>Team</th><th>Spend</th>'
        "<th>Share</th><th>Accepted</th><th>$/accepted</th></tr></thead>"
        f"<tbody>{spenders}</tbody></table></div></div>"
    )


def section(report: SpendReport) -> str:
    """Body of the synthetic dashboard's Token economics section; needs a before/after split."""
    if len(report.periods) != 2:
        raise ValueError("the Token economics section needs a before/after split")
    split = report.periods[1].label
    return (
        _tiles(report)
        + '<div class="grid2 gap">'
        + _weekly(report, f"routing change: {split}")
        + _mix(report)
        + "</div>"
        + '<div class="grid2 gap">'
        + _task_types(report)
        + _periods(report)
        + "</div>"
        + _guards(report)
        + '<div class="grid2 gap">'
        + _budget(report)
        + '<div class="card"><h3>Prices</h3>'
        f'<p class="small">{escape(report.note)}</p></div>' + "</div>" + _anomalies(report)
    )


BLANK_TILES: Sequence[tuple[str, str]] = (
    ("North star", "Cost per accepted change"),
    ("Model mix", "Spend by model, frontier share"),
    ("Per task", "Cost per PR, review, agent session"),
    ("Waste", "Spend on reverted and abandoned work"),
    ("Input tokens", "Prompt-cache hit ratio"),
    ("Routing", "Frontier spend a cheaper tier passes"),
    ("Budget", "Burn and quarter-end forecast"),
    ("Anomalies", "Runaway agent sessions"),
)


def blank_section() -> str:
    """Public repositories expose PRs, not invoices: the token KPIs stay blank, on purpose."""
    tiles = "".join(
        f'<div class="tile blank"><div class="hint">{escape(hint)}</div>'
        f'<div class="label">{escape(label)}</div><div class="value">n/a</div>'
        f'<div class="meta" style="color:{NEUTRAL}">needs telemetry</div></div>'
        for hint, label in BLANK_TILES
    )
    return (
        f'<div class="tiles">{tiles}</div>'
        '<div class="telemetry">Not observable from public data: a public repository shows '
        "pull requests, not the tokens, models or invoices behind them, so these tiles are "
        "left blank rather than estimated. Inside a company the same KPIs come from usage "
        "telemetry (Copilot usage reports, provider usage and cost exports, an LLM gateway, "
        "or OpenTelemetry GenAI spans) joined to these PRs. "
        f'<a href="{DOC_URL}">How to connect it: Token economics</a>.</div>'
    )
