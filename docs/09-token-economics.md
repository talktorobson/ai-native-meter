# 09 · Token economics: meter cost per accepted change, not per token

Docs 06 to 08 measure what AI changes in delivery. This chapter measures what it costs, and
whether that cost buys anything. The unit that matters is the **cost per accepted change**:
all AI spend on a change (authoring chat, agent steps, AI review, CI) divided by the changes
that merged and stayed merged. Cost per token is an input price; it says nothing about value.
The decision is recorded in [ADR-0006](adr/0006-cost-per-accepted-outcome.md).

```bash
devex spend                                   # synthetic demo: 4 teams, 4 models, Q2 2026
devex spend --by surface                      # or model | team | task
devex spend --events usage.csv --prices data/model_prices.json --budget 25000
devex spend --events otel-traces.json --split 2026-05-04   # before/after a change
```

**Public repositories leave it blank.** A public repository shows pull requests, not the
tokens, models or invoices behind them. The real-data dashboards
([vscode](dashboard-real.html), [dotnet/runtime](dashboard-real-dotnet-runtime.html))
therefore show these KPIs as blank "needs telemetry" tiles rather than estimates. Everything
below runs on synthetic events (`data/usage_events.jsonl`, seeded and committed) and is
meant for a company that owns its telemetry. [Synthetic dashboard](dashboard.html), section
"Token economics".

## What to meter

