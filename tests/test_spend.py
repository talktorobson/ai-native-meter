from datetime import date, datetime, timedelta

import pytest

from devex import metrics, spend, synthetic_usage, usage
from devex.cli import PRICES, USAGE_EVENTS, main
from devex.usage import Costed, UsageEvent

T0 = datetime(2026, 5, 4, 10)
TABLE = usage.load_prices(PRICES)
TIER_MODEL = {"frontier-large": "claude-opus-4", "mid-tier": "claude-sonnet-4"}


def _c(
    usd: float,
    *,
    days: int = 0,
    tier: str = "mid-tier",
    team: str = "payments",
    engineer: str = "eng-000001",
    surface: str = "coding_agent",
    task_id: str = "",
    task_type: str = "chat",
    pr: int | None = None,
    outcome: str | None = None,
    task_class: str | None = None,
    tokens: tuple[int, int, int] = (1000, 100, 0),
) -> Costed:
    event = UsageEvent(
        T0 + timedelta(days=days),
        team,
        engineer,
        surface,
        "anthropic",
        TIER_MODEL.get(tier, "claude-3-5-haiku"),
        *tokens,
        task_id=task_id,
        task_type=task_type,
        repo="acme/app" if pr else "",
        pr=pr,
        outcome=outcome,
        task_class=task_class,
    )
    return Costed(event, usd, tier)


@pytest.fixture(scope="module")
def demo():
    return usage.cost(usage.load_events(USAGE_EVENTS), TABLE)


@pytest.fixture(scope="module")
def report(demo):
    return spend.analyze(
        demo,
        TABLE,
        budget_usd=synthetic_usage.BUDGET_USD,
        split=synthetic_usage.ROUTING_CHANGE,
    )


def test_breakdown_shares_add_up_and_sort_by_spend():
    items = (_c(3, tier="frontier-large"), _c(1), _c(1, team="data"))
    by_tier = spend.breakdown(items, "tier")
    assert [s.key for s in by_tier] == ["frontier-large", "mid-tier"]
    assert sum(s.usd_share for s in by_tier) == pytest.approx(1)
    assert by_tier[0].usd_share == pytest.approx(0.6) and by_tier[0].token_share == pytest.approx(
        1 / 3
    )
    assert {s.key for s in spend.breakdown(items, "team")} == {"payments", "data"}
    with pytest.raises(ValueError):
        spend.breakdown(items, "colour")


def test_tasks_group_by_id_and_skip_untracked_events():
    items = (
        _c(1, task_id="a", task_type="agent_session", tier="frontier-large"),
        _c(0.5, task_id="a", task_type="agent_session", days=1, outcome="merged"),
        _c(2, task_id="r", task_type="review"),
        _c(9),
    )
    found = {t.task_id: t for t in spend.tasks(items)}
    assert set(found) == {"a", "r"}
    assert (found["a"].usd, found["a"].steps, found["a"].outcome) == (1.5, 2, "merged")
    assert found["a"].model == "claude-opus-4" and found["a"].start == T0
    by_type = {t.task_type: t for t in spend.per_task_type(tuple(found.values()))}
    assert by_type["agent_session"].usd_per_task == 1.5 and by_type["review"].tasks == 1


def test_a_reverted_merge_is_a_reverted_change():
    items = (
        _c(1, pr=1, task_type="pr", outcome="merged"),
        _c(1, pr=1, task_type="review", days=3, outcome="reverted"),
    )
    (change,) = spend.changes(items)
    assert change.key == "acme/app#1" and change.outcome == "reverted" and not change.accepted


def test_outcomes_divide_concluded_spend_by_accepted_changes():
    items = (
        _c(4, pr=1, task_type="pr", outcome="merged"),
        _c(2, pr=1, task_type="review"),  # AI review of the same PR counts towards it
        _c(3, pr=2, task_type="pr", outcome="merged"),
        _c(5, pr=3, task_type="pr", outcome="reverted"),
        _c(1, task_id="s9", task_type="agent_session", outcome="abandoned"),
        _c(7, pr=4, task_type="pr"),  # in flight: on neither side
        _c(6, task_id="inc", task_type="incident", outcome="resolved"),
        _c(2, task_id="inc2", task_type="incident"),
        _c(10),  # chat with no task: only in the all-in variant
    )
    o = spend.outcomes(items)
    assert (o.accepted, o.reverted, o.abandoned, o.in_flight) == (2, 1, 1, 1)
    assert o.cost_per_accepted_change == pytest.approx((4 + 2 + 3 + 5 + 1) / 2)
    assert o.all_in_cost_per_accepted_change == pytest.approx((40 - 8) / 2)
    assert (o.waste_reverted_usd, o.waste_abandoned_usd, o.in_flight_usd) == (5, 1, 7)
    assert o.waste_share == pytest.approx(6 / 40)
    assert o.revert_rate == pytest.approx(1 / 3)
    assert (o.incidents, o.incidents_resolved, o.cost_per_resolved_incident) == (2, 1, 8)


