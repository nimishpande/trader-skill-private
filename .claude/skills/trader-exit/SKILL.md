---
name: trader-exit
description: >-
  Exit planner for Indian stocks (NSE). Sets the stop and the target before
  any entry, rounded to a valid NSE tick: intraday stop at the opening-range
  midpoint by default (or the range low, a percent, or a price), swing stop at
  2x the daily ATR (or under the 10-day low), target at 2R by default. Intraday
  plans carry a flat-by time before the broker's auto square-off; swing plans a
  maximum holding period. Refuses stops above the entry or so tight they sit in
  normal noise, and says why. Writes trader/plan.json. Use when the user says
  "where's my stop", "set stop and target", "plan the exit", "when do I get
  out", "stop loss", "risk reward", "what's my R", or right after /trader-entry.
argument-hint: "plan [--stop RULE] [--target RULE]"
---

# trader-exit

`${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                              # defaults from trader/config.json
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-low --target 1.5R
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5               # swing
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --entry 2914.5 --symbol RELIANCE --mode intraday --stop pct:0.8
```

Stop rules: `or-mid`, `or-low`, `atr:N` (daily ATR14), `low-10d`, `pct:N`,
`price:X`. Target rules: `NR` (2R, 1.5R), `pct:N`, `price:X`. Other flags:
`--flat-by HH:MM`, `--max-hold-days N`, `--atr X`, `--no-write`, `--json`.

## NSE tick sizes

Below ₹250: ₹0.01. Up to ₹1,000: ₹0.05. Up to ₹5,000: ₹0.10. Up to ₹10,000:
₹0.50. Up to ₹20,000: ₹1. Above: ₹5 (NSE, from April 2025). NSE fixes each
stock's tick from a reference price, so the broker's tick is final; override a
stock with `"tick_overrides": {"SYMBOL": 0.05}` in trader/config.json. The stop
is rounded down (away from the entry) and the target down (never flattering the
reward).

## Rules for Claude

- Numbers here are before costs. `/trader-budget` shows reward:risk after
  STT, brokerage and the rest; on tight intraday stops that can be much lower.
- Intraday: flat by 15:10 IST by default, before brokers auto square-off MIS
  positions (around 15:15 to 15:25 depending on the broker).
- Swing trades are delivery (CNC): higher STT and a DP charge on the sell, and
  overnight gap risk. A stop can fill well past its price on a gap.
- Next: `/trader-timemachine` for the odds.

## Safety

Suggestions only, not financial advice. This skill plans; it never orders.
