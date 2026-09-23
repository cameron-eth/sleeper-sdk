---
description: "Rank every team in a Sleeper dynasty league by total KTC roster value. Use for \"who has the best roster\", \"where do I rank\", \"power rankings by value\"."
argument-hint: "<username> [--league <name>] [--format sf|1qb]"
---
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
# Rank all teams in a dynasty superflex league
python3 -m sleeper.cli roster-rank <username> --league "<league>" --format sf

# 1QB format
python3 -m sleeper.cli roster-rank <username> --league "<league>" --format 1qb
```

## Notes

- If `--league` is omitted and the user has only one league, it auto-selects
- Player-to-KTC mapping uses name + position + team matching (~92% match rate)
- Unmatched players (rookies not yet on KTC, etc.) count as 0 value
- The dynasty league resolves from `--league`; do not hardcode an ID.
