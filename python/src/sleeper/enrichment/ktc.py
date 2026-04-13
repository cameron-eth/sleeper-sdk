"""KTC (KeepTradeCut) integration: scrape values, analyze trades, compute market price."""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from typing import Optional

import httpx

from sleeper.cache.ktc_cache import KTCCache
from sleeper.types.player import Player

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KTC_DYNASTY_RANKINGS_URL = "https://keeptradecut.com/dynasty-rankings"
KTC_TRADE_DATABASE_URL = "https://keeptradecut.com/dynasty/trade-database"

_PLAYERS_ARRAY_RE = re.compile(r"var\s+playersArray\s*=\s*(\[.*?\]);\s*\n", re.DOTALL)
_TRADES_VAR_RE = re.compile(r"var\s+trades\s*=\s*(\[.*?\]);\s*\n", re.DOTALL)

_VALUE_FLOOR = 100  # floor for unranked assets to avoid div-by-zero
_KTC_TIMEOUT = 30.0

_TEAM_ALIASES: dict[str, str] = {
    "JAC": "JAX", "JAG": "JAX",
    "WSH": "WAS",
    "LVR": "LV", "LAS": "LV",
    "SFO": "SF",
    "TBB": "TB",
    "NOS": "NO",
    "NEP": "NE",
    "GBP": "GB",
    "KCC": "KC",
}

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class KTCPlayerValue:
    """Value data for one scoring format."""
    value: int = 0
    rank: int = 0
    positional_rank: int = 0
    overall_trend: int = 0
    positional_7day_trend: int = 0


@dataclass
class KTCPlayer:
    """A player/pick entry from KTC's playersArray."""
    ktc_id: str
    player_name: str = ""
    slug: str = ""
    position: str = ""
    team: str = ""
    age: Optional[float] = None
    one_qb: KTCPlayerValue = field(default_factory=KTCPlayerValue)
    superflex: KTCPlayerValue = field(default_factory=KTCPlayerValue)
    mfl_id: Optional[str] = None
    sleeper_id: Optional[str] = None


@dataclass
class KTCTradeSide:
    """One side of a KTC trade."""
    place: int = 0
    player_ids: list[str] = field(default_factory=list)
    player_names: list[str] = field(default_factory=list)


@dataclass
class KTCTradeSettings:
    """League settings for a KTC trade."""
    league_id: Optional[str] = None
    teams: Optional[int] = None
    qbs: Optional[int] = None
    ppr: Optional[int] = None
    tep: Optional[int] = None


@dataclass
class KTCTrade:
    """A trade from KTC's trade database."""
    trade_id: str
    date: str = ""
    side_one: KTCTradeSide = field(default_factory=KTCTradeSide)
    side_two: KTCTradeSide = field(default_factory=KTCTradeSide)
    settings: KTCTradeSettings = field(default_factory=KTCTradeSettings)


@dataclass
class TradeDetail:
    """One trade's breakdown for the market value report."""
    trade_id: str
    date: str = ""
    player_side: list[str] = field(default_factory=list)
    other_side: list[str] = field(default_factory=list)
    player_side_total_ktc: int = 0
    other_side_total_ktc: int = 0
    surplus: int = 0
    implied_price: int = 0
    is_solo: bool = False


@dataclass
class MarketValueReport:
    """Complete market value analysis for a player."""
    player_name: str
    position: str = ""
    team: str = ""
    ktc_id: str = ""
    ktc_value: int = 0
    format: str = "sf"
    implied_market_values: list[int] = field(default_factory=list)
    median_market_value: Optional[int] = None
    mean_market_value: Optional[int] = None
    num_trades: int = 0
    pct_of_ktc: Optional[float] = None
    trades: list[TradeDetail] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_cache: KTCCache | None = None


def _get_cache() -> KTCCache:
    global _cache
    if _cache is None:
        _cache = KTCCache()
    return _cache


