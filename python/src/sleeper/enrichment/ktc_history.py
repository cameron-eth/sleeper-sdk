"""Read KTC snapshot files written by scripts/snapshot_ktc.py.

Snapshots live at `data/ktc/YYYY-MM-DD.json` relative to the repo root.
This module is read-only: it parses the files and exposes helpers for
looking up a player's value over time.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional


DEFAULT_SNAPSHOT_DIR = Path("data/ktc")


@dataclass
class ValuePoint:
    date: str  # YYYY-MM-DD
    sf_value: int
    sf_rank: int
    oqb_value: int
    oqb_rank: int


@dataclass
class PlayerTrend:
    ktc_id: str
    name: str
    position: str
    team: str
    points: list[ValuePoint]  # chronological, oldest first

    def first(self) -> Optional[ValuePoint]:
        return self.points[0] if self.points else None

    def last(self) -> Optional[ValuePoint]:
        return self.points[-1] if self.points else None

    def delta(self, fmt: str = "sf") -> Optional[int]:
        if len(self.points) < 2:
            return None
        attr = "sf_value" if fmt == "sf" else "oqb_value"
        return getattr(self.points[-1], attr) - getattr(self.points[0], attr)


def _snapshot_files(snapshot_dir: Path) -> list[Path]:
    if not snapshot_dir.exists():
        return []
    files = []
    for p in snapshot_dir.glob("*.json"):
        if p.name == "latest.json":
            continue
        # must look like YYYY-MM-DD.json
        try:
            datetime.strptime(p.stem, "%Y-%m-%d")
        except ValueError:
            continue
        files.append(p)
    files.sort(key=lambda p: p.stem)
    return files


def list_snapshot_dates(snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR) -> list[str]:
    """Return all available snapshot dates, oldest first."""
    return [p.stem for p in _snapshot_files(Path(snapshot_dir))]


def load_snapshot(date_str: str, snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR) -> dict:
    """Load one snapshot by date string (YYYY-MM-DD)."""
    path = Path(snapshot_dir) / f"{date_str}.json"
    return json.loads(path.read_text())


def load_player_history(
    lookup: str,
    snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR,
    days: Optional[int] = None,
) -> Optional[PlayerTrend]:
    """Build a trend for a single player.

    Args:
        lookup: ktc_id or player name (case-insensitive substring match on name).
        snapshot_dir: directory holding YYYY-MM-DD.json files.
        days: if set, only include the most recent N days of snapshots.

    Returns:
        PlayerTrend, or None if no snapshots matched the player.
    """
    files = _snapshot_files(Path(snapshot_dir))
    if days is not None:
        cutoff = date.today() - timedelta(days=days)
        files = [
            f for f in files
            if datetime.strptime(f.stem, "%Y-%m-%d").date() >= cutoff
        ]

    lookup_lower = lookup.lower()
    resolved_id: Optional[str] = None
    name = position = team = ""
    points: list[ValuePoint] = []

    for f in files:
        snapshot = json.loads(f.read_text())
        for row in snapshot.get("players", []):
            if resolved_id is None:
                if str(row.get("ktc_id")) == lookup or lookup_lower in (row.get("name") or "").lower():
                    resolved_id = str(row.get("ktc_id"))
                    name = row.get("name", "")
                    position = row.get("position", "")
                    team = row.get("team", "")
            if resolved_id is not None and str(row.get("ktc_id")) == resolved_id:
                points.append(ValuePoint(
                    date=snapshot.get("date", f.stem),
                    sf_value=int(row.get("sf_value") or 0),
                    sf_rank=int(row.get("sf_rank") or 0),
                    oqb_value=int(row.get("oqb_value") or 0),
                    oqb_rank=int(row.get("oqb_rank") or 0),
                ))
                # keep the newest name/team in case they change mid-history
                name = row.get("name", name)
                team = row.get("team", team)
                break

    if not resolved_id or not points:
        return None

    return PlayerTrend(
        ktc_id=resolved_id,
        name=name,
        position=position,
        team=team,
        points=points,
    )


def top_movers(
    fmt: str = "sf",
    days: int = 7,
    min_value: int = 2000,
    limit: int = 20,
    snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR,
) -> list[tuple[PlayerTrend, int]]:
    """Return (trend, delta) for the biggest movers in the window.

    Only players whose latest value >= min_value are considered (filters out
    deep-bench noise). Sorted by absolute delta descending.
    """
    files = _snapshot_files(Path(snapshot_dir))
    if not files:
        return []

    cutoff = date.today() - timedelta(days=days)
    windowed = [
        f for f in files
        if datetime.strptime(f.stem, "%Y-%m-%d").date() >= cutoff
    ]
    if len(windowed) < 2:
        return []

    first_snapshot = json.loads(windowed[0].read_text())
    last_snapshot = json.loads(windowed[-1].read_text())

    attr = "sf_value" if fmt == "sf" else "oqb_value"
    rank_attr = "sf_rank" if fmt == "sf" else "oqb_rank"

    first_by_id = {str(r["ktc_id"]): r for r in first_snapshot.get("players", [])}

    movers: list[tuple[PlayerTrend, int]] = []
    for row in last_snapshot.get("players", []):
        kid = str(row.get("ktc_id"))
        last_val = int(row.get(attr) or 0)
        if last_val < min_value:
            continue
        prev = first_by_id.get(kid)
        if not prev:
            continue
        first_val = int(prev.get(attr) or 0)
        delta = last_val - first_val
        if delta == 0:
            continue

        trend = PlayerTrend(
            ktc_id=kid,
            name=row.get("name", ""),
            position=row.get("position", ""),
            team=row.get("team", ""),
            points=[
                ValuePoint(
                    date=first_snapshot.get("date", windowed[0].stem),
                    sf_value=int(prev.get("sf_value") or 0),
                    sf_rank=int(prev.get("sf_rank") or 0),
                    oqb_value=int(prev.get("oqb_value") or 0),
                    oqb_rank=int(prev.get("oqb_rank") or 0),
                ),
                ValuePoint(
                    date=last_snapshot.get("date", windowed[-1].stem),
                    sf_value=int(row.get("sf_value") or 0),
                    sf_rank=int(row.get("sf_rank") or 0),
                    oqb_value=int(row.get("oqb_value") or 0),
                    oqb_rank=int(row.get("oqb_rank") or 0),
                ),
            ],
        )
        movers.append((trend, delta))

    movers.sort(key=lambda t: abs(t[1]), reverse=True)
    return movers[:limit]
