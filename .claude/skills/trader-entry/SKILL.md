---
name: trader-entry
description: >-
  Entry timer for Indian stocks (NSE). Checks a setup bar by bar and says
  whether it has triggered: not open yet, range still forming, waiting (and how
  far away), triggered (when, at what price, and how far it has run since,
  flagged stale if it has run), or expired. Intraday: the first 5-minute close
  above the 9:15-9:30 opening range high, or above a level you give; optional
  volume confirmation. Swing: a trade at the scanner's trigger, one tick above
  the signal day's high. Long side only. Writes trader/entry.json for
  /trader-exit. Use when the user says "has it broken out", "tell me when to
  get in", "watch TCS", "did it break the opening range", "entry signal",
  "is the swing setup triggered", or after /trader-scanner and /trader-news.
argument-hint: "check|watch SYMBOL [--setup orb|level|swing] [--level PRICE]"
---

# trader-entry

`${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE                        # opening-range breakout
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --min-volume-ratio 1.5 # trigger bar on 1.5x the range bars' volume
python3 "${CLAUDE_SKILL_DIR}/entry.py" check INFY --setup level --level 1520
python3 "${CLAUDE_SKILL_DIR}/entry.py" check TATAMOTORS --setup swing        # trigger from the swing watchlist
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --every 60             # re-check each minute until it resolves
```

Flags: `--or-minutes` (default 15), `--max-days` (swing: sessions after the
signal before it expires, default 3), `--no-write`, `--json`, `--now`,
`--offline`. With `--now`, `watch` steps the pinned clock instead of sleeping,
which replays a past morning.

## States

| state | meaning |
| --- | --- |
| not open / closed | before 9:15, or no session today (weekend, NSE holiday) |
| range forming | before the opening range ends |
| waiting | no trigger yet; shows the level and how far away the price is |
| triggered | the time and price; `stale` once it has run 0.5% past the trigger or 30 minutes have passed |
| triggered earlier | swing: it traded through the trigger on an earlier session; rescan |
| expired | intraday: nothing by no_entries_after (14:30), or a trigger after it; swing: --max-days passed |
| unusable | a 5-minute bar is missing today (a halt or thin trading) |

## Rules for Claude

- "Triggered" means a rule fired. It is not advice to enter.
- If it is stale, say the real entry would be worse than the plan assumes.
- Next step after a trigger (or to pre-plan a waiting one): `/trader-exit`,
  or `/trader-suggest SYMBOL` for the whole card.
- Data is free Yahoo 5-minute bars; the latest bar is minutes old.

## Safety

Suggestions only, not financial advice. This skill never places an order.
