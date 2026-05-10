# suggest-trades

Whole-league 1-for-1 trade scan that surfaces trades correcting positional
imbalances. Less surgical than `find-trades` (which is target-driven) — this
is a "what's the best move available right now?" sweep.

## When to use this skill

- "Suggest some trades for me"
- "What's the best trade available?"
- "Show me 10 ideas across the league"
- "Find me one-for-one trades"

## How to run

```bash
python3 -m sleeper.cli suggest-trades camfleety --league "OGs" --top 10
```

## How it differs from find-trades

| Dimension | `suggest-trades` | `find-trades` |
|---|---|---|
| Scan scope | Every roster pair, 1-for-1 | Target-position-driven |
| Scoring | Positional imbalance correction | Adjusted overpay buckets |
| Use when | Open-ended exploration | Targeted upgrade |

## Useful follow-ups

- A trade in the top 5 looks promising → run `trade-check` on it
- The best trades all consolidate roster spots → switch to `find-trades` for chip-pairing
- You want a trade involving picks → currently picks-aware logic lives in `find-trades` only
