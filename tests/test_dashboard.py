import re
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime

import pytest

from devex import dashboard, synthetic
from devex.cli import build_parser, dashboard_data, main
from devex.dashboard import Bar, Kpi, Series
from devex.metrics import PullRequest, Scorecard, Window


def _card(**overrides) -> Scorecard:
    base = dict(
        engineers=10,
        weeks=13.0,
        merged_prs=100,
        prs_per_engineer_week=2.0,
        quality_adjusted_prs_per_engineer_week=1.8,
        change_failure_rate=0.1,
        median_cycle_time_hours=20.0,
        median_review_latency_hours=10.0,
        median_pr_size_lines=80.0,
        ai_assisted_share=0.2,
        token_cost_usd=100.0,
        cost_per_merged_pr_usd=1.0,
    )
    return Scorecard(**{**base, **overrides})


@pytest.fixture(scope="module")
def data() -> dashboard.DashboardData:
    return dashboard_data(build_parser().parse_args(["dashboard"]))


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 1.0), (-3, 1.0), (0.07, 0.1), (0.13, 0.2), (2.2, 2.5), (3.1, 5), (7, 10), (10, 10)],
)
def test_nice_max_rounds_up_to_a_round_number(value, expected):
    assert dashboard.nice_max(value) == pytest.approx(expected)


def test_weekly_windows_cover_whole_weeks_only():
    window = Window(datetime(2026, 1, 5), datetime(2026, 1, 25))
    weeks = dashboard.weekly_windows(window)
    assert len(weeks) == 2
    assert weeks[0].start == window.start
    assert weeks[1].end == datetime(2026, 1, 19)


def test_weekly_scorecards_split_merges_by_week():
    window = Window(datetime(2026, 1, 5), datetime(2026, 1, 19))
    pr = PullRequest(
        id="1",
        author="a",
        opened_at=datetime(2026, 1, 6),
        merged_at=datetime(2026, 1, 13),
        first_review_at=datetime(2026, 1, 7),
        lines_changed=10,
        ai_assisted=False,
    )
    first, second = dashboard.weekly_scorecards((pr,), 1, window)
    assert (first.merged_prs, second.merged_prs) == (0, 1)
    assert second.prs_per_engineer_week == pytest.approx(1.0)


def test_line_chart_is_valid_svg_with_one_polyline_per_series():
    svg = dashboard.line_chart(
        (Series("a", (1.0, 2.0, 3.0), "#000"), Series("b<", (2.0, 2.0, 2.0), "#111", dashed=True)),
        x_labels=("W1", "W2", "W3"),
        y_format="{:.1f}",
        title="Throughput & more",
    )
    root = ET.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    assert len(root.findall(f"{ns}polyline")) == 2
    assert len(root.findall(f"{ns}circle")) == 6
    assert "stroke-dasharray" in svg
    assert "b&lt;" in svg and "Throughput &amp; more" in svg


def test_line_chart_puts_the_max_value_inside_the_plot():
    svg = dashboard.line_chart(
        (Series("a", (0.0, 10.0), "#000"),), x_labels=("W1", "W2"), y_format="{:.0f}", title="t"
    )
    ys = [float(v) for v in re.findall(r'cy="([\d.]+)"', svg)]
    assert min(ys) > 14  # below the top padding
    assert max(ys) == pytest.approx(210.0)  # zero sits on the x axis


def test_bar_chart_scales_bars_and_caps_overflow():
    svg = dashboard.bar_chart(
        (Bar("half", 0.5, "#000", "50%"), Bar("over", 2.0, "#000", "200%")),
        title="t",
        scale=1.0,
        width=300,
        label_w=100,
    )
    ET.fromstring(svg)
    plot_w = 300 - 100 - 70
    widths = [float(w) for w in re.findall(r'width="([\d.]+)" height="18" rx="3"', svg)]
    assert widths == [pytest.approx(plot_w / 2), pytest.approx(plot_w)]


@pytest.mark.parametrize(
    ("direction", "after", "color"),
    [
        (1, 3.0, dashboard.GOOD),
        (1, 1.0, dashboard.BAD),
        (-1, 1.0, dashboard.GOOD),
        (-1, 3.0, dashboard.BAD),
        (0, 3.0, dashboard.NEUTRAL),
    ],
)
def test_kpi_tile_colours_delta_by_direction(direction, after, color):
    kpi = Kpi("Throughput", "prs_per_engineer_week", "{:.2f}", direction)
    html = dashboard.kpi_tile(kpi, _card(), _card(prs_per_engineer_week=after))
    assert f"color:{color}" in html
    assert "vs baseline 2.00" in html


def test_render_is_self_contained_and_covers_every_section(data):
    html = dashboard.render(data)
    assert html.startswith("<!doctype html>")
    assert not re.search(r'(src|href)="(https?:)?//', html)
    assert "<script" not in html and "<link" not in html
    for text in (
        "Synthetic / illustrative data",
        "North star and guardrails",
        "Weekly trend",
        "Goodhart guards",
        "Code-review agents",
        "Noise per PR",
        "Eval-driven model routing",
        "Pass-rate trade-off",
        "Build vs partner",
        "Verdict:",
    ):
        assert text in html
    assert html.count('class="tile') >= len(dashboard.KPIS)
    assert html.count("<polyline") == 4


def test_render_is_deterministic(data):
    assert dashboard.render(data) == dashboard.render(
        dashboard_data(build_parser().parse_args(["dashboard"]))
    )


def test_weekly_series_match_the_quarter(data):
    assert len(data.weekly_before) == int(synthetic.QUARTER_1.weeks)
    assert len(data.weekly_after) == int(synthetic.QUARTER_2.weeks)


def test_signals_render_as_alerts_or_all_clear(data):
    assert "No Goodhart guard triggered" in dashboard.render(replace(data, signals=()))
    assert dashboard.render(replace(data, signals=("x",))).count('class="alert warn"') == 1


def test_cli_dashboard_writes_html(tmp_path, capsys):
    out = tmp_path / "nested" / "dash.html"
    assert main(["dashboard", "--out", str(out)]) == 0
    assert str(out) in capsys.readouterr().out
    first = out.read_text(encoding="utf-8")
    main(["dashboard", "--out", str(out)])
    assert out.read_text(encoding="utf-8") == first
    assert "AI-native developer experience" in first
