---
description: "Set the starting lineup for a week. Writes to a real league; previews the exact payload first and requires confirmation before executing."
argument-hint: "<username> --starters \"<p1>,<p2>,...\" [--league <name>]"
disable-model-invocation: true
---
# roster:set-lineup

Sets starters for the current week.

> **Every write previews by default.** Running the command without
> `--execute` builds the exact payload Sleeper would receive, prints it with
> a `preview_id`, and caches it to `~/.sleeper-sdk/previews/` for 10 minutes.
> Nothing has happened yet. `sleeper preview-show <id>` re-reads it and
> `sleeper execute <id>` (or re-running with `--execute`) fires it.
>
> **Show the preview to the user and get a yes before executing.** This
> mutates a real roster in a real league, and several of these moves cannot
> be undone from the SDK.

## How to run

```bash
# 1. See what would change — nothing is sent
python3 -m sleeper.cli lineup-set <username> --league "<league>" \
  --starters "Player One,Player Two,Player Three"

# 2. After the user confirms
python3 -m sleeper.cli execute <preview_id>
```

## Key context

- **Work out the right lineup before writing it.** `/roster:lineup` shows
  current vs optimal and `/roster:start-sit` settles individual calls.
  This skill only applies a decision already made.
- `--starters` is the **full** starting lineup, in slot order — not a diff.
  Omitting a player benches them.
- A preview expires after 10 minutes. Past that, re-run to get a fresh one
  rather than executing a stale `preview_id`.
