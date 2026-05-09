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
# Tuned 2026-05: prior values (600/400/250) systematically undervalued elite-tier
# consolidation by ~3x relative to actual market behavior. Real consolidation
# trades for top-12 WR/RB require 2-3K KTC of premium, not 600-750.
SPOT_VALUE_ELITE = 1500
SPOT_VALUE_HIGH = 1000
SPOT_VALUE_MID = 600
SPOT_VALUE_BASE = 200

# Tier premium — a percentage of the target's KTC added when the receive side
# acquires an elite/high-tier asset. Captures the scarcity premium that elite
# young WR1/RB1 command above raw KTC face value.
TIER_PREMIUM_ELITE = 0.30   # 30% of target KTC for elite (8000+)
TIER_PREMIUM_HIGH = 0.18    # 18% of target KTC for high (6000-7999)
TIER_PREMIUM_MID = 0.08     # 8% for mid (4000-5999)

# Quality-gap penalty multiplier — when the best single asset on the send side
# is materially smaller than the target, an additional premium is owed for the
# quality compression. Multiplies (target - top_send) by this factor.
QUALITY_GAP_MULT_ELITE = 0.40
QUALITY_GAP_MULT_HIGH = 0.25
QUALITY_GAP_MULT_MID = 0.10


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


def _tier_premium(target_ktc: int, tier: str) -> int:
    """Scarcity premium owed to the side acquiring an elite/high tier asset.

    Elite young WR1/RB1 transact at 15-30% above raw KTC face value because
    they're rare and consolidating. Captured here as a flat % of target KTC.
    """
    if tier == "elite":
        return int(target_ktc * TIER_PREMIUM_ELITE)
    if tier == "high":
        return int(target_ktc * TIER_PREMIUM_HIGH)
    if tier == "mid":
        return int(target_ktc * TIER_PREMIUM_MID)
    return 0


def _quality_gap_penalty(top_send: int, top_recv: int, tier: str) -> int:
    """Premium owed when the best single chip is materially below the target.

    A 4,888 chip going for a 9,910 target leaves a 5,022 quality gap — the
    receive side is concentrating value the send side can't match in any
    single asset. Penalty scales with gap size and target tier.
    """
    if top_recv <= 0 or top_send >= top_recv:
        return 0
    gap = top_recv - top_send
    if tier == "elite":
        return int(gap * QUALITY_GAP_MULT_ELITE)
    if tier == "high":
        return int(gap * QUALITY_GAP_MULT_HIGH)
    if tier == "mid":
        return int(gap * QUALITY_GAP_MULT_MID)
    return 0


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

    spot_component = spot_diff * spot_value

    # Additional "stud factor" bonus — being a true elite asset carries
    # extra weight beyond the per-spot math.
    if tier == "elite":
        spot_component = int(spot_component * 1.25)
    elif tier == "high":
        spot_component = int(spot_component * 1.10)

    # Tier scarcity premium — % of target KTC. Only applies when the stud
    # is on the receive side (i.e. someone is acquiring concentrated value).
    # Symmetric: also applies when stud is on send side (someone consolidates
    # to land them — roles reversed but the premium is the same magnitude).
    target_ktc = max(receive_values) if favors == "receive" else max(send_values)
    tier_prem = _tier_premium(target_ktc, tier)

    # Quality-gap penalty — how far below the target is the best single chip
    # on the filler side?
    if favors == "receive":
        # Send side is the filler side
        top_filler = max(send_values) if send_values else 0
        top_target = max(receive_values) if receive_values else 0
    else:
        # Receive side is the filler side
        top_filler = max(receive_values) if receive_values else 0
        top_target = max(send_values) if send_values else 0
    quality_gap = _quality_gap_penalty(top_filler, top_target, tier)

    adjustment = spot_component + tier_prem + quality_gap

    rationale = (
        f"{stud_side_desc.capitalize()} side sent 1 stud (KTC {top_stud:,}, {tier} tier); "
        f"{filler_side_desc} side sent {spot_diff} extra roster spots of filler. "
        f"Spot: +{spot_component:,}, tier premium: +{tier_prem:,}, quality gap: +{quality_gap:,}. "
        f"Total adjustment: +{adjustment:,} to the {stud_side_desc} side."
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
