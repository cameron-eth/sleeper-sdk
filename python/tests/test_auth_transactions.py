"""Waiver claim and free-agent add must use the mutations the web client sends."""
import base64
import json
import time

from sleeper.auth.client import SleeperAuthClient


def _fake_jwt() -> str:
    payload = {"user_id": "1", "display_name": "t", "iat": 0, "exp": int(time.time()) + 3600}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def _client_recording_calls():
    client = SleeperAuthClient(token=_fake_jwt())
    calls = []
    client.gql = lambda op, query, variables=None: calls.append((op, query, variables)) or {}
    return client, calls


def test_waiver_claim_uses_submit_waiver_claim_with_bid():
    client, calls = _client_recording_calls()
    client.submit_waiver_claim("123", 3, add_player_id="13346", drop_player_id="5995", faab_bid=12)
    op, query, variables = calls[0]
    assert op == "submit_waiver_claim"
    assert "submit_waiver_claim(" in query and "create_waiver_claim" not in query
    assert variables["k_adds"] == ["13346"] and variables["v_adds"] == [3]
    assert variables["k_drops"] == ["5995"] and variables["v_drops"] == [3]
    assert variables["k_settings"] == ["waiver_bid"] and variables["v_settings"] == [12]


def test_free_agent_add_uses_league_create_transaction():
    client, calls = _client_recording_calls()
    client.add_drop("123", 3, add_player_id="13346")
    op, query, variables = calls[0]
    assert op == "league_create_transaction"
    assert "create_free_agent" not in query
    assert variables["type"] == "free_agent"
    assert variables["k_adds"] == ["13346"] and variables["k_drops"] == []
