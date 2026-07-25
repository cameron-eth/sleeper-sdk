"""Tests for analytics.value_adjustment (v3 model).

v3 is a single, bounded "concentration premium" that fixes v2's overshoot,
double-counting, tier cliffs, and wrong-stud-reference bugs. These tests
assert the *properties* that define the new model rather than pinning exact
magic numbers, so a future recalibration of the constants stays green as
long as the KTC-aligned behavior holds.

Coverage:
- Degenerate / equal-count cases produce zero adjustment
- The premium is bounded by CAP_RATE * stud (no overshoot — the DeHop case)
- The scarcity rate is smooth and monotonic (no tier cliffs)
- Dilution amplifies with filler count and dispersion, but saturates
- The stud is read from the fewer-bodies side, not the whole-trade max
- Sign correctness for apply_adjustment_to_delta
- Display tier labels are cosmetic and don't drive the math
- suggest_evening_piece — reverse-engineered "missing piece" label
"""
from __future__ import annotations

import random

import pytest

from sleeper.analytics.value_adjustment import (
    CAP_RATE,
    DILUTION_GAIN,
    SAT_COUNT,
    STUD_CEIL,
    STUD_FLOOR,
    STUD_MAX_RATE,
    apply_adjustment_to_delta,
    compute_value_adjustment,
    suggest_evening_piece,
)


# ---------------------------------------------------------------------------
# Degenerate & equal-count cases
# ---------------------------------------------------------------------------

def test_empty_trade_returns_zero_adjustment():
    adj = compute_value_adjustment(send_values=[], receive_values=[])
    assert adj.adjustment == 0
    assert adj.favors == "none"
    assert adj.roster_spot_diff == 0


def test_even_spot_count_returns_zero_adjustment():
    """1-for-1 is a like-for-like swap — no consolidation premium."""
    adj = compute_value_adjustment(send_values=[6157], receive_values=[9985])
    assert adj.adjustment == 0
    assert adj.favors == "none"
    assert adj.roster_spot_diff == 0


def test_2_for_2_returns_zero_adjustment():
    """Equal body counts → no premium even when value is concentrated."""
    adj = compute_value_adjustment(send_values=[9000, 400], receive_values=[4700, 4700])
    assert adj.adjustment == 0
    assert adj.favors == "none"


def test_low_value_stud_below_floor_has_no_premium():
    """A 'stud' under STUD_FLOOR isn't scarce — rate is 0, premium is 0."""
    adj = compute_value_adjustment(send_values=[1200, 1200], receive_values=[2500])
    assert adj.top_stud_value == 2500
    assert adj.scarcity_rate == 0.0
    assert adj.adjustment == 0


# ---------------------------------------------------------------------------
# No overshoot — the premium is bounded (the DeAndre Hopkins case)
# ---------------------------------------------------------------------------

def test_dehop_case_is_bounded_not_thirty_thousand():
    """v2 produced 30,171 here; v3 must stay within CAP_RATE * stud."""
    adj = compute_value_adjustment(send_values=[1500] * 6, receive_values=[9000])
    assert adj.capped is True
    assert adj.adjustment == pytest.approx(CAP_RATE * 9000, abs=1)


def test_extreme_dilution_still_capped():
    """20 tiny chips for one elite cannot exceed the cap either."""
    adj = compute_value_adjustment(send_values=[450] * 20, receive_values=[9000])
    assert adj.adjustment <= CAP_RATE * 9000 + 1


def test_premium_never_exceeds_cap_fuzz():
    """Property: across random consolidations, adj ≤ CAP_RATE * stud."""
    rng = random.Random(1234)
    for _ in range(3000):
        n_send = rng.randint(1, 6)
        n_recv = rng.randint(1, 6)
        if n_send == n_recv:
            continue
        send = [rng.randint(200, 11000) for _ in range(n_send)]
        recv = [rng.randint(200, 11000) for _ in range(n_recv)]
        adj = compute_value_adjustment(send, recv)
        stud_side = send if n_send < n_recv else recv
        stud = max(stud_side)
        assert adj.adjustment <= CAP_RATE * stud + 1


# ---------------------------------------------------------------------------
# Smooth scarcity curve — no tier cliffs
# ---------------------------------------------------------------------------

