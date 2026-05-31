# trade-partners

Ranks every other owner in a league by **trade-partner compatibility**
with you. Combines three signals into a single engagement score:

1. **Archetype synergy** — rebuilders pair with contenders, contenders
   avoid each other, etc.
2. **Positional fit** — your surplus position vs their need, and vice versa
3. **History** — past completed trades between you, weighted by activity
   and net KTC swing

## When to use this skill

- "Who should I trade with?"
- "Who's the best partner for me right now?"
- "Which owners are worth engaging?"
- "Rank my league by trade compatibility"
- "Who exploits me — who do I exploit?"

## How to run

### Default — top 12 partners
```bash
python3 -m sleeper.cli trade-partners camfleety --league "Meat Market"
```

### Top 5 only
```bash
python3 -m sleeper.cli trade-partners camfleety --league "OGs" --top 5
```

### 1QB league
```bash
python3 -m sleeper.cli trade-partners camfleety --league "Slime Season" --format 1qb
```

## Reading the output

```
#  Owner       Score  Arch         Syn  Pos  Hist  Rationale
1  romanempire   +12  REBUILDING   +5   +4   +3    REBUILDING · wants your QB · 4 prior trades (+13,200 net)
2  zaybanga      +9   CONTENDER    +5   +2   +2    CONTENDER · has WR you need · 3 prior trades (+8,400 net)
```

- **Score** — final engagement priority (typical range −5 to +15)
- **Syn** — archetype synergy points (−3 to +5)
- **Pos** — positional fit (2 pts per surplus↔need overlap)
- **Hist** — historical trade record points (activity + net KTC ÷ 5K)
- **Rationale** — one-line summary you can paste into a DM

## Useful follow-ups

| Goal | Chain to |
|---|---|
| Build a trade for the top partner | `find-trades --include <their player>` |
| Verify a specific offer | `trade-check --give ... --get ...` |
| See historical trades with that owner | `proposed-trades --user <name>` |
| Fire the trade | `send-trade --to-roster <id> --send ... --get ...` |

## Key context

- **Archetype source**: lazy-loads `analytics/gm_mode.py` with the same
  classification logic as the `gm-mode` command. Owners whose roster
  can't be classified (early season, no production data) get archetype
  `UNKNOWN` and a 0 synergy score.
- **History is optional**: with `SLEEPER_TOKEN` set, the command pulls
  completed league trades and computes the user-net-KTC per partner.
  Without auth, every partner gets the small "untapped" history bonus.
- **3-way trades are skipped** in history scoring — they distort the
  pairwise net.
- **Top score caps**: `Hist` is capped at ±8 so a single huge historical
  win doesn't dominate; macro fit matters more than past results.
