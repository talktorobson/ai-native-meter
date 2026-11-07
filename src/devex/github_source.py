"""Real pull-request data from a public GitHub repository, mapped onto ``metrics.PullRequest``.

Two layers, kept apart so the heuristics are testable offline:

* **Fetch (impure).** ``fetch`` pages through the GitHub GraphQL search API by shelling out to
  ``gh api graphql`` (stdlib only; ``gh`` handles auth). Results are reduced to compact records
  and written to a committed JSON snapshot, so tests, CI and the dashboard never need the network.
* **Mapping (pure).** ``to_record`` turns one GraphQL node into a record and records the
  *evidence* behind every classification; ``to_pull_requests`` turns a snapshot into
  ``PullRequest`` values. Every heuristic is a named constant or a small function below.

Heuristics (documented in ``docs/06-real-data.md``):

* **Scope.** Merged PRs into the default branch only; cherry-picks to release branches would
  double-count work.
* **AI-assisted.** Any of: author is a coding agent (``AI_AGENT_LOGINS``); a ``Co-authored-by``
  trailer in the PR body or its last ``COMMITS_SCANNED`` commits names an AI tool
  (``AI_TOOLS``); a "Generated with <tool>" marker; an explicit AI label (``AI_LABELS``).
  A trailer on a commit that applies an AI reviewer's suggestion is kept apart as
  ``review-fix``: the AI touched the change, but did not write it.
* **Who did the work.** Agent PRs are credited to the first human assignee (the engineer who
  delegated and steered the agent), else the human who merged it.
* **Bots.** Any other bot (dependabot, renovate, release automation) is ``is_bot`` and never
  counts as throughput: GitHub Apps, plus the automation *user* accounts in
  ``AUTOMATION_LOGINS``, which also apply to snapshots fetched before a login was added.
* **First review.** Earliest review by a human who is not the PR's author; AI reviewers such as
  ``copilot-pull-request-reviewer`` are bots and are skipped.
* **Change failure proxy.** A PR is ``reverted`` if a later merged PR whose title leads with
  "revert" references it, within ``REVERT_HORIZON`` of its merge.
* **Scope.** The common directory of a PR's changed files, ``SCOPE_DEPTH`` levels deep, so PRs
  confined to one area (``extensions/copilot``) can be set aside in a sensitivity check.
* **Repository consolidation.** A predecessor repository whose code later moved into the
  primary one (``microsoft/vscode-copilot-chat`` into ``microsoft/vscode/extensions/copilot``)
  is fetched into the same window, so a baseline before the move covers the same work as a
  window after it. Engineers are distinct logins across repositories.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from posixpath import commonpath
from typing import Any

from devex.metrics import PullRequest, Window, merged_in

__all__ = [
    "AI_AGENT_LOGINS",
    "AI_LABELS",
    "AI_TOOLS",
    "COHORTS",
    "COMMITS_SCANNED",
    "REVERT_HORIZON",
    "SCOPE_DEPTH",
    "Predecessor",
    "Snapshot",
    "ai_cohort",
    "ai_evidence",
    "driver",
    "engineers_in",
    "fetch",
    "first_human_review",
    "is_ai_agent",
    "is_automation",
    "load_snapshot",
    "merge_predecessor",
    "merge_snapshot",
    "pr_id",
    "record_repo",
    "revert_targets",
    "scope",
    "search_queries",
    "to_pull_requests",
    "to_record",
    "write_snapshot",
]

Node = Mapping[str, Any]
Record = dict[str, Any]
Runner = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]

AI_AGENT_LOGINS = frozenset(
    {
        "copilot",
        "copilot-swe-agent",
        "devin-ai-integration",
        "cursor",
        "cursoragent",
        "claude",
        "chatgpt-codex-connector",
        "google-labs-jules",
        "openhands-agent",
        "sweep-ai",
        "codegen-sh",
        "amazon-q-developer",
    }
)
AI_TOOLS = (
    "copilot",
    "claude",
    "cursor",
    "codex",
    "chatgpt",
    "gemini",
    "devin",
    "aider",
    "jules",
    "openhands",
    "windsurf",
)
AI_LABELS = frozenset({"ai-generated", "ai-assisted", "copilot-generated", "agent-generated"})
AUTOMATION_LOGINS = frozenset(
    {
        "dependabot",
        "renovate",
        "renovate-bot",
        "greenkeeper",
        "snyk-bot",
        "dotnet-bot",
        "dotnet-renovate-bot",
        "vscodebot-pr",
    }
)
COMMITS_SCANNED = 30
REVERT_HORIZON = timedelta(days=28)
SCOPE_DEPTH = 2
SEARCH_CAP = 1000

_CO_AUTHOR = re.compile(r"^\s*co-authored-by:\s*(?P<who>[^\n]*)$", re.IGNORECASE | re.MULTILINE)
_MARKER = re.compile(
    r"generated (?:with|by|using) \[?(?P<tool>claude code|github copilot|"
    + "|".join(AI_TOOLS)
    + r")",
    re.IGNORECASE,
)
_REVIEW_FIX = re.compile(
    r"\s*(?:apply suggestions? from|potential fix for (?:pull request|code scanning))",
    re.IGNORECASE,
)
_REVERT_TITLE = re.compile(r"^\W*(?:\[[^\]]*\]\s*)*(?:[\w./ -]{1,40}:\s*)?revert\b", re.IGNORECASE)
_REVERT_REF = re.compile(
    r"\brevert(?:s|ed|ing)?\b[^\n#]{0,30}?"
    r"(?:(?P<repo>[\w.-]+/[\w.-]+)?#|https://github\.com/(?P<url>[\w.-]+/[\w.-]+)/pull/)"
    r"(?P<num>\d+)",
    re.IGNORECASE,
)


def _login(actor: Node | None) -> str:
    return ((actor or {}).get("login") or "ghost").lower().removesuffix("[bot]")


def is_ai_agent(actor: Node | None) -> bool:
    return _login(actor) in AI_AGENT_LOGINS


def is_automation(actor: Node | None) -> bool:
    """A bot that is not a coding agent: its PRs are not engineering throughput."""
    raw = (actor or {}).get("login") or ""
    botlike = (actor or {}).get("__typename") == "Bot" or raw.lower().endswith("[bot]")
    return not is_ai_agent(actor) and (botlike or _login(actor) in AUTOMATION_LOGINS)


def _is_human(actor: Node | None) -> bool:
    return actor is not None and not is_ai_agent(actor) and not is_automation(actor)


def _nodes(node: Node, key: str) -> tuple[Node, ...]:
    return tuple((node.get(key) or {}).get("nodes") or ())


def _tool_in(text: str) -> str | None:
    lowered = text.lower().replace("anthropic", "claude")
    return next((tool for tool in AI_TOOLS if tool in lowered), None)


def _texts(node: Node) -> tuple[str, ...]:
    commits = tuple((c.get("commit") or {}).get("message") or "" for c in _nodes(node, "commits"))
    return (node.get("body") or "", *commits)


def ai_evidence(node: Node) -> tuple[str, ...]:
    """Every reason this PR counts as AI-assisted, as sorted ``kind:detail`` strings."""
    texts = _texts(node)
    agent = (f"agent:{_login(node.get('author'))}",) if is_ai_agent(node.get("author")) else ()
    trailers = (
        f"{'review-fix' if _REVIEW_FIX.match(text) else 'co-author'}:{tool}"
        for text in texts
        for match in _CO_AUTHOR.finditer(text)
        if (tool := _tool_in(match["who"]))
    )
    markers = (
        f"marker:{_tool_in(match['tool']) or match['tool'].lower()}"
        for text in texts
        for match in _MARKER.finditer(text)
    )
    labels = (
        f"label:{name}"
        for label in _nodes(node, "labels")
        if (name := (label.get("name") or "").lower()) in AI_LABELS
    )
    return tuple(sorted({*agent, *trailers, *markers, *labels}))


def driver(node: Node) -> str:
    """The accountable human: the author, or for agent PRs the delegating assignee."""
    author = node.get("author")
    if not is_ai_agent(author):
        return _login(author)
    humans = (a for a in (*_nodes(node, "assignees"), node.get("mergedBy")) if _is_human(a))
    return next((_login(a) for a in humans), _login(author))


def first_human_review(node: Node) -> str | None:
    author = _login(node.get("author"))
    times = sorted(
        r["createdAt"]
        for r in _nodes(node, "reviews")
        if r.get("createdAt") and _is_human(r.get("author")) and _login(r["author"]) != author
    )
    return times[0] if times else None


def revert_targets(node: Node, repo: str) -> tuple[int, ...]:
    """PR numbers this PR reverts, if its title leads with "revert" (``scope: revert ...``)."""
    title = node.get("title") or ""
    if not _REVERT_TITLE.search(title):
        return ()
    own = int(node["number"])
    refs = (
        int(m["num"])
        for m in _REVERT_REF.finditer(f"{title}\n{node.get('body') or ''}")
        if (m["repo"] or m["url"] or repo).lower() == repo.lower()
    )
    return tuple(sorted({n for n in refs if n != own}))


def scope(node: Node) -> str | None:
    """Common directory of the changed files, at most ``SCOPE_DEPTH`` levels; "" when they span
    the root, ``None`` when the file list is missing or truncated."""
    files = node.get("files")
    paths = [f["path"] for f in (files or {}).get("nodes") or ()]
    if not paths or files.get("totalCount", len(paths)) > len(paths):
        return None
    return commonpath(["/".join(path.split("/")[:-1][:SCOPE_DEPTH]) for path in paths])


def to_record(node: Node, repo: str) -> Record:
    found = scope(node)
    return {
        "number": int(node["number"]),
        "author": driver(node),
        "login": _login(node.get("author")),
        "created": node["createdAt"],
        "merged": node["mergedAt"],
        "review": first_human_review(node),
        "lines": int(node.get("additions") or 0) + int(node.get("deletions") or 0),
        "bot": is_automation(node.get("author")),
        "ai": list(ai_evidence(node)),
        "reverts": list(revert_targets(node, repo)),
        **({"scope": found} if found is not None else {}),
    }


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class Predecessor:
    """A repository whose code later moved into the primary one, fetched for one window."""

    repo: str
    window: Window
    fetched_on: str
    moved_to: str  # path of its code in the primary repository


@dataclass(frozen=True, slots=True)
class Snapshot:
    repo: str
    default_branch: str
    windows: tuple[tuple[Window, str], ...]  # (window, fetched_on)
    records: tuple[Record, ...]
    predecessors: tuple[Predecessor, ...] = ()

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}"

    def evidence(self) -> dict[str, tuple[str, ...]]:
        return {pr_id(r, self.repo): tuple(r["ai"]) for r in self.records}

    def repos_in(self, window: Window) -> tuple[str, ...]:
        """The primary repository, then every predecessor fetched for this window."""
        return (self.repo, *(p.repo for p in self.predecessors if p.window == window))


def record_repo(record: Record, primary: str) -> str:
    """Records of the primary repository carry no ``repo`` key."""
    return record.get("repo", primary)


def pr_id(record: Record, primary: str) -> str:
    return f"{record_repo(record, primary)}#{record['number']}"


def _key(record: Record) -> tuple[str, int]:
    return record.get("repo", ""), record["number"]


def _reverted(records: Sequence[Record], primary: str) -> frozenset[str]:
    """Reverts only reference PRs of their own repository."""
    merged = {_key(r): _when(r["merged"]) for r in records}
    return frozenset(
        f"{record_repo(r, primary)}#{target}"
        for r in records
        for target in r["reverts"]
        if (key := (r.get("repo", ""), target)) in merged
        and timedelta(0) <= _when(r["merged"]) - merged[key] <= REVERT_HORIZON
    )


def to_pull_requests(snapshot: Snapshot) -> tuple[PullRequest, ...]:
    """Token cost and incidents are not observable in public data, so they stay at zero."""
    reverted = _reverted(snapshot.records, snapshot.repo)
    return tuple(
        PullRequest(
            id=pr_id(r, snapshot.repo),
            author=r["author"],
            opened_at=_when(r["created"]),
            merged_at=_when(r["merged"]),
            first_review_at=_when(r["review"]),
            lines_changed=r["lines"],
            ai_assisted=bool(r["ai"]),
            reverted=pr_id(r, snapshot.repo) in reverted,
            is_bot=r["bot"] or _login({"login": r["login"]}) in AUTOMATION_LOGINS,
        )
        for r in snapshot.records
    )


def engineers_in(prs: Iterable[PullRequest], window: Window) -> int:
    """Active engineers: distinct humans credited with at least one merged PR in the window,
    across repositories (a login is one engineer)."""
    return len({pr.author for pr in merged_in(prs, window)})


COHORTS = ("agent-authored", "AI co-authored", "AI review fix only", "no AI signal")


def ai_cohort(evidence: Sequence[str]) -> str:
    """Strongest evidence wins: an agent wrote it > AI wrote some of it > a human accepted an
    AI reviewer's suggested fix > nothing observable."""
    kinds = {e.partition(":")[0] for e in evidence}
    if "agent" in kinds:
        return COHORTS[0]
    if kinds - {"review-fix"}:
        return COHORTS[1]
    return COHORTS[2] if kinds else COHORTS[3]


