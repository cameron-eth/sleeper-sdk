# sleeper-sdk

A Python SDK for the [Sleeper Fantasy Football API](https://docs.sleeper.com). Typed, async-first, with built-in rate limiting and player caching.

## Install

```bash
pip install -e .
```

## Quick Start

```python
import asyncio
from sleeper import SleeperClient

async def main():
    async with SleeperClient() as client:
        # Get a league
        league = await client.leagues.get_league("1328460395249172480")
        print(league.name)  # "The Meat Market"

        # Get rosters with W/L records
        rosters = await client.leagues.get_rosters(league.league_id)
        for r in rosters:
            print(f"Roster {r.roster_id}: {r.settings.wins}W-{r.settings.losses}L")

        # Walk dynasty league history
        prev = await client.leagues.get_league(league.previous_league_id)
        print(f"Previous season: {prev.season} ({prev.status})")

        # Get draft picks
        drafts = await client.drafts.get_drafts_for_league(league.league_id)
        picks = await client.drafts.get_picks(drafts[0].draft_id)

        # Get traded picks
        traded = await client.leagues.get_traded_picks(league.league_id)
        print(f"{len(traded)} traded picks")

        # Get trending players
        trending = await client.players.get_trending(type="add")
        for t in trending[:5]:
            print(f"Player {t.player_id}: {t.count} adds")

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
| `client.users` | `get_user` |
| `client.leagues` | `get_league`, `get_leagues_for_user`, `get_rosters`, `get_users`, `get_matchups`, `get_winners_bracket`, `get_losers_bracket`, `get_transactions`, `get_traded_picks` |
| `client.drafts` | `get_draft`, `get_drafts_for_user`, `get_drafts_for_league`, `get_picks`, `get_traded_picks` |
| `client.players` | `get_all_players` (cached), `get_trending` |
| `client.state` | `get_state` |

## Features

- **Async-first** — built on `httpx.AsyncClient` with a `sync()` helper for convenience
- **Fully typed** — Pydantic models for every API response
- **Rate limiting** — token-bucket algorithm, stays under Sleeper's 1000 req/min limit
- **Retries** — automatic retry with exponential backoff on 5xx errors
- **Player caching** — memory + filesystem cache with 24h TTL (the `/players/nfl` endpoint returns ~5MB)
- **Zero config** — no API keys needed, just install and go

## Coming Soon

- **Analytics module** — standings, power rankings, trade volume tracking, dynasty draft pick mapping, roster composition analysis
