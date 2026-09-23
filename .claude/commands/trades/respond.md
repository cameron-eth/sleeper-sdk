---
description: "Accept or reject a pending trade offer. Writes to a real league; previews the exact payload first and requires confirmation before executing."
argument-hint: "<transaction_id> --username <name> --leg N --accept|--reject"
disable-model-invocation: true
---
# trades:respond

Accepts or rejects a pending trade.

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
# 1. Find the transaction_id and leg
python3 -m sleeper.cli inbox <username> --league "<league>"

# 2. Preview the response — nothing is sent
python3 -m sleeper.cli trade-respond <transaction_id> \
  --username <username> --league "<league>" --leg 0 --accept

# 3. After the user confirms
python3 -m sleeper.cli execute <preview_id>
```

## Key context

- **Accepting is final and immediate.** It moves real players between real
  rosters. Restate both sides of the trade in plain language and get an
  explicit yes before executing — never infer approval from the user having
  asked you to evaluate the offer.
- **Evaluate before responding.** `/trades:check` scores the give/get with
  the value adjustment; a raw KTC sum misprices consolidation in both
  directions.
- `--leg` identifies which side of a multi-team trade to respond as. It
  comes from `inbox`; do not guess it.
- Rejecting is safe and reversible in the sense that the partner can re-send.
