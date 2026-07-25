"""L3 — Trade runtime (mutual-fit matching, acceptance, rationale).

The top of the stack. Where the old engine asked "what can I package to buy
this target, and is the KTC overpay inside a band?", this asks the question
that actually produces trades people accept:

    Is there an exchange where BOTH sides move toward THEIR OWN optimum?

That's only expressible because L2 gives each side its own valuation of the
same asset. A trade is scored by **mutual gain**: each side's contextual value
of what it receives minus what it sends, measured in *its own* window. A
rebuilder shipping an aging producer for a pick can show a positive gain for
both sides simultaneously — impossible in a single-valuation model.

Guardrails that keep this honest:

* **Market realism gate.** Mutual contextual gain is necessary but not
  sufficient — a package that is absurd at market price won't be accepted no
  matter how well the windows align. Every candidate must also clear a raw
  market-value sanity band (via the L-value-adjustment consolidation premium).
* **Acceptance is a PROBABILITY, not a verdict.** We surface how likely the
  partner is to engage, and never claim a trade "will" happen.
* **Every proposal carries a rationale** naming the windows, the asset
  horizons, and both sides' gains — so a human can sanity-check the thesis.
* **Proposals are stamped with the model version** they were computed against,
  so a stale suggestion can be re-validated before execution (see the
  versioning design; `model_version` is carried through opaquely here).

Pure functions over L0/L1/L2 outputs; see tests/test_trade_runtime.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable, Optional, Sequence

from sleeper.analytics.base_value import Asset
from sleeper.analytics.contextual_value import contextual_value, horizon_score
from sleeper.analytics.league_model import LeagueModel, TeamProfile
from sleeper.analytics.value_adjustment import apply_adjustment_to_delta

# Market-realism band on the raw (adjusted) KTC delta, from the proposing
# side's perspective. Negative = you give up more market value than you get.
# A little generosity is how trades actually get accepted; a lot is a fleece
# that will be laughed at.
MARKET_FLOOR = -3500
MARKET_CEIL = 3500

# Minimum contextual gain (per side) for a trade to count as mutually good.
MIN_SIDE_GAIN = 150

# Acceptance model weights.
_ACCEPT_MARKET_SCALE = 4000.0
_ACCEPT_NEED_BONUS = 0.15


@dataclass
class TradeProposal:
    partner_roster_id: int
    partner_owner: str
    send: list[Asset]
    receive: list[Asset]
    my_gain: int                  # contextual gain to me, in my window
    partner_gain: int             # contextual gain to partner, in their window
    mutual_gain: int              # min of the two — the binding constraint
    market_delta: int             # raw adjusted KTC delta from my perspective
    acceptance: float             # probability-ish score in [0, 1]
    rationale: str = ""
    model_version: Optional[str] = None


def _sum_ctx(assets: Iterable[Asset], window: float, fmt: str) -> int:
    return sum(contextual_value(a, window, fmt) for a in assets)


def side_gain(
    receive: Sequence[Asset],
    send: Sequence[Asset],
    window: float,
    fmt: str = "sf",
) -> int:
    """Contextual gain for a team at `window` receiving `receive` for `send`."""
    return _sum_ctx(receive, window, fmt) - _sum_ctx(send, window, fmt)


def market_delta(
    send: Sequence[Asset],
    receive: Sequence[Asset],
    fmt: str = "sf",
) -> int:
    """Raw market delta from the sender's perspective, consolidation-adjusted."""
    send_vals = [a.base_value(fmt) for a in send]
    recv_vals = [a.base_value(fmt) for a in receive]
    raw = sum(recv_vals) - sum(send_vals)
    adjusted, _ = apply_adjustment_to_delta(raw, send_vals, recv_vals)
    return adjusted


def acceptance_probability(
    partner_gain: int,
    market_delta_for_partner: int,
    need_met: bool,
) -> float:
    """How likely the partner is to engage, in [0, 1].

    Deliberately a soft score, not a gate. Driven by whether the deal is good
    for them on raw market terms (what most owners actually eyeball), lifted
    when it also fills a real positional need. Contextual gain sets the floor:
    a deal that's bad for them in their own window is never likely.
    """
    if partner_gain <= 0:
        return 0.0
    # Logistic-ish on the partner's market delta.
    x = market_delta_for_partner / _ACCEPT_MARKET_SCALE
    base = 1.0 / (1.0 + pow(2.718281828, -x))
    if need_met:
        base += _ACCEPT_NEED_BONUS
    return max(0.0, min(1.0, base))


