"""Projections and realized weekly stats from `api.sleeper.com`.

This endpoint family is **not** part of the documented `api.sleeper.app/v1`
read API — different host, no `/v1` prefix, no published contract. It is what
the Sleeper app itself renders, so it is the same projection a leaguemate sees
in the app, which is the reason to prefer it over a third-party source for
start/sit answers.

Quirks worth knowing, all established empirically:

* **Multiple positions need the bracket form `position[]`.** Repeating the
  plain param (`&position=QB&position=RB`) silently keeps only the *last*
  one, and `position=QB,RB` returns `[]` — both fail quietly, which is why
  this module always sends `position[]`. The bracket form unions them in a
  single request: verified against week 3 of 2026, `position[]` for
  QB/RB/TE/WR returned exactly the 3,116 rows of the four single-position
  responses combined, same `player_id` set, no duplicates. It works the same
  way on `/stats/`.
* **`order_by` is accepted and ignored.** `order_by=ppr`, `order_by=pts_ppr`
  and `order_by=bogus` all return a byte-identical, *unsorted* body — the
  leading rows carry no `pts_ppr` at all. It is sent because the app sends
  it; never rely on the response being ordered. Sort client-side
  (`enrichment.projections.rank_projections` does).
* **Omitting `position` returns the entire NFL** — ~9.4k rows including
  offensive linemen and long snappers, ~5.7 MB. Always pass positions unless
  you truly want that.
* **Most rows are filler.** A position-wide response includes every rostered
  and unrostered player; only the ones with a real forecast carry `pts_ppr`
  (see `PlayerProjection.has_projection`).
* **Bye weeks appear as rows with `game_id: null`** rather than being omitted.
"""
from __future__ import annotations

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
        """One request, however many positions — see the module docstring.

        `positions=None` omits the param entirely, which returns every
        position in the NFL (~9.4k rows).
        """
        params: dict[str, Any] = {"season_type": season_type}
        if order_by:
            params["order_by"] = order_by
        if positions:
            # Bracket form. The plain `position` param cannot express a union
            # and fails silently when asked to; never send it.
            params["position[]"] = positions

        data = await self._http.get(path, params=params)

        # Sleeper has not been observed to repeat a player across positions in
        # one response, but a player eligible at two of them (a WR carrying RB
        # in `fantasy_positions`) is exactly the row that would, and a caller
        # counting starters must not see them twice. Keep the first.
        merged: dict[str, PlayerProjection] = {}
        for record in data or []:
            proj = PlayerProjection.model_validate(record)
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
            order_by: Sent for parity with the app's own request, but
                Sleeper ignores it and the body comes back unsorted. Sort the
                result yourself.
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
