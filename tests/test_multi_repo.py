"""Several repositories: snapshot and page naming, the cross-repo table, and the CLI, offline."""

import dataclasses

import pytest
from conftest import REPO

from devex import cli, dashboard_real, dashboard_spend, github_source, real
from devex.cli import main

OTHER = "dotnet/runtime"


@pytest.fixture(scope="module")
def views(three_window_snapshot, small_snapshot) -> tuple[real.RealView, real.RealView]:
    other = dataclasses.replace(small_snapshot, repo=OTHER)
    return real.build(three_window_snapshot), real.build(other)


@pytest.fixture
def data_dir(tmp_path, monkeypatch, three_window_snapshot, small_snapshot):
    """A data/real folder with both repositories, and docs/ as the working directory."""
    folder = tmp_path / "data" / "real"
    folder.mkdir(parents=True)
    github_source.write_snapshot(three_window_snapshot, folder / "microsoft-vscode.json")
    other = dataclasses.replace(small_snapshot, repo=OTHER)
    github_source.write_snapshot(other, folder / "dotnet-runtime.json")
    monkeypatch.setattr(cli, "DATA", tmp_path / "data")
    monkeypatch.setattr(cli, "SNAPSHOT", folder / "microsoft-vscode.json")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    ("repo", "name"),
    [(REPO, "dashboard-real.html"), (OTHER, "dashboard-real-dotnet-runtime.html")],
)
def test_page_name_keeps_the_first_example_url_stable(repo, name):
    assert dashboard_real.page_name(repo) == name


def test_committed_snapshots_list_the_first_example_first(data_dir):
    assert [p.name for p in cli.committed_snapshots()] == [
        "microsoft-vscode.json",
        "dotnet-runtime.json",
    ]


def test_compare_cell_adds_the_reference_change_when_there_is_one(views):
    with_ref, without = views
    cell = real.compare_cell(with_ref, "ai_assisted_share", "{:.0%}")
    assert cell.endswith(f" vs {with_ref.reference.label})")
    assert real.compare_cell(without, "merged_prs", "{:,}") == "4 → 8 (+100%)"


def test_compare_markdown_puts_one_column_per_repository(views):
    out = real.compare_markdown(views)
    lines = out.splitlines()
    assert lines[0] == "## Real data across repositories"
    assert f"| Metric | [{REPO}](https://github.com/{REPO}) | [{OTHER}]" in out
    assert all(line.count("|") == 4 for line in lines if line.startswith("|"))
    assert "| Merged PRs | " in out and "| Token cost per merged PR | not observable |" in out
    vscode, other = views
    assert (
        f"| Windows | {vscode.before_label} → {vscode.after_label} (reference "
        f"{vscode.reference.label}) | {other.before_label} → {other.after_label} |"
    ) in out


def test_render_compares_repositories_only_with_peers(views):
    vscode, other = views
    assert "Across repositories" not in dashboard_real.render(vscode)
    html = dashboard_real.render(vscode, views)
    assert "Across repositories" in html and "microsoft/vscode (this page)" in html
    assert '<a href="dashboard-real-dotnet-runtime.html">dotnet/runtime</a>' in html
    assert '<a href="dashboard-real.html">microsoft/vscode</a>' in dashboard_real.render(
        other, views
    )


def test_cli_report_takes_several_repos(data_dir, capsys):
    assert main(["report", "--source", "real", "--repo", REPO, "--repo", OTHER]) == 0
    out = capsys.readouterr().out
    assert f"## Real data: {REPO}" in out and f"## Real data: {OTHER}" in out


def test_cli_report_defaults_to_the_first_example(data_dir, capsys):
    assert main(["report", "--source", "real"]) == 0
    assert OTHER not in capsys.readouterr().out


def test_cli_compare_defaults_to_every_committed_snapshot(data_dir, capsys):
    assert main(["compare"]) == 0
    out = capsys.readouterr().out
    assert f"[{REPO}]" in out and f"[{OTHER}]" in out


def test_cli_dashboard_writes_one_linked_page_per_snapshot(data_dir, capsys):
    assert main(["dashboard", "--source", "real"]) == 0
    names = ("dashboard-real.html", "dashboard-real-dotnet-runtime.html")
    assert all("Across repositories" in (data_dir / "docs" / n).read_text() for n in names)
    assert capsys.readouterr().out.count("Dashboard written to") == 2


def test_cli_dashboard_for_one_repo_still_compares_with_the_others(data_dir):
    assert main(["dashboard", "--source", "real", "--repo", OTHER]) == 0
    html = (data_dir / "docs" / "dashboard-real-dotnet-runtime.html").read_text()
    assert '<a href="dashboard-real.html">microsoft/vscode</a>' in html
    assert not (data_dir / "docs" / "dashboard-real.html").exists()


def test_cli_dashboard_out_needs_a_single_repo(data_dir):
    with pytest.raises(SystemExit, match="--out needs a single"):
        main(["dashboard", "--source", "real", "--out", "x.html"])


def test_cli_reports_a_missing_snapshot(data_dir):
    with pytest.raises(SystemExit, match=r"No snapshot at .*o-missing\.json"):
        main(["report", "--source", "real", "--repo", "o/missing"])


def test_cli_fetch_takes_exactly_one_repo():
    with pytest.raises(SystemExit, match="fetch needs"):
        main(["fetch", "--repo", REPO, "--repo", OTHER, "--since", "2025-01-01", "--until", "x"])


def test_real_dashboards_leave_token_economics_blank(views):
    for view in views:
        html = dashboard_real.render(view, views)
        section = html[html.index("Token economics: needs telemetry") :]
        section = section[: section.index("Limits of this evidence")]
        assert section.count("needs telemetry") == len(dashboard_spend.BLANK_TILES) + 1
        assert dashboard_spend.DOC_URL in section and "$" not in section
