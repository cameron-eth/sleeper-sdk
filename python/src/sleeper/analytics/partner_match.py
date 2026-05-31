"""Trade-partner matchmaking — score how attractive each other owner in a
league is as a trade partner for the user.

Three signals combine into a single compatibility score:

1. **Archetype synergy** — a rebuilder wants contenders to buy their vets
   for picks; a contender wants rebuilders/pretenders to sell their stars.
   Two CONTENDERs fighting over the same assets is a bad match. The
   `_SYNERGY` table encodes this explicitly.

2. **Positional fit** — the user's surplus position is the partner's need,
   and vice versa. Each overlap = +2 to the score.

3. **History** — past completed trades between the two rosters.
   - More history = more engaged partner (capped, so a single grumpy
     veteran trade-partner doesn't dominate)
   - User's net KTC win/loss with that partner = trust signal

The output is a ranked list with a human-readable rationale per partner,
so the CLI can show "engage romanempire (rebuilder) — your QB surplus
fits his QB need, +13K KTC history" instead of just a number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Archetype = Literal[
    "CONTENDER", "RELOADING", "REBUILDING", "PRETENDER", "UNKNOWN"
]


# Archetype synergy matrix: (user_archetype, partner_archetype) -> score.
# Range: -3 to +5. Captures "who wants to trade what" at the macro level.
_SYNERGY: dict[tuple[str, str], int] = {
    # User REBUILDING wants contenders who'll buy vets for picks
    ("REBUILDING", "CONTENDER"):  5,
    ("REBUILDING", "RELOADING"):  3,
    ("REBUILDING", "PRETENDER"):  4,   # they SHOULD be selling vets to you
    ("REBUILDING", "REBUILDING"): 1,
    # User CONTENDER wants rebuilders/pretenders to sell stars
    ("CONTENDER", "REBUILDING"):  5,
    ("CONTENDER", "PRETENDER"):   4,
    ("CONTENDER", "RELOADING"):   2,
    ("CONTENDER", "CONTENDER"):  -2,   # competing for same assets
    # User RELOADING is flexible, fits most
    ("RELOADING", "CONTENDER"):   3,
    ("RELOADING", "REBUILDING"):  3,
    ("RELOADING", "PRETENDER"):   2,
    ("RELOADING", "RELOADING"):   0,
    # User PRETENDER's best move is selling vets while they hold value
    ("PRETENDER", "CONTENDER"):   5,
    ("PRETENDER", "REBUILDING"):  3,
    ("PRETENDER", "RELOADING"):   2,
    ("PRETENDER", "PRETENDER"):  -1,
}


def archetype_synergy(user_archetype: str, partner_archetype: str) -> int:
    """Return the synergy score for a (user, partner) archetype pair.

    Unknown archetypes return 0 (neutral). This keeps the algorithm safe
    when gm_mode can't classify a roster (e.g., season hasn't started and
    production rank is unreliable).
    """
    return _SYNERGY.get((user_archetype, partner_archetype), 0)


@dataclass
class PositionalFit:
    """Overlap between user surplus and partner need (and vice versa)."""

    user_offers: list[str] = field(default_factory=list)
    """Positions where the user is STRONG and the partner is WEAK —
    things the user can ship for upgrades elsewhere."""

    partner_offers: list[str] = field(default_factory=list)
    """Positions where the partner is STRONG and the user is WEAK —
    things the user wants to acquire."""

    score: int = 0


def positional_fit(
    user_strong: set[str],
    user_weak: set[str],
    partner_strong: set[str],
    partner_weak: set[str],
) -> PositionalFit:
    """Compute mutual positional fit between user and partner.

    Each overlap (user surplus ↔ partner need OR partner surplus ↔ user
    need) is worth 2 points. The bi-directional flow is what makes a
    trade actually transactable; one-sided fits often stall.
    """
    user_offers = sorted(user_strong & partner_weak)
    partner_offers = sorted(partner_strong & user_weak)
    score = len(user_offers) * 2 + len(partner_offers) * 2
    return PositionalFit(
        user_offers=user_offers,
        partner_offers=partner_offers,
        score=score,
    )


@dataclass
class TradeHistory:
    """Compressed completed-trade record between two rosters."""

    total: int = 0
    """Total completed trades between user and this partner."""

    user_wins: int = 0
    user_losses: int = 0
    fair: int = 0
    user_net_ktc: int = 0
    """Sum of adjusted_overpay across all scored trades (positive = user
    won more value than they gave). Unscored trades contribute 0."""


def history_score(history: TradeHistory) -> int:
    """Score a partner based on past completed trades with the user.

    Logic:
    - 0 trades  → +1 (small upside for an untapped channel — every league
      member is worth a first conversation)
    - 1-2 trades → activity counts, KTC swing dominates
    - 3+ trades → high engagement bonus, KTC swing capped at ±3

    KTC swing is scaled at 5K = 1 point. A +15K net partner is a clear
    "they overpay you" channel (+3). A −15K partner exploits you (−3).
    """
    if history.total == 0:
        return 1   # fresh — small "worth trying" upside
    activity = min(history.total, 5)
    ktc_factor = history.user_net_ktc // 5000
    return activity + max(-3, min(3, ktc_factor))


@dataclass
class PartnerScore:
    """Final compatibility breakdown for one potential partner."""

    owner: str
    roster_id: int
    archetype: str
    synergy: int
    positional: PositionalFit
    history: TradeHistory
    history_pts: int
    total: int
    rationale: str


def score_partner(
    *,
    owner: str,
    roster_id: int,
    user_archetype: str,
    partner_archetype: str,
    user_strong: set[str],
    user_weak: set[str],
    partner_strong: set[str],
    partner_weak: set[str],
    history: TradeHistory,
) -> PartnerScore:
    """Combine the three signals into one PartnerScore.

    Final score = synergy + positional.score + history_pts.
    Typical range: -5 (avoid) to +15 (engage immediately).
    """
    syn = archetype_synergy(user_archetype, partner_archetype)
    fit = positional_fit(user_strong, user_weak, partner_strong, partner_weak)
    hist_pts = history_score(history)
    total = syn + fit.score + hist_pts

    bits: list[str] = []
    bits.append(f"{partner_archetype}")
    if fit.user_offers:
        bits.append(f"wants your {'+'.join(fit.user_offers)}")
    if fit.partner_offers:
        bits.append(f"has {'+'.join(fit.partner_offers)} you need")
    if history.total > 0:
        sign = "+" if history.user_net_ktc >= 0 else ""
        bits.append(
            f"{history.total} prior trades ({sign}{history.user_net_ktc:,} net)"
        )
    else:
        bits.append("no prior history")
    rationale = " · ".join(bits)

    return PartnerScore(
        owner=owner,
        roster_id=roster_id,
        archetype=partner_archetype,
        synergy=syn,
        positional=fit,
        history=history,
        history_pts=hist_pts,
        total=total,
        rationale=rationale,
    )


def rank_partners(scores: list[PartnerScore]) -> list[PartnerScore]:
    """Sort partners by total score, descending (best engage targets first).

    Stable sort: ties broken by synergy, then history_pts, then owner name
    so the output is deterministic across runs.
    """
    return sorted(
        scores,
        key=lambda s: (-s.total, -s.synergy, -s.history_pts, s.owner.lower()),
    )
