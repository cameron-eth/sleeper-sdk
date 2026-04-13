# trending

Show players with the biggest KTC dynasty value movement over the last 7 days.

## Usage

```bash
python3 -m sleeper.cli trending [--format sf|1qb] [--top N] [--direction up|down|both] [--position QB|RB|WR|TE]
```

## What it does

- Fetches current KTC dynasty rankings (cached 1hr)
- Reads the 7-day `overallTrend` value for each player
- Sorts by magnitude of change (or direction if specified)
- Shows value, absolute change, and percentage change

## Examples

```bash
# Top 20 movers (up or down) in SF
python3 -m sleeper.cli trending --format sf

# Top 10 risers this week
python3 -m sleeper.cli trending --direction up --top 10

# Falling WRs only
python3 -m sleeper.cli trending --direction down --position WR

# Rising QBs in 1QB format
python3 -m sleeper.cli trending --direction up --position QB --format 1qb --top 5
```

## Notes

- The trend value is KTC's own 7-day point change (e.g., +40 means gained 40 KTC points)
- Picks are excluded from results
- Players with 0 KTC value are excluded
- Useful for spotting injury news, breakout performances, or offseason moves
