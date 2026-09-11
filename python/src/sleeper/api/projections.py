"""Projections and realized weekly stats from `api.sleeper.com`.

This endpoint family is **not** part of the documented `api.sleeper.app/v1`
read API — different host, no `/v1` prefix, no published contract. It is what
the Sleeper app itself renders, so it is the same projection a leaguemate sees
in the app, which is the reason to prefer it over a third-party source for
start/sit answers.

Quirks worth knowing, all established empirically:

* **`position` does not accept multiple values.** Repeating the param
  (`&position=QB&position=RB`) silently keeps only the last one, and
  `position=QB,RB` returns `[]`. Passing a list here fans out one request per
  position concurrently and merges, deduped by `player_id`.
* **Omitting `position` returns the entire NFL** — ~9.4k rows including
  offensive linemen and long snappers, ~5.7 MB. Always pass positions unless
  you truly want that.
* **Most rows are filler.** A position-wide response includes every rostered
  and unrostered player; only the ones with a real forecast carry `pts_ppr`
  (see `PlayerProjection.has_projection`).
* **Bye weeks appear as rows with `game_id: null`** rather than being omitted.
"""
from __future__ import annotations

import asyncio
from typing import Any, Iterable, Sequence, Union

from sleeper.http.client import HttpClient
from sleeper.types.projection import PlayerProjection

PositionArg = Union[str, Sequence[str], None]

#: Skill positions worth fetching for lineup decisions. The default for
#: `get_week`, chosen so the common case does not download the 5.7 MB
#: everyone-in-the-NFL response.
DEFAULT_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


def _normalize_positions(position: PositionArg) -> list[str] | None:
    """Return a de-duplicated, upper-cased position list, or None for 'all'."""
    if position is None:
        return None
    if isinstance(position, str):
        candidates: Iterable[str] = [position]
    else:
        candidates = position
    seen: list[str] = []
    for p in candidates:
        if p is None:
            continue
        norm = str(p).strip().upper()
        if norm and norm not in seen:
            seen.append(norm)
    return seen or None


class ProjectionsApi:
    """Read projections and weekly stats.

    Lives on its own `HttpClient` because the base URL differs from the rest
    of the SDK; `SleeperClient` owns both and closes both.
    """

    def __init__(self, http: HttpClient):
        self._http = http

    # -- internal -----------------------------------------------------------

    async def _fetch(
        self,
        path: str,
        *,
        positions: list[str] | None,
        season_type: str,
        order_by: str | None,
    ) -> list[PlayerProjection]:
        base: dict[str, Any] = {"season_type": season_type}
        if order_by:
            base["order_by"] = order_by

        if not positions:
            data = await self._http.get(path, params=base)
            return [PlayerProjection.model_validate(d) for d in data or []]

        # One request per position — the API cannot union them itself.
        async def one(pos: str) -> list:
            params = dict(base, position=pos)
            return await self._http.get(path, params=params) or []

        batches = await asyncio.gather(*(one(p) for p in positions))

        # A player eligible at two requested positions (e.g. a WR listed with
        # RB among fantasy_positions) comes back in both batches. Keep first.
        merged: dict[str, PlayerProjection] = {}
        for batch in batches:
            for d in batch:
                proj = PlayerProjection.model_validate(d)
                merged.setdefault(proj.player_id, proj)
        return list(merged.values())

    # -- projections --------------------------------------------------------

    async def get_week(
        self,
        season: int | str,
        week: int,
        *,
        position: PositionArg = DEFAULT_POSITIONS,
        season_type: str = "regular",
        order_by: str | None = "pts_ppr",
        sport: str = "nfl",
    ) -> list[PlayerProjection]:
        """Projections for one week.

        Args:
            position: One position, a sequence of them, or None for every
                position in the league (large — see module docstring).
            order_by: Sleeper-side sort key, e.g. `pts_ppr`. Only meaningful
                for a single position; with a fan-out each batch is sorted
                independently, so re-sort the merged list yourself.
        """
        return await self._fetch(
            f"/projections/{sport}/{season}/{week}",
            positions=_normalize_positions(position),
            season_type=season_type,
            order_by=order_by,
        )

    async def get_season(
        self,
        season: int | str,
        *,
        position: PositionArg = DEFAULT_POSITIONS,
        season_type: str = "regular",
        order_by: str | None = "pts_ppr",
        sport: str = "nfl",
    ) -> list[PlayerProjection]:
        """Full-season projections (rest-of-season totals, plus ADP fields)."""
        return await self._fetch(
            f"/projections/{sport}/{season}",
            positions=_normalize_positions(position),
            season_type=season_type,
            order_by=order_by,
        )

    async def get_player_weeks(
        self,
        player_id: str,
        season: int | str,
        *,
        season_type: str = "regular",
        sport: str = "nfl",
    ) -> dict[int, PlayerProjection]:
        """Every week's projection for one player, keyed by week number.

        Cheaper than a position sweep when you already know the player, and the
        natural source for a rest-of-season view.

        **This endpoint's rows differ from the weekly sweep's in two ways that
        will bite you**, both verified against the live feed:

        1. **No embedded `player` object.** `name`, `position` and
           `injury_status` all read as None here. Join against
           `get_all_players()` if you need them.
        2. **The bye week is absent, not flagged.** Sleeper returns `null` for
           it (week 10 for a 2026 PHI player), and those weeks are dropped
           from the result — so the keys are exactly the weeks with data, and
           a bye shows up as a missing key rather than as `is_bye`. The
           position sweep does the opposite: it *includes* a bye row with
           `game_id: None`.

        So the bye weeks are `set(range(1, 19)) - set(result)`, and
        `is_bye` is always False on these rows.
        """
        data = await self._http.get(
            f"/projections/{sport}/player/{player_id}",
            params={"season": str(season), "season_type": season_type, "grouping": "week"},
        )
        out: dict[int, PlayerProjection] = {}
        for week_key, record in (data or {}).items():
            if not record:
                continue
            try:
                week_num = int(week_key)
            except (TypeError, ValueError):
                continue
            out[week_num] = PlayerProjection.model_validate(record)
        return out

    # -- realized stats -----------------------------------------------------

    async def get_week_stats(
        self,
        season: int | str,
        week: int,
        *,
        position: PositionArg = DEFAULT_POSITIONS,
        season_type: str = "regular",
        order_by: str | None = "pts_ppr",
        sport: str = "nfl",
    ) -> list[PlayerProjection]:
        """Realized stats for a completed week — same shape as projections.

        Included here because it is the same host, params and schema, and
        because "what has he actually done lately" is half of a start/sit
        answer. `category` is `"stat"` on these rows, not `"proj"`.
        """
        return await self._fetch(
            f"/stats/{sport}/{season}/{week}",
            positions=_normalize_positions(position),
            season_type=season_type,
            order_by=order_by,
        )
