# sleeper-sdk

> A Python toolkit for turning the [Sleeper Fantasy Football API](https://docs.sleeper.com) into a decision-making engine. Typed, async-first, KTC-enriched, and wired into a CLI that can actually propose trades.

---

## The story

Sleeper gives you the raw data — rosters, matchups, transactions, drafts. That's useful but not *actionable*. To know whether you should trade Kyler Murray for a 1st-round pick, you need three more things stacked on top:

1. **A value system** — how much is each player / pick actually worth?
2. **A production signal** — is that value backed by real points on the board?
3. **A decision layer** — given my roster and the league, what should I do next?

This SDK layers those three on top of Sleeper:

```
┌──────────────────────────────────────────────────────────┐
│  Decision Layer — CLI commands & agent skills            │
│  gm-mode · find-trades · suggest-trades · send-trade     │
├──────────────────────────────────────────────────────────┤
│  Analytics — rank, classify, match, score                │
│  archetypes · P/E ratio · positional fit · value deltas  │
├──────────────────────────────────────────────────────────┤
│  Enrichment — external value sources                     │
│  KTC dynasty values · marketplace (actual trade prices)  │
│  · NFL stats (FFPG)                                      │
├──────────────────────────────────────────────────────────┤
│  Sleeper API — typed, async, rate-limited                │
│  users · leagues · rosters · drafts · players · state    │
└──────────────────────────────────────────────────────────┘
```

Every layer is usable on its own. The CLI is the top-level fastest path; the Python API is the flexibility path.

---

## Install

```bash
cd python
pip install -e .

# Optional extras
pip install 'sleeper-sdk[nfl-data]'   # for P/E ratio (real FFPG via nflreadpy)
```

## 60-second tour

```bash
# Who am I and what leagues am I in?
python -m sleeper.cli league-values camfleety

# Classify my team: contender, reloading, rebuilding, or pretender?
python -m sleeper.cli gm-mode camfleety --league "Meat Market"

# Find me realistic RB upgrades
python -m sleeper.cli find-trades camfleety --league "Meat Market" --position RB --mode normal

# Fire one of them (requires SLEEPER_TOKEN)
SLEEPER_TOKEN='eyJ...' python -m sleeper.cli send-trade camfleety --league "Meat Market" --suggestion 1
```

That's the loop. The rest of this README is the map of how the layers underneath it work.

---

## Layer 1 — Sleeper API

The base layer. Everything else pulls from here.

```python
import asyncio
from sleeper import SleeperClient

async def main():
    async with SleeperClient() as client:
        user = await client.users.get_user("camfleety")
        leagues = await client.users.get_user_leagues(user.user_id, season="2025")
        for lg in leagues:
            print(f"{lg.name} — {lg.total_rosters} teams")

asyncio.run(main())
```

Sync shortcut for scripts and notebooks:

```python
client = SleeperClient()
league = client.sync(client.leagues.get_league("1328460395249172480"))
```

| Module | Methods |
|--------|---------|
| `client.users` | `get_user`, `get_user_leagues`, `get_user_drafts` |
| `client.leagues` | `get_league`, `get_leagues_for_user`, `get_rosters`, `get_users`, `get_matchups`, `get_winners_bracket`, `get_losers_bracket`, `get_transactions`, `get_traded_picks` |
| `client.drafts` | `get_draft`, `get_drafts_for_user`, `get_drafts_for_league`, `get_picks`, `get_traded_picks` |
| `client.players` | `get_all_players` (cached), `get_trending` |
| `client.state` | `get_state` |

**Built-ins:** token-bucket rate limiting (under Sleeper's 1000 req/min cap), exponential-backoff retries on 5xx, 24h player cache to memory + disk, Pydantic types on every response.

**Authenticated reads + trade writes** live in `sleeper.auth.SleeperAuthClient` — these hit Sleeper's GraphQL endpoint (which requires a bearer token) for things like reading your own trade inbox or calling `propose_trade`. Token comes from the `SLEEPER_TOKEN` env var.

---

## Layer 2 — Enrichment

External value sources, fused onto Sleeper player IDs.

### KTC Dynasty Values

Scrapes [KeepTradeCut](https://keeptradecut.com) and fuzzy-matches to Sleeper IDs. Both Superflex and 1QB formats.

```python
from sleeper.enrichment.ktc import fetch_ktc_players, build_ktc_to_sleeper_map, detect_scoring_type

ktc = fetch_ktc_players()                    # 24h cache to disk
mapping = build_ktc_to_sleeper_map(ktc, sleeper_players)
scoring = detect_scoring_type(league)        # "sf" or "1qb"
```

**Cache:** `$TMPDIR/sleeper_sdk_cache/ktc_values.json`, 24h TTL. A daily GitHub Action (see `.github/workflows/`) snapshots values so `ktc-trend` can plot historical movement.

#### KTC Value Adjustment (important!)

KTC itself publishes a **Value Adjustment** — extra KTC added to the side giving up more "roster spots" or "stud factor" in a lopsided trade. The idea, in their words: *12 third-round picks should not be a fair deal for DeAndre Hopkins.* The adjustment is reverse-engineered from the filler players needed to even out the trade.

This SDK doesn't replicate KTC's exact adjustment formula (it's proprietary and reverse-engineered from their UI), but it applies the same *principle* in `find-trades` and `suggest-trades`:

- **Asset concentration weighting** — a trade that sends 1 stud for 3 mid-tier players incurs a penalty proportional to the roster-spot differential.
- **Stud-factor tilting** — a top-10-positional player counts more than raw KTC would suggest.

When you see `--max-overpay 1500` in the CLI, that's your manual value-adjustment budget: how much extra KTC you're willing to pay for the privilege of consolidating onto the stud. Default `normal` mode caps overpay at 3500, which roughly mirrors KTC's own adjustment ceiling.

### Marketplace Values

KTC tells you *theory*. The marketplace module tells you what players *actually* trade for, built from real transactions.

```python
from sleeper.enrichment.marketplace import build_marketplace_values, compare_ktc_vs_actual

marketplace = build_marketplace_values(trade_observations, ktc_values)
comparisons = compare_ktc_vs_actual(marketplace)
# signal = "BUY" (cheaper than KTC), "SELL" (pricier), or "FAIR"
```

For N-for-N trades each player's cost is **isolated** via subtraction — `cost = other_side_total - sum(companions)` — rather than proportional attribution, so the engine doesn't assume KTC ratios are correct. This is the mechanism that lets the `market-value` CLI flag arbitrage.

### NFL Stats — the production signal

Real FFPG via [`nflreadpy`](https://github.com/nflverse/nflreadpy). Joined to KTC via Sleeper IDs. This is what powers P/E ratio and GM Mode's production rank.

```python
from sleeper.enrichment.stats import get_season_stats
stats = get_season_stats([2025])
```

---

## Layer 3 — Analytics

Ranking, classifying, and scoring.

### P/E Ratio — finding undervalued players

Borrowing from equities: **Price** = KTC value, **Earnings** = fantasy points per game. Normalized against the positional median so QB/RB/WR/TE are comparable.

```
price_multiple    = ktc_value / positional_median_ktc
earnings_multiple = ffpg      / positional_median_ffpg
pe_ratio          = price_multiple / earnings_multiple
```

| P/E | Meaning |
|------|---------|
| `< 0.7` | **Undervalued** — production exceeds price (buy) |
| `~ 1.0` | Fair |
| `> 1.5` | **Overvalued** — paying for hype (sell) |
| `None`  | Speculative — no production sample yet |

### GM Mode — team archetype classification

One function that answers *what kind of team am I?* and *what should I do about it?*

```python
from sleeper.analytics.gm_mode import generate_gm_report
report = generate_gm_report(my_roster, all_rosters, sleeper_players, sleeper_to_ktc, ...)
print(report.archetype.name)  # CONTENDER / RELOADING / REBUILDING / PRETENDER
```

Archetype is computed from:
- **Value rank** (total KTC including picks) vs the league
- **Production rank** (season FPTS) vs the league
- **Roster age** (avg starter age)
- **Youth-weighted value** (% of KTC tied up in players under 26)

The PRETENDER detector is the most distinctive check: mid-value teams (rank 3-6) that are *underperforming their value* (production rank 6-9) AND skewing old → usually the classic dynasty dead-zone trap.

### Single-league analytics

| Module | What it does |
|--------|-------------|
| `analytics.standings` | Standings, power rankings, median record, weekly points |
| `analytics.dynasty` | Initial draft map, trade volume, future pick ownership |
| `analytics.matchups` | H2H records, closest games, highest-scoring weeks |
| `analytics.trades` | Transaction summaries, most-traded players, waiver activity |
| `analytics.rosters` | Composition, player-to-team map |
| `analytics.valuation` | P/E ratio |
| `analytics.gm_mode` | Archetype classification |
| `analytics.trade_suggestions` | Positional surplus ↔ need matching for 1-for-1 swaps |

### Cross-league user trade evaluation

For users in multiple leagues, `analytics.user_collector` + `analytics.user_trades` aggregate every trade you've made, score it against marketplace values, and surface your best / worst deals + net value + win rate.

```python
from sleeper.analytics.user_collector import collect_user_league_snapshots, extract_trades_only
from sleeper.analytics.user_trades import evaluate_user_trades, build_user_trade_report

snapshots = await collect_user_league_snapshots(client, user_id, seasons=["2024", "2025"])
trades = extract_trades_only(snapshots, user_id)
report = build_user_trade_report(evaluate_user_trades(trades, marketplace, ktc))
print(f"Win rate: {report.win_rate:.0%}  Net: {report.net_value:+.0f}")
```

---

## Layer 4 — CLI & the decision loop

The CLI is where the whole stack comes together. Installed as the `sleeper` entry point (or `python -m sleeper.cli`).

### The full loop

```
gm-mode          →  What kind of team am I? What should I focus on?
   │
   ▼
find-trades      →  Given my weakness + strategy, what trades make sense?
   │  OR
   ▼
suggest-trades   →  Auto-match my surplus to the league's needs
   │
   ▼
send-trade       →  Preview + fire the proposal via Sleeper GraphQL
```

### Command reference

| Command | Purpose |
|---------|---------|
| `gm-mode <user>` | Archetype report: CONTENDER / RELOADING / REBUILDING / PRETENDER + strategy |
| `find-trades <user>` | Flexible trade search with position / include / exclude / mode filters |
| `suggest-trades <user>` | Auto-suggest 1-for-1 swaps matching positional surplus ↔ need |
| `send-trade <user>` | Propose a trade via Sleeper GraphQL (preview + confirm) |
| `league-values <user>` | KTC values for every player on a roster |
| `roster-rank <user>` | Rank all teams in a league by total KTC value |
| `picks <user>` | Future pick assets with KTC values |
| `market-value "Name"` | KTC listed value vs median actual trade price |
| `trade-check` | Evaluate a hypothetical trade (`--give ... --get ...`) |
| `trending` | Biggest 7-day KTC movers |
| `buy-sell buy\|sell` | Players trading below / above their KTC value |
| `ktc-trend <player>` | Historical KTC from daily snapshots |
| `pe-ratio` | Price-to-Earnings scan — find undervalued players |

### `gm-mode` — archetype + strategy

```bash
python -m sleeper.cli gm-mode camfleety --league "Meat Market" --format sf

# Scout another owner
python -m sleeper.cli gm-mode camfleety --league "Meat Market" --owner someone_else
```

Output: archetype + confidence, value rank vs production rank, positional breakdown (STRONG/AVG/WEAK × DEEP/AVG/SHALLOW), top 5 assets, aging liabilities, archetype-matched buy/sell targets.

### `find-trades` — flexible trade finder

```bash
# Realistic RB upgrades (mode=normal is balanced overpay)
python -m sleeper.cli find-trades camfleety --league "Meat Market" \
    --position RB --mode normal --min-ktc 4000

# Target specific players, exclude your own
python -m sleeper.cli find-trades camfleety --league "Meat Market" \
    --position WR --include "Puka Nacua" "Garrett Wilson" \
    --exclude "Emeka Egbuka"

# Liquidate a surplus QB for a tier down + picks
python -m sleeper.cli find-trades camfleety --league "Meat Market" \
    --mode downtiering --position QB
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--position` | all | `QB RB WR TE` (space-separated) |
| `--include` / `--exclude` | none | Target whitelist / blacklist |
| `--mode` | `normal` | `normal` (overpay +300 to +3500) / `upgrade` (-5000 to 0) / `downtiering` (+500 to +5000) |
| `--min-overpay` / `--max-overpay` | mode-dependent | Manual KTC value-adjustment budget |
| `--min-ktc` | 0 | Filter targets by min KTC |
| `--top` | 15 | Row limit |
| `--single-only` | false | Disable multi-asset packaging |

The three modes correspond to three strategic postures:
- **normal** — balanced: you pay a small premium to consolidate. Matches KTC's own value-adjustment ceiling.
- **upgrade** — you want the better player and will absorb the value loss (tank the KTC delta, win on talent).
- **downtiering** — you're liquidating: trade a stud for a tier-down player + picks.

### `suggest-trades` → `send-trade`

`suggest-trades` automates the partner-finding step by matching *your* positional surplus against *another team's* need (and vice versa), bounded by KTC value parity. Suggestions are numbered and cached to `~/.sleeper-sdk/last_suggestions.json` keyed by user + league.

```bash
# 1. Find good trades
python -m sleeper.cli suggest-trades camfleety --league "Meat Market" --top 10

# 2. Broaden tolerance if nothing matches
python -m sleeper.cli suggest-trades camfleety --league "Meat Market" --tolerance 15 --position WR

# 3. Preview + fire suggestion #2
SLEEPER_TOKEN='eyJ...' python -m sleeper.cli send-trade camfleety --league "Meat Market" --suggestion 2

# Or send explicitly
python -m sleeper.cli send-trade camfleety --league "Meat Market" \
    --to-roster 8 --send "Will Levis" --get "Jerome Ford"
```

`send-trade` **always** prints a preview table and requires interactive `y` confirmation unless `--yes` is passed (for scripted agent workflows — preview still prints). `SLEEPER_TOKEN` is read from the env var only; never logged, never stored.

Capture the token once from sleeper.com → DevTools → Network → any `graphql` request → `authorization` header.

### `pe-ratio` — undervalued player scan

```bash
python -m sleeper.cli pe-ratio \
    --format sf --seasons 2025 --position WR \
    --max-age 27 --min-ppg 8 --min-ktc 2500 \
    --exclude-speculative --top 20 --sort pe
```

Requires the `nfl-data` extra.

---

## Claude skills

Skills in `.claude/commands/` let an agent invoke the right CLI recipe for the right situation:

| Skill | Intent it handles |
|-------|---|
| `gm-mode` | "What kind of team am I?" "Am I a contender?" |
| `team-report` | Full roster + picks + trends combined |
| `market-value` | "What's X actually trading for?" |
| `buy-sell` | "Who should I buy low / sell high?" |
| `trending` | "Who's moving this week?" |
| `roster-rank` | "Where do I stack up?" |
| `picks` | "What picks does each team own?" |
| `league-values` | "How much is my roster worth?" |
| `trade-check` | "Is this trade fair?" |
| `trade-guru` | Multi-turn trade negotiation reasoning |

---

## Project structure

```
sleeper-sdk/
├── .claude/commands/           # Claude skill files
├── .github/workflows/          # Daily KTC value snapshots
├── python/
│   ├── examples/
│   └── src/sleeper/
│       ├── api/                # Layer 1: Sleeper REST wrappers
│       ├── auth/               # GraphQL client (trades + private reads)
│       ├── enrichment/         # Layer 2: KTC, marketplace, stats
│       │   ├── ktc.py
│       │   ├── marketplace.py
│       │   ├── rankings.py
│       │   ├── stats.py
│       │   └── values.py
│       ├── analytics/          # Layer 3: rank, classify, score
│       │   ├── gm_mode.py
│       │   ├── trade_suggestions.py
│       │   ├── valuation.py    # P/E ratio
│       │   ├── user_collector.py
│       │   ├── user_trades.py
│       │   ├── standings.py
│       │   ├── dynasty.py
│       │   ├── matchups.py
│       │   ├── trades.py
│       │   └── rosters.py
│       ├── types/              # Pydantic models
│       ├── cache/              # Player + KTC on-disk cache
│       ├── http/               # Rate-limited httpx client
│       ├── cli.py              # Layer 4: entry point for all commands
│       └── client.py           # Main SleeperClient
└── pyproject.toml
```

## Features at a glance

- **Async-first** — `httpx.AsyncClient` with `sync()` helper
- **Fully typed** — Pydantic models everywhere
- **Rate limited** — token bucket under Sleeper's 1000 req/min
- **Player + KTC caching** — disk cache, 24h TTL
- **SF/1QB auto-detect** — from league roster positions
- **Fuzzy matching** — strips Jr./III, handles team changes
- **Historical KTC** — daily snapshots via GitHub Action
- **Agent-ready** — every CLI command has a corresponding Claude skill
- **Zero config for reads** — no API key needed; token only for `send-trade`
