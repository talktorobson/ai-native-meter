# 06 · Real data: microsoft/vscode

The synthetic dashboard shows what the metrics engine *should* reveal. This chapter runs the
same engine, unchanged, on real merged pull requests from
[microsoft/vscode](https://github.com/microsoft/vscode), so every claim about measurement can
be checked against public data.

[![Dashboard on real microsoft/vscode data](dashboard-real.png)](dashboard-real.html)

## Why microsoft/vscode

- **Famous and high-volume.** One of the most active public repositories on GitHub: about
  2,400 merged PRs into `main` in Q3 2025 and 4,900 in Q3 2026, from a large core team.
- **AI assistance is visible, not guessed.** The repo uses the Copilot coding agent
  (`copilot-swe-agent` authors PRs; a human is assigned) and many commits carry
  `Co-authored-by: Copilot` trailers. That gives an observable adoption signal.
- **Verified before choosing it:**

  | Merged into `main` | Q1 2025 | Q3 2025 | Q1 2026 | Q3 2026 |
  |---|---|---|---|---|
  | PRs | 2,512 | 2,361 | 4,177 | 4,906 |
  | PRs authored by `copilot-swe-agent` | 0 | 115 | 192 | 176 |
  | Commits with a `Co-authored-by: Copilot` trailer | 0 | 82 | 858 | 3,555 |

## Three windows, one headline

The snapshot holds three calendar quarters of 13 weeks each. Each plays a different role:

| Role | Window | Why |
|---|---|---|
| **Current** | Q3 2026 (2026-07-01 to 2026-10-01) | Latest complete quarter, at peak observable adoption (66% AI-assisted). |
| **Baseline** (headline) | Q3 2025 (2025-07-01 to 2025-10-01) | The same quarter a year earlier. Same season, same summer holidays and release rhythm, so the year-over-year comparison controls for seasonality. |
| **Pre-AI reference** | Q1 2025 (2025-01-01 to 2025-04-01) | The last quarter with no observable AI signal at all. Q3 2025 is not a clean "before": the coding agent already authored 115 merged PRs (5%), and its AI-assisted share is 6%, not zero. |

The headline compares Q3 2026 with Q3 2025. Every table and chart also shows Q1 2025, so a
reader can see when the year-over-year baseline is itself unusual. By default `devex` picks
the latest window as current, the window starting exactly one year earlier as baseline
(falling back to the earliest), and the earliest earlier window as reference. `--baseline`
and `--current` (window start dates) override the choice.

## Method

```bash
devex fetch --repo microsoft/vscode --since 2025-01-01 --until 2025-04-01
devex fetch --repo microsoft/vscode --since 2025-07-01 --until 2025-10-01
devex fetch --repo microsoft/vscode --since 2026-07-01 --until 2026-10-01
devex report --source real       # the tables below
devex dashboard --source real    # docs/dashboard-real.html
devex report --source real --baseline 2025-01-01   # Q1 2025 as baseline instead
```

1. **Fetch** ([`github_source.py`](../src/devex/github_source.py)). `gh api graphql` pages
   through `is:pr is:merged base:main` search results in 7-day slices (the search API caps
   at 1,000 results per query). Each PR brings its author, assignees, merger, labels, body,
   reviews, size and its last 30 commit messages. A second search covers the 28 days after
   each window for `revert` PRs, so late reverts of in-window PRs are still seen.
2. **Snapshot.** Each PR is reduced to a compact record together with the *evidence* behind every
   classification, and written to [`data/real/microsoft-vscode.json`](../data/real/microsoft-vscode.json)
   (9,797 PRs, ~2 MB, one PR per line). Tests, CI and the dashboard read only the snapshot:
   nothing needs the network or a token after the fetch.
3. **Map** to the existing `metrics.PullRequest` and score with the existing `scorecard`,
   `cohort_scorecards` and `gaming_signals`. No metric was changed for real data.

## Heuristics

Every rule is a named constant or a small pure function in `github_source.py`, with tests
on recorded fixtures.

| Field | Rule |
|---|---|
| Scope | Merged PRs into the default branch only. Cherry-picks to release branches would double-count work. |
| `ai_assisted` | Any explicit, observable signal: **agent** author (`copilot-swe-agent`, `devin-ai-integration`, `cursor`, `claude`, `chatgpt-codex-connector`, `google-labs-jules`, …); **co-author**: a `Co-authored-by` trailer in the PR body or its last 30 commits naming an AI tool (Copilot, Claude, Cursor, Codex, ChatGPT, Gemini, Devin, Aider, Jules, OpenHands, Windsurf); **marker**: "Generated with <tool>"; **label**: exactly `ai-generated`, `ai-assisted`, `copilot-generated` or `agent-generated` (vscode's area label `copilot` is a product area, not evidence). |
| Review fix | A Copilot trailer on a commit titled "Apply suggestion(s) from…" or "Potential fix for pull request finding" is kept apart as `review-fix`: the AI touched the change but did not write it. It counts as AI-assisted but forms its own cohort. |
| Author (engineer) | Agent PRs are credited to the **first human assignee** (who delegated and steered the agent), else to the human who merged it. Engineers in a window are the distinct humans credited with a merged PR. |
| Bots | Any other bot account (`dependabot`, `vs-code-engineering`, renovate, …) is `is_bot` and never counts as throughput: 14 PRs in Q1 2025, 15 in Q3 2025, 164 in Q3 2026. |
| First review | Earliest review by a human who is not the PR's author. AI reviewers (`copilot-pull-request-reviewer`) are bots and skipped. |
| Change failure | A PR is `reverted` if a later merged PR **whose title leads with "revert"** references it ("Reverts #N", "revert owner/repo#N", or a PR URL in the same repo) and merges within 28 days. 17 PRs reverted in Q1 2025, 15 in Q3 2025, 31 in Q3 2026. |
| Cycle time | PR created to merged. |
| Size | Additions plus deletions. |
| Token cost | Not observable in public data; reported as "not observable", never as zero. |

## Results

Verbatim output of `devex report --source real`:

### Real data: microsoft/vscode, Q3 2025 vs Q3 2026 (pre-AI reference Q1 2025)

Source: https://github.com/microsoft/vscode · merged PRs into `main` · fetched 2026-11-07 · 9,797 PRs in the snapshot.

| Metric | Q1 2025 (reference) | Q3 2025 (baseline) | Q3 2026 (current) | Q3 2026 vs Q3 2025 | Q3 2026 vs Q1 2025 |
|---|---|---|---|---|---|
| Merged PRs | 2,498 | 2,346 | 4,742 | +102% | +90% |
| Active engineers | 108 | 134 | 184 | +37% | +70% |
| PRs / engineer / week | 1.80 | 1.33 | 1.96 | +47% | +9% |
| Quality-adjusted PRs / engineer / week | 1.79 | 1.32 | 1.95 | +47% | +9% |
| Revert rate (change failure proxy) | 0.7% | 0.6% | 0.7% | +0.0 pp | -0.0 pp |
| Median cycle time (h) | 0.5 | 0.8 | 2.5 | +220% | +401% |
| Median time to first human review (h) | 0.3 | 0.4 | 1.2 | +190% | +327% |
| Median PR size (lines) | 21 | 18 | 121 | +572% | +476% |
| AI-assisted share | 0% | 6% | 66% | +60.8 pp | +66.5 pp |
| Token cost per merged PR | not observable | not observable | not observable | n/a | n/a |

**AI cohorts** (cohort engineers are the distinct authors in that cohort)

| Window | Cohort | Merged PRs | Share | Engineers | Revert rate | Median cycle (h) | Median first review (h) | Median size |
|---|---|---|---|---|---|---|---|---|
| Q1 2025 | no AI signal | 2,498 | 100% | 108 | 0.7% | 0.5 | 0.3 | 21 |
| Q3 2025 | agent-authored | 115 | 5% | 20 | 0.0% | 25.4 | 3.5 | 32 |
| Q3 2025 | AI co-authored | 17 | 1% | 7 | 0.0% | 2.5 | 1.1 | 49 |
| Q3 2025 | AI review fix only | 1 | 0% | 1 | 0.0% | 75.8 | 75.8 | 426 |
| Q3 2025 | no AI signal | 2,213 | 94% | 134 | 0.7% | 0.7 | 0.4 | 18 |
| Q3 2026 | agent-authored | 176 | 4% | 18 | 0.0% | 12.2 | 4.3 | 51 |
| Q3 2026 | AI co-authored | 2,901 | 61% | 99 | 0.7% | 2.5 | 1.1 | 173 |
| Q3 2026 | AI review fix only | 75 | 2% | 24 | 1.3% | 3.8 | 1.4 | 65 |
| Q3 2026 | no AI signal | 1,590 | 34% | 138 | 0.7% | 2.1 | 1.0 | 75 |

**AI evidence on merged PRs** (a PR can carry several)

| Evidence | Q1 2025 | Q3 2025 | Q3 2026 |
|---|---|---|---|
| `co-author:copilot` | 0 | 19 | 2,895 |
| `agent:copilot-swe-agent` | 0 | 115 | 176 |
| `review-fix:copilot` | 0 | 2 | 148 |
| `marker:copilot` | 0 | 0 | 110 |
| `co-author:claude` | 0 | 0 | 47 |
| `marker:claude` | 0 | 0 | 32 |
| `co-author:cursor` | 0 | 0 | 2 |

**Goodhart guards**
- Q3 2026 vs Q3 2025: none triggered.
- Q3 2026 vs Q1 2025: none triggered.

**Limits**
- Reverts are a change-failure proxy: incidents, hotfixes and rollbacks without a revert PR are invisible, so the true failure rate is higher on both sides.
- AI attribution is incomplete: a window with no observable signal is not a window with no AI use, and assistance without a trailer, marker or agent author is counted as human.
- Correlation is not causation: team size, roadmap, release cadence and tooling all changed between the windows. Comparing the same quarter a year apart removes seasonality, nothing else.
- Open source is not an enterprise: drive-by contributors dilute per-engineer numbers and review norms differ.
- Token cost is not observable in public data, so cost per merged PR is not reported.

## What the real data says

- **Adoption is real and fast.** The AI-assisted share went from 6% in Q3 2025 to 66% in
  Q3 2026 (0% in Q1 2025). In Q3 2025 the signal was mostly the coding agent (115 PRs);
  `Co-authored-by: Copilot` trailers were rare (17 PRs). A year later trailers are on 61% of
  merged PRs, climbing from about 50% in the first week of the quarter to 76% in the last,
  while the agent's share stayed at 4 to 5%.
- **Throughput per engineer: +47% year over year, +9% against the pre-AI reference.** Both
  numbers are real, and neither should be quoted alone. Q3 2025 is the weakest of the three
  windows (1.33 PRs per engineer per week, against 1.80 in Q1 2025): more engineers were
  credited (134) for fewer merged PRs (2,346). The data does not say why. So the year-over-year
  gain is partly a soft baseline. Merged PRs doubled (+102%), and engineers grew by 37%.
- **Quality held, by the only proxy available.** The revert rate is 0.6% in Q3 2025 and 0.7%
  in Q3 2026 and Q1 2025: flat within noise. The AI co-authored cohort reverts no more than PRs
  with no AI signal (0.7% vs 0.7%). The agent-authored cohort had no reverts in 115 PRs, then
  in 176, which is too few to conclude anything.
- **The work got bigger and slower to land, everywhere.** Median PR size went from 18 to
  121 lines, median cycle time from 0.8 h to 2.5 h, and time to first human review from
  0.4 h to 1.2 h. This is not only the AI cohorts: PRs with no AI signal also grew (18 to 75
  lines) and slowed (0.7 h to 2.1 h cycle). Either the team's way of working changed, or AI
  use without a trailer is common, or both. AI co-authored PRs are the largest (median 173
  lines). Agent PRs wait longest for a human (4.3 h to first review), though their median cycle
  time halved from 25.4 h to 12.2 h in a year. **Review is the new bottleneck**, as the
  architecture chapter argues.
- **No Goodhart guard fired**, against either window. PRs did not get split (they grew), and
  the change failure proxy did not worsen. The cost guard cannot fire because token cost is
  not observable. That is a gap the dashboard states, not a pass.

## Limits

- **Reverts are a weak change-failure proxy.** Incidents, hotfixes, rollbacks and fixes
  without a revert PR are invisible. Reverts pushed as direct commits, or without a PR
  reference, are missed. The true failure rate is higher on both sides.
- **AI attribution is incomplete.** A window with *no observable* signal is not a window
  with *no* AI use: inline completions leave no trace. The reference Q1 2025 is "pre-AI" only
  in that sense. PRs with more than 30 commits are only scanned on
  their last 30. One user account, `vscodebot-pr` (7 PRs), opens Copilot co-authored PRs
  and is counted as a human engineer.
- **Correlation is not causation.** Team size, roadmap, release process and review
  norms changed between the windows. Comparing Q3 with Q3 removes seasonality, nothing
  else. The baseline is also partly treated: Q3 2025 already has 6% AI-assisted PRs.
- **Open source is not an enterprise.** Drive-by contributors dilute per-engineer numbers,
  core maintainers merge many small changes within minutes (the 2025 median cycle time
  is 0.5 h),
  and incentives differ from an internal team.
- **Token cost is not observable.** The cost guardrail, central to the synthetic case, cannot
  be checked on public data.

## Reproduce or extend

```bash
gh auth login                                   # any account; public data only
devex fetch --repo microsoft/vscode --since 2026-01-01 --until 2026-04-01   # add a window
devex fetch --repo dotnet/runtime --since 2025-01-01 --until 2025-04-01     # another repo
devex report --source real --snapshot data/real/dotnet-runtime.json
```

`fetch` merges windows into the snapshot. Reports compare the latest window against the one
a year earlier (else the earliest), with any earlier window as a pre-AI reference; pass
`--baseline` and `--current` with window start dates to choose explicitly.
