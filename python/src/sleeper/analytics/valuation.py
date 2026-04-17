"""Valuation analytics — KTC price vs real production.

Price-to-Earnings (P/E) ratio for dynasty assets:
    Price    = KTC value (market price)
    Earnings = fantasy points per game (FFPG)

Normalized against the positional median so QB/RB/WR/TE are comparable:
    price_multiple    = ktc_value / positional_median_ktc
    earnings_multiple = ffpg      / positional_median_ffpg
    pe_ratio          = price_multiple / earnings_multiple

PE > 1.5  -> overvalued (paying more than production justifies)
PE ~ 1.0  -> fair
PE < 0.7  -> undervalued
PE = None -> speculative (no production sample yet)
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional


VALID_POSITIONS = {"QB", "RB", "WR", "TE"}


@dataclass
class PlayerPERatio:
    sleeper_id: Optional[str]
    name: str
    position: str
    team: str
    age: Optional[float]
    ktc_value: int
    ffpg: float
    games: int
    pos_median_ktc: float
    pos_median_ffpg: float
    price_multiple: float
    earnings_multiple: float
    pe_ratio: Optional[float]
    raw_value_per_point: Optional[float]
    signal: str  # "undervalued" | "fair" | "overvalued" | "speculative"


def _classify_signal(pe: Optional[float]) -> str:
    if pe is None:
        return "speculative"
    if pe < 0.7:
        return "undervalued"
    if pe > 1.5:
        return "overvalued"
    return "fair"


def _aggregate_ffpg(
    season_stats: dict,
    sleeper_id: str,
    seasons: list[int],
    scoring: str = "ppr",
) -> tuple[float, int]:
    """Return (ffpg, games) summed across the requested seasons."""
    per_season = season_stats.get(sleeper_id, {})
    total_pts = 0.0
    total_games = 0
    for yr in seasons:
        line = per_season.get(yr)
        if not line:
            continue
        total_games += line.games or 0
        if scoring == "ppr":
            total_pts += line.fantasy_points_ppr or 0.0
        else:
            total_pts += line.fantasy_points or 0.0
    if total_games <= 0:
        return 0.0, 0
    return total_pts / total_games, total_games


def compute_pe_ratios(
    ktc_players: list,
    season_stats: dict,
    seasons: list[int],
    fmt: str = "sf",
    scoring: str = "ppr",
    min_games: int = 4,
) -> list[PlayerPERatio]:
    """Compute the P/E ratio for every KTC player matched to Sleeper stats.

    Args:
        ktc_players: list of KTCPlayer (must already have sleeper_id populated)
        season_stats: {sleeper_id: {season: SeasonStatLine}} from get_season_stats()
        seasons: list of season years to aggregate FFPG across
        fmt: "sf" or "1qb"
        scoring: "ppr" or "standard"
        min_games: players below this threshold are excluded from the
            positional median calc (their FFPG is too noisy) but still get
            a PlayerPERatio record marked "speculative" if they have a KTC value.

    Returns:
        list[PlayerPERatio] sorted by pe_ratio ascending, with speculative
        entries pushed to the end.
    """
    # Step 1: gather (player, ktc_value, ffpg, games) for valid positions
    rows: list[tuple] = []
    for p in ktc_players:
        pos = (p.position or "").upper()
        if pos not in VALID_POSITIONS:
            continue
        if "Pick" in (p.player_name or ""):
            continue
        ktc_val = p.superflex.value if fmt == "sf" else p.one_qb.value
        if ktc_val <= 0:
            continue
        sid = getattr(p, "sleeper_id", None)
        ffpg, games = (0.0, 0)
        if sid:
            ffpg, games = _aggregate_ffpg(season_stats, sid, seasons, scoring)
        rows.append((p, sid, ktc_val, ffpg, games))

    # Step 2: positional medians (only over players with enough games)
    pos_ktc: dict[str, list[int]] = {}
    pos_ffpg: dict[str, list[float]] = {}
    for p, sid, ktc_val, ffpg, games in rows:
        pos = p.position.upper()
        pos_ktc.setdefault(pos, []).append(ktc_val)
        if games >= min_games and ffpg > 0:
            pos_ffpg.setdefault(pos, []).append(ffpg)

    median_ktc = {pos: statistics.median(v) for pos, v in pos_ktc.items() if v}
    median_ffpg = {pos: statistics.median(v) for pos, v in pos_ffpg.items() if v}

    # Step 3: build PlayerPERatio records
    results: list[PlayerPERatio] = []
    for p, sid, ktc_val, ffpg, games in rows:
        pos = p.position.upper()
        m_ktc = median_ktc.get(pos, 0.0) or 0.0
        m_ffpg = median_ffpg.get(pos, 0.0) or 0.0

        price_mult = (ktc_val / m_ktc) if m_ktc > 0 else 0.0

        if games >= min_games and ffpg > 0 and m_ffpg > 0:
            earnings_mult = ffpg / m_ffpg
            pe = price_mult / earnings_mult if earnings_mult > 0 else None
            raw_vpp = ktc_val / max(ffpg, 0.01)
        else:
            earnings_mult = 0.0
            pe = None
            raw_vpp = None

        results.append(PlayerPERatio(
            sleeper_id=sid,
            name=p.player_name,
            position=pos,
            team=p.team or "FA",
            age=p.age,
            ktc_value=ktc_val,
            ffpg=round(ffpg, 2),
            games=games,
            pos_median_ktc=round(m_ktc, 1),
            pos_median_ffpg=round(m_ffpg, 2),
            price_multiple=round(price_mult, 3),
            earnings_multiple=round(earnings_mult, 3),
            pe_ratio=round(pe, 3) if pe is not None else None,
            raw_value_per_point=round(raw_vpp, 1) if raw_vpp is not None else None,
            signal=_classify_signal(pe),
        ))

    # Speculative entries (pe=None) sort to the bottom of pe-asc views
    results.sort(key=lambda r: (r.pe_ratio is None, r.pe_ratio if r.pe_ratio is not None else 0))
    return results
