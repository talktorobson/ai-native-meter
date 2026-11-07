import xml.etree.ElementTree as ET
from datetime import datetime

import pytest
from conftest import AFTER, BEFORE, MID, REPO

from devex import cli, dashboard_real, github_source, real
from devex.cli import main
from devex.metrics import Window


@pytest.fixture(scope="module")
def view(small_snapshot) -> real.RealView:
    return real.build(small_snapshot)


@pytest.mark.parametrize(
    ("start", "end", "label"),
    [
        (datetime(2025, 1, 1), datetime(2025, 4, 1), "Q1 2025"),
        (datetime(2026, 7, 1), datetime(2026, 10, 1), "Q3 2026"),
        (datetime(2025, 10, 1), datetime(2026, 1, 1), "Q4 2025"),
        (datetime(2025, 2, 1), datetime(2025, 5, 1), "2025-02-01 to 2025-05-01"),
        (datetime(2025, 1, 6), datetime(2025, 1, 20), "2025-01-06 to 2025-01-20"),
    ],
)
def test_window_label_names_calendar_quarters(start, end, label):
    assert real.window_label(Window(start, end)) == label


def test_build_needs_a_baseline_and_a_current_window(small_snapshot):
    one = github_source.Snapshot(REPO, "main", small_snapshot.windows[:1], small_snapshot.records)
    with pytest.raises(ValueError, match="two fetched windows"):
        real.build(one)


def test_build_scores_both_windows(view):
    assert (view.before.merged_prs, view.after.merged_prs) == (4, 8)
    assert (view.before.engineers, view.after.engineers) == (3, 5)
    assert view.before.change_failure_rate == pytest.approx(1 / 4)
    assert view.after.change_failure_rate == pytest.approx(2 / 8)
    assert view.before.ai_assisted_share == 0
    assert view.after.ai_assisted_share == pytest.approx(6 / 8)
    assert view.after.token_cost_usd == 0
    assert view.fetched_on == "2026-11-07"


def test_cohorts_split_by_strongest_evidence(view):
    after = view.cohorts_after
    assert list(after) == ["agent-authored", "AI co-authored", "AI review fix only", "no AI signal"]
    assert {k: c.merged_prs for k, c in after.items()} == {
        "agent-authored": 2,
        "AI co-authored": 3,
        "AI review fix only": 1,
        "no AI signal": 2,
    }
    assert after["agent-authored"].engineers == 2
    assert after["AI co-authored"].change_failure_rate == pytest.approx(2 / 3)
    assert list(view.cohorts_before) == ["no AI signal"]


def test_evidence_counts_are_sorted_by_frequency(view):
    assert view.evidence_after[0] == ("agent:copilot-swe-agent", 2)
    assert dict(view.evidence_after)["marker:claude"] == 1


@pytest.mark.parametrize(
    ("before", "after", "fmt", "text"),
    [
        (0.0, 0.66, "{:.0%}", "+66.0 pp"),
        (0.007, 0.0067, "{:.1%}", "-0.0 pp"),
        (1.80, 1.96, "{:.2f}", "+9%"),
        (0, 12, "{:,}", "+0%"),
    ],
)
def test_change_uses_points_for_rates_and_relative_change_otherwise(before, after, fmt, text):
    assert real.change(before, after, fmt) == text


def test_real_dashboard_shows_rate_changes_in_points(view):
    html = dashboard_real.render(view)
    assert " pp</span> vs baseline" in html


def test_markdown_reports_scorecards_cohorts_evidence_guards_and_limits(view):
    text = real.to_markdown(view)
    for needle in (
        "## Real data: microsoft/vscode, 2025-01-06 to 2025-01-20 vs 2026-07-06 to 2026-07-20",
        "https://github.com/microsoft/vscode",
        "| Revert rate (change failure proxy) | 25.0% | 25.0% | +0.0 pp |",
        "| Token cost per merged PR | not observable |",
        "| agent-authored | 2 |",
        "`agent:copilot-swe-agent`",
        "**Goodhart guards**",
        "**Limits**",
        "Correlation is not causation",
    ):
        assert needle in text


def test_real_dashboard_labels_its_source_and_has_valid_svg(view):
    html = dashboard_real.render(view)
    assert "Real public data" in html and "Synthetic" not in html
    assert 'href="https://github.com/microsoft/vscode"' in html
    assert "Fetched <b>2026-11-07</b>" in html
    assert all(k.label in html for k in dashboard_real.KPIS)
    assert all(name in html for name in github_source.COHORTS)
    for svg in html.split("<svg")[1:]:
        ET.fromstring("<svg" + svg.split("</svg>")[0] + "</svg>")


def test_cli_report_runs_on_a_snapshot(snapshot_file, capsys):
    assert main(["report", "--source", "real", "--snapshot", str(snapshot_file)]) == 0
    assert "## Real data: microsoft/vscode" in capsys.readouterr().out


def test_cli_dashboard_writes_the_real_dashboard(snapshot_file, tmp_path, capsys):
    out = tmp_path / "real.html"
    args = ["dashboard", "--source", "real", "--snapshot", str(snapshot_file), "--out", str(out)]
    assert main(args) == 0
    assert "Real public data" in out.read_text(encoding="utf-8")
    assert str(out) in capsys.readouterr().out


def test_cli_dashboard_defaults_to_the_real_output_path():
    args = cli.build_parser().parse_args(["dashboard", "--source", "real"])
    assert args.out is None and cli._snapshot_path(args) == cli.SNAPSHOT


