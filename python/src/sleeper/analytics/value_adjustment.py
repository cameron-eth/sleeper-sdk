"""KTC-style Value Adjustment v2.

KeepTradeCut's stated principle (from their docs):

    "Trading is more than simple addition. We add value to the side of the
    trade that's giving up more when you look at roster spots, players'
    'stud' factor, etc. This is our way of countering — as much as
    possible — trade calculations that say 12 third round picks are a
    fair deal for DeAndre Hopkins. The actual adjustment is reverse
    engineered from the player the lesser side needs to have added to
    even the trade."

This module implements that principle as a pure function. It is not a
byte-for-byte reverse of KTC's proprietary formula (not public), but it
behaves the same way: lopsided-count + high-stud trades carry a tax that
tilts value toward the stud side, scaled by how diluted the filler is.

Four components combine into the adjustment:

1. **Spot premium** — per roster-spot the consolidating side compresses.
   Scales by the stud's tier (elite > high > mid > none).

2. **Tier scarcity premium** — a percentage of the target's KTC, recognizing
   that elite assets transact above face value because they're rare.

3. **Isolation gap** — sum of (target − chip) across ALL filler chips below
   the target, multiplied by tier coefficient. Captures "every chip below
   the stud compounds the quality compression," not just the top one.
   (v1 only looked at the top filler chip and missed this completely.)

4. **Dilution penalty** — punishes stacking many low-value chips to reach
   the target. The market discounts filler-stacking because roster slots
   are valuable, stud production is multiplicative, and multi-chip trades
   carry execution risk. Quantifies the "10 third-round picks ≠ DeAndre
   Hopkins" principle by penalizing the gap between AVG chip value and
   target value, compounded by chip count.

    dilution = target × (1 − avg/target)^1.5 × (n − 1) × DILUTION_MULT

The 1.5 exponent makes the penalty curve steep at the extremes (low avg
ratio = severe punishment) but mild for healthy 2-for-1 packages where
each chip is meaningful on its own.

Usage:
    adj = compute_value_adjustment(
        send_values=[5200, 3100, 1400],
        receive_values=[9800],
    )
    adjusted_delta = raw_delta - adj.adjustment  # if you're the stud receiver

A note on the "missing piece" idea: KTC reverse-engineers the adjustment
from the player needed to even the trade. We expose `suggest_evening_piece`
that maps an adjustment amount back to a human-readable description (≈ a
2027 Mid 1st, ≈ a low-end RB1, etc.) so the same intuition surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# ---------------------------------------------------------------------------
# Stud-factor thresholds
# ---------------------------------------------------------------------------

STUD_TIER_ELITE = 8000      # Top-3 positional (Chase, Bijan, Allen)
STUD_TIER_HIGH = 6000       # Top-6 positional (WR1, RB1)
STUD_TIER_MID = 4000        # Top-12 positional (WR/RB1 borderline)


# ---------------------------------------------------------------------------
# Per-roster-spot premium
# ---------------------------------------------------------------------------

SPOT_VALUE_ELITE = 1500
SPOT_VALUE_HIGH = 1000
SPOT_VALUE_MID = 600
SPOT_VALUE_BASE = 200


# ---------------------------------------------------------------------------
# Tier scarcity premium — % of target KTC (the "they're rare" tax)
# ---------------------------------------------------------------------------

TIER_PREMIUM_ELITE = 0.30
TIER_PREMIUM_HIGH = 0.18
TIER_PREMIUM_MID = 0.08


# ---------------------------------------------------------------------------
# Isolation gap — sum over ALL filler chips of (target − chip), × multiplier.
# v1's quality_gap only looked at the top filler chip; v2 sums every chip
# that's below the target so a 5-filler package compounds, not just the best.
# ---------------------------------------------------------------------------

ISOLATION_GAP_MULT_ELITE = 0.25
ISOLATION_GAP_MULT_HIGH = 0.18
ISOLATION_GAP_MULT_MID = 0.08


# ---------------------------------------------------------------------------
# Tier dispatch tables — single source of truth so all four components scale
# consistently. Replaces three if/elif chains scattered through the module.
# ---------------------------------------------------------------------------

TIER_PREMIUM_MAP: dict[str, float] = {
    "elite": TIER_PREMIUM_ELITE,
    "high":  TIER_PREMIUM_HIGH,
    "mid":   TIER_PREMIUM_MID,
    "none":  0.0,
}

ISOLATION_GAP_MAP: dict[str, float] = {
    "elite": ISOLATION_GAP_MULT_ELITE,
    "high":  ISOLATION_GAP_MULT_HIGH,
    "mid":   ISOLATION_GAP_MULT_MID,
    "none":  0.0,
}

SPOT_MULTIPLIER_MAP: dict[str, float] = {
    "elite": 1.25,
    "high":  1.10,
    "mid":   1.00,
    "none":  1.00,
}


# ---------------------------------------------------------------------------
# Dilution penalty — the "10 shit players ≠ 1 elite" tax.
# Scales with chip count AND how low-quality the average chip is.
# ---------------------------------------------------------------------------

DILUTION_MULT = 0.20
DILUTION_EXPONENT = 1.5   # curve steepness; > 1 means low avg ratios hurt more


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class ValueAdjustment:
    adjustment: int                        # KTC owed to the stud side
    favors: Literal["send", "receive", "none"]
    roster_spot_diff: int                  # positive = receive side sent more bodies
    top_stud_value: int                    # KTC of the largest single asset in the trade
    stud_tier: Literal["elite", "high", "mid", "none"]
    spot_component: int = 0                # per-spot premium contribution
    tier_premium: int = 0                  # scarcity premium contribution
    isolation_gap: int = 0                 # sum-of-gaps contribution
    dilution: int = 0                      # avg-value-per-asset tax
    rationale: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _stud_tier(ktc: int) -> tuple[str, int]:
    """Return (tier_name, per_spot_value) for a player's KTC."""
    if ktc >= STUD_TIER_ELITE:
        return "elite", SPOT_VALUE_ELITE
    if ktc >= STUD_TIER_HIGH:
        return "high", SPOT_VALUE_HIGH
    if ktc >= STUD_TIER_MID:
        return "mid", SPOT_VALUE_MID
    return "none", SPOT_VALUE_BASE


