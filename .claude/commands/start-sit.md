# start-sit

Answer "should I start X or Y this week?" using Sleeper's own weekly projections, re-scored under the league's actual scoring settings, with bye weeks and injury designations handled.

## Usage

```bash
python3 -m sleeper.cli start-sit <username> --players "<player1>" "<player2>" [...] [--league <name>] [--week N] [--slots N] [--verbose] [--json]
```

## What it does

- Fetches the week's projections from `api.sleeper.com` — the same numbers the Sleeper app shows
- **Re-scores them with the league's `scoring_settings`** instead of using generic PPR, so 6-point passing TDs, TE premium and bonus scoring are priced correctly
- Ranks availability *before* points: a player on bye or ruled Out never outranks someone who can actually play, regardless of the stale projection Sleeper still serves for him
- Reports a confidence band, because a sub-point projection edge is noise

## Confidence bands

Confidence is `margin / (margin + 3.0)` — continuous, so no decision hinges on landing either side of a threshold.

| Label | Margin | Meaning |
|-------|--------|---------|
| coin-flip | < 1.0 pt | Inside projection noise — either choice is defensible |
| lean | 1.0–3.0 pts | Real but soft edge |
| clear | > 3.0 pts | Start the higher one |

## Examples

```bash
# The basic question
python3 -m sleeper.cli start-sit camfleety --league "Meat Market" \
  --players "Daniel Jones" "Deshaun Watson"
```

```bash
# Two flex slots, three candidates — margin is #2 vs #3, the choice actually being made
python3 -m sleeper.cli start-sit camfleety --league "Meat Market" --slots 2 \
  --players "Jordan Addison" "Parker Washington" "Zavion Thomas"
```

```bash
# A future week, with the stat lines behind each projection
python3 -m sleeper.cli start-sit camfleety --league "Meat Market" --week 6 --verbose \
  --players "Jordan Addison" "Parker Washington"
```

```bash
# Whole-lineup version: current vs optimal starters, league-scored
python3 -m sleeper.cli lineup camfleety --league "Meat Market"
```

```bash
# Raw projection board, no league needed
python3 -m sleeper.cli projections --position QB --top 15
```

## Reading the output

- `Proj` is **league-scored**, not Sleeper's `pts_ppr` — they differ whenever scoring is non-standard
- `BYE` in the Opp column means no game; it is reported as a bye, not as a projection of 0.0
- A `0.0` next to an `Out` tag means the projection was discarded, not that Sleeper forecast zero
- `-` in the Proj column means Sleeper published no projection at all (deep bench) — that is unknown, not zero
- The `Why:` section lists every flagged candidate and the reason

## Follow-up chains

- Ruled-out starter in the lineup? → `lineup` to see the optimal swap, then `lineup-set` to apply it
- Need a replacement off waivers? → `waivers <username> --league <name>`
- Weighing a longer-term hold instead? → `market-value` / `ktc-trend` for dynasty value rather than this week's points

## Notes

- Names are matched ignoring punctuation and case, so `"DeZhaun Stribling"` finds `"De'Zhaun Stribling"`
- Resolution prefers the asker's own roster, then the rest of the league, then all of the NFL — so scouting a leaguemate's player or a free agent works
- Without `--week` it uses Sleeper's current week; without `--league` it requires the user to have exactly one league
- Pass more players than `--slots`, or there is no decision to make
- Read-only — no `SLEEPER_TOKEN` needed
- Kicker and DEF totals can drift slightly from the app's: leagues score plain `fgmiss`, but Sleeper only projects `fgmiss_30_39` / `fgmiss_40_49`, so that rule cannot be priced. Skill positions are unaffected
