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
# camfleety's Meat Market dynasty roster
python3 -m sleeper.cli league-values camfleety --league "Meat Market" --format sf

# 1QB redraft
python3 -m sleeper.cli league-values camfleety --league "OGs" --format 1qb
```

## Notes

- Uses current year automatically (no hardcoded season)
- camfleety's leagues: The Meat Market (dynasty/SF), The OGs (keeper/SF), Ball Knowers, Da Skreets
