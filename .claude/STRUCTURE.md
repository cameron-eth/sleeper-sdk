# Sleeper SDK — Skill Layer

**Skills (this directory)** are the high-level "what to do when the user
asks X" specs. **CLI commands** (in the `python/src/sleeper/cli/`
package — split across `values.py`, `trades.py`, `send_trade.py`,
`analysis.py` — and `cli_agent.py`) are the low-level primitives skills
orchestrate.

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

### Read-only analysis
| Skill | CLI command | Auth? |
|---|---|---|
| `market-value` | `market-value` | none |
| `league-values` | `league-values` | none |
| `roster-rank` | `roster-rank` | none |
| `picks` | `picks` | none |
| `trending` | `trending` | none |
| `buy-sell` | `buy-sell` | none |
| `pe-ratio` | `pe-ratio` | none |
| `ktc-trend` | `ktc-trend` | none |
| `trade-check` | `trade-check` | none |
| `gm-mode` | `gm-mode` | none |
| `find-trades` | `find-trades` | none |
| `suggest-trades` | `suggest-trades` | none |

### Authenticated reads (require `SLEEPER_TOKEN`)
| Skill | CLI command |
|---|---|
| `proposed-trades` | `proposed-trades` |

### Composite skills (orchestrate multiple CLI commands)
| Skill | What it chains |
|---|---|
| `team-report` | `gm-mode` → `find-trades` → `pe-ratio` |
| `trade-guru` | `find-trades` (multi-mode) → `trade-check` → `proposed-trades` |
| `data-scientist` | open-ended analysis using any of the above |

### Authenticated writes (no skill exposed — explicit user trigger only)
- `send-trade` — fires a real `propose_trade` mutation. Documented in
  CLI `--help` only; no skill markdown by design.

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
5. Create `<command-name>.md` in this directory describing:
   - **When to use this skill** — natural-language triggers
   - **How to run** — concrete CLI invocations
   - **Useful follow-ups** — which other skills/commands chain in
   - **Key context** — gotchas the agent must know
6. Open a PR. CI must be green before merge.

## Repository hygiene rules

- **No file over 750 LOC.** Current largest: `cli/values.py` at 586.
- **Pure logic lives in `analytics/`** and is unit-tested. Recent
  extractions: `chip_value.py`, `pick_value.py`, `find_trades_engine.py`.
- **CLI command handlers are thin** — they orchestrate analytics
  primitives, never re-implement math inline.
- **Shared CLI helpers live in `cli/_common.py`** — every command module
  imports from there (DRY by convention).
- **Skills are markdown-only** — never Python in `.claude/commands/`.
- CLI command handlers are thin wrappers that orchestrate analytics
- Auth code is isolated in `auth/`
- Skills are markdown-only; never put Python in `.claude/commands/`
