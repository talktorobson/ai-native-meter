import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from devex import github_source
from devex.metrics import Window

FIXTURES = Path(__file__).parent / "fixtures"
REPO = "microsoft/vscode"
BEFORE = Window(datetime(2025, 1, 6), datetime(2025, 1, 20))
AFTER = Window(datetime(2026, 7, 6), datetime(2026, 7, 20))
MID = Window(datetime(2025, 7, 6), datetime(2025, 7, 20))  # AFTER, one year earlier


def _shifted(record: dict, number: int, delta: timedelta) -> dict:
    """A recorded PR moved in time, to populate another window."""

    def move(stamp: str | None) -> str | None:
        if stamp is None:
            return None
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00")) + delta
        return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        **record,
        "number": number,
        "created": move(record["created"]),
        "merged": move(record["merged"]),
        "review": move(record["review"]),
        "reverts": [],
    }


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


@pytest.fixture(scope="session")
def three_window_snapshot(small_snapshot) -> github_source.Snapshot:
    """Reference (BEFORE), year-over-year baseline (MID) and current (AFTER)."""
    by_number = {r["number"]: r for r in small_snapshot.records}
    mid = (
        _shifted(by_number[102], 301, MID.start - BEFORE.start),
        _shifted(by_number[201], 302, MID.start - AFTER.start),
    )
    return github_source.merge_snapshot(small_snapshot, REPO, "main", MID, "2026-11-07", mid)


@pytest.fixture
def three_window_file(tmp_path, three_window_snapshot) -> Path:
    path = tmp_path / "three.json"
    github_source.write_snapshot(three_window_snapshot, path)
    return path
