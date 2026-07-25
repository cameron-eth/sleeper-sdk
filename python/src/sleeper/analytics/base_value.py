"""L0 — Base asset value (the context-free primitive).

This is the bottom layer of the trade-intelligence stack. It answers one
question with no league context: *what is this asset objectively worth?*

Design decision (backed by the KTC data, 2026-07-25):

    KTC value ALREADY prices age in. Peak values live at young ages; the
    old players still in KTC's top-500 are elite survivors, so a
    cross-sectional "market age curve" can't be cleanly fit (survivorship
    bias) and — more importantly — multiplying KTC by an age discount here
    would DOUBLE-COUNT age.

    Therefore L0 does NOT apply any age multiplier. Base value is the raw
    KTC number (format-selected) for players and a slot-tiered chart value
    for picks. Age and position ride along as METADATA for the layers above.

The age-vs-window interaction ("a rebuilder discounts age more than the
market does; a contender pays up for win-now production") is a *behavioral
re-weighting of the market price* and lives at L2 (contextual value), not
here. Keeping the primitive thin is deliberate: all the intelligence is in
the league model (L1) and the window re-weighting (L2).

Everything here is a pure function of explicit inputs, so it can be
validated numerically in isolation (see tests/test_base_value.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal, Mapping, Optional

PLAYER_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")
PICK_TIERS: tuple[str, ...] = ("Early", "Mid", "Late")

# KTC encodes picks as RDP rows named e.g. "2027 Mid 1st".
_PICK_NAME_RE = re.compile(r"^(\d{4})\s+(Early|Mid|Late)\s+(\d+)(?:st|nd|rd|th)$")

# Tier fallback order when a requested tier is absent from the chart. "Mid"
# is the neutral default for a pick of unknown slot; Early over-values a
# late pick and Late under-values an early one.
_TIER_FALLBACK: tuple[str, ...] = ("Mid", "Early", "Late")


def normalize_age(age: object) -> Optional[float]:
    """Coerce a KTC age field to a real age or None.

    KTC uses -1 (and occasionally 0) as a sentinel for rookies / unknown
    age. Those become None so upstream layers never treat a sentinel as a
    literal age.
    """
    if age is None:
        return None
    try:
        a = float(age)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if a <= 0:
        return None
    return a


@dataclass(frozen=True)
class Asset:
    """A tradeable asset — a player or a draft pick — with its base value.

    base_sf / base_1qb are the raw KTC values (or pick-chart values). They
    are NOT age-adjusted; `age` is carried separately as metadata.
    """
    kind: Literal["player", "pick"]
    id: str
    name: str
    position: str                          # QB/RB/WR/TE for players, "PICK" for picks
    base_sf: int
    base_1qb: int
    age: Optional[float] = None            # None for picks / unknown-age players
    pos_rank_sf: Optional[int] = None
    pos_rank_1qb: Optional[int] = None
    # pick-only metadata
    season: Optional[str] = None
    rnd: Optional[int] = None
    tier: Optional[str] = None

    @property
    def is_pick(self) -> bool:
        return self.kind == "pick"

    def base_value(self, fmt: str = "sf") -> int:
        """Objective base value for the given format ('sf' or '1qb')."""
        return self.base_sf if fmt == "sf" else self.base_1qb

    def with_age(self, age: object) -> "Asset":
        """Return a copy with `age` filled in if this Asset lacks one.

        KTC omits age for many rookies (the field arrives as the -1 sentinel),
        which would otherwise leave them looking prime-aged/neutral to the
        layers above — badly understating young assets on a rebuilding roster.
        Sleeper's player data has real ages, so callers backfill from there.
        An existing age is never overwritten; KTC's decimal age is more precise
        than Sleeper's integer.
        """
        if self.age is not None:
            return self
        a = normalize_age(age)
        if a is None:
            return self
        return replace(self, age=a)


# ---------------------------------------------------------------------------
# Player construction
# ---------------------------------------------------------------------------


def player_asset(
    *,
    ktc_id: str,
    name: str,
    position: str,
    sf_value: int,
    oqb_value: int,
    age: object = None,
    sf_pos_rank: Optional[int] = None,
    oqb_pos_rank: Optional[int] = None,
) -> Asset:
    """Build a player Asset from explicit KTC fields."""
    return Asset(
        kind="player",
        id=str(ktc_id),
        name=name,
        position=position.upper(),
        base_sf=int(sf_value),
        base_1qb=int(oqb_value),
        age=normalize_age(age),
        pos_rank_sf=sf_pos_rank,
        pos_rank_1qb=oqb_pos_rank,
    )


def asset_from_ktc_dict(rec: Mapping[str, object]) -> Asset:
    """Build an Asset from a raw KTC snapshot record (data/ktc/*.json shape).

    RDP records become pick Assets; everything else is a player Asset.
    """
    position = str(rec.get("position", "")).upper()
    if position == "RDP":
        parsed = _parse_pick_name(str(rec.get("name", "")))
        season, tier, rnd = parsed if parsed else (None, None, None)
        return Asset(
            kind="pick",
            id=str(rec.get("ktc_id", rec.get("name", ""))),
            name=str(rec.get("name", "")),
            position="PICK",
            base_sf=_to_int(rec.get("sf_value")),
            base_1qb=_to_int(rec.get("oqb_value")),
            season=season,
            rnd=rnd,
            tier=tier,
        )
    return player_asset(
        ktc_id=str(rec.get("ktc_id", "")),
        name=str(rec.get("name", "")),
        position=position,
        sf_value=_to_int(rec.get("sf_value")),
        oqb_value=_to_int(rec.get("oqb_value")),
        age=rec.get("age"),
        sf_pos_rank=_opt_int(rec.get("sf_pos_rank")),
        oqb_pos_rank=_opt_int(rec.get("oqb_pos_rank")),
    )


# ---------------------------------------------------------------------------
# Pick chart
# ---------------------------------------------------------------------------


def _parse_pick_name(name: str) -> Optional[tuple[str, str, int]]:
    """'2027 Mid 1st' -> ('2027', 'Mid', 1); None if it doesn't parse."""
    m = _PICK_NAME_RE.match(name.strip())
    if not m:
        return None
    return m.group(1), m.group(2), int(m.group(3))


def _to_int(v: object, default: int = 0) -> int:
    """Coerce a JSON-sourced value (int/float/str) to int, else default."""
    try:
        return int(v)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default


def _opt_int(v: object) -> Optional[int]:
    try:
        return int(v)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


class PickChart:
    """Slot-tiered draft-pick value table, built from KTC RDP rows.

    Keys are (season, round, tier); values are (sf, 1qb). Determining which
    *tier* a given team's pick will land in (from standings) is an L1
    concern — this chart only maps a resolved slot-tier to a value.
    """

    def __init__(self, table: Mapping[tuple[str, int, str], tuple[int, int]]):
        self._table = dict(table)

    @classmethod
    def from_ktc_records(cls, records: list[Mapping[str, object]]) -> "PickChart":
        table: dict[tuple[str, int, str], tuple[int, int]] = {}
        for rec in records:
            if str(rec.get("position", "")).upper() != "RDP":
                continue
            parsed = _parse_pick_name(str(rec.get("name", "")))
            if not parsed:
                continue
            season, tier, rnd = parsed
            table[(season, rnd, tier)] = (
                _to_int(rec.get("sf_value")),
                _to_int(rec.get("oqb_value")),
            )
        return cls(table)

    def value(
        self,
        season: str,
        rnd: int,
        tier: str = "Mid",
        fmt: str = "sf",
    ) -> int:
        """Value of a (season, round, tier) pick. 0 if unknown.

        If the exact tier is missing, fall back Mid -> Early -> Late so a
        pick of unspecified slot still resolves to a sensible value.
        """
        order = [tier] + [t for t in _TIER_FALLBACK if t != tier]
        for t in order:
            hit = self._table.get((season, rnd, t))
            if hit is not None:
                return hit[0] if fmt == "sf" else hit[1]
        return 0

    def pick_asset(
        self,
        season: str,
        rnd: int,
        tier: str = "Mid",
    ) -> Asset:
        """Build a pick Asset at a resolved slot-tier."""
        sf = self.value(season, rnd, tier, "sf")
        oqb = self.value(season, rnd, tier, "1qb")
        ordinal = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(rnd, f"{rnd}th")
        return Asset(
            kind="pick",
            id=f"{season}-{rnd}-{tier}",
            name=f"{season} {tier} {ordinal}",
            position="PICK",
            base_sf=sf,
            base_1qb=oqb,
            season=season,
            rnd=rnd,
            tier=tier,
        )

    def __len__(self) -> int:
        return len(self._table)
