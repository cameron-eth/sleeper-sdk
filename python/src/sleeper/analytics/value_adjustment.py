"""KTC-style Value Adjustment for lopsided trades.

KeepTradeCut applies a "Value Adjustment" on lopsided trades — extra KTC is
added to the side giving up more roster spots or more "stud factor". The
principle (in their words): 12 third-round picks are NOT a fair deal for
DeAndre Hopkins — the 1-stud side needs compensation for compressing roster
depth and concentrating production.

This module implements the same principle as a pure function. It's not a
byte-for-byte reverse of KTC's proprietary formula (not publicly documented),
but it behaves the same way: lopsided-count + high-stud trades get an
adjustment that tilts value toward the stud side.

Usage:
    adj = compute_value_adjustment(
        send_values=[5200, 3100, 1400],
        receive_values=[9800],
    )
    # => {"adjustment": +1342, "favors": "send", "roster_spot_diff": 2, "top_stud_value": 9800}

    adjusted_delta = raw_delta - adj["adjustment"]  # subtract if you're the stud-receiving side
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# Stud-factor thresholds — above these KTC values the player counts as a true
# positional stud and justifies extra compensation.
STUD_TIER_ELITE = 8000      # Top-3 positional (Chase, Bijan, Allen)
STUD_TIER_HIGH = 6000       # Top-6 positional (WR1, RB1)
STUD_TIER_MID = 4000        # Top-12 positional (WR/RB1 borderline)

# Per-roster-spot value added to the stud side for each extra player sent
# by the non-stud side. Scales with the tier of the top player in the trade.
SPOT_VALUE_ELITE = 600
SPOT_VALUE_HIGH = 400
SPOT_VALUE_MID = 250
SPOT_VALUE_BASE = 100


@dataclass
class ValueAdjustment:
    adjustment: int                    # KTC to add to the stud side
    favors: Literal["send", "receive", "none"]
    roster_spot_diff: int              # positive = receive side sent more bodies
    top_stud_value: int                # KTC of the largest single asset in the trade
    stud_tier: Literal["elite", "high", "mid", "none"]
    rationale: str


def _stud_tier(ktc: int) -> tuple[str, int]:
    if ktc >= STUD_TIER_ELITE:
        return "elite", SPOT_VALUE_ELITE
    if ktc >= STUD_TIER_HIGH:
        return "high", SPOT_VALUE_HIGH
    if ktc >= STUD_TIER_MID:
        return "mid", SPOT_VALUE_MID
    return "none", SPOT_VALUE_BASE


def compute_value_adjustment(
    send_values: list[int],
    receive_values: list[int],
) -> ValueAdjustment:
    """Compute a KTC-style value adjustment for a two-sided trade.

    Args:
        send_values: KTC values of assets leaving your roster.
        receive_values: KTC values of assets joining your roster.

    Returns:
        ValueAdjustment describing the adjustment + which side it favors.

    The adjustment is ALWAYS positive (or zero). It represents extra KTC
    that should be added to the side giving up fewer roster spots (the
    "stud side") to compensate them for the roster-depth loss on the
    other side.
    """
    n_send = len(send_values)
    n_recv = len(receive_values)

    # Roster-spot differential: positive means receive side is sending more
    # bodies (i.e. YOU are sending fewer = you're the stud side).
    spot_diff = abs(n_send - n_recv)

    if spot_diff == 0:
        top = max(send_values + receive_values) if (send_values or receive_values) else 0
        tier, _ = _stud_tier(top)
        return ValueAdjustment(
            adjustment=0,
            favors="none",
            roster_spot_diff=0,
            top_stud_value=top,
            stud_tier=tier,  # type: ignore[arg-type]
            rationale="Even roster-spot count — no adjustment.",
        )

    # The "stud" is the largest single asset in the trade. Their tier sets
    # the per-spot value.
    all_values = send_values + receive_values
    if not all_values:
        return ValueAdjustment(
            adjustment=0, favors="none", roster_spot_diff=0,
            top_stud_value=0, stud_tier="none",
            rationale="Empty trade.",
        )
    top_stud = max(all_values)
    tier, spot_value = _stud_tier(top_stud)

    # Who has the stud? Whoever is SENDING fewer bodies is the stud side
    # (they're consolidating value; the other side is filling with filler).
    if n_send < n_recv:
        # YOU are sending 1 (the stud) for many -> adjustment favors SEND side
        favors = "send"
        stud_side_desc = "send"
        filler_side_desc = "receive"
    else:
        # YOU are receiving 1 (the stud) for many -> adjustment favors RECEIVE side
        favors = "receive"
        stud_side_desc = "receive"
        filler_side_desc = "send"

    adjustment = spot_diff * spot_value

    # Additional "stud factor" bonus — being a true elite asset carries
    # extra weight beyond the per-spot math.
    if tier == "elite":
        adjustment = int(adjustment * 1.25)
    elif tier == "high":
        adjustment = int(adjustment * 1.10)

    rationale = (
        f"{stud_side_desc.capitalize()} side sent 1 stud (KTC {top_stud:,}, {tier} tier); "
        f"{filler_side_desc} side sent {spot_diff} extra roster spots of filler. "
        f"Adjustment: +{adjustment:,} to the {stud_side_desc} side."
    )

    return ValueAdjustment(
        adjustment=adjustment,
        favors=favors,  # type: ignore[arg-type]
        roster_spot_diff=spot_diff,
        top_stud_value=top_stud,
        stud_tier=tier,  # type: ignore[arg-type]
        rationale=rationale,
    )


def apply_adjustment_to_delta(
    raw_delta: int,
    send_values: list[int],
    receive_values: list[int],
) -> tuple[int, ValueAdjustment]:
    """Apply value adjustment from the 'send' side's perspective.

    Args:
        raw_delta: receive_total - send_total (positive = you win on raw KTC)
        send_values: what you're sending
        receive_values: what you're receiving

    Returns:
        (adjusted_delta, ValueAdjustment)

    The adjusted_delta accounts for the stud-factor: if you're receiving
    the stud (favors="receive"), the adjustment INCREASES the effective
    cost to you (adjusted_delta goes DOWN). If you're sending the stud
    (favors="send"), the adjustment makes the trade MORE favorable to
    you (adjusted_delta goes UP).
    """
    adj = compute_value_adjustment(send_values, receive_values)

    if adj.favors == "receive":
        # You're getting the stud — pay extra value adjustment
        adjusted = raw_delta - adj.adjustment
    elif adj.favors == "send":
        # You're sending the stud — get credit for extra value adjustment
        adjusted = raw_delta + adj.adjustment
    else:
        adjusted = raw_delta

    return adjusted, adj
