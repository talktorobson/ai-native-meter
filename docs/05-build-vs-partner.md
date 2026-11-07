# 05 · Build vs partner: decisions as data

Implemented in [`src/devex/vendor_scorecard.py`](../src/devex/vendor_scorecard.py).

## Where each motion fits

| Motion | Use when | Examples |
|---|---|---|
| **Buy** | Commodity capability, fast-moving market, low differentiation | IDE assistants, generic chat |
| **Partner / co-build** | Speed matters and the partner benefits from your scale or domain | Code review, AI SRE diagnosis |
| **Build** | The capability *is* your context, or the risk profile forbids external control | Context layer, eval sets, routing policy |

The rule of thumb: **own the context, the evals and the interfaces; rent the rest.**
Whoever owns those three can swap any vendor in a quarter.

## The structured engagement

1. **Two-week technical spike** under NDA on non-sensitive repositories. Exit if the
   integration surface is closed or data terms fail.
2. **Six-week paid pilot** against the *shared* internal eval set, with written success
   thresholds agreed before it starts.
3. **Decision review** with the scorecard below. Ties trigger a time-boxed bake-off,
   never a debate.

## Scorecard

- **Hard gates** (fail any → disqualified): data residency, security certification,
  no training on customer code, audit logging.
- **Weighted criteria**: eval quality (for review: F0.5 on the seeded benchmark),
  integration effort, time to value, cost at projected scale, operability.
- **Lock-in risk** (0–100%) from five factors: data export, open interfaces, model
  choice, self-host option, exit in under 90 days. It discounts the weighted score.

Demo output on illustrative candidates:

| Candidate | Motion | Score | Lock-in risk | Gates |
|---|---|---|---|---|
| Vendor B co-build | partner | 0.70 | 20% | pass |
| In-house reviewer on context layer | build | 0.67 | 0% | pass |
| Vendor A review bot | buy | 0.62 | 60% | pass |
| Vendor C SaaS | buy | 0.59 | 100% | data_residency |

**Verdict:** too close to call between the partner and the build option, so run a
time-boxed bake-off on the shared eval set. Note that the vendor with the *best raw
quality* is excluded by a hard gate, and the captive option is discounted by lock-in.
That is the point: the scorecard surfaces trade-offs a demo hides.

## Managing lock-in during the relationship

- Contract for data export, model choice and exit assistance up front.
- Integrate through the gateway and MCP/OpenAPI interfaces, never directly into
  developer tools.
- Keep running the eval set quarterly against the incumbent and at least one
  alternative.
- Track vendor spend per merged PR alongside first-party cost.
