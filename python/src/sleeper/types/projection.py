"""Models for the `api.sleeper.com` projections/stats feed.

This is a *different host* from the documented read API (`api.sleeper.app/v1`)
and it is undocumented, so the models are deliberately permissive:
`extra="allow"` everywhere and almost every field optional. Sleeper adds
`stats` keys mid-season and has changed the provider (`company`) between
years; parsing must not break when that happens.

Shape of one record::

    {"player_id": "6904", "week": 1, "season": "2026", "season_type": "regular",
     "team": "PHI", "opponent": "WAS", "game_id": "202610126", "date": "2026-09-13",
     "company": "rotowire", "category": "proj",
     "stats": {"pts_ppr": 22.75, "pass_yd": 258.4, "pass_td": 1.69, ...},
     "player": {"first_name": "Jalen", "last_name": "Hurts", "position": "QB", ...}}

Three record classes matter for lineup decisions, and they are *not*
distinguished by the points value — they are distinguished by `team`/`game_id`:

======================  ==========  ==========  ===================================
Case                    ``team``    ``game_id`` ``stats``
======================  ==========  ==========  ===================================
Playing this week       set         set         full projection incl. ``pts_ppr``
On bye                  set         ``None``    ``{"adp_dd_ppr": ...}`` only
No NFL team (FA/UDFA)   ``None``    ``None``    ``{"adp_dd_ppr": ...}`` only
======================  ==========  ==========  ===================================

Note the trap: a bye-week record has **no** ``pts_ppr`` key rather than a
``pts_ppr`` of 0. Code that reaches for ``stats.get("pts_ppr", 0.0)`` gets the
right number by accident but loses the reason, and reports a bye as a genuine
"projected 0.0". Use :attr:`PlayerProjection.is_bye` /
:attr:`PlayerProjection.has_projection` to tell them apart.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, field_validator


class ProjectionPlayer(BaseModel, extra="allow"):
    """The embedded player blob. Thinner than `types.player.Player`.

    Carries `injury_status` and `fantasy_positions`, which is why it is worth
    reading instead of re-joining against `get_all_players()`.
    """

    first_name: str | None = None
    last_name: str | None = None
    position: str | None = None
    fantasy_positions: list[str] | None = None
    team: str | None = None
    years_exp: int | None = None
    injury_status: str | None = None
    injury_body_part: str | None = None
    injury_notes: str | None = None
    injury_start_date: str | None = None
    news_updated: int | None = None
    metadata: dict[str, Any] | None = None

    @property
    def full_name(self) -> str | None:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or None


class PlayerProjection(BaseModel, extra="allow"):
    """One player-week (or player-season) row from the projections feed.

    Also used for the `/stats/` endpoint, which returns the identical shape
    with `category="stat"` and realized rather than projected numbers.
    """

    player_id: str
    week: int | None = None
    season: str | None = None
    season_type: str | None = None
    sport: str | None = None
    category: str | None = None
    company: str | None = None
    team: str | None = None
    opponent: str | None = None
    game_id: str | None = None
    date: str | None = None
    status: str | None = None
    updated_at: int | None = None
    last_modified: int | None = None
    stats: dict[str, float] = {}
    player: Optional[ProjectionPlayer] = None

    @field_validator("stats", mode="before")
    @classmethod
    def _coerce_stats(cls, v: Any) -> dict:
        """Tolerate `"stats": null` and drop non-numeric values.

        Every record observed so far carries a dict, but this endpoint is
        undocumented and a single bad value should not fail the whole batch.
        """
        if not isinstance(v, dict):
            return {}
        out: dict[str, float] = {}
        for k, val in v.items():
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                out[str(k)] = float(val)
        return out

    # -- identity -----------------------------------------------------------

    @property
    def name(self) -> str | None:
        return self.player.full_name if self.player else None

    @property
    def position(self) -> str | None:
        return self.player.position if self.player else None

    @property
    def fantasy_positions(self) -> list[str]:
        if self.player and self.player.fantasy_positions:
            return list(self.player.fantasy_positions)
        pos = self.position
        return [pos] if pos else []

    @property
    def injury_status(self) -> str | None:
        return self.player.injury_status if self.player else None

    # -- availability -------------------------------------------------------

    @property
    def has_game(self) -> bool:
        """True when this player's team plays in this week."""
        return self.game_id is not None

    @property
    def is_bye(self) -> bool:
        """True when the player is rostered by an NFL team that is idle.

        Distinct from `team is None` (unsigned / practice squad), which has no
        game for a different reason and should not be described as a bye.
        """
        return self.team is not None and self.game_id is None

    @property
    def has_projection(self) -> bool:
        """True when Sleeper published real scoring volume for this row.

        Most rows in a position-wide response are deep-bench filler carrying
        only an ADP field — 159 of 1364 week-1 WR rows had `pts_ppr`. Filter
        on this before treating a 0.0 as a forecast.
        """
        return "pts_ppr" in self.stats

    # -- points -------------------------------------------------------------

    @property
    def pts_ppr(self) -> float | None:
        return self.stats.get("pts_ppr")

    @property
    def pts_half_ppr(self) -> float | None:
        return self.stats.get("pts_half_ppr")

    @property
    def pts_std(self) -> float | None:
        return self.stats.get("pts_std")

    def points(self, scoring: str = "ppr") -> float | None:
        """Sleeper's own precomputed total for one of its three base formats.

        For league-specific scoring use
        `enrichment.projections.score_projection`, which re-derives the total
        from the stat line and the league's `scoring_settings`.
        """
        key = {"ppr": "pts_ppr", "half_ppr": "pts_half_ppr", "std": "pts_std"}.get(scoring)
        if key is None:
            raise ValueError(f"scoring must be one of ppr/half_ppr/std, got {scoring!r}")
        return self.stats.get(key)
