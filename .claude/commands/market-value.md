# market-value

Analyze a player's actual market value vs their KTC listed value, using real trade data.

## Usage

```bash
python3 -m sleeper.cli market-value "<player name>" [--format sf|1qb]
```

## What it does

- Fetches the player's current KTC dynasty value
- Pulls recent trades from the KTC trade database that include this player
- Uses **proportional surplus attribution** to isolate the player's implied price in multi-player trades:
  - `player_weight = player_ktc_value / player_side_total`
  - `implied_price = player_ktc_value + (player_weight * surplus)`
- Reports: KTC listed value, median/mean market value, % of KTC, and a buy/sell signal
- Shows a full trade-by-trade breakdown table

## Signals

| Signal | Meaning |
|--------|---------|
| UNDERVALUED | Trades above KTC listed (>105% of KTC) — potential buy |
| FAIRLY VALUED | Trades near KTC listed (95-105%) |
| OVERVALUED | Trades below KTC listed (<95% of KTC) — potential sell |

## Examples

```bash
# Superflex (default)
python3 -m sleeper.cli market-value "Ja'Marr Chase"

# 1QB format
python3 -m sleeper.cli market-value "Justin Jefferson" --format 1qb

# Multi-word name
python3 -m sleeper.cli market-value "Bijan Robinson" --format sf
```

## Notes

- KTC caps values at 9,999 — top players often trade above this
- Filters trades by format (SF leagues require qbs >= 2)
- Results cached for 1 hour in `~/.tmp/sleeper_sdk_cache/ktc/`
- Force refresh cache: add `force_refresh=True` in Python or clear the cache dir
