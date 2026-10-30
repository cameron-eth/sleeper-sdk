"""IR, taxi and trade mutations must use the forms the Sleeper web client sends."""
import base64
import json
import time

import pytest

from sleeper.auth.client import SleeperAuthClient


def _fake_jwt() -> str:
    payload = {"user_id": "1", "display_name": "t", "iat": 0, "exp": int(time.time()) + 3600}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def _client(roster=None):
    """Client whose gql() records calls; league_rosters reads return ``roster``."""
    client = SleeperAuthClient(token=_fake_jwt())
    calls = []

    def fake_gql(op, query, variables=None):
        calls.append((op, query))
        if op == "league_rosters":
            return {"league_rosters": [{"roster_id": 3, **(roster or {})}]}
        return {}

    client.gql = fake_gql
    return client, calls


@pytest.mark.parametrize("method", ["accept_trade", "reject_trade"])
def test_trade_responses_inline_validated_ids(method):
    client, calls = _client()
    getattr(client, method)("123", "456", 8)
    op, query = calls[0]
    assert op == method
    assert 'league_id: "123"' in query and 'transaction_id: "456"' in query and "leg: 8" in query
    assert "Snowflake!" not in query


def test_cancel_trade_withdraws_with_reject_trade():
    client, calls = _client()
    client.cancel_trade("123", "456", 8)
    assert calls[0][0] == "reject_trade"
    assert "cancel_trade" not in calls[0][1]


def test_trade_ids_are_validated_before_interpolation():
    client, _ = _client()
    with pytest.raises(ValueError):
        client.accept_trade('1" } mutation x {', "456", 8)


def test_move_to_ir_appends_to_existing_reserve():
    client, calls = _client({"players": ["a", "b"], "reserve": ["a"], "taxi": []})
    client.move_to_ir("123", 3, "b")
    op, query = calls[-1]
    assert op == "roster_update_reserve"
    assert 'reserve: ["a", "b"]' in query and "roster_id: 3" in query


def test_activate_from_ir_removes_only_that_player():
    client, calls = _client({"players": ["a", "b"], "reserve": ["a", "b"], "taxi": []})
    client.activate_from_ir("123", 3, "a")
    assert 'reserve: ["b"]' in calls[-1][1]


def test_move_to_taxi_uses_roster_update_taxi():
    client, calls = _client({"players": ["a"], "reserve": [], "taxi": []})
    client.move_to_taxi("123", 3, "a")
    op, query = calls[-1]
    assert op == "roster_update_taxi"
    assert 'taxi: ["a"]' in query


def test_move_to_ir_is_idempotent_and_rejects_bad_ids():
    client, calls = _client({"players": ["a"], "reserve": ["a"], "taxi": []})
    client.move_to_ir("123", 3, "a")
    assert 'reserve: ["a"]' in calls[-1][1]
    with pytest.raises(ValueError):
        client.move_to_ir("123", 3, 'a"] x')
