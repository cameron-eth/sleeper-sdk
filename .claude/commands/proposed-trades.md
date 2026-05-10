# proposed-trades

Lists every trade in a league — pending, completed, rejected, cancelled —
with KTC valuation and a per-side win/loss verdict from the value-adjusted
math. Hits Sleeper's private GraphQL endpoint via `SLEEPER_TOKEN`.

## When to use this skill

- "What trades are happening in the league?"
- "Show me historical trades in OGs"
- "Who's buying / selling?"
- "Who fleeced who in the offseason?"
- "What did <user> trade for?"
- "Are there any pending trade offers?"

## How it works

1. Fetches **league-wide** trades by passing every roster_id to bypass the
   default auth-scoping (which would otherwise only show trades you're a
   party to).
2. Resolves player adds, drops, and traded picks (KTC-valued).
3. Computes per-side KTC totals and applies the same value adjustment as
   `trade-check` and `find-trades` for a fair-or-lopsided verdict.

## Privacy boundary

Sleeper's API exposes:
- ✅ All **completed** trades league-wide
- ❌ **Proposed / rejected / cancelled** trades for parties that don't
  include the authenticated user — these are private to the rosters involved

So historical-completed analysis is the strongest use case.

## How to run

### Auth setup (one-time)
```bash
# Grab JWT from DevTools → Network → graphql → Authorization header
export SLEEPER_TOKEN='eyJhbGc...'
```

### See every historical trade in a league
```bash
python3 -m sleeper.cli proposed-trades camfleety --league "OGs"
```

### Filter to specific users (case-insensitive substring on display name)
```bash
python3 -m sleeper.cli proposed-trades camfleety --league "OGs" \
  --user romanempire ssyork zaybanga
```

### Filter by status
```bash
python3 -m sleeper.cli proposed-trades camfleety --league "Meat Market" \
  --status complete
```

Statuses: `proposed`, `complete`, `rejected`, `cancelled`, `vetoed`.

### Useful flags

- `--limit N`  cap fetch (default 200)
- `--user <name> [<name> …]`   substring-match on display name
- `--status <statuses…>`       any of the 5 above

## What to do with the output

The output groups trades by transaction id, shows each side's adds + picks
with KTC totals, and renders a verdict:

- **Raw KTC delta** — face-value swing
- **Stud premium** — what the receive side owes for landing a top-tier asset
- **Adjusted** — final delta after premium
- **Verdict** — WIN / FAIR / slight edge

**Follow-ups by analysis goal:**

| Question | Chain to |
|---|---|
| "How does <user> trade in general?" | `--user <name>` then `trade-guru` |
| "Who's been targeting my players?" | grep your roster name in the output |
| "What patterns show up in trades?" | pipe through `awk` or feed completed-trades JSON to `data-scientist` |
| "Should I propose to <partner>?" | look at their historical wins/losses, then `find-trades` with their roster as target |

## Key context

- **Auth scope**: by default the GraphQL endpoint returns only trades you're
  a party to. This skill explicitly passes all roster_ids to surface the
  full league activity (completed only — privacy).
- **Picks**: parsed from Sleeper's `"original_roster,season,round,from,to"`
  string format and KTC-valued via "Mid" tier as default
- **Histogram**: the league-wide summary line shows the actual league total.
  The user-filter line (when used) shows the filtered subset count.
