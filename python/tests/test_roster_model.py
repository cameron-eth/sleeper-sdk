"""Regression tests for the Roster model against Sleeper's real payload shape.

The Roster model previously omitted ``taxi``, ``co_owners``, ``keepers``, and
``metadata``, so pydantic silently dropped them at validation time. Agent code
reading ``roster.taxi`` (``agent/helpers.build_context``) then crashed with
``AttributeError: 'Roster' object has no attribute 'taxi'``, breaking every
command that builds roster context: ``context``, ``roster``, ``lineup``,
``lineup-health``, ``status``, ``matchup``, and the waiver/transaction flows
built on the same context.

Payload shape below mirrors the live ``GET /v1/league/<id>/rosters`` response
(verified 2026-09-03).
"""
from __future__ import annotations

from sleeper.types.league import Roster

REAL_PAYLOAD = {
    "roster_id": 6,
    "owner_id": "1267509994606051328",
    "league_id": "1401057434608410624",
    "starters": ["11564", "12512", "4199", "0", "LAC"],
    "players": ["11564", "12512", "4199", "2832"],
    "taxi": ["2832"],
    "reserve": [],
    "co_owners": None,
    "keepers": None,
    "metadata": {"allow_pn_scoring": "1"},
    "settings": {
        "wins": 0,
        "losses": 0,
        "ties": 0,
        "fpts": 0,
        "fpts_decimal": 0,
        "fpts_against": 0,
        "fpts_against_decimal": 0,
        "waiver_position": 3,
        "waiver_budget_used": 0,
        "total_moves": 0,
    },
}


def test_roster_parses_taxi_and_new_fields():
    roster = Roster.model_validate(REAL_PAYLOAD)
    assert roster.taxi == ["2832"]
    assert roster.reserve == []
    assert roster.co_owners is None
    assert roster.keepers is None
    assert roster.metadata == {"allow_pn_scoring": "1"}


def test_roster_new_fields_default_when_absent():
    payload = dict(REAL_PAYLOAD)
    for key in ("taxi", "co_owners", "keepers", "metadata"):
        payload.pop(key, None)
    roster = Roster.model_validate(payload)
    assert roster.taxi is None
    assert roster.co_owners is None
    assert roster.keepers is None
    assert roster.metadata is None


def test_roster_bench_property_unchanged():
    roster = Roster.model_validate(REAL_PAYLOAD)
    assert "2832" in roster.bench
    assert "11564" not in roster.bench
