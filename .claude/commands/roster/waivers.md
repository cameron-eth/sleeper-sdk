---
description: "Rank the free-agent pool by KTC dynasty value, with age and positional-need filters. Use for \"who should I pick up\", \"best free agent\", \"anyone worth adding on waivers\", \"who is available\"."
argument-hint: "<username> [--league <name>] [--top N] [--age-max N]"
---
# roster:waivers

Ranked free-agent pool for a league — who is actually available and what
they are worth.

## When to use this skill

- "Who should I pick up?"
- "Best available free agent?"
- "Anyone worth adding?"
- "Is there a young RB on waivers?"

## How to run

```bash
python3 -m sleeper.cli waivers <username> --league "<league>" --top 25

# Dynasty-relevant: young players only
python3 -m sleeper.cli waivers <username> --league "<league>" --age-max 25

# Weight toward a position you actually need
python3 -m sleeper.cli waivers <username> --league "<league>" --position-priority RB
```

## Key context

- **Ranked by dynasty value, not by this week.** A 29-year-old with a good
  matchup ranks below a 22-year-old stash. For a win-now pickup, cross-check
  `/roster:projections` for the actual week.
- Pair with `/roster:lineup` first — an empty or injured slot tells you
  *which* position to filter on.
- Claiming is a write: `/roster:moves` handles add, drop and FAAB claims,
  and previews before it fires.
