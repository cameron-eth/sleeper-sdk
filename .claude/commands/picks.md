# picks

Show all future draft pick assets in a Sleeper dynasty league with their current KTC values.

## Usage

```bash
python3 -m sleeper.cli picks <username> [--league <name>] [--format sf|1qb] [--owner <name>] [--traded-only]
```

## What it does

- Fetches all traded picks from the Sleeper league
- Shows every future pick (current year + 2 future years, rounds 1-4)
- Maps each pick to its current holder (tracking trades)
- Looks up each pick's KTC dynasty value by tier (Early/Mid/Late + round)
- Sorted by KTC value descending

## Examples

```bash
# All picks in The Meat Market
python3 -m sleeper.cli picks camfleety --league "Meat Market" --format sf

# Only traded picks (picks that have changed hands)
python3 -m sleeper.cli picks camfleety --league "Meat Market" --traded-only

# Show only camfleety's picks
python3 -m sleeper.cli picks camfleety --league "Meat Market" --owner camfleety

# HarryBushWacker's pick haul
python3 -m sleeper.cli picks camfleety --league "Meat Market" --owner Harry --traded-only
```

## Output columns

| Column | Description |
|--------|-------------|
| Pick | Year + tier + round (e.g., "2026 Early 1st") |
| Slot | Draft slot within the round (P01-P12) |
| KTC Value | Current KTC dynasty value for this pick tier |
| Current Holder | Who owns the pick now |
| Original Owner | Who originally owned the pick (blank if not traded) |
| Traded | Y/N whether this pick has been traded |

## Notes

- Pick tiers are based on league size: Early = top 1/3, Mid = middle 1/3, Late = bottom 1/3
- The Meat Market is 12 teams: P01-P04 = Early, P05-P08 = Mid, P09-P12 = Late
- 2026 picks may already be gone (drafted) depending on the time of year
- camfleety's dynasty league: **The Meat Market** (league_id: 1328460395249172480)
