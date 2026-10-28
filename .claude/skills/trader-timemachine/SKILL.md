---
name: trader-timemachine
description: >-
  The honest odds for a trade plan on an Indian stock, before risking a rupee.
  Intraday: rebuilds the rest of the session 10,000 times from the stock's own
  5-minute bars at that time of day over its last ~60 sessions, and replays the
  same opening-range rule on every past session. Swing: rebuilds the holding
  period from a year of its own daily bars with gaps, and replays the same
  setup, stop and target on two years of history, one trade at a time. Both
  scored in R after Indian trading costs, with a 95% bootstrap range on the
  history average and one evidence label: WEAK, MIXED or SUPPORTIVE. Use when
  the user says "what are the odds", "run the time machine", "backtest this",
  "simulate this trade", "how often does this work", "is this a coin flip",
  "monte carlo", or after /trader-exit and before /trader-budget.
argument-hint: "run [--replays 10000] [--seed 19]"
---

# trader-timemachine

`${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                      # reads trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --replays 20000 --seed 7
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --history 5y         # swing: more history
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --setup bull_flag    # swing: replay a different setup
```

Flags: `--window N` (intraday: draw each bar from +/- N time slots, default 2),
`--no-write`, `--json`, `--now`, `--offline`. Same seed, same answer.

## What it measures

- **Simulation**: target first / stop first / neither, and the average R after
  costs. Intraday draws each future 5-minute bar from the stock's own bars at
  that time of day; if stop and target fall in one bar it counts the stop.
  Swing draws daily bars (open, high, low, close relative to the prior close),
  so overnight gaps through the stop are in it.
- **History**: the same rule on every past day it fired on this stock. Intraday
  uses the last ~60 sessions (Yahoo keeps 60 days of 5-minute bars). Swing uses
  two years: the entry is a trade above the signal day's high within 3
  sessions, a gap open above fills at the open, one trade at a time.
- **Costs**: every trade is charged STT, brokerage, exchange, SEBI, GST, stamp
  (and DP for delivery) at roughly the size /trader-budget would pick. Tight
  intraday stops can lose 0.3R to 0.5R a trade to charges alone.
- **95% range**: a bootstrap range for the history's average R. If it spans
  zero, there is no clear edge, however good the average looks.

Evidence label: SUPPORTIVE needs 15+ past trades whose 95% range stays above
zero (and a simulation that is not negative). WEAK when the history range is
below zero, or the simulation averages under -0.1R without supportive history.
Everything else is MIXED.

## Rules for Claude

- Report the label, the after-cost averages and the sample size first.
- Under 15 past trades: say it is too few to trust either way.
- A simulation is not a forecast. The next session can do what the last 60
  never did: results, a block deal, a circuit, an index move.
- Never turn a SUPPORTIVE label into "this will work".

## Safety

Suggestions only, not financial advice.
