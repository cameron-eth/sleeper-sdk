# draft-assist

Live draft co-pilot: polls a Sleeper draft and prints a KTC-valued best-available board after every pick.

## Usage

```bash
# From python/ — live mode (polls until the draft ends, Ctrl-C to stop)
python3 scripts/draft_assist.py USERNAME --league NAME

# One-shot board snapshot (also works pre-draft for prep)
python3 scripts/draft_assist.py USERNAME --league NAME --once

# Options
#   --draft-id ID     target a draft directly (skips league lookup)
#   --interval SECS   poll frequency (default 8)
#   --top N           overall board depth (default 12)
```

## What it does

- Discovers the user's draft for the current season (or use `--draft-id`)
- Auto-detects SF vs 1QB from league roster positions and uses the matching KTC column
- Values come from `data/ktc/latest.json` (the committed daily snapshot) — no scrape, no token
- Announces each new pick with its KTC value and pre-draft board rank; flags the user's own picks
- Reprints best available: top N overall, plus top 5 per position with `|CLIFF` markers on value drops ≥400
- Shows who's on the clock and how many picks until the user's turn (snake-aware)
- Tracks the user's roster as it builds

## Caveats

- Read-only — it cannot make picks; draft in the Sleeper app
- KTC is a **dynasty** market; in redraft leagues treat values as a market signal, not a ranking
- K and DEF have no KTC values and never appear on the board
- If the user has no draft slot assigned yet, "your turn in N picks" is unavailable until Sleeper assigns slots

## When invoked as a skill

Run the script in the background (live mode) or `--once` for a snapshot, then interpret the board for the user: point out positional cliffs, whether value at their next pick favors a position, and any reaches/steals in recent picks. During a live draft, re-check shortly before their turn.
