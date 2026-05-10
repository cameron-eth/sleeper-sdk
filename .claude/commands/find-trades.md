# find-trades

Targeted trade search with positional filters. Builds 1-chip and 2-chip
packages from your roster and scores them with the KTC Value Adjustment
(stud premium + roster-spot adjustment) so lopsided-but-fair-looking
trades get correctly ranked.

## When to use this skill

- "Find me a trade for an RB"
- "Who can I get for Egbuka?"
- "I need to upgrade my WR2"
- "Liquidate my aging vets"
- "Show me realistic upgrades"

## Three modes

| Mode | Overpay range | Use when |
|---|---|---|
| `normal` | +300 to +3,500 KTC | You're willing to slightly overpay for the right player |
| `upgrade` | -5,000 to 0 | You want to net positive value (you "win" KTC on the trade) |
| `downtiering` | +300 to +5,000 | You're liquidating star talent for picks/depth (rebuilding) |

## How to run

### Normal mode — find a fair trade for an RB
```bash
python3 -m sleeper.cli find-trades camfleety --league "OGs" \
  --mode normal --position RB
```

### Upgrade mode — get more value than you give
```bash
python3 -m sleeper.cli find-trades camfleety --league "Meat Market" \
  --mode upgrade --position WR
```

### Downtiering mode — rebuild flow, ship aging stars
```bash
python3 -m sleeper.cli find-trades camfleety --league "OGs" \
  --mode downtiering --include "David Montgomery"
```

### Useful flags

- `--position WR RB`        target multiple positions
- `--include <name>`        only consider these as targets
- `--exclude <name>`        block specific targets (e.g. `--exclude "CeeDee Lamb"` to protect a star)
- `--min-overpay`/`--max-overpay`   override the auto-set range
- `--single-only`           1-for-1 trades only (no chip combinations)
- `--top N`                 max trades to show (default 15)

## What to do with the output

The command prints a ranked table:
- **Adj Δ** in [300, 3500] (normal) → the trade is "fair with a small stud premium owed"
- **Adj Δ** << 0 → you're getting value; **Adj Δ** >> 0 → you're overpaying

**Follow-ups:**

1. If you find a trade you like → preview it:
   ```bash
   python3 -m sleeper.cli send-trade camfleety --league "OGs" \
     --to-roster <id> --send <give> --get <get> --dry-run
   ```

2. If results are full of unrealistic bait (e.g. shipping a star for an upgrade), tighten:
   ```bash
   --exclude "CeeDee Lamb" "Jayden Daniels"
   ```

3. If nothing is in range → the target is out of reach with current chips.
   You probably need to add picks. Switch to `suggest-trades` for picks-aware combinations.

## Key context

- **QB chip discount**: aging QBs (28+/30+/32+) auto-discount to 0.75x/0.55x/0.45x of face KTC because they don't transact at face value
- **Stud premium**: when receiving an elite (8000+) target, the algorithm adds a 30% scarcity premium to the cost. Quality-gap penalty also applies when your best chip is far below the target.
- **`--include` filters TARGETS, not chips on the give side** — to protect chips, use `--exclude` (excludes them as targets, but they can still appear in your packages)
