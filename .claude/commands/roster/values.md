---
description: "KTC dynasty values for every player on one roster in a Sleeper league. Use for \"what is my roster worth\", \"show me my players' values\"."
argument-hint: "<username> [--league <name>] [--format sf|1qb]"
---
# league-values

Show KTC dynasty values for all players on your roster in a Sleeper league.

## Usage

```bash
python3 -m sleeper.cli league-values <username> [--league <name>] [--format sf|1qb]
```

## What it does

- Fetches the user's Sleeper leagues for the current season
- Resolves to a single league (or prompts to pick one)
- Finds the user's roster in that league
- Maps each player to their KTC dynasty value
- Prints roster sorted by value with total

## Examples

```bash
# a dynasty superflex roster
python3 -m sleeper.cli league-values <username> --league "<league>" --format sf

# 1QB redraft
python3 -m sleeper.cli league-values <username> --league "<league>" --format 1qb
```

## Notes

- Uses current year automatically (no hardcoded season)
- Leagues resolve from the username at run time; do not hardcode one.
