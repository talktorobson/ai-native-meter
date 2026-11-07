"""Static HTML dashboard for the real-data run: same inline-SVG building blocks as ``dashboard``,
laid out around what public PR data can and cannot show."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from devex import dashboard, real
from devex.dashboard import BASELINE_COLOR, CSS, PLATFORM_COLOR, Bar, Kpi, Series
from devex.github_source import COHORTS
from devex.metrics import Scorecard

__all__ = ["COHORT_COLORS", "KPIS", "ROLE_COLORS", "render"]

ROLE_COLORS = {"reference": "#cbd5e1", "baseline": BASELINE_COLOR, "current": PLATFORM_COLOR}

COHORT_COLORS = dict(
    zip(COHORTS, ("#7c3aed", PLATFORM_COLOR, "#0d9488", BASELINE_COLOR), strict=True)
)

KPIS: tuple[Kpi, ...] = (
    Kpi(
        "Quality-adjusted PRs / engineer / week",
        "quality_adjusted_prs_per_engineer_week",
        "{:.2f}",
        1,
        "North star",
    ),
    Kpi("PRs / engineer / week", "prs_per_engineer_week", "{:.2f}", 1, "Raw throughput"),
    Kpi("Revert rate", "change_failure_rate", "{:.1%}", -1, "Change failure proxy", True),
    Kpi("Median cycle time", "median_cycle_time_hours", "{:.1f} h", -1, "Guardrail"),
    Kpi(
        "Median time to first human review",
        "median_review_latency_hours",
        "{:.1f} h",
        -1,
        "Guardrail",
    ),
    Kpi("Median PR size", "median_pr_size_lines", "{:.0f} lines", 0, "Gaming check"),
    Kpi("AI-assisted share", "ai_assisted_share", "{:.0%}", 0, "Adoption (observable)", True),
    Kpi("Active engineers", "engineers", "{:,}", 0, "Distinct human authors"),
)

EXTRA_CSS = """
.banner.real{background:#dcfce7;color:#166534;border-color:#86efac}
.facts{display:flex;flex-wrap:wrap;gap:8px 22px;font-size:13px;color:#334155;margin:4px 0 0}
.facts b{color:#0f172a}
tr.headline td{background:#eff6ff;font-weight:600}
a{color:#1d4ed8}
ul.limits{margin:0;padding-left:20px;color:#334155}ul.limits li{margin-bottom:6px}
"""


def _trend(view: real.RealView, title: str, field: str, y_format: str) -> str:
    def weekly(period: real.Period) -> tuple[float, ...]:
        cards = dashboard.weekly_scorecards(view.prs, period.card.engineers, period.window)
        return tuple(getattr(c, field) for c in cards)

    series = tuple(
        Series(f"{p.label} ({p.role})", weekly(p), ROLE_COLORS[p.role], dashed=p.role != "current")
        for p in view.periods
    )
    weeks = max(len(s.values) for s in series)
    chart = dashboard.line_chart(
        series, x_labels=tuple(f"W{i + 1}" for i in range(weeks)), y_format=y_format, title=title
    )
    legend = "".join(
        f'<span class="key"><i style="background:{s.color}"></i>{escape(s.label)}</span>'
        for s in series
    )
    return (
        f'<div class="card"><h3>{escape(title)}</h3><div class="legend">{legend}</div>{chart}</div>'
    )


def _cohort_card(cards: dict[str, Scorecard], title: str, value, text, scale=None) -> str:
    bars = tuple(Bar(name, value(c), COHORT_COLORS[name], text(c)) for name, c in cards.items())
    chart = dashboard.bar_chart(bars, title=title, scale=scale, width=380, label_w=130)
    return f'<div class="card"><h3>{escape(title)}</h3>{chart}</div>'


def _cohorts(view: real.RealView) -> str:
    cards = view.cohorts_after
    total = sum(c.merged_prs for c in cards.values()) or 1
    charts = (
        _cohort_card(
            cards,
            "Share of merged PRs",
            lambda c: c.merged_prs / total,
            lambda c: f"{c.merged_prs / total:.0%}",
            1.0,
        ),
        _cohort_card(
            cards,
            "Revert rate (lower is better)",
            lambda c: c.change_failure_rate,
            lambda c: f"{c.change_failure_rate:.1%}",
        ),
        _cohort_card(
            cards,
            "Median cycle time, hours (lower is better)",
            lambda c: c.median_cycle_time_hours,
            lambda c: f"{c.median_cycle_time_hours:.1f}",
        ),
        _cohort_card(
            cards,
            "Median time to first human review, hours",
            lambda c: c.median_review_latency_hours,
            lambda c: f"{c.median_review_latency_hours:.1f}",
        ),
        _cohort_card(
            cards,
            "Median PR size, lines",
            lambda c: c.median_pr_size_lines,
            lambda c: f"{c.median_pr_size_lines:.0f}",
        ),
        _cohort_card(
            cards,
            "Engineers with at least one PR in the cohort",
            lambda c: c.engineers,
            lambda c: f"{c.engineers}",
        ),
    )
    return f'<div class="grid3">{"".join(charts)}</div>{_cohort_table(view)}'


def _cohort_table(view: real.RealView) -> str:
    def rows(label: str, cards: dict[str, Scorecard]) -> str:
        total = sum(c.merged_prs for c in cards.values()) or 1
        return "".join(
            f"<tr><td>{escape(label)}</td><td>"
            f'<span class="key"><i style="background:{COHORT_COLORS[name]}"></i></span>'
            f"{escape(name)}</td><td>{c.merged_prs:,}</td><td>{c.merged_prs / total:.0%}</td>"
            f"<td>{c.engineers}</td><td>{c.change_failure_rate:.1%}</td>"
            f"<td>{c.median_cycle_time_hours:.1f}</td>"
            f"<td>{c.median_review_latency_hours:.1f}</td>"
            f"<td>{c.median_pr_size_lines:.0f}</td></tr>"
            for name, c in cards.items()
        )

    return (
        '<div class="card gap"><table class="compact"><thead><tr><th>Window</th><th>Cohort</th>'
        "<th>Merged PRs</th><th>Share</th><th>Engineers</th><th>Revert rate</th>"
        "<th>Median cycle (h)</th><th>Median first review (h)</th><th>Median size</th></tr>"
        f"</thead><tbody>{''.join(rows(p.label, p.cohorts) for p in view.periods)}"
        "</tbody></table></div>"
    )


def _evidence(view: real.RealView) -> str:
    items: Sequence[tuple[str, int]] = view.evidence_after
    if not items:
        return '<div class="card">No AI evidence found on merged PRs.</div>'
    kinds = {"agent": "#7c3aed", "co-author": PLATFORM_COLOR, "marker": PLATFORM_COLOR}
    bars = tuple(
        Bar(kind, count, kinds.get(kind.partition(":")[0], "#0d9488"), f"{count:,}")
        for kind, count in items
    )
    chart = dashboard.bar_chart(bars, title="AI evidence", width=1100, label_w=220)
    return (
        f'<div class="card"><h3>Merged PRs per kind of evidence, {escape(view.after_label)} '
        f"(a PR can carry several)</h3>{chart}"
        '<p class="small"><b>agent</b>: authored by a coding agent and credited to the human '
        "assignee · <b>co-author</b>: a <code>Co-authored-by</code> trailer names an AI tool · "
        "<b>marker</b>: a “Generated with &lt;tool&gt;” line · <b>review-fix</b>: the trailer sits "
        "on a commit that applies an AI reviewer's suggestion.</p></div>"
    )


def _limits() -> str:
    items = "".join(f"<li>{escape(line)}</li>" for line in real.LIMITS)
    return f'<div class="card"><ul class="limits">{items}</ul></div>'


def _headline(view: real.RealView) -> str:
    ref = view.reference
    reference = (
        f" {ref.label} is shown as the reference before any visible AI signal "
        f"({ref.card.ai_assisted_share:.0%} AI-assisted)."
        if ref
        else ""
    )
    return (
        f"{view.after_label} vs {view.before_label} "
        f"({view.before.ai_assisted_share:.0%} AI-assisted).{reference} Engineers are the "
        "distinct humans credited with a merged PR in each window; bots are excluded. Token "
        "cost per merged PR is not observable in public data."
    )


def _consolidation(view: real.RealView) -> str:
    def cell(v: real.Variant, f: str, fmt: str) -> str:
        b, c = getattr(v.baseline, f), getattr(v.current, f)
        return (
            f"<td>{fmt.format(b)} → <b>{fmt.format(c)}</b> "
            f'<span class="small">({escape(real.change(b, c, fmt))})</span></td>'
        )

    def row(v: real.Variant) -> str:
        name = escape(v.name).replace("`", "")
        style = ' class="headline"' if v.headline else ""
        cells = "".join(cell(v, f, fmt) for _, f, fmt in real.CONSOLIDATION_ROWS)
        return f"<tr{style}><td>{name}</td>{cells}</tr>"

    head = "".join(f"<th>{escape(label)}</th>" for label, _, _ in real.CONSOLIDATION_ROWS)
    return (
        '<div class="card"><table class="compact"><thead><tr><th>Comparison</th>'
        f"{head}</tr></thead><tbody>{''.join(row(v) for v in view.variants)}</tbody></table>"
        '<p class="small">The bold row is the headline used everywhere else on this page.</p>'
        "</div>"
    )


def _guards(view: real.RealView) -> str:
    ref = view.reference
    blocks = ((view.before_label, view.signals),) + (
        ((ref.label, view.signals_reference),) if ref else ()
    )
    return "".join(
        f'<h3 class="small">{escape(view.after_label)} vs {escape(label)}</h3>'
        f"{dashboard._signals(signals)}"
        for label, signals in blocks
    )


def render(view: real.RealView) -> str:
    snap = view.snapshot
    repo, url = escape(snap.repo), escape(snap.url)
    tiles = "".join(dashboard.kpi_tile(k, view.before, view.after) for k in KPIS)
    trends = (
        '<div class="grid2">'
        + _trend(view, "PRs per engineer per week", "prs_per_engineer_week", "{:.1f}")
        + _trend(view, "Revert rate", "change_failure_rate", "{:.0%}")
        + _trend(view, "AI-assisted share of merged PRs", "ai_assisted_share", "{:.0%}")
        + _trend(
            view, "Median hours to first human review", "median_review_latency_hours", "{:.1f}"
        )
        + "</div>"
    )
    ref = view.reference
    reference = f" · pre-AI reference <b>{escape(ref.label)}</b>" if ref else ""
    base_repos = view.baseline.repos
    counted = (
        "<span>Baseline counts "
        + " + ".join(
            f'<a href="https://github.com/{escape(r)}"><b>{escape(r)}</b></a>' for r in base_repos
        )
        + "</span>"
        if len(base_repos) > 1
        else ""
    )
    facts = (
        f'<div class="facts"><span>Source <a href="{url}"><b>{repo}</b></a></span>'
        f"<span>Merged PRs into <b>{escape(snap.default_branch)}</b></span>"
        f"<span>Current <b>{escape(view.after_label)}</b> vs baseline "
        f"<b>{escape(view.before_label)}</b>{reference}</span>{counted}"
        f"<span>Fetched <b>{escape(view.fetched_on)}</b> via the GitHub API</span>"
        f"<span><b>{len(view.prs):,}</b> PRs in the snapshot</span></div>"
    )
    body = "".join(
        (
            dashboard._section(
                "North star and guardrails",
                _headline(view),
                f'<div class="tiles">{tiles}</div>',
            ),
            dashboard._section(
                "Repository consolidation",
                real.moved_note(view).replace("`", ""),
                _consolidation(view),
            )
            if view.variants
            else "",
            dashboard._section(
                "Weekly trend",
                "Same week index in each window; per-engineer rates use the window's engineers. "
                "Dashed lines are the earlier windows.",
                trends,
            ),
            dashboard._section(
                "Goodhart guards",
                "The same automatic checks as the synthetic run, applied to the real numbers.",
                _guards(view),
            ),
            dashboard._section(
                f"AI cohorts, {view.after_label}",
                "Merged PRs split by the strongest AI evidence they carry. Cohorts differ in who "
                "uses them and for what, so read differences as descriptive, not causal.",
                _cohorts(view),
            ),
            dashboard._section(
                "How AI assistance was attributed",
                "Only explicit, observable signals count; everything else is 'no AI signal'.",
                _evidence(view),
            ),
            dashboard._section(
                "Limits of this evidence", "Read every number above with these in mind.", _limits()
            ),
        )
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>AI-Native Productivity Meter on real data: {repo}</title>"
        f"<style>{CSS}{EXTRA_CSS}</style></head><body><main>"
        f"<header><div><h1>AI-Native Productivity Meter on real data: {repo}</h1>"
        '<p class="sub">The same <code>devex</code> metrics engine, run on merged pull requests '
        f'of <a href="{url}">{url}</a>.</p>{facts}</div>'
        '<div class="banner real">Real public data</div></header>'
        f"{body}"
        f"<footer>Generated by <code>devex dashboard --source real</code> from a committed "
        f'snapshot of <a href="{url}">{repo}</a> fetched {escape(view.fetched_on)}. Method, '
        "heuristics and limits: docs/06-real-data.md.</footer>"
        "</main></body></html>\n"
    )
