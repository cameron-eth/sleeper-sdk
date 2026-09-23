# Sleeper SDK — Skill Layer

**Skills (this directory)** are the high-level "what to do when the user
asks X" specs. **CLI commands** (in the `python/src/sleeper/cli/`
package — split across `values.py`, `trades.py`, `send_trade.py`,
`analysis.py`, `projections.py` — and `cli_agent.py`) are the low-level
primitives skills orchestrate.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  USER PROMPT — "GM mode for OGs"                        │
└─────────────────┬───────────────────────────────────────┘
                  ▼
┌─────────────────────────────────────────────────────────┐
│  SKILL LAYER  (.claude/commands/*.md)                   │
│    • Maps natural-language intent → CLI commands         │
│    • Documents follow-up chains for deep analysis        │
│    • Lives outside the Python package — pure markdown    │
└─────────────────┬───────────────────────────────────────┘
                  ▼ runs / chains
┌─────────────────────────────────────────────────────────┐
│  CLI LAYER  (python -m sleeper.cli <command>)            │
│    • Each subcommand = one analysis primitive            │
│    • Composable via shell pipes / multiple invocations   │
│    • Reads from KTC + Sleeper API + local snapshots      │
└─────────────────┬───────────────────────────────────────┘
                  ▼ imports from
┌─────────────────────────────────────────────────────────┐
│  ANALYTICS LAYER  (sleeper.analytics.*)                  │
│    • Pure functions: value_adjustment, gm_mode, …        │
│    • Tested in python/tests/                             │
└─────────────────────────────────────────────────────────┘
```

## Skill ↔ CLI map

Skills are namespaced by subdirectory: `.claude/commands/trades/find.md`
is invoked as `/trades:find`.

### roster/ — the team you control
| Skill | CLI | Auth? |
|---|---|---|
| `/roster:lineup` | `lineup`, `lineup-health` | token |
| `/roster:start-sit` | `start-sit` | none |
| `/roster:projections` | `projections` | none |
| `/roster:waivers` | `waivers` | token |
| `/roster:values` | `league-values`, `roster` | none / token |
| `/roster:picks` | `picks` | none |
| `/roster:draft` | `scripts/draft_assist.py` | none |
| `/roster:set-lineup` ✍ | `lineup-set` | token |
| `/roster:moves` ✍ | `add`, `drop`, `waiver-claim` | token |
| `/roster:slots` ✍ | `taxi-move`, `ir-move`, `activate` | token |

### trades/
| Skill | CLI | Auth? |
|---|---|---|
| `/trades:find` | `find-trades` | none |
| `/trades:check` | `trade-check` | none |
| `/trades:suggest` | `suggest-trades` | none |
| `/trades:partners` | `trade-partners` | none |
| `/trades:proposed` | `proposed-trades` | token |
| `/trades:inbox` | `inbox`, `outbox` | token |
| `/trades:guru` | chains find → check → proposed | none |
| `/trades:respond` ✍ | `trade-respond` | token |

### market/ — player valuation, independent of any roster
| Skill | CLI |
|---|---|
| `/market:value` | `market-value` |
| `/market:buy-sell` | `buy-sell` |
| `/market:pe-ratio` | `pe-ratio` |
| `/market:trending` | `trending` |
| `/market:ktc-trend` | `ktc-trend` |

### league/
| Skill | CLI | Auth? |
|---|---|---|
| `/league:rank` | `roster-rank` | none |
| `/league:matchup` | `matchup` | token |
| `/league:status` | `status`, `context`, `whoami`, `auth-check` | token |

### strategy/ — composites
| Skill | What it chains |
|---|---|
| `/strategy:gm-mode` | `gm-mode` |
| `/strategy:team-report` | `gm-mode` → `find-trades` → `pe-ratio` |
| `/strategy:data-scientist` | open-ended, any of the above |

✍ = write. Carries `disable-model-invocation: true` and previews before
firing. `/roster:draft` is also user-invoked only — it polls in an
unbounded loop.

**Not exposed as a skill, by design:** `send-trade` fires a real
`propose_trade`. Documented in CLI `--help` only; explicit user trigger.
`execute` / `preview-show` are mechanics of the write flow rather than
tasks, and are documented inside each write skill.

## Parallel execution principle

When a skill needs deep analysis, it should run **independent CLI
commands in parallel** (multiple Bash tool calls in one message), not
sequentially. Example for "give me a full read on my OGs team":

```
parallel:
  - sleeper gm-mode camfleety --league OGs
  - sleeper roster-rank camfleety --league OGs
  - sleeper picks camfleety --league OGs
  - sleeper proposed-trades camfleety --league OGs --status complete
```

…then synthesize.

## Adding a new skill

1. Add the command function (`def cmd_<name>(args)`) in the right
   `python/src/sleeper/cli/` submodule:
   - `values.py` — read-only KTC + valuation
   - `trades.py` — trade scoring/search
   - `analysis.py` — picks, gm-mode, proposed-trades
   - `send_trade.py` — only for write ops
   - `cli_agent.py` — only for auth-required agent commands
2. Wire it into argparse + the dispatch table in `cli/_main.py`.
3. Add a smoke test entry in `python/tests/test_cli_smoke.py`.
4. If there's pure math involved, extract it into a new
   `sleeper.analytics.<name>` module and write unit tests in
   `python/tests/test_<name>.py`.
5. Create `commands/<category>/<name>.md` — pick the category from the
   map above; it becomes the `/category:name` namespace. Required
   frontmatter:
   ```yaml
   ---
   description: "What it decides, then the phrasings a user actually types. CI rejects anything under 40 characters."
   argument-hint: "<username> [--league <name>]"
   # writes only:
   disable-model-invocation: true
   ---
   ```
   Then the body: **When to use this skill** (triggers), **How to run**
   (concrete invocations), **Key context** (gotchas the agent must know).
   Take the user and league as arguments — never hardcode either.
6. Run `python3 scripts/sync_skills.py` to regenerate the Codex mirror.
   CI fails if you skip this.
7. Open a PR. CI must be green before merge.

## Repository hygiene rules

- **No file over 750 LOC.** Two files currently exceed this and are the
  standing refactor targets: `agent/helpers.py` at 773 and `cli_agent.py`
  at 767. Nothing enforces the rule, so it is on the author to check.
  (`cli/values.py`, named here as "current largest at 586" until
  2026-09, is 577 and no longer close to the top.)
- **Pure logic lives in `analytics/`** and is unit-tested. Recent
  extractions: `chip_value.py`, `pick_value.py`, `find_trades_engine.py`.
- **CLI command handlers are thin** — they orchestrate analytics
  primitives, never re-implement math inline.
- **Shared CLI helpers live in `cli/_common.py`** — every command module
  imports from there (DRY by convention).
- **Auth code is isolated in `auth/`**; every write goes through the
  preview/execute pattern in `agent/preview.py`.
- **Skills are markdown-only** — never Python in `.claude/commands/`.
