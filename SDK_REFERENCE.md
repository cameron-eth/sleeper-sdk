# Sleeper SDK — Complete Reference

A Python SDK for the Sleeper Fantasy Football API with KTC dynasty value integration, trade analytics, and a full CLI.

**Install:** `pip3 install /path/to/sleeper-sdk/python/`
**CLI entry point:** `sleeper <command>` or `python3 -m sleeper.cli <command>`
**Requires:** Python ≥ 3.9, httpx, pydantic

---

## Table of Contents

1. [SleeperClient](#1-sleeperclient)
2. [API Modules](#2-api-modules)
3. [Type Models](#3-type-models)
4. [KTC Integration](#4-ktc-integration)
5. [Enrichment (NFL Data)](#5-enrichment-nfl-data)
6. [Analytics](#6-analytics)
7. [Cache](#7-cache)
8. [CLI Commands](#8-cli-commands)
9. [Claude Skills](#9-claude-skills)
10. [Errors & Exceptions](#10-errors--exceptions)
11. [Key Notes & Gotchas](#11-key-notes--gotchas)
12. [User Context](#12-user-context)

---

## 1. SleeperClient

Main async client. Use as a context manager.

```python
from sleeper.client import SleeperClient

# Async (recommended)
async with SleeperClient() as client:
    user = await client.users.get_user("camfleety")

# Single async block (Python 3.9 compatible)
import asyncio

async def run():
    async with SleeperClient() as client:
        user = await client.users.get_user("camfleety")
        leagues = await client.leagues.get_leagues_for_user(user.user_id, "nfl", "2025")
        return user, leagues

user, leagues = asyncio.run(run())
```

```python
SleeperClient(
    cache_dir: str | Path | None = None,   # defaults to system tmp
    cache_ttl: float = 86400,              # player cache TTL in seconds (24h)
    cache_filesystem: bool = True,
    timeout: float = 30.0,
)
```

| Property | Type | Description |
|---|---|---|
| `users` | `UsersApi` | User lookups |
| `leagues` | `LeaguesApi` | League + roster data |
| `drafts` | `DraftsApi` | Draft picks and history |
| `players` | `PlayersApi` | Player search and trends |
| `state` | `StateApi` | NFL season state |

```python
await client.get_all_players(sport="nfl", force_refresh=False) -> dict[str, Player]
# Cached 24hr. Returns {sleeper_player_id: Player}
```

> ⚠️ **Python 3.9:** Multiple `asyncio.run()` calls fail (event loop closed). Batch all async calls into a single `asyncio.run()` with one async function.

---

## 2. API Modules

### UsersApi — `client.users`

```python
await client.users.get_user(username_or_id: str) -> User
await client.users.get_user_leagues(user_id: str, sport="nfl", season="2025") -> list[League]
await client.users.get_user_drafts(user_id: str, sport="nfl", season="2025") -> list[Draft]
```

### LeaguesApi — `client.leagues`

```python
await client.leagues.get_leagues_for_user(user_id, sport="nfl", season="2025") -> list[League]
await client.leagues.get_league(league_id: str) -> League
await client.leagues.get_rosters(league_id: str) -> list[Roster]
await client.leagues.get_users(league_id: str) -> list[LeagueUser]
await client.leagues.get_matchups(league_id: str, week: int) -> list[Matchup]
await client.leagues.get_winners_bracket(league_id: str) -> list[BracketMatchup]
await client.leagues.get_losers_bracket(league_id: str) -> list[BracketMatchup]
await client.leagues.get_transactions(league_id: str, week: int) -> list[Transaction]
await client.leagues.get_traded_picks(league_id: str) -> list[TradedPick]
```

### DraftsApi — `client.drafts`

```python
await client.drafts.get_drafts_for_user(user_id, sport="nfl", season="2025") -> list[Draft]
await client.drafts.get_drafts_for_league(league_id: str) -> list[Draft]
await client.drafts.get_draft(draft_id: str) -> Draft
await client.drafts.get_picks(draft_id: str) -> list[DraftPick]
await client.drafts.get_traded_picks(draft_id: str) -> list[TradedPick]
```

### PlayersApi — `client.players`

```python
await client.players.get_all_players(sport="nfl") -> dict[str, Player]
await client.players.get_trending(
    type="add",           # "add" or "drop"
    sport="nfl",
    lookback_hours=24,
    limit=25,
) -> list[TrendingPlayer]
```

### StateApi — `client.state`

```python
await client.state.get_state(sport="nfl") -> SportState
```

---

## 3. Type Models

All are Pydantic `BaseModel` (use `.field_name`, not `["field_name"]`).

### User

```python
class User(BaseModel):
    user_id: str
    username: str | None
    display_name: str | None
    avatar: str | None

    # Properties
    avatar_url: str | None       # full-size avatar URL
    avatar_thumb_url: str | None # thumbnail avatar URL
```

### Player

```python
class Player(BaseModel):
    player_id: str
    first_name: str | None
    last_name: str | None
    full_name: str | None          # ← use this for name matching (has spaces)
    position: str | None           # "QB", "RB", "WR", "TE", "K", "DEF"
    fantasy_positions: list[str] | None
    team: str | None               # "KC", "SF", "GB" etc.
    number: int | None
    status: str | None             # "Active", "Inactive", "IR"
    age: int | None
    height: str | None
    weight: str | None
    college: str | None
    years_exp: int | None
    depth_chart_order: int | None
    injury_status: str | None      # "Questionable", "Out", "IR", etc.
    injury_start_date: str | None
    practice_participation: str | None
    search_full_name: str | None   # ← no spaces, e.g. "calebwilliams" — NOT for matching
    search_rank: int | None
    birth_date: str | None
    # External IDs
    espn_id: Any
    yahoo_id: Any
    sportradar_id: str | None
    rotowire_id: Any
    fantasy_data_id: Any

class TrendingPlayer(BaseModel):
    player_id: str
    count: int                     # add/drop count over lookback window
```

### Roster

```python
class RosterSettings(BaseModel):
    wins: int = 0
    losses: int = 0
    ties: int = 0
    fpts: float = 0
    fpts_decimal: float = 0
    fpts_against: float = 0
    fpts_against_decimal: float = 0
    waiver_position: int = 0
    waiver_budget_used: int = 0
    total_moves: int = 0

class Roster(BaseModel):
    roster_id: int
    owner_id: str | None
    league_id: str | None
    starters: list[str] = []      # player_ids currently starting
    players: list[str] = []       # all player_ids on roster
    reserve: list[str] | None     # IR/taxi squad
    settings: RosterSettings | None

    # Property
    bench: list[str]               # players not in starters
```

### LeagueUser

```python
class LeagueUser(BaseModel):
    user_id: str
    username: str | None
    display_name: str | None
    avatar: str | None
    metadata: dict[str, Any] | None
    is_owner: bool | None

    # Property
    team_name: str | None          # from metadata["team_name"]
```

### League

```python
class League(BaseModel):
    league_id: str
    name: str | None
    status: str | None             # "pre_draft", "drafting", "in_season", "complete"
    sport: str | None
    season: str | None
    season_type: str | None        # "regular", "pre", "post"
    total_rosters: int | None
    draft_id: str | None
    previous_league_id: str | None
    avatar: str | None
    settings: dict[str, Any] | None
    scoring_settings: dict[str, float] | None
    roster_positions: list[str] | None
```

### Matchup

```python
class Matchup(BaseModel):
    roster_id: int
    matchup_id: int | None         # teams with same matchup_id face each other
    starters: list[str] = []
    players: list[str] = []
    starters_points: list[float] | None
    points: float | None
    custom_points: float | None
```

### Transaction

```python
class TradedPick(BaseModel):
    season: str                    # "2026", "2027"
    round: int                     # 1–4
    roster_id: int                 # ← original pick slot (NOT current holder)
    previous_owner_id: int         # roster_id of previous holder
    owner_id: int                  # ← current holder's roster_id (NOT user_id!)

class WaiverBudget(BaseModel):
    sender: int                    # roster_id
    receiver: int
    amount: int

class Transaction(BaseModel):
    transaction_id: str
    type: str                      # "trade", "free_agent", "waiver", "commissioner"
    status: str                    # "complete", "failed", "vetoed"
    status_updated: int | None     # epoch ms
    created: int | None
    creator: str | None
    roster_ids: list[int] | None
    consenter_ids: list[int] | None
    adds: dict[str, int] | None    # {player_id: roster_id}
    drops: dict[str, int] | None   # {player_id: roster_id}
    draft_picks: list[TradedPick] = []
    waiver_budget: list[WaiverBudget] = []
    settings: dict[str, Any] | None
    metadata: dict[str, Any] | None
    leg: int | None                # week number
```

### Draft

```python
class DraftSettings(BaseModel):
    teams: int | None
    rounds: int | None
    pick_timer: int | None
    slots_qb: int | None
    slots_rb: int | None
    slots_wr: int | None
    slots_te: int | None
    slots_flex: int | None
    slots_def: int | None
    slots_k: int | None
    slots_bn: int | None

class DraftPickMetadata(BaseModel):
    player_id: str | None
    first_name: str | None
    last_name: str | None
    team: str | None
    position: str | None
    number: str | None
    status: str | None
    injury_status: str | None

class DraftPick(BaseModel):
    draft_id: str
    player_id: str | None
    picked_by: str | None          # user_id
    roster_id: str | None
    round: int
    draft_slot: int | None
    pick_no: int
    is_keeper: bool | None
    metadata: DraftPickMetadata | None

class Draft(BaseModel):
    draft_id: str
    league_id: str | None
    type: str | None               # "snake", "auction", "linear"
    status: str | None             # "pre_draft", "drafting", "complete"
    sport: str | None
    season: str | None
    start_time: int | None         # epoch ms
    settings: DraftSettings | None
    metadata: dict[str, Any] | None
    draft_order: dict[str, int] | None       # {user_id: slot}
    slot_to_roster_id: dict[str, int] | None # {slot: roster_id}
```

### BracketMatchup

```python
class BracketFrom(BaseModel):
    w: int | None   # winner of match_id
    l: int | None   # loser of match_id

class BracketMatchup(BaseModel):
    r: int           # round number
    m: int           # match id
    t1: int | None   # roster_id of team 1
    t2: int | None   # roster_id of team 2
    t1_from: BracketFrom | None
    t2_from: BracketFrom | None
    w: int | None    # winner roster_id
    l: int | None    # loser roster_id
    p: int | None    # placement (3rd place, etc.)
```

### SportState

```python
class SportState(BaseModel):
    week: int
    season: str
    season_type: str | None        # "regular", "pre", "post"
    season_start_date: str | None
    previous_season: str | None
    leg: int | None
    league_season: str | None
    league_create_season: str | None
    display_week: int | None
```

---

## 4. KTC Integration

Scrapes KTC dynasty values and trade data. No API key required. All functions cached 1 hour.

```python
from sleeper.enrichment.ktc import (
    fetch_ktc_players,
    fetch_ktc_trades,
    build_ktc_to_sleeper_map,
    get_player_market_value,
    KTCPlayer, KTCPlayerValue, KTCTrade, KTCTradeSide,
    KTCTradeSettings, TradeDetail, MarketValueReport,
)
```

### KTC Dataclasses

```python
@dataclass
class KTCPlayerValue:
    value: int = 0              # KTC dynasty value (0–9999, capped at 9999)
    rank: int = 0               # overall rank
    positional_rank: int = 0    # rank within position
    overall_trend: int = 0      # 7-day value change (e.g., +40 or -25)
    positional_7day_trend: int = 0

@dataclass
class KTCPlayer:
    ktc_id: str                 # KTC's internal numeric ID (as string)
    player_name: str = ""
    slug: str = ""              # URL slug, e.g. "jamarr-chase"
    position: str = ""          # "QB", "RB", "WR", "TE", "PICK"
    team: str = ""
    age: float | None = None
    one_qb: KTCPlayerValue      # 1QB format values
    superflex: KTCPlayerValue   # SF format values
    mfl_id: str | None = None
    sleeper_id: str | None = None

@dataclass
class KTCTradeSide:
    place: int = 0
    player_ids: list[str]       # list of ktc_id strings (or pick strings)
    player_names: list[str]

@dataclass
class KTCTradeSettings:
    league_id: str | None
    teams: int | None
    qbs: int | None             # ≥2 = superflex/SF league
    ppr: int | None
    tep: int | None             # tight-end premium

@dataclass
class KTCTrade:
    trade_id: str
    date: str                   # ISO datetime string
    side_one: KTCTradeSide
    side_two: KTCTradeSide
    settings: KTCTradeSettings

@dataclass
class TradeDetail:
    trade_id: str
    date: str
    player_side: list[str]           # display names on player's side
    other_side: list[str]            # display names on other side
    player_side_total_ktc: int
    other_side_total_ktc: int
    surplus: int                     # other_side_total - player_side_total
    implied_price: int               # calculated market value
    is_solo: bool                    # True if player was traded alone (no packaging)

@dataclass
class MarketValueReport:
    player_name: str
    position: str
    team: str
    ktc_id: str
    ktc_value: int                   # KTC listed value
    format: str                      # "sf" or "1qb"
    implied_market_values: list[int] # implied price from each trade
    median_market_value: int | None
    mean_market_value: int | None
    num_trades: int
    pct_of_ktc: float | None         # median / ktc_value * 100
    trades: list[TradeDetail]
```

### KTC Public Functions

```python
fetch_ktc_players(force_refresh: bool = False) -> list[KTCPlayer]
```
Scrapes `keeptradecut.com/dynasty-rankings`. Parses `playersArray` JS variable. Cached 1hr.

```python
fetch_ktc_trades(force_refresh: bool = False) -> list[KTCTrade]
```
Scrapes `keeptradecut.com/dynasty/trade-database`. Parses `trades` JS variable. Cached 1hr.

```python
build_ktc_to_sleeper_map(
    ktc_players: list[KTCPlayer],
    sleeper_players: dict[str, Player],
) -> dict[str, str]   # {ktc_id: sleeper_id}
```
Maps KTC IDs to Sleeper IDs via name + position + team matching. ~92% match rate. Uses `Player.full_name` (not `search_full_name`).

```python
get_player_market_value(
    player_name: str,
    fmt: str = "sf",            # "sf" or "1qb"
    force_refresh: bool = False,
) -> MarketValueReport
```
Full market analysis. Finds player in KTC, pulls all trades they appear in, computes implied price per trade.

**Surplus attribution math:**
```
player_weight  = player_ktc_value / player_side_total
implied_price  = player_ktc_value + (player_weight × surplus)
where surplus  = other_side_total − player_side_total
```

### KTC Constants & Helpers

| Constant | Value |
|---|---|
| `KTC_DYNASTY_RANKINGS_URL` | `https://keeptradecut.com/dynasty-rankings` |
| `KTC_TRADE_DATABASE_URL` | `https://keeptradecut.com/dynasty/trade-database` |
| `_VALUE_FLOOR` | `100` (floor for unknown/unranked assets) |
| `_KTC_TIMEOUT` | `30.0` seconds |

| Helper | Signature | Notes |
|---|---|---|
| `_find_ktc_player` | `(name, ktc_players) -> KTCPlayer \| None` | Fuzzy name match |
| `_normalize_name` | `(name) -> str` | Strips Jr/Sr/III, lowercases |
| `_normalize_team` | `(team) -> str` | Maps aliases (JAC→JAX, GBP→GB, etc.) |
| `_classify_pick_tier` | `(pick_number, total_teams=12) -> str` | "Early"/"Mid"/"Late" |
| `_get_pick_ktc_value` | `(pick_str, ktc_by_name, fmt, total_teams=12) -> int` | "2026 Pick 1.08" → KTC value |
| `_fetch_page` | `(url, params=None) -> str` | Falls back to `curl` on SSL error |
| `_resolve_asset_value` | `(asset_id, ktc_by_id, ktc_by_name, fmt) -> int` | Player ID or pick string |
| `_compute_implied_price` | `(trade, target_ktc_id, ...) -> TradeDetail \| None` | Surplus attribution |

**Team aliases applied:**
`JAC/JAG → JAX`, `WSH → WAS`, `LVR/LAS → LV`, `SFO → SF`, `TBB → TB`, `NOS → NO`, `NEP → NE`, `GBP → GB`, `KCC → KC`

---

## 5. Enrichment (NFL Data)

Requires optional dependency: `pip install sleeper-sdk[nfl-data]`

```python
from sleeper.enrichment import (
    PlayerIdBridge, PlayerIds,
    get_season_stats, enrich_rosters_with_stats, EnrichedPlayer, SeasonStatLine,
    get_player_rankings, PlayerRanking,
    get_trade_values, get_buy_low_sell_high, TradeValue, BuySellSignal,
)
```

### PlayerIdBridge

Cross-platform ID mapping via nflreadpy.

```python
@dataclass
class PlayerIds:
    sleeper_id: str
    gsis_id: str | None
    fantasypros_id: int | None
    espn_id: int | None
    yahoo_id: int | None
    pfr_id: str | None
    sportradar_id: str | None
    name: str | None
    position: str | None
    team: str | None

class PlayerIdBridge:
    def load() -> None                          # call once before use
    def from_sleeper(sleeper_id: str) -> PlayerIds | None
    def from_gsis(gsis_id: str) -> PlayerIds | None
    def from_fantasypros(fp_id: int) -> PlayerIds | None
    def sleeper_to_gsis(sleeper_id: str) -> str | None
    def gsis_to_sleeper(gsis_id: str) -> str | None
    total_mapped: int                           # property
```

### Stats

```python
@dataclass
class SeasonStatLine:
    season: int
    games: int = 0
    fantasy_points: float = 0.0
    fantasy_points_ppr: float = 0.0
    ppg: float = 0.0
    ppg_ppr: float = 0.0
    # Passing
    passing_yards: int = 0
    passing_tds: int = 0
    interceptions: int = 0
    # Rushing
    carries: int = 0
    rushing_yards: int = 0
    rushing_tds: int = 0
    # Receiving
    targets: int = 0
    receptions: int = 0
    receiving_yards: int = 0
    receiving_tds: int = 0
    target_share: float = 0.0

@dataclass
class EnrichedPlayer:
    sleeper_id: str
    name: str | None
    position: str | None
    team: str | None
    roster_id: int = 0
    seasons: dict[int, SeasonStatLine]   # {2024: StatLine, 2023: StatLine}

get_season_stats(
    seasons: list[int],
    bridge: PlayerIdBridge | None = None,
) -> dict[str, dict[int, SeasonStatLine]]
# Returns {sleeper_id: {season: SeasonStatLine}}

enrich_rosters_with_stats(
    rosters: list[Roster],
    seasons: list[int],
    bridge: PlayerIdBridge | None = None,
) -> list[EnrichedPlayer]
```

### Rankings

```python
@dataclass
class PlayerRanking:
    sleeper_id: str
    name: str | None
    position: str | None
    team: str | None
    ecr: float | None            # expert consensus ranking (lower = better)
    ecr_type: str | None         # "ros", "draft", "week"
    positional_rank: int | None
    best: int | None             # best ranking from any expert
    worst: int | None
    std_dev: float | None

get_player_rankings(
    bridge: PlayerIdBridge | None = None,
    ranking_type: str | None = None,
) -> list[PlayerRanking]          # sorted by ECR ascending
```

### Trade Values (ECR-based)

```python
@dataclass
class TradeValue:
    sleeper_id: str
    name: str | None
    position: str | None
    team: str | None
    roster_id: int = 0
    ecr: float | None
    positional_rank: int | None
    ppg_ppr: float = 0.0
    value_tier: str = ""         # "elite", "starter", "flex", "bench", "droppable"

@dataclass
class BuySellSignal:
    sleeper_id: str
    name: str | None
    position: str | None
    team: str | None
    roster_id: int = 0
    signal: str = ""             # "buy_low" or "sell_high"
    ecr: float | None
    positional_rank: int | None
    ppg_ppr: float = 0.0
    rank_vs_performance_gap: float = 0.0

get_trade_values(
    rosters: list[Roster],
    bridge: PlayerIdBridge | None = None,
    ranking_type: str | None = None,
) -> list[TradeValue]

get_buy_low_sell_high(
    rosters: list[Roster],
    seasons: list[int],
    bridge: PlayerIdBridge | None = None,
    min_games: int = 4,
    gap_threshold: float = 0.15,
) -> list[BuySellSignal]
```

---

## 6. Analytics

All analytics functions take pre-fetched data — no async calls inside.

```python
from sleeper.analytics.standings import (
    get_standings, get_power_rankings, get_points_per_week,
    get_record_by_week, get_median_record,
    TeamStanding, PowerRanking, WeekRecord,
)
from sleeper.analytics.matchups import (
    get_head_to_head, get_closest_games, get_highest_scoring_weeks,
    HeadToHeadRecord, ClosestGame, HighScoringWeek,
)
from sleeper.analytics.dynasty import (
    get_initial_draft_map, get_trade_volume_by_player,
    get_trade_volume_by_team, get_future_pick_ownership,
    DraftMapEntry, PlayerTradeVolume, TeamTradeVolume, FuturePickOwnership,
)
from sleeper.analytics.rosters import (
    get_roster_composition, get_player_to_team_map,
    RosterComposition, PlayerTeamMapping,
)
from sleeper.analytics.trades import (
    get_transaction_summary, get_most_traded_players,
    get_trade_partners, get_waiver_activity,
    MostTradedPlayer, TradePartnerFrequency, WaiverActivitySummary,
)
from sleeper.analytics.user_collector import (
    collect_user_league_snapshots, extract_trades_only,
    LeagueSnapshot,
)
```

### Standings

```python
@dataclass
class TeamStanding:
    roster_id: int
    owner_id: str | None
    display_name: str | None
    team_name: str | None
    wins: int
    losses: int
    ties: int
    fpts: float
    fpts_against: float
    streak: int
    median_wins: int
    median_losses: int

@dataclass
class WeekRecord:
    week: int
    roster_id: int
    points: float
    opponent_points: float
    won: bool | None
    matchup_id: int | None

@dataclass
class PowerRanking:
    roster_id: int
    display_name: str | None
    team_name: str | None
    rank: int
    score: float           # composite: 40% win%, 40% pts rank, 20% median
    wins: int
    fpts: float
    median_wins: int

get_standings(rosters, users) -> list[TeamStanding]

get_points_per_week(
    matchups_by_week: dict[int, list[Matchup]]
) -> dict[int, dict[int, float]]    # {week: {roster_id: points}}

get_record_by_week(
    matchups_by_week: dict[int, list[Matchup]]
) -> dict[int, list[WeekRecord]]

get_median_record(
    matchups_by_week: dict[int, list[Matchup]]
) -> dict[int, tuple[int, int]]     # {roster_id: (median_wins, median_losses)}

get_power_rankings(rosters, users, matchups_by_week) -> list[PowerRanking]
```

### Matchups

```python
@dataclass
class HeadToHeadRecord:
    roster_id_1: int
    roster_id_2: int
    wins_1: int
    wins_2: int
    ties: int
    total_points_1: float
    total_points_2: float

@dataclass
class ClosestGame:
    week: int
    matchup_id: int
    roster_id_1: int
    roster_id_2: int
    points_1: float
    points_2: float
    margin: float

@dataclass
class HighScoringWeek:
    week: int
    roster_id: int
    points: float
    display_name: str | None

get_head_to_head(matchups_by_week, roster_id_1, roster_id_2) -> HeadToHeadRecord
get_closest_games(matchups_by_week, limit=10) -> list[ClosestGame]
get_highest_scoring_weeks(matchups_by_week, users, limit=10) -> list[HighScoringWeek]
```

### Dynasty Analytics

```python
@dataclass
class DraftMapEntry:
    player_id: str
    pick_no: int
    round: int
    draft_slot: int | None
    roster_id: str | None
    picked_by: str | None
    first_name: str | None
    last_name: str | None
    position: str | None
    team: str | None

@dataclass
class PlayerTradeVolume:
    player_id: str
    times_traded: int
    first_name: str | None
    last_name: str | None
    position: str | None

@dataclass
class TeamTradeVolume:
    roster_id: int
    display_name: str | None
    team_name: str | None
    total_trades: int
    players_acquired: int
    players_sent: int
    picks_acquired: int
    picks_sent: int
    faab_spent: int
    faab_received: int

@dataclass
class FuturePickOwnership:
    season: str
    round: int
    original_roster_id: int
    current_owner_id: int

get_initial_draft_map(picks: list[DraftPick]) -> list[DraftMapEntry]
get_trade_volume_by_player(transactions) -> list[PlayerTradeVolume]
get_trade_volume_by_team(transactions, users) -> list[TeamTradeVolume]
get_future_pick_ownership(traded_picks: list[TradedPick]) -> list[FuturePickOwnership]
```

### Roster Analytics

```python
@dataclass
class RosterComposition:
    roster_id: int
    owner_id: str | None
    total_players: int
    by_position: dict[str, int]    # {"QB": 2, "RB": 7, ...}
    starters_count: int
    bench_count: int

@dataclass
class PlayerTeamMapping:
    player_id: str
    player_name: str | None
    position: str | None
    nfl_team: str | None
    roster_id: int

get_roster_composition(rosters, players) -> list[RosterComposition]
get_player_to_team_map(rosters, players) -> list[PlayerTeamMapping]
```

### Trade / Transaction Analytics

```python
@dataclass
class MostTradedPlayer:
    player_id: str
    times_traded: int

@dataclass
class TradePartnerFrequency:
    roster_id_1: int
    roster_id_2: int
    trade_count: int

@dataclass
class WaiverActivitySummary:
    total_waiver_moves: int
    total_free_agent_moves: int
    total_faab_spent: int
    most_active_roster_id: int | None
    most_active_moves: int

get_transaction_summary(transactions) -> dict[str, int]   # {"trade": 45, "waiver": 112, ...}
get_most_traded_players(transactions, limit=20) -> list[MostTradedPlayer]
get_trade_partners(transactions) -> list[TradePartnerFrequency]
get_waiver_activity(transactions) -> WaiverActivitySummary
```

### User Collector (Cross-League)

```python
@dataclass
class LeagueSnapshot:
    league_id: str
    league_name: str
    season: str
    user_roster_id: int
    scoring_type: str              # "sf" or "1qb"
    league: League | None
    rosters: list[Roster]
    users: list[LeagueUser]
    transactions: list[Transaction]
    roster_to_owner: dict[int, str]    # {roster_id: user_id}
    owner_to_name: dict[str, str]      # {user_id: display_name}

async collect_user_league_snapshots(
    users_api: UsersApi,
    leagues_api: LeaguesApi,
    user_id: str,
    seasons: list[str] | None = None,   # defaults to "2017"–current year
    max_transaction_week: int = 18,
) -> list[LeagueSnapshot]

extract_trades_only(
    snapshots: list[LeagueSnapshot],
) -> list[tuple[Transaction, LeagueSnapshot]]
```

---

## 7. Cache

### PlayerCache

```python
from sleeper.cache.player_cache import PlayerCache

PlayerCache(
    cache_dir: str | Path | None = None,
    ttl: float = 86400,               # 24 hours
    filesystem_enabled: bool = True,
)

.get(sport: str) -> dict[str, Any] | None
.set(sport: str, players: dict[str, Any]) -> None
.clear(sport: str | None = None) -> None
```
Cache file: `$TMPDIR/sleeper_sdk_cache/players_{sport}.json`

### KTCCache

```python
from sleeper.cache.ktc_cache import KTCCache

KTCCache(
    cache_dir: str | Path | None = None,
    ttl: float = 3600,                # 1 hour
    filesystem_enabled: bool = True,
)

.get(key: str) -> Any | None
.set(key: str, payload: Any) -> None
.clear(key: str | None = None) -> None
```
Keys: `"players"`, `"trades"`
Cache files: `$TMPDIR/sleeper_sdk_cache/ktc/ktc_{key}.json`

---

## 8. CLI Commands

**Run via:** `python3 -m sleeper.cli <command>` or `sleeper <command>` (after install)

### `market-value`
```
sleeper market-value <player name> [--format sf|1qb]
```
KTC listed value vs actual market price from trade data. Uses surplus attribution math.

| Signal | Meaning |
|---|---|
| UNDERVALUED | Market > 105% of KTC — potential buy target |
| FAIRLY VALUED | Market is 95–105% of KTC |
| OVERVALUED | Market < 95% of KTC — potential sell |

### `league-values`
```
sleeper league-values <username> [--league <name>] [--format sf|1qb]
```
KTC values for every player on your roster, sorted by value, with total.

### `roster-rank`
```
sleeper roster-rank <username> [--league <name>] [--format sf|1qb]
```
Rank all 12 teams by total KTC roster value. Shows player count, total value, best player.

### `trade-check`
```
sleeper trade-check --give "<player>" [<player> ...] --get "<player>" [<player> ...] [--format sf|1qb]
```
Evaluate a proposed trade. Player names are fuzzy-matched.

| Verdict | Net value diff |
|---|---|
| WIN | +500 or more |
| SLIGHT WIN | +1 to +499 |
| SLIGHT LOSS | −1 to −499 |
| LOSS | −500 or worse |

```bash
sleeper trade-check \
  --give "Ja'Marr Chase" \
  --get "Justin Jefferson" "2026 Early 1st" \
  --format sf
```

### `trending`
```
sleeper trending [--format sf|1qb] [--top N] [--direction up|down|both] [--position QB|RB|WR|TE]
```
Players with biggest 7-day KTC value movement. Shows Δ value and Δ%.

### `buy-sell`
```
sleeper buy-sell <buy|sell> [--format sf|1qb] [--top N] [--position POS] [--min-trades N]
```
Uses actual trade data (not just KTC rankings).
- `buy` → market value < 90% of KTC listed (underpriced)
- `sell` → market value > 110% of KTC listed (overpriced)
- `--min-trades` default: 2 (filter out single-trade noise)

### `picks`
```
sleeper picks <username> [--league <name>] [--format sf|1qb] [--owner <name>] [--traded-only]
```
All future picks (current year + 2 out, rounds 1–4) with KTC values. Tracks trades.

| Column | Description |
|---|---|
| Pick | Year + tier + round (e.g. "2026 Early 1st") |
| Slot | Draft slot (P01–P12) |
| KTC Value | Current KTC dynasty value for this pick tier |
| Current Holder | Who owns it now |
| Original Owner | Who it originally belonged to (blank if untouched) |
| Traded | Y/N |

---

## 9. Claude Skills

Located in `.claude/commands/`. Invoke with `/skill-name` in Claude Code.

| Skill | Type | Purpose |
|---|---|---|
| `/market-value` | Tool docs | How to run market value analysis |
| `/league-values` | Tool docs | How to view roster values |
| `/roster-rank` | Tool docs | How to rank league teams |
| `/trade-check` | Tool docs | How to evaluate a trade |
| `/trending` | Tool docs | How to find trending players |
| `/buy-sell` | Tool docs | How to find buy/sell candidates |
| `/picks` | Tool docs | How to view pick assets |
| `/trade-guru` | **Agentic** | Trade analyst — builds full trade proposals for camfleety |
| `/team-report` | **Agentic** | Compiles full dynasty team report (roster, picks, signals, summary) |
| `/data-scientist` | **Agentic** | Answers open-ended dynasty data questions using SDK directly |

### Agentic Skill Workflows

**`/trade-guru`** — Runs in sequence:
1. `league-values` → current roster
2. `roster-rank` → league landscape
3. `buy-sell sell` → sell-high candidates on roster
4. `buy-sell buy` → buy-low targets available
5. `trending` → short-term windows
6. `trade-check` → evaluate specific proposals
7. `market-value` → validate key player prices

**`/team-report`** — Full diagnostic:
1. `league-values` → roster with values
2. `roster-rank` → where you stand
3. `picks --owner camfleety` → pick capital
4. `trending` → check roster overlap
5. `market-value` → top 3–5 players
6. `buy-sell sell` → sell windows

**`/data-scientist`** — Use Python directly:
```python
from sleeper.enrichment.ktc import fetch_ktc_players, fetch_ktc_trades

players = fetch_ktc_players()
trades = fetch_ktc_trades()

# Example: all WRs under 24 with SF value > 4000
targets = [
    p for p in players
    if p.position == "WR"
    and (p.age or 99) < 24
    and p.superflex.value > 4000
]
targets.sort(key=lambda p: p.superflex.value, reverse=True)
```

---

## 10. Errors & Exceptions

```python
from sleeper.errors import SleeperApiError, SleeperNotFoundError, SleeperRateLimitError

class SleeperApiError(Exception):
    status_code: int
    message: str

class SleeperNotFoundError(SleeperApiError):
    # Raised on 404

class SleeperRateLimitError(SleeperApiError):
    # Raised on 429
```

**HTTP layer:** Auto-retries up to 3 times with exponential backoff (1s, 2s, 4s).
**Rate limit:** 1000 requests per 60 seconds (token-bucket).

---

## 11. Key Notes & Gotchas

| Issue | Detail |
|---|---|
| **Name matching** | Use `Player.full_name` for matching — `search_full_name` has no spaces (e.g. `"calebwilliams"`) |
| **Python 3.9 event loop** | Multiple `asyncio.run()` calls fail — batch all async into one call |
| **KTC SSL on macOS** | System Python 3.9 has outdated SSL; `_fetch_page()` falls back to `curl` automatically |
| **KTC value cap** | KTC caps at 9,999 — top players (Chase, Mahomes, Allen) often trade above this |
| **TradedPick.owner_id** | This is a **roster_id** (1–12), NOT a user_id — map via `roster_to_owner` |
| **SF vs 1QB** | Trades filtered by `settings.qbs >= 2` for SF. Always pass `--format sf` for The Meat Market |
| **Pick tiers** | Calculated by slot in round: top 1/3 = Early, middle 1/3 = Mid, bottom 1/3 = Late |
| **KTC match rate** | ~458/500 players matched (92%) — rookies/backups may be missing |
| **Cache location** | `$TMPDIR/sleeper_sdk_cache/` — delete to force refresh |
| **Install** | Editable install doesn't work — always use `pip3 install .` |

---

## 12. User Context

| Key | Value |
|---|---|
| Username | `camfleety` (no underscore) |
| User ID | `1015846922424725504` |
| Dynasty league | **The Meat Market** |
| Meat Market league_id | `1328460395249172480` |
| Meat Market format | 12-team Dynasty, Superflex (SF) |
| Meat Market record | 3–25 all-time (rebuild mode) |
| Other leagues | The OGs (keeper/SF), Ball Knowers, Da Skreets, FF Creator League |

### Quick Start for Meat Market

```bash
# Full team report
sleeper league-values camfleety --league "Meat Market" --format sf

# Where does your roster rank?
sleeper roster-rank camfleety --league "Meat Market" --format sf

# Your pick assets
sleeper picks camfleety --league "Meat Market" --format sf --owner camfleety

# Evaluate a trade
sleeper trade-check --give "Emeka Egbuka" --get "Malik Nabers" --format sf

# Who to sell right now?
sleeper buy-sell sell --format sf --min-trades 1 --top 10

# What's moving this week?
sleeper trending --format sf --direction both --top 20
```