One **usage event** per model call or agent step. The ingest schema is deliberately flat, so
any source can be mapped onto it with a few lines
([`usage.py`](https://github.com/talktorobson/ai-native-meter/blob/main/src/devex/usage.py)):

| Field | Required | Meaning |
|---|---|---|
| `timestamp` | yes | ISO 8601; converted to UTC |
| `org`, `team` | team | Who pays; drives every breakdown |
| `engineer` | no | Pseudonymised on ingest (`eng-` plus 6 hex, as in [doc 08](08-engineers.md)); blank is "unattributed" |
| `surface` | yes | `ide_chat`, `completion`, `coding_agent`, `code_review`, `ci`, `sre`, `other` |
| `provider`, `model` | model | The model is the key into the price table |
| `input_tokens`, `output_tokens` | yes | Input **includes** cached input |
| `cached_input_tokens` | no | Input served from the prompt cache, billed at the cached price |
| `task_id`, `task_type` | for per-task KPIs | `pr`, `review`, `incident`, `agent_session`, `chat`; events sharing a task_id are one task |
| `repo`, `pr` | for outcome KPIs | Links the spend to a change; every event with the same `repo#pr` is one change |
| `outcome` | for outcome KPIs | `merged`, `reverted`, `abandoned`, `resolved`; the latest wins (merged then reverted is reverted) |
| `task_class` | for routing | `trivial`, `standard`, `complex`, as classified at the gateway |
| `cost_usd` | no | A billed cost from the source; overrides the price table |

Files: `.jsonl`/`.ndjson` and `.csv` in this shape, a `.json` list of records, or an OTLP JSON
trace export (detected by `resourceSpans`).

**Prices are data, not code.** [`data/model_prices.json`](https://github.com/talktorobson/ai-native-meter/blob/main/data/model_prices.json)
holds USD per million input, output and cached-input tokens per model, each with an
`effective_from` date and a routing tier. An event is priced at the price in effect on its
date, so a mid-quarter price cut (o3 in the table) is costed correctly on both sides. The
prices are **illustrative**: verify them against each provider's current price list, and
replace them with your negotiated rates. A model missing from the table is an error naming
the model, not a silent zero.

## Connecting real telemetry

No adapter here calls an external API; each source is exported, then loaded. Field names
move between versions of each product, so check them against the version you run.

| Source | What it gives | How it maps |
|---|---|---|
| **OpenTelemetry GenAI spans** (built in: `from_otel`) | Per call: `gen_ai.request.model` / `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, provider (`gen_ai.provider.name` or the older `gen_ai.system`), cached input if your instrumentation emits it (`gen_ai.usage.cache_read.input_tokens`) | Span start time is the timestamp. Attribution comes from attributes your gateway or agent adds: `user.id`, `devex.team`, `devex.surface`, `devex.task.id`, `devex.task.type`, `devex.repo`, `devex.pr`, `devex.outcome`, `devex.task.class`. Resource attributes (e.g. `devex.team` per service) apply to every span |
| **LLM gateway** (e.g. LiteLLM spend logs) | Per request: model, user, team, prompt and completion tokens, computed spend, request tags or metadata | `user` to engineer, `team_id` to team, tokens as they are, `spend` to `cost_usd` if you trust the gateway's prices; put surface, task and PR ids in request metadata or tags. The gateway is the best place to meter: it sees every surface and can classify the task for routing |
| **Provider usage and cost exports** (Anthropic, OpenAI admin APIs) | Tokens per model per time bucket, grouped by workspace/project, API key or user; cached input reported separately; daily cost | One event per bucket and group, with `team` from the workspace or project. Good for totals, model mix and cache ratio; no task or PR link, so they reconcile the gateway rather than replace it |
| **GitHub Copilot** | Usage metrics are activity and acceptance (active users, suggestions, acceptances, chat turns), not tokens; premium requests are billed per request | Map billed premium-request usage to events with `cost_usd` and `surface` from the feature (chat, coding agent, code review). Copilot coding-agent PRs already carry the outcome on GitHub, so join them on `repo#pr` |

The join that matters is **spend to change**: tag every agent session, review and chat that
works on a PR with `repo` and `pr`, then let the outcome come from GitHub (merged, then
reverted or not; the same revert detection as [doc 06](06-real-data.md)).

## The KPIs, and why each one

All pure functions in [`spend.py`](https://github.com/talktorobson/ai-native-meter/blob/main/src/devex/spend.py), tested offline.

| KPI | Definition | Why |
|---|---|---|
| **Cost per accepted change** (north star) | Spend on concluded changes / changes merged and not reverted. In-flight changes are on neither side | Prices the outcome, so waste, loops and reverts all show up in it |
| All-in cost per accepted change | Every dollar except incidents / accepted changes | Includes chat and completions that no PR claims |
| Waste | Spend on reverted and on abandoned changes, and its share | The money a token-price view never sees |
| Cost per resolved incident | Incident-task spend / incidents resolved | The AI SRE line of [doc 03](03-architecture.md) |
| Spend by model, team, surface, task | Dollars and tokens, with shares | Where the money goes |
| Model mix | Share of tokens vs share of dollars per tier | Frontier models are a small share of tokens and a large share of dollars |
| Cost per task type | Mean, median and p90 dollars and tokens per PR, review, incident, agent session | Mean and p90 apart reveal a long tail |
| Prompt-cache hit ratio | Cached input / input tokens | The cheapest optimisation there is |
| Routing opportunity | Frontier spend on calls whose task class a cheaper tier passes the [routing evals](adr/0003-route-by-evals.md) for, repriced at the cheapest model of that tier; unclassified spend shown apart | Savings the evals already allow, not a guess |
| Spend vs value | Cost per accepted change in minutes of a loaded engineer-hour (`--hourly-cost`, default $100) | A sense of scale. The rate is a parameter, not a finding |
| Budget burn and forecast | Quarter to date, plus the trailing 28-day daily run rate for the days left; the date the budget runs out, if it does | Linear on purpose: easy to explain and check |
| Anomalies | Agent sessions with at least 4x the median steps (minimum 25): runaway loops. Tasks above 3x the p90 of the same team, task type and main model | Like is compared with like, so a deliberate model change does not flag every task |
| Top spenders | Pseudonymous, with accepted changes and cost per accepted change next to the spend | For follow-up on anomalies, never a leaderboard ([ADR-0005](adr/0005-individual-metrics-are-not-leaderboards.md)) |

**The guardrail: cheaper must not mean worse.** `metrics.cost_quality_signal` fires when cost
per change falls while the change failure rate rises by more than a bar, or review latency
rises by more than 25%. The same function is a fourth [Goodhart guard](02-metrics.md) on
the PR scorecard and runs per team on spend (bar: 1 point for the org, 2 points per team,
which is a much smaller sample).

## Results on the synthetic demo

The story: four teams (31 engineers) start Q2 2026 with every agent, review and SRE call on
the frontier model and little prompt caching. On 2026-05-04 they adopt eval-driven routing
and a shared prompt cache. The mobile team instead forces the small model everywhere.
Data runs to 2026-06-10, so the last changes are still in flight. Output of `devex spend`:

| Headline | Value |
|---|---:|
| **Cost per accepted change** (merged, not reverted) | **$4.63** |
| All-in cost per accepted change | $5.34 |
| Accepted / reverted / abandoned / in flight | 511 / 18 / 25 / 9 |
| Total spend | $2,835 (3,979 usage events) |
| Waste: reverted + abandoned work | $142 (5.0% of spend; reverted $39, abandoned $102) |
| Cost per resolved incident | $13.30 (8 of 9 resolved) |
| Prompt-cache hit ratio | 41.2% |
| Frontier share of $ / of tokens | 80.5% / 33.2% |
| Routing opportunity | $820 routable, $724 saving |
| Value check at $100/h (assumption) | 2.8 engineer-minutes per accepted change |

| Period | $ per day | Cost per accepted change | Revert rate | Frontier $ | Cache | Waste | Routing saving left |
|---|---:|---:|---:|---:|---:|---:|---:|
| before 2026-05-04 | $59.21 | $7.30 | 2.9% | 87.5% | 25.6% | 4.6% | $724 |
| from 2026-05-04 | $23.19 | **$2.32** | 3.9% | 64.7% | 54.3% | 5.9% | $0 |

| Surface | Share of $ | Share of tokens |
|---|---:|---:|
| coding_agent | 50.6% | 29.3% |
| code_review | 26.7% | 14.0% |
| ide_chat | 16.4% | 32.5% |
| sre | 3.8% | 1.7% |
| completion | 1.6% | 19.4% |

| Task type | Tasks | $ per task | Median | p90 |
|---|---:|---:|---:|---:|
| incident | 9 | $11.82 | $9.85 | $18.48 |
| agent_session | 260 | $5.57 | $1.26 | $9.86 |
| review | 530 | $1.43 | $0.37 | $4.82 |
| pr (authoring chat) | 303 | $0.60 | $0.28 | $1.63 |

What it says:

- **Cost per accepted change fell 68%**, from $7.30 to $2.32. Routing moved the calls the
  evals allow off the frontier model, and caching doubled. The routing opportunity before the
  change ($724) is spent by the end: none is left.
- **Quality held at org level, barely.** The revert rate rose from 2.9% to 3.9%, just under
  the 1-point bar, so the org guard is quiet. It is the number to watch next quarter.
- **The mobile team is the cautionary tale.** Its cost per accepted change fell from $14.44 to
  $0.29, the best on the page. Its revert rate rose from 3.6% to 9.7%. The team guard fires:
  "savings bought with quality". A cost-per-token dashboard would have celebrated it.
- **Agent sessions have a long tail.** The median is $1.26, the mean $5.57. Four runaway
  sessions (49 to 77 calls; the largest $244) explain much of it. Two of them merged anyway,
  which is why loops need a step budget and not only an outcome check.
- **Model mix is the lever.** The frontier tier is a third of the tokens and four fifths of
  the dollars.
- **Budget:** $2,835 of $4,500 spent (63%) with 78% of the quarter elapsed; the linear forecast
  is $3,322 (74%) at $24.34/day. At the pre-change rate of $59/day, the quarter would have
  overshot.
- **Scale check:** $4.63 per accepted change is about 3 minutes of a $100/h engineer. At
  these prices, the case for AI does not hinge on token cost. It hinges on whether the
  changes are good, which is why the headline is per *accepted* change.

## Limits

- **Synthetic.** The events are generated to show the method; the numbers are not a
  benchmark of any team, model or vendor.
- **Prices are illustrative** and list prices; enterprise, batch and regional prices differ.
  Seat licences (e.g. Copilot seats) are fixed costs outside the per-token view; add them to
  the all-in figure if you allocate them.
- **Attribution depends on tagging.** Untagged calls are counted in totals but cannot be tied
  to a change; report the unattributed share and drive it down.
- **Reverts are a proxy** for change failure, as in [doc 06](06-real-data.md).
- **Not observable on public repositories.** Open-source PRs do not reveal token spend, so the
  real-data dashboards leave these KPIs blank.