def _window(raw: Mapping[str, str]) -> Window:
    return Window(datetime.fromisoformat(raw["since"]), datetime.fromisoformat(raw["until"]))


def _window_json(window: Window) -> dict[str, str]:
    return {"since": f"{window.start:%Y-%m-%d}", "until": f"{window.end:%Y-%m-%d}"}


def load_snapshot(path: str | Path) -> Snapshot:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return Snapshot(
        repo=raw["repo"],
        default_branch=raw["default_branch"],
        windows=tuple((_window(w), w["fetched_on"]) for w in raw["windows"]),
        records=tuple(raw["pull_requests"]),
        predecessors=tuple(
            Predecessor(p["repo"], _window(p), p["fetched_on"], p["moved_to"])
            for p in raw.get("predecessors", ())
        ),
    )


def _sorted_records(records: Iterable[Record]) -> tuple[Record, ...]:
    """Newer records replace older ones with the same repository and number."""
    by_key = {_key(r): r for r in records}
    return tuple(by_key[k] for k in sorted(by_key))


def merge_snapshot(
    base: Snapshot | None,
    repo: str,
    default_branch: str,
    window: Window,
    fetched_on: str,
    records: Iterable[Record],
) -> Snapshot:
    """Add one fetched window to a snapshot; newer records replace older ones by number."""
    same = base is not None and base.repo == repo
    old_windows = base.windows if same else ()
    windows = {w.start: (w, f) for w, f in (*old_windows, (window, fetched_on))}
    return Snapshot(
        repo=repo,
        default_branch=default_branch,
        windows=tuple(windows[k] for k in sorted(windows)),
        records=_sorted_records((*(base.records if same else ()), *records)),
        predecessors=base.predecessors if same else (),
    )


