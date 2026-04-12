"""
Sleeper Fantasy Football API Wrapper & League Analytics
Analyzes rivalries, worst losses, biggest upsets, worst trades, and more.
"""

import requests
import json
import time
from collections import defaultdict
from typing import Optional

BASE_URL = "https://api.sleeper.app/v1"
USER_ID = "1328460395249172480"
SEASONS = list(range(2017, 2026))


# ─────────────────────────────────────────────────────────────────────────────
# API Wrapper
# ─────────────────────────────────────────────────────────────────────────────

class SleeperAPI:
    def __init__(self, rate_limit_delay=0.05):
        self.session = requests.Session()
        self.delay = rate_limit_delay

    def _get(self, path: str) -> Optional[dict | list]:
        url = f"{BASE_URL}{path}"
        resp = self.session.get(url)
        time.sleep(self.delay)
        if resp.status_code == 200:
            return resp.json()
        return None

    def get_user(self, user_id_or_name: str):
        return self._get(f"/user/{user_id_or_name}")

    def get_user_leagues(self, user_id: str, sport: str, season: int):
        return self._get(f"/user/{user_id}/leagues/{sport}/{season}") or []

    def get_league(self, league_id: str):
        return self._get(f"/league/{league_id}")

    def get_rosters(self, league_id: str):
        return self._get(f"/league/{league_id}/rosters") or []

    def get_users(self, league_id: str):
        return self._get(f"/league/{league_id}/users") or []

    def get_matchups(self, league_id: str, week: int):
        return self._get(f"/league/{league_id}/matchups/{week}") or []

    def get_transactions(self, league_id: str, week: int):
        return self._get(f"/league/{league_id}/transactions/{week}") or []

    def get_traded_picks(self, league_id: str):
        return self._get(f"/league/{league_id}/traded_picks") or []

    def get_winners_bracket(self, league_id: str):
        return self._get(f"/league/{league_id}/winners_bracket") or []

    def get_nfl_state(self):
        return self._get("/state/nfl")

    def get_all_drafts(self, league_id: str):
        return self._get(f"/league/{league_id}/drafts") or []


# ─────────────────────────────────────────────────────────────────────────────
# Data Collection
# ─────────────────────────────────────────────────────────────────────────────

def collect_all_league_data(api: SleeperAPI, user_id: str):
    """Collect all matchup + roster + user data across all seasons for user's leagues."""
    all_leagues = []
    for season in SEASONS:
        leagues = api.get_user_leagues(user_id, "nfl", season)
        for league in leagues:
            league["season_year"] = season
        all_leagues.extend(leagues)

    if not all_leagues:
        print(f"  No leagues found for user {user_id}")
        return {}

    print(f"  Found {len(all_leagues)} total leagues across seasons")

    results = {}
    for league in all_leagues:
        lid = league["league_id"]
        season = league.get("season") or league.get("season_year")
        name = league.get("name", "Unknown")
        settings = league.get("settings", {})
        playoff_week_start = settings.get("playoff_week_start", 15)
        # number of regular season weeks
        num_reg_weeks = playoff_week_start - 1 if playoff_week_start else 14

        print(f"  [{season}] {name} (id={lid})")

        users = api.get_users(lid)
        rosters = api.get_rosters(lid)

        # map roster_id -> owner_id
        roster_to_owner = {r["roster_id"]: r.get("owner_id") for r in rosters}
        # map owner_id -> display name
        owner_to_name = {}
        for u in users:
            uid = u["user_id"]
            owner_to_name[uid] = u.get("metadata", {}).get("team_name") or u.get("display_name") or u.get("username") or uid

        # Collect all regular season matchups
        all_matchups = []
        for week in range(1, num_reg_weeks + 1):
            matchups = api.get_matchups(lid, week)
            for m in matchups:
                m["week"] = week
            all_matchups.extend(matchups)

        # Collect transactions for all weeks (1-17)
        all_transactions = []
        for week in range(1, 18):
            txns = api.get_transactions(lid, week)
            all_transactions.extend(txns)

        results[lid] = {
            "league": league,
            "season": season,
            "name": name,
            "users": users,
            "rosters": rosters,
            "roster_to_owner": roster_to_owner,
            "owner_to_name": owner_to_name,
            "matchups": all_matchups,
            "transactions": all_transactions,
            "traded_picks": api.get_traded_picks(lid),
            "num_reg_weeks": num_reg_weeks,
        }
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Analytics
# ─────────────────────────────────────────────────────────────────────────────

