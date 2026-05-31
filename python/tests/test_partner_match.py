"""Tests for analytics.partner_match.

Covers archetype synergy lookups, positional fit overlap math, history
scoring boundaries, and end-to-end `score_partner` rationale rendering.
"""
from __future__ import annotations

import pytest

from sleeper.analytics.partner_match import (
    PartnerScore,
    PositionalFit,
    TradeHistory,
    archetype_synergy,
    history_score,
    positional_fit,
    rank_partners,
    score_partner,
)


# ---------------------------------------------------------------------------
# Archetype synergy
# ---------------------------------------------------------------------------

def test_rebuilder_loves_contender():
    """The canonical "best pair" — rebuilder wants contenders to buy vets."""
    assert archetype_synergy("REBUILDING", "CONTENDER") == 5


def test_contender_avoids_other_contender():
    """Two contenders fight over the same assets — negative synergy."""
    assert archetype_synergy("CONTENDER", "CONTENDER") == -2


def test_pretender_should_sell_to_contender():
    """Pretender's best move is cashing vets while value is still there."""
    assert archetype_synergy("PRETENDER", "CONTENDER") == 5


def test_unknown_archetype_returns_zero():
    """Defensive: when gm_mode can't classify, synergy is neutral."""
    assert archetype_synergy("REBUILDING", "UNKNOWN") == 0
    assert archetype_synergy("UNKNOWN", "CONTENDER") == 0


# ---------------------------------------------------------------------------
# Positional fit
# ---------------------------------------------------------------------------

def test_perfect_positional_fit_both_directions():
    """User QB-strong/RB-weak meets partner RB-strong/QB-weak: both sides win."""
    fit = positional_fit(
        user_strong={"QB"},
        user_weak={"RB"},
        partner_strong={"RB"},
        partner_weak={"QB"},
    )
    assert fit.user_offers == ["QB"]
    assert fit.partner_offers == ["RB"]
    assert fit.score == 4   # 2 + 2


def test_no_overlap_returns_zero():
    """Nothing in common, nothing to trade — score 0."""
    fit = positional_fit(
        user_strong={"QB"},
        user_weak={"WR"},
        partner_strong={"QB"},   # both strong at QB
        partner_weak={"WR"},     # both weak at WR
    )
    assert fit.user_offers == []
    assert fit.partner_offers == []
    assert fit.score == 0


def test_one_way_fit_counts():
    """Only one side has a fit — score 2, not 4."""
    fit = positional_fit(
        user_strong={"QB"},
        user_weak=set(),
        partner_strong=set(),
        partner_weak={"QB"},
    )
    assert fit.user_offers == ["QB"]
    assert fit.partner_offers == []
    assert fit.score == 2


def test_multi_position_fit():
    """Multiple overlaps stack."""
    fit = positional_fit(
        user_strong={"QB", "TE"},
        user_weak={"WR"},
        partner_strong={"WR"},
        partner_weak={"QB", "TE"},
    )
    assert fit.user_offers == ["QB", "TE"]
    assert fit.partner_offers == ["WR"]
    assert fit.score == 6   # 2*2 + 1*2


# ---------------------------------------------------------------------------
# History scoring
# ---------------------------------------------------------------------------

def test_no_history_gives_small_upside_bonus():
    """An untapped partner is worth a +1 conversation."""
    assert history_score(TradeHistory()) == 1


def test_three_trades_neutral_ktc_scores_three():
    """Activity dominates when KTC net is small."""
    h = TradeHistory(total=3, user_net_ktc=2000)
    # activity = min(3, 5) = 3, ktc_factor = 2000 // 5000 = 0
    assert history_score(h) == 3


def test_big_positive_ktc_caps_at_three():
    """A partner who overpays you by 50K KTC caps at +3 contribution."""
    h = TradeHistory(total=10, user_net_ktc=50000)
    # activity = 5 (capped), ktc_factor capped at 3
    assert history_score(h) == 5 + 3   # = 8


def test_big_negative_ktc_caps_at_minus_three():
    """A partner who exploits you caps at −3 contribution (still subject to activity)."""
    h = TradeHistory(total=10, user_net_ktc=-50000)
    assert history_score(h) == 5 + (-3)   # = 2


def test_one_trade_with_loss_is_punished():
    """A single bad trade isn't a death sentence but shows up."""
    h = TradeHistory(total=1, user_net_ktc=-8000)
    # activity 1, ktc_factor -8000 // 5000 = -2 (floor div in Python is toward -inf)
    assert history_score(h) == 1 + (-2)   # = -1


# ---------------------------------------------------------------------------
# Score combination + rationale
# ---------------------------------------------------------------------------

def test_score_partner_combines_three_signals():
    """The total field is the sum of synergy + positional.score + history_pts."""
    ps = score_partner(
        owner="testuser",
        roster_id=3,
        user_archetype="REBUILDING",
        partner_archetype="CONTENDER",
        user_strong={"QB"},
        user_weak={"RB"},
        partner_strong={"RB"},
        partner_weak={"QB"},
        history=TradeHistory(total=2, user_net_ktc=6000),
    )
    # synergy = 5, positional = 4, history = 2 + 1 = 3 → total 12
    assert ps.synergy == 5
    assert ps.positional.score == 4
    assert ps.history_pts == 3
    assert ps.total == 12


def test_rationale_mentions_offers_when_present():
    ps = score_partner(
        owner="u",
        roster_id=1,
        user_archetype="REBUILDING",
        partner_archetype="CONTENDER",
        user_strong={"QB"},
        user_weak={"RB"},
        partner_strong={"RB"},
        partner_weak={"QB"},
        history=TradeHistory(),
    )
    assert "wants your QB" in ps.rationale
    assert "has RB you need" in ps.rationale
    assert "no prior history" in ps.rationale


def test_rationale_mentions_history_when_present():
    ps = score_partner(
        owner="u",
        roster_id=1,
        user_archetype="REBUILDING",
        partner_archetype="REBUILDING",
        user_strong=set(),
        user_weak=set(),
        partner_strong=set(),
        partner_weak=set(),
        history=TradeHistory(total=4, user_net_ktc=10000),
    )
    assert "4 prior trades" in ps.rationale
    assert "+10,000" in ps.rationale


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _make_score(owner: str, total: int, synergy: int = 0, history_pts: int = 0):
    return PartnerScore(
        owner=owner,
        roster_id=1,
        archetype="REBUILDING",
        synergy=synergy,
        positional=PositionalFit(),
        history=TradeHistory(),
        history_pts=history_pts,
        total=total,
        rationale="",
    )


def test_rank_partners_sorts_by_total_desc():
    scores = [
        _make_score("alpha", 3),
        _make_score("bravo", 8),
        _make_score("charlie", 5),
    ]
    ranked = rank_partners(scores)
    assert [s.owner for s in ranked] == ["bravo", "charlie", "alpha"]


def test_rank_partners_breaks_ties_by_synergy():
    """When totals tie, higher synergy wins (it's the more durable signal)."""
    scores = [
        _make_score("alpha", 5, synergy=1, history_pts=4),
        _make_score("bravo", 5, synergy=4, history_pts=1),
    ]
    ranked = rank_partners(scores)
    assert ranked[0].owner == "bravo"


def test_rank_partners_is_deterministic_for_full_ties():
    """Final tie-break is alphabetical (case-insensitive) — output stable across runs."""
    scores = [
        _make_score("Zeta", 3),
        _make_score("alpha", 3),
        _make_score("Beta", 3),
    ]
    ranked = rank_partners(scores)
    assert [s.owner for s in ranked] == ["alpha", "Beta", "Zeta"]