def test_outcomes_without_accepted_changes_do_not_divide_by_zero():
    o = spend.outcomes((_c(1, pr=1, task_type="pr", outcome="abandoned"),))
    assert o.cost_per_accepted_change == 0 and o.waste_share == 1


def test_cache_ratio_and_frontier_share():
    items = (
        _c(3, tier="frontier-large", tokens=(1000, 0, 750)),
        _c(1, tokens=(3000, 0, 250)),
    )
    assert spend.cache_ratio(items) == pytest.approx(1000 / 4000)
    assert spend.frontier_share(items) == pytest.approx((0.75, 0.25))


def test_routing_opportunity_reprices_only_what_the_evals_allow():
    items = (
        _c(0, tier="frontier-large", task_class="trivial", tokens=(100_000, 1000, 0)),
        _c(0, tier="frontier-large", task_class="complex", tokens=(100_000, 1000, 0)),
        _c(0, tier="frontier-large", tokens=(100_000, 1000, 0)),
        _c(0, task_class="trivial"),
    )
    costed = tuple(
        Costed(c.event, usd, c.tier) for c, usd in zip(items, (1.575, 1.575, 1.575, 1), strict=True)
    )
    result = spend.routing_opportunity(costed, TABLE)
    cheapest_small = (100_000 * 0.4 + 1000 * 1.6) / 1e6  # gpt-4.1-mini beats haiku
    assert result.frontier_usd == pytest.approx(4.725)
    assert result.classified_usd == pytest.approx(3.15)
    assert result.unclassified_usd == pytest.approx(1.575)
    assert result.routable_usd == pytest.approx(1.575)
    assert result.savings_usd == pytest.approx(1.575 - cheapest_small)
    assert list(result.by_task_type) == ["chat"]


def test_engineer_minutes_and_quarter():
    assert spend.engineer_minutes(5, 100) == pytest.approx(3)
    assert spend.engineer_minutes(5, 0) == 0
    assert spend.quarter(date(2026, 5, 4)) == (date(2026, 4, 1), date(2026, 7, 1))
    assert spend.quarter(date(2026, 11, 30)) == (date(2026, 10, 1), date(2027, 1, 1))


def test_budget_forecast_is_linear_on_the_trailing_run_rate():
    days = tuple(_c(10, days=d) for d in range(28))  # 2026-05-04 .. 2026-05-31
    b = spend.budget_forecast(days, 500, as_of=date(2026, 5, 31))
    assert (b.start, b.end, b.spent_usd, b.daily_run_rate) == (
        date(2026, 4, 1),
        date(2026, 7, 1),
        280,
        10,
    )
    assert b.projected_usd == pytest.approx(280 + 10 * 30)
    assert b.exhausted_on == date(2026, 5, 31) + timedelta(days=22)
    assert b.burn == pytest.approx(0.56) and b.projected_share == pytest.approx(580 / 500)
    assert spend.budget_forecast(days, 1000, as_of=date(2026, 5, 31)).exhausted_on is None
    assert spend.budget_forecast(days, 100, as_of=date(2026, 5, 31)).exhausted_on == date(
        2026, 5, 14
    )


def test_anomalies_find_runaway_loops_and_cost_outliers():
    def task(name: str, usd: float, steps: int, task_type: str = "agent_session") -> spend.Task:
        return spend.Task(name, task_type, "data", "eng-000001", T0, usd, 1, steps, None, "m")

    normal = tuple(task(f"s{i}", 1.0, 6) for i in range(12))
    reviews = tuple(task(f"r{i}", 0.5, 1, "review") for i in range(12))
    found = spend.anomalies(
        (*normal, *reviews, task("loop", 9.0, 60), task("big", 0.5 * 9, 1, "review"))
    )
    assert [(a.kind, a.task.task_id) for a in found] == [("runaway", "loop"), ("cost", "big")]
    assert found[0].threshold == 25


def test_top_spenders_are_pseudonymous_with_cost_per_accepted_change():
    items = (
        _c(6, engineer="eng-00000a", pr=1, task_type="pr", outcome="merged"),
        _c(3, engineer="eng-00000a", pr=2, task_type="pr", outcome="merged"),
        _c(1, engineer="eng-00000b"),
    )
    first, second = spend.top_spenders(items)
    assert (first.engineer, first.usd, first.accepted, first.cost_per_accepted_change) == (
        "eng-00000a",
        9,
        2,
        4.5,
    )
    assert second.share == pytest.approx(0.1) and second.cost_per_accepted_change == 0
    assert len(spend.top_spenders(items, 1)) == 1


