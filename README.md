# sleeper-sdk

A Python toolkit for Sleeper fantasy football. Look up leagues and rosters,
compare player values from KeepTradeCut (KTC), find trades, and get lineup advice.
Use it from the command line, Python, or a coding agent.

## Install

Requires Python 3.9+.

```bash
git clone https://github.com/cameron-eth/sleeper-sdk.git
cd sleeper-sdk/python
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install .
```

After pulling updates, run `python3 -m pip install .` again from `python/`.
For NFL stats and production-based analysis, install the optional extra:
`python3 -m pip install '.[nfl-data]'`.

## Try it

Replace `YOUR_USERNAME` and `YOUR_LEAGUE` with your Sleeper details.

```bash
# See your players' dynasty values
sleeper league-values YOUR_USERNAME --league "YOUR_LEAGUE"

# Get a team assessment and strategy
sleeper gm-mode YOUR_USERNAME --league "YOUR_LEAGUE"

# Find running back trades
sleeper find-trades YOUR_USERNAME --league "YOUR_LEAGUE" --position RB
```

Run `sleeper --help` for all commands or `sleeper find-trades --help` for options.

Public data and trade discovery need no API key. Private reads and actions such
as sending trades or changing lineups require `SLEEPER_TOKEN`. Review the
preview before confirming or executing an action; it changes your real team.

## Use in Python

```python
import asyncio
from sleeper import SleeperClient

async def main():
    async with SleeperClient() as client:
        user = await client.users.get_user("YOUR_USERNAME")
        state = await client.state.get_state()
        leagues = await client.users.get_user_leagues(
            user.user_id, season=state.season
        )
        for league in leagues:
            print(league.name)

asyncio.run(main())
```

## Use with an agent

Open this repo in Claude Code or Codex. Skills cover rosters, trades, player
values, and team strategy. Give the agent your Sleeper username and league name.

- [Claude skills](.claude/commands/) — for example, `/trades:find` or `/strategy:gm-mode`.
- [Codex skills](.agents/skills/) — generated from the Claude skills.
- [Contributor and agent guidance](CLAUDE.md) — read before changing code.

## More detail

- [SDK reference](SDK_REFERENCE.md) — API methods, analytics, and CLI options.
- [Examples](python/examples/) — Python usage.
- [Architecture](.claude/STRUCTURE.md) — how the code and skills fit together.
