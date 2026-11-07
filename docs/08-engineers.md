# 08 · Engineers: distribution, guardrails and paired change

Docs 06 and 07 measure repositories. This chapter takes the same snapshots down to the
engineer: who merges what, how evenly output is spread, and whether engineers who adopted AI
changed more than peers who did not. It follows
[ADR-0005](adr/0005-individual-metrics-are-not-leaderboards.md). Individual numbers are for
distribution, coaching and self-reflection, not for leaderboards, so every name here is a
pseudonym.

```bash
devex engineers --repo microsoft/vscode --repo dotnet/runtime          # Q3 2026, pseudonyms
devex engineers --repo dotnet/runtime --window baseline --sort revert_rate --top 20
devex engineers --repo microsoft/vscode --logins                       # local only: real logins
```

Each per-repository dashboard ([vscode](dashboard-real.html),
[dotnet/runtime](dashboard-real-dotnet-runtime.html)) has the same section. It contains a
sortable top-15 table, the per-engineer output distribution for each window, and the paired
adopter chart.

## Method

- **Who is an engineer.** Each merged PR in the window is grouped by its credited author, as
  in docs 06 and 07:
  - bots and automation accounts are excluded;
  - agent PRs are credited to the human assignee;
  - logins are matched across repositories, so the vscode Q3 2025 baseline includes the
    same people's work in vscode-copilot-chat.
- **Pseudonyms.** Each name is `eng-` plus 6 hex characters of a salted SHA-256 of the
  lower-cased login.
  - Names are stable across windows and runs and do not collide in either snapshot.
  - A test fails if any human login from the committed snapshots appears in `docs/` or the
    README.
  - `--logins` shows real logins, with a warning, for local use only.
- **KPIs, each with its own rank.**
  - PRs per week.
  - Quality-adjusted PRs per week: excludes PRs later reverted.
  - AI-assisted share.
  - Revert rate.
  - Median PR size.
  - Median first-review latency.
  - Median cycle time.

  Rank 1 is the highest for PRs and AI share and the lowest for the others, with ties sharing
  a rank. Only engineers with at least 5 merged PRs in the window are ranked
  (`--min-prs`). Throughput is never shown without the quality columns beside it.

## Guardrails per engineer

These guardrails apply the Goodhart guards of [02 · Metrics](02-metrics.md) to individuals.
They are checked only for engineers at or above the 75th percentile of ranked throughput,
which is where a throughput target would be gamed. A flag prompts a conversation; it is not
a verdict.

| Flag | Rule |
|---|---|
| small PRs | Median PR size under 50% of the repository median (splitting?) |
| reverts above repo | Revert rate above the repository's |
| light review | At least 50% of PRs merged with no human review, or with the first review within 5 minutes of opening |

| Q3 2026 | microsoft/vscode | dotnet/runtime |
|---|---|---|
| Ranked engineers (≥ 5 PRs) | 52 | 69 |
| Throughput threshold (p75) | 7.40 PRs / week | 2.13 PRs / week |
| Flagged | 6: all "reverts above repo", one also "small PRs" | 4: 3 "small PRs", 1 "reverts above repo" |
| "light review" fired | never | never |

**Light review never fires, and that is a finding.** Both repositories require review to
merge. Only 2 of 10,670 vscode PRs and 14 of 4,325 dotnet/runtime PRs have no human review
at all. A review within 5 minutes covers 10% of vscode PRs and 3% of dotnet/runtime PRs.
No engineer comes near 50% of either, which suggests the branch protection is doing its job.
In an organisation without required review, this is the first guard to watch.

The vscode revert flags deserve context. The repository revert rate is 0.7%, so a single
reverted PR out of 120 is enough to be "above the repository". The top-throughput engineer
merged 392 PRs with 3 reverted (0.8%). That warrants a look, not an alarm.

## How output is distributed

PRs per week over the whole Q3 2026 window:

| Engineers | p10 | p25 | p50 | p75 | p90 | Top 10% merge |
|---|---|---|---|---|---|---|
| vscode, all 183 active | 0.08 | 0.08 | 0.08 | 0.72 | 6.13 | 71% of PRs |
| vscode, 52 ranked | 0.69 | 1.35 | 4.72 | 7.40 | 17.27 | |
| dotnet/runtime, all 175 active | 0.08 | 0.08 | 0.15 | 0.80 | 2.10 | 58% of PRs |
| dotnet/runtime, 69 ranked | 0.44 | 0.53 | 1.07 | 2.13 | 4.23 | |