def _tier_premium(target_ktc: int, tier: str) -> int:
    """Scarcity premium — % of target KTC by tier."""
    return int(target_ktc * TIER_PREMIUM_MAP.get(tier, 0.0))


def _isolation_gap(filler_values: list[int], target_ktc: int, tier: str) -> int:
    """Sum of (target − chip) for every filler chip BELOW the target.

    v2 change: this used to only consider the top filler chip
    (`max(filler_values)`). A 4K + 4K + 4K package for a 10K stud only got
    penalized for the 6K gap on the top chip — the other two chips were
    "free." Now every below-target chip's gap contributes, which is what
    captures the "many small chips compound the quality compression"
    intuition.
    """
    if target_ktc <= 0 or not filler_values:
        return 0
    total_gap = sum(max(0, target_ktc - v) for v in filler_values)
    return int(total_gap * ISOLATION_GAP_MAP.get(tier, 0.0))


def _dilution_penalty(filler_values: list[int], target_ktc: int) -> int:
    """The "10 shit players ≠ 1 elite player" tax.

    Punishes stacking many low-value chips to reach a target. Driven by:

      avg_ratio = avg(filler) / target_ktc       # how good is the avg chip?
      shortfall = max(0, 1 − avg_ratio)          # how far from target on avg?
      penalty   = target × shortfall^1.5 × (n − 1) × DILUTION_MULT

    Properties:
      - 1-for-1: returns 0 (n − 1 = 0)
      - avg ≥ target: returns 0 (shortfall = 0)
      - avg ≈ target / 2 with 2 chips: small penalty
      - avg ≈ target / 10 with 10 chips: large penalty (the canonical case)

    The 1.5 exponent gives a steep curve: severe punishment for genuinely
    diluted packages, mild for balanced 2-for-1s where each chip is real.
    """
    if target_ktc <= 0 or not filler_values:
        return 0
    n = len(filler_values)
    if n <= 1:
        return 0
    avg = sum(filler_values) / n
    if avg >= target_ktc:
        return 0
    shortfall = 1.0 - (avg / target_ktc)
    curve = shortfall ** DILUTION_EXPONENT
    return int(target_ktc * curve * (n - 1) * DILUTION_MULT)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_value_adjustment(
    send_values: list[int],
    receive_values: list[int],
) -> ValueAdjustment:
    """Compute a KTC-style value adjustment for a two-sided trade.

    Args:
        send_values: KTC values of assets leaving your roster.
        receive_values: KTC values of assets joining your roster.

    Returns:
        ValueAdjustment with the total + per-component breakdown.

    The adjustment is ALWAYS non-negative. It represents extra KTC the
    side acquiring the concentrated value (the "stud side") owes ON TOP
    of face value to fairly land the deal.
    """
    n_send = len(send_values)
    n_recv = len(receive_values)
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

    all_values = send_values + receive_values
    if not all_values:
        return ValueAdjustment(
            adjustment=0, favors="none", roster_spot_diff=0,
            top_stud_value=0, stud_tier="none",
            rationale="Empty trade.",
        )

    top_stud = max(all_values)
    tier, spot_value = _stud_tier(top_stud)

    # The "stud side" is whoever sends FEWER bodies (consolidates value).
    if n_send < n_recv:
        favors = "send"
        stud_side_desc = "send"
        filler_side_desc = "receive"
        filler_values = receive_values
    else:
        favors = "receive"
        stud_side_desc = "receive"
        filler_side_desc = "send"
        filler_values = send_values

    target_ktc = max(receive_values) if favors == "receive" else max(send_values)

    # 1. Spot premium — per roster-spot, scaled by tier
    spot_component = int(spot_diff * spot_value * SPOT_MULTIPLIER_MAP.get(tier, 1.0))

    # 2. Tier scarcity premium
    tier_prem = _tier_premium(target_ktc, tier)

    # 3. Isolation gap (sum of all filler chips below target × tier multiplier)
    isolation = _isolation_gap(filler_values, target_ktc, tier)

    # 4. Dilution penalty (the "many small chips ≠ one stud" tax)
    dilution = _dilution_penalty(filler_values, target_ktc)

    adjustment = spot_component + tier_prem + isolation + dilution

    rationale = (
        f"{stud_side_desc.capitalize()} side has the stud (KTC {top_stud:,}, "
        f"{tier} tier); {filler_side_desc} side compresses {spot_diff} extra "
        f"roster spot(s) of filler. "
        f"Spot: +{spot_component:,}, tier premium: +{tier_prem:,}, "
        f"isolation gap: +{isolation:,}, dilution tax: +{dilution:,}. "
        f"Total adjustment: +{adjustment:,} to the {stud_side_desc} side."
    )

    return ValueAdjustment(
        adjustment=adjustment,
        favors=favors,  # type: ignore[arg-type]
        roster_spot_diff=spot_diff,
        top_stud_value=top_stud,
        stud_tier=tier,  # type: ignore[arg-type]
        spot_component=spot_component,
        tier_premium=tier_prem,
        isolation_gap=isolation,
        dilution=dilution,
        rationale=rationale,
    )


