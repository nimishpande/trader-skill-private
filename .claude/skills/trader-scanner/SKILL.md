---
name: trader-scanner
description: >-
  Chart scanner for Indian stocks (NSE). Builds the stock list from NSE's own
  index files (NIFTY 500 by default, or NIFTY 50/100/200, or every EQ stock),
  runs a swing scan of daily closes (20-day breakout, bull flag, 20-DMA reclaim
  in an uptrend, oversold bounce) and an intraday opening-range breakout scan on
  5-minute bars (9:15 to 9:30 range), and writes trader/watchlist.json for the
  other trader skills. Free Yahoo Finance data (SYMBOL.NS). Use when the user
  says "scan the market", "find me setups", "what is breaking out", "opening
  range breakouts", "scan NIFTY 500", "swing setups", "bull flags", "oversold
  stocks", "pre-market scan", "build my watchlist", "what should I look at
  today", or at the start of a trading day.
argument-hint: "[universe | swing | orb] [--at HH:MM]"
---

# trader-scanner

The scanner reads hundreds of free charts and lists the ones that match a rule.
A match is a chart pattern, not a prediction and not a buy signal. It saves the
user from flipping through 500 charts by hand.

`${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                    # NIFTY 500 -> trader/universe.csv
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --index nifty100   # or nifty50, nifty200, all
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --file saved.csv   # a list you downloaded yourself
python3 "${CLAUDE_SKILL_DIR}/scan.py" swing                       # before 9:15 or after 15:30 IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" swing --symbols RELIANCE,TCS,INFY
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                         # from 9:35 IST to no_entries_after (14:30)
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15              # replay the morning at 10:15
```

Flags: `--symbols A,B` or `--watchlist FILE` instead of the universe,
`--min-price` (default 20), `--min-turnover-cr` (swing, default 2 crore),
`--confirm N` (how many hits to re-check with real highs and lows),
`--show N`, `--no-write`, `--json`, `--now "YYYY-MM-DD HH:MM"` (IST) to pin the
clock, `--offline PATH` to read a saved data bundle. Exit code 0 when something
matched, 1 when nothing did or it could not run (the reason is one plain line).

## The rules

Swing (daily closes, so the scanner and the time machine test the same rule):

| setup | rule |
| --- | --- |
| 20d breakout | the last close is above every close of the 20 sessions before it |
| bull flag | up more than 10% over the 10 sessions before the last 5, then 5 closes inside a 5% range |
| 20-DMA reclaim | closed back above the 20-day average while the 50-day is above the 200-day |
| oversold bounce | RSI 14 was under 30 yesterday and today closed up (counter-trend) |

Each hit gets a trigger: one tick above the signal day's high. The swing entry
is a trade at that trigger.

Opening-range breakout (intraday): the range is the 9:15, 9:20 and 9:25
five-minute bars. A hit is the first completed bar that CLOSES above the range
high. Stage 1 reads closes for everything (20 symbols a request); stage 2
confirms the candidates with real highs and lows. A chart counts only if every
5-minute bar since 9:15 is there (a missing bar means a halt or thin trading).
Trade-to-trade (BE series) stocks are left out of intraday scans: NSE does not
allow intraday trading in them.

## Steps

1. Once a week or so: `scan.py universe`. If NSE refuses the download, ask the
   user to open the URL it prints in their browser, save the CSV, and run
   `scan.py universe --file <saved csv>`.
2. Swing: run before the open or after the close. Report fetched / liquid /
   matched counts and the top few by setup.
3. Intraday: run after 9:35 IST (10:00 to 10:15 is common). Report how many
   usable charts broke out and when; an early hit has had time to move on.
4. The user picks one. Hand it on: `/trader-suggest SYMBOL` (the whole card),
   or step by step `/trader-news`, `/trader-entry`. Never pick for them.

## Rules for Claude

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data. A partial scan is called partial.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- A hit is stale once the price has moved on. Re-run before acting.
- Data is free and unofficial (Yahoo Finance), bar by bar, not tick by tick.

## Safety

Suggestions only, not financial advice. This skill never places, sizes or
checks an order.
