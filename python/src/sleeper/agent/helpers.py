"""High-level composites for agents.

These helpers do the multi-call orchestration so agents can stay one
function-call away from the answer they need.

Conventions
-----------
* Every helper is sync-callable (uses SleeperClient.sync internally) but
  also exposes an async variant (`a*` prefix).
* Every helper returns plain dicts/lists, not Pydantic models, so the
  result is JSON-serializable by default.
* No I/O outside the helper — call once, get everything.

Examples
--------
    from sleeper.agent.helpers import build_context, summarize_inbox

    ctx = build_context(username="camfleety", league_filter="Meat Market")
    decisions = summarize_inbox(ctx)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from sleeper.client import SleeperClient


# ---------------------------------------------------------------------------
# Internal shared bootstrap
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_season() -> str:
    return str(datetime.now().year)


async def _resolve_user_and_league(
    client: SleeperClient,
    username: str,
    league_filter: Optional[str] = None,
) -> tuple:
    user = await client.users.get_user(username)
    leagues = await client.users.get_user_leagues(user.user_id, season=_current_season())
    if not leagues:
        raise ValueError(f"No leagues for user {username}")
    if league_filter:
        matched = [lg for lg in leagues if league_filter.lower() in (lg.name or "").lower()]
        if not matched:
            raise ValueError(
                f"No league matches {league_filter!r}. Available: "
                + ", ".join(lg.name for lg in leagues)
            )
        league = matched[0]
    elif len(leagues) == 1:
        league = leagues[0]
    else:
        raise ValueError(
            "Multiple leagues; pass league_filter. Available: "
            + ", ".join(lg.name for lg in leagues)
        )
    return user, league, leagues


# ---------------------------------------------------------------------------
# build_context — the one-call agent prompt-input
# ---------------------------------------------------------------------------

async def abuild_context(
    username: str,
    league_filter: Optional[str] = None,
    *,
    include_opponents: bool = True,
    include_picks: bool = True,
) -> dict:
    """Return a full agent-ready context bundle for one league.

    Shape:
        {
          "user": {...}, "league": {...}, "week": int,
          "my_roster": {...},
          "opponents": [{owner, players[]}, ...],
          "matchup": {opponent_owner, my_starters, opp_starters},
          "settings": {...},
          "picks": [...]            # if include_picks
        }
    """
    async with SleeperClient() as client:
        user, league, _all = await _resolve_user_and_league(client, username, league_filter)
        rosters = await client.leagues.get_rosters(league.league_id)
        users = await client.leagues.get_users(league.league_id)
        sleeper_players = await client.get_all_players()
        state = await client.state.get_state()
        week = int(getattr(state, "week", 1) or 1)

        my_roster = next((r for r in rosters if r.owner_id == user.user_id), None)

        # display name lookup
        u_by_id = {u.user_id: u for u in users}

        def expand_player(pid: str) -> dict:
            p = sleeper_players.get(pid)
            if not p:
                return {"player_id": pid}
            return {
                "player_id": pid,
                "name": getattr(p, "full_name", None) or f"{p.first_name} {p.last_name}",
                "position": p.position,
                "team": p.team,
                "age": getattr(p, "age", None),
                "injury_status": getattr(p, "injury_status", None),
                "status": getattr(p, "status", None),
            }

        def roster_view(r) -> dict:
            owner = u_by_id.get(r.owner_id)
            return {
                "roster_id": r.roster_id,
                "owner_user_id": r.owner_id,
                "owner_display_name": getattr(owner, "display_name", None) if owner else None,
                "starters": [expand_player(p) for p in (r.starters or []) if p and p != "0"],
                "players": [expand_player(p) for p in (r.players or []) if p and p != "0"],
                "taxi": [expand_player(p) for p in (r.taxi or []) if p],
                "reserve": [expand_player(p) for p in (r.reserve or []) if p],
                "settings": r.settings,
            }

        my_view = roster_view(my_roster) if my_roster else None
        opponent_views = (
            [roster_view(r) for r in rosters if r.roster_id != (my_roster.roster_id if my_roster else -1)]
            if include_opponents else []
        )

        # current week matchup
        matchup_view: dict = {}
        try:
            matchups = await client.leagues.get_matchups(league.league_id, week)
            if my_roster:
                mine = next((m for m in matchups if m.roster_id == my_roster.roster_id), None)
                opp = next((m for m in matchups if mine and m.matchup_id == mine.matchup_id and m.roster_id != mine.roster_id), None)
                if mine and opp:
                    opp_roster = next((r for r in rosters if r.roster_id == opp.roster_id), None)
                    matchup_view = {
                        "week": week,
                        "my_starters": [expand_player(p) for p in (mine.starters or []) if p and p != "0"],
                        "my_projected_points": getattr(mine, "points", None),
                        "opponent_roster_id": opp.roster_id,
                        "opponent_owner_display_name": (
                            getattr(u_by_id.get(opp_roster.owner_id) if opp_roster else None, "display_name", None)
                        ),
                        "opp_starters": [expand_player(p) for p in (opp.starters or []) if p and p != "0"],
                        "opp_projected_points": getattr(opp, "points", None),
                    }
        except Exception:
            pass

        traded_picks: list = []
        if include_picks:
            try:
                tp = await client.leagues.get_traded_picks(league.league_id)
                traded_picks = [t.model_dump() if hasattr(t, "model_dump") else dict(t) for t in tp]
            except Exception:
                pass

    return {
        "ts": _now_iso(),
        "user": {"user_id": user.user_id, "username": user.username, "display_name": user.display_name},
        "league": {
            "league_id": league.league_id,
            "name": league.name,
            "season": league.season,
            "total_rosters": league.total_rosters,
        },
        "week": week,
        "my_roster": my_view,
        "opponents": opponent_views,
        "matchup": matchup_view,
        "settings": getattr(league, "settings", None),
        "scoring_settings": getattr(league, "scoring_settings", None),
        "roster_positions": getattr(league, "roster_positions", None),
        "traded_picks": traded_picks,
    }


def build_context(username: str, league_filter: Optional[str] = None, **kw) -> dict:
    return asyncio.run(abuild_context(username, league_filter, **kw))


# ---------------------------------------------------------------------------
# summarize_inbox — translate raw GraphQL trades into agent-readable rows
# ---------------------------------------------------------------------------

def summarize_inbox(
    trades: list,
    *,
    my_roster_id: Optional[int] = None,
    sleeper_players: Optional[dict] = None,
    ktc_lookup: Optional[dict] = None,
) -> list[dict]:
    """Flatten a list of raw trade dicts into one row per trade.

    Each row has: transaction_id, status, leg, my_side[], their_side[],
    my_ktc_total, their_ktc_total, ktc_delta, requires_my_consent.
    """
    out = []
    for t in trades or []:
        adds = t.get("metadata", {}).get("adds") if isinstance(t.get("metadata"), dict) else None
        # Sleeper packs adds/drops into metadata or top-level keys depending on path.
        # The graphql response from get_trades() uses settings/metadata; defer to the caller
        # to enrich beyond what we know.
        roster_ids = t.get("roster_ids") or []
        consenter_ids = t.get("consenter_ids") or []

        def player_view(pid: str) -> dict:
            if not sleeper_players:
                return {"player_id": pid}
            p = sleeper_players.get(pid)
            if not p:
                return {"player_id": pid}
            return {
                "player_id": pid,
                "name": getattr(p, "full_name", None) or f"{p.first_name} {p.last_name}",
                "position": p.position,
                "team": p.team,
                "ktc": (ktc_lookup or {}).get(pid),
            }

        my_side: list = []
        their_side: list = []
        # Best-effort: if we know the metadata shape, derive sides; else leave empty for caller
        meta = t.get("metadata") if isinstance(t.get("metadata"), dict) else {}
        if my_roster_id is not None and meta:
            for pid, rid in (meta.get("adds") or {}).items():
                view = player_view(pid)
                if int(rid) == my_roster_id:
                    my_side.append(view)
                else:
                    their_side.append(view)

        my_ktc = sum((p.get("ktc") or 0) for p in my_side)
        their_ktc = sum((p.get("ktc") or 0) for p in their_side)

        out.append({
            "transaction_id": t.get("transaction_id"),
            "status": t.get("status"),
            "leg": t.get("leg"),
            "creator_user_id": t.get("creator"),
            "roster_ids": roster_ids,
            "my_side": my_side,
            "their_side": their_side,
            "my_ktc_total": my_ktc,
            "their_ktc_total": their_ktc,
            "ktc_delta": their_ktc - my_ktc,        # positive = I gain
            "requires_my_consent": (
                my_roster_id is not None and my_roster_id in roster_ids
                and (not consenter_ids or my_roster_id not in consenter_ids)
            ),
            "raw": t,
        })
    return out


# ---------------------------------------------------------------------------
# optimal_lineup — projection-driven lineup optimizer
# ---------------------------------------------------------------------------

# Standard slot definitions across most leagues.
SLOT_ELIGIBILITY = {
    "QB":     {"QB"},
    "RB":     {"RB"},
    "WR":     {"WR"},
    "TE":     {"TE"},
    "FLEX":   {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX":  {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "K":      {"K"},
    "DEF":    {"DEF"},
    "BN":     set(),  # bench
    "IR":     set(),
    "TAXI":   set(),
}


def optimal_lineup(
    roster_view: dict,
    roster_positions: list[str],
    projections: Optional[dict[str, float]] = None,
) -> dict:
    """Pick optimal starters given roster positions and per-player projections.

    Returns:
        {
          "starters": [player_id, ...],         # ordered to match roster_positions slots
          "by_slot": [{slot, player_id, name, projected_points}, ...],
          "current_starters": [...],
          "projected_points": float,
          "current_projected_points": float,
          "delta_vs_current": float,
        }
    """
    projections = projections or {}
    players = list(roster_view.get("players") or [])
    current = [p.get("player_id") for p in (roster_view.get("starters") or [])]

    # Filter actives
    eligible = [p for p in players
                if p.get("position") and p.get("status") in (None, "Active")]

    chosen: list[str] = []
    by_slot: list[dict] = []
    used: set[str] = set()

    # Sort by projection desc; greedy fill
    def proj(p: dict) -> float:
        return projections.get(p.get("player_id"), 0.0)

    for slot in roster_positions or []:
        slot_u = slot.upper()
        if slot_u in ("BN", "IR", "TAXI"):
            continue
        elig = SLOT_ELIGIBILITY.get(slot_u, set())
        candidates = [p for p in eligible
                      if p.get("player_id") not in used and p.get("position") in elig]
        candidates.sort(key=proj, reverse=True)
        pick = candidates[0] if candidates else None
        if pick:
            used.add(pick["player_id"])
            chosen.append(pick["player_id"])
            by_slot.append({
                "slot": slot,
                "player_id": pick["player_id"],
                "name": pick.get("name"),
                "position": pick.get("position"),
                "projected_points": proj(pick),
                "injury_status": pick.get("injury_status"),
            })
        else:
            chosen.append(None)
            by_slot.append({"slot": slot, "player_id": None})

    proj_total = sum((s.get("projected_points") or 0) for s in by_slot)
    cur_proj = sum(projections.get(pid, 0.0) for pid in (current or []))

    return {
        "starters": chosen,
        "by_slot": by_slot,
        "current_starters": current,
        "projected_points": round(proj_total, 2),
        "current_projected_points": round(cur_proj, 2),
        "delta_vs_current": round(proj_total - cur_proj, 2),
    }


# ---------------------------------------------------------------------------
# check_lineup_health — injury / bye / empty-slot scan
# ---------------------------------------------------------------------------

INJURY_DEMOTE = {"Out", "IR", "PUP", "Suspended", "Doubtful"}
INJURY_WATCH  = {"Questionable", "Probable", "DTD"}


def check_lineup_health(
    roster_view: dict,
    week: int,
    roster_positions: Optional[list[str]] = None,
) -> dict:
    """Inspect current starters for injury/bye/empty-slot risk.

    Returns:
        {
          "ok": bool,
          "risks": [
            {"severity": "high"|"medium"|"low",
             "player_id": ..., "name": ..., "slot": ...,
             "reason": "Out"|"Bye Week"|"Empty Slot"|"Questionable"|...},
            ...
          ],
          "summary": "2 high, 1 medium, 0 low"
        }
    """
    risks: list[dict] = []
    starters = roster_view.get("starters") or []
    slots = roster_positions or [None] * len(starters)

    for i, p in enumerate(starters):
        slot = slots[i] if i < len(slots) else None
        if not p or not p.get("player_id"):
            risks.append({"severity": "high", "slot": slot, "reason": "Empty Slot"})
            continue
        inj = p.get("injury_status")
        team = p.get("team")
        # Bye detection requires team's bye week — agent should pre-attach via projections
        # or a separate bye-week map. Here we only flag the field if present in the dict.
        bye = p.get("bye_week")
        if bye and int(bye) == int(week):
            risks.append({"severity": "high", "slot": slot,
                          "player_id": p["player_id"], "name": p.get("name"),
                          "reason": "Bye Week", "team": team})
            continue
        if inj in INJURY_DEMOTE:
            risks.append({"severity": "high", "slot": slot,
                          "player_id": p["player_id"], "name": p.get("name"),
                          "reason": inj, "team": team})
        elif inj in INJURY_WATCH:
            risks.append({"severity": "medium", "slot": slot,
                          "player_id": p["player_id"], "name": p.get("name"),
                          "reason": inj, "team": team})

    sev_count = {"high": 0, "medium": 0, "low": 0}
    for r in risks:
        sev_count[r.get("severity", "low")] += 1

    return {
        "ok": sev_count["high"] == 0,
        "risks": risks,
        "summary": f'{sev_count["high"]} high, {sev_count["medium"]} medium, {sev_count["low"]} low',
        "counts": sev_count,
    }


# ---------------------------------------------------------------------------
# rank_drop_candidates — pure ordering, no writes
# ---------------------------------------------------------------------------

def rank_drop_candidates(
    roster_view: dict,
    *,
    ktc_lookup: Optional[dict] = None,
    untouchables: Optional[set] = None,
    keep_position_min: Optional[dict] = None,
) -> list[dict]:
    """Return players on the roster ranked from safest to drop → riskiest."""
    ktc_lookup = ktc_lookup or {}
    untouchables = set((s or "").lower() for s in (untouchables or []))
    keep_min = keep_position_min or {}

    players = list(roster_view.get("players") or [])
    starters_set = {p.get("player_id") for p in roster_view.get("starters") or []}
    pos_count: dict = {}
    for p in players:
        pos_count[p.get("position")] = pos_count.get(p.get("position"), 0) + 1

    candidates: list[dict] = []
    for p in players:
        if (p.get("name") or "").lower() in untouchables:
            continue
        if p.get("player_id") in starters_set:
            continue
        pos = p.get("position")
        if pos and pos_count.get(pos, 0) <= keep_min.get(pos, 0):
            continue
        ktc = ktc_lookup.get(p.get("player_id")) or 0
        candidates.append({
            "player_id": p.get("player_id"),
            "name": p.get("name"),
            "position": pos,
            "team": p.get("team"),
            "ktc": ktc,
            "age": p.get("age"),
            "injury_status": p.get("injury_status"),
        })

    candidates.sort(key=lambda x: (x.get("ktc") or 0, x.get("age") or 0))
    return candidates


# ---------------------------------------------------------------------------
# rank_waiver_targets — FA pool sorted for adds
# ---------------------------------------------------------------------------

def rank_waiver_targets(
    fa_players: list[dict],
    *,
    ktc_lookup: Optional[dict] = None,
    position_priority: Optional[list[str]] = None,
    age_max: Optional[int] = None,
    top: int = 25,
) -> list[dict]:
    """Rank free agents by KTC, with position priority + age filter."""
    ktc_lookup = ktc_lookup or {}
    pri = {p: i for i, p in enumerate(position_priority or [])}

    rows: list[dict] = []
    for p in fa_players:
        if age_max and (p.get("age") or 0) > age_max:
            continue
        ktc = ktc_lookup.get(p.get("player_id")) or 0
        rows.append({
            "player_id": p.get("player_id"),
            "name": p.get("name"),
            "position": p.get("position"),
            "team": p.get("team"),
            "age": p.get("age"),
            "ktc": ktc,
            "_pri": pri.get(p.get("position"), 999),
        })
    rows.sort(key=lambda x: (x["_pri"], -x["ktc"], x.get("age") or 99))
    for r in rows:
        r.pop("_pri", None)
    return rows[:top]
