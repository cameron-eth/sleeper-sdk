"""Authenticated GraphQL client for sleeper.com."""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx


SLEEPER_GRAPHQL_URL = "https://sleeper.com/graphql"


class SleeperAuthError(Exception):
    pass


@dataclass
class TokenInfo:
    user_id: str
    display_name: str
    issued_at: int
    expires_at: int

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def seconds_remaining(self) -> int:
        return max(0, int(self.expires_at - time.time()))


def _decode_jwt_payload(jwt: str) -> dict:
    parts = jwt.split(".")
    if len(parts) != 3:
        raise SleeperAuthError("Token does not look like a JWT (expected 3 dot-separated parts)")
    body = parts[1]
    # JWT base64url, may need padding
    body += "=" * (-len(body) % 4)
    try:
        decoded = base64.urlsafe_b64decode(body.encode())
        return json.loads(decoded)
    except Exception as e:
        raise SleeperAuthError(f"Failed to decode JWT payload: {e}") from e


def _normalize_errors(errs) -> list:
    """Sleeper returns errors in inconsistent shapes:
      - list of dicts (typical GraphQL)
      - single dict with {"message": "..."} (HTTP 500 path)
      - list of strings (rare)
    Always return a list of dicts so callers can iterate uniformly.
    """
    if isinstance(errs, dict):
        return [errs]
    if isinstance(errs, list):
        return [e if isinstance(e, dict) else {"message": str(e)} for e in errs]
    return [{"message": str(errs)}]


def inspect_token(jwt: str) -> TokenInfo:
    """Parse (but do not verify) a Sleeper JWT to see who it belongs to and when it expires."""
    payload = _decode_jwt_payload(jwt)
    return TokenInfo(
        user_id=str(payload.get("user_id", "")),
        display_name=payload.get("display_name", ""),
        issued_at=int(payload.get("iat", 0)),
        expires_at=int(payload.get("exp", 0)),
    )


