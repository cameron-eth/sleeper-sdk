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
ALPHA = 0.35
MULT_MIN = 0.60
MULT_MAX = 1.40


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
