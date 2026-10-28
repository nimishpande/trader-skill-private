---
name: trader-journal
description: >-
  Keeps score of every suggestion from the India Trader skills, taken or not,
  so the user learns how accurate the suggestions really are. Logs a
  suggestion with its plan, size and predicted odds; records whether the user
  took or skipped it and their real fill and exit; works out what each one
  actually did from the price bars (target, stop, or time exit, in R after
  costs); and reports the real hit rate and average R by mode, setup and
  evidence label, the rupees on trades taken, and how the time machine's odds
  compared with reality. Use when the user says "log this", "I took it",
  "I skipped it", "I sold at", "how are the suggestions doing", "my hit rate",
  "journal", "track record", "review my trades", "how accurate is this".
argument-hint: "log | take ID | skip ID | close ID --price P | review | stats | list"
---

# trader-journal

`${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/journal.py" log                      # the current suggestion
python3 "${CLAUDE_SKILL_DIR}/journal.py" take J4 --price 2916.5   # your real fill (and --qty if different)
python3 "${CLAUDE_SKILL_DIR}/journal.py" skip J4
python3 "${CLAUDE_SKILL_DIR}/journal.py" close J4 --price 2940    # your real exit
python3 "${CLAUDE_SKILL_DIR}/journal.py" review                   # score whatever has played out
python3 "${CLAUDE_SKILL_DIR}/journal.py" stats
python3 "${CLAUDE_SKILL_DIR}/journal.py" list
```

Writes trader/journal.json. In a cloud session that file disappears with the
session unless it is committed to the repo: offer to commit it after `log`,
`take`, `close` or `review` (the repo's .gitignore keeps it; check that the
repo is private first).

## How outcomes are scored

`review` replays each open suggestion on the real bars after it was logged,
with the same rules the time machine uses: intraday, the stop or the target,
whichever the 5-minute bars reach first (both in one bar counts the stop),
else the close of the bar at the flat-by time; swing, the same on daily bars
with gaps filled at the open, else the close after max-hold sessions. A
suggestion that never reached its entry is "not filled" and is not scored.
Every result is in R after charges at the logged size.

## Rules for Claude

- Encourage logging every suggestion, including skipped ones: skipping is a
  decision too, and the journal shows what it saved or cost.
- Under 30 scored suggestions, say the numbers can still swing a lot.
- Use `stats` to answer "how accurate is this": the realized hit rate and
  average R, and the calibration line (predicted vs actual), not the backtest.
- If the WEAK label does as well as SUPPORTIVE over many trades, say the label
  is not adding information yet.

## Safety

Suggestions only, not financial advice. Nothing is ever placed.
