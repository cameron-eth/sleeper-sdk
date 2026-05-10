"""Tests for analytics.pick_value.lookup_pick_ktc.

KTC encodes picks as RDP-position rows with names like "2027 Mid 1st".
Trades reference picks by (season, round) only — this module bridges
the two by trying Mid → Early → Late tier in order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from sleeper.analytics.pick_value import lookup_pick_ktc


# Lightweight fakes that mirror the KTCPlayer structure
# (`p.superflex.value` / `p.one_qb.value`) without dragging in pydantic.

@dataclass
class _FakeSide:
    value: int


@dataclass
class _FakeKTC:
    superflex: Optional[_FakeSide] = None
    one_qb: Optional[_FakeSide] = None


def _picks(**kwargs: int) -> dict[str, _FakeKTC]:
    """Build a name → fake KTC map. Pass `**{"2027 Mid 1st": 5500, ...}`."""
    return {name: _FakeKTC(superflex=_FakeSide(value=v)) for name, v in kwargs.items()}


# ---------------------------------------------------------------------------
# Happy path — Mid tier present
# ---------------------------------------------------------------------------

def test_lookup_returns_mid_tier_when_present():
    picks = _picks(**{"2027 Mid 1st": 5500})
    assert lookup_pick_ktc("2027", 1, picks) == 5500


def test_lookup_uses_2nd_round_ordinal():
    picks = _picks(**{"2026 Mid 2nd": 3000})
    assert lookup_pick_ktc("2026", 2, picks) == 3000


def test_lookup_uses_3rd_round_ordinal():
    picks = _picks(**{"2028 Mid 3rd": 2100})
    assert lookup_pick_ktc("2028", 3, picks) == 2100


def test_lookup_uses_4th_round_ordinal():
    picks = _picks(**{"2027 Mid 4th": 1700})
    assert lookup_pick_ktc("2027", 4, picks) == 1700


# ---------------------------------------------------------------------------
# Tier fallback — Mid → Early → Late
# ---------------------------------------------------------------------------

def test_falls_back_to_early_when_mid_missing():
    picks = _picks(**{"2027 Early 1st": 7100})  # only Early present
    assert lookup_pick_ktc("2027", 1, picks) == 7100


def test_falls_back_to_late_when_mid_and_early_missing():
    picks = _picks(**{"2027 Late 1st": 4800})  # only Late present
    assert lookup_pick_ktc("2027", 1, picks) == 4800


def test_mid_wins_over_early_and_late_when_all_present():
    """If multiple tiers exist, Mid is the canonical answer."""
    picks = _picks(**{
        "2027 Mid 1st":   5500,
        "2027 Early 1st": 7100,
        "2027 Late 1st":  4800,
    })
    assert lookup_pick_ktc("2027", 1, picks) == 5500


# ---------------------------------------------------------------------------
# Missing data — graceful zeros
# ---------------------------------------------------------------------------

def test_missing_pick_returns_zero():
    """Unknown season returns 0, no exception."""
    picks = _picks(**{"2027 Mid 1st": 5500})
    assert lookup_pick_ktc("2030", 1, picks) == 0


def test_empty_map_returns_zero():
    assert lookup_pick_ktc("2027", 1, {}) == 0


def test_pick_with_no_superflex_side_returns_zero():
    """Defensive: a KTCPlayer with .superflex=None falls back to 0."""
    picks = {"2027 Mid 1st": _FakeKTC(superflex=None)}
    assert lookup_pick_ktc("2027", 1, picks) == 0


def test_high_round_uses_nth_ordinal():
    """5th-round picks etc. should still attempt a lookup, even if KTC has no data."""
    picks = _picks(**{"2027 Mid 5th": 800})
    assert lookup_pick_ktc("2027", 5, picks) == 800


# ---------------------------------------------------------------------------
# Format selector
# ---------------------------------------------------------------------------

def test_one_qb_format_uses_one_qb_side():
    fake = _FakeKTC(superflex=_FakeSide(value=5500), one_qb=_FakeSide(value=3000))
    picks = {"2027 Mid 1st": fake}
    assert lookup_pick_ktc("2027", 1, picks, fmt="1qb") == 3000


def test_default_format_is_sf():
    fake = _FakeKTC(superflex=_FakeSide(value=5500), one_qb=_FakeSide(value=3000))
    picks = {"2027 Mid 1st": fake}
    assert lookup_pick_ktc("2027", 1, picks) == 5500
