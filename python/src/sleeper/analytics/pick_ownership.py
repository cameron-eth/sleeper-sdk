"""L1 — Draft-pick ownership and slot-aware valuation.

Two jobs the rest of the stack needs and nothing previously did:

1. **Ownership.** Sleeper models picks by exception: every team implicitly owns
   its own pick in every (season, round), and `traded_picks` records only the
   ones that moved. Each record is
   ``{season, round, roster_id, previous_owner_id, owner_id}`` where
   ``roster_id`` is the pick's ORIGINAL team (which determines its draft slot)
   and ``owner_id`` is who holds it now. Reconstructing inventory means
   starting from the defaults and applying those overrides.

2. **Slot projection.** A pick's value depends on where it lands, and where it
   lands depends on how bad its ORIGINAL team is — a rebuilding team's 1st is
   an Early pick and worth far more than a contender's. The old `pick_value.py`
   defaulted everything to "Mid", systematically mispricing both ends. Here we
   project the tier from the originating team's starter strength: weakest third
   of the league → Early, middle → Mid, strongest third → Late.

That projection is *predictive*, not certain — it assumes current strength
predicts final standings. That's a reasonable prior in dynasty and it is far
better than assuming every pick is average, but it should be revisited once
real standings exist deep into a season.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

from sleeper.analytics.base_value import Asset, PickChart

DEFAULT_DRAFT_ROUNDS = 4


@dataclass(frozen=True)
class PickSlot:
    season: str
    rnd: int
    original_roster_id: int   # whose finish sets the slot
    owner_roster_id: int      # who holds it now


def project_slot_tiers(
    strength_by_roster: Mapping[int, int],
) -> dict[int, str]:
    """Map each roster to the pick tier its own picks project into.

    Weakest third of the league picks Early (most valuable), middle third Mid,
    strongest third Late. Ties break by roster_id for determinism.
    """
    if not strength_by_roster:
        return {}
    order = sorted(strength_by_roster.items(), key=lambda kv: (kv[1], kv[0]))
    n = len(order)
    third = max(1, n // 3)
    tiers: dict[int, str] = {}
    for i, (rid, _strength) in enumerate(order):
        if i < third:
            tiers[rid] = "Early"
        elif i < n - third:
            tiers[rid] = "Mid"
        else:
            tiers[rid] = "Late"
    return tiers


def pick_inventory(
    roster_ids: Sequence[int],
    traded_picks: Iterable[Mapping[str, object]],
    seasons: Sequence[str],
    draft_rounds: int = DEFAULT_DRAFT_ROUNDS,
) -> dict[int, list[PickSlot]]:
    """Reconstruct which roster holds which (season, round, original) pick.

    Starts from "everyone owns their own" for the given seasons/rounds, then
    applies the traded-pick overrides.
    """
    owner: dict[tuple[str, int, int], int] = {}
    for season in seasons:
        for rnd in range(1, draft_rounds + 1):
            for rid in roster_ids:
                owner[(season, rnd, rid)] = rid

    for tp in traded_picks:
        season = str(tp.get("season", ""))
        try:
            rnd = int(tp.get("round"))  # type: ignore[call-overload]
            original = int(tp.get("roster_id"))  # type: ignore[call-overload]
            current = int(tp.get("owner_id"))  # type: ignore[call-overload]
        except (TypeError, ValueError):
            continue
        key = (season, rnd, original)
        if key in owner:
            owner[key] = current

    out: dict[int, list[PickSlot]] = {rid: [] for rid in roster_ids}
    for (season, rnd, original), current in owner.items():
        if current in out:
            out[current].append(
                PickSlot(season=season, rnd=rnd,
                         original_roster_id=original, owner_roster_id=current)
            )
    for rid in out:
        out[rid].sort(key=lambda p: (p.season, p.rnd, p.original_roster_id))
    return out


def pick_assets_for_roster(
    slots: Sequence[PickSlot],
    chart: PickChart,
    tier_by_roster: Mapping[int, str],
    owners: Optional[Mapping[int, str]] = None,
) -> list[Asset]:
    """Turn owned pick slots into valued Assets with projected tiers.

    Picks with no chart entry (e.g. seasons beyond KTC's horizon) are dropped
    rather than valued at zero, so they neither inflate nor silently pad a
    roster's capital.
    """
    assets: list[Asset] = []
    for s in slots:
        tier = tier_by_roster.get(s.original_roster_id, "Mid")
        a = chart.pick_asset(s.season, s.rnd, tier)
        if a.base_sf <= 0 and a.base_1qb <= 0:
            continue
        if owners is not None and s.original_roster_id != s.owner_roster_id:
            src = owners.get(s.original_roster_id, f"R{s.original_roster_id}")
            a = Asset(
                kind=a.kind, id=a.id, name=f"{a.name} (via {src})",
                position=a.position, base_sf=a.base_sf, base_1qb=a.base_1qb,
                season=a.season, rnd=a.rnd, tier=a.tier,
            )
        assets.append(a)
    return assets
