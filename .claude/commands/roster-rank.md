# roster-rank

Rank every team in a Sleeper dynasty league by total KTC roster value.

## Usage

```bash
python3 -m sleeper.cli roster-rank <username> [--league <name>] [--format sf|1qb]
```

## What it does

- Fetches all rosters in the specified Sleeper league
- Maps each player on every roster to their KTC dynasty value
- Ranks teams from highest to lowest total roster value
- Shows player count, total value, and best player per team

## Examples

```bash
# Rank all teams in The Meat Market (dynasty, SF)
python3 -m sleeper.cli roster-rank camfleety --league "Meat Market" --format sf

# 1QB format
python3 -m sleeper.cli roster-rank camfleety --league "OGs" --format 1qb
```

## Notes

- If `--league` is omitted and the user has only one league, it auto-selects
- Player-to-KTC mapping uses name + position + team matching (~92% match rate)
- Unmatched players (rookies not yet on KTC, etc.) count as 0 value
- camfleety's dynasty league: **The Meat Market** (league_id: 1328460395249172480)
