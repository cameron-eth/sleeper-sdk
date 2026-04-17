"""Write a daily KTC snapshot to data/ktc/YYYY-MM-DD.json.

Intended to run in CI on a cron. Skips writing if today's file already exists
and --force is not set. Also updates data/ktc/latest.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root without install
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1] / "src"))

from sleeper.enrichment.ktc import fetch_ktc_players  # noqa: E402


def _player_to_row(p) -> dict:
    return {
        "ktc_id": p.ktc_id,
        "name": p.player_name,
        "position": p.position,
        "team": p.team,
        "age": p.age,
        "sf_value": p.superflex.value,
        "sf_rank": p.superflex.rank,
        "sf_pos_rank": p.superflex.positional_rank,
        "oqb_value": p.one_qb.value,
        "oqb_rank": p.one_qb.rank,
        "oqb_pos_rank": p.one_qb.positional_rank,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot KTC dynasty values")
    parser.add_argument(
        "--out-dir",
        default="data/ktc",
        help="Output directory (default: data/ktc)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite today's snapshot if it already exists",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dated_path = out_dir / f"{today}.json"
    latest_path = out_dir / "latest.json"

    if dated_path.exists() and not args.force:
        print(f"Snapshot already exists for {today}: {dated_path} (use --force to overwrite)")
        return 0

    print("Fetching KTC players...")
    # force_refresh so we don't pick up a stale local cache in CI
    players = fetch_ktc_players(force_refresh=True)
    print(f"Fetched {len(players)} players")

    rows = [_player_to_row(p) for p in players]
    # Stable sort so the diff between days is minimal
    rows.sort(key=lambda r: (r.get("ktc_id") or ""))

    snapshot = {
        "date": today,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "player_count": len(rows),
        "players": rows,
    }

    dated_path.write_text(json.dumps(snapshot, indent=2, sort_keys=False) + "\n")
    latest_path.write_text(json.dumps(snapshot, indent=2, sort_keys=False) + "\n")
    print(f"Wrote {dated_path}")
    print(f"Wrote {latest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
