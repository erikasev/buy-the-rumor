# ADR-0002: Briefs are research leads, not trade orders

| Field | Value |
| --- | --- |
| Status | Proposed |
| Date | 2026-10-22 |
| Deciders | Erika, Daniel |

## Context

The first pipeline treated posts from market commentators as the basis for direct positions: it read tweets and wrote Coinbase spot buys and holds. But a post is one person's observation or opinion, often unverified. Acting on it directly turns chatter into trades.

What makes the posts valuable is what they point at. A commentator noting that CDS prices imply higher default rates is a lead: research the exposed companies, and if the user trades options, consider puts. The research and the decision belong to the agent that acts on the brief, using real data.

## Decision

The brief is a research brief for another agent.

- **Leads, not positions.** The strategist returns up to five leads. Each has the signal observed, the hypothesis it suggests, concrete research steps, possible expressions (instrument class, underlying, one-line idea), a horizon, a conviction level, and what would invalidate it. `positions` stays in the schema for v1 compatibility and is empty.
- **Any instrument the user allows.** Expressions may use stocks, ETFs, options, futures, crypto, bonds, FX or commodities, as long as the user's rules permit that instrument class. Code rejects an expression the rules don't allow.
- **The user's rules carry over.** A first LLM step reads the rules the user stated (instruments, sectors, sizes, stop-loss, frequency, horizon, anything else) into `user_rules`. They are copied into the brief unchanged for the downstream agent.
- **Fixed caps stay as an upper bound.** `constraints` is our hard limits ($100, 25% per position, limit orders, 8% stop-loss), tightened by any number the user stated. The user can make them stricter, never looser.
- **Every lead is grounded.** Each lead must cite at least one collected post, checked in code. Citations are then stripped (ADR-0001), and code rejects any user-facing text that names an account, handle or link.

## Consequences

- The brief changes additively within contract v1: `user_rules` and `leads` are new optional fields, and `positions` may be empty. Readers that ignore unknown fields, including the web service today, keep working.
- The web page needs a section to show `leads` and `user_rules`. Until then it shows the overview (`thesis`) and `risks` only.
- Rules extraction adds one cheap LLM call per brief. If it can't produce valid rules, the job fails rather than run without them.
- Rules the code can't check mechanically (sectors, frequency, free-text rules) rely on the prompt and on the downstream agent following the brief's instructions.