def _fetch_page(url: str, params: dict[str, str] | None = None) -> str:
    # Build full URL with params for curl fallback
    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"

    try:
        with httpx.Client(timeout=_KTC_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; sleeper-sdk/0.1.0)"},
            )
            resp.raise_for_status()
            return resp.text
    except (httpx.ConnectError, httpx.ConnectTimeout):
        # Fall back to curl (handles SSL on systems with outdated Python SSL)
        import subprocess
        result = subprocess.run(
            ["curl", "-s", "-L", "--max-time", str(int(_KTC_TIMEOUT)),
             "-H", "User-Agent: Mozilla/5.0 (compatible; sleeper-sdk/0.1.0)", url],
            capture_output=True, text=True, timeout=int(_KTC_TIMEOUT) + 5,
        )
        if result.returncode != 0:
            raise RuntimeError(f"curl failed for {url}: {result.stderr}")
        return result.stdout


def _extract_js_var(html: str, pattern: re.Pattern[str]) -> list[dict]:
    match = pattern.search(html)
    if not match:
        return []
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return []


def _normalize_name(name: str) -> str:
    name = name.lower().strip()
    for suffix in [" jr.", " sr.", " jr", " sr", " iii", " ii", " iv", " v"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    name = re.sub(r"[^a-z\s]", "", name)
    return re.sub(r"\s+", " ", name).strip()


def _normalize_team(team: str) -> str:
    t = team.upper().strip()
    return _TEAM_ALIASES.get(t, t)


def _classify_pick_tier(pick_number: int, total_teams: int = 12) -> str:
    third = total_teams / 3
    if pick_number <= third:
        return "Early"
    elif pick_number <= 2 * third:
        return "Mid"
    return "Late"


def _parse_pick_string(pick_str: str) -> tuple[str, int, int] | None:
    match = re.match(r"(\d{4})\s+Pick\s+(\d+)\.(\d+)", pick_str)
    if match:
        return match.group(1), int(match.group(2)), int(match.group(3))
    return None


def _get_pick_ktc_value(
    pick_str: str,
    ktc_by_name: dict[str, KTCPlayer],
    fmt: str,
    total_teams: int = 12,
) -> int:
    parsed = _parse_pick_string(pick_str)
    if parsed is None:
        return _VALUE_FLOOR

    year, rnd, pick_in_round = parsed
    tier = _classify_pick_tier(pick_in_round, total_teams)
    rnd_suffix = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}.get(rnd, f"{rnd}th")

    # Try exact pattern "2026 Mid 1st"
    target = f"{year} {tier} {rnd_suffix}"
    target_norm = _normalize_name(target)

    for name, kp in ktc_by_name.items():
        if _normalize_name(name) == target_norm:
            val = kp.superflex.value if fmt == "sf" else kp.one_qb.value
            return val if val > 0 else _VALUE_FLOOR

    # Fuzzy fallback: year + tier + round anywhere in name
    for name, kp in ktc_by_name.items():
        name_lower = name.lower()
        if year in name_lower and tier.lower() in name_lower and rnd_suffix.lower() in name_lower:
            val = kp.superflex.value if fmt == "sf" else kp.one_qb.value
            return val if val > 0 else _VALUE_FLOOR

    return _VALUE_FLOOR


# ---------------------------------------------------------------------------
# Serialization helpers (for caching)
# ---------------------------------------------------------------------------

def _ktc_player_to_dict(p: KTCPlayer) -> dict:
    return {
        "ktc_id": p.ktc_id, "player_name": p.player_name, "slug": p.slug,
        "position": p.position, "team": p.team, "age": p.age,
        "oqb_v": p.one_qb.value, "oqb_r": p.one_qb.rank,
        "oqb_pr": p.one_qb.positional_rank, "oqb_t": p.one_qb.overall_trend,
        "oqb_7d": p.one_qb.positional_7day_trend,
        "sf_v": p.superflex.value, "sf_r": p.superflex.rank,
        "sf_pr": p.superflex.positional_rank, "sf_t": p.superflex.overall_trend,
        "sf_7d": p.superflex.positional_7day_trend,
        "mfl_id": p.mfl_id, "sleeper_id": p.sleeper_id,
    }


def _dict_to_ktc_player(d: dict) -> KTCPlayer:
    return KTCPlayer(
        ktc_id=d["ktc_id"], player_name=d.get("player_name", ""),
        slug=d.get("slug", ""), position=d.get("position", ""),
        team=d.get("team", ""), age=d.get("age"),
        one_qb=KTCPlayerValue(
            value=d.get("oqb_v", 0), rank=d.get("oqb_r", 0),
            positional_rank=d.get("oqb_pr", 0), overall_trend=d.get("oqb_t", 0),
            positional_7day_trend=d.get("oqb_7d", 0),
        ),
        superflex=KTCPlayerValue(
            value=d.get("sf_v", 0), rank=d.get("sf_r", 0),
            positional_rank=d.get("sf_pr", 0), overall_trend=d.get("sf_t", 0),
            positional_7day_trend=d.get("sf_7d", 0),
        ),
        mfl_id=d.get("mfl_id"), sleeper_id=d.get("sleeper_id"),
    )


