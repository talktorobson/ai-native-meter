"""Command line entry point: ``devex <command>``. Commands print Markdown; ``dashboard``
writes a self-contained HTML file; ``fetch`` snapshots real PRs from a public GitHub repo.
``--source real`` runs ``report`` and ``dashboard`` on that snapshot instead of synthetic data."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime
from functools import reduce
from pathlib import Path

from devex import (
    context,
    dashboard,
    dashboard_real,
    decision,
    github_source,
    metrics,
    motion_fit,
    real,
    review_eval,
    routing,
    synthetic,
    vendor_scorecard,
)

DATA = Path(__file__).resolve().parents[2] / "data"
SNAPSHOT = DATA / "real" / "microsoft-vscode.json"
ENGINEERS = 120


def _pct(value: float) -> str:
    return f"{value:+.0%}"


def _rollout() -> tuple[tuple[metrics.PullRequest, ...], tuple[metrics.PullRequest, ...]]:
    """Synthetic PR history: baseline in Q1, platform rollout in Q2."""
    before = synthetic.generate(
        seed=1,
        engineers=ENGINEERS,
        window=synthetic.QUARTER_1,
        profile=synthetic.BASELINE,
        label="q1",
    )
    after = synthetic.generate(
        seed=2,
        engineers=ENGINEERS,
        window=synthetic.QUARTER_2,
        profile=synthetic.WITH_PLATFORM,
        label="q2",
    )
    return before, after


def _review_reports(
    cases: Sequence[review_eval.ReviewCase],
) -> tuple[review_eval.EvalReport, ...]:
    return review_eval.compare(
        {
            "rule-baseline": (review_eval.rule_reviewer, 0.0),
            "keyword-noisy": (review_eval.keyword_reviewer, 0.0),
        },
        cases,
    )


def _snapshot_path(args: argparse.Namespace) -> Path:
    if args.snapshot:
        return Path(args.snapshot)
    if args.repo:
        return DATA / "real" / f"{args.repo.replace('/', '-').lower()}.json"
    return SNAPSHOT


def _real_view(args: argparse.Namespace) -> real.RealView:
    snapshot = github_source.load_snapshot(_snapshot_path(args))
    try:
        return real.build(snapshot, baseline=args.baseline, current=args.current)
    except ValueError as error:
        raise SystemExit(str(error)) from error


def cmd_fetch(args: argparse.Namespace) -> str:
    if not (args.repo and args.since and args.until) or args.repo.count("/") != 1:
        raise SystemExit("fetch needs --repo OWNER/NAME, --since YYYY-MM-DD and --until YYYY-MM-DD")
    window = metrics.Window(datetime.fromisoformat(args.since), datetime.fromisoformat(args.until))
    path = _snapshot_path(args)
    base = github_source.load_snapshot(path) if path.exists() else None
    branch, records = github_source.fetch(
        args.repo, window, log=lambda line: print(line, file=sys.stderr)
    )
    fetched_on = args.fetched_on or date.today().isoformat()
    snapshot = github_source.merge_snapshot(base, args.repo, branch, window, fetched_on, records)
    github_source.write_snapshot(snapshot, path)
    return (
        f"Fetched {len(records):,} merged PRs from {args.repo} ({args.since} to {args.until}); "
        f"{path} now holds {len(snapshot.records):,} PRs in {len(snapshot.windows)} window(s)."
    )


def cmd_report(args: argparse.Namespace) -> str:
    if args.source == "real":
        return real.to_markdown(_real_view(args))
    before_prs, after_prs = _rollout()
    before = metrics.scorecard(before_prs, ENGINEERS, synthetic.QUARTER_1)
    after = metrics.scorecard(after_prs, ENGINEERS, synthetic.QUARTER_2)
    rows = (
        ("PRs / engineer / week", "prs_per_engineer_week", "{:.2f}"),
        (
            "Quality-adjusted PRs / engineer / week",
            "quality_adjusted_prs_per_engineer_week",
            "{:.2f}",
        ),
        ("Change failure rate", "change_failure_rate", "{:.1%}"),
        ("Median cycle time (h)", "median_cycle_time_hours", "{:.1f}"),
        ("Median review latency (h)", "median_review_latency_hours", "{:.1f}"),
        ("Median PR size (lines)", "median_pr_size_lines", "{:.0f}"),
        ("AI-assisted share", "ai_assisted_share", "{:.0%}"),
        ("Token cost per merged PR (USD)", "cost_per_merged_pr_usd", "${:.2f}"),
    )
    table = "\n".join(
        f"| {label} | {fmt.format(getattr(before, f))} | {fmt.format(getattr(after, f))} "
        f"| {_pct(metrics.relative_change(getattr(before, f), getattr(after, f)))} |"
        for label, f, fmt in rows
    )
    signals = metrics.gaming_signals(before, after) or ("None triggered.",)
    return "\n".join(
        (
            f"## Scorecard: baseline vs platform rollout ({ENGINEERS} engineers, synthetic)",
            "",
            "| Metric | Baseline | With platform | Change |",
            "|---|---|---|---|",
            table,
            "",
            "**Goodhart guards**",
            *(f"- {s}" for s in signals),
        )
    )


def cmd_eval_review(args: argparse.Namespace) -> str:
    cases = review_eval.load_cases(args.cases)
    reports = _review_reports(cases)
    return "\n".join(
        (
            f"## Code-review eval ({len(cases)} seeded cases)",
            "",
            review_eval.to_markdown(reports),
            "",
            "Plug any agent in as `Callable[[ReviewCase], Sequence[Finding]]` and it is scored "
            "on the same cases.",
        )
    )


def cmd_context(args: argparse.Namespace) -> str:
    graph = context.load_graph(args.graph)
    now = datetime.fromisoformat(args.now)
    pack = context.retrieve(graph, args.query, budget_tokens=args.budget, now=now)
    picked = "\n".join(
        f"| {s.entity.id} | {s.entity.kind} | {s.score:.2f} | {s.reason} |" for s in pack.items
    )
    issues = "\n".join(f"- `{i.entity_id}` {i.rule}: {i.detail}" for i in context.lint(graph, now))
    return "\n".join(
        (
            f'## Context pack for "{args.query}"',
            "",
            f"{len(pack.items)} items, {pack.tokens_used}/{pack.budget} tokens, "
            f"{pack.dropped} dropped by budget.",
            "",
            "| Entity | Kind | Score | Why |",
            "|---|---|---|---|",
            picked,
            "",
            "## Curation lint",
            "",
            issues or "- Clean.",
        )
    )


def cmd_route(args: argparse.Namespace) -> str:
    report = routing.simulate(routing.load_requests(args.requests), min_quality=args.min_quality)
    mix = ", ".join(f"{k}: {v}" for k, v in sorted(report.mix.items()))
    usage = ", ".join(f"{k}: {v}" for k, v in sorted(report.model_usage.items()))
    return "\n".join(
        (
            f"## Model routing (quality bar {args.min_quality:.0%})",
            "",
            f"- Requests: {report.requests} ({mix})",
            f"- Models used: {usage}",
            f"- Cost: ${report.baseline_cost_usd:.3f} always-frontier vs "
            f"${report.routed_cost_usd:.3f} routed ({report.savings_ratio:.0%} saved)",
            f"- Expected pass rate: {report.baseline_expected_quality:.1%} vs "
            f"{report.routed_expected_quality:.1%}",
        )
    )


def _motion_fits(args: argparse.Namespace) -> tuple[motion_fit.MotionFit, ...]:
    return tuple(map(motion_fit.assess, motion_fit.load_capabilities(args.capabilities)))


def _analysis(args: argparse.Namespace, fits: Sequence[motion_fit.MotionFit]) -> decision.Analysis:
    """Stage 2, with review quality measured live by ``review_eval`` where a candidate names
    a reviewer, and any ``--weight`` overrides applied (others rescaled to sum to 1)."""
    measured = {r.reviewer: r.f_beta for r in _review_reports(review_eval.load_cases(args.cases))}
    spec = vendor_scorecard.load_decision(args.candidates, measured)
    unknown = sorted({k for k, _ in args.weight} - set(spec.weights))
    if unknown:
        raise SystemExit(f"Unknown weight(s): {', '.join(unknown)}; use {', '.join(spec.weights)}")
    weights = reduce(lambda w, kv: vendor_scorecard.reweight(w, *kv), args.weight, spec.weights)
    fit = next((f for f in fits if f.capability.name == spec.capability), None)
    return decision.analyse(spec, fit=fit, weights=weights)


def cmd_decide(args: argparse.Namespace) -> str:
    fits = _motion_fits(args)
    analysis = _analysis(args, fits)
    if args.memo:
        return decision.memo(analysis, args.now[:10])
    return "\n".join(
        (
            "## Build vs partner: motion fit (Stage 1, illustrative)",
            "",
            "Fit = 0.5 + sum of affinity x (attribute - 0.5). Struck-through motions are "
            "blocked by a guard. Own the context, the evals and the interfaces; rent the rest.",
            "",
            motion_fit.to_markdown(fits),
            "",
            decision.to_markdown(analysis),
        )
    )


def cmd_demo(args: argparse.Namespace) -> str:
    steps: Sequence[Callable[[argparse.Namespace], str]] = (
        cmd_report,
        cmd_eval_review,
        cmd_context,
        cmd_route,
        cmd_decide,
    )
    return "\n\n".join(step(args) for step in steps)


def dashboard_data(args: argparse.Namespace) -> dashboard.DashboardData:
    before_prs, after_prs = _rollout()
    q1, q2 = synthetic.QUARTER_1, synthetic.QUARTER_2
    before = metrics.scorecard(before_prs, ENGINEERS, q1)
    after = metrics.scorecard(after_prs, ENGINEERS, q2)
    cases = review_eval.load_cases(args.cases)
    fits = _motion_fits(args)
    return dashboard.DashboardData(
        engineers=ENGINEERS,
        baseline_label="Q1 2026",
        platform_label="Q2 2026",
        before=before,
        after=after,
        weekly_before=dashboard.weekly_scorecards(before_prs, ENGINEERS, q1),
        weekly_after=dashboard.weekly_scorecards(after_prs, ENGINEERS, q2),
        signals=metrics.gaming_signals(before, after),
        review=_review_reports(cases),
        review_cases=len(cases),
        routing=routing.simulate(
            routing.load_requests(args.requests), min_quality=args.min_quality
        ),
        min_quality=args.min_quality,
        motion_fits=fits,
        decision=_analysis(args, fits),
    )


def cmd_dashboard(args: argparse.Namespace) -> str:
    is_real = args.source == "real"
    out = Path(args.out or ("docs/dashboard-real.html" if is_real else "docs/dashboard.html"))
    html = (
        dashboard_real.render(_real_view(args))
        if is_real
        else dashboard.render(dashboard_data(args))
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return f"Dashboard written to {out}"


def _weight(text: str) -> tuple[str, float]:
    name, sep, value = text.partition("=")
    try:
        number = float(value)
    except ValueError:
        number = -1.0
    if not sep or not 0 <= number < 1:
        raise argparse.ArgumentTypeError(f"expected NAME=VALUE with 0 <= VALUE < 1, got {text!r}")
    return name, number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devex", description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "review_benchmark.json"))
    parser.add_argument("--graph", default=str(DATA / "context_graph.json"))
    parser.add_argument("--requests", default=str(DATA / "routing_requests.json"))
    parser.add_argument("--candidates", default=str(DATA / "vendor_candidates.json"))
    parser.add_argument("--capabilities", default=str(DATA / "capabilities.json"))
    parser.add_argument("--memo", action="store_true", help="decide: print an ADR-style memo")
    parser.add_argument(
        "--weight",
        type=_weight,
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="decide: override one criterion weight (repeatable); the others rescale",
    )
    parser.add_argument("--query", default="add idempotency to the batch transfers endpoint")
    parser.add_argument("--budget", type=int, default=400)
    parser.add_argument("--now", default="2026-11-01T00:00:00")
    parser.add_argument("--min-quality", type=float, default=0.9)
    parser.add_argument(
        "--out",
        help="dashboard output path (default docs/dashboard.html, or docs/dashboard-real.html)",
    )
    parser.add_argument(
        "--source",
        choices=("synthetic", "real"),
        default="synthetic",
        help="report/dashboard: synthetic rollout, or the committed real-PR snapshot",
    )
    parser.add_argument("--snapshot", help=f"real-PR snapshot path (default {SNAPSHOT.name})")
    parser.add_argument("--repo", help="fetch: public GitHub repo as OWNER/NAME")
    parser.add_argument("--since", help="fetch: window start date, inclusive (YYYY-MM-DD)")
    parser.add_argument("--until", help="fetch: window end date, exclusive (YYYY-MM-DD)")
    parser.add_argument("--fetched-on", help="fetch: date to record (default today)")
    parser.add_argument(
        "--baseline",
        help="real: start date of the baseline window (default: same quarter a year before "
        "the current window, else the earliest)",
    )
    parser.add_argument(
        "--current", help="real: start date of the current window (default: the latest)"
    )
    commands = {
        "report": cmd_report,
        "eval-review": cmd_eval_review,
        "context": cmd_context,
        "route": cmd_route,
        "decide": cmd_decide,
        "demo": cmd_demo,
        "dashboard": cmd_dashboard,
        "fetch": cmd_fetch,
    }
    parser.add_argument("command", choices=commands)
    parser.set_defaults(commands=commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(args.commands[args.command](args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