The share merged by the top 10% of engineers in each window:

| Repository | Q1 2025 | Q3 2025 | Q3 2026 |
|---|---|---|---|
| microsoft/vscode | 67% | 70% | 71% |
| dotnet/runtime | 51% | 49% | 58% |

In open source, many active engineers merge a single PR in a quarter (0.08 per week): half on
vscode and a quarter on dotnet/runtime. A repository mean of PRs per engineer per week
therefore says little about a typical engineer. Output concentrates heavily: the top 10%
merge about 70% of vscode PRs and 58% of dotnet/runtime PRs in Q3 2026. On dotnet/runtime
that share rose 9 points year over year while AI share went from 10% to 71%, and its three
most prolific engineers in Q3 2026 are 96% to 98% AI-assisted.

## AI adopters vs others: the same engineers, a year apart

This is the comparison that matters for the case. It takes engineers who merged in both the
Q3 2025 baseline and the Q3 2026 current window and splits them by AI share in the current
window: **adopters** are at or above 50% AI-assisted. Change is each engineer's PRs per
week, current over baseline. *Pooled* sums each group first, so heavy contributors weigh
more. The gap is the adopters' median change minus the others', with a seeded 90%
bootstrap interval.

| Repository | Min PRs each | Group | Engineers | Median PRs / week | Median change | Pooled | Improved |
|---|---|---|---|---|---|---|---|
| microsoft/vscode | ≥ 1 | AI adopters | 27 | 3.04 → 6.24 | +10% | +95% | 56% |
| microsoft/vscode | ≥ 1 | Others | 20 | 1.67 → 1.75 | +29% | +4% | 60% |
| microsoft/vscode | ≥ 5 | AI adopters | 20 | 5.40 → 6.28 | +16% | +71% | 60% |
| microsoft/vscode | ≥ 5 | Others | 13 | 2.51 → 3.20 | +34% | +37% | 69% |
| dotnet/runtime | ≥ 1 | AI adopters | 51 | 0.61 → 1.07 | +89% | +97% | 75% |
| dotnet/runtime | ≥ 1 | Others | 33 | 0.38 → 0.38 | +0% | -24% | 39% |
| dotnet/runtime | ≥ 5 | AI adopters | 27 | 1.75 → 2.36 | +38% | +83% | 74% |
| dotnet/runtime | ≥ 5 | Others | 10 | 1.33 → 1.03 | -3% | +8% | 30% |

| Gap in median change (adopters minus others) | ≥ 1 PR each | ≥ 5 PRs each |
|---|---|---|
| microsoft/vscode | -19% (90% interval -42% to +59%) | -18% (-43% to +52%) |
| dotnet/runtime | **+89%** (+27% to +200%) | **+41%** (+11% to +100%) |

**dotnet/runtime shows a clear adopter gap.** The typical adopter's output rose 38% to 89%
while the typical non-adopter stayed flat, and three quarters of adopters improved against
a third of the others. The interval excludes zero at both thresholds.

**vscode shows no gap in the typical engineer, and a large one in the total.** The median
change is no better for adopters (the interval spans zero), but pooled adopter output rose
71% to 95% against 4% to 37%. The gain sits with the heavy contributors, who are also the
heaviest AI users. On these numbers vscode's year-over-year throughput gain (doc 06) is a
concentration story: a few prolific engineers delegating to agents, not a lift across the
team.

## What this does and does not show

- **Observational, not causal.** Adopters chose to adopt and may differ from the others:
  core maintainers versus occasional contributors, areas more suited to agents, or more
  time on the project. Adopter status is defined on the current window, so engineers who
  happened to merge more agent PRs are partly selected by output.
- **Small groups.** 10 to 51 engineers per cell. Regression to the mean moves medians, and
  the bootstrap intervals are wide, especially on vscode.
- **Delegation raises counts by design.** Agent PRs are credited to the assignee, so
  delegating more lifts that engineer's PRs per week. Whether the delegated PRs matter as
  much as the others is not observable here: median size and revert rate are the only
  quality signals. GitHub records the agent as author, so the assignee's own review can
  count as the first human review.
- **PRs per week is the whole window.** Part-time, new, leaving or on-leave engineers look
  slower. It says nothing about difficulty, or about review, design and mentoring work.
- **Not a performance view.** Per [ADR-0005](adr/0005-individual-metrics-are-not-leaderboards.md),
  this view shows how output is distributed and gives engineers a mirror for self-reflection
  or coaching. Pseudonyms are a convention, not anonymity: the salt is public and the data is
  public.