def build_head_to_head(league_data: dict) -> dict:
    """
    Build matchup-level head-to-head records across all leagues.
    Returns dict keyed by frozenset({owner_a, owner_b}) -> {wins_a, wins_b, ...}
    """
    h2h = defaultdict(lambda: {"wins": defaultdict(int), "games": [], "total_pts": defaultdict(float)})

    for lid, data in league_data.items():
        matchups = data["matchups"]
        r2o = data["roster_to_owner"]
        o2n = data["owner_to_name"]
        season = data["season"]

        # Group matchups by week+matchup_id
        game_map = defaultdict(list)
        for m in matchups:
            key = (m["week"], m["matchup_id"])
            game_map[key].append(m)

        for (week, mid), teams in game_map.items():
            if len(teams) != 2:
                continue
            t1, t2 = teams
            o1 = r2o.get(t1["roster_id"])
            o2 = r2o.get(t2["roster_id"])
            if not o1 or not o2 or o1 == o2:
                continue
            pts1 = t1.get("points") or 0
            pts2 = t2.get("points") or 0
            key = frozenset([o1, o2])
            winner = o1 if pts1 > pts2 else o2
            loser = o2 if pts1 > pts2 else o1
            h2h[key]["wins"][winner] += 1
            h2h[key]["total_pts"][o1] += pts1
            h2h[key]["total_pts"][o2] += pts2
            h2h[key]["games"].append({
                "season": season,
                "week": week,
                "owner1": o1,
                "pts1": pts1,
                "owner2": o2,
                "pts2": pts2,
                "winner": winner,
                "loser": loser,
                "margin": abs(pts1 - pts2),
                "league": data["name"],
            })
    return h2h


def find_biggest_rivals(h2h: dict, owner_to_name: dict, top_n=5) -> list:
    """Rivalries = pairs with most games played between them."""
    rivalries = []
    for pair, data in h2h.items():
        owners = list(pair)
        if len(owners) < 2:
            continue
        o1, o2 = owners
        total_games = len(data["games"])
        w1 = data["wins"].get(o1, 0)
        w2 = data["wins"].get(o2, 0)
        n1 = owner_to_name.get(o1, o1[:8])
        n2 = owner_to_name.get(o2, o2[:8])
        rivalries.append({
            "owner1": o1, "name1": n1, "wins1": w1,
            "owner2": o2, "name2": n2, "wins2": w2,
            "total_games": total_games,
            "closest_game": min(data["games"], key=lambda g: g["margin"]) if data["games"] else None,
        })
    rivalries.sort(key=lambda x: x["total_games"], reverse=True)
    return rivalries[:top_n]


def find_worst_losses(league_data: dict, owner_to_name: dict, top_n=10) -> list:
    """Worst losses by margin (lost by most points)."""
    all_games = []
    for lid, data in league_data.items():
        r2o = data["roster_to_owner"]
        matchups = data["matchups"]
        game_map = defaultdict(list)
        for m in matchups:
            key = (m["week"], m["matchup_id"])
            game_map[key].append(m)
        for (week, mid), teams in game_map.items():
            if len(teams) != 2:
                continue
            t1, t2 = teams
            pts1 = t1.get("points") or 0
            pts2 = t2.get("points") or 0
            if pts1 == pts2:
                continue
            loser_team = t1 if pts1 < pts2 else t2
            winner_team = t2 if pts1 < pts2 else t1
            loser_pts = min(pts1, pts2)
            winner_pts = max(pts1, pts2)
            loser_owner = r2o.get(loser_team["roster_id"])
            winner_owner = r2o.get(winner_team["roster_id"])
            all_games.append({
                "season": data["season"],
                "league": data["name"],
                "week": week,
                "loser": loser_owner,
                "loser_name": owner_to_name.get(loser_owner, str(loser_owner)[:8]),
                "loser_pts": loser_pts,
                "winner": winner_owner,
                "winner_name": owner_to_name.get(winner_owner, str(winner_owner)[:8]),
                "winner_pts": winner_pts,
                "margin": winner_pts - loser_pts,
            })
    all_games.sort(key=lambda x: x["margin"], reverse=True)
    return all_games[:top_n]


