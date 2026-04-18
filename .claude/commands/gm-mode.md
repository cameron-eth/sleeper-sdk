# gm-mode

Full dynasty team analysis with archetype classification. Tells the owner whether they're a **CONTENDER**, **RELOADING**, **REBUILDING**, or **PRETENDER** team, then recommends trade strategy to match.

## When to use this skill

- "GM mode"
- "What kind of team am I?"
- "Am I a contender or rebuilding?"
- "Give me a full GM analysis"
- "Should I be buying or selling?"
- "Classify my team"

## How it works

The `gm-mode` command:

1. Pulls the owner's full roster + league rosters from Sleeper
2. Computes total KTC value (including starters, bench, and future picks)
3. Ranks the owner vs the league on **team value** AND **season production (fpts)**
4. Calculates positional strength/weakness (QB/RB/WR/TE) vs league averages
5. Evaluates roster age and % of value in players under 26
6. Classifies archetype:
   - **CONTENDER** — top-tier value + winning now
   - **RELOADING** — mid-tier value, retooling for a window
   - **REBUILDING** — bottom-tier value OR young-heavy accumulation mode
   - **PRETENDER** — 3rd-6th in team value BUT 6th-9th in production AND older roster (classic dead-zone warning)
7. Emits a strategic recommendation matched to the archetype

## How to run

### Primary report
```bash
python3 -m sleeper.cli gm-mode camfleety --league "Meat Market" --format sf
```

### Analyze another owner in the same league
```bash
python3 -m sleeper.cli gm-mode camfleety --league "Meat Market" --owner "someone_else" --format sf
```

### 1QB league
```bash
python3 -m sleeper.cli gm-mode camfleety --league "Slime Season" --format 1qb
```

## What to do with the output

The report prints:
- Archetype + confidence + one-line reasoning
- Total KTC value, league rank, production rank, age, youth %
- Position table (starters/bench/total, league avg, rank, STRONG/AVG/WEAK, DEEP/AVG/SHALLOW)
- Top 5 assets
- Liabilities (aging players with high KTC — obvious sell candidates)
- Strategic recommendation + buy/sell targets

**Follow-ups the user will usually want:**

1. If archetype is **PRETENDER** → urgent sell-off of aging vets. Chain with:
   ```bash
   python3 -m sleeper.cli find-trades camfleety --league "Meat Market" --mode downtiering --include "<aging vet name>"
   ```

2. If archetype is **REBUILDING** → accumulate picks/youth. Chain with:
   ```bash
   python3 -m sleeper.cli find-trades camfleety --league "Meat Market" --mode upgrade --position <weak pos>
   ```

3. If archetype is **CONTENDER** → close gaps, lock in wins. Chain with:
   ```bash
   python3 -m sleeper.cli find-trades camfleety --league "Meat Market" --mode normal --position <weak pos>
   ```

4. If archetype is **RELOADING** → selective moves. Use `suggest-trades` for the full league scan:
   ```bash
   python3 -m sleeper.cli suggest-trades camfleety --league "Meat Market" --top 10
   ```

## Key context

- **Format**: Superflex ("sf") is default — QB values are premium
- **Archetype confidence**: 0.0–1.0 scale; below 0.7 means edge case (usually RELOADING)
- **Production rank** = rank by `roster.settings.fpts`; early season = unreliable
- **Young value %** = sum of KTC from rostered players under 26 ÷ total player KTC
