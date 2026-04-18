# sleeper-sdk

A Python SDK for the [Sleeper Fantasy Football API](https://docs.sleeper.com). Typed, async-first, with built-in rate limiting, player caching, KTC dynasty values, and trade analytics.

## Install

```bash
cd python
pip install -e .
```

## Quick Start

```python
import asyncio
from sleeper import SleeperClient

async def main():
    async with SleeperClient() as client:
        user = await client.users.get_user("your_username")
        leagues = await client.users.get_user_leagues(user.user_id, season="2024")
        for lg in leagues:
            print(f"{lg.name} — {lg.total_rosters} teams")

asyncio.run(main())
```

Sync usage for scripts and notebooks:

```python
from sleeper import SleeperClient

client = SleeperClient()
league = client.sync(client.leagues.get_league("1328460395249172480"))
print(league.name)
```

## API Coverage

| Module | Methods |
|--------|---------|
| `client.users` | `get_user`, `get_user_leagues`, `get_user_drafts` |
| `client.leagues` | `get_league`, `get_leagues_for_user`, `get_rosters`, `get_users`, `get_matchups`, `get_winners_bracket`, `get_losers_bracket`, `get_transactions`, `get_traded_picks` |
| `client.drafts` | `get_draft`, `get_drafts_for_user`, `get_drafts_for_league`, `get_picks`, `get_traded_picks` |
| `client.players` | `get_all_players` (cached), `get_trending` |
| `client.state` | `get_state` |

## Enrichment

Modules that add external data on top of Sleeper's API.

### KTC Dynasty Values

Scrapes [KeepTradeCut](https://keeptradecut.com) player values and matches them to Sleeper player IDs with fuzzy name matching. Supports both Superflex and 1QB formats.

```python
from sleeper.enrichment.ktc import fetch_ktc_values, match_ktc_to_sleeper, detect_scoring_type

# Scrape KTC — cached for 24 hours automatically
ktc_players = fetch_ktc_values()

# Force refresh (use this in a daily cron job)
ktc_players = fetch_ktc_values(force_refresh=True)

# Match to Sleeper IDs
all_players = await client.players.get_all_players()
matched = match_ktc_to_sleeper(ktc_players, all_players)

# Auto-detect SF vs 1QB from league settings
scoring = detect_scoring_type(league)  # "sf" or "1qb"
```

**Cache:** KTC values are cached in memory + on disk (`$TMPDIR/sleeper_sdk_cache/ktc_values.json`) with a 24-hour TTL. Set up a cron to call `fetch_ktc_values(force_refresh=True)` once daily to keep values fresh without hitting KTC on every SDK call.

### Marketplace Values

Builds real acquisition costs from actual trade activity across leagues — what players *actually* trade for vs what KTC says they're worth.

```python
from sleeper.enrichment.marketplace import build_marketplace_values, compare_ktc_vs_actual

# Build marketplace from trade observations
marketplace = build_marketplace_values(trade_observations, ktc_values)

# Compare KTC theoretical value vs actual trade cost
comparisons = compare_ktc_vs_actual(marketplace)
for comp in comparisons:
    print(f"{comp.player_id}: KTC={comp.ktc_value} Actual={comp.median_acquisition_cost} ({comp.signal})")
    # signal = "BUY" (cheaper than KTC), "SELL" (more expensive), or "FAIR"
```

**Multi-player trade isolation:** For N-for-N trades, each player's acquisition cost is isolated using subtraction — `cost = other_side_total - sum(companions)` — rather than proportional attribution, so the engine doesn't assume KTC ratios are correct.

## Analytics

Cross-league analysis tools for user-level trade evaluation.

### User Trade Collection

Aggregates rosters, users, and transactions across all of a user's leagues and seasons.

```python
from sleeper.analytics.user_collector import collect_user_league_snapshots, extract_trades_only

async with SleeperClient() as client:
    snapshots = await collect_user_league_snapshots(client, user_id, seasons=["2023", "2024"])
    trades = extract_trades_only(snapshots, user_id)
```

### Trade Evaluation

Scores every trade a user has made against marketplace values. Best trades, worst trades, net value, win rate.

```python
from sleeper.analytics.user_trades import evaluate_user_trades, build_user_trade_report

evaluated = evaluate_user_trades(trades, marketplace, ktc_values)
report = build_user_trade_report(evaluated)

print(f"Win rate: {report.win_rate:.0%}")
print(f"Net value: {report.net_value:+.0f}")
for t in report.best_trades[:3]:
    print(f"  +{t.value_gained:.0f}: {t.description}")
```

### Single-League Analytics

| Module | Functions |
|--------|-----------|
| `analytics.standings` | `get_standings`, `get_power_rankings`, `get_median_record`, `get_points_per_week` |
| `analytics.dynasty` | `get_initial_draft_map`, `get_trade_volume_by_player`, `get_future_pick_ownership` |
| `analytics.matchups` | `get_head_to_head`, `get_closest_games`, `get_highest_scoring_weeks` |
| `analytics.trades` | `get_transaction_summary`, `get_most_traded_players`, `get_trade_partners`, `get_waiver_activity` |
| `analytics.rosters` | `get_roster_composition`, `get_player_to_team_map` |
| `analytics.valuation` | `compute_pe_ratios` — Price-to-Earnings ratio (KTC value vs real FFPG) |

### Valuation — P/E Ratio

Borrowing from finance: **Price** = KTC value, **Earnings** = fantasy points per game. Normalized against the positional median so QB/RB/WR/TE are comparable.

```
price_multiple    = ktc_value / positional_median_ktc
earnings_multiple = ffpg      / positional_median_ffpg
pe_ratio          = price_multiple / earnings_multiple
```

| PE | Meaning |
|------|---------|
| `< 0.7` | **Undervalued** — production exceeds price (buy) |
| `~ 1.0` | Fair |
| `> 1.5` | **Overvalued** — paying for hype (sell) |
| `None`  | Speculative — no production sample yet |

```python
from sleeper.analytics.valuation import compute_pe_ratios
from sleeper.enrichment.ktc import fetch_ktc_players, build_ktc_to_sleeper_map
from sleeper.enrichment.stats import get_season_stats

ktc = fetch_ktc_players()
# attach sleeper_ids so the join to stats works
mapping = build_ktc_to_sleeper_map(ktc, sleeper_players)
for p in ktc:
    p.sleeper_id = mapping.get(p.ktc_id)

stats = get_season_stats([2025])
pes = compute_pe_ratios(ktc, stats, seasons=[2025], fmt="sf")
buys = [r for r in pes if r.signal == "undervalued"][:15]
```

## CLI

Installed as the `sleeper` entry point (or run via `python -m sleeper.cli`).

```bash
python -m sleeper.cli <command> [options]
```

| Command | Purpose |
|---------|---------|
| `market-value "Player Name"` | KTC listed value vs median actual trade price |
| `league-values <user>` | KTC values for every player on your roster |
| `roster-rank <user>` | Rank all teams in a league by total KTC value |
| `trade-check --give ... --get ...` | Evaluate a proposed trade |
| `trending` | Biggest 7-day KTC movers (up / down / both) |
| `buy-sell buy\|sell` | Players trading below / above their KTC value |
| `ktc-trend <player>` | Historical KTC value for a player (from daily snapshots) |
| `picks <user>` | Future pick assets across the league with KTC values |
| `pe-ratio` | Price-to-Earnings ratio — KTC price vs real FFPG |
| `suggest-trades <user>` | 1-for-1 trades that improve your roster's positional balance |
| `find-trades <user>` | Flexible trade finder with position, include/exclude, and mode filters |
| `send-trade <user>` | Fire a `propose_trade` mutation against Sleeper (auth required) |

### Suggest → Send workflow

`suggest-trades` finds 1-for-1 swaps where you have positional surplus and a partner has the position you need (and vice versa), bounded by KTC value parity. Each suggestion is numbered and cached locally so you can fire it with `send-trade --suggestion N`.

```bash
# 1. Find good trades
python -m sleeper.cli suggest-trades camfleety --league "Meat Market" --top 10

# 2. (Optional) Increase tolerance to broaden matches
python -m sleeper.cli suggest-trades camfleety --league "Meat Market" --tolerance 15 --position WR

# 3. Preview + send suggestion #2 (asks y/N before firing)
SLEEPER_TOKEN='eyJ...' python -m sleeper.cli send-trade camfleety --league "Meat Market" --suggestion 2

# Or send explicitly without going through suggest-trades:
python -m sleeper.cli send-trade camfleety --league "Meat Market" \
    --to-roster 8 --send "Will Levis" --get "Jerome Ford"
```

`send-trade` always prints a preview table before sending. Without `--yes` it requires interactive confirmation (`y` to send). With `--yes` it still prints the preview but skips the prompt — use that for scripted/agent workflows.

**`SLEEPER_TOKEN`** must be set for `send-trade`. Capture it once from sleeper.com DevTools → Network → any `graphql` request → `authorization` header. The SDK reads it from the env var only — it is never logged or stored.

### `pe-ratio` — flag reference

```bash
python -m sleeper.cli pe-ratio \
    --format sf \
    --seasons 2025 \
    --position WR \
    --max-age 27 \
    --min-ppg 8 \
    --min-ktc 2500 \
    --exclude-speculative \
    --top 20 \
    --sort pe
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--format` | `sf` | `sf` or `1qb` |
| `--seasons` | current year | Comma-separated years (e.g. `2024,2025`) |
| `--scoring` | `ppr` | `ppr` or `standard` |
| `--position` | all | `QB`, `RB`, `WR`, `TE` |
| `--min-games` | `4` | Below this a player is marked speculative |
| `--max-age` | none | Cap age for dynasty targets (e.g. `26`) |
| `--min-age` | none | Floor age |
| `--min-ppg` | none | Filter out low-production noise |
| `--min-ktc` | none | Filter out deep bench |
| `--exclude-speculative` | false | Hide rookies / IR with no sample |
| `--top` | `25` | Row limit |
| `--sort` | `pe` | `pe`, `pe-desc`, `value`, `ffpg` |

Requires the `nfl-data` extra (`pip install 'sleeper-sdk[nfl-data]'`) for live NFL stats via `nflreadpy`.

### `find-trades` — Flexible Trade Finder

Search for trades targeting any position(s), with powerful include/exclude/mode filters. Find 1-for-1 or multi-asset combinations that match your criteria.

```bash
# Find all elite RB trades where you overpay 500-1500 KTC
python -m sleeper.cli find-trades camfleety --league "Meat Market" \
    --position RB --min-ktc 5000 --max-overpay 1500

# Find trades targeting specific players, excluding your own
python -m sleeper.cli find-trades camfleety --league "Meat Market" \
    --position RB \
    --include "Breece Hall" "De'Von Achane" "Jonathan Taylor" \
    --exclude "TreVeyon Henderson" "Quinshon Judkins"

# Upgrade mode: find trades where you get more value back
python -m sleeper.cli find-trades camfleety --league "Meat Market" \
    --mode upgrade --position QB --top 20

# Downtiering mode: liquidate high assets for lower-tier players
python -m sleeper.cli find-trades camfleety --league "Meat Market" \
    --mode downtiering --top 25
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--position` | all | Target position(s): `QB RB WR TE` (space-separated) |
| `--include` | none | Only consider these players as targets |
| `--exclude` | none | Exclude these players from results |
| `--mode` | `normal` | `normal` (balanced overpay), `upgrade` (get more back), `downtiering` (liquidate) |
| `--min-overpay` | mode-dependent | Minimum KTC overpay (default: 300 normal, -5000 upgrade) |
| `--max-overpay` | mode-dependent | Maximum KTC overpay (default: 3500 normal, 0 upgrade, 5000 downtiering) |
| `--min-ktc` | 0 | Filter targets by minimum KTC value |
| `--top` | 15 | Max trades to show |
| `--single-only` | false | Only single-player trades (don't combine assets) |

The command combines your highest-value chips (QBs, WRs, TEs) to match target players. Trades are scored by target value and overpay balance.

## Examples

```bash
# Full trade report for a user
python python/examples/trade_report.py your_username --seasons 2024

# Individual module demos
python python/examples/module_examples.py
```

## Features

- **Async-first** — built on `httpx.AsyncClient` with a `sync()` helper
- **Fully typed** — Pydantic models for every API response
- **Rate limiting** — token-bucket algorithm, stays under Sleeper's 1000 req/min limit
- **Retries** — automatic retry with exponential backoff on 5xx errors
- **Player caching** — memory + filesystem cache with 24h TTL
- **KTC caching** — 24h TTL cache so you don't spam keeptradecut.com
- **SF/1QB auto-detect** — checks league roster positions for SUPER_FLEX slot
- **Fuzzy name matching** — normalized matching (strips Jr./III, handles team changes)
- **Zero config** — no API keys needed, just install and go

## Project Structure

```
sleeper-sdk/
├── python/
│   ├── examples/
│   │   ├── trade_report.py        # Full CLI trade analysis pipeline
│   │   └── module_examples.py     # Individual module demos
│   ├── src/sleeper/
│   │   ├── api/                   # Core Sleeper API wrappers
│   │   │   ├── users.py
│   │   │   ├── leagues.py
│   │   │   ├── drafts.py
│   │   │   ├── players.py
│   │   │   └── state.py
│   │   ├── enrichment/            # External data integrations
│   │   │   ├── ktc.py             # KTC scraper + 24hr cache + fuzzy matching
│   │   │   ├── marketplace.py     # Actual trade value engine
│   │   │   ├── rankings.py
│   │   │   ├── stats.py
│   │   │   └── values.py
│   │   ├── analytics/             # Analysis & reporting
│   │   │   ├── user_collector.py  # Cross-league data aggregation
│   │   │   ├── user_trades.py     # Trade evaluation & grading
│   │   │   ├── standings.py
│   │   │   ├── dynasty.py
│   │   │   ├── matchups.py
│   │   │   ├── trades.py
│   │   │   └── rosters.py
│   │   ├── types/                 # Pydantic models
│   │   ├── cache/                 # Player + KTC caching
│   │   ├── http/                  # HTTP client with rate limiting
│   │   └── client.py              # Main SleeperClient entry point
│   └── pyproject.toml
├── sleeper_wrapper.py             # Reference: original wrapper
└── sleeper-api.ts                 # Reference: TypeScript types
```