def _ktc_trade_to_dict(t: KTCTrade) -> dict:
    return {
        "trade_id": t.trade_id, "date": t.date,
        "s1_place": t.side_one.place, "s1_pids": t.side_one.player_ids,
        "s2_place": t.side_two.place, "s2_pids": t.side_two.player_ids,
        "league_id": t.settings.league_id, "teams": t.settings.teams,
        "qbs": t.settings.qbs, "ppr": t.settings.ppr, "tep": t.settings.tep,
    }


def _dict_to_ktc_trade(d: dict) -> KTCTrade:
    return KTCTrade(
        trade_id=d["trade_id"], date=d.get("date", ""),
        side_one=KTCTradeSide(place=d.get("s1_place", 0), player_ids=d.get("s1_pids", [])),
        side_two=KTCTradeSide(place=d.get("s2_place", 0), player_ids=d.get("s2_pids", [])),
        settings=KTCTradeSettings(
            league_id=d.get("league_id"), teams=d.get("teams"),
            qbs=d.get("qbs"), ppr=d.get("ppr"), tep=d.get("tep"),
        ),
    )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_ktc_player_entry(entry: dict) -> KTCPlayer | None:
    ktc_id = entry.get("playerID") or entry.get("playerId")
    if ktc_id is None:
        return None

    oqb = entry.get("oneQBValues") or {}
    sf = entry.get("superflexValues") or {}

    return KTCPlayer(
        ktc_id=str(ktc_id),
        player_name=entry.get("playerName", ""),
        slug=entry.get("slug", ""),
        position=entry.get("position", ""),
        team=entry.get("team", ""),
        age=entry.get("age"),
        one_qb=KTCPlayerValue(
            value=oqb.get("value", 0), rank=oqb.get("rank", 0),
            positional_rank=oqb.get("positionalRank", 0),
            overall_trend=oqb.get("overallTrend", 0),
            positional_7day_trend=oqb.get("positional7DayTrend", 0),
        ),
        superflex=KTCPlayerValue(
            value=sf.get("value", 0), rank=sf.get("rank", 0),
            positional_rank=sf.get("positionalRank", 0),
            overall_trend=sf.get("overallTrend", 0),
            positional_7day_trend=sf.get("positional7DayTrend", 0),
        ),
        mfl_id=str(entry["mflid"]) if entry.get("mflid") else None,
    )


