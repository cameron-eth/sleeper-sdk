"""Suggest trades by matching positional surplus with positional need.

For every (my_roster, other_roster) pair we look for a 1-for-1 player swap that:
  1. Sends a player from a position where I'm above the league median
  2. Receives a player at a position where I'm below the league median
  3. Has a KTC value within `value_tolerance_pct` percent both ways

Suggestions are ranked by composite benefit: positive value_delta, P/E arbitrage,
and how much each side moves toward median depth.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional

VALID_POSITIONS = {"QB", "RB", "WR", "TE"}


@dataclass
class PlayerLeg:
    sleeper_id: str
    name: str
    position: str
    team: str
    ktc_value: int
    pe_ratio: Optional[float] = None


@dataclass
class TradeSuggestion:
    from_roster_id: int
    from_owner: str
    to_roster_id: int
    to_owner: str
    send_players: list[PlayerLeg] = field(default_factory=list)
    receive_players: list[PlayerLeg] = field(default_factory=list)
    send_value: int = 0
    receive_value: int = 0
    value_delta: int = 0
    # KTC-style Value Adjustment (stud side bonus for lopsided count). For
    # 1-for-1 suggestions this is always 0 — populated for forward-compat.
    value_adjustment: int = 0
    adjusted_delta: int = 0
    your_position_help: dict[str, int] = field(default_factory=dict)
    their_position_help: dict[str, int] = field(default_factory=dict)
    pe_arbitrage: float = 0.0
    rationale: str = ""


def _pos_counts(player_ids: list[str], sleeper_players: dict) -> dict[str, int]:
    """Count players on a roster by position (only QB/RB/WR/TE)."""
    counts = {pos: 0 for pos in VALID_POSITIONS}
    for pid in player_ids or []:
        sp = sleeper_players.get(pid)
        if sp and sp.position in VALID_POSITIONS:
            counts[sp.position] += 1
    return counts


def _ktc_value(ktc_player, fmt: str) -> int:
    if ktc_player is None:
        return 0
    return ktc_player.superflex.value if fmt == "sf" else ktc_player.one_qb.value


def suggest_trades(
    my_roster,
    all_rosters: list,
    sleeper_players: dict,
    sleeper_to_ktc: dict,
    user_display: dict[str, str],
    pe_by_sleeper_id: Optional[dict[str, float]] = None,
    fmt: str = "sf",
    top: int = 10,
    max_per_partner: int = 2,
    value_tolerance_pct: float = 10.0,
    surplus_threshold: int = 2,
    need_threshold: int = -1,
    position_filter: Optional[str] = None,
) -> list[TradeSuggestion]:
    """Find 1-for-1 trades that improve my_roster's positional balance.

    Args:
        my_roster: Roster — the user's roster
        all_rosters: list[Roster] — every roster in the league (incl. mine)
        sleeper_players: dict[sleeper_id -> Player]
        sleeper_to_ktc: dict[sleeper_id -> KTCPlayer] (pre-built reverse map)
        user_display: dict[user_id -> display_name]
        pe_by_sleeper_id: optional dict[sleeper_id -> pe_ratio] for arbitrage scoring
        fmt: "sf" or "1qb"
        top: max number of suggestions to return overall
        max_per_partner: max suggestions per (other_team) pair
        value_tolerance_pct: KTC value match tolerance, both directions
        surplus_threshold: must have at least (median + this) at sending position
        need_threshold: at receiving position must be at most (median + this)
        position_filter: if set, only suggest trades involving this position on either side
    """
    pe_by_sleeper_id = pe_by_sleeper_id or {}

    # 1. League positional medians
    all_counts = [_pos_counts(r.players or [], sleeper_players) for r in all_rosters]
    medians: dict[str, float] = {
        pos: statistics.median([c[pos] for c in all_counts]) for pos in VALID_POSITIONS
    }

    my_counts = _pos_counts(my_roster.players or [], sleeper_players)

    # 2. My surplus & need positions
    my_surplus = [pos for pos in VALID_POSITIONS if my_counts[pos] - medians[pos] >= surplus_threshold]
    my_need = [pos for pos in VALID_POSITIONS if my_counts[pos] - medians[pos] <= need_threshold]

    if position_filter:
        position_filter = position_filter.upper()

    # Pre-build my eligible "send" players grouped by position
    def player_legs(roster, positions: list[str]) -> dict[str, list[PlayerLeg]]:
        bucket: dict[str, list[PlayerLeg]] = {p: [] for p in positions}
        for pid in roster.players or []:
            sp = sleeper_players.get(pid)
            if not sp or sp.position not in positions:
                continue
            ktc_p = sleeper_to_ktc.get(pid)
            val = _ktc_value(ktc_p, fmt)
            if val <= 0:
                continue
            name = sp.full_name or " ".join(p for p in [sp.first_name or "", sp.last_name or ""] if p) or pid
            bucket[sp.position].append(PlayerLeg(
                sleeper_id=pid,
                name=name,
                position=sp.position,
                team=sp.team or "FA",
                ktc_value=val,
                pe_ratio=pe_by_sleeper_id.get(pid),
            ))
        for pos in bucket:
            bucket[pos].sort(key=lambda p: -p.ktc_value)
        return bucket

    my_send_pool = player_legs(my_roster, my_surplus)

    suggestions: list[TradeSuggestion] = []

    for other in all_rosters:
        if other.roster_id == my_roster.roster_id:
            continue
        their_counts = _pos_counts(other.players or [], sleeper_players)

        # Their surplus matching MY need
        their_surplus = [
            pos for pos in my_need
            if their_counts[pos] - medians[pos] >= surplus_threshold
        ]
        # Their need matching MY surplus
        their_need = [
            pos for pos in my_surplus
            if their_counts[pos] - medians[pos] <= need_threshold
        ]

        if not (their_surplus and their_need):
            continue

        # Apply position filter
        if position_filter and position_filter not in (their_surplus + their_need):
            continue

        their_send_pool = player_legs(other, their_surplus)

        per_partner_count = 0
        partner_suggestions: list[TradeSuggestion] = []

        # For each combination of (I send a player at their_need pos, I get a player at their_surplus pos)
        for send_pos in their_need:
            if position_filter and send_pos != position_filter and len(their_surplus) > 0:
                pass  # filter applies to whole trade not single side
            for receive_pos in their_surplus:
                if position_filter and position_filter not in (send_pos, receive_pos):
                    continue
                send_candidates = my_send_pool.get(send_pos, [])
                receive_candidates = their_send_pool.get(receive_pos, [])
                # Skip the highest-value player on a position (presumed untouchable)
                # — only consider the 2nd+ ranked players for sending.
                send_candidates = send_candidates[1:] if len(send_candidates) > 1 else send_candidates

                for send_p in send_candidates:
                    tol = send_p.ktc_value * value_tolerance_pct / 100.0
                    lo = send_p.ktc_value - tol
                    hi = send_p.ktc_value + tol
                    matches = [r for r in receive_candidates if lo <= r.ktc_value <= hi]
                    if not matches:
                        continue
                    # Best match: closest in value, ties go to lower-PE (better buy)
                    matches.sort(key=lambda r: (
                        abs(r.ktc_value - send_p.ktc_value),
                        r.pe_ratio if r.pe_ratio is not None else 99,
                    ))
                    receive_p = matches[0]
                    if receive_p.sleeper_id == send_p.sleeper_id:
                        continue

                    delta = receive_p.ktc_value - send_p.ktc_value
                    pe_gain = 0.0
                    if send_p.pe_ratio and receive_p.pe_ratio:
                        pe_gain = round(send_p.pe_ratio - receive_p.pe_ratio, 3)

                    # Position-help: my counts after the swap
                    your_help = {send_pos: -1, receive_pos: +1}
                    their_help = {send_pos: +1, receive_pos: -1}

                    rationale = (
                        f"You: {send_pos} surplus->balanced, {receive_pos} need->filled. "
                        f"Them: {receive_pos} surplus->balanced."
                    )

                    # Value adjustment (will be 0 for 1-for-1, but include for
                    # forward-compat when multi-player packages are added)
                    from sleeper.analytics.value_adjustment import apply_adjustment_to_delta
                    adj_delta, adj = apply_adjustment_to_delta(
                        raw_delta=delta,
                        send_values=[send_p.ktc_value],
                        receive_values=[receive_p.ktc_value],
                    )
                    sug = TradeSuggestion(
                        from_roster_id=my_roster.roster_id,
                        from_owner=user_display.get(str(my_roster.owner_id) or "", "you"),
                        to_roster_id=other.roster_id,
                        to_owner=user_display.get(str(other.owner_id) or "", f"roster {other.roster_id}"),
                        send_players=[send_p],
                        receive_players=[receive_p],
                        send_value=send_p.ktc_value,
                        receive_value=receive_p.ktc_value,
                        value_delta=delta,
                        value_adjustment=adj.adjustment,
                        adjusted_delta=adj_delta,
                        your_position_help=your_help,
                        their_position_help=their_help,
                        pe_arbitrage=pe_gain,
                        rationale=rationale,
                    )
                    partner_suggestions.append(sug)

        # Rank within partner: value_delta then pe_arbitrage
        partner_suggestions.sort(key=lambda s: (-s.adjusted_delta, -s.pe_arbitrage))
        suggestions.extend(partner_suggestions[:max_per_partner])

    # Global rank
    suggestions.sort(key=lambda s: (-s.adjusted_delta, -s.pe_arbitrage))
    return suggestions[:top]