def apply_adjustment_to_delta(
    raw_delta: int,
    send_values: list[int],
    receive_values: list[int],
) -> tuple[int, ValueAdjustment]:
    """Apply the value adjustment from the user's perspective.

    Args:
        raw_delta: receive_total − send_total (positive = user "wins" on raw KTC)
        send_values: what the user is sending
        receive_values: what the user is receiving

    Returns:
        (adjusted_delta, ValueAdjustment)

    If the user is RECEIVING the stud (favors="receive"), the adjustment
    is owed ON TOP of face value — subtract from the delta (worse for you).
    If the user is SENDING the stud (favors="send"), the partner owes the
    premium — add to the delta (better for you).
    """
    adj = compute_value_adjustment(send_values, receive_values)

    if adj.favors == "receive":
        adjusted = raw_delta - adj.adjustment
    elif adj.favors == "send":
        adjusted = raw_delta + adj.adjustment
    else:
        adjusted = raw_delta

    return adjusted, adj


# ---------------------------------------------------------------------------
# "Missing piece" — reverse-engineer KTC's hint: "what would even the trade?"
# ---------------------------------------------------------------------------

# Rough KTC tiers for picks and player asset bands — used to translate an
# adjustment amount back into a human-readable "what would balance this?"
# string. Values are intentionally fuzzy bands; the labels are the point.
_PIECE_BANDS: tuple[tuple[int, str], ...] = (
    (    250, "a throw-in (rookie taxi piece)"),
    (    750, "a deep bench flier"),
    (   1500, "a late 2nd-round rookie pick"),
    (   2200, "a 2027 2nd"),
    (   3000, "a 2026 2nd"),
    (   3800, "a 2027 Mid 1st"),
    (   4800, "a 2026 Mid 1st"),
    (   6000, "a low-end WR2 / RB2"),
    (   7500, "a high-end WR2 / RB2"),
    (   9500, "a fringe WR1 / RB1"),
    (  12000, "an elite WR1 / RB1"),
)


def suggest_evening_piece(adjustment: int) -> str:
    """Translate an adjustment amount into a human-readable "missing piece".

    Inspired by KTC's framing — the adjustment is the value the lesser
    side would need to add to even the trade. Mapping that number back to
    a recognizable asset ("≈ a 2027 Mid 1st") makes the math actionable
    in a DM.

    Returns a short phrase suitable for inline rendering:

        >>> suggest_evening_piece(4800)
        '≈ a 2026 Mid 1st'
        >>> suggest_evening_piece(150)
        '≈ a throw-in'
        >>> suggest_evening_piece(15000)
        '≈ a stud-tier asset (12K+ KTC)'
    """
    if adjustment <= 0:
        return "≈ already balanced"
    for ceiling, label in _PIECE_BANDS:
        if adjustment <= ceiling:
            return f"≈ {label}"
    return "≈ a stud-tier asset (12K+ KTC)"
