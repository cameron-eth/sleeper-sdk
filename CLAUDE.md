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

`SleeperClient` is async-first and namespaced: `client.users`, `.leagues`, `.drafts`, `.players`, `.state`, `.projections`. Only `get_all_players()` hangs off the client directly. `client.sync()` exists for one-shot sync use.

`.projections` is the one namespace on a **second host** (`api.sleeper.com`, no `/v1`), so the client owns two `HttpClient`s and `close()` must close both. Adding another endpoint family on that host goes through `_projections_http`, not `_http`.

### Projections and start/sit

`api/projections.py` (fetch) → `enrichment/projections.py` (league scoring) → `analytics/start_sit.py` (decision) → `agent/helpers.py` + `cli/projections.py`. The analytics layer is pure; all I/O is in the agent/CLI layer, so start/sit tests run offline against `tests/fixtures/projections_week.json` — six real captured records covering playing / bye / no-team.

League points are a dot product of the projection's `stats` and the league's `scoring_settings`, which share a key vocabulary (`pass_yd`, `rec`, `bonus_rec_te`, `pts_allow_21_27`). Taking the intersection means non-scoring keys (`gp`, `cmp_pct`, `adp_*`, and Sleeper's own `pts_*`) drop out on their own — **never add `pts_ppr` to a scoring dict**, it double-counts the whole stat line.

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
- **Availability outranks the projection in start/sit.** Sleeper still serves a projection for a player ruled out on Friday. `compare_projections` sorts on `(availability_rank, -points)` so points can never promote someone who cannot play; `projection_points()` omits them for the same reason. Bypassing `build_candidate` to score the stat line directly reintroduces the bug.
- **A bye is an absent `pts_ppr`, not a zero.** `stats.get("pts_ppr", 0.0)` yields the right ordering by accident while reporting a bye as a genuine projection of 0.0. Use `is_bye` / `has_projection`.
- **The two projection endpoints disagree about byes and about the player blob.** The position sweep includes a bye row with `game_id: None` and an embedded `player`; `get_player_weeks` omits the bye week entirely and has **no** `player` object, so `name`/`position` read as None there. Code written against one silently misreads the other.

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
| **KTC page shape** | The rankings payload lives in `<script type="application/json" id="ktc-players">`, not a JS variable. The page still contains the text `var playersArray =`, but it reads `JSON.parse(document.getElementById('ktc-players').textContent)`. `_PLAYERS_ARRAY_RE` is kept only as a fallback. The page also embeds 3-5 record "featured" and risers/fallers arrays — don't mistake one for the board. |
| **KTC scrape fails loud** | `fetch_ktc_players()` raises `KTCScrapeError` on no payload or a board under `_MIN_EXPECTED_PLAYERS` (100), and never caches a partial one. This is deliberate: the old code returned `[]` on a regex miss, so when KTC moved the payload the snapshot job kept exiting 0 and committed three days of `player_count: 0`, silently zeroing every KTC command. `snapshot_ktc.py --min-players` guards the write too, and `_snapshot_files()` skips empty snapshots by size so one can't become a `get_movers()` window boundary. |
| **KTC value cap** | Values cap at 9,999, so the very top players trade above their listed number. |
| **`TradedPick.owner_id`** | A **roster_id** (1–12), not a user_id. |
| **KTC match rate** | ~92% of players map to Sleeper IDs; rookies and backups may be missing. |
| **Cache** | `$TMPDIR/sleeper_sdk_cache/` — delete to force a refresh. Projections are **not** cached; they move during the week. |
| **`position` is singular** | The projections endpoint keeps only the *last* `position` param and returns `[]` for `position=QB,RB`. Pass a list to `get_week` and it fans out per position and merges. Omitting it returns the whole NFL (~9.4k rows, 5.7 MB). |
| **Most projection rows are filler** | A position sweep returns every rostered *and* unrostered player; only ~12% carry a real forecast (159 of 1364 week-1 WRs). Filter on `has_projection`. |
| **Unpriceable scoring rules** | Leagues score plain `fgmiss`, but Sleeper only projects `fgmiss_30_39` / `fgmiss_40_49`, so that rule cannot be priced and K/DEF totals drift slightly from the app's. `ScoredProjection.unmatched_scoring_keys` reports it. Skill positions are unaffected. |

## Secrets

`SLEEPER_TOKEN` (the session token authorizing trades/drafts) is read from the environment, or from a gitignored `.env` loaded by `sleeper/config.py` (see `.env.example`). It is only needed for write operations — `send-trade` and friends. Every read-only command, including all trade *discovery*, works without it.

## Skills

`.claude/commands/*.md` define ~18 slash-command skills wrapping the CLI (`gm-mode`, `find-trades`, `trade-guru`, `team-report`, `start-sit`, …). A few hardcode a specific user and league rather than taking parameters.

## Git

`main` is protected by the `protect-main` ruleset: PRs required (0 approvals), no force-push, no deletion, with repo admins as bypass actors. Feature branches are the norm; the KTC snapshot bot publishes through an auto-merged PR.