def _parse_ktc_trade_entry(entry: dict) -> KTCTrade | None:
    trade_id = entry.get("id")
    if trade_id is None:
        return None

    t1 = entry.get("teamOne") or {}
    t2 = entry.get("teamTwo") or {}
    s = entry.get("settings") or {}

    return KTCTrade(
        trade_id=str(trade_id),
        date=str(entry.get("date", "")),
        side_one=KTCTradeSide(
            place=t1.get("place", 0),
            player_ids=[str(pid) for pid in (t1.get("playerIds") or [])],
        ),
        side_two=KTCTradeSide(
            place=t2.get("place", 0),
            player_ids=[str(pid) for pid in (t2.get("playerIds") or [])],
        ),
        settings=KTCTradeSettings(
            league_id=str(s["id"]) if s.get("id") else None,
            teams=s.get("teams"), qbs=s.get("qBs"),
            ppr=s.get("ppr"), tep=s.get("tep"),
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_ktc_players(force_refresh: bool = False) -> list[KTCPlayer]:
    """Scrape KTC dynasty-rankings page and return player values.

    Results are cached for 1 hour.
    """
    cache = _get_cache()

    if not force_refresh:
        cached = cache.get("players")
        if cached is not None:
            return [_dict_to_ktc_player(d) for d in cached]

    html = _fetch_page(KTC_DYNASTY_RANKINGS_URL)
    raw = _extract_js_var(html, _PLAYERS_ARRAY_RE)

    players = []
    for entry in raw:
        p = _parse_ktc_player_entry(entry)
        if p is not None:
            players.append(p)

    cache.set("players", [_ktc_player_to_dict(p) for p in players])
    return players


def fetch_ktc_trades(force_refresh: bool = False) -> list[KTCTrade]:
    """Scrape KTC trade database page and return recent trades.

    Results are cached for 1 hour.
    """
    cache = _get_cache()

    if not force_refresh:
        cached = cache.get("trades")
        if cached is not None:
            return [_dict_to_ktc_trade(d) for d in cached]

    html = _fetch_page(KTC_TRADE_DATABASE_URL)
    raw = _extract_js_var(html, _TRADES_VAR_RE)

    trades = []
    for entry in raw:
        t = _parse_ktc_trade_entry(entry)
        if t is not None:
            trades.append(t)

    cache.set("trades", [_ktc_trade_to_dict(t) for t in trades])
    return trades


def build_ktc_to_sleeper_map(
    ktc_players: list[KTCPlayer],
    sleeper_players: dict[str, Player],
) -> dict[str, str]:
    """Map KTC player IDs to Sleeper player IDs via name+position+team matching.

    Returns dict of {ktc_id: sleeper_id}.
    """
    # Build Sleeper lookup indices
    exact_index: dict[tuple[str, str, str], str] = {}
    name_pos_index: dict[tuple[str, str], str] = {}

    for pid, player in sleeper_players.items():
        # Prefer full_name (has proper spacing), fall back to first+last
        name = player.full_name or ""
        if not name and player.first_name and player.last_name:
            name = f"{player.first_name} {player.last_name}"
        if not name:
            continue

        norm_name = _normalize_name(name)
        pos = (player.position or "").upper()
        team = _normalize_team(player.team or "")

        key_exact = (norm_name, pos, team)
        key_np = (norm_name, pos)

        if key_exact not in exact_index or player.team:
            exact_index[key_exact] = pid
        if key_np not in name_pos_index or player.team:
            name_pos_index[key_np] = pid

    mapping: dict[str, str] = {}
    for kp in ktc_players:
        if not kp.player_name or "Pick" in kp.player_name:
            continue

        norm = _normalize_name(kp.player_name)
        pos = kp.position.upper()
        team = _normalize_team(kp.team)

        sid = exact_index.get((norm, pos, team))
        if sid is None:
            sid = name_pos_index.get((norm, pos))
        if sid is not None:
            mapping[kp.ktc_id] = sid

    return mapping


def get_player_market_value(
    player_name: str,
    fmt: str = "sf",
    force_refresh: bool = False,
) -> MarketValueReport:
    """Analyze a player's actual market value from KTC trade data.

    Compares their listed KTC value against what they actually trade for,
    using proportional surplus attribution for multi-player trades.

    Args:
        player_name: Player name to search for (fuzzy matched).
        fmt: Scoring format -- "sf" or "1qb".
        force_refresh: Bypass cache.
    """
    ktc_players = fetch_ktc_players(force_refresh=force_refresh)

    # Find target player
    target = _find_ktc_player(player_name, ktc_players)
    if target is None:
        return MarketValueReport(player_name=player_name, format=fmt)

    ktc_by_id: dict[str, KTCPlayer] = {p.ktc_id: p for p in ktc_players}
    ktc_by_name: dict[str, KTCPlayer] = {p.player_name: p for p in ktc_players}
    ktc_value = (target.superflex.value if fmt == "sf" else target.one_qb.value) or 0

    # Fetch trades and filter to this player
    trades = fetch_ktc_trades(force_refresh=force_refresh)
    relevant = [
        t for t in trades
        if target.ktc_id in t.side_one.player_ids or target.ktc_id in t.side_two.player_ids
    ]

    implied_values: list[int] = []
    trade_details: list[TradeDetail] = []

    for trade in relevant:
        # Filter by format: SF trades have qbs >= 2
        is_sf = (trade.settings.qbs or 1) >= 2
        if fmt == "sf" and not is_sf:
            continue
        if fmt == "1qb" and is_sf:
            continue

        detail = _compute_implied_price(
            trade, target.ktc_id, ktc_by_id, ktc_by_name, fmt,
        )
        if detail is not None:
            implied_values.append(detail.implied_price)
            trade_details.append(detail)

    median_val = int(statistics.median(implied_values)) if implied_values else None
    mean_val = int(statistics.mean(implied_values)) if implied_values else None
    pct = round(median_val / ktc_value * 100, 1) if (median_val and ktc_value) else None

    return MarketValueReport(
        player_name=target.player_name,
        position=target.position,
        team=target.team,
        ktc_id=target.ktc_id,
        ktc_value=ktc_value,
        format=fmt,
        implied_market_values=implied_values,
        median_market_value=median_val,
        mean_market_value=mean_val,
        num_trades=len(trade_details),
        pct_of_ktc=pct,
        trades=trade_details,
    )


# ---------------------------------------------------------------------------
# Internal computation
# ---------------------------------------------------------------------------

def _find_ktc_player(name: str, ktc_players: list[KTCPlayer]) -> KTCPlayer | None:
    norm = _normalize_name(name)

    for p in ktc_players:
        if _normalize_name(p.player_name) == norm:
            return p

    for p in ktc_players:
        pn = _normalize_name(p.player_name)
        if norm in pn or pn in norm:
            return p

    return None


def _resolve_asset_value(
    asset_id: str,
    ktc_by_id: dict[str, KTCPlayer],
    ktc_by_name: dict[str, KTCPlayer],
    fmt: str,
) -> int:
    """Get KTC value for a player ID or pick string."""
    # Try as a player ID first
    player = ktc_by_id.get(asset_id)
    if player:
        val = player.superflex.value if fmt == "sf" else player.one_qb.value
        return val if val and val > 0 else _VALUE_FLOOR

    # Try as a pick string (e.g., "2026 Pick 1.08")
    if "Pick" in asset_id:
        return _get_pick_ktc_value(asset_id, ktc_by_name, fmt)

    # Try as a named pick in playersArray (e.g., "2027 Mid 1st (RDP, FA)")
    for name, kp in ktc_by_name.items():
        if asset_id in name or name in asset_id:
            val = kp.superflex.value if fmt == "sf" else kp.one_qb.value
            return val if val and val > 0 else _VALUE_FLOOR

    return _VALUE_FLOOR


def _resolve_asset_name(asset_id: str, ktc_by_id: dict[str, KTCPlayer]) -> str:
    player = ktc_by_id.get(asset_id)
    if player:
        return player.player_name
    return asset_id  # picks are already human-readable strings


def _compute_implied_price(
    trade: KTCTrade,
    target_ktc_id: str,
    ktc_by_id: dict[str, KTCPlayer],
    ktc_by_name: dict[str, KTCPlayer],
    fmt: str,
) -> TradeDetail | None:
    """Compute implied market price via proportional surplus attribution.

    player_weight = player_ktc_value / player_side_total
    implied_price = player_ktc_value + (player_weight * surplus)
    """
    if target_ktc_id in trade.side_one.player_ids:
        player_side, other_side = trade.side_one, trade.side_two
    elif target_ktc_id in trade.side_two.player_ids:
        player_side, other_side = trade.side_two, trade.side_one
    else:
        return None

    player_side_total = sum(
        _resolve_asset_value(pid, ktc_by_id, ktc_by_name, fmt)
        for pid in player_side.player_ids
    )
    other_side_total = sum(
        _resolve_asset_value(pid, ktc_by_id, ktc_by_name, fmt)
        for pid in other_side.player_ids
    )

    if player_side_total == 0:
        return None

    target_value = _resolve_asset_value(target_ktc_id, ktc_by_id, ktc_by_name, fmt)
    surplus = other_side_total - player_side_total
    player_weight = target_value / player_side_total
    implied_price = int(target_value + (player_weight * surplus))

    player_side_names = [_resolve_asset_name(pid, ktc_by_id) for pid in player_side.player_ids]
    other_side_names = [_resolve_asset_name(pid, ktc_by_id) for pid in other_side.player_ids]

    return TradeDetail(
        trade_id=trade.trade_id,
        date=trade.date,
        player_side=player_side_names,
        other_side=other_side_names,
        player_side_total_ktc=player_side_total,
        other_side_total_ktc=other_side_total,
        surplus=surplus,
        implied_price=max(implied_price, 0),
        is_solo=len(player_side.player_ids) == 1,
    )
