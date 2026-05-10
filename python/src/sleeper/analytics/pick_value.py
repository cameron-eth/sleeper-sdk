"""Lookup helpers for KTC valuations of draft picks.

KTC encodes picks as RDP-position players with names like "2027 Mid 1st"
(season + tier + round-ordinal). Trades reference picks by season + round
without slot information, so this module bridges the two: given a season
and round, find the closest KTC pick row and return its value.

The lookup tries Mid → Early → Late tier in order. "Mid" is the safest
default for picks of unknown draft slot; using Early would over-value
late-round picks and Late would under-value early-round ones.
"""
from __future__ import annotations

from typing import Mapping


# Round number → ordinal suffix used by KTC pick names.
_ROUND_ORDINALS: dict[int, str] = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}

# Tier search order — closest-to-Mid first, fall back outward.
_TIER_FALLBACK: tuple[str, ...] = ("Mid", "Early", "Late")


def _ordinal(rnd: int) -> str:
    """Return the KTC ordinal string for a round number."""
    return _ROUND_ORDINALS.get(rnd, f"{rnd}th")


def lookup_pick_ktc(
    season: str,
    rnd: int,
    pick_ktc_by_name: Mapping[str, "object"],
    fmt: str = "sf",
) -> int:
    """Resolve a (season, round) pick to its KTC value.

    Args:
        season: 4-digit year string, e.g. "2027".
        rnd: Round number (1-4 supported by name; higher rounds fall through).
        pick_ktc_by_name: Map of "<season> <tier> <ordinal>" → KTCPlayer
            (the caller typically builds this with
            `{p.player_name: p for p in ktc_players if p.position == "RDP"}`).
        fmt: KTC format — "sf" (default) or "1qb".

    Returns:
        Pick KTC value, or 0 if no matching tier exists.

    Tier resolution: Mid → Early → Late. Mid is the safest default for
    picks of unknown slot (matches typical league-wide pick valuation).

    Examples:
        >>> picks = {"2027 Mid 1st": _FakeKTC(sf=5500)}
        >>> lookup_pick_ktc("2027", 1, picks)
        5500
        >>> lookup_pick_ktc("2030", 1, picks)   # not present at any tier
        0
    """
    ord_str = _ordinal(rnd)
    for tier in _TIER_FALLBACK:
        name = f"{season} {tier} {ord_str}"
        p = pick_ktc_by_name.get(name)
        if p is None:
            continue
        side = getattr(p, "superflex", None) if fmt == "sf" else getattr(p, "one_qb", None)
        if side is None:
            continue
        val = getattr(side, "value", None)
        if val:
            return int(val)
    return 0
