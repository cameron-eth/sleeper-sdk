"""KeepTradeCut dynasty value integration.

Scrapes KTC player values and matches them to Sleeper player IDs
using fuzzy name matching. Supports both 1QB and Superflex formats.

Includes a 24-hour TTL cache (memory + filesystem) so repeated calls
don't spam keeptradecut.com. A daily cron job can call
``fetch_ktc_values(force_refresh=True)`` to warm the cache.
"""
from __future__ import annotations

import re
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import requests

from sleeper.types.league import League
from sleeper.types.player import Player

KTC_URL = "https://keeptradecut.com/dynasty/trade-database"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://keeptradecut.com/",
}

# Suffixes to strip for name normalization
_NAME_SUFFIXES = re.compile(r"\s+(jr\.?|sr\.?|ii|iii|iv|v)$", re.IGNORECASE)


@dataclass
class KtcPlayer:
    ktc_player_id: int
    player_name: str
    position: str
    team: str
    age: Optional[float] = None
    rookie: bool = False
    value_1qb: int = 0
    value_sf: int = 0
    rank_1qb: int = 0
    rank_sf: int = 0


# ── KTC Cache ──

KTC_CACHE_TTL = 86400  # 24 hours in seconds


class _KtcCache:
    """In-memory + filesystem cache for KTC player values.

    Avoids hitting keeptradecut.com more than once per TTL window.
    The filesystem layer survives process restarts so a cron-warmed
    cache is usable by subsequent SDK calls.
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        ttl: float = KTC_CACHE_TTL,
    ):
        self._memory: list[KtcPlayer] | None = None
        self._memory_ts: float = 0.0
        self._ttl = ttl

        if cache_dir:
            self._cache_dir = Path(cache_dir)
        else:
            import tempfile
            self._cache_dir = Path(tempfile.gettempdir()) / "sleeper_sdk_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _fs_path(self) -> Path:
        return self._cache_dir / "ktc_values.json"

    def _is_fresh(self, ts: float) -> bool:
        return (time.time() - ts) < self._ttl

    def get(self) -> list[KtcPlayer] | None:
        """Return cached KTC players if fresh, else None."""
        # Memory first
        if self._memory is not None and self._is_fresh(self._memory_ts):
            return self._memory

        # Filesystem fallback
        if self._fs_path.exists():
            try:
                raw = json.loads(self._fs_path.read_text())
                cached_at = raw.get("_cached_at", 0)
                if self._is_fresh(cached_at):
                    players = [KtcPlayer(**p) for p in raw.get("players", [])]
                    self._memory = players
                    self._memory_ts = cached_at
                    return players
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        return None

    def set(self, players: list[KtcPlayer]) -> None:
        """Store KTC players in memory and on disk."""
        now = time.time()
        self._memory = players
        self._memory_ts = now

        data = {
            "_cached_at": now,
            "players": [asdict(p) for p in players],
        }
        self._fs_path.write_text(json.dumps(data))

    def clear(self) -> None:
        """Invalidate cache (memory + disk)."""
        self._memory = None
        self._memory_ts = 0.0
        self._fs_path.unlink(missing_ok=True)


# Module-level singleton — shared across all calls in the same process
_ktc_cache = _KtcCache()


def fetch_ktc_values(
    force_refresh: bool = False,
    cache_dir: str | Path | None = None,
    ttl: float | None = None,
) -> list[KtcPlayer]:
    """Scrape current KTC dynasty values from keeptradecut.com.

    Results are cached for 24 hours (memory + filesystem) so repeated
    calls don't hit the website. A daily cron job can call with
    ``force_refresh=True`` to warm the cache.

    KTC embeds a ``playersArray`` JavaScript variable in the HTML
    containing all player values for different scoring formats.

    Args:
        force_refresh: If True, bypass the cache and scrape fresh data.
        cache_dir: Custom directory for the filesystem cache.
                   Defaults to ``$TMPDIR/sleeper_sdk_cache/``.
        ttl: Custom cache TTL in seconds. Defaults to 86400 (24 hours).

    Returns:
        List of KtcPlayer with values for both 1QB and Superflex.

    Raises:
        ValueError: If the playersArray cannot be found in the HTML.
        requests.RequestException: On network errors.
    """
    global _ktc_cache

    # Allow caller to override cache settings
    if cache_dir is not None or ttl is not None:
        _ktc_cache = _KtcCache(
            cache_dir=cache_dir,
            ttl=ttl if ttl is not None else KTC_CACHE_TTL,
        )

    # Check cache first (unless forcing refresh)
    if not force_refresh:
        cached = _ktc_cache.get()
        if cached is not None:
            return cached

    # Scrape from KTC
    session = requests.Session()
    session.headers.update(_HEADERS)
    resp = session.get(KTC_URL, timeout=30)
    resp.raise_for_status()

    pattern = r"var\s+playersArray\s*=\s*(\[.*?\]);"
    match = re.search(pattern, resp.text, re.DOTALL)
    if not match:
        raise ValueError(
            "Could not find playersArray in KTC HTML. "
            "The site structure may have changed."
        )

    raw = json.loads(match.group(1))
    players: list[KtcPlayer] = []
    for p in raw:
        players.append(KtcPlayer(
            ktc_player_id=p.get("playerID", 0),
            player_name=p.get("playerName", ""),
            position=p.get("position", ""),
            team=p.get("team", "") or "",
            age=p.get("age"),
            rookie=bool(p.get("rookie", False)),
            value_1qb=_nested_int(p, "oneQBValues", "value"),
            value_sf=_nested_int(p, "superflexValues", "value"),
            rank_1qb=_nested_int(p, "oneQBValues", "rank"),
            rank_sf=_nested_int(p, "superflexValues", "rank"),
        ))

    # Store in cache
    _ktc_cache.set(players)

    return players


def clear_ktc_cache() -> None:
    """Manually invalidate the KTC cache (memory + filesystem)."""
    _ktc_cache.clear()


def match_ktc_to_sleeper(
    ktc_players: list[KtcPlayer],
    sleeper_players: dict[str, Player],
) -> dict[str, KtcPlayer]:
    """Match KTC players to Sleeper player IDs using fuzzy name matching.

    Matching strategy (in order of priority):
    1. Exact normalized name + position + team
    2. Exact normalized name + position (team may differ due to trades)
    3. Exact normalized name only (fallback for edge cases)

    Args:
        ktc_players: KTC player list from :func:`fetch_ktc_values`.
        sleeper_players: Sleeper player dict keyed by player_id.

    Returns:
        Dict mapping sleeper_id -> KtcPlayer for all matched players.
    """
    # Build lookup indexes from Sleeper players
    # Key: (normalized_name, position, team) -> sleeper_id
    by_name_pos_team: dict[tuple[str, str, str], str] = {}
    by_name_pos: dict[tuple[str, str], str] = {}
    by_name: dict[str, str] = {}

    for sid, sp in sleeper_players.items():
        name = _normalize_name(sp.full_name or f"{sp.first_name} {sp.last_name}")
        pos = (sp.position or "").upper()
        team = (sp.team or "").upper()

        key_full = (name, pos, team)
        key_pos = (name, pos)

        if key_full not in by_name_pos_team:
            by_name_pos_team[key_full] = sid
        if key_pos not in by_name_pos:
            by_name_pos[key_pos] = sid
        if name not in by_name:
            by_name[name] = sid

    result: dict[str, KtcPlayer] = {}

    for kp in ktc_players:
        name = _normalize_name(kp.player_name)
        pos = kp.position.upper()
        team = kp.team.upper()

        # Try most specific match first
        sid = by_name_pos_team.get((name, pos, team))
        if sid is None:
            sid = by_name_pos.get((name, pos))
        if sid is None:
            sid = by_name.get(name)

        if sid is not None and sid not in result:
            result[sid] = kp

    return result


def get_ktc_values(
    sleeper_players: dict[str, Player],
    scoring_type: Optional[str] = None,
    force_refresh: bool = False,
) -> dict[str, int]:
    """Convenience function: scrape KTC and return sleeper_id -> KTC value.

    Uses the 24-hour cache by default. Pass ``force_refresh=True`` to
    bypass the cache and scrape fresh data.

    Args:
        sleeper_players: Sleeper player dict from ``client.get_all_players()``.
        scoring_type: ``"sf"`` for superflex or ``"1qb"`` for one-QB.
                      Defaults to ``"sf"``.
        force_refresh: If True, bypass the cache.

    Returns:
        Dict mapping sleeper_id to the integer KTC value.
    """
    if scoring_type is None:
        scoring_type = "sf"

    ktc_players = fetch_ktc_values(force_refresh=force_refresh)
    matched = match_ktc_to_sleeper(ktc_players, sleeper_players)

    values: dict[str, int] = {}
    for sid, kp in matched.items():
        if scoring_type == "sf":
            values[sid] = kp.value_sf
        else:
            values[sid] = kp.value_1qb

    return values


def detect_scoring_type(league: League) -> str:
    """Detect whether a league is Superflex or 1QB.

    Checks ``league.roster_positions`` for a ``SUPER_FLEX`` slot.

    Args:
        league: A League object with roster_positions populated.

    Returns:
        ``"sf"`` if superflex, ``"1qb"`` otherwise.
    """
    if league.roster_positions:
        for pos in league.roster_positions:
            if pos.upper() == "SUPER_FLEX":
                return "sf"
    return "1qb"


# ── Internal helpers ──


def _normalize_name(name: str) -> str:
    """Normalize a player name for matching.

    Lowercases, strips suffixes (Jr., III, etc.), removes non-alpha chars
    except spaces, and collapses whitespace.
    """
    n = name.strip().lower()
    n = _NAME_SUFFIXES.sub("", n)
    n = re.sub(r"[^a-z\s]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _nested_int(obj: dict, *keys) -> int:
    """Safely navigate nested dict keys, returning 0 if missing."""
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return 0
        cur = cur.get(k)
    return int(cur) if cur is not None else 0
