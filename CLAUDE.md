# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

The installable package lives in `python/` — that is the working directory for nearly every command. The root also holds `data/` (committed KTC snapshots), `.claude/commands/` (skills), and two legacy standalone files (`sleeper_wrapper.py`, `sleeper-api.ts`) that are **not** part of the package and are not imported by it.

## Commands

All from `python/`:

```bash
pip3 install .                 # editable install does NOT work here — always reinstall
pytest tests/ -v               # full suite
pytest tests/test_value_adjustment.py::test_no_tier_cliff_across_6000_boundary -v  # single test
mypy src/sleeper --ignore-missing-imports                                       # CI gate
```

CI (`.github/workflows/`) runs **pytest on 3.11 + 3.12** and **mypy**; both must pass. Run mypy locally before pushing — it is easy to forget and it gates merges.

Run the CLI from source without reinstalling: `PYTHONPATH=src python -m sleeper.cli <command>`. Note the installed `sleeper` binary is a *copy*, so source edits do not affect it until you reinstall.

## Architecture

Four stacked layers, each usable alone (see `README.md` for the diagram): Sleeper API → enrichment (KTC values, NFL stats) → analytics → decision layer (CLI + skills).

`SleeperClient` is async-first and namespaced: `client.users`, `.leagues`, `.drafts`, `.players`, `.state`. Only `get_all_players()` hangs off the client directly. `client.sync()` exists for one-shot sync use.

### Two trade systems coexist

This is the most important thing to know before touching trade logic. Both are live and they do not share a value model:

- **Legacy** — `find_trades_engine.py`, `trade_suggestions.py`, `partner_match.py`, driven by `cli/trades.py`. This is what the shipped `find-trades` / `suggest-trades` CLI commands use. Values assets at raw KTC and scores packages against a fixed overpay band.
- **Window-relative stack (L0–L3)** — `base_value.py` → `league_model.py` + `pick_ownership.py` → `contextual_value.py` → `trade_runtime.py`. Newer, tested, and validated against live leagues, but **not yet wired into the CLI**; it is currently driven by ad-hoc scripts. Wiring it in (or retiring the legacy path) is the natural next step.

Design notes for the L0–L3 stack live in the module docstrings, which record *why* each decision was made. Read them before changing constants — several encode bugs that were found the hard way.

### Invariants that are easy to break

- **L0 applies no age multiplier.** KTC already prices age in, and survivorship makes a cross-sectional age curve unfittable. Age is metadata; the window re-weighting at L2 is where age matters. Multiplying by an age factor in `base_value.py` double-counts.
- **Contextual value is a preference, not a currency.** It cannot be spent. Window re-weighting (`ALPHA` in `contextual_value.py`) must stay small enough to break ties among market-fair trades, never to justify market-losing ones. The market-realism gate in `trade_runtime.py` is the primary guardrail, not a formality.
- **Two mirrored sign conventions for the consolidation premium.** `find_trades_engine.package_overpay` works in *overpay space* (positive = you overpay, so the premium is subtracted); `value_adjustment.apply_adjustment_to_delta` works in *net-value space* (positive = you gain, so acquiring the stud adds and shipping it subtracts). Conflating them has produced real bugs in both directions. The Hopkins case in `tests/test_value_adjustment.py` is the regression guard.
- **Sub-2nd-round players are not full trade currency.** ~76% of KTC's pool sits below a mid-2nd; `tradeable_value()` discounts them. Picks are exempt.

Tests for these modules assert *properties* (monotonic, bounded, no cliffs, correct sign) rather than pinning magic numbers, so recalibration should not require rewriting them. Keep that style.

## Data and external sources

`data/ktc/` holds daily KTC snapshots committed by a scheduled workflow; `latest.json` is what analytics reads. Records are `{ktc_id, name, position, team, age, sf_value, sf_rank, sf_pos_rank, oqb_value, ...}`. Draft picks appear as `position: "RDP"` rows named `"2027 Mid 1st"` (seasons 2026–28, rounds 1–4, Early/Mid/Late).

Sleeper models pick ownership by *exception*: every team implicitly owns its own picks, and `get_traded_picks()` records only those that moved. In each record `roster_id` is the pick's **original** team (which sets its draft slot) and `owner_id` is the current holder.

## Gotchas

| Issue | Detail |
|---|---|
| **Season** | Always query the **current** season. League IDs change every year, so a hardcoded season silently returns a stale roster from a different league. |
| **Name matching** | Match on `Player.full_name`; `search_full_name` has no spaces (`"calebwilliams"`). |
| **Missing ages** | KTC omits age for many rookies (arrives as `-1`). Backfill from Sleeper via `Asset.with_age()`, or young assets read as prime-aged. |
| **Python 3.9 event loop** | Multiple `asyncio.run()` / `sync()` calls fail. Batch all async work into one block. |
| **KTC SSL on macOS** | System Python 3.9 has outdated SSL; `_fetch_page()` falls back to `curl` automatically. |
| **KTC value cap** | Values cap at 9,999, so the very top players trade above their listed number. |
| **`TradedPick.owner_id`** | A **roster_id** (1–12), not a user_id. |
| **KTC match rate** | ~92% of players map to Sleeper IDs; rookies and backups may be missing. |
| **Cache** | `$TMPDIR/sleeper_sdk_cache/` — delete to force a refresh. |

## Secrets

`SLEEPER_TOKEN` (the session token authorizing trades/drafts) is read from the environment, or from a gitignored `.env` loaded by `sleeper/config.py` (see `.env.example`). It is only needed for write operations — `send-trade` and friends. Every read-only command, including all trade *discovery*, works without it.

## Skills

`.claude/commands/*.md` define ~17 slash-command skills wrapping the CLI (`gm-mode`, `find-trades`, `trade-guru`, `team-report`, …). A few hardcode a specific user and league rather than taking parameters.

## Git

`main` is protected by the `protect-main` ruleset: PRs required (0 approvals), no force-push, no deletion, with repo admins as bypass actors. Feature branches are the norm; the KTC snapshot bot publishes through an auto-merged PR.
