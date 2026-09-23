"""Transport-layer coverage for `ProjectionsApi`.

`test_projections.py` covers the models, league scoring and ranking — it never
touches request construction. This file does only that: what URL and params go
out, and how the body comes back. Every test runs offline against a fake
`HttpClient` that records calls.

The one thing worth restating, because it is the reason this file exists: the
multi-position union is expressed as `position[]`, and the two obvious
alternatives fail *silently* rather than erroring. A regression here does not
raise; it quietly returns one position's worth of players, or none. So the
assertions are on the outgoing params, not just on the parsed result.
"""
from __future__ import annotations

import httpx
import pytest

from sleeper.api.projections import DEFAULT_POSITIONS, ProjectionsApi


class FakeHttp:
    """Stands in for `HttpClient`, recording `(path, params)` per call."""

    def __init__(self, response=None):
        self._response = [] if response is None else response
        self.calls: list[tuple[str, dict]] = []

    async def get(self, path: str, params: dict | None = None):
        self.calls.append((path, dict(params or {})))
        return self._response

    @property
    def only_call(self) -> tuple[str, dict]:
        assert len(self.calls) == 1, f"expected exactly one request, got {len(self.calls)}"
        return self.calls[0]


def row(player_id: str, position: str = "WR", **stats):
    return {
        "player_id": player_id,
        "week": 3,
        "season": "2026",
        "team": "PHI",
        "game_id": "202610301",
        "stats": stats or {"pts_ppr": 10.0},
        "player": {"first_name": "A", "last_name": player_id, "position": position},
    }


def api(response=None) -> tuple[ProjectionsApi, FakeHttp]:
    http = FakeHttp(response)
    return ProjectionsApi(http), http  # type: ignore[arg-type]


# -- the union is one request, in bracket form ------------------------------


async def test_many_positions_are_one_request_not_one_per_position():
    """Four positions used to mean four round trips. Verified live that the
    bracket form unions them server-side, so it must stay one."""
    client, http = api()
    await client.get_week(2026, 3, position=["QB", "RB", "TE", "WR"])

    path, params = http.only_call
    assert params["position[]"] == ["QB", "RB", "TE", "WR"]


@pytest.mark.parametrize("bad_key", ["position", "positions"])
async def test_the_silently_broken_param_spellings_are_never_sent(bad_key):
    """`position=QB&position=RB` keeps only the last and `position=QB,RB`
    returns []. Neither errors, so only an assertion catches the regression."""
    client, http = api()
    await client.get_week(2026, 3, position=["QB", "RB"])

    _, params = http.only_call
    assert bad_key not in params


async def test_the_union_is_a_list_never_a_comma_joined_string():
    """`position=QB,RB` returns `[]` — an empty board, not an error."""
    client, http = api()
    await client.get_week(2026, 3, position=["QB", "RB"])

    value = http.only_call[1]["position[]"]
    assert isinstance(value, list) and not isinstance(value, str)


async def test_httpx_encodes_the_bracket_list_as_repeated_pairs():
    """The param only works as `position[]=QB&position[]=RB` on the wire.
    Asserting the dict alone would miss a serializer that JSON-encodes lists."""
    client, http = api()
    await client.get_week(2026, 3, position=["QB", "RB"])

    path, params = http.only_call
    url = httpx.Request("GET", f"https://api.sleeper.com{path}", params=params).url
    assert str(url).count("position%5B%5D=") == 2
    assert "position%5B%5D=QB" in str(url)
    assert "position%5B%5D=RB" in str(url)


# -- position normalization reaches the wire --------------------------------


async def test_a_single_position_string_still_uses_the_bracket_form():
    client, http = api()
    await client.get_week(2026, 3, position="te")

    _, params = http.only_call
    assert params["position[]"] == ["TE"]


async def test_default_positions_are_sent_when_caller_says_nothing():
    """The default exists to avoid the 5.7 MB whole-NFL response; if it stops
    being sent the call succeeds and quietly gets 100x the data."""
    client, http = api()
    await client.get_week(2026, 3)

    _, params = http.only_call
    assert params["position[]"] == list(DEFAULT_POSITIONS)


