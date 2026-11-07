# ADR-0005 · Individual metrics are for distribution and coaching, not leaderboards

- **Status:** accepted
- **Date:** 2026-11-07

## Context

Once team metrics exist, someone asks for them per engineer. There are good reasons for
that view:

- Averages hide the distribution. On open-source repositories the top 10% of engineers
  merge 58% to 71% of PRs.
- A team-level gain can come from a few heavy users.
- An engineer can compare their own trend before and after adopting a tool.

There is also a well-documented failure mode. DORA measures delivery for teams and
services, not individuals, and warns that using its metrics to compare or rank people
degrades them. SPACE (Forsgren et al., 2021) argues that no single dimension captures
productivity, and that activity counts such as PRs or commits are the dimension most easily
misread when taken alone. Goodhart's law applies directly: once PRs per week becomes a
target, PRs get smaller, reviews get lighter and reverts rise, while the number improves.

## Decision

`devex engineers` ranks engineers by KPI, under four rules:

1. **Pseudonyms by default, everywhere published.** Each GitHub login maps to a stable
   pseudonym, `eng-` plus 6 hex characters of a salted SHA-256 of the lower-cased login. The
   salt is a constant in code, so a person keeps the same name across runs, windows and
   repositories. The dashboards, docs, Pages site and README only ever show pseudonyms, and
   a test fails if any human login from the committed snapshots appears in `docs/` or the
   README. The salt is public, so pseudonyms resist casual reading, not a determined attempt
   to re-identify someone: they are a convention, not anonymisation.
2. **The reveal is opt-in and local.** `--logins` prints real logins with a warning, for
   self-reflection or a one-to-one. Its output is not committed or published.
3. **Throughput never appears alone.** Every row shows PRs per week next to the following
   columns, each with its own rank:
   - a quality-adjusted rate that excludes reverted PRs;
   - AI share;
   - revert rate;
   - median size;
   - first-review latency;
   - cycle time.
   
   Engineers at or above the 75th percentile of throughput are checked for small PRs, a
   revert rate above the repository's, and light review. The flags prompt a conversation;
   they are not verdicts.
4. **Distribution and paired change come first.** The section leads with percentiles of
   per-engineer output and a paired before/after comparison of the same engineers. A single
   ordered list is not the headline.

## Consequences

- Leaders see where output concentrates and whether AI adopters changed more than
  their peers, without a public league table.
- An engineer can look up their own numbers locally and discuss them with context the data
  lacks: part-time work, leave, review load, design work, mentoring and area difficulty.
- Ranks are still visible, so the guardrails, the caveats and this ADR have to travel
  with every view. The dashboard section links here.
- Pseudonyms are not anonymity. Anyone with the snapshot and the code can recompute them,
  so the snapshots stay public-data-only and this view is never joined to HR data.
