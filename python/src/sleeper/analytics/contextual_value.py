"""L2 — Contextual (window-relative) value.

The keystone of the stack. L0 gives an asset one objective number; L2 makes
that number *depend on who holds it*. A 30-year-old producing RB is worth more
to a contender (who needs win-now points) than to a rebuilder (who can't use
the production before it decays). A rookie pick is the reverse. That
disagreement is the entire reason a trade both sides want can exist.

    contextual_value(asset, window) = base_value(asset) × window_multiplier

The multiplier is a *behavioral re-weighting of the market price*, not a second
age discount (KTC already prices age — see base_value.py). It's driven by two
things:

  * **horizon_score(asset)** ∈ [−1, +1] — does this asset pay off NOW (−1:
    aging producer) or in the FUTURE (+1: young player / draft pick)? Prime age
    is position-specific (RBs age first, QBs last in SF), which is how the RB
    cliff enters the model without a fragile full age curve.
  * **window** ∈ [−1, +1] — the holder's contention window from L1.

        multiplier = 1 − ALPHA · window · horizon_score        (clamped)

  Sign check: contender (window +1) × now-asset (horizon −1) → multiplier > 1
  (they pay up for win-now). Contender × future-asset → < 1. Rebuilder
  (window −1) × future-asset → > 1 (youth/picks are their currency). Rebuilder
  × aging-producer → < 1 (dead weight to them).

These parameters are SET here and meant to be **calibrated later against real
trade history** (the backtest) — same discipline as value_adjustment. Pure
functions throughout; see tests/test_contextual_value.py.
"""
from __future__ import annotations

from sleeper.analytics.base_value import Asset

# Position-specific prime age — the age at which horizon flips from future to
# now. RBs decline first, QBs hold value longest (especially in Superflex).
POSITION_PRIME_AGE: dict[str, float] = {
    "QB": 28.0,
    "RB": 25.0,
    "WR": 26.5,
    "TE": 27.0,
}
DEFAULT_PRIME_AGE = 26.5

# Years around prime over which horizon ramps from 0 to ±1.
HORIZON_SCALE = 4.0

# A pick is pure future.
PICK_HORIZON = 1.0

# Strength of the window re-weighting and the multiplier clamp.
#
# CRITICAL: contextual value is a PREFERENCE, not a currency. You cannot spend
# it. If a trade sheds real market value, your tradeable capital is
# permanently smaller and the "contextual gain" is imaginary. So the window
# re-weighting must stay small enough that it breaks ties between
# market-fair trades rather than justifying market-losing ones.
#
# v1 shipped ALPHA=0.35 with a [0.60, 1.40] clamp, which let two teams value
# the same asset 108% apart (1.40/0.65 = 2.08x). That rationalized handing
# elite youth to a rival for aging filler while eating a 3,000+ KTC loss.
# Real dynasty window effects are on the order of 10-20%, so the spread is
# now capped near 30% end-to-end.
ALPHA = 0.12
MULT_MIN = 0.88
MULT_MAX = 1.12


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def horizon_score(asset: Asset) -> float:
    """Future-vs-now score ∈ [−1, +1]. +1 = future (young/pick), −1 = now (aging)."""
    if asset.is_pick:
        return PICK_HORIZON
    if asset.age is None:
        return 0.0
    prime = POSITION_PRIME_AGE.get(asset.position, DEFAULT_PRIME_AGE)
    return _clamp((prime - asset.age) / HORIZON_SCALE, -1.0, 1.0)


def window_multiplier(asset: Asset, window: float) -> float:
    """Window re-weighting multiplier for an asset held by a team at `window`."""
    h = horizon_score(asset)
    m = 1.0 - ALPHA * window * h
    return _clamp(m, MULT_MIN, MULT_MAX)


def contextual_value(asset: Asset, window: float, fmt: str = "sf") -> int:
    """Base value re-weighted for a holder at contention `window` ∈ [−1, +1]."""
    return int(round(asset.base_value(fmt) * window_multiplier(asset, window)))


# ---------------------------------------------------------------------------
# Liquidity — low-value assets are not real trade currency
# ---------------------------------------------------------------------------

# Fraction of face value the very cheapest assets actually command in a trade.
# Nobody gives up real value for deep-bench pieces; they ride along as
# throw-ins. Without this, a package of fodder sums to a headline KTC number
# that no owner would ever honor, and the finder proposes handing over a real
# asset for a pile of names.
FODDER_FLOOR = 0.35

# Default line below which an asset reads as fodder. Callers should pass the
# league's actual mid-2nd-round pick value instead — a 2nd is the practical
# floor of "an asset someone will actually negotiate over".
DEFAULT_FODDER_LINE = 3400


def liquidity_factor(value: int, fodder_line: int = DEFAULT_FODDER_LINE) -> float:
    """How much of an asset's face value survives contact with the market.

    Assets at or above `fodder_line` (roughly a mid 2nd-round pick) trade at
    face. Below it, value ramps down linearly toward FODDER_FLOOR, because
    those players are depth/throw-ins rather than things a counterparty will
    pay for. Three 1,200-value bench pieces are not a 3,600-value asset.
    """
    if fodder_line <= 0 or value >= fodder_line:
        return 1.0
    if value <= 0:
        return FODDER_FLOOR
    r = value / fodder_line
    return FODDER_FLOOR + (1.0 - FODDER_FLOOR) * r


def tradeable_value(
    asset: Asset,
    fmt: str = "sf",
    fodder_line: int = DEFAULT_FODDER_LINE,
) -> int:
    """Face value discounted for market liquidity — what it's really worth
    as *currency* in a trade, as opposed to what a ranking site lists it at.

    Draft picks are exempt: a pick is a clean, universally-priced asset that
    every owner will transact on, regardless of where it sits on the board.
    """
    v = asset.base_value(fmt)
    if asset.is_pick:
        return v
    return int(round(v * liquidity_factor(v, fodder_line)))
