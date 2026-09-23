---
description: "Add, drop, or submit a FAAB waiver claim. Writes to a real league; previews the exact payload first and requires confirmation before executing."
argument-hint: "<username> --player \"<name>\" [--drop \"<name>\"] [--faab N]"
disable-model-invocation: true
---
# roster:moves

Free-agent adds, drops, and waiver claims.

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
# Add, dropping someone to make room
python3 -m sleeper.cli add <username> --league "<league>" \
  --player "Incoming Player" --drop "Outgoing Player"

# Drop alone
python3 -m sleeper.cli drop <username> --league "<league>" --player "Name"

# Waiver claim with a FAAB bid
python3 -m sleeper.cli waiver-claim <username> --league "<league>" \
  --add "Target" --drop "Cut" --faab 12

# After the user confirms
python3 -m sleeper.cli execute <preview_id>
```

## Key context

- **A drop is effectively irreversible.** The player hits waivers or the
  free-agent pool and any leaguemate can take them. Confirm the drop
  explicitly, by name, before executing — not just the add.
- **Check the roster is actually full first.** `/roster:lineup` and
  `/roster:waivers` establish whether a drop is needed at all.
- FAAB budgets do not replenish. A bid is a real allocation for the season.
- Dropping a player on an active roster slot leaves a hole that scores zero
  if the week starts before it is filled.