def merge_predecessor(
    base: Snapshot,
    repo: str,
    window: Window,
    fetched_on: str,
    moved_to: str,
    records: Iterable[Record],
) -> Snapshot:
    """Add a predecessor repository's PRs to an already fetched window of the primary one."""
    if repo.lower() == base.repo.lower():
        raise ValueError(f"{repo} is the snapshot's own repository")
    if window not in {w for w, _ in base.windows}:
        raise ValueError(f"fetch {base.repo} for {window_text(window)} first")
    kept = tuple(p for p in base.predecessors if (p.repo, p.window) != (repo, window))
    added = Predecessor(repo, window, fetched_on, moved_to)
    return Snapshot(
        repo=base.repo,
        default_branch=base.default_branch,
        windows=base.windows,
        records=_sorted_records((*base.records, *({**r, "repo": repo} for r in records))),
        predecessors=tuple(sorted((*kept, added), key=lambda p: (p.window.start, p.repo))),
    )


def window_text(window: Window) -> str:
    return f"{window.start:%Y-%m-%d} to {window.end:%Y-%m-%d}"


def write_snapshot(snapshot: Snapshot, path: str | Path) -> None:
    """One PR per line: compact, yet reviewable in a diff."""
    head = {
        "source": "GitHub GraphQL API via gh",
        "repo": snapshot.repo,
        "url": snapshot.url,
        "default_branch": snapshot.default_branch,
        "heuristics": "devex.github_source (see docs/06-real-data.md)",
        "windows": [{**_window_json(w), "fetched_on": f} for w, f in snapshot.windows],
        **(
            {
                "predecessors": [
                    {
                        "repo": p.repo,
                        **_window_json(p.window),
                        "fetched_on": p.fetched_on,
                        "moved_to": p.moved_to,
                    }
                    for p in snapshot.predecessors
                ]
            }
            if snapshot.predecessors
            else {}
        ),
    }
    lines = ",\n".join(json.dumps(r, separators=(",", ":")) for r in snapshot.records)
    header = json.dumps(head, indent=1)[:-2]
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f'{header},\n "pull_requests": [\n{lines}\n]}}\n', encoding="utf-8")


