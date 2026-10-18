---
brief_id: br_2026-10-21_ai_01
schema_version: 1
theme: "AI and compute"
generated_at: 2026-10-21T13:00:00Z
expires_at: 2026-10-21T19:00:00Z
constraints:
  max_total_usd: 100
  max_position_pct: 25
  allowed_order_types: [limit]
  stop_loss_pct: 8
positions:
  - asset: ASSET-A-USD
    action: buy
    allocation_pct: 20
    order_type: limit
    entry: limit at or below 2% under last price
    confidence: medium
  - asset: ASSET-B-USD
    action: hold
    allocation_pct: 0
    confidence: low
---

# Instructions for the trading agent
Follow only the frontmatter. Do not trade after expires_at.
Use limit orders only. Exit any position down 8% from entry.

## Thesis
Sentiment on ASSET-A turned positive across several independent signals.

## Risks and what would change the plan
A reversal in sentiment would invalidate the thesis.

