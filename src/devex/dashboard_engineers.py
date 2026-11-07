"""The "Engineers" section of a real-data dashboard: a sortable top-N table with its guardrail
flags, the spread of per-engineer output, and the paired change of AI adopters vs others.
Pseudonyms only: this module never receives a real login. Deterministic HTML and SVG."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from devex import dashboard, engineers, real
from devex.dashboard import BASELINE_COLOR, PLATFORM_COLOR
from devex.engineers import KPIS, Engineer, Paired, Ranking

__all__ = ["CSS", "TOP", "Strip", "section", "strip_chart"]

TOP = 15
ADOPTER_COLOR = "#7c3aed"
ROLE_COLORS = {"reference": "#cbd5e1", "baseline": BASELINE_COLOR, "current": PLATFORM_COLOR}

CSS = """
table.sortable th[data-k]{cursor:pointer}table.sortable th[data-k]:after{content:" ↕";color:#cbd5e1}
td .rk{color:#94a3b8;font-size:11px;margin-left:4px}
td.flag{color:#b45309;font-weight:600}td.nm{white-space:nowrap}
.strip .dot{fill-opacity:.55}.strip .med{stroke:#0f172a;stroke-width:2.5}
.strip .box{fill:#0f172a;fill-opacity:.06;stroke:#94a3b8}
"""

SORT_JS = (
    "<script>document.querySelectorAll('table.sortable th[data-k]').forEach(function(h){"
    "h.addEventListener('click',function(){var b=h.closest('table').tBodies[0],"
    "i=Array.prototype.indexOf.call(h.parentNode.children,h),d=h.dataset.d=h.dataset.d==='1'?"
    "'-1':'1',v=function(r){var x=parseFloat(r.cells[i].dataset.v);return isNaN(x)?Infinity:x};"
    "Array.prototype.slice.call(b.rows).sort(function(x,y){return(v(x)-v(y))*d})"
    ".forEach(function(r){b.appendChild(r)})})})</script>"
)


@dataclass(frozen=True, slots=True)
class Strip:
    """One row of a strip chart: named points, a colour, and the quartile box and median."""

    label: str
    points: tuple[tuple[str, float], ...]  # (pseudonym, value)
    color: str


def _jitter(name: str) -> float:
    """A stable vertical offset in [0, 1) from the pseudonym's hex digits."""
    digits = "".join(c for c in name if c in "0123456789abcdef")[-4:] or "0"
    return int(digits, 16) / 16 ** len(digits)


def strip_chart(
    strips: Sequence[Strip],
    *,
    title: str,
    lo: float,
    hi: float,
    ticks: Sequence[tuple[float, str]],
    width: int = 560,
    label_w: int = 150,
) -> str:
    """Dots on a log axis, one row per strip, with the p25-p75 box and the median as a bar."""
    row_h, top, bottom = 46, 6, 24
    plot_w = width - label_w - 16
    span = math.log(hi) - math.log(lo)

    def x(value: float) -> str:
        clipped = min(max(value, lo), hi)
        return dashboard._num(label_w + plot_w * (math.log(clipped) - math.log(lo)) / span)

    height = top + row_h * len(strips) + bottom
    grid = "".join(
        f'<line x1="{x(v)}" x2="{x(v)}" y1="{top}" y2="{height - bottom}" class="grid"/>'
        f'<text x="{x(v)}" y="{height - 8}" class="ytick" text-anchor="middle">{escape(t)}</text>'
        for v, t in ticks
    )

    def row(i: int, strip: Strip) -> str:
        y0 = top + i * row_h
        values = [v for _, v in strip.points]
        q1, mid, q3 = engineers.percentiles(values, (25, 50, 75))
        dots = "".join(
            f'<circle cx="{x(v)}" cy="{dashboard._num(y0 + 8 + 26 * _jitter(n))}" r="3.2" '
            f'class="dot" fill="{strip.color}"/>'
            for n, v in strip.points
        )
        box = (
            f'<rect x="{x(q1)}" y="{y0 + 4}" height="34" class="box" '
            f'width="{dashboard._num(max(float(x(q3)) - float(x(q1)), 1))}"/>'
            f'<line x1="{x(mid)}" x2="{x(mid)}" y1="{y0 + 2}" y2="{y0 + 40}" class="med"/>'
            if values
            else ""
        )
        return (
            f'<text x="{label_w - 10}" y="{y0 + 20}" class="blabel">{escape(strip.label)}</text>'
            f'<text x="{label_w - 10}" y="{y0 + 34}" class="blabel small">n = {len(values)}'
            "</text>"
            f"{box}{dots}"
        )

    rows = "".join(row(i, s) for i, s in enumerate(strips))
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" class="strip" '
        f'aria-label="{escape(title)}" xmlns="http://www.w3.org/2000/svg">'
        f"<title>{escape(title)}</title>{grid}{rows}</svg>"
    )


def _cell(person: Engineer, key: str, fmt: str, ranks: dict[str, dict[str, int]]) -> str:
    value = getattr(person, key)
    if value is None:
        return '<td data-v="">-</td>'
    rank = ranks[key].get(person.name)
    return (
        f'<td data-v="{value:.6g}">{escape(fmt.format(value))}<span class="rk">#{rank}</span></td>'
    )


def _table(board: Ranking) -> str:
    head = "".join(f'<th data-k="{k.key}">{escape(k.label)}</th>' for k in KPIS)
    rows = "".join(
        f'<tr><td data-v="{i}">{i}</td><td class="nm">{escape(e.name)}</td>'
        f'<td data-v="{e.merged_prs}">{e.merged_prs}</td>'
        + "".join(_cell(e, k.key, k.fmt, board.ranks) for k in KPIS)
        + f'<td class="{"flag" if e.flags else ""}">{escape(", ".join(e.flags)) or "-"}</td></tr>'
        for i, e in enumerate(board.people[:TOP], 1)
    )
    flagged = board.flagged
    rules = " · ".join(f"<b>{escape(n)}</b>: {escape(r)}" for n, r in engineers.GUARDS)
    return (
        '<div class="card gap"><table class="compact sortable"><thead><tr>'
        '<th data-k="rank">#</th><th>Engineer</th><th data-k="prs">Merged PRs</th>'
        f"{head}<th>Guardrail flags</th></tr></thead><tbody>{rows}</tbody></table>"
        f'<p class="small">Top {min(TOP, len(board.people))} of {len(board.people)} engineers '
        f"with at least {board.min_prs} merged PRs ({board.active} merged at least one), by "
        f"{escape(board.sort.label)}. The grey number is the rank on that KPI among ranked "
        "engineers (1 = highest PRs and AI share, lowest otherwise); click a header to sort. "
        f"Guardrails apply from the p75 of ranked throughput ({board.threshold:.2f} PRs per "
        f"week): {len(flagged)} flagged in total. {rules}.</p></div>"
    )


THROUGHPUT_TICKS = ((0.1, "0.1"), (0.3, "0.3"), (1, "1"), (3, "3"), (10, "10"), (30, "30"))
CHANGE_TICKS = (
    (1 / 8, "÷8"),
    (1 / 4, "÷4"),
    (1 / 2, "÷2"),
    (1, "\u00d71"),
    (2, "\u00d72"),
    (4, "\u00d74"),
    (8, "\u00d78"),
)


def _spread(view: real.RealView, min_prs: int) -> str:
    boards = tuple(
        (p, engineers.ranking(view.prs, p.window, min_prs=min_prs)) for p in view.periods
    )
    everyone = tuple(
        Strip(
            f"{p.label}",
            tuple((e.name, e.prs_per_week) for e in engineers.engineers(view.prs, p.window)),
            ROLE_COLORS[p.role],
        )
        for p, _ in boards
    )
    chart = strip_chart(
        everyone,
        title="Merged PRs per week, one dot per engineer",
        lo=0.05,
        hi=40,
        ticks=THROUGHPUT_TICKS,
    )
    points = "".join(f"<th>p{p}</th>" for p in engineers.PERCENTILES)
    rows = "".join(
        f"<tr><td>{escape(p.label)}</td><td>{label}</td>"
        + "".join(f"<td>{v:.2f}</td>" for v in spread)
        + f"<td>{share}</td></tr>"
        for p, b in boards
        for label, spread, share in (
            (f"all {b.active}", b.spread, f"{b.top_share:.0%}"),
            (f"{len(b.people)} with ≥ {min_prs} PRs", b.core_spread, ""),
        )
    )
    return (
        '<div class="card"><h3>Merged PRs per week, one dot per engineer (log scale)</h3>'
        f"{chart}"
        '<p class="small">Box: p25 to p75 · bar: median. Every active engineer is a dot, so the '
        "long tail of occasional contributors piles up on the left.</p></div>"
        '<div class="card"><h3>Percentiles of PRs per engineer per week</h3>'
        '<table class="compact"><thead><tr><th>Window</th><th>Engineers</th>'
        f"{points}<th>Top 10% merge</th></tr></thead><tbody>{rows}</tbody></table>"
        '<p class="small">“Top 10% merge” is the share of merged PRs by the most prolific tenth '
        "of active engineers.</p></div>"
    )


def _paired(view: real.RealView, pairs: Sequence[Paired]) -> str:
    widest = pairs[0]
    strips = tuple(
        Strip(
            "AI adopters" if g is widest.adopters else "Others",
            tuple((p.name, p.after / p.before) for p in g.pairs),
            ADOPTER_COLOR if g is widest.adopters else BASELINE_COLOR,
        )
        for g in (widest.adopters, widest.others)
    )
    chart = strip_chart(
        strips,
        title="Change in PRs per week, same engineer",
        lo=1 / 10,
        hi=10,
        ticks=CHANGE_TICKS,
    )
    rows = "".join(
        f"<tr><td>≥ {p.min_prs}</td><td>{escape(g.label)}</td><td>{g.n}</td>"
        f"<td>{g.median_before:.2f} → {g.median_after:.2f}</td>"
        f"<td><b>{g.median_change:+.0%}</b></td><td>{g.pooled_change:+.0%}</td>"
        f"<td>{g.improved:.0%}</td></tr>"
        for p in pairs
        for g in (p.adopters, p.others)
    )
    gaps = " ".join(
        f"With at least {p.min_prs} merged PR{'s' if p.min_prs > 1 else ''} in each window, the "
        f"gap between the median changes is <b>{p.gap:+.0%}</b> (90% bootstrap interval "
        f"{p.interval[0]:+.0%} to {p.interval[1]:+.0%})."
        for p in pairs
    )
    return (
        f'<div class="card"><h3>{escape(view.before_label)} → {escape(view.after_label)}, '
        "engineers active in both (log scale)</h3>"
        f"{chart}"
        '<p class="small">One dot per engineer: current PRs per week over baseline. Box: p25 to '
        f"p75 · bar: median. Adopters: at least {engineers.ADOPTER_SHARE:.0%} AI-assisted merged "
        "PRs in the current window.</p></div>"
        '<div class="card"><h3>Paired change in PRs per engineer per week</h3>'
        '<table class="compact"><thead><tr><th>Min PRs each</th><th>Group</th><th>Engineers</th>'
        "<th>Median PRs / week</th><th>Median change</th><th>Pooled</th><th>Improved</th></tr>"
        f'</thead><tbody>{rows}</tbody></table><p class="small">{gaps} Observational: adopters '
        "chose to adopt and differ in role and area; agent PRs count for the engineer who "
        "delegated them.</p></div>"
    )


def section(view: real.RealView, min_prs: int = 5) -> str:
    """The Engineers section body for one repository's page."""
    board = engineers.ranking(view.prs, view.current.window, min_prs=min_prs)
    pairs = engineers.view_pairs(view, min_prs)
    caveats = "".join(f"<li>{escape(c)}</li>" for c in engineers.CAVEATS)
    return (
        f"{_table(board)}"
        f'<div class="grid2">{_spread(view, min_prs)}</div>'
        f'<div class="grid2 gap">{_paired(view, pairs)}</div>'
        f'<div class="card gap"><ul class="limits">{caveats}</ul></div>'
        f"{SORT_JS}"
    )