# --- Fetch (network) -------------------------------------------------------------------------

QUERY = (
    """
query($q: String!, $after: String) {
  rateLimit { remaining resetAt }
  search(query: $q, type: ISSUE, first: 50, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest {
      number title body createdAt mergedAt additions deletions
      author { login __typename }
      mergedBy { login __typename }
      assignees(first: 5) { nodes { login __typename } }
      labels(first: 20) { nodes { name } }
      reviews(first: 20) { nodes { createdAt author { login __typename } } }
      commits(last: COMMITS) { nodes { commit { message } } }
      files(first: 100) { totalCount nodes { path } }
    } }
  }
}
"""
).replace("COMMITS", str(COMMITS_SCANNED))

BRANCH_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) { defaultBranchRef { name } }
}
"""


def gh_runner(query: str, variables: Mapping[str, Any], attempts: int = 4) -> Mapping[str, Any]:
    """POST one GraphQL request through ``gh api`` (uses gh's auth); retries on failure."""
    body = json.dumps({"query": query, "variables": dict(variables)})
    for attempt in range(attempts):
        done = subprocess.run(
            ["gh", "api", "graphql", "--input", "-"],
            input=body,
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode == 0:
            payload = json.loads(done.stdout)
            if not payload.get("errors"):
                return payload["data"]
        if attempt == attempts - 1:
            raise RuntimeError(f"gh api graphql failed: {done.stderr or done.stdout}"[:2000])
        time.sleep(5 * 2**attempt)
    raise AssertionError("unreachable")


def search_queries(
    repo: str, branch: str, start: datetime, end: datetime, *, days: int = 7, title: str = ""
) -> tuple[str, ...]:
    """Search strings covering [start, end) in slices small enough for the 1,000-result cap."""
    step = timedelta(days=days)
    starts = tuple(start + i * step for i in range(-(-(end - start).days // days)))
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    return tuple(
        f"repo:{repo} is:pr is:merged base:{branch} {title}".strip()
        + f" merged:{s:{stamp}}..{min(s + step, end) - timedelta(seconds=1):{stamp}}"
        for s in starts
    )


def _pages(query: str, run: Runner) -> Iterator[Node]:
    after: str | None = None
    while True:
        data = run(QUERY, {"q": query, "after": after})
        search = data["search"]
        if search["issueCount"] > SEARCH_CAP:
            raise RuntimeError(f"{search['issueCount']} results for {query!r}; use smaller slices")
        yield from (n for n in search["nodes"] if n)
        if not search["pageInfo"]["hasNextPage"]:
            return
        after = search["pageInfo"]["endCursor"]


def fetch(
    repo: str,
    window: Window,
    *,
    run: Runner | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> tuple[str, tuple[Record, ...]]:
    """Merged PRs in the window, plus revert PRs merged up to ``REVERT_HORIZON`` after it."""
    run = run or gh_runner
    owner, name = repo.split("/")
    branch = run(BRANCH_QUERY, {"owner": owner, "name": name})["repository"]["defaultBranchRef"]
    branch_name = branch["name"]
    queries = (
        *search_queries(repo, branch_name, window.start, window.end),
        *search_queries(
            repo,
            branch_name,
            window.end,
            window.end + REVERT_HORIZON,
            days=REVERT_HORIZON.days,
            title="revert in:title",
        ),
    )

    def records(q: str) -> tuple[Record, ...]:
        found = tuple(to_record(n, repo) for n in _pages(q, run))
        log(f"{len(found):>5} PRs  {q}")
        return found

    main_part = tuple(r for q in queries[:-1] for r in records(q))
    tail = tuple(r for r in records(queries[-1]) if r["reverts"])
    return branch_name, (*main_part, *tail)
