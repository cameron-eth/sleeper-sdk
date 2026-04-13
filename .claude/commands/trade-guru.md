# trade-guru

Act as a dynasty trade analyst for camfleety's team in The Meat Market. Given a goal or a player of interest, explore trade scenarios, evaluate fairness, and recommend actionable deals.

## When to use this skill

- "What should I trade for?" / "What should I sell?"
- "Find me a trade package to get [player]"
- "Who on my roster should I sell high on?"
- "Help me rebuild/contend"
- "Evaluate this trade: [give X for Y]"

## How to use it as an agent

When this skill is invoked, you should:

1. **Get current roster state**
   ```bash
   python3 -m sleeper.cli league-values camfleety --league "Meat Market" --format sf
   ```

2. **Get full league roster rankings** to understand where camfleety sits and what other teams need
   ```bash
   python3 -m sleeper.cli roster-rank camfleety --league "Meat Market" --format sf
   ```

3. **Find sell-high candidates on camfleety's roster**
   ```bash
   python3 -m sleeper.cli buy-sell sell --format sf --min-trades 1
   ```
   Cross-reference with camfleety's actual roster players.

4. **Find buy-low targets** that would improve the roster
   ```bash
   python3 -m sleeper.cli buy-sell buy --format sf --min-trades 1
   ```

5. **Check trending players** for short-term buy/sell windows
   ```bash
   python3 -m sleeper.cli trending --format sf --direction up --top 15
   python3 -m sleeper.cli trending --format sf --direction down --top 15
   ```

6. **Evaluate specific trade scenarios**
   ```bash
   python3 -m sleeper.cli trade-check --give "[player A]" --get "[player B]" "[pick]" --format sf
   ```

7. **Check market value** for key players in a proposed deal
   ```bash
   python3 -m sleeper.cli market-value "[player name]" --format sf
   ```

## Analysis framework

When building trade recommendations:
- **Roster construction**: What positions are deep/thin for camfleety?
- **Win-now vs rebuild**: camfleety is 3-25 all-time — lean toward rebuild/youth
- **Target teams**: Who in the league has weak rosters and veteran players to sell?
- **Value arbitrage**: Who is sell-high on KTC vs actual market?
- **Age curve**: Prioritize players under 26 for dynasty value

## Key context

- **camfleety's league**: The Meat Market (12-team dynasty, SF, league_id: 1328460395249172480)
- **camfleety's record**: 3-25 all-time (needs a rebuild)
- **Format**: Superflex (SF) — QBs are extremely valuable
- **Username**: camfleety (no underscore)

## Output format

Provide:
1. **Roster assessment** — strengths, weaknesses, age curve
2. **Top sell-high candidates** — players to move
3. **Top trade targets** — players to acquire
4. **2-3 specific trade proposals** with `trade-check` output
5. **Strategic recommendation** — rebuild path or contention window
