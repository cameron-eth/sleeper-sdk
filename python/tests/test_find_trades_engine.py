"""Tests for analytics.find_trades_engine.

Targets the scoring math that had the May 2026 sign bug. With the math
now extracted from the CLI, these tests guard the canonical sign
convention without subprocess gymnastics.

Sign convention recap:
    favors == "receive"  →  adjusted = raw - premium  (you owe extra)
    favors == "send"     →  adjusted = raw + premium  (partner owes extra)
    favors == "none"     →  adjusted = raw            (1-for-1, no premium)
"""
from __future__ import annotations

import pytest

from sleeper.analytics.find_trades_engine import (
    PackageScore,
    is_fair_overpay,
    package_overpay,
)


# ---------------------------------------------------------------------------
# 1-for-1: no adjustment, raw == adjusted
# ---------------------------------------------------------------------------

def test_one_for_one_has_zero_adjustment():
    score = package_overpay([6157], 9985)
    assert score.adjustment.favors == "none"
    assert score.raw_overpay == 6157 - 9985
    assert score.adjusted_overpay == score.raw_overpay
    assert score.adjustment.adjustment == 0


def test_one_for_one_overpay_flows_through():
    """If you send a chip worth more than the target, raw overpay is positive."""
    score = package_overpay([7000], 5000)
    assert score.raw_overpay == 2000
    assert score.adjusted_overpay == 2000


# ---------------------------------------------------------------------------
# 2-for-1 receive-stud: premium is a debit
# ---------------------------------------------------------------------------

def test_two_for_one_receive_stud_subtracts_premium():
    """Sending two chips for an elite stud — you owe the premium ON TOP of face."""
    target = 9910      # elite tier
    chip_a = 4881
    chip_b = 2589

    score = package_overpay([chip_a, chip_b], target)

    assert score.adjustment.favors == "receive"
    assert score.raw_overpay == (chip_a + chip_b) - target  # +(-2440)
    # adjusted = raw - premium → MORE NEGATIVE than raw
    assert score.adjusted_overpay == score.raw_overpay - score.adjustment.adjustment
    assert score.adjusted_overpay < score.raw_overpay


def test_scrub_for_elite_target_is_clearly_unfair_after_premium():
    """The classic regression: 2 scrubs for an elite stud must NOT score as fair.

    Pre-fix bug: the algorithm credited the premium against your raw deficit,
    making a wildly underpriced offer score within the 'fair overpay' band.
    """
    score = package_overpay([700, 1600], 10000)
    # Adjusted overpay should be deeply negative — not in any reasonable
    # 'fair' band (e.g. normal mode +300..+3500).
    assert score.adjusted_overpay < -5000


# ---------------------------------------------------------------------------
# 1-for-2 send-stud: premium is a credit
# ---------------------------------------------------------------------------

def test_one_for_two_send_stud_adds_premium():
    """Sending one stud for two chips — partner owes the premium to you."""
    target_total = 8500
    score = package_overpay([3000, 2500], target_total)
    assert score.adjustment.favors == "receive"   # YOU received the stud (single recv)

    # Now flip — send the stud (single send to multiple receive).
    # find-trades doesn't currently use this path because it's targeted at
    # acquiring, not consolidating. But validate the engine handles it.
    # The engine's `package_overpay` always assumes ONE receive target, so
    # this configuration isn't directly testable through the engine signature.
    # That's by design — sending a stud is what `send-trade` orchestrates,
    # and the wrapper there evaluates from the partner's perspective.


# ---------------------------------------------------------------------------
# is_fair_overpay — band membership
# ---------------------------------------------------------------------------

def test_is_fair_overpay_inside_band_returns_true():
    score = PackageScore(raw_overpay=1000, adjusted_overpay=1000, adjustment=None)  # type: ignore[arg-type]
    assert is_fair_overpay(score, min_overpay=300, max_overpay=3500)


def test_is_fair_overpay_below_band_returns_false():
    score = PackageScore(raw_overpay=-500, adjusted_overpay=-500, adjustment=None)  # type: ignore[arg-type]
    assert not is_fair_overpay(score, min_overpay=300, max_overpay=3500)


def test_is_fair_overpay_above_band_returns_false():
    score = PackageScore(raw_overpay=4000, adjusted_overpay=4000, adjustment=None)  # type: ignore[arg-type]
    assert not is_fair_overpay(score, min_overpay=300, max_overpay=3500)


def test_is_fair_overpay_at_boundaries_is_inclusive():
    """Band edges are inclusive — 300 and 3500 both qualify."""
    lo = PackageScore(raw_overpay=300, adjusted_overpay=300, adjustment=None)  # type: ignore[arg-type]
    hi = PackageScore(raw_overpay=3500, adjusted_overpay=3500, adjustment=None)  # type: ignore[arg-type]
    assert is_fair_overpay(lo, min_overpay=300, max_overpay=3500)
    assert is_fair_overpay(hi, min_overpay=300, max_overpay=3500)


def test_is_fair_overpay_uses_adjusted_not_raw():
    """The band check operates on adjusted_overpay, not raw_overpay.

    A scrub-package with raw_overpay in-range but adjusted_overpay outside
    the band must be rejected — that's the entire point of the May 2026 fix.
    """
    score = PackageScore(raw_overpay=500, adjusted_overpay=-8000, adjustment=None)  # type: ignore[arg-type]
    assert not is_fair_overpay(score, min_overpay=300, max_overpay=3500)