def find_biggest_upsets(league_data: dict, owner_to_name: dict, top_n=10) -> list:
    """
    Upsets = a team with poor season record beats a team with a great record,
    OR a low-scoring team beats a high-scoring team by margin.
    We approximate by: winner scored much less than loser on average that season,
    but still won. Proxy: team with lower season avg pts wins.
    """
    # First build per-owner per-season avg scores from rosters
    owner_season_fpts = {}
    for lid, data in league_data.items():
        season = data["season"]
        for roster in data["rosters"]:
            owner = roster.get("owner_id")
            fpts = roster.get("settings", {}).get("fpts", 0) or 0
            fpts_dec = roster.get("settings", {}).get("fpts_decimal", 0) or 0
            total = fpts + fpts_dec / 100
            owner_season_fpts[(owner, season)] = total

    upsets = []
    for lid, data in league_data.items():
        r2o = data["roster_to_owner"]
        season = data["season"]
        matchups = data["matchups"]
        game_map = defaultdict(list)
        for m in matchups:
            key = (m["week"], m["matchup_id"])
            game_map[key].append(m)

        for (week, mid), teams in game_map.items():
            if len(teams) != 2:
                continue
            t1, t2 = teams
            pts1 = t1.get("points") or 0
            pts2 = t2.get("points") or 0
            if pts1 == pts2:
                continue
            o1 = r2o.get(t1["roster_id"])
            o2 = r2o.get(t2["roster_id"])
            if not o1 or not o2:
                continue
            season_fpts1 = owner_season_fpts.get((o1, season), 0)
            season_fpts2 = owner_season_fpts.get((o2, season), 0)
            # upset if winner had lower season total
            winner_owner = o1 if pts1 > pts2 else o2
            loser_owner = o2 if pts1 > pts2 else o1
            winner_season = season_fpts1 if winner_owner == o1 else season_fpts2
            loser_season = season_fpts2 if loser_owner == o2 else season_fpts1
            upset_magnitude = loser_season - winner_season
            if upset_magnitude > 0:
                upsets.append({
                    "season": season,
                    "league": data["name"],
                    "week": week,
                    "winner": winner_owner,
                    "winner_name": owner_to_name.get(winner_owner, str(winner_owner)[:8]),
                    "winner_pts": max(pts1, pts2),
                    "winner_season_total": winner_season,
                    "loser": loser_owner,
                    "loser_name": owner_to_name.get(loser_owner, str(loser_owner)[:8]),
                    "loser_pts": min(pts1, pts2),
                    "loser_season_total": loser_season,
                    "upset_magnitude": upset_magnitude,
                    "margin": abs(pts1 - pts2),
                })
    upsets.sort(key=lambda x: x["upset_magnitude"], reverse=True)
    return upsets[:top_n]


