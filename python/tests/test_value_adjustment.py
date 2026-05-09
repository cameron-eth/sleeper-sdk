"""Tests for analytics.value_adjustment.

Coverage:
- Empty / degenerate cases
- Even-spot trades produce zero adjustment
- Tier classification (elite / high / mid / none)
- Spot premium scaling with tier
- Tier scarcity premium (% of target KTC)
- Quality-gap penalty when top send chip is far below target
- Sign correctness for `apply_adjustment_to_delta`
- Regression tests for the May 2026 tuning + sign fix
"""
from __future__ import annotations

import pytest

from sleeper.analytics.value_adjustment import (
    QUALITY_GAP_MULT_ELITE,
    SPOT_VALUE_BASE,
    SPOT_VALUE_ELITE,
    STUD_TIER_ELITE,
    STUD_TIER_HIGH,
    STUD_TIER_MID,
    TIER_PREMIUM_ELITE,
    TIER_PREMIUM_HIGH,
    TIER_PREMIUM_MID,
    apply_adjustment_to_delta,
    compute_value_adjustment,
)


# ---------------------------------------------------------------------------
# Degenerate cases
# ---------------------------------------------------------------------------

def test_empty_trade_returns_zero_adjustment():
    adj = compute_value_adjustment(send_values=[], receive_values=[])
    assert adj.adjustment == 0
    assert adj.favors == "none"
    assert adj.roster_spot_diff == 0


def test_even_spot_count_returns_zero_adjustment():
    """1-for-1 trades carry no roster-spot adjustment regardless of tier."""
    adj = compute_value_adjustment(send_values=[6157], receive_values=[9985])
    assert adj.adjustment == 0
    assert adj.favors == "none"
    assert adj.roster_spot_diff == 0


def test_2_for_2_returns_zero_adjustment():
    adj = compute_value_adjustment(send_values=[5000, 4000], receive_values=[7000, 2000])
    assert adj.adjustment == 0
    assert adj.favors == "none"


# ---------------------------------------------------------------------------
# Tier classification — verifies the boundary thresholds match the constants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ktc,expected_tier", [
    (STUD_TIER_ELITE,     "elite"),
    (STUD_TIER_ELITE - 1, "high"),
    (STUD_TIER_HIGH,      "high"),
    (STUD_TIER_HIGH - 1,  "mid"),
    (STUD_TIER_MID,       "mid"),
    (STUD_TIER_MID - 1,   "none"),
    (0,                   "none"),
])
def test_tier_classification(ktc, expected_tier):
    """Top stud's KTC determines the tier; verify each threshold."""
    # Provide an even trade so adjustment is 0 but tier is reported.
    adj = compute_value_adjustment(send_values=[ktc], receive_values=[1])
    assert adj.stud_tier == expected_tier


# ---------------------------------------------------------------------------
# Spot premium component
# ---------------------------------------------------------------------------

def test_2_for_1_with_elite_stud_uses_elite_spot_value():
    """The receive side gets a 2-for-1 elite consolidation."""
    adj = compute_value_adjustment(
        send_values=[10000],
        receive_values=[3000, 3000],
    )
    # favors=send because send has 1 chip = the stud side
    assert adj.favors == "send"
    assert adj.roster_spot_diff == 1
    # spot premium = SPOT_VALUE_ELITE * 1.25 (elite multiplier)
    expected_spot = int(SPOT_VALUE_ELITE * 1 * 1.25)
    # Plus tier premium and (possibly) quality gap on top
    assert adj.adjustment >= expected_spot


def test_2_for_1_with_no_stud_uses_base_spot_value():
    """When no chip is in any tier, spot value is the BASE constant only."""
    adj = compute_value_adjustment(
        send_values=[2000],
        receive_values=[1000, 800],
    )
    assert adj.stud_tier == "none"
    # Should use SPOT_VALUE_BASE without tier scaling
    assert adj.adjustment == SPOT_VALUE_BASE


