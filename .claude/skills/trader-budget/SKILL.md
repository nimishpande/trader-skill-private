---
name: trader-budget
description: >-
  Position sizing and rule checks for Indian stocks. Finds the most whole
  shares where the loss at the stop, INCLUDING every Indian charge (brokerage,
  STT, exchange, SEBI, GST, stamp duty, DP for delivery), fits your max loss
  per trade, your position cap and your cash; shows rupees lost at the stop and
  made at the target after charges, reward:risk after costs and the breakeven
  price; then checks your rules (minimum net reward:risk, entry window, trades
  and loss limits for the day, trade-to-trade stocks, the time machine's
  evidence, news red flags, circuit bands) and says CONSIDER or SKIP. Never
  orders. Use when the user says "how many shares", "size this", "how much
  should I buy", "I have 1 lakh", "I only want to risk 500", "what are the
  charges", "is it worth it after costs", "check my rules", or after
  /trader-timemachine.
argument-hint: "[--capital RUPEES] [--max-loss RUPEES]"
---

# trader-budget

`${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                                  # trader/config.json limits
python3 "${CLAUDE_SKILL_DIR}/size.py" --capital 200000 --max-loss 1000
```

Reads trader/plan.json, and when present trader/odds.json, trader/news.json
and trader/journal.json. Writes trader/sizing.json.

## Your rules (trader/config.json)

Copy `config.example.json` from the repo root to `trader/config.json` and edit:

| key | default | meaning |
| --- | --- | --- |
| capital | 100000 | rupees you trade with |
| max_loss_per_trade | 500 | the most one trade may lose at its stop, charges included |
| daily_loss_limit | 1500 | stop suggesting new trades past this loss in a day |
| max_trades_per_day | 3 | |
| min_net_reward_risk | 1.5 | reward:risk AFTER charges |
| position_cap_pct | 25 | one position at most this share of capital |
| cash_buffer_pct | 10 | never plan to use the last 10% |
| intraday_leverage | 1 | 1 = your own money only; MIS margin is a broker choice, not a budget |
| costs | Zerodha | override brokerage / DP for your broker (see README) |

## Rules for Claude

- Show the rupees, not just R: "you lose ₹181 if the stop fills, charges
  included".
- If reward:risk after costs is far below the plan's R, point it out: charges
  matter most on small, tight intraday trades.
- SKIP means the user's own rules or the evidence say skip. Say it plainly.
  CONSIDER means nothing rules it out. Both end with: the decision is theirs.
- Never suggest raising leverage or the max loss to make a trade fit.

## Safety

Suggestions only, not financial advice. Nothing is ever placed.
