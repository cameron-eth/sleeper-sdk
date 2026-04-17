"""Authenticated Sleeper client for write operations and private reads.

The public REST API at api.sleeper.app is read-only. Writes (trade proposals,
roster moves) and private reads (pending trades, rejected trades) go through
an undocumented GraphQL endpoint at https://sleeper.com/graphql.

Auth is a JWT captured from the web app (DevTools -> Network -> graphql ->
Request Headers -> `authorization`). There is no programmatic login.

Usage:
    export SLEEPER_TOKEN='eyJhbGci...'
    from sleeper.auth import SleeperAuthClient
    client = SleeperAuthClient()
    pending = client.get_trades(league_id, statuses=["pending"])
"""
from sleeper.auth.client import SleeperAuthClient, SleeperAuthError

__all__ = ["SleeperAuthClient", "SleeperAuthError"]
