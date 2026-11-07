"""Command line entry point: ``devex <command>``. Commands print Markdown; ``dashboard``
writes a self-contained HTML file; ``fetch`` snapshots real PRs from a public GitHub repo.
``--source real`` runs ``report`` and ``dashboard`` on committed snapshots instead of synthetic
data; ``compare`` puts the real-data headlines of several repositories side by side."""

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


def snapshot_path(repo: str) -> Path:
    return DATA / "real" / f"{repo.replace('/', '-').lower()}.json"


def committed_snapshots() -> tuple[Path, ...]:
    """Every snapshot in ``data/real``, the original example (microsoft/vscode) first."""
    others = sorted(p for p in (DATA / "real").glob("*.json") if p != SNAPSHOT)
    return (SNAPSHOT, *others) if SNAPSHOT.exists() else tuple(others)


def _snapshot_path(args: argparse.Namespace) -> Path:
    if args.snapshot:
        return Path(args.snapshot)
    repo = getattr(args, "into", None) or next(iter(args.repo or ()), None)
    return snapshot_path(repo) if repo else SNAPSHOT


def _snapshot_paths(args: argparse.Namespace, *, every: bool) -> tuple[Path, ...]:
    """``--snapshot``, else one snapshot per ``--repo``, else all committed (``every``) or the
    original example."""
    if args.snapshot:
        return (Path(args.snapshot),)
    if args.repo:
        return tuple(map(snapshot_path, args.repo))
    return committed_snapshots() if every else (SNAPSHOT,)


def _real_views(args: argparse.Namespace, *, every: bool) -> tuple[real.RealView, ...]:
    missing = [str(p) for p in _snapshot_paths(args, every=every) if not p.exists()]
    if missing:
        raise SystemExit(f"No snapshot at {', '.join(missing)}; run devex fetch first")
    try:
        return tuple(
            real.build(github_source.load_snapshot(p), baseline=args.baseline, current=args.current)
            for p in _snapshot_paths(args, every=every)
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error


def cmd_compare(args: argparse.Namespace) -> str:
    return real.compare_markdown(_real_views(args, every=True))


def cmd_fetch(args: argparse.Namespace) -> str:
    repos = args.repo or ()
    if len(repos) != 1 or not (args.since and args.until) or repos[0].count("/") != 1:
        raise SystemExit("fetch needs --repo OWNER/NAME, --since YYYY-MM-DD and --until YYYY-MM-DD")
    repo = repos[0]
    window = metrics.Window(datetime.fromisoformat(args.since), datetime.fromisoformat(args.until))
    path = _snapshot_path(args)
    base = github_source.load_snapshot(path) if path.exists() else None
    if args.into:
        _merged(args, base, window, "", "", ())  # fail before the network, not after
    branch, records = github_source.fetch(
        repo, window, log=lambda line: print(line, file=sys.stderr)
    )
    fetched_on = args.fetched_on or date.today().isoformat()
    snapshot = _merged(args, base, window, fetched_on, branch, records)
    github_source.write_snapshot(snapshot, path)
    return (
        f"Fetched {len(records):,} merged PRs from {repo} ({args.since} to {args.until}); "
        f"{path} now holds {len(snapshot.records):,} PRs in {len(snapshot.windows)} window(s)."
    )


def _merged(
    args: argparse.Namespace,
    base: github_source.Snapshot | None,
    window: metrics.Window,
    fetched_on: str,
    branch: str,
    records: tuple[dict, ...],
) -> github_source.Snapshot:
    repo = args.repo[0]
    if not args.into:
        return github_source.merge_snapshot(base, repo, branch, window, fetched_on, records)
    if base is None or base.repo.lower() != args.into.lower():
        raise SystemExit(f"--into needs an existing snapshot of {args.into}")
    try:
        return github_source.merge_predecessor(
            base, repo, window, fetched_on, args.moved_to or "", records
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error


def cmd_report(args: argparse.Namespace) -> str:
    if args.source == "real":
        return "\n\n".join(map(real.to_markdown, _real_views(args, every=False)))
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


def _write(out: Path, html: str) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return f"Dashboard written to {out}"


def _real_dashboards(args: argparse.Namespace) -> str:
    """One page per snapshot (all committed ones by default), each with the cross-repo table of
    every committed snapshot, so a page reads the same however it was regenerated."""
    views = _real_views(args, every=True)
    if args.out and len(views) > 1:
        raise SystemExit("--out needs a single --repo or --snapshot")
    built = {v.snapshot.repo: v for v in views}
    peers = (
        views
        if args.snapshot
        else tuple(
            built.get(s.repo) or real.build(s, baseline=args.baseline, current=args.current)
            for s in map(github_source.load_snapshot, committed_snapshots())
        )
    )
    return "\n".join(
        _write(
            Path(args.out or Path("docs") / dashboard_real.page_name(v.snapshot.repo)),
            dashboard_real.render(v, peers),
        )
        for v in views
    )


def cmd_dashboard(args: argparse.Namespace) -> str:
    if args.source == "real":
        return _real_dashboards(args)
    return _write(Path(args.out or "docs/dashboard.html"), dashboard.render(dashboard_data(args)))


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
        help="dashboard output path (default docs/dashboard.html; with --source real, one "
        "docs/dashboard-real*.html page per snapshot)",
    )
    parser.add_argument(
        "--source",
        choices=("synthetic", "real"),
        default="synthetic",
        help="report/dashboard: synthetic rollout, or the committed real-PR snapshot",
    )
    parser.add_argument("--snapshot", help=f"real-PR snapshot path (default {SNAPSHOT.name})")
    parser.add_argument(
        "--repo",
        action="append",
        help="public GitHub repo as OWNER/NAME; fetch takes one, report/dashboard/compare "
        "take any number (repeat the flag); its snapshot is data/real/OWNER-NAME.json",
    )
    parser.add_argument("--since", help="fetch: window start date, inclusive (YYYY-MM-DD)")
    parser.add_argument("--until", help="fetch: window end date, exclusive (YYYY-MM-DD)")
    parser.add_argument("--fetched-on", help="fetch: date to record (default today)")
    parser.add_argument(
        "--into",
        help="fetch: add --repo as a predecessor of this OWNER/NAME snapshot, same window",
    )
    parser.add_argument(
        "--moved-to", help="fetch --into: path where the predecessor's code now lives"
    )
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
        "compare": cmd_compare,
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
