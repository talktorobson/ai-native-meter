"""Static, self-contained HTML dashboard: inline CSS and inline SVG, no external assets.

Every number comes from the pure functions in ``metrics``, ``review_eval``, ``routing``
and ``vendor_scorecard``; this module only lays them out. Output is deterministic for a
given input, so the generated file can be diffed and committed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from html import escape
from math import floor, log10

from devex import metrics
from devex.metrics import PullRequest, Scorecard, Window
from devex.review_eval import EvalReport
from devex.routing import RoutingReport
from devex.vendor_scorecard import Recommendation

__all__ = [
    "KPIS",
    "Bar",
    "DashboardData",
    "Kpi",
    "Series",
    "bar_chart",
    "kpi_tile",
    "line_chart",
    "nice_max",
    "render",
    "weekly_scorecards",
    "weekly_windows",
]

BASELINE_COLOR = "#94a3b8"
PLATFORM_COLOR = "#2563eb"
GOOD = "#15803d"
BAD = "#b91c1c"
NEUTRAL = "#64748b"
SMALL, SMALL_LABEL = 360, 120


@dataclass(frozen=True, slots=True)
class Kpi:
    label: str
    field: str
    fmt: str
    direction: int  # +1 higher is better, -1 lower is better, 0 neutral
    hint: str = ""


KPIS: tuple[Kpi, ...] = (
    Kpi(
        "Quality-adjusted PRs / engineer / week",
        "quality_adjusted_prs_per_engineer_week",
        "{:.2f}",
        1,
        "North star",
    ),
    Kpi("PRs / engineer / week", "prs_per_engineer_week", "{:.2f}", 1, "Raw throughput"),
    Kpi("Change failure rate", "change_failure_rate", "{:.1%}", -1, "Guardrail"),
    Kpi("Median cycle time", "median_cycle_time_hours", "{:.1f} h", -1, "Guardrail"),
    Kpi("Median review latency", "median_review_latency_hours", "{:.1f} h", -1, "Guardrail"),
    Kpi("Token cost per merged PR", "cost_per_merged_pr_usd", "${:.2f}", -1, "Guardrail"),
    Kpi("Median PR size", "median_pr_size_lines", "{:.0f} lines", 0, "Gaming check"),
    Kpi("AI-assisted share", "ai_assisted_share", "{:.0%}", 0, "Adoption"),
)


@dataclass(frozen=True, slots=True)
class Series:
    label: str
    values: tuple[float, ...]
    color: str
    dashed: bool = False


@dataclass(frozen=True, slots=True)
class Bar:
    label: str
    value: float
    color: str
    text: str


@dataclass(frozen=True, slots=True)
class DashboardData:
    engineers: int
    baseline_label: str
    platform_label: str
    before: Scorecard
    after: Scorecard
    weekly_before: tuple[Scorecard, ...]
    weekly_after: tuple[Scorecard, ...]
    signals: tuple[str, ...]
    review: tuple[EvalReport, ...]
    review_cases: int
    routing: RoutingReport
    min_quality: float
    vendor: Recommendation


def weekly_windows(window: Window) -> tuple[Window, ...]:
    """Whole weeks inside the window; a trailing partial week is dropped."""
    week = timedelta(days=7)
    return tuple(
        Window(window.start + i * week, window.start + (i + 1) * week)
        for i in range(int(window.weeks))
    )


def weekly_scorecards(
    prs: Iterable[PullRequest], engineers: int, window: Window
) -> tuple[Scorecard, ...]:
    items = tuple(prs)
    return tuple(metrics.scorecard(items, engineers, w) for w in weekly_windows(window))


def nice_max(value: float) -> float:
    """Smallest 'round' number (1, 2, 2.5, 5 x 10^k) at or above value."""
    if value <= 0:
        return 1.0
    base = 10 ** floor(log10(value))
    return next(m * base for m in (1, 2, 2.5, 5, 10) if m * base >= value)


def _num(value: float) -> str:
    return f"{value:.1f}"


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def line_chart(
    series: Sequence[Series],
    *,
    x_labels: Sequence[str],
    y_format: str,
    title: str,
    width: int = 560,
    height: int = 240,
) -> str:
    left, right, top, bottom = 52, 16, 14, 30
    plot_w, plot_h = width - left - right, height - top - bottom
    peak = max((v for s in series for v in s.values), default=0.0) * 1.1
    y_max = 4 * nice_max(peak / 4)
    steps = max(len(x_labels) - 1, 1)

    def x(i: int) -> float:
        return left + plot_w * i / steps

    def y(v: float) -> float:
        return top + plot_h * (1 - v / y_max)

    ticks = tuple(y_max * k / 4 for k in range(5))
    grid = "".join(
        f'<line x1="{left}" x2="{width - right}" y1="{_num(y(t))}" y2="{_num(y(t))}" '
        f'class="grid"/><text x="{left - 8}" y="{_num(y(t) + 4)}" class="ytick">'
        f"{escape(y_format.format(t))}</text>"
        for t in ticks
    )
    xticks = "".join(
        f'<text x="{_num(x(i))}" y="{height - 10}" class="xtick">{escape(label)}</text>'
        for i, label in enumerate(x_labels)
    )

    def path(s: Series) -> str:
        dash = ' stroke-dasharray="6 4"' if s.dashed else ""
        points = " ".join(f"{_num(x(i))},{_num(y(v))}" for i, v in enumerate(s.values))
        dots = "".join(
            f'<circle cx="{_num(x(i))}" cy="{_num(y(v))}" r="3" fill="{s.color}">'
            f"<title>{escape(s.label)} {escape(x_labels[i])}: "
            f"{escape(y_format.format(v))}</title></circle>"
            for i, v in enumerate(s.values)
        )
        return (
            f'<polyline fill="none" stroke="{s.color}" stroke-width="2.5"{dash} '
            f'points="{points}"/>{dots}'
        )

    lines = "".join(map(path, series))
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="{escape(title)}" xmlns="http://www.w3.org/2000/svg">'
        f"<title>{escape(title)}</title>{grid}{xticks}{lines}</svg>"
    )


def bar_chart(
    bars: Sequence[Bar],
    *,
    title: str,
    scale: float | None = None,
    width: int = 560,
    label_w: int = 170,
) -> str:
    """Horizontal bars on a shared scale (defaults to a round number above the max)."""
    text_w, row_h, bar_h = 70, 30, 18
    height = row_h * len(bars) + 4
    top = scale or nice_max(max((b.value for b in bars), default=0.0))
    plot_w = width - label_w - text_w
    rows = "".join(
        f'<g transform="translate(0,{i * row_h + 4})">'
        f'<text x="{label_w - 10}" y="{bar_h - 4}" class="blabel">{escape(b.label)}</text>'
        f'<rect x="{label_w}" y="0" width="{plot_w}" height="{bar_h}" class="track"/>'
        f'<rect x="{label_w}" y="0" width="{_num(plot_w * min(b.value / top, 1.0))}" '
        f'height="{bar_h}" rx="3" fill="{b.color}"/>'
        f'<text x="{width - text_w + 8}" y="{bar_h - 4}" class="bvalue">{escape(b.text)}</text>'
        f"</g>"
        for i, b in enumerate(bars)
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="{escape(title)}" xmlns="http://www.w3.org/2000/svg">'
        f"<title>{escape(title)}</title>{rows}</svg>"
    )


def _delta_color(change: float, direction: int) -> str:
    if direction == 0 or change == 0:
        return NEUTRAL
    return GOOD if change * direction > 0 else BAD


def kpi_tile(kpi: Kpi, before: Scorecard, after: Scorecard) -> str:
    old, new = getattr(before, kpi.field), getattr(after, kpi.field)
    change = metrics.relative_change(old, new)
    arrow = "▲" if change > 0 else "▼" if change < 0 else "●"
    hero = " hero" if kpi.hint == "North star" else ""
    color = _delta_color(change, kpi.direction)
    return (
        f'<div class="tile{hero}"><div class="hint">{escape(kpi.hint)}</div>'
        f'<div class="label">{escape(kpi.label)}</div>'
        f'<div class="value">{escape(kpi.fmt.format(new))}</div>'
        f'<div class="meta"><span class="delta" style="color:{color}">'
        f"{arrow} {change:+.0%}</span> vs baseline {escape(kpi.fmt.format(old))}</div></div>"
    )


def _legend(series: Sequence[Series], y_format: str) -> str:
    return "".join(
        f'<span class="key"><i style="background:{s.color}"></i>{escape(s.label)} '
        f"(avg {escape(y_format.format(_mean(s.values)))})</span>"
        for s in series
    )


def _trend(
    title: str, field: str, y_format: str, data: DashboardData, x_labels: Sequence[str]
) -> str:
    series = (
        Series(
            data.baseline_label,
            tuple(getattr(s, field) for s in data.weekly_before),
            BASELINE_COLOR,
            dashed=True,
        ),
        Series(
            data.platform_label,
            tuple(getattr(s, field) for s in data.weekly_after),
            PLATFORM_COLOR,
        ),
    )
    chart = line_chart(series, x_labels=x_labels, y_format=y_format, title=title)
    return (
        f'<div class="card"><h3>{escape(title)}</h3>'
        f'<div class="legend">{_legend(series, y_format)}</div>{chart}</div>'
    )


def _section(title: str, lead: str, body: str) -> str:
    return f'<section><h2>{escape(title)}</h2><p class="lead">{escape(lead)}</p>{body}</section>'


def _signals(signals: Sequence[str]) -> str:
    if not signals:
        return '<div class="alert ok">✓ No Goodhart guard triggered.</div>'
    return "".join(f'<div class="alert warn">⚠ {escape(s)}</div>' for s in signals)


def _review(reports: Sequence[EvalReport]) -> str:
    palette = (PLATFORM_COLOR, "#f59e0b", "#7c3aed", "#0d9488")
    colors = {r.reviewer: palette[i % len(palette)] for i, r in enumerate(reports)}
    ratio_metrics = (("Precision", "precision"), ("Recall", "recall"), ("F0.5", "f_beta"))
    noise_top = nice_max(max((r.noise_per_pr for r in reports), default=0.0))
    cards = "".join(
        f'<div class="card"><h3>{escape(label)}</h3>'
        + bar_chart(
            tuple(
                Bar(
                    r.reviewer,
                    getattr(r, field),
                    colors[r.reviewer],
                    f"{getattr(r, field):.2f}" if field == "f_beta" else f"{getattr(r, field):.0%}",
                )
                for r in reports
            ),
            title=label,
            scale=1.0,
        )
        + "</div>"
        for label, field in ratio_metrics
    )
    noise = (
        '<div class="card"><h3>Noise per PR (false positives, lower is better)</h3>'
        + bar_chart(
            tuple(
                Bar(r.reviewer, r.noise_per_pr, colors[r.reviewer], f"{r.noise_per_pr:.2f}")
                for r in reports
            ),
            title="Noise per PR",
            scale=noise_top,
        )
        + "</div>"
    )
    return f'<div class="grid2">{cards}{noise}</div>'


def _routing(report: RoutingReport, min_quality: float) -> str:
    cost_top = nice_max(report.baseline_cost_usd)
    cost = bar_chart(
        (
            Bar(
                "Always frontier",
                report.baseline_cost_usd,
                BASELINE_COLOR,
                f"${report.baseline_cost_usd:.2f}",
            ),
            Bar(
                "Routed by evals",
                report.routed_cost_usd,
                PLATFORM_COLOR,
                f"${report.routed_cost_usd:.2f}",
            ),
        ),
        title="Cost",
        scale=cost_top,
        width=SMALL,
        label_w=SMALL_LABEL,
    )
    quality = bar_chart(
        (
            Bar(
                "Always frontier",
                report.baseline_expected_quality,
                BASELINE_COLOR,
                f"{report.baseline_expected_quality:.1%}",
            ),
            Bar(
                "Routed by evals",
                report.routed_expected_quality,
                PLATFORM_COLOR,
                f"{report.routed_expected_quality:.1%}",
            ),
        ),
        title="Expected pass rate",
        scale=1.0,
        width=SMALL,
        label_w=SMALL_LABEL,
    )
    usage = bar_chart(
        tuple(
            Bar(name, count, PLATFORM_COLOR, str(count))
            for name, count in sorted(report.model_usage.items())
        ),
        title="Requests per model",
        width=SMALL,
        label_w=SMALL_LABEL,
    )
    pass_delta = report.routed_expected_quality - report.baseline_expected_quality
    stats = (
        f'<div class="tiles3">'
        f'<div class="tile"><div class="label">Cost saved</div>'
        f'<div class="value" style="color:{GOOD}">{report.savings_ratio:.0%}</div>'
        f'<div class="meta">{report.requests} requests, quality bar {min_quality:.0%}</div></div>'
        f'<div class="tile"><div class="label">Pass-rate trade-off</div>'
        f'<div class="value" style="color:{_delta_color(pass_delta, 1)}">'
        f"{pass_delta * 100:+.1f} pp</div>"
        f'<div class="meta">stated next to the savings, never hidden</div></div>'
        f'<div class="tile"><div class="label">Task mix</div>'
        f'<div class="value small">'
        + escape(", ".join(f"{k}: {v}" for k, v in sorted(report.mix.items())))
        + "</div></div></div>"
    )
    return (
        stats
        + '<div class="grid3">'
        + f'<div class="card"><h3>Cost for the same requests</h3>{cost}</div>'
        + f'<div class="card"><h3>Expected pass rate</h3>{quality}</div>'
        + f'<div class="card"><h3>Requests per model</h3>{usage}</div></div>'
    )


def _vendor(rec: Recommendation) -> str:
    top = nice_max(max((d.score for d in rec.ranking), default=0.0))
    rows = "".join(
        f"<tr><td>{escape(d.name)}</td><td>{escape(d.motion)}</td>"
        f'<td class="barcell">'
        + bar_chart(
            (
                Bar(
                    "", d.score, PLATFORM_COLOR if d.qualified else BASELINE_COLOR, f"{d.score:.2f}"
                ),
            ),
            title=f"{d.name} score",
            scale=top,
            width=320,
            label_w=0,
        )
        + f"</td><td>{d.lock_in_risk:.0%}</td><td>"
        + (
            '<span class="badge pass">pass</span>'
            if d.qualified
            else "".join(f'<span class="badge fail">{escape(g)}</span>' for g in d.failed_gates)
        )
        + "</td></tr>"
        for d in rec.ranking
    )
    return (
        '<div class="card"><table><thead><tr><th>Candidate</th><th>Motion</th><th>Score</th>'
        f"<th>Lock-in risk</th><th>Hard gates</th></tr></thead><tbody>{rows}</tbody></table>"
        f'<div class="verdict"><strong>Verdict:</strong> {escape(rec.verdict)}</div></div>'
    )


CSS = """
*{box-sizing:border-box}
body{margin:0;color:#0f172a;background:#f8fafc;
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:1240px;margin:0 auto;padding:32px 28px 48px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;margin-bottom:8px}
h1{font-size:26px;margin:0 0 4px}
h2{font-size:19px;margin:36px 0 2px}
h3{font-size:14px;margin:0 0 8px;color:#334155}
.sub,.lead{color:#475569;margin:0 0 14px}
.banner{background:#fef3c7;color:#92400e;border:1px solid #fcd34d;border-radius:999px;
padding:6px 14px;font-weight:600;font-size:13px;white-space:nowrap}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.tiles3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:14px}
.tile,.card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px}
.tile.hero{border:2px solid #2563eb;background:#eff6ff}
.hint{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#64748b;font-weight:600}
.label{font-size:13px;color:#334155}
.value{font-size:28px;font-weight:700;margin:2px 0}
.value.small{font-size:16px;font-weight:600;padding-top:8px}
.meta{font-size:12.5px;color:#64748b}
.delta{font-weight:700}
.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.legend{font-size:12.5px;color:#334155;margin-bottom:4px}
.key{margin-right:16px}.key i{display:inline-block;width:12px;height:12px;border-radius:3px;
margin-right:6px;vertical-align:-1px}
.alert{border-radius:10px;padding:12px 16px;margin-top:12px;font-weight:500}
.alert.warn{background:#fef2f2;border:1px solid #fecaca;color:#991b1b}
.alert.ok{background:#f0fdf4;border:1px solid #bbf7d0;color:#166534}
svg text{font-family:inherit}
.grid{stroke:#e2e8f0}.ytick{font-size:11px;fill:#64748b;text-anchor:end}
.xtick{font-size:11px;fill:#64748b;text-anchor:middle}
.blabel{font-size:12.5px;fill:#334155;text-anchor:end}.bvalue{font-size:12.5px;fill:#0f172a;
font-weight:600}.track{fill:#f1f5f9}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#64748b;
border-bottom:1px solid #e2e8f0;padding:8px}
td{padding:8px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
td.barcell{width:300px}
.badge{display:inline-block;border-radius:999px;padding:1px 10px;font-size:12px;font-weight:600;
margin-right:4px}
.badge.pass{background:#dcfce7;color:#166534}.badge.fail{background:#fee2e2;color:#991b1b}
.verdict{margin-top:14px;padding:12px 16px;background:#eff6ff;border-radius:10px;color:#1e3a8a}
footer{margin-top:40px;font-size:12.5px;color:#64748b}
"""


def render(data: DashboardData) -> str:
    x_labels = tuple(f"W{i + 1}" for i in range(len(data.weekly_before)))
    tiles = "".join(kpi_tile(k, data.before, data.after) for k in KPIS)
    trends = (
        '<div class="grid2">'
        + _trend("PRs per engineer per week", "prs_per_engineer_week", "{:.1f}", data, x_labels)
        + _trend("Change failure rate", "change_failure_rate", "{:.0%}", data, x_labels)
        + "</div>"
    )
    body = "".join(
        (
            _section(
                "North star and guardrails",
                f"{data.baseline_label} (baseline) vs {data.platform_label} (with platform), "
                f"{data.engineers} engineers. Green means the move is good for that metric.",
                f'<div class="tiles">{tiles}</div>',
            ),
            _section(
                "Weekly trend",
                "Same week index in each quarter. Throughput rises without the failure rate "
                "following it up.",
                trends,
            ),
            _section(
                "Goodhart guards",
                "Automatic checks that throughput gains are real gains.",
                _signals(data.signals),
            ),
            _section(
                "Code-review agents, scored on evals",
                f"{data.review_cases} seeded-defect cases. F0.5 weights precision above recall: "
                "noise is what gets reviewers muted.",
                _review(data.review),
            ),
            _section(
                "Eval-driven model routing",
                "Cheapest model whose measured pass rate clears the bar, per task class.",
                _routing(data.routing, data.min_quality),
            ),
            _section(
                "Build vs partner: code review",
                "Hard gates first, then weighted criteria discounted by lock-in risk.",
                _vendor(data.vendor),
            ),
        )
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>AI-native DevEx dashboard</title><style>{CSS}</style></head><body><main>"
        "<header><div><h1>AI-native developer experience</h1>"
        '<p class="sub">Throughput, quality and cost of an AI platform rollout, '
        "computed by the <code>devex</code> reference implementation.</p></div>"
        '<div class="banner">Synthetic / illustrative data</div></header>'
        f"{body}"
        "<footer>Generated by <code>devex dashboard</code>. All data is synthetic and every "
        "vendor is fictional; numbers illustrate the method, not results.</footer>"
        "</main></body></html>\n"
    )
