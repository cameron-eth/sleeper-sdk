# pe-ratio

Computes a player's "P/E ratio" — KTC market price ÷ real-world fantasy
production (FFPG). High P/E = expensive relative to output; low P/E =
cheap relative to output. Useful for spotting overhyped names and
buy-low candidates.

## When to use this skill

- "Who's overpriced right now?"
- "Which players are cheap relative to production?"
- "Who's the biggest stock-vs-stats mismatch?"
- "Find me undervalued players"

## How to run

```bash
python3 -m sleeper.cli pe-ratio --position WR --top 20
```

### Useful flags

- `--position QB|RB|WR|TE`   filter by position
- `--top N`                  rows to display
- `--format sf|1qb`          KTC format (default sf)

## Reading the output

- **Low P/E (e.g. < 200)** → producing more than market thinks. Buy candidate.
- **High P/E (e.g. > 600)** → market is paying for hype/upside not production. Sell candidate.

## Useful follow-ups

- `buy-sell` — same principle, different framing (over/under the trend line)
- `trade-check` — once you spot a target, propose a trade
- `find-trades --include "<player>"` — find packages that land them