def test_no_tier_cliff_across_6000_boundary():
    """A 2-KTC move across the old 6,000 threshold barely changes the premium."""
    lo = compute_value_adjustment(send_values=[3000, 3000], receive_values=[5999])
    hi = compute_value_adjustment(send_values=[3000, 3000], receive_values=[6001])
    assert abs(hi.adjustment - lo.adjustment) <= 20


def test_scarcity_rate_is_monotonic_in_stud_value():
    """Higher stud KTC → higher (or equal) scarcity rate, smoothly."""
    rates = [
        compute_value_adjustment(send_values=[v // 2, v // 2], receive_values=[v]).scarcity_rate
        for v in range(2000, 11000, 500)
    ]
    assert rates == sorted(rates)
    assert rates[0] == 0.0                      # below floor
    assert rates[-1] == pytest.approx(STUD_MAX_RATE)  # at/above ceil


def test_scarcity_rate_bounds():
    """Rate is 0 at/below the floor and saturates at STUD_MAX_RATE by the ceil."""
    below = compute_value_adjustment(send_values=[1000, 1000], receive_values=[STUD_FLOOR - 1])
    at_ceil = compute_value_adjustment(send_values=[4000, 4000], receive_values=[STUD_CEIL])
    above = compute_value_adjustment(send_values=[5000, 5000], receive_values=[STUD_CEIL + 3000])
    assert below.scarcity_rate == 0.0
    assert at_ceil.scarcity_rate == pytest.approx(STUD_MAX_RATE)
    assert above.scarcity_rate == pytest.approx(STUD_MAX_RATE)


# ---------------------------------------------------------------------------
# Dilution amplifier — count & dispersion, with saturation
# ---------------------------------------------------------------------------

def test_dilution_mult_at_least_one():
    adj = compute_value_adjustment(send_values=[5000, 3500], receive_values=[7000])
    assert adj.dilution_mult >= 1.0


def test_more_filler_chips_increase_premium():
    """Two chips vs three chips for the same stud: more bodies → bigger premium."""
    two = compute_value_adjustment(send_values=[3000, 3000], receive_values=[7000])
    three = compute_value_adjustment(send_values=[2000, 2000, 2000], receive_values=[7000])
    assert three.adjustment > two.adjustment


def test_deeper_dispersion_increases_premium():
    """Same count, but chips further below the stud → bigger premium."""
    tight = compute_value_adjustment(send_values=[6000, 6000], receive_values=[7000])
    diluted = compute_value_adjustment(send_values=[1500, 1500], receive_values=[7000])
    assert diluted.adjustment > tight.adjustment


def test_count_weight_saturates():
    """Beyond SAT_COUNT extra bodies, adding more filler stops raising the mult."""
    filler_at_sat = [1500] * (SAT_COUNT + 1)
    filler_beyond = [1500] * (SAT_COUNT + 4)
    a = compute_value_adjustment(send_values=filler_at_sat, receive_values=[9000])
    b = compute_value_adjustment(send_values=filler_beyond, receive_values=[9000])
    # count_weight is already 1.0 at SAT_COUNT extra bodies; dispersion is
    # identical here, so the dilution multiplier must match.
    assert a.dilution_mult == pytest.approx(b.dilution_mult)


# ---------------------------------------------------------------------------
# Stud is read from the fewer-bodies side, not the whole-trade max
# ---------------------------------------------------------------------------

def test_stud_is_the_consolidated_asset_not_the_biggest_asset():
    """Sending a huge asset to acquire a smaller target: the premium prices
    the acquired target (fewer-bodies side), not the asset you gave up."""
    # You send [8200, 300] (2 bodies) to receive [6000] (1 body).
    # Fewer-bodies side is the receive side → stud = 6000, not 8200.
    adj = compute_value_adjustment(send_values=[8200, 300], receive_values=[6000])
    assert adj.favors == "receive"
    assert adj.top_stud_value == 6000
    assert adj.stud_tier == "high"      # 6000 is 'high', not 'elite'


def test_favors_send_when_you_consolidate_the_stud_out():
    """You give one stud, receive a package → you're the stud side."""
    adj = compute_value_adjustment(send_values=[9000], receive_values=[5000, 3500])
    assert adj.favors == "send"
    assert adj.top_stud_value == 9000
    assert adj.adjustment > 0


# ---------------------------------------------------------------------------
# Sign correctness for apply_adjustment_to_delta
# ---------------------------------------------------------------------------

def test_apply_receive_stud_subtracts_premium():
    """Acquiring the stud: you owe the premium → adjusted delta drops."""
    raw = 6151 - 9408                       # receive − send (you overpay)
    adjusted, adj = apply_adjustment_to_delta(raw, [4704, 4704], [6151])
    assert adj.favors == "receive"
    assert adjusted == raw - adj.adjustment
    assert adjusted < raw


def test_apply_send_stud_adds_premium():
    """Giving up the stud: partner owes you → adjusted delta rises."""
    raw = 8500 - 9000                       # receive package − send stud
    adjusted, adj = apply_adjustment_to_delta(raw, [9000], [5000, 3500])
    assert adj.favors == "send"
    assert adjusted == raw + adj.adjustment
    assert adjusted > raw


def test_apply_even_trade_no_change():
    raw = 9985 - 6157
    adjusted, adj = apply_adjustment_to_delta(raw, [6157], [9985])
    assert adj.favors == "none"
    assert adjusted == raw


# ---------------------------------------------------------------------------
# Tier labels are cosmetic only
# ---------------------------------------------------------------------------

def test_tier_label_does_not_change_smoothness():
    """The label flips at 6000 but the adjustment stays continuous there."""
    lo = compute_value_adjustment(send_values=[3000, 3000], receive_values=[5999])
    hi = compute_value_adjustment(send_values=[3000, 3000], receive_values=[6001])
    assert lo.stud_tier == "mid"
    assert hi.stud_tier == "high"
    assert abs(hi.adjustment - lo.adjustment) <= 20   # value ≠ label


@pytest.mark.parametrize(
    "stud,expected",
    [(9000, "elite"), (8000, "elite"), (6500, "high"), (4200, "mid"), (2000, "none")],
)
def test_tier_labels(stud, expected):
    adj = compute_value_adjustment(send_values=[stud // 2, stud // 2], receive_values=[stud])
    assert adj.stud_tier == expected


# ---------------------------------------------------------------------------
# suggest_evening_piece
# ---------------------------------------------------------------------------

def test_suggest_evening_piece_zero_or_negative():
    assert suggest_evening_piece(0) == "≈ already balanced"
    assert suggest_evening_piece(-500) == "≈ already balanced"


def test_suggest_evening_piece_bands():
    assert suggest_evening_piece(150).startswith("≈")
    assert "2026 Mid 1st" in suggest_evening_piece(4800)
    assert suggest_evening_piece(999999) == "≈ a stud-tier asset (12K+ KTC)"


def test_suggest_evening_piece_is_monotonic_bandwise():
    """Larger adjustments never map to an 'earlier' (cheaper) band."""
    labels = [suggest_evening_piece(v) for v in range(100, 13000, 100)]
    # Each distinct label should appear as one contiguous run (no reversion).
    seen: list[str] = []
    for lab in labels:
        if not seen or seen[-1] != lab:
            assert lab not in seen, f"band {lab!r} reappeared out of order"
            seen.append(lab)


# ---------------------------------------------------------------------------
# A couple of anchored sanity values (guard rails, not exact-formula pins)
# ---------------------------------------------------------------------------

def test_healthy_2_for_1_premium_is_modest():
    """A balanced 2-for-1 for a high stud should carry a real but modest
    premium — well under the cap, comfortably in the low thousands."""
    adj = compute_value_adjustment(send_values=[5000, 3500], receive_values=[7000])
    assert 0 < adj.adjustment < CAP_RATE * 7000
    assert adj.capped is False
    assert adj.base_premium == int(round(7000 * adj.scarcity_rate))
    # dilution multiplier is the documented shape
    assert adj.dilution_mult == pytest.approx(
        1 + DILUTION_GAIN * ((7000 - 5000) / 7000 + (7000 - 3500) / 7000) / 2
        * min(1.0, 1 / SAT_COUNT),
        abs=1e-6,
    )
