"""Regression tests for KTC payload extraction.

Guards the failure that shipped on 2026-09-08: KTC moved its rankings payload
out of a `var playersArray = [...]` literal and into a JSON script tag. The
extractor's regex stopped matching, `_extract_js_var` returned `[]` on a miss,
and nothing raised. The scheduled snapshot job kept exiting 0 and committed
three consecutive days of `player_count: 0` into `data/ktc/`, which silently
zeroed every KTC-dependent command in the repo (market-value, find-trades,
trending, pe-ratio, gm-mode, roster-rank, ...).

Two properties matter here and both are asserted below:

1. **Both page shapes parse.** The current JSON-script-tag form and the legacy
   array literal, so a KTC revert does not break us again.
2. **Failure is loud.** A page we cannot extract from, or one that yields a
   suspiciously short board, must raise rather than return a short list. A
   short list is indistinguishable from a real answer downstream, which is
   exactly why the original bug went unnoticed for three days.

No network access.
"""
from __future__ import annotations

import json

import pytest

from sleeper.enrichment import ktc as ktc_mod
from sleeper.enrichment.ktc import (
    _MIN_EXPECTED_PLAYERS,
    _PLAYERS_ARRAY_RE,
    _PLAYERS_JSON_TAG_RE,
    _extract_first,
    _extract_js_var,
    _parse_ktc_player_entry,
    KTCScrapeError,
    fetch_ktc_players,
)


def _record(ktc_id: int, name: str = "Test Player", position: str = "WR") -> dict:
    """One record in KTC's current schema."""
    return {
        "playerName": name,
        "playerID": ktc_id,
        "slug": f"{name.lower().replace(' ', '-')}-{ktc_id}",
        "position": position,
        "team": "PHI",
        "age": 25.4,
        "mflid": 10000 + ktc_id,
        "oneQBValues": {
            "value": 5000, "rank": 40, "positionalRank": 12,
            "overallTrend": -3, "positional7DayTrend": 1,
        },
        "superflexValues": {
            "value": 6000, "rank": 30, "positionalRank": 9,
            "overallTrend": 4, "positional7DayTrend": -2,
        },
    }


def _board(n: int) -> list[dict]:
    return [_record(i, f"Player {i}") for i in range(1, n + 1)]


def _json_tag_page(records: list[dict]) -> str:
    """The page shape KTC serves now."""
    return (
        "<html><head></head><body>\n"
        '<script type="application/json" id="ktc-players">'
        + json.dumps(records)
        + "</script>\n"
        "<script>\n"
        "    var playersArray = JSON.parse("
        "document.getElementById('ktc-players').textContent);\n"
        "    var oneQBPlayers = " + json.dumps(records[:3]) + ";\n"
        "</script></body></html>\n"
    )


def _legacy_page(records: list[dict]) -> str:
    """The pre-2026-09-08 page shape."""
    return (
        "<html><body><script>\n"
        "    var playersArray = " + json.dumps(records) + ";\n"
        "</script></body></html>\n"
    )


# ---------------------------------------------------------------------------
# Both page shapes extract
# ---------------------------------------------------------------------------

def test_json_script_tag_is_extracted():
    """The shape that broke us. This is the core regression guard."""
    records = _board(120)
    got = _extract_first(_json_tag_page(records), [_PLAYERS_JSON_TAG_RE, _PLAYERS_ARRAY_RE])
    assert len(got) == 120
    assert got[0]["playerName"] == "Player 1"


def test_legacy_array_literal_still_extracted():
    records = _board(120)
    got = _extract_first(_legacy_page(records), [_PLAYERS_JSON_TAG_RE, _PLAYERS_ARRAY_RE])
    assert len(got) == 120


def test_json_tag_wins_over_the_small_featured_array_on_the_same_page():
    """The live page has both: the full board in the tag, 3 featured records
    in `oneQBPlayers`. Pattern order must prefer the full board."""
    records = _board(200)
    got = _extract_first(_json_tag_page(records), [_PLAYERS_JSON_TAG_RE, _PLAYERS_ARRAY_RE])
    assert len(got) == 200, "extracted the featured subset instead of the full board"


def test_legacy_pattern_alone_finds_nothing_in_the_new_shape():
    """Documents the exact break: `var playersArray = JSON.parse(...)` has no
    array literal for the old regex to capture."""
    assert _extract_js_var(_json_tag_page(_board(120)), _PLAYERS_ARRAY_RE) == []


