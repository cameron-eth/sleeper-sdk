# team-report

Generate a comprehensive dynasty team analysis report for camfleety in The Meat Market. Combines roster values, market signals, trending players, and pick assets into a single data-driven report.

## When to use this skill

- "Give me a full team report"
- "How does my team look right now?"
- "Build me a dynasty report"
- "What's the state of my roster?"

## How to use it as an agent

Run ALL of the following commands and synthesize the results into a unified report:

### Step 1: Current roster
```bash
python3 -m sleeper.cli league-values camfleety --league "Meat Market" --format sf
```

### Step 2: League standings by roster value
```bash
python3 -m sleeper.cli roster-rank camfleety --league "Meat Market" --format sf
```

### Step 3: Pick assets
```bash
python3 -m sleeper.cli picks camfleety --league "Meat Market" --format sf --owner camfleety
```

### Step 4: Trending players (check if any on camfleety's roster are rising/falling)
```bash
python3 -m sleeper.cli trending --format sf --top 30
```
Cross-reference results with camfleety's roster.

### Step 5: Market value check on top 3-5 most valuable players
```bash
python3 -m sleeper.cli market-value "[top player 1]" --format sf
python3 -m sleeper.cli market-value "[top player 2]" --format sf
# etc.
```

### Step 6: Sell-high opportunities on camfleety's roster
```bash
python3 -m sleeper.cli buy-sell sell --format sf --min-trades 1
```

## Report structure

Compile results into this format:

---
## 🏈 Dynasty Team Report — camfleety (The Meat Market)
*Date: [current date]*

### Roster Overview
- Total KTC Value: [X]
- League Rank: [X of 12]
- Roster breakdown by position

### Best Assets
Top 5 players by KTC value with any market signals

### Pick Capital
Owned future picks with KTC values and total pick capital

### Action Items
- **Sell High**: Players trading above KTC — window to maximize return
- **Buy Low**: Market inefficiencies to exploit
- **Trending Up**: Players gaining value on your roster
- **Trending Down**: Players losing value on your roster (sell before it gets worse?)

### Strategic Summary
2-3 sentences on rebuild/contend status and recommended next moves

---

## Key context

- **camfleety's league**: The Meat Market (12-team dynasty, SF)
- **Record**: 3-25 (deep rebuild mode)
- **Format**: Superflex — QB value is premium
- **Username**: camfleety
