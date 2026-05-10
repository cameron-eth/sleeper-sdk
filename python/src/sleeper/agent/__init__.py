"""Agent-ergonomic helpers and primitives.

This subpackage exists to make it trivial for an external agent (Claude,
LangChain, custom cron loop, etc.) to consume the SDK. Three pieces:

* `envelope` — wrap any return value in a stable JSON shape with
  schema_version + ok flag + warnings + errors.
* `preview` — preview/execute pattern with on-disk caching for safe writes.
* `helpers` — high-level composites (build_context, optimal_lineup,
  check_lineup_health, summarize_inbox).

See AGENT_GUIDE.md for the recommended consumption pattern.
"""
from sleeper.agent.envelope import envelope, error_envelope, ok_envelope
from sleeper.agent.preview import (
    PreviewStore,
    create_preview,
    load_preview,
    consume_preview,
)
from sleeper.agent import helpers

__all__ = [
    "envelope",
    "error_envelope",
    "ok_envelope",
    "PreviewStore",
    "create_preview",
    "load_preview",
    "consume_preview",
    "helpers",
]
