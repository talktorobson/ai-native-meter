import json
from datetime import datetime
from pathlib import Path

import pytest

from devex import github_source
from devex.metrics import Window

FIXTURES = Path(__file__).parent / "fixtures"
REPO = "microsoft/vscode"
BEFORE = Window(datetime(2025, 1, 6), datetime(2025, 1, 20))
AFTER = Window(datetime(2026, 7, 6), datetime(2026, 7, 20))


@pytest.fixture(scope="session")
def nodes() -> dict[int, dict]:
    """Recorded GraphQL search nodes, keyed by PR number."""
    raw = json.loads((FIXTURES / "github_prs.json").read_text(encoding="utf-8"))
    return {n["number"]: n for n in raw}


@pytest.fixture(scope="session")
def small_snapshot(nodes) -> github_source.Snapshot:
    records = tuple(github_source.to_record(n, REPO) for n in nodes.values())
    first = github_source.merge_snapshot(None, REPO, "main", BEFORE, "2026-11-01", records)
    return github_source.merge_snapshot(first, REPO, "main", AFTER, "2026-11-07", ())


@pytest.fixture
def snapshot_file(tmp_path, small_snapshot) -> Path:
    path = tmp_path / "snapshot.json"
    github_source.write_snapshot(small_snapshot, path)
    return path