def find_worst_trades(league_data: dict, owner_to_name: dict, top_n=10) -> list:
    """
    Identify trades involving the most assets changing hands (most picks + players).
    We can't fully evaluate trade value without historical player stats,
    but we can flag trades with big asset imbalance (# of players traded each way).
    """
    all_trades = []
    for lid, data in league_data.items():
        season = data["season"]
        r2o = data["roster_to_owner"]
        for txn in data["transactions"]:
            if txn.get("type") != "trade":
                continue
            adds = txn.get("adds") or {}
            drops = txn.get("drops") or {}
            picks = txn.get("draft_picks") or []
            roster_ids = txn.get("roster_ids") or []
            # Count what each side sent
            side_players = defaultdict(lambda: {"sent": 0, "received": 0})
            for player_id, roster_id in adds.items():
                side_players[roster_id]["received"] += 1
            for player_id, roster_id in drops.items():
                side_players[roster_id]["sent"] += 1
            for pick in picks:
                prev = pick.get("previous_owner_id")
                new = pick.get("owner_id")
                if prev:
                    side_players[prev]["sent"] += 1
                if new:
                    side_players[new]["received"] += 1
            if len(roster_ids) < 2:
                continue
            totals = [(r, side_players[r]["received"], side_players[r]["sent"]) for r in roster_ids]
            imbalance = max(abs(t[1] - t[2]) for t in totals) if totals else 0
            all_trades.append({
                "season": season,
                "league": data["name"],
                "transaction_id": txn.get("transaction_id"),
                "week": txn.get("leg"),
                "rosters": roster_ids,
                "owners": [owner_to_name.get(r2o.get(r), str(r)) for r in roster_ids],
                "player_adds": len(adds),
                "picks_traded": len(picks),
                "asset_imbalance": imbalance,
                "total_assets": len(adds) + len(picks),
                "raw": txn,
            })
    all_trades.sort(key=lambda x: x["total_assets"], reverse=True)
    return all_trades[:top_n]


def find_most_lopsided_season(league_data: dict, owner_to_name: dict) -> list:
    """Team with highest points that missed playoffs / lowest wins."""
    results = []
    for lid, data in league_data.items():
        season = data["season"]
        for roster in data["rosters"]:
            s = roster.get("settings", {}) or {}
            wins = s.get("wins", 0)
            losses = s.get("losses", 0)
            fpts = (s.get("fpts", 0) or 0) + (s.get("fpts_decimal", 0) or 0) / 100
            owner = roster.get("owner_id")
            results.append({
                "season": season,
                "league": data["name"],
                "owner": owner,
                "name": owner_to_name.get(owner, str(owner)[:8]),
                "wins": wins,
                "losses": losses,
                "fpts": fpts,
                "win_pct": wins / (wins + losses) if (wins + losses) > 0 else 0,
            })
    # Most pts with worst record
    results.sort(key=lambda x: x["fpts"] - x["win_pct"] * 200, reverse=True)
    return results[:10]


def best_worst_records(league_data: dict, owner_to_name: dict):
    """All-time wins/losses per owner across all leagues."""
    records = defaultdict(lambda: {"wins": 0, "losses": 0, "ties": 0, "fpts": 0.0, "seasons": set()})
    for lid, data in league_data.items():
        season = data["season"]
        for roster in data["rosters"]:
            s = roster.get("settings", {}) or {}
            owner = roster.get("owner_id")
            if not owner:
                continue
            records[owner]["wins"] += s.get("wins", 0)
            records[owner]["losses"] += s.get("losses", 0)
            records[owner]["ties"] += s.get("ties", 0)
            records[owner]["fpts"] += (s.get("fpts", 0) or 0) + (s.get("fpts_decimal", 0) or 0) / 100
            records[owner]["seasons"].add(season)
    result = []
    for owner, r in records.items():
        total = r["wins"] + r["losses"] + r["ties"]
        result.append({
            "owner": owner,
            "name": owner_to_name.get(owner, str(owner)[:8]),
            "wins": r["wins"],
            "losses": r["losses"],
            "ties": r["ties"],
            "fpts": round(r["fpts"], 1),
            "win_pct": round(r["wins"] / total, 3) if total > 0 else 0,
            "seasons": len(r["seasons"]),
        })
    result.sort(key=lambda x: x["win_pct"], reverse=True)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def print_separator(title: str):
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")


