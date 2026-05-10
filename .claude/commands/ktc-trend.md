# ktc-trend

Reads the local daily KTC snapshot history (`data/ktc/<date>.json`) and
shows a player's KTC value over time. Useful for spotting a player on the
rise or in free-fall before the rest of the league reacts.

## When to use this skill

- "How has <player>'s value changed?"
- "Is <player> trending up or down?"
- "Show me the biggest movers"
- "What did <player> cost a month ago?"

## How to run

### Single player history
```bash
python3 -m sleeper.cli ktc-trend "Emeka Egbuka"
```

### Compare a window
```bash
python3 -m sleeper.cli ktc-trend "Brock Bowers" --days 30
```

## Useful follow-ups

- `trending` — top 7-day movers across the league (live KTC, not snapshots)
- `buy-sell` — pair a falling KTC with weak production = buy candidate
- `find-trades --include "<player>"` — once you confirm the dip, find packages

## Key context

- Snapshots are committed daily by the `ktc-snapshot.yml` GitHub Action
- Earliest snapshots set the timeline; trend window is bounded by available data
- Picks are tracked too (RDP rows like "2027 Mid 1st")