# ---------------------------------------------------------------------------
# Tier scarcity premium
# ---------------------------------------------------------------------------

def test_elite_target_carries_30pct_tier_premium():
    """Receiving an elite (8000+) stud adds 30% of target KTC as tier premium."""
    target = 10000
    adj = compute_value_adjustment(
        send_values=[3000, 3000],   # filler side
        receive_values=[target],
    )
    assert adj.favors == "receive"
    assert adj.stud_tier == "elite"
    # adjustment should include >= 30% of target as tier premium component
    expected_tier_prem = int(target * TIER_PREMIUM_ELITE)
    assert adj.adjustment >= expected_tier_prem


def test_high_target_carries_18pct_tier_premium():
    target = 7000  # high tier
    adj = compute_value_adjustment(
        send_values=[3000, 2500],
        receive_values=[target],
    )
    assert adj.stud_tier == "high"
    expected_tier_prem = int(target * TIER_PREMIUM_HIGH)
    assert adj.adjustment >= expected_tier_prem


def test_mid_target_carries_8pct_tier_premium():
    target = 5000  # mid tier
    adj = compute_value_adjustment(
        send_values=[2000, 1500],
        receive_values=[target],
    )
    assert adj.stud_tier == "mid"
    expected_tier_prem = int(target * TIER_PREMIUM_MID)
    assert adj.adjustment >= expected_tier_prem


# ---------------------------------------------------------------------------
# Quality-gap penalty
# ---------------------------------------------------------------------------

def test_quality_gap_penalty_for_elite_target_with_weak_filler():
    """When best send chip is far below an elite target, gap penalty applies."""
    target = 10000
    weak_top = 2000   # large gap
    decent_top = 8000   # small gap
    adj_weak = compute_value_adjustment(
        send_values=[weak_top, 1500],
        receive_values=[target],
    )
    adj_decent = compute_value_adjustment(
        send_values=[decent_top, 1500],
        receive_values=[target],
    )
    # Bigger gap => bigger adjustment
    assert adj_weak.adjustment > adj_decent.adjustment


def test_quality_gap_zero_when_top_send_meets_target():
    """No gap penalty when send side already has a chip ≥ target KTC."""
    adj = compute_value_adjustment(
        send_values=[10000, 1000],
        receive_values=[8500],
    )
    # Top send (10000) >= target (8500) -> gap penalty is 0
    # adjustment should equal spot premium + tier premium (no gap)
    spot = int(SPOT_VALUE_ELITE * 1 * 1.25)
    tier_prem = int(8500 * TIER_PREMIUM_ELITE)
    expected = spot + tier_prem
    assert adj.adjustment == expected


# ---------------------------------------------------------------------------
# apply_adjustment_to_delta — sign correctness
# ---------------------------------------------------------------------------

def test_apply_adjustment_receive_stud_lowers_adjusted_delta():
    """If you receive the stud, the adjustment makes the trade WORSE for you."""
    raw_delta = 1000   # you "win" 1000 on raw
    adjusted, adj = apply_adjustment_to_delta(
        raw_delta=raw_delta,
        send_values=[3000, 2500],
        receive_values=[8500],
    )
    assert adj.favors == "receive"
    assert adjusted < raw_delta
    assert adjusted == raw_delta - adj.adjustment


def test_apply_adjustment_send_stud_raises_adjusted_delta():
    """If you send the stud, the adjustment makes the trade BETTER for you."""
    raw_delta = -1000   # you "lose" 1000 on raw
    adjusted, adj = apply_adjustment_to_delta(
        raw_delta=raw_delta,
        send_values=[8500],
        receive_values=[3000, 2500],
    )
    assert adj.favors == "send"
    assert adjusted > raw_delta
    assert adjusted == raw_delta + adj.adjustment


def test_apply_adjustment_even_spots_returns_raw_delta():
    raw_delta = 500
    adjusted, adj = apply_adjustment_to_delta(
        raw_delta=raw_delta,
        send_values=[5000],
        receive_values=[5500],
    )
    assert adj.favors == "none"
    assert adjusted == raw_delta