def run(user_id: str):
    api = SleeperAPI()

    # Try fetching user
    user = api.get_user(user_id)
    if not user:
        print(f"[!] Could not find user with ID {user_id}. Trying as username...")
        # Could not find — might need username. Let's try some fallbacks
        return

    print(f"User: {user.get('display_name') or user.get('username')} (ID: {user.get('user_id')})")

    print("\nCollecting league data across all seasons...")
    league_data = collect_all_league_data(api, user_id)

    if not league_data:
        print("No league data found. Exiting.")
        return

    # Build a combined owner_to_name across all leagues
    combined_o2n = {}
    for lid, data in league_data.items():
        combined_o2n.update(data["owner_to_name"])

    # ── Head-to-Head
    h2h = build_head_to_head(league_data)

    print_separator("TOP RIVALRIES (Most Games Played Head-to-Head)")
    rivals = find_biggest_rivals(h2h, combined_o2n, top_n=10)
    for i, r in enumerate(rivals, 1):
        print(f"  {i}. {r['name1']} ({r['wins1']}W) vs {r['name2']} ({r['wins2']}W) — {r['total_games']} games")
        if r["closest_game"]:
            cg = r["closest_game"]
            print(f"     Closest game: {cg['league']} S{cg['season']} W{cg['week']} "
                  f"| {cg['pts1']:.1f} vs {cg['pts2']:.1f} (margin: {cg['margin']:.1f})")

    print_separator("WORST LOSSES (Biggest Margins of Defeat)")
    worst = find_worst_losses(league_data, combined_o2n, top_n=10)
    for i, g in enumerate(worst, 1):
        print(f"  {i}. {g['loser_name']} lost by {g['margin']:.1f}pts "
              f"({g['loser_pts']:.1f} vs {g['winner_pts']:.1f}) "
              f"— {g['league']} S{g['season']} W{g['week']} "
              f"(vs {g['winner_name']})")

    print_separator("BIGGEST UPSETS (Underdog Wins by Season Dominance Metric)")
    upsets = find_biggest_upsets(league_data, combined_o2n, top_n=10)
    for i, u in enumerate(upsets, 1):
        print(f"  {i}. {u['winner_name']} ({u['winner_pts']:.1f}pts, season total: {u['winner_season_total']:.1f}) "
              f"beat {u['loser_name']} ({u['loser_pts']:.1f}pts, season total: {u['loser_season_total']:.1f}) "
              f"— upset magnitude: {u['upset_magnitude']:.1f} "
              f"| {u['league']} S{u['season']} W{u['week']}")

    print_separator("MOST LOPSIDED LUCK (High Points, Bad Record)")
    lopsided = find_most_lopsided_season(league_data, combined_o2n)
    for i, l in enumerate(lopsided, 1):
        print(f"  {i}. {l['name']} — {l['fpts']:.1f} pts but {l['wins']}W-{l['losses']}L "
              f"({l['win_pct']*100:.0f}% win rate) — {l['league']} {l['season']}")

    print_separator("BIGGEST TRADES BY ASSET VOLUME")
    trades = find_worst_trades(league_data, combined_o2n, top_n=10)
    for i, t in enumerate(trades, 1):
        owners_str = " ↔ ".join(t["owners"])
        print(f"  {i}. {owners_str} — {t['player_adds']} players + {t['picks_traded']} picks "
              f"(imbalance: {t['asset_imbalance']}) — {t['league']} S{t['season']} W{t['week']}")

    print_separator("ALL-TIME WIN % LEADERBOARD")
    records = best_worst_records(league_data, combined_o2n)
    for i, r in enumerate(records, 1):
        print(f"  {i:>2}. {r['name']:<20} {r['wins']:>3}W {r['losses']:>3}L  "
              f"({r['win_pct']*100:.1f}%)  {r['fpts']:>8.1f} pts  "
              f"({r['seasons']} season{'s' if r['seasons']!=1 else ''})")

    print("\n")
    return league_data


if __name__ == "__main__":
    run(USER_ID)
