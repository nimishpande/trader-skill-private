---
name: trader-news
description: >-
  News monitor for Indian stocks. For each NSE stock: the move since the
  previous close, Yahoo Finance headlines, and the company's BSE corporate
  announcements (results, board meetings, auditor resignations, pledges,
  orders won, fund raises, regulator actions), newest first in India time,
  tagged, with red flags marked. Shows only what is new since the last check.
  Use when the user says "any news on RELIANCE", "why is this stock moving",
  "news on my watchlist", "morning briefing", "did anything come out",
  "check announcements", "any results today", "red flags", or after
  /trader-scanner and before /trader-entry.
argument-hint: "[--tickers A,B] [--hours 24] [--all]"
---

# trader-news

`${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE,HDFCBANK      # new items, last 24 hours
python3 "${CLAUDE_SKILL_DIR}/news.py"                                  # the watchlist's hits (up to --max 10)
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers INFY --hours 72 --all  # everything in 3 days, seen or not
```

Flags: `--no-bse`, `--no-yahoo`, `--no-write`, `--json`, `--now`, `--offline`.

## Sources

- **Price move**: Yahoo Finance (SYMBOL.NS): during the session the last
  completed 5-minute bar against yesterday's close; after the close, the day's
  change.
- **Headlines**: Yahoo Finance's RSS feed for the stock (India edition). Thin
  for small companies.
- **Announcements**: BSE's corporate-announcements API, the filings companies
  must make (most NSE stocks are also listed on BSE). The NSE symbol is matched
  to its BSE scrip code by ISIN (from trader/universe.csv) and cached in
  trader/bse_codes.json. Each filing links to its PDF.

## Tags

`results`, `results_due` (a board meeting to consider results), `dividend`,
`corporate_action`, `fundraise`, `deal` (orders, acquisitions), `analyst`,
`management`, `insider` (SAST, promoter deals), `block_deal`, `macro`, and the
red flags marked `!`: `auditor` (resignation or qualification), `default`
(insolvency, NCLT), `pledge`, `regulatory` (SEBI orders, penalties, raids),
`surveillance` (ASM / GSM). Tags come from keywords: read the item before
relying on one.

## Rules for Claude

- Lead with red flags and results timing; they matter more than headlines.
- A day trade is flat before the close, so results after hours do not hit it.
  A swing trade held through results can gap past its stop: say so.
- Do not summarize a headline as a reason to trade. News explains a move; it
  does not predict the next one.

## Safety

Suggestions only, not financial advice. Free, unofficial data that can lag.