def test_cli_fetch_merges_a_window_into_the_snapshot(monkeypatch, nodes, tmp_path, capsys):
    def fake_fetch(repo, window, *, run=None, log=print):
        log("1 PRs")
        return "main", (github_source.to_record(nodes[201], repo),)

    monkeypatch.setattr(github_source, "fetch", fake_fetch)
    path = tmp_path / "x.json"
    common = ["fetch", "--repo", REPO, "--snapshot", str(path), "--fetched-on", "2026-11-07"]
    main([*common, "--since", "2025-01-01", "--until", "2025-04-01"])
    main([*common, "--since", "2026-07-01", "--until", "2026-10-01"])
    snapshot = github_source.load_snapshot(path)
    assert len(snapshot.windows) == 2 and len(snapshot.records) == 1
    assert "now holds 1 PRs in 2 window(s)" in capsys.readouterr().out


def test_cli_fetch_derives_the_snapshot_path_from_the_repo():
    args = cli.build_parser().parse_args(["fetch", "--repo", "Owner/Name"])
    assert cli._snapshot_path(args) == cli.DATA / "real" / "owner-name.json"


@pytest.mark.parametrize(
    "extra", [[], ["--repo", REPO], ["--repo", "vscode", "--since", "2025-01-01", "--until", "x"]]
)
def test_cli_fetch_validates_its_arguments(extra):
    with pytest.raises(SystemExit, match="fetch needs"):
        main(["fetch", *extra])


def test_committed_snapshot_reports_offline(capsys):
    assert main(["report", "--source", "real"]) == 0
    out = capsys.readouterr().out
    assert "microsoft/vscode, Q3 2025 vs Q3 2026 (pre-AI reference Q1 2025)" in out


def test_pick_windows_defaults_to_the_same_season_a_year_earlier():
    assert real.pick_windows((BEFORE, MID, AFTER)) == (BEFORE, MID, AFTER)
    assert real.pick_windows((AFTER, BEFORE, MID)) == (BEFORE, MID, AFTER)


def test_pick_windows_falls_back_to_the_earliest_window_without_a_reference():
    assert real.pick_windows((BEFORE, AFTER)) == (None, BEFORE, AFTER)


@pytest.mark.parametrize(
    ("baseline", "current", "expected"),
    [
        ("2025-01-06", None, (None, BEFORE, AFTER)),
        (None, "2025-07-06", (None, BEFORE, MID)),
        ("2025-07-06", "2026-07-06", (BEFORE, MID, AFTER)),
    ],
)
def test_pick_windows_honours_explicit_choices(baseline, current, expected):
    assert real.pick_windows((BEFORE, MID, AFTER), baseline, current) == expected


@pytest.mark.parametrize(
    ("baseline", "current", "error"),
    [
        ("2024-01-01", None, "no fetched window starts on 2024-01-01"),
        ("2026-07-06", None, "must be different"),
    ],
)
def test_pick_windows_rejects_unknown_or_identical_windows(baseline, current, error):
    with pytest.raises(ValueError, match=error):
        real.pick_windows((BEFORE, MID, AFTER), baseline, current)


def test_build_scores_reference_baseline_and_current(three_window_snapshot):
    view = real.build(three_window_snapshot)
    assert [p.role for p in view.periods] == ["reference", "baseline", "current"]
    assert (view.reference.window, view.baseline.window, view.current.window) == (
        BEFORE,
        MID,
        AFTER,
    )
    assert view.before.merged_prs == 2 and view.before.ai_assisted_share == pytest.approx(0.5)
    assert view.reference.card.ai_assisted_share == 0
    assert dict(view.baseline.evidence) == {"agent:copilot-swe-agent": 1}
    assert isinstance(view.signals_reference, tuple)


def test_two_windows_have_no_reference_guards(view):
    assert view.reference is None and view.signals_reference == ()


def test_markdown_compares_current_with_baseline_and_reference(three_window_snapshot):
    text = real.to_markdown(real.build(three_window_snapshot))
    b, m, a = "2025-01-06 to 2025-01-20", "2025-07-06 to 2025-07-20", "2026-07-06 to 2026-07-20"
    assert f"## Real data: microsoft/vscode, {m} vs {a} (pre-AI reference {b})" in text
    assert f"| Metric | {b} (reference) | {m} (baseline) | {a} (current) " in text
    assert f"| {a} vs {m} | {a} vs {b} |" in text
    assert "| Merged PRs | 4 | 2 | 8 | +300% | +100% |" in text
    assert f"| Evidence | {b} | {m} | {a} |" in text
    assert "| `agent:copilot-swe-agent` | 0 | 1 | 2 |" in text
    assert f"- {a} vs {m}:" in text and f"- {a} vs {b}:" in text
    assert text.count(f"| {m} | ") >= 2  # cohort rows for the baseline window


def test_real_dashboard_draws_every_window(three_window_snapshot):
    html = dashboard_real.render(real.build(three_window_snapshot))
    assert "pre-AI reference <b>2025-01-06 to 2025-01-20</b>" in html
    for role in ("reference", "baseline", "current"):
        assert f"({role})</span>" in html
    assert html.count('stroke-dasharray="6 4"') == 2 * 4  # two earlier windows, four trends


def test_cli_report_accepts_an_explicit_baseline(three_window_file, capsys):
    args = ["report", "--source", "real", "--snapshot", str(three_window_file)]
    assert main([*args, "--baseline", "2025-01-06"]) == 0
    assert "2025-01-06 to 2025-01-20 vs 2026-07-06 to 2026-07-20\n" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="no fetched window starts on 1999-01-01"):
        main([*args, "--baseline", "1999-01-01"])
