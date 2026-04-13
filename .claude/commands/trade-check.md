# trade-check

Evaluate a proposed dynasty trade using KTC values. Shows what you give vs get and a verdict.

## Usage

```bash
python3 -m sleeper.cli trade-check --give "<player1>" ["<player2>" ...] --get "<player1>" ["<player2>" ...] [--format sf|1qb]
```

## What it does

- Looks up each player's current KTC dynasty value
- Sums both sides of the trade
- Computes net value difference and percentage
- Returns a verdict: WIN / SLIGHT WIN / SLIGHT LOSS / LOSS

## Verdict thresholds

| Result | Net diff |
|--------|----------|
| WIN | +500 or more |
| SLIGHT WIN | +1 to +499 |
| SLIGHT LOSS | -1 to -499 |
| LOSS | -500 or worse |

## Examples

```bash
# Simple 1-for-1
python3 -m sleeper.cli trade-check \
  --give "Ja'Marr Chase" \
  --get "Justin Jefferson" "2026 Early 1st" \
  --format sf

# Multi-player trade
python3 -m sleeper.cli trade-check \
  --give "Drake Maye" "Emeka Egbuka" \
  --get "Bijan Robinson" "2027 Mid 2nd" \
  --format sf
```

## Notes

- Player names are fuzzy-matched — partial names work (e.g., "Bijan" finds "Bijan Robinson")
- Draft picks should be passed as the pick tier label used in KTC, e.g. "2026 Early 1st", "2027 Mid 2nd"
- This tool only checks KTC listed values, not actual market trading prices
- For actual market prices (what players really trade for), use `market-value`
