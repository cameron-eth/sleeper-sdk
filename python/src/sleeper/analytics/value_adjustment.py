"""KTC-style Value Adjustment v3 — single, bounded concentration premium.

KeepTradeCut's stated principle (from their docs):

    "Trading is more than simple addition. We add value to the side of the
    trade that's giving up more when you look at roster spots, players'
    'stud' factor, etc. This is our way of countering — as much as
    possible — trade calculations that say 12 third round picks are a
    fair deal for DeAndre Hopkins. The actual adjustment is reverse
    engineered from the player the lesser side needs to have added to
    even the trade."

v3 rewrite (this file) fixes four ways the v2 model diverged from that
definition — all confirmed with numeric probes:

1. **Overshoot.** v2 was a forward *sum* of four independent penalties
   (spot + tier% + isolation + dilution). For "6 mid picks for one 9,000
   elite" it produced an adjustment of 30,171 — 3.3x the stud's own value.
   KTC's adjustment is "the piece needed to even the trade," a bounded
   quantity. v3 is a single premium, hard-capped at `CAP_RATE * stud`.

2. **Double-counting.** v2's `isolation_gap` and `dilution` both taxed
   filler-below-target; `spot` and `tier_premium` both scaled with the
   stud tier. v3 has ONE scarcity premium modulated by ONE dilution factor.

3. **Tier cliffs.** v2 snapped at hard 4,000 / 6,000 / 8,000 thresholds, so
   a target moving 5,999 → 6,001 nearly doubled the adjustment. KTC values
   come from a smooth ELO curve; v3's stud factor is a continuous
   smoothstep, so a 2-point move changes the premium by ~3, not ~1,700.

4. **Wrong stud reference.** v2 read the tier from `max(all_values)` — the
   biggest asset anywhere in the trade, even one you're giving away. v3
   keys the premium off the concentrated asset on the *fewer-bodies* side
   (the stud actually being consolidated toward).

The model
---------
The premium applies only to a *consolidation* — one side packages more
bodies than the other to acquire a concentrated stud. (Equal body counts
get no adjustment; a 1-for-1 is a like-for-like swap, not a consolidation.
This mirrors KTC's dominant behavior and avoids spurious premiums on even
swaps.)

    stud          = top asset on the fewer-bodies side (what's consolidated)
    filler        = the many-bodies side's chips
    scarcity_rate = STUD_MAX_RATE * smoothstep(stud; FLOOR..CEIL)   # 0..0.30
    base          = stud * scarcity_rate                           # scarcity premium
    dispersion    = mean((stud - v)/stud) over filler chips below stud   # 0..1
    count_weight  = min(1, (n_filler - 1) / SAT_COUNT)             # saturating
    dilution_mult = 1 + DILUTION_GAIN * dispersion * count_weight  # >= 1
    premium       = min(base * dilution_mult, CAP_RATE * stud)     # bounded

`base` is the "studs are scarce, they transact above face" premium. The
dilution factor grows it when the filler is many small chips far below the
stud (the DeAndre-Hopkins case) but saturates on count and is capped
overall, so it can never dwarf the stud it's pricing.

Usage:
    adj = compute_value_adjustment(
        send_values=[5200, 3100, 1400],
        receive_values=[9800],
    )
    adjusted_delta = raw_delta - adj.adjustment  # if you're the stud receiver

`suggest_evening_piece` maps an adjustment back to a human-readable asset
("≈ a 2027 Mid 1st"), surfacing KTC's "what would even the trade?" framing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# ---------------------------------------------------------------------------
# Smooth stud-scarcity curve (replaces v2's hard tier thresholds)
# ---------------------------------------------------------------------------

STUD_FLOOR = 3000       # below this KTC, ~no scarcity premium (not a stud)
STUD_CEIL = 9000        # at/above this, full scarcity rate
STUD_MAX_RATE = 0.30    # max premium as a fraction of the stud's KTC


# ---------------------------------------------------------------------------
# Dilution factor — the "many small chips ≠ one stud" amplifier
# ---------------------------------------------------------------------------

SAT_COUNT = 4           # extra filler bodies at which count weight saturates
DILUTION_GAIN = 1.6     # how hard dispersion × count amplifies the base premium


# ---------------------------------------------------------------------------
# Global bound — the premium can never exceed this fraction of the stud.
# This is what keeps the adjustment "the piece needed to even it" rather
# than an unbounded stack of penalties.
# ---------------------------------------------------------------------------

CAP_RATE = 0.60


# ---------------------------------------------------------------------------
# Display-only tier labels. NOT used in the math (the math is smooth); these
# just give callers a word to print ("elite tier stud"). Keeping them purely
# cosmetic is deliberate — it's why the tier cliffs no longer affect values.
# ---------------------------------------------------------------------------

TIER_ELITE = 8000
TIER_HIGH = 6000
TIER_MID = 4000


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class ValueAdjustment:
    adjustment: int                        # KTC premium owed to the stud side
    favors: Literal["send", "receive", "none"]
    roster_spot_diff: int                  # |bodies sent − bodies received|
    top_stud_value: int                    # KTC of the consolidated stud
    stud_tier: Literal["elite", "high", "mid", "none"]  # display label only
    scarcity_rate: float = 0.0             # smooth premium rate applied to stud
    base_premium: int = 0                  # stud × scarcity_rate (pre-dilution)
    dilution_mult: float = 1.0             # dispersion × count amplifier (≥ 1)
    capped: bool = False                   # True if the CAP_RATE bound bit
    rationale: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _smoothstep(x0: float, x1: float, x: float) -> float:
    """Classic smoothstep: 0 below x0, 1 above x1, smooth S-curve between.

    Continuous with zero first-derivative at both ends — no kinks, which is
    what kills the v2 tier-cliff artifact.
    """
    if x1 <= x0:
        return 1.0 if x >= x1 else 0.0
    t = (x - x0) / (x1 - x0)
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _scarcity_rate(stud_ktc: int) -> float:
    """Smooth premium rate in [0, STUD_MAX_RATE] as a function of stud KTC."""
    return STUD_MAX_RATE * _smoothstep(STUD_FLOOR, STUD_CEIL, float(stud_ktc))


def _tier_label(stud_ktc: int) -> str:
    """Cosmetic tier word for display. Does not enter the math."""
    if stud_ktc >= TIER_ELITE:
        return "elite"
    if stud_ktc >= TIER_HIGH:
        return "high"
    if stud_ktc >= TIER_MID:
        return "mid"
    return "none"


def _dilution_mult(filler_values: list[int], stud_ktc: int) -> tuple[float, float, float]:
    """Return (dilution_mult, dispersion, count_weight).

    dispersion  = mean fractional gap below the stud across filler chips
                  that are below it (0 = all filler ≈ stud, 1 = filler ≈ 0)
    count_weight= min(1, (n_filler − 1) / SAT_COUNT), saturating on body count
    mult        = 1 + DILUTION_GAIN · dispersion · count_weight   (≥ 1)
    """
    n = len(filler_values)
    if n <= 1 or stud_ktc <= 0:
        return 1.0, 0.0, 0.0
    below = [v for v in filler_values if v < stud_ktc]
    dispersion = (
        sum((stud_ktc - v) / stud_ktc for v in below) / len(below)
        if below else 0.0
    )
    count_weight = min(1.0, (n - 1) / SAT_COUNT)
    mult = 1.0 + DILUTION_GAIN * dispersion * count_weight
    return mult, dispersion, count_weight


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
        ValueAdjustment with the total premium + a transparent breakdown.

    The adjustment is ALWAYS non-negative and bounded by ``CAP_RATE`` of the
    consolidated stud's value. It represents the extra KTC the side
    acquiring the concentrated stud owes ON TOP of face value to fairly land
    the deal — i.e. KTC's "piece needed to even the trade."
    """
    n_send = len(send_values)
    n_recv = len(receive_values)
    spot_diff = abs(n_send - n_recv)

    all_values = send_values + receive_values
    if not all_values:
        return ValueAdjustment(
            adjustment=0, favors="none", roster_spot_diff=0,
            top_stud_value=0, stud_tier="none", rationale="Empty trade.",
        )

    # No consolidation (equal body counts) → no premium. A 1-for-1 or N-for-N
    # is a like-for-like swap; KTC's adjustment targets packaging asymmetry.
    if spot_diff == 0:
        top = max(all_values)
        return ValueAdjustment(
            adjustment=0,
            favors="none",
            roster_spot_diff=0,
            top_stud_value=top,
            stud_tier=_tier_label(top),  # type: ignore[arg-type]
            rationale="Even roster-spot count — no consolidation premium.",
        )

    # The stud side consolidates value into FEWER bodies. The filler side is
    # the many-bodies side. `favors` is from the user's ("send") perspective.
    if n_send < n_recv:
        favors: Literal["send", "receive"] = "send"
        stud_side, filler_values = send_values, receive_values
    else:
        favors = "receive"
        stud_side, filler_values = receive_values, send_values

    stud = max(stud_side)
    rate = _scarcity_rate(stud)
    base = stud * rate
    mult, dispersion, count_weight = _dilution_mult(filler_values, stud)

    raw_premium = base * mult
    cap = CAP_RATE * stud
    capped = raw_premium > cap
    premium = int(round(min(raw_premium, cap)))

    tier = _tier_label(stud)
    stud_side_desc = "send" if favors == "send" else "receive"
    filler_side_desc = "receive" if favors == "send" else "send"
    rationale = (
        f"{stud_side_desc.capitalize()} side consolidates a {tier}-tier stud "
        f"(KTC {stud:,}); {filler_side_desc} side packages {len(filler_values)} "
        f"bodies ({spot_diff} extra spot(s)). "
        f"Scarcity {rate*100:.0f}% → base +{int(round(base)):,}, "
        f"dilution ×{mult:.2f} (dispersion {dispersion*100:.0f}%). "
        f"Premium +{premium:,}{' (capped)' if capped else ''} to the "
        f"{stud_side_desc} side."
    )

    return ValueAdjustment(
        adjustment=premium,
        favors=favors,
        roster_spot_diff=spot_diff,
        top_stud_value=stud,
        stud_tier=tier,  # type: ignore[arg-type]
        scarcity_rate=rate,
        base_premium=int(round(base)),
        dilution_mult=mult,
        capped=capped,
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

    This works in NET-VALUE space (positive = you come out ahead), which is
    the mirror of the overpay space used by `find_trades_engine`. The premium
    says the concentrated stud is worth MORE than its face KTC, so:

      * Stud on your RECEIVE side  -> you acquired something worth above face
        -> ``raw_delta + adjustment``.
      * Stud on your SEND side     -> you gave away something worth above face
        -> ``raw_delta - adjustment``.

    Worked example (KTC's own): you trade Hopkins for 12 third-rounders whose
    face values sum to exactly Hopkins'. ``raw_delta`` is 0, but Hopkins is
    genuinely worth more than that pile, so the deal is bad for you by the
    premium — hence minus when the stud leaves your roster.

    NOTE: these signs were inverted before 2026-07, which made "give up the
    stud, take back two lesser pieces" score as a *gain*. It surfaced as
    trades like McMillan (6,392) for Mayfield + Green (5,871) being reported
    as +872 in your favor when it is really about -1,900.
    """
    adj = compute_value_adjustment(send_values, receive_values)

    if adj.favors == "receive":
        adjusted = raw_delta + adj.adjustment
    elif adj.favors == "send":
        adjusted = raw_delta - adj.adjustment
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
        '≈ a throw-in (rookie taxi piece)'
        >>> suggest_evening_piece(15000)
        '≈ a stud-tier asset (12K+ KTC)'
    """
    if adjustment <= 0:
        return "≈ already balanced"
    for ceiling, label in _PIECE_BANDS:
        if adjustment <= ceiling:
            return f"≈ {label}"
    return "≈ a stud-tier asset (12K+ KTC)"
