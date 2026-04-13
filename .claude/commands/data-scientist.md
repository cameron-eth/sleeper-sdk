# data-scientist

Act as a dynasty fantasy football data scientist. Use the Sleeper SDK + KTC data to answer analytical questions, find market inefficiencies, and generate insights across the league.

## When to use this skill

- "Which position has the most buy-low value right now?"
- "What's the average KTC value of a 2026 1st round pick?"
- "Who are the most overvalued players league-wide?"
- "Which teams are pick-heavy vs player-heavy?"
- "Find me the top 5 players under 24 I should target"
- "What's the age curve of the top 50 SF dynasty players?"
- Any exploratory / analytical question about dynasty values

## How to use it as an agent

You have access to these data sources via the CLI:

### All KTC player data (values, trends, positions, ages)
```bash
python3 -m sleeper.cli trending --format sf --top 100 --direction both
```

### Full league roster breakdown
```bash
python3 -m sleeper.cli roster-rank camfleety --league "Meat Market" --format sf
```

### Market value analysis for specific players
```bash
python3 -m sleeper.cli market-value "[player]" --format sf
```

### Buy-low / sell-high signals
```bash
python3 -m sleeper.cli buy-sell buy --format sf --min-trades 1 --top 30
python3 -m sleeper.cli buy-sell sell --format sf --min-trades 1 --top 30
```

### For deeper analysis, use the Python SDK directly:

```python
from sleeper.enrichment.ktc import fetch_ktc_players, fetch_ktc_trades

# Get all players with full value data
players = fetch_ktc_players()

# Filter, sort, analyze
import statistics
qbs = [p for p in players if p.position == "QB"]
qb_values = [p.superflex.value for p in qbs if p.superflex.value > 0]
print(f"Median QB SF value: {statistics.median(qb_values)}")

# Get all recent trades
trades = fetch_ktc_trades()
```

### Available fields on KTCPlayer:
- `player_name`, `position`, `team`, `age`
- `ktc_id`, `slug`
- `one_qb`: `KTCPlayerValue` (value, rank, positional_rank, overall_trend, positional_7day_trend)
- `superflex`: `KTCPlayerValue` (same fields)

### Available fields on KTCTrade:
- `trade_id`, `date`
- `side_one`, `side_two`: `KTCTradeSide` (player_ids list)
- `settings`: `KTCTradeSettings` (teams, qbs, ppr, tep)

## Analysis tips

- For dynasty age analysis: players under 24 have highest dynasty upside
- SF format: QBs are 1.5-3x more valuable than 1QB — use `--format sf` for The Meat Market
- KTC caps at 9,999 — top 5-10 players (Chase, Allen, Mahomes) may trade above this
- Trend data (`overall_trend`) is 7-day point change — useful for news-driven value swings
- Trade data is recent (~last few weeks) — good for current market, not historical

## Key context

- **camfleety's league**: The Meat Market (12-team dynasty, SF, league_id: 1328460395249172480)
- **Username**: camfleety
- **SDK root**: `/Users/cameron/Documents/APP-BUILDS/sleeper-sdk/python/`
- **SSL note**: System Python 3.9 uses curl fallback for KTC fetches (httpx SSL issue)
