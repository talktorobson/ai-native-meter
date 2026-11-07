"""Offline checks that docs/ builds into a coherent GitHub Pages site."""

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs"
PAGES = sorted([*DOCS.glob("*.md"), *DOCS.glob("adr/*.md")])
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
TRANSITION = re.compile(r"\d[\d,.]*%?(?: h)? → \d[\d,.]*%?(?: h)?(?: \(\+\d+%\))?")


def _plain(text: str) -> str:
    return text.replace("**", "")


def _local_links(page: Path) -> list[str]:
    return [
        target
        for target in LINK.findall(page.read_text())
        if not target.startswith(("http://", "https://", "mailto:"))
    ]


def _slug(heading: str) -> str:
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", heading.strip().lower()))


def _anchors(page: Path) -> set[str]:
    return {_slug(h) for h in re.findall(r"^#+\s+(.+)$", page.read_text(), re.MULTILINE)}


def _headline(page: Path) -> str:
    text = page.read_text()
    start = text.index("## On real data")
    return text[start : text.index("\n## ", start + 1)]


CHAPTERS = ("06-real-data.md", "07-dotnet-runtime.md", "08-engineers.md")
DASHBOARDS = ("dashboard.html", "dashboard-real.html", "dashboard-real-dotnet-runtime.html")


def _row(chapter: str, prefix: str) -> str:
    return next(
        line for line in (DOCS / chapter).read_text().splitlines() if line.startswith(prefix)
    )


def test_index_headline_numbers_match_real_data_chapters():
    text = _plain("".join((DOCS / name).read_text() for name in CHAPTERS))
    chapter = re.sub(r"(\d[\d,.]*%?(?: h)?) to (\d)", r"\1 → \2", text)
    transitions = TRANSITION.findall(_plain(_headline(DOCS / "index.md")))

    assert len(transitions) >= 10
    assert [t for t in transitions if t not in chapter] == []


@pytest.mark.parametrize(
    ("chapter", "row", "quotes"),
    [
        ("06-real-data.md", "| PRs / engineer / week | 1.80", ("1.56 → 1.97 (+26%)", "+9%")),
        ("07-dotnet-runtime.md", "| PRs / engineer / week | 0.47", ("0.58 → 0.81 (+41%)", "+71%")),
    ],
)
def test_index_quotes_per_engineer_gain_against_both_baselines(chapter, row, quotes):
    year_over_year, vs_reference = quotes[0].rsplit("(", 1)[1].rstrip(")"), quotes[1]

    assert f"| {year_over_year} | {vs_reference} |" in _row(chapter, row)
    assert all(q in _plain(_headline(DOCS / "index.md")) for q in quotes)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(DOCS)))
def test_local_links_resolve_inside_the_site(page):
    def broken(target: str) -> bool:
        path, _, fragment = target.partition("#")
        resolved = (page.parent / path).resolve() if path else page
        return (
            not resolved.is_relative_to(DOCS)
            or not resolved.is_file()
            or (bool(fragment) and resolved.suffix == ".md" and fragment not in _anchors(resolved))
        )

    assert [t for t in _local_links(page) if broken(t)] == []


def test_index_links_every_doc_adr_and_dashboard():
    linked = {target.partition("#")[0] for target in _local_links(DOCS / "index.md")}
    expected = {str(p.relative_to(DOCS)) for p in PAGES if p.name != "index.md"}

    assert expected | set(DASHBOARDS) <= linked


@pytest.mark.parametrize("name", DASHBOARDS)
def test_dashboards_are_served_verbatim(name):
    assert not (DOCS / name).read_text().startswith("---")


def test_site_config_and_mermaid_wiring():
    config = (DOCS / "_config.yml").read_text()
    layout = (DOCS / "_layouts" / "default.html").read_text()
    include = (DOCS / "_includes" / "mermaid.html").read_text()

    assert "baseurl: /ai-native-meter" in config
    assert "- jekyll-relative-links" in config
    assert re.search(r"^theme: \S+", config, re.MULTILINE)
    assert "{% include mermaid.html %}" in layout
    assert "language-mermaid" in include
    assert any("```mermaid" in p.read_text() for p in PAGES)
