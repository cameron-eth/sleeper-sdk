# buy-sell

Find players trading significantly below (buy-low) or above (sell-high) their KTC listed value in actual trades.

## Usage

```bash
python3 -m sleeper.cli buy-sell <buy|sell> [--format sf|1qb] [--top N] [--position POS] [--min-trades N]
```

## What it does

- Analyzes every player in every KTC trade using proportional surplus attribution
- Computes each player's median implied market price across all trades
- Compares to their KTC listed value
- **buy**: Shows players where market value < 90% of KTC (i.e., getting them costs less than KTC says)
- **sell**: Shows players where market value > 110% of KTC (i.e., you can sell for more than KTC says)

## Examples

```bash
# Buy-low candidates in SF (all positions)
python3 -m sleeper.cli buy-sell buy --format sf

# Sell-high WRs with at least 3 trades of data
python3 -m sleeper.cli buy-sell sell --format sf --position WR --min-trades 3

# Buy-low RBs in 1QB
python3 -m sleeper.cli buy-sell buy --format 1qb --position RB --top 10

# All sell-high candidates with at least 1 trade
python3 -m sleeper.cli buy-sell sell --min-trades 1 --top 20
```

## Notes

- This uses actual trade data — much more meaningful than KTC rankings alone
- Players with KTC value < 500 are excluded (prevents noise from fringe players)
- Default `--min-trades 2` filters out single-trade flukes
- Slow first run (scrapes KTC trade database) — subsequent runs use 1hr cache
- Picks are excluded
