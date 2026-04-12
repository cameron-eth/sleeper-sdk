"""Bridge between Sleeper player IDs and nflverse/fantasypros IDs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:
    import nflreadpy as nfl
    HAS_NFLREADPY = True
except ImportError:
    HAS_NFLREADPY = False


@dataclass
class PlayerIds:
    sleeper_id: str
    gsis_id: Optional[str] = None
    fantasypros_id: Optional[int] = None
    espn_id: Optional[int] = None
    yahoo_id: Optional[int] = None
    pfr_id: Optional[str] = None
    sportradar_id: Optional[str] = None
    name: Optional[str] = None
    position: Optional[str] = None
    team: Optional[str] = None


class PlayerIdBridge:
    """Maps Sleeper player IDs to other platform IDs using nflreadpy."""

    def __init__(self) -> None:
        if not HAS_NFLREADPY:
            raise ImportError(
                "nflreadpy is required for the enrichment module. "
                "Install it with: pip install sleeper-sdk[nfl-data]"
            )

        self._by_sleeper: dict[str, PlayerIds] = {}
        self._by_gsis: dict[str, PlayerIds] = {}
        self._by_fantasypros: dict[int, PlayerIds] = {}
        self._loaded = False

    def load(self) -> None:
        """Load the ID mapping from nflreadpy. Call once, reuse the bridge."""
        df = nfl.load_ff_playerids()

        for row in df.iter_rows(named=True):
            sleeper_id = row.get("sleeper_id")
            if sleeper_id is None:
                continue

            sleeper_id = str(int(sleeper_id)) if isinstance(sleeper_id, float) else str(sleeper_id)

            gsis_id = row.get("gsis_id")
            fp_id = row.get("fantasypros_id")
            espn_id = row.get("espn_id")
            yahoo_id = row.get("yahoo_id")
            pfr_id = row.get("pfr_id")
            sr_id = row.get("sportradar_id")

            ids = PlayerIds(
                sleeper_id=sleeper_id,
                gsis_id=gsis_id,
                fantasypros_id=int(fp_id) if fp_id is not None else None,
                espn_id=int(espn_id) if espn_id is not None else None,
                yahoo_id=int(yahoo_id) if yahoo_id is not None else None,
                pfr_id=pfr_id,
                sportradar_id=sr_id,
                name=row.get("name"),
                position=row.get("position"),
                team=row.get("team"),
            )

            self._by_sleeper[sleeper_id] = ids
            if gsis_id:
                self._by_gsis[gsis_id] = ids
            if fp_id is not None:
                self._by_fantasypros[int(fp_id)] = ids

        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def from_sleeper(self, sleeper_id: str) -> Optional[PlayerIds]:
        self._ensure_loaded()
        return self._by_sleeper.get(sleeper_id)

    def from_gsis(self, gsis_id: str) -> Optional[PlayerIds]:
        self._ensure_loaded()
        return self._by_gsis.get(gsis_id)

    def from_fantasypros(self, fp_id: int) -> Optional[PlayerIds]:
        self._ensure_loaded()
        return self._by_fantasypros.get(fp_id)

    def sleeper_to_gsis(self, sleeper_id: str) -> Optional[str]:
        ids = self.from_sleeper(sleeper_id)
        return ids.gsis_id if ids else None

    def gsis_to_sleeper(self, gsis_id: str) -> Optional[str]:
        ids = self.from_gsis(gsis_id)
        return ids.sleeper_id if ids else None

    @property
    def total_mapped(self) -> int:
        self._ensure_loaded()
        return len(self._by_sleeper)