@pytest.mark.parametrize("empty", [None, [], (), ["", "  "]])
async def test_explicitly_empty_positions_omit_the_param_entirely(empty):
    """Omitting the param is what asks for every position — it must not
    degrade into an empty list, which the server would read as no filter
    anyway but which hides the caller's intent."""
    client, http = api()
    await client.get_week(2026, 3, position=empty)

    _, params = http.only_call
    assert "position[]" not in params


# -- paths and other params -------------------------------------------------


async def test_week_season_and_stats_paths():
    for coro_name, kwargs, expected in [
        ("get_week", {"season": 2026, "week": 3}, "/projections/nfl/2026/3"),
        ("get_season", {"season": 2026}, "/projections/nfl/2026"),
        ("get_week_stats", {"season": 2026, "week": 3}, "/stats/nfl/2026/3"),
    ]:
        client, http = api()
        await getattr(client, coro_name)(**kwargs)
        assert http.only_call[0] == expected


async def test_sport_is_not_hardcoded_into_the_path():
    client, http = api()
    await client.get_week(2026, 3, sport="lcs")
    assert http.only_call[0].startswith("/projections/lcs/")


async def test_season_type_is_passed_through():
    client, http = api()
    await client.get_week(2026, 3, season_type="post")
    assert http.only_call[1]["season_type"] == "post"


async def test_order_by_is_sent_by_default_but_droppable():
    """Sleeper ignores it, so it is cosmetic — but it should stay togglable
    rather than being silently hardcoded."""
    client, http = api()
    await client.get_week(2026, 3)
    assert http.only_call[1]["order_by"] == "pts_ppr"

    client, http = api()
    await client.get_week(2026, 3, order_by=None)
    assert "order_by" not in http.only_call[1]


# -- response handling ------------------------------------------------------


async def test_records_are_parsed_into_models():
    client, _ = api([row("111", "WR", pts_ppr=12.5)])
    (proj,) = await client.get_week(2026, 3)
    assert proj.player_id == "111"
    assert proj.pts_ppr == 12.5


async def test_a_player_returned_twice_is_yielded_once():
    """A WR carrying RB in fantasy_positions is the row that can duplicate
    across a union. A start/sit caller counting starters must not see two."""
    client, _ = api([row("111"), row("222"), row("111")])
    result = await client.get_week(2026, 3, position=["RB", "WR"])
    assert [p.player_id for p in result] == ["111", "222"]


async def test_the_first_occurrence_wins_a_duplicate():
    client, _ = api([row("111", pts_ppr=1.0), row("111", pts_ppr=99.0)])
    (proj,) = await client.get_week(2026, 3)
    assert proj.pts_ppr == 1.0


@pytest.mark.parametrize("body", [None, [], {}])
async def test_an_empty_body_is_an_empty_list_not_a_crash(body):
    client, _ = api(body)
    assert await client.get_week(2026, 3) == []


# -- the per-player endpoint ------------------------------------------------


async def test_player_weeks_uses_grouping_and_string_season():
    client, http = api({})
    await client.get_player_weeks("6904", 2026)

    path, params = http.only_call
    assert path == "/projections/nfl/player/6904"
    assert params == {"season": "2026", "season_type": "regular", "grouping": "week"}


async def test_player_weeks_keys_are_ints_not_the_raw_string_keys():
    client, _ = api({"1": row("6904"), "2": row("6904")})
    weeks = await client.get_player_weeks("6904", 2026)
    assert set(weeks) == {1, 2}


async def test_player_weeks_drops_the_null_bye_week():
    """Sleeper returns `null` for the bye here rather than a flagged row, so
    a bye is a missing key — see the docstring on `get_player_weeks`."""
    client, _ = api({"9": row("6904"), "10": None, "11": row("6904")})
    weeks = await client.get_player_weeks("6904", 2026)
    assert set(weeks) == {9, 11}
    assert 10 not in weeks


async def test_player_weeks_ignores_non_numeric_grouping_keys():
    client, _ = api({"1": row("6904"), "season": row("6904")})
    weeks = await client.get_player_weeks("6904", 2026)
    assert set(weeks) == {1}
