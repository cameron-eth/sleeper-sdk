"""Tests for analytics.value_adjustment (v2 model).

Coverage:
- Empty / degenerate cases
- Even-spot trades produce zero adjustment
- Tier classification (elite / high / mid / none)
- Spot premium scales with tier
- Tier scarcity premium = % of target KTC
- Isolation gap considers ALL filler chips (not just top)
- Dilution penalty quantifies "10 shit players ≠ 1 elite"
- Sign correctness for `apply_adjustment_to_delta`
- Regression tests for the May 2026 sign-fix
- suggest_evening_piece — reverse-engineered "missing piece" label
"""
from __future__ import annotations

import pytest

from sleeper.analytics.value_adjustment import (
    DILUTION_MULT,
    ISOLATION_GAP_MULT_ELITE,
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
    suggest_evening_piece,
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
# Tier classification
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
    adj = compute_value_adjustment(send_values=[ktc], receive_values=[1])
    assert adj.stud_tier == expected_tier


# ---------------------------------------------------------------------------
# Spot premium
# ---------------------------------------------------------------------------

def test_2_for_1_with_elite_stud_uses_elite_spot_value():
    """The receive side gets a 2-for-1 elite consolidation."""
    adj = compute_value_adjustment(
        send_values=[10000],
        receive_values=[3000, 3000],
    )
    assert adj.favors == "send"
    assert adj.roster_spot_diff == 1
    expected_spot = int(SPOT_VALUE_ELITE * 1 * 1.25)
    assert adj.spot_component == expected_spot


def test_2_for_1_with_no_stud_uses_base_spot_value():
    """When no chip is in any tier, spot value is the BASE constant only."""
    adj = compute_value_adjustment(
        send_values=[2000],
        receive_values=[1000, 800],
    )
    assert adj.stud_tier == "none"
    assert adj.spot_component == SPOT_VALUE_BASE


# ---------------------------------------------------------------------------
# Tier scarcity premium
# ---------------------------------------------------------------------------

def test_elite_target_carries_30pct_tier_premium():
    target = 10000
    adj = compute_value_adjustment(
        send_values=[3000, 3000],
        receive_values=[target],
    )
    assert adj.favors == "receive"
    assert adj.stud_tier == "elite"
    assert adj.tier_premium == int(target * TIER_PREMIUM_ELITE)


def test_high_target_carries_18pct_tier_premium():
    target = 7000
    adj = compute_value_adjustment(
        send_values=[3000, 2500],
        receive_values=[target],
    )
    assert adj.stud_tier == "high"
    assert adj.tier_premium == int(target * TIER_PREMIUM_HIGH)


def test_mid_target_carries_8pct_tier_premium():
    target = 5000
    adj = compute_value_adjustment(
        send_values=[2000, 1500],
        receive_values=[target],
    )
    assert adj.stud_tier == "mid"
    assert adj.tier_premium == int(target * TIER_PREMIUM_MID)


# ---------------------------------------------------------------------------
# Isolation gap — every chip below target compounds (v2 change)
# ---------------------------------------------------------------------------

def test_isolation_gap_sums_all_filler_chips_not_just_top():
    """The v2 isolation_gap considers EVERY chip below target.

    Old v1 quality_gap was max(0, target - top_send) × mult — a chip pack
    of [9K, 1K, 1K] for 10K target only penalized the 1K gap on top chip.
    v2 sums (10K-9K) + (10K-1K) + (10K-1K) = 19K of gap.
    """
    target = 10000
    adj = compute_value_adjustment(
        send_values=[9000, 1000, 1000],
        receive_values=[target],
    )
    expected_gap = ((target - 9000) + (target - 1000) + (target - 1000))
    expected_iso = int(expected_gap * ISOLATION_GAP_MULT_ELITE)
    assert adj.isolation_gap == expected_iso


def test_isolation_gap_ignores_chips_above_target():
    """A chip ABOVE target contributes nothing to the gap — only below counts."""
    target = 5000
    adj = compute_value_adjustment(
        send_values=[8000, 1000],   # 8000 is above 5000, ignore for gap
        receive_values=[target],
    )
    expected_gap = target - 1000   # only the 1000 chip
    expected_iso = int(expected_gap * ISOLATION_GAP_MULT_ELITE)
    assert adj.isolation_gap == expected_iso


def test_quality_gap_zero_when_all_chips_meet_target():
    adj = compute_value_adjustment(
        send_values=[10000, 11000],
        receive_values=[8500],
    )
    assert adj.isolation_gap == 0


# ---------------------------------------------------------------------------
# Dilution penalty — the v2 headline feature
# ---------------------------------------------------------------------------

def test_dilution_is_zero_for_one_for_one():
    """A 1-for-1 trade has no dilution by definition."""
    adj = compute_value_adjustment(send_values=[5000], receive_values=[9000])
    assert adj.dilution == 0


def test_dilution_is_zero_when_avg_meets_target():
    """If avg chip value is at the target, nothing is diluted."""
    target = 5000
    adj = compute_value_adjustment(
        send_values=[5000, 5000, 5000],
        receive_values=[target],
    )
    assert adj.dilution == 0


def test_dilution_grows_with_chip_count():
    """Same avg ratio but more chips => more dilution tax."""
    target = 10000
    # 2 chips at 2K each = 1/5 ratio, 1 extra spot
    adj_2 = compute_value_adjustment(send_values=[2000, 2000], receive_values=[target])
    # 5 chips at 2K each = 1/5 ratio, 4 extra spots — should hurt much more
    adj_5 = compute_value_adjustment(
        send_values=[2000, 2000, 2000, 2000, 2000],
        receive_values=[target],
    )
    assert adj_5.dilution > adj_2.dilution * 2   # noticeably more than linear


def test_dilution_grows_as_avg_drops():
    """Same chip count but lower avg => more dilution tax."""
    target = 10000
    # 2 chips at 4K each — avg is 40% of target
    adj_mid = compute_value_adjustment(send_values=[4000, 4000], receive_values=[target])
    # 2 chips at 500 each — avg is 5% of target
    adj_low = compute_value_adjustment(send_values=[500, 500], receive_values=[target])
    assert adj_low.dilution > adj_mid.dilution


def test_ten_filler_chips_for_elite_pays_massive_dilution():
    """The canonical KTC case: many filler ≠ one elite. Must hurt a lot."""
    target = 10000
    adj = compute_value_adjustment(
        send_values=[1000] * 10,
        receive_values=[target],
    )
    # Dilution alone should exceed a 2026 mid 1st (~4800 KTC)
    assert adj.dilution > 4800
    # And the TOTAL adjustment should make this trade WILDLY unfair
    assert adj.adjustment > 20000


# ---------------------------------------------------------------------------
# apply_adjustment_to_delta — sign correctness
# ---------------------------------------------------------------------------

def test_apply_adjustment_receive_stud_lowers_adjusted_delta():
    """If you receive the stud, the adjustment makes the trade WORSE for you."""
    raw_delta = 1000
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
    raw_delta = -1000
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
# Regression tests
# ---------------------------------------------------------------------------

def test_regression_fannin_mayfield_for_jsn_needs_substantial_extra_value():
    """User intuition: Fannin + Mayfield-discount → JSN should need a 1st+2nd.

    With v2 (dilution + all-chips isolation), the adjusted delta should
    sit in a "needs ≈ a 2026 1st + some change" range.
    """
    fannin = 4881
    mayfield_discounted = 2589
    jsn = 9917

    raw_delta = jsn - (fannin + mayfield_discounted)   # = +2447 (cam wins raw)
    adjusted, adj = apply_adjustment_to_delta(
        raw_delta=raw_delta,
        send_values=[fannin, mayfield_discounted],
        receive_values=[jsn],
    )
    assert adj.favors == "receive"
    # v2 model produces a deeper deficit than v1 — match the new range.
    assert -10000 <= adjusted <= -3500, (
        f"Expected adjusted delta in [-10000, -3500] for Fannin+Mayfield→JSN, got {adjusted}"
    )


def test_regression_scrub_package_for_elite_stud_is_clearly_unfair():
    """Sending only weak filler for an elite stud must NOT score as fair.

    The raw delta is +7700 (you "win" the face value swap), but the
    adjustment should make the effective delta deeply negative because
    the trade would never transact at this ratio.
    """
    target = 10000
    raw_delta = target - (700 + 1600)   # +7700
    adjusted, adj = apply_adjustment_to_delta(
        raw_delta=raw_delta,
        send_values=[700, 1600],
        receive_values=[target],
    )
    # v2 should make this clearly negative — at minimum -1000
    assert adjusted < -1000, (
        f"Scrub-for-elite must net deeply negative after v2 premium; got {adjusted}"
    )


def test_regression_ten_for_one_makes_3rd_round_for_hopkins_a_joke():
    """The KTC docs example: 12 third-round picks ≠ DeAndre Hopkins.

    Stand in: Hopkins-tier asset at 7500 KTC vs ten 800 KTC picks.
    With v2, this MUST score as deeply unfair (massive negative delta).
    """
    target = 7500
    raw_delta = target - (800 * 10)   # = -500 (filler totals more than face)
    adjusted, adj = apply_adjustment_to_delta(
        raw_delta=raw_delta,
        send_values=[800] * 10,
        receive_values=[target],
    )
    # v2 must punish this severely — needs many thousands MORE in value
    assert adjusted < -10000, (
        f"12-for-1 filler-for-Hopkins must be clearly unfair; got {adjusted}"
    )


# ---------------------------------------------------------------------------
# suggest_evening_piece — KTC-style missing piece
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("amount,expected_keyword", [
    (0, "balanced"),
    (200, "throw-in"),
    (1400, "2nd-round rookie"),
    (4500, "Mid 1st"),
    (7000, "WR2 / RB2"),
    (50000, "stud-tier"),
])
def test_suggest_evening_piece_returns_recognizable_label(amount, expected_keyword):
    out = suggest_evening_piece(amount)
    assert expected_keyword in out


def test_suggest_evening_piece_handles_negative_input():
    assert "balanced" in suggest_evening_piece(-500)


# ---------------------------------------------------------------------------
# ValueAdjustment dataclass surface
# ---------------------------------------------------------------------------

def test_value_adjustment_breakdown_fields_populated():
    adj = compute_value_adjustment(
        send_values=[3000, 2000],
        receive_values=[8500],
    )
    # Components sum to total
    assert (
        adj.spot_component + adj.tier_premium + adj.isolation_gap + adj.dilution
        == adj.adjustment
    )


def test_value_adjustment_top_stud_value_is_max_of_all():
    adj = compute_value_adjustment(
        send_values=[3000, 9500],
        receive_values=[5000, 4000],
    )
    assert adj.top_stud_value == 9500


def test_rationale_mentions_all_four_components():
    adj = compute_value_adjustment(
        send_values=[3000, 2000],
        receive_values=[8500],
    )
    for word in ("Spot", "tier premium", "isolation gap", "dilution"):
        assert word in adj.rationale