class SleeperAuthClient:
    """GraphQL client for authenticated Sleeper operations.

    Reads `SLEEPER_TOKEN` from the environment by default. All requests hit
    https://sleeper.com/graphql with the token passed as the raw `authorization`
    header value (no `Bearer` prefix — that's the format Sleeper actually uses).
    """

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: str = SLEEPER_GRAPHQL_URL,
        timeout: float = 30.0,
    ):
        self.token = token or os.environ.get("SLEEPER_TOKEN")
        if not self.token:
            raise SleeperAuthError(
                "No token provided. Set SLEEPER_TOKEN env var or pass token=... "
                "(capture from DevTools -> Network -> graphql -> authorization header)."
            )
        self.base_url = base_url
        self._client = httpx.Client(timeout=timeout)
        self.token_info = inspect_token(self.token)

    def __enter__(self) -> "SleeperAuthClient":
        return self

    def __exit__(self, *a) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- transport ---------------------------------------------------------

    def _headers(self, op_name: str) -> dict:
        return {
            "authorization": self.token,
            "content-type": "application/json",
            "accept": "application/json",
            "origin": "https://sleeper.com",
            "referer": "https://sleeper.com/",
            "x-sleeper-graphql-op": op_name,
            "user-agent": "sleeper-sdk-python/0.1",
        }

    def gql(
        self,
        op_name: str,
        query: str,
        variables: Optional[dict] = None,
    ) -> dict:
        """Send a GraphQL request. Returns `data`; raises SleeperAuthError on errors."""
        if self.token_info.is_expired:
            raise SleeperAuthError(
                "Token is expired. Re-capture from the web app and update SLEEPER_TOKEN."
            )
        resp = self._client.post(
            self.base_url,
            headers=self._headers(op_name),
            json={
                "operationName": op_name,
                "query": query,
                "variables": variables or {},
            },
        )
        # Don't raise_for_status — errors come as 200 with an `errors` array.
        try:
            body = resp.json()
        except Exception:
            raise SleeperAuthError(
                f"Non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}"
            )
        if "errors" in body and body["errors"]:
            errs = _normalize_errors(body["errors"])
            codes = [e.get("code") for e in errs if isinstance(e, dict)]
            if "unauthorized" in codes:
                raise SleeperAuthError(
                    "Unauthorized — token may be invalid, expired, or lacking permissions"
                )
            raise SleeperAuthError(f"GraphQL errors: {errs}")
        return body.get("data") or {}

    # -- reads -------------------------------------------------------------

    def get_trades(
        self,
        league_id: str,
        statuses: Optional[list[str]] = None,
        legs: Optional[list[int]] = None,
        roster_ids: Optional[list[int]] = None,
        limit: int = 200,
    ) -> list[dict]:
        """Fetch trades for a league with arbitrary status filters.

        Valid status values (observed): "proposed", "complete", "rejected",
        "cancelled" (note British spelling), "vetoed".
        The public REST API only returns "complete"; this endpoint returns all.
        """
        query = """
        query league_transactions_filtered(
          $league_id: Snowflake!,
          $type_filters: [String],
          $status_filters: [String],
          $leg_filters: [Int],
          $roster_id_filters: [Int],
          $limit: Int
        ) {
          league_transactions_filtered(
            league_id: $league_id
            type_filters: $type_filters
            status_filters: $status_filters
            leg_filters: $leg_filters
            roster_id_filters: $roster_id_filters
            limit: $limit
          ) {
            transaction_id
            status
            type
            creator
            consenter_ids
            roster_ids
            created
            leg
            adds
            drops
            metadata
            settings
            draft_picks
            waiver_budget
          }
        }
        """
        data = self.gql("league_transactions_filtered", query, {
            "league_id": league_id,
            "type_filters": ["trade"],
            "status_filters": statuses,
            "leg_filters": legs,
            "roster_id_filters": roster_ids,
            "limit": limit,
        })
        return data.get("league_transactions_filtered") or []

    # -- writes ------------------------------------------------------------

    def propose_trade(
        self,
        league_id: str,
        adds: list[tuple[str, int]],
        drops: list[tuple[str, int]],
        draft_picks: Optional[list[str]] = None,
        waiver_budget: Optional[list[str]] = None,
        expires_at: Optional[int] = None,
    ) -> dict:
        """Propose a trade.

        Args:
            league_id: Sleeper league id
            adds: list of (player_id, receiving_roster_id) — who GETS each player
            drops: list of (player_id, sending_roster_id) — who SENDS each player
            draft_picks: optional pick payloads. Format is COMMA-separated, 5 ints:
                "original_owner_roster,season,round,from_roster,to_roster"
                e.g. "8,2026,1,7,8" = roster 8's own 2026 R1, currently held by
                roster 7, sent to roster 8.
            waiver_budget: optional FAAB transfers (format: "from_roster-to_roster-amount")
            expires_at: optional unix timestamp when the offer expires
        """
        k_adds = [p for p, _ in adds]
        v_adds = [r for _, r in adds]
        k_drops = [p for p, _ in drops]
        v_drops = [r for _, r in drops]

        query = """
        mutation propose_trade(
          $league_id: Snowflake!,
          $k_adds: [String], $v_adds: [Int],
          $k_drops: [String], $v_drops: [Int],
          $draft_picks: [String], $waiver_budget: [String],
          $expires_at: Int
        ) {
          propose_trade(
            league_id: $league_id
            k_adds: $k_adds, v_adds: $v_adds
            k_drops: $k_drops, v_drops: $v_drops
            draft_picks: $draft_picks
            waiver_budget: $waiver_budget
            expires_at: $expires_at
          ) {
            transaction_id
            status
            type
            created
            leg
            metadata
            settings
          }
        }
        """
        data = self.gql("propose_trade", query, {
            "league_id": league_id,
            "k_adds": k_adds, "v_adds": v_adds,
            "k_drops": k_drops, "v_drops": v_drops,
            "draft_picks": draft_picks or [],
            "waiver_budget": waiver_budget or [],
            "expires_at": expires_at,
        })
        return data.get("propose_trade") or {}

    def accept_trade(self, league_id: str, transaction_id: str, leg: int) -> dict:
        query = """
        mutation accept_trade($league_id: Snowflake!, $transaction_id: Snowflake!, $leg: Int!) {
          accept_trade(league_id: $league_id, transaction_id: $transaction_id, leg: $leg) {
            transaction_id status type created
          }
        }
        """
        data = self.gql("accept_trade", query, {
            "league_id": league_id,
            "transaction_id": transaction_id,
            "leg": leg,
        })
        return data.get("accept_trade") or {}

    def reject_trade(self, league_id: str, transaction_id: str, leg: int) -> dict:
        query = """
        mutation reject_trade($league_id: Snowflake!, $transaction_id: Snowflake!, $leg: Int!) {
          reject_trade(league_id: $league_id, transaction_id: $transaction_id, leg: $leg) {
            transaction_id status type created
          }
        }
        """
        data = self.gql("reject_trade", query, {
            "league_id": league_id,
            "transaction_id": transaction_id,
            "leg": leg,
        })
        return data.get("reject_trade") or {}

    def cancel_trade(self, league_id: str, transaction_id: str, leg: int) -> dict:
        """Cancel a trade YOU proposed (works while it's still in 'proposed' status)."""
        query = """
        mutation cancel_trade($league_id: Snowflake!, $transaction_id: Snowflake!, $leg: Int!) {
          cancel_trade(league_id: $league_id, transaction_id: $transaction_id, leg: $leg) {
            transaction_id status type created
          }
        }
        """
        data = self.gql("cancel_trade", query, {
            "league_id": league_id,
            "transaction_id": transaction_id,
            "leg": leg,
        })
        return data.get("cancel_trade") or {}

    # -- inbox / convenience reads ----------------------------------------

    def get_inbox(self, league_id: str, *, my_roster_id: int | None = None) -> list[dict]:
        """Pending incoming trades I have not yet consented to.

        Filters `get_trades(status='proposed')` to ones where my roster is a
        participant and has not yet consented. If `my_roster_id` is None this
        returns all proposed trades in the league.
        """
        trades = self.get_trades(league_id, statuses=["proposed"], limit=200)
        if my_roster_id is None:
            return trades
        out = []
        for t in trades:
            roster_ids = t.get("roster_ids") or []
            consenters = t.get("consenter_ids") or []
            if my_roster_id in roster_ids and my_roster_id not in consenters:
                out.append(t)
        return out

    def get_outbox(self, league_id: str, *, my_roster_id: int | None = None) -> list[dict]:
        """Pending outgoing trades I have proposed (i.e., I am a consenter
        but at least one other roster has not yet consented)."""
        trades = self.get_trades(league_id, statuses=["proposed"], limit=200)
        if my_roster_id is None:
            return trades
        out = []
        for t in trades:
            roster_ids = t.get("roster_ids") or []
            consenters = t.get("consenter_ids") or []
            if my_roster_id in roster_ids and my_roster_id in consenters:
                # someone else still hasn't consented
                if any(r not in consenters for r in roster_ids):
                    out.append(t)
        return out

    # -- roster mutations -------------------------------------------------

    def set_starters(
        self,
        league_id: str,
        roster_id: int,
        starters: list[str],
        *,
        leg: int | None = None,
    ) -> dict:
        """Set the starters list for a roster. Order must match league
        roster_positions slots (excluding BN/IR/TAXI)."""
        query = """
        mutation update_roster_starters(
          $league_id: Snowflake!, $roster_id: Int!, $starters: [String]!, $leg: Int
        ) {
          update_roster_starters(
            league_id: $league_id, roster_id: $roster_id, starters: $starters, leg: $leg
          ) {
            roster_id starters players reserve taxi
          }
        }
        """
        data = self.gql("update_roster_starters", query, {
            "league_id": league_id,
            "roster_id": roster_id,
            "starters": starters,
            "leg": leg,
        })
        return data.get("update_roster_starters") or {}

    def add_drop(
        self,
        league_id: str,
        roster_id: int,
        *,
        add_player_id: str | None = None,
        drop_player_id: str | None = None,
    ) -> dict:
        """Free-agent add and/or drop in a single transaction.

        Either or both of add_player_id / drop_player_id must be set.
        """
        if not add_player_id and not drop_player_id:
            raise ValueError("Specify at least one of add_player_id or drop_player_id")
        adds: dict = {add_player_id: roster_id} if add_player_id else {}
        drops: dict = {drop_player_id: roster_id} if drop_player_id else {}
        query = """
        mutation create_free_agent(
          $league_id: Snowflake!, $roster_id: Int!,
          $adds: JSON, $drops: JSON
        ) {
          create_free_agent(
            league_id: $league_id, roster_id: $roster_id,
            adds: $adds, drops: $drops
          ) {
            transaction_id status type created adds drops
          }
        }
        """
        data = self.gql("create_free_agent", query, {
            "league_id": league_id,
            "roster_id": roster_id,
            "adds": adds,
            "drops": drops,
        })
        return data.get("create_free_agent") or {}

    def submit_waiver_claim(
        self,
        league_id: str,
        roster_id: int,
        *,
        add_player_id: str,
        drop_player_id: str | None = None,
        faab_bid: int = 0,
    ) -> dict:
        """Submit a waiver claim. FAAB bid in dollars (0 if league uses
        priority instead of FAAB)."""
        adds = {add_player_id: roster_id}
        drops: dict = {drop_player_id: roster_id} if drop_player_id else {}
        query = """
        mutation create_waiver_claim(
          $league_id: Snowflake!, $roster_id: Int!,
          $adds: JSON, $drops: JSON, $waiver_budget: Int
        ) {
          create_waiver_claim(
            league_id: $league_id, roster_id: $roster_id,
            adds: $adds, drops: $drops, waiver_budget: $waiver_budget
          ) {
            transaction_id status type created adds drops settings
          }
        }
        """
        data = self.gql("create_waiver_claim", query, {
            "league_id": league_id,
            "roster_id": roster_id,
            "adds": adds,
            "drops": drops,
            "waiver_budget": int(faab_bid),
        })
        return data.get("create_waiver_claim") or {}

    def cancel_waiver_claim(self, league_id: str, transaction_id: str, leg: int) -> dict:
        query = """
        mutation cancel_waiver_claim($league_id: Snowflake!, $transaction_id: Snowflake!, $leg: Int!) {
          cancel_waiver_claim(league_id: $league_id, transaction_id: $transaction_id, leg: $leg) {
            transaction_id status type created
          }
        }
        """
        data = self.gql("cancel_waiver_claim", query, {
            "league_id": league_id,
            "transaction_id": transaction_id,
            "leg": leg,
        })
        return data.get("cancel_waiver_claim") or {}

    def move_to_taxi(self, league_id: str, roster_id: int, player_id: str) -> dict:
        """Move a rostered player to the taxi squad."""
        query = """
        mutation move_to_taxi($league_id: Snowflake!, $roster_id: Int!, $player_id: String!) {
          move_to_taxi(league_id: $league_id, roster_id: $roster_id, player_id: $player_id) {
            roster_id taxi
          }
        }
        """
        data = self.gql("move_to_taxi", query, {
            "league_id": league_id,
            "roster_id": roster_id,
            "player_id": player_id,
        })
        return data.get("move_to_taxi") or {}

    def move_to_ir(self, league_id: str, roster_id: int, player_id: str) -> dict:
        """Move a rostered player to IR."""
        query = """
        mutation move_to_ir($league_id: Snowflake!, $roster_id: Int!, $player_id: String!) {
          move_to_ir(league_id: $league_id, roster_id: $roster_id, player_id: $player_id) {
            roster_id reserve
          }
        }
        """
        data = self.gql("move_to_ir", query, {
            "league_id": league_id,
            "roster_id": roster_id,
            "player_id": player_id,
        })
        return data.get("move_to_ir") or {}

    def activate_from_ir(self, league_id: str, roster_id: int, player_id: str) -> dict:
        """Activate an IR player back to the active roster."""
        query = """
        mutation activate_from_ir($league_id: Snowflake!, $roster_id: Int!, $player_id: String!) {
          activate_from_ir(league_id: $league_id, roster_id: $roster_id, player_id: $player_id) {
            roster_id reserve players
          }
        }
        """
        data = self.gql("activate_from_ir", query, {
            "league_id": league_id,
            "roster_id": roster_id,
            "player_id": player_id,
        })
        return data.get("activate_from_ir") or {}