def test_weekly_buckets_start_on_monday():
    weeks, series = spend.weekly((_c(1), _c(2, days=6), _c(4, days=7, tier="frontier-large")))
    assert weeks == (date(2026, 5, 4), date(2026, 5, 11))
    assert series == {"frontier-large": (0.0, 4.0), "mid-tier": (3.0, 0.0)}


def test_period_keeps_a_straddling_change_whole_where_it_started():
    items = (
        _c(2, pr=1, task_type="pr", days=-1),
        _c(3, pr=1, task_type="review", days=1, outcome="merged"),
        _c(1, days=1),
    )
    before = spend.period(items, "before", date(2026, 5, 1), date(2026, 5, 4))
    after = spend.period(items, "after", date(2026, 5, 4), date(2026, 5, 8))
    assert (before.usd, before.outcomes.accepted, before.days) == (5, 1, 3)
    assert (after.usd, after.outcomes.accepted, after.routing) == (1, 0, None)
    assert before.usd_per_day == pytest.approx(5 / 3)


def _team(team: str, cost: float, reverts: int, start: int, days: int) -> tuple[Costed, ...]:
    return tuple(
        _c(
            cost,
            team=team,
            pr=start + i,
            task_type="pr",
            days=days,
            outcome="reverted" if i < reverts else "merged",
        )
        for i in range(10)
    )


def test_team_guards_flag_cheaper_but_worse_teams_only():
    items = (
        *_team("steady", 4, 0, 0, -3),
        *_team("steady", 2, 0, 100, 1),
        *_team("cutter", 4, 0, 200, -3),
        *_team("cutter", 1, 3, 300, 1),
    )
    guards = {g.team: g for g in spend.team_guards(items, date(2026, 5, 4))}
    assert guards["steady"].signal is None
    assert "savings bought with quality" in guards["cutter"].signal
    assert guards["cutter"].after.revert_rate == pytest.approx(0.3)


def test_cost_quality_signal_is_shared_with_the_scorecard_guards():
    assert metrics.cost_quality_signal(2, 1, 0.05, 0.05) is None
    assert metrics.cost_quality_signal(1, 2, 0.05, 0.20) is None  # dearer: not this guard
    assert "change failure rate" in metrics.cost_quality_signal(2, 1, 0.05, 0.07)
    assert "review latency" in metrics.cost_quality_signal(2, 1, 0.05, 0.05, 10, 13)
    assert metrics.cost_quality_signal(2, 1, 0.05, 0.05, 10, 12) is None


def test_demo_story_cuts_cost_per_accepted_change_without_hiding_quality(report):
    before, after = report.periods
    assert after.outcomes.cost_per_accepted_change < 0.5 * before.outcomes.cost_per_accepted_change
    assert after.frontier_usd_share < before.frontier_usd_share
    assert after.cache_ratio > before.cache_ratio
    assert before.routing.savings_usd > 0 and after.routing.savings_usd < 1
    assert report.org_signal is None
    assert [g.team for g in report.guards if g.signal] == ["mobile"]
    assert report.budget.projected_usd < report.budget.budget_usd
    assert {a.kind for a in report.anomalies} == {"runaway"}
    assert all(s.engineer.startswith("eng-") for s in report.spenders)
    assert sum(s.usd_share for s in report.mix) == pytest.approx(1)


def test_to_markdown_leads_with_cost_per_accepted_change(report):
    text = spend.to_markdown(report)
    assert "Cost per accepted change" in text
    assert text.index("Cost per accepted change") < text.index("Cache")
    assert "mobile" in text and "savings bought with quality" in text


@pytest.mark.parametrize("by", ["model", "team", "surface", "task"])
def test_cli_spend_breaks_down_by_each_key(capsys, by):
    assert main(["spend", "--by", by]) == 0
    assert "Cost per accepted change" in capsys.readouterr().out


def test_cli_spend_reads_a_custom_csv_without_demo_defaults(tmp_path, capsys):
    path = tmp_path / "events.csv"
    usage.write_events(synthetic_usage.generate()[:300], path)
    assert main(["spend", "--events", str(path), "--budget", "100"]) == 0
    out = capsys.readouterr().out
    assert "Budget" in out and "Before and after" not in out


def test_cli_spend_names_models_missing_from_the_price_table(tmp_path):
    path = tmp_path / "events.jsonl"
    event = UsageEvent(T0, "t", "eng-000001", "ide_chat", "x", "mystery-model", 10, 1)
    usage.write_events((event,), path)
    with pytest.raises(SystemExit, match="mystery-model"):
        main(["spend", "--events", str(path)])


def test_cli_context_budget_still_defaults(capsys):
    assert main(["context"]) == 0
    assert capsys.readouterr().out
