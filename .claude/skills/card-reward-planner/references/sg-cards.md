# Singapore cashback cards - planner reference

This is working knowledge for reasoning, not a source of truth for numbers. The
numbers the app uses live in `data/cards.json` and carry explicit `assumptions`.
Bank T&Cs change quarterly; when a user quotes a rate that differs from
`cards.json`, trust the user and offer to update the JSON.

## How the three modelled cards actually behave

| Card | Real mechanic | v1 simplification |
|---|---|---|
| DBS Live Fresh | 5% on online + contactless/Visa payWave spend, separate small caps per tier, $600/mo minimum, 0.3% otherwise | Flat 5% on Shopping, Dining, Transport; single $20 cap; $600 min |
| UOB One | Tiered *quarterly* rebate ($500 / $1,000 / $2,000 per month for 3 consecutive months), min 5 transactions/month, higher rates at selected merchants | Monthly $500 min; Groceries 10%, Transport/Utilities 6%, Dining 4%; $50/mo cap |
| OCBC 365 | 6% dining (5% weekday / 6% weekend historically), 3% groceries/land transport/utilities/online travel, $800/mo min, $80 cap | Single 6% dining; 3% on groceries, transport, utilities; $800 min; $80 cap |

Common real-world wrinkles the engine does **not** model - mention them as
watch-outs when relevant:

- Quarterly consistency (UOB One): missing the minimum in *one* month of the quarter
  forfeits the whole quarter's rebate.
- Transaction-count minimums (e.g. 5 per month) and excluded MCCs (insurance,
  education, government, top-ups like EZ-Link/GrabPay wallet loads).
- Cashback credited the following month, so a cap "resets" on the statement cycle,
  not the calendar month.
- Foreign-currency fees (~3.25%) can wipe out a 5% bonus on overseas online spend.

## Leakage patterns to look for

1. **Spread too thin.** Three cards each below their minimum -> every category at base
   rate. Fix: consolidate onto the one or two cards whose minimums the spend can
   actually clear. The engine's optimal allocation shows which.
2. **Right category, wrong card.** Groceries on a dining card. The suboptimal table
   surfaces this row by row.
3. **Cap saturation.** A $20 cap on a 5% card is exhausted by $400 of spend; the next
   $1,000 earns 0.3%. Anything after `CAP HIT` should move.
4. **Minimum-spend padding that costs more than it earns.** Moving $300 of 10% groceries
   to pad a $600 minimum on a 5% card loses money. Always score both.
5. **General Spend blind spot.** Unmapped merchants earn only base rates on every card;
   if the General Spend bucket is >20% of spend, a flat 1.5% card often beats the
   bonus cards for that bucket.

## How to read the CLI output

```
earned $58.75 (2.08%)  optimal $125.19 (4.44%)  MISSED $66.44
```
- `earned` = what the statement actually paid, chronological cap accounting.
- `optimal` = per-transaction re-allocation across the wallet (an upper bound a human
  cannot fully execute).
- A `--strategy` check is the executable middle: a category-level plan. Expect it to sit
  between `earned` and `optimal`; if it is below `earned`, the plan is worse than doing
  nothing - say so.

## Phrasing

- Say "engine-verified" for any number produced by `analyze_cli.py`.
- Say "modelled as" when quoting a simplified rule, and point to the assumption.
- Give the missed amount per cycle *and* annualised (x12) - the yearly figure is what
  makes people change behaviour.