# ---------------------------------------------------------------------------
# Regression tests for the May 2026 tuning fix
# ---------------------------------------------------------------------------

def test_regression_fannin_mayfield_for_jsn_needs_substantial_extra_value():
    """User intuition: Fannin + Mayfield-discount → JSN should need a 1st+2nd.

    Combined effect: the QB age discount in find-trades shaves ~2,120 KTC
    off Mayfield's face (4,709 -> 2,589). On top of that, this test checks
    that compute_value_adjustment alone reports a deficit > one 1st pick
    (~5K) — i.e. you owe at LEAST a first to land JSN, on top of the
    aging-QB discount.

    Combined "true gap" Cam owes vs. face KTC ≈ Mayfield discount + |adj
    overpay| ≈ 2,120 + 4,400 ≈ 6,500 KTC ≈ 2027 1st + 2027 2nd.
    """
    fannin = 4881
    mayfield_discounted = 2589   # 4709 * 0.55 (age 31)
    jsn = 9917

    raw_delta = jsn - (fannin + mayfield_discounted)   # = 2447 (positive: cam wins raw)
    adjusted, adj = apply_adjustment_to_delta(
        raw_delta=raw_delta,
        send_values=[fannin, mayfield_discounted],
        receive_values=[jsn],
    )
    assert adj.favors == "receive"
    # After stud premium, cam owes a meaningful chunk of additional value —
    # at least the value of one rookie 2027 1st (~4K), but not so much that
    # it's wildly out of band (capped at ~7K to catch over-tuning regressions).
    assert -7000 <= adjusted <= -3500, (
        f"Expected adjusted delta in [-7000, -3500] (≈1st owed on top of the "
        f"implicit Mayfield discount), got {adjusted}"
    )


def test_regression_scrub_package_for_elite_stud_is_not_fair():
    """Sending only weak filler for an elite stud must NOT score as fair.

    Pre-tuning bug: the algorithm would credit a large stud premium that
    offset the raw deficit, making 2K-of-scrub-for-10K-elite score like
    a fair small overpay. After the May 2026 fix, the adjusted delta
    should be deeply negative (the trade isn't anywhere near fair).
    """
    target = 10000   # elite RB1
    scrub_a = 700
    scrub_b = 1600

    raw_delta = target - (scrub_a + scrub_b)   # +7700, you "win" raw
    adjusted, adj = apply_adjustment_to_delta(
        raw_delta=raw_delta,
        send_values=[scrub_a, scrub_b],
        receive_values=[target],
    )
    # After premium, you should still be "winning" raw — but the trade
    # is so wildly underpriced it would never transact. Premium should
    # at LEAST eat most of the raw win, leaving < 0 effective.
    assert adjusted < 0, (
        f"Scrub-for-elite must net negative after premium; got {adjusted}"
    )


def test_regression_premium_grows_with_quality_gap():
    """Same target, weaker filler => bigger premium owed."""
    target = 9000
    _, adj_weak = apply_adjustment_to_delta(
        raw_delta=0,
        send_values=[1000, 500],   # huge gap
        receive_values=[target],
    )
    _, adj_strong = apply_adjustment_to_delta(
        raw_delta=0,
        send_values=[7000, 2000],   # small gap
        receive_values=[target],
    )
    assert adj_weak.adjustment > adj_strong.adjustment


# ---------------------------------------------------------------------------
# ValueAdjustment dataclass surface
# ---------------------------------------------------------------------------

def test_value_adjustment_includes_rationale():
    adj = compute_value_adjustment(
        send_values=[3000, 2000],
        receive_values=[8500],
    )
    assert adj.rationale != ""
    assert "tier premium" in adj.rationale
    assert "quality gap" in adj.rationale


def test_value_adjustment_top_stud_value_is_max_of_all():
    adj = compute_value_adjustment(
        send_values=[3000, 9500],
        receive_values=[5000, 4000],
    )
    assert adj.top_stud_value == 9500
