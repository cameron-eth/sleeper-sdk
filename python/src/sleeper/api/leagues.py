from __future__ import annotations

from sleeper.http.client import HttpClient
from sleeper.types.league import League, LeagueUser, Roster
from sleeper.types.matchup import Matchup
from sleeper.types.bracket import BracketMatchup
from sleeper.types.transaction import Transaction, TradedPick


class LeaguesApi:
    def __init__(self, http: HttpClient):
        self._http = http

    async def get_leagues_for_user(self, user_id: str, sport: str = "nfl", season: str = "2024") -> list[League]:
        data = await self._http.get(f"/user/{user_id}/leagues/{sport}/{season}")
        return [League.model_validate(d) for d in data]

    async def get_league(self, league_id: str) -> League:
        data = await self._http.get(f"/league/{league_id}")
        return League.model_validate(data)

    async def get_rosters(self, league_id: str) -> list[Roster]:
        data = await self._http.get(f"/league/{league_id}/rosters")
        return [Roster.model_validate(d) for d in data]

    async def get_users(self, league_id: str) -> list[LeagueUser]:
        data = await self._http.get(f"/league/{league_id}/users")
        return [LeagueUser.model_validate(d) for d in data]

    async def get_matchups(self, league_id: str, week: int) -> list[Matchup]:
        data = await self._http.get(f"/league/{league_id}/matchups/{week}")
        return [Matchup.model_validate(d) for d in data]

    async def get_winners_bracket(self, league_id: str) -> list[BracketMatchup]:
        data = await self._http.get(f"/league/{league_id}/winners_bracket")
        return [BracketMatchup.model_validate(d) for d in data]

    async def get_losers_bracket(self, league_id: str) -> list[BracketMatchup]:
        data = await self._http.get(f"/league/{league_id}/losers_bracket")
        return [BracketMatchup.model_validate(d) for d in data]

    async def get_transactions(self, league_id: str, week: int) -> list[Transaction]:
        data = await self._http.get(f"/league/{league_id}/transactions/{week}")
        return [Transaction.model_validate(d) for d in data]

    async def get_traded_picks(self, league_id: str) -> list[TradedPick]:
        data = await self._http.get(f"/league/{league_id}/traded_picks")
        return [TradedPick.model_validate(d) for d in data]
