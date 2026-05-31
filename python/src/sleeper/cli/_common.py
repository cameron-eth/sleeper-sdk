"""Shared helpers used across CLI command modules.

Single source of truth for table rendering, league resolution, roster +
player fetch, KTC ↔ sleeper_id mapping, and the small KTC accessor
helpers. Every command module imports from here — DRY by convention.
"""
from __future__ import annotations

import sys


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "(no data)"
    col_widths = [
        max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    lines = [
        "  ".join(h.ljust(w) for h, w in zip(headers, col_widths)),
        "  ".join("-" * w for w in col_widths),
    ]
    for row in rows:
        lines.append("  ".join(str(v).ljust(w) for v, w in zip(row, col_widths)))
    return "\n".join(lines)


def _resolve_league(username: str, league_filter: str | None) -> tuple:
    """Fetch user + leagues and resolve to a single league. Returns (user, league)."""
    import asyncio
    from datetime import datetime
    from sleeper.client import SleeperClient

    season = str(datetime.now().year)

    async def _fetch_all():
        async with SleeperClient() as client:
            user = await client.users.get_user(username)
            leagues = await client.leagues.get_leagues_for_user(user.user_id, "nfl", season)
            return user, leagues

    print(f"Fetching data for '{username}'...")
    try:
        user, leagues = asyncio.run(_fetch_all())
    except Exception:
        print(f"User '{username}' not found.")
        sys.exit(1)

    if not leagues:
        print(f"No leagues found for '{username}'.")
        sys.exit(1)

    if league_filter:
        matched = [lg for lg in leagues if league_filter.lower() in (lg.name or "").lower()]
        if not matched:
            print(f"No league matching '{league_filter}'. Available:")
            for lg in leagues:
                print(f"  - {lg.name}")
            sys.exit(1)
        league = matched[0]
    elif len(leagues) == 1:
        league = leagues[0]
    else:
        print("Multiple leagues found. Use --league to pick one:")
        for lg in leagues:
            print(f"  - {lg.name}")
        sys.exit(1)

    return user, league


def _fetch_roster_and_players(league_id: str) -> tuple:
    """Fetch rosters + all players for a league. Returns (rosters, sleeper_players)."""
    import asyncio
    from sleeper.client import SleeperClient

    async def _fetch():
        async with SleeperClient() as client:
            rosters = await client.leagues.get_rosters(league_id)
            sleeper_players = await client.get_all_players()
            return rosters, sleeper_players

    return asyncio.run(_fetch())


def _build_sleeper_to_ktc(ktc_players, sleeper_players) -> dict:
    """Build sleeper_id -> KTCPlayer reverse map."""
    from sleeper.enrichment.ktc import build_ktc_to_sleeper_map
    ktc_map = build_ktc_to_sleeper_map(ktc_players, sleeper_players)
    ktc_by_id = {p.ktc_id: p for p in ktc_players}
    sleeper_to_ktc = {}
    for ktc_id, sleeper_id in ktc_map.items():
        sleeper_to_ktc[sleeper_id] = ktc_by_id[ktc_id]
    return sleeper_to_ktc


def _ktc_value(ktc_p, fmt: str) -> int:
    if ktc_p is None:
        return 0
    return ktc_p.superflex.value if fmt == "sf" else ktc_p.one_qb.value


def _ktc_rank(ktc_p, fmt: str) -> int:
    if ktc_p is None:
        return 0
    return ktc_p.superflex.rank if fmt == "sf" else ktc_p.one_qb.rank


def _ktc_trend(ktc_p, fmt: str) -> int:
    """7-day overall trend for the given format."""
    if ktc_p is None:
        return 0
    pv = ktc_p.superflex if fmt == "sf" else ktc_p.one_qb
    return pv.overall_trend


# ---------------------------------------------------------------------------
# Verdict thresholds & helper
# ---------------------------------------------------------------------------
# A single source of truth for trade-verdict bands. Used by trade-check,
# proposed-trades, and send-trade preview so they all agree on what "WIN"
# vs "FAIR" vs "LOSS" means after the value adjustment is applied.

VERDICT_WIN_THRESHOLD = 500       # adjusted delta above this = WIN
VERDICT_FAIR_BAND = 800           # within ±this of zero = FAIR / slight edge


def _verdict_from_delta(adjusted_delta: int) -> str:
    """Translate an adjusted KTC delta into a verdict label.

    Positive = trade favors the user. Bands tuned against the historical
    Meat Market trade audit: <500 KTC noise; 500-800 a soft edge;
    >800 a meaningful win.
    """
    if adjusted_delta > VERDICT_WIN_THRESHOLD:
        if adjusted_delta > VERDICT_FAIR_BAND:
            return "WIN — significant value"
        return "SLIGHT WIN — minor value"
    if adjusted_delta < -VERDICT_WIN_THRESHOLD:
        if adjusted_delta < -VERDICT_FAIR_BAND:
            return "LOSS — significant value"
        return "SLIGHT LOSS — minor value"
    return "FAIR"


# ---------------------------------------------------------------------------
# find-trades mode defaults — strategy lookup, not an if/elif chain
# ---------------------------------------------------------------------------

MODE_DEFAULTS: dict[str, tuple[int, int]] = {
    "normal":      (300, 3500),    # slight overpay band — fair stud tax
    "upgrade":     (-5000, 0),     # net positive value to user
    "downtiering": (300, 5000),    # liquidating star talent for picks
}


def _mode_defaults(mode: str) -> tuple[int, int]:
    """Return (min_overpay, max_overpay) for a find-trades mode.

    Unknown modes fall back to the normal band so the CLI doesn't crash
    if someone passes a typo — they just get the default behavior.
    """
    return MODE_DEFAULTS.get(mode, MODE_DEFAULTS["normal"])


# ---------------------------------------------------------------------------
# League display + player view helpers
# ---------------------------------------------------------------------------


def _build_user_display(league_users) -> dict[str, str]:
    """Map Sleeper user_id (string) → display_name from a league users list.

    Replaces a 6-line `if hasattr(u, ...)` + filter block that was
    duplicated in 6+ CLI commands. Empty-safe: None or empty input
    returns an empty dict.
    """
    out: dict[str, str] = {}
    for u in (league_users or []):
        uid = str(getattr(u, "user_id", "") or "")
        disp = getattr(u, "display_name", "") or ""
        if uid and disp:
            out[uid] = disp
    return out


def _player_view(pid: str, sleeper_players: dict, sleeper_to_ktc: dict, fmt: str = "sf") -> dict:
    """Resolve a Sleeper player_id into a render-ready dict.

    Returns {player_id, name, position, team, ktc}. Falls back to the raw
    pid when the player isn't in the cache, returns ktc=0 when KTC has no
    entry for them. Used by every command that prints player lines.
    """
    p = sleeper_players.get(pid)
    ktc_p = sleeper_to_ktc.get(pid)
    return {
        "player_id": pid,
        "name": (getattr(p, "full_name", None) if p else None) or pid,
        "position": (getattr(p, "position", None) if p else None) or "?",
        "team": (getattr(p, "team", None) if p else None) or "",
        "ktc": _ktc_value(ktc_p, fmt),
    }


# ---------------------------------------------------------------------------
# League context bundle — opens every command identically
# ---------------------------------------------------------------------------


def _setup_league_context(
    username: str,
    league_filter: str | None = None,
    *,
    fetch_users: bool = False,
) -> dict:
    """One-shot opening sequence for every league-scoped CLI command.

    Returns a dict with `user`, `league`, `rosters`, `sleeper_players`,
    and optionally `league_users` + `user_display` when `fetch_users=True`.
    Collapses the 4-line `_resolve_league` + `_fetch_roster_and_players`
    boilerplate that opens every command in the package.
    """
    import asyncio
    from sleeper.client import SleeperClient

    user, league = _resolve_league(username, league_filter)
    rosters, sleeper_players = _fetch_roster_and_players(league.league_id)
    ctx = {
        "user": user,
        "league": league,
        "rosters": rosters,
        "sleeper_players": sleeper_players,
    }
    if fetch_users:
        async def _get_users():
            async with SleeperClient() as client:
                return await client.leagues.get_users(league.league_id)
        league_users = asyncio.run(_get_users())
        ctx["league_users"] = league_users
        ctx["user_display"] = _build_user_display(league_users)
    return ctx


# ---------------------------------------------------------------------------
# Lazy-load analytics modules by file path
# ---------------------------------------------------------------------------


def _lazy_load_analytics(module_name: str):
    """Import `sleeper.analytics.<module_name>` by file path.

    `sleeper.analytics.__init__` re-exports some modules whose import
    chain can break in partial installs (user_collector references that
    won't resolve without optional deps). Loading by file path bypasses
    the package init entirely, so commands that just need gm_mode or
    valuation can succeed without pulling in everything.

    Returns the loaded module object — caller pulls the functions they
    need off it.
    """
    import importlib.util
    import os
    import sys as _sys

    # __file__ is .../sleeper/cli/_common.py → walk up to .../sleeper/
    pkg_root = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(pkg_root, "analytics", f"{module_name}.py")
    spec_name = f"_sleeper_lazy_{module_name}"
    spec = importlib.util.spec_from_file_location(spec_name, path)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[spec_name] = mod
    spec.loader.exec_module(mod)
    return mod