# ---------------------------------------------------------------------------
# Failure is loud
# ---------------------------------------------------------------------------

def test_unmatched_pattern_returns_empty_so_callers_can_try_the_next_shape():
    assert _extract_js_var("<html><body>nothing here</body></html>", _PLAYERS_JSON_TAG_RE) == []


def test_matched_but_unparseable_payload_raises():
    """A match with bad JSON means the format changed. Returning [] here is
    what made the original failure look like a quiet day at KTC."""
    page = '<script type="application/json" id="ktc-players">{not json,,}</script>'
    with pytest.raises(KTCScrapeError, match="could not parse"):
        _extract_js_var(page, _PLAYERS_JSON_TAG_RE)


def test_fetch_raises_when_no_payload_is_found(monkeypatch):
    monkeypatch.setattr(ktc_mod, "_fetch_page", lambda *a, **k: "<html><body>nope</body></html>")
    monkeypatch.setattr(ktc_mod, "_get_cache", lambda: _NullCache())
    with pytest.raises(KTCScrapeError, match="no player payload"):
        fetch_ktc_players(force_refresh=True)


def test_fetch_raises_on_a_suspiciously_short_board(monkeypatch):
    """A partial board must never be returned or cached.

    Downstream code treats the list as the complete market, so a short board
    silently corrupts every valuation rather than failing.
    """
    short = _json_tag_page(_board(_MIN_EXPECTED_PLAYERS - 1))
    monkeypatch.setattr(ktc_mod, "_fetch_page", lambda *a, **k: short)
    monkeypatch.setattr(ktc_mod, "_get_cache", lambda: _NullCache())
    with pytest.raises(KTCScrapeError, match="expected at least"):
        fetch_ktc_players(force_refresh=True)


def test_fetch_succeeds_at_the_threshold(monkeypatch):
    page = _json_tag_page(_board(_MIN_EXPECTED_PLAYERS))
    monkeypatch.setattr(ktc_mod, "_fetch_page", lambda *a, **k: page)
    monkeypatch.setattr(ktc_mod, "_get_cache", lambda: _NullCache())
    players = fetch_ktc_players(force_refresh=True)
    assert len(players) == _MIN_EXPECTED_PLAYERS


def test_a_short_board_is_never_cached(monkeypatch):
    """If a bad board reached the cache it would poison the next hour of reads."""
    cache = _NullCache()
    monkeypatch.setattr(ktc_mod, "_fetch_page",
                        lambda *a, **k: _json_tag_page(_board(5)))
    monkeypatch.setattr(ktc_mod, "_get_cache", lambda: cache)
    with pytest.raises(KTCScrapeError):
        fetch_ktc_players(force_refresh=True)
    assert cache.sets == [], "a partial board was written to the cache"


class _NullCache:
    """Cache stub that never returns a hit and records what was stored."""

    def __init__(self):
        self.sets: list = []

    def get(self, key):
        return None

    def set(self, key, value):
        self.sets.append((key, value))


# ---------------------------------------------------------------------------
# Record schema
# ---------------------------------------------------------------------------

def test_record_parses_into_both_format_values():
    p = _parse_ktc_player_entry(_record(7, "Real Player", "RB"))
    assert p is not None
    assert p.ktc_id == "7"
    assert p.player_name == "Real Player"
    assert p.position == "RB"
    assert p.superflex.value == 6000
    assert p.superflex.rank == 30
    assert p.one_qb.value == 5000
    assert p.mfl_id == "10007"


def test_record_without_an_id_is_skipped():
    """Pick/placeholder rows without playerID must not become value-0 players."""
    assert _parse_ktc_player_entry({"playerName": "No ID"}) is None


def test_missing_value_blocks_default_to_zero_not_crash():
    p = _parse_ktc_player_entry({"playerName": "Bare", "playerID": 1})
    assert p is not None
    assert p.superflex.value == 0 and p.one_qb.value == 0


def test_rdp_pick_rows_parse():
    """Draft picks ride in the same array as `position: "RDP"`."""
    pick = dict(_record(2078, "2026 Pick 1.03", "RDP"), team="FA", age=0.0)
    p = _parse_ktc_player_entry(pick)
    assert p is not None and p.position == "RDP"