def _fills_need(receive: Sequence[Asset], profile: TeamProfile) -> bool:
    """True if any received player addresses a real positional hole."""
    for a in receive:
        if a.is_pick:
            continue
        n = profile.needs.get(a.position)
        if n is not None and n.need > 0 and a.base_sf >= n.replacement:
            return True
    return False


def _describe(assets: Sequence[Asset]) -> str:
    return " + ".join(a.name for a in assets) if assets else "(nothing)"


def build_rationale(
    me: TeamProfile,
    partner: TeamProfile,
    send: Sequence[Asset],
    receive: Sequence[Asset],
    my_gain: int,
    partner_gain: int,
) -> str:
    def horizon_word(assets: Sequence[Asset]) -> str:
        if not assets:
            return "nothing"
        h = sum(horizon_score(a) for a in assets) / len(assets)
        if h > 0.25:
            return "future value (youth/picks)"
        if h < -0.25:
            return "win-now production"
        return "prime-age value"

    return (
        f"You ({me.archetype}, window {me.window:+.2f}) send "
        f"{horizon_word(send)} and get {horizon_word(receive)}; "
        f"{partner.owner} ({partner.archetype}, window {partner.window:+.2f}) "
        f"moves the other way. Contextual gain: you {my_gain:+,}, "
        f"them {partner_gain:+,}."
    )


def find_mutual_trades(
    model: LeagueModel,
    my_roster_id: int,
    teams_assets: dict[int, Sequence[Asset]],
    *,
    max_send: int = 2,
    max_receive: int = 2,
    top: int = 15,
    min_side_gain: int = MIN_SIDE_GAIN,
    market_floor: int = MARKET_FLOOR,
    market_ceil: int = MARKET_CEIL,
    model_version: Optional[str] = None,
) -> list[TradeProposal]:
    """Search the league for exchanges that improve BOTH sides' windows.

    Enumerates small packages (up to `max_send` × `max_receive` assets) against
    every other team, keeps those where each side shows contextual gain and the
    raw market delta stays realistic, and ranks by mutual gain × acceptance.
    """
    fmt = model.fmt
    me = model.teams[my_roster_id]
    my_assets = list(teams_assets.get(my_roster_id, []))
    proposals: list[TradeProposal] = []

    def packages(assets: Sequence[Asset], k: int) -> list[tuple[Asset, ...]]:
        out: list[tuple[Asset, ...]] = []
        for size in range(1, k + 1):
            out.extend(combinations(assets, size))
        return out

    my_packages = packages(my_assets, max_send)

    for rid, profile in model.teams.items():
        if rid == my_roster_id:
            continue
        their_assets = list(teams_assets.get(rid, []))
        their_packages = packages(their_assets, max_receive)

        for send in my_packages:
            for receive in their_packages:
                my_g = side_gain(receive, send, me.window, fmt)
                if my_g < min_side_gain:
                    continue
                partner_g = side_gain(send, receive, profile.window, fmt)
                if partner_g < min_side_gain:
                    continue

                mdelta = market_delta(send, receive, fmt)
                if not (market_floor <= mdelta <= market_ceil):
                    continue

                accept = acceptance_probability(
                    partner_g, -mdelta, _fills_need(send, profile)
                )
                if accept <= 0:
                    continue

                proposals.append(TradeProposal(
                    partner_roster_id=rid,
                    partner_owner=profile.owner,
                    send=list(send),
                    receive=list(receive),
                    my_gain=my_g,
                    partner_gain=partner_g,
                    mutual_gain=min(my_g, partner_g),
                    market_delta=mdelta,
                    acceptance=accept,
                    rationale=build_rationale(me, profile, send, receive, my_g, partner_g),
                    model_version=model_version,
                ))

    proposals.sort(key=lambda p: -(p.mutual_gain * (0.5 + p.acceptance)))
    return proposals[:top]
