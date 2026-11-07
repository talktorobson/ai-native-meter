from pathlib import Path

import pytest

from devex.review_eval import (
    Defect,
    Finding,
    ReviewCase,
    compare,
    evaluate,
    keyword_reviewer,
    load_cases,
    match,
    rule_reviewer,
    to_markdown,
)

BENCHMARK = Path(__file__).parents[1] / "data" / "review_benchmark.json"


def case(code: str, *defects: Defect, cid: str = "c") -> ReviewCase:
    return ReviewCase(id=cid, title=cid, code=code, defects=tuple(defects))


def test_match_is_one_to_one_with_line_tolerance():
    defects = (Defect(3, "secret", "critical"),)
    findings = (
        Finding(4, "secret", "critical"),
        Finding(3, "secret", "critical"),
        Finding(3, "broad-except", "medium"),
    )
    caught, noise = match(defects, findings, tolerance=1)
    assert caught == defects
    assert len(noise) == 2


def test_match_rejects_far_lines():
    caught, noise = match((Defect(1, "secret", "critical"),), (Finding(5, "secret", "high"),))
    assert caught == ()
    assert len(noise) == 1


def test_evaluate_perfect_reviewer():
    cases = (case("x", Defect(1, "secret", "critical")), case("y"))
    perfect = lambda c: tuple(Finding(d.line, d.category, d.severity) for d in c.defects)  # noqa: E731
    report = evaluate("perfect", perfect, cases, cost_per_case_usd=0.1)
    assert (report.precision, report.recall, report.f_beta) == (1.0, 1.0, 1.0)
    assert report.clean_case_silence_rate == 1.0
    assert report.cost_per_true_positive_usd == pytest.approx(0.2)


def test_evaluate_silent_reviewer_scores_zero_without_dividing_by_zero():
    report = evaluate("silent", lambda _: (), (case("x", Defect(1, "secret", "critical")),))
    assert report.precision == 0.0
    assert report.recall == 0.0
    assert report.f_beta == 0.0
    assert report.false_negatives == 1


def test_severity_weighted_recall_values_critical_more():
    cases = (case("x", Defect(1, "secret", "critical"), Defect(2, "broad-except", "medium")),)
    only_critical = lambda _: (Finding(1, "secret", "critical"),)  # noqa: E731
    report = evaluate("crit", only_critical, cases)
    assert report.recall == 0.5
    assert report.severity_weighted_recall == pytest.approx(5 / 7)


def test_rule_reviewer_catches_fintech_defects():
    code = "\n".join(
        (
            'API_KEY = "abcdefgh12345"',
            "fee = float(total) * 0.02",
            "requests.get(url)",
            "requests.get(url, timeout=2)",
            "except Exception:",
            "logger.info('user cpf=%s', cpf)",
            'cursor.execute(f"SELECT {x}")',
        )
    )
    found = {(f.line, f.category) for f in rule_reviewer(case(code))}
    assert found == {
        (1, "secret"),
        (2, "float-money"),
        (3, "missing-timeout"),
        (5, "broad-except"),
        (6, "pii-logging"),
        (7, "sql-injection"),
    }


def test_benchmark_ranks_precise_reviewer_above_noisy_one():
    cases = load_cases(BENCHMARK)
    assert len(cases) == 10
    assert sum(not c.defects for c in cases) == 3
    reports = compare({"noisy": (keyword_reviewer, 0.0), "rules": (rule_reviewer, 0.0)}, cases)
    assert [r.reviewer for r in reports] == ["rules", "noisy"]
    assert reports[0].precision > reports[1].precision
    assert reports[1].noise_per_pr > reports[0].noise_per_pr


def test_to_markdown_has_row_per_reviewer():
    cases = (case("x", Defect(1, "secret", "critical")),)
    table = to_markdown((evaluate("a", lambda _: (), cases), evaluate("b", lambda _: (), cases)))
    assert table.count("\n") == 3
